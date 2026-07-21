"""Resolve the required visible entity for each documentary scene."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .scene_entities import SceneEntity


class VisualFocusRole(str, Enum):
    """The editorial relationship between a scene focus and its documentary."""

    SUBJECT = "subject"
    MECHANISM = "mechanism"
    RESULT = "result"
    EVIDENCE = "evidence"
    PROCESS = "process"
    CONTEXT = "context"


@dataclass(frozen=True)
class SceneVisualFocus:
    """One immutable, retrieval-facing visible entity for a scene."""

    scene_index: int
    documentary_anchor: str
    required_visual_entity: str
    role: VisualFocusRole
    aliases: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    reason: str = ""
    requires_documentary_anchor: bool = True

    def to_scene_entity(self, source_entity: SceneEntity | None = None) -> SceneEntity:
        """Create a retrieval entity while retaining existing forbidden terms."""

        return SceneEntity(
            canonical_entity=self.required_visual_entity,
            entity_type=f"scene_{self.role.value}",
            aliases=self.aliases,
            required_terms=(self.required_visual_entity,),
            optional_terms=_dedupe((
                *self.query_terms,
                *(source_entity.optional_terms if source_entity else ()),
            )),
            forbidden_terms=source_entity.forbidden_terms if source_entity else (),
            confidence=source_entity.confidence if source_entity else 0.9,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the focus contract for audit and pipeline resume."""

        return {
            "scene_index": self.scene_index,
            "documentary_anchor": self.documentary_anchor,
            "required_visual_entity": self.required_visual_entity,
            "role": self.role.value,
            "aliases": list(self.aliases),
            "query_terms": list(self.query_terms),
            "reason": self.reason,
            "requires_documentary_anchor": self.requires_documentary_anchor,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneVisualFocus":
        """Restore a persisted scene focus."""

        return cls(
            scene_index=int(data.get("scene_index", 0)),
            documentary_anchor=str(data.get("documentary_anchor", "")),
            required_visual_entity=str(data.get("required_visual_entity", "")),
            role=VisualFocusRole(str(data.get("role", VisualFocusRole.SUBJECT.value))),
            aliases=tuple(str(item) for item in data.get("aliases", [])),
            query_terms=tuple(str(item) for item in data.get("query_terms", [])),
            reason=str(data.get("reason", "")),
            requires_documentary_anchor=bool(data.get("requires_documentary_anchor", True)),
        )


@dataclass(frozen=True)
class SceneVisualFocusReport:
    """Run-level map from documentary anchors to scene-required visuals."""

    documentary_topic: str
    primary_subject: str
    scenes: tuple[SceneVisualFocus, ...]

    def scene_for_index(self, index: int) -> SceneVisualFocus | None:
        """Return focus information for one zero-based scene index."""

        return next((scene for scene in self.scenes if scene.scene_index == index), None)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report without changing Timeline or queue metadata."""

        return {
            "documentary_topic": self.documentary_topic,
            "primary_subject": self.primary_subject,
            "scenes": [scene.to_dict() for scene in self.scenes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneVisualFocusReport":
        """Restore focus diagnostics for a resumed run."""

        return cls(
            documentary_topic=str(data.get("documentary_topic", "")),
            primary_subject=str(data.get("primary_subject", "")),
            scenes=tuple(SceneVisualFocus.from_dict(item) for item in data.get("scenes", [])),
        )

    def write_json(self, path: Path) -> Path:
        """Persist the report for audit and pipeline resume."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class SceneVisualFocusPlanner:
    """Select scene-visible entities without changing documentary identity."""

    def plan(
        self,
        *,
        documentary_topic: str,
        shot_plan: Any,
        knowledge_domains: Sequence[Any] = (),
    ) -> SceneVisualFocusReport:
        """Build a focus report from existing ShotIntents and knowledge packs."""

        domain = next(
            (
                item for item in knowledge_domains
                if str(getattr(item, "id", "")) == str(getattr(shot_plan, "domain_id", ""))
            ),
            None,
        )
        anchor = str(getattr(shot_plan, "primary_subject", ""))
        scenes = tuple(
            self._focus_for_intent(intent, anchor, domain)
            for intent in getattr(shot_plan, "intents", ())
        )
        return SceneVisualFocusReport(
            documentary_topic=documentary_topic,
            primary_subject=anchor,
            scenes=scenes,
        )

    def _focus_for_intent(
        self,
        intent: Any,
        anchor: str,
        domain: Any | None,
    ) -> SceneVisualFocus:
        scene_index = int(getattr(intent, "scene_index", 0))
        narration = _normalized_narration(intent)
        rule = _matching_rule(narration, getattr(domain, "scene_focus_rules", ()))
        if rule:
            return SceneVisualFocus(
                scene_index=scene_index,
                documentary_anchor=anchor,
                required_visual_entity=str(rule["entity"]),
                role=VisualFocusRole(str(rule.get("role", VisualFocusRole.MECHANISM.value))),
                aliases=tuple(str(item) for item in rule.get("aliases", [])),
                query_terms=tuple(str(item) for item in rule.get("query_terms", [])),
                reason=f"knowledge-pack rule matched: {', '.join(rule.get('match_terms', []))}",
                requires_documentary_anchor=bool(rule.get("requires_documentary_anchor", False)),
            )

        supporting, ignored_context = _matching_supporting_entity(
            intent,
            anchor,
            narration,
            domain,
        )
        if supporting:
            role = _role_from_intent(intent)
            return SceneVisualFocus(
                scene_index=scene_index,
                documentary_anchor=anchor,
                required_visual_entity=supporting,
                role=role,
                aliases=(),
                query_terms=(supporting, anchor),
                reason="explicit supporting entity matched scene narration or B-roll intent",
                requires_documentary_anchor=False,
            )

        source_entity = getattr(intent, "scene_entity", None)
        entity = str(getattr(source_entity, "canonical_entity", "") or anchor)
        reason = "no explicit concrete result, process, or evidence entity was named"
        if ignored_context:
            reason += f"; retained anchor over context: {', '.join(ignored_context)}"
        return SceneVisualFocus(
            scene_index=scene_index,
            documentary_anchor=anchor,
            required_visual_entity=entity,
            role=VisualFocusRole.SUBJECT,
            aliases=tuple(getattr(source_entity, "aliases", ()) or ()),
            query_terms=(entity,),
            reason=reason,
            requires_documentary_anchor=True,
        )


def _matching_rule(corpus: str, rules: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    matched: list[tuple[int, Mapping[str, Any]]] = []
    for rule in rules:
        terms = tuple(str(item) for item in rule.get("match_terms", []))
        matching_terms = [term for term in terms if _normalize(term) in corpus]
        if matching_terms:
            specificity = max(len(_normalize(term).split()) for term in matching_terms)
            matched.append((specificity, rule))
    return max(matched, key=lambda item: item[0])[1] if matched else None


_CONTEXT_ONLY_TERMS = {
    "arctic",
    "cloud",
    "clouds",
    "desert",
    "environment",
    "forest",
    "landscape",
    "nature",
    "ocean",
    "reef",
    "sea",
    "sky",
    "storm",
    "underwater",
    "water",
    "weather",
}


def _matching_supporting_entity(
    intent: Any,
    anchor: str,
    narration: str,
    domain: Any | None,
) -> tuple[str, tuple[str, ...]]:
    """Return an explicit concrete focus and context terms kept as constraints."""

    candidates = (
        *getattr(domain, "named_entities", ()),
        *getattr(intent, "required_entities", ()),
        *getattr(getattr(intent, "scene_entity", None), "optional_terms", ()),
    )
    concrete: list[str] = []
    ignored_context: list[str] = []
    for candidate in _dedupe(tuple(str(item) for item in candidates)):
        normalized = _normalize(candidate)
        if not normalized or normalized not in narration:
            continue
        if _is_anchor_variant(normalized, _normalize(anchor)):
            continue
        if normalized in _CONTEXT_ONLY_TERMS:
            ignored_context.append(candidate)
            continue
        concrete.append(candidate)
    if not concrete:
        return "", tuple(_dedupe(tuple(ignored_context)))
    return max(concrete, key=lambda value: len(_normalize(value).split())), ()


def _role_from_intent(intent: Any) -> VisualFocusRole:
    mode = str(getattr(getattr(intent, "media_mode", None), "value", "")).lower()
    goal = str(getattr(getattr(intent, "visual_goal", None), "value", "")).lower()
    if mode == "prove" or goal == "prove":
        return VisualFocusRole.EVIDENCE
    if mode == "explain" or goal == "explain":
        return VisualFocusRole.MECHANISM
    return VisualFocusRole.PROCESS


def _normalized_narration(intent: Any) -> str:
    diagnostics = getattr(intent, "diagnostics", {}) or {}
    return _normalize(diagnostics.get("narration", ""))


def _is_anchor_variant(candidate: str, anchor: str) -> bool:
    return bool(candidate and anchor and (candidate in anchor or anchor in candidate))


def _normalize(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = _normalize(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)
