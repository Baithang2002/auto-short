"""Preserve mandatory visual requirements from planning through provider queries."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SceneConstraintConfig:
    """Configuration for deterministic scene-constraint preservation."""

    enabled: bool = True
    max_queries_per_scene: int = 4

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SceneConstraintConfig":
        """Load constraint settings from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_SCENE_CONSTRAINTS_ENABLED", True),
            max_queries_per_scene=max(
                1,
                _env_int(values, "AUTO_VIDEO_SCENE_CONSTRAINT_MAX_QUERIES", 4),
            ),
        )


@dataclass(frozen=True)
class MandatoryVisualConstraint:
    """One scene requirement that provider-facing queries must preserve."""

    kind: str
    canonical_term: str
    accepted_terms: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the constraint for diagnostics and resume support."""

        return {
            "kind": self.kind,
            "canonical_term": self.canonical_term,
            "accepted_terms": list(self.accepted_terms),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MandatoryVisualConstraint":
        """Restore a serialized constraint."""

        return cls(
            kind=str(data.get("kind", "")),
            canonical_term=str(data.get("canonical_term", "")),
            accepted_terms=tuple(str(item) for item in data.get("accepted_terms", [])),
            source=str(data.get("source", "")),
        )


@dataclass(frozen=True)
class SceneVisualConstraints:
    """Immutable query contract for a single documentary scene."""

    scene_index: int
    canonical_entity: str
    visual_goal: str
    constraints: tuple[MandatoryVisualConstraint, ...]
    query_seeds: tuple[str, ...]
    diagnostics: Mapping[str, Any]

    def filter_queries(
        self,
        queries: Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
        """Keep only queries that preserve every mandatory scene requirement."""

        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        for query in queries:
            cleaned = _clean(query)
            if not cleaned:
                continue
            missing = [
                constraint.canonical_term
                for constraint in self.constraints
                if not _matches(cleaned, constraint)
            ]
            if missing:
                rejected.append({
                    "query": cleaned,
                    "reason": "missing mandatory visual constraints",
                    "missing": ", ".join(missing),
                })
                continue
            accepted.append(cleaned)
        return _dedupe(accepted), tuple(rejected)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the immutable contract."""

        return {
            "scene_index": self.scene_index,
            "canonical_entity": self.canonical_entity,
            "visual_goal": self.visual_goal,
            "mandatory_constraints": [constraint.to_dict() for constraint in self.constraints],
            "query_seeds": list(self.query_seeds),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneVisualConstraints":
        """Restore a serialized scene contract."""

        return cls(
            scene_index=int(data.get("scene_index", 0)),
            canonical_entity=str(data.get("canonical_entity", "")),
            visual_goal=str(data.get("visual_goal", "show")),
            constraints=tuple(
                MandatoryVisualConstraint.from_dict(item)
                for item in data.get("mandatory_constraints", [])
            ),
            query_seeds=tuple(str(item) for item in data.get("query_seeds", [])),
            diagnostics=dict(data.get("diagnostics", {})),
        )


@dataclass(frozen=True)
class SceneConstraintReport:
    """Document-wide scene constraint diagnostics."""

    documentary_topic: str
    config: SceneConstraintConfig
    scenes: tuple[SceneVisualConstraints, ...]

    def scene_for_index(self, index: int) -> SceneVisualConstraints | None:
        """Return the contract for one zero-based scene index."""

        return next((scene for scene in self.scenes if scene.scene_index == index), None)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report without modifying the Timeline schema."""

        return {
            "documentary_topic": self.documentary_topic,
            "configuration": asdict(self.config),
            "scenes": [scene.to_dict() for scene in self.scenes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneConstraintReport":
        """Restore a report for resumable pipeline stages."""

        config_data = dict(data.get("configuration", {}))
        return cls(
            documentary_topic=str(data.get("documentary_topic", "")),
            config=SceneConstraintConfig(
                enabled=bool(config_data.get("enabled", True)),
                max_queries_per_scene=int(config_data.get("max_queries_per_scene", 4)),
            ),
            scenes=tuple(
                SceneVisualConstraints.from_dict(item) for item in data.get("scenes", [])
            ),
        )

    def write_json(self, path: Path) -> Path:
        """Persist the report for review and pipeline resume."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class SceneConstraintPlanner:
    """Build mandatory visual query contracts from canonical scene planning."""

    def __init__(self, config: SceneConstraintConfig | None = None) -> None:
        self.config = config or SceneConstraintConfig()

    def plan(
        self,
        *,
        documentary_topic: str,
        shot_plan: Any,
        canonical_report: Any | None = None,
    ) -> SceneConstraintReport:
        """Create one immutable contract for each existing ShotIntent."""

        scenes = tuple(
            self._scene_constraints(intent, canonical_report)
            for intent in getattr(shot_plan, "intents", ())
        )
        return SceneConstraintReport(
            documentary_topic=documentary_topic,
            config=self.config,
            scenes=scenes,
        )

    def _scene_constraints(
        self,
        intent: Any,
        canonical_report: Any | None,
    ) -> SceneVisualConstraints:
        scene_index = int(getattr(intent, "scene_index", 0))
        resolved = canonical_report.scene_for_index(scene_index) if canonical_report else None
        source_entity = (
            getattr(resolved, "resolved_entity", None)
            or getattr(intent, "scene_entity", None)
        )
        canonical = str(
            getattr(resolved, "canonical_entity", "")
            or getattr(source_entity, "canonical_entity", "")
            or getattr(intent, "primary_subject", "")
        )
        aliases = tuple(getattr(source_entity, "aliases", ()) or ())
        constraints: list[MandatoryVisualConstraint] = []
        if canonical:
            constraints.append(_constraint("subject", canonical, aliases, "canonical_scene_entity"))

        corpus = " ".join(
            str(value or "")
            for value in (
                getattr(intent, "environment", ""),
                getattr(intent, "action", ""),
                getattr(intent, "diagnostics", {}).get("narration", ""),
                *getattr(intent, "search_queries", ()),
            )
        )
        for kind, term in _detect_visual_requirements(corpus, intent):
            if term and term != canonical:
                constraints.append(
                    _constraint(kind, term, _aliases_for(term), "scene_narration_or_intent")
                )
        constraints = _dedupe_constraints(constraints)
        query_seeds = _query_seeds(canonical, constraints, self.config.max_queries_per_scene)
        if not self.config.enabled:
            query_seeds = tuple(
                getattr(intent, "search_queries", ())[:self.config.max_queries_per_scene]
            )
            constraints = []
        return SceneVisualConstraints(
            scene_index=scene_index,
            canonical_entity=canonical,
            visual_goal=str(
                getattr(
                    getattr(intent, "visual_goal", None),
                    "value",
                    getattr(intent, "visual_goal", "show"),
                )
            ),
            constraints=tuple(constraints),
            query_seeds=query_seeds or ((canonical,) if canonical else ()),
            diagnostics={
                "source_environment": str(getattr(intent, "environment", "")),
                "source_action": str(getattr(intent, "action", "")),
                "preservation_enabled": self.config.enabled,
            },
        )


_REQUIREMENT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "underwater": ("underwater", "submarine", "subsea", "deep ocean", "ocean floor", "undersea"),
    "deep ocean": ("deep ocean", "deep sea", "ocean depths", "abyssal", "underwater"),
    "desert": ("desert", "sand dunes", "sand", "arid"),
    "hive": ("hive", "bee colony", "honeycomb"),
    "reef": ("reef", "coral reef", "underwater reef"),
    "arctic": ("arctic", "polar", "icy", "ice"),
    "dark ocean": ("dark ocean", "deep ocean", "ocean depths", "underwater"),
    "green sky": ("green sky", "green clouds", "green storm sky"),
    "supercell": ("supercell", "severe thunderstorm", "rotating storm"),
    "waggle dance": ("waggle dance", "bee dance", "waggle"),
    "eruption": ("eruption", "erupting", "lava plume", "volcanic vent"),
    "camouflage": ("camouflage", "changing color", "color change", "disguise"),
    "squeezing through": ("squeezing through", "squeeze through", "narrow crevice"),
    "solar wind": ("solar wind", "sun particles", "charged particles"),
    "magnetosphere": ("magnetosphere", "earth magnetic field", "magnetic field"),
    "wide": ("wide", "wide shot", "aerial", "establishing"),
    "close": ("close", "close up", "macro", "detail"),
    "macro": ("macro", "close up", "detail"),
    "aerial": ("aerial", "drone", "wide"),
}

_REQUIREMENT_ORDER = (
    "underwater",
    "deep ocean",
    "arctic",
    "desert",
    "hive",
    "reef",
    "dark ocean",
    "green sky",
    "supercell",
    "waggle dance",
    "eruption",
    "camouflage",
    "squeezing through",
    "solar wind",
    "magnetosphere",
)


def _detect_visual_requirements(corpus: str, intent: Any) -> tuple[tuple[str, str], ...]:
    normalized = _normalize(corpus)
    detected: list[tuple[str, str]] = []
    for term in _REQUIREMENT_ORDER:
        aliases = _aliases_for(term)
        if any(_normalize(alias) in normalized for alias in aliases):
            kind = "environment" if term in {
                "underwater", "deep ocean", "arctic", "desert", "hive", "reef", "dark ocean",
            } else "mechanism"
            if term in {"green sky", "supercell"}:
                kind = "atmosphere"
            detected.append((kind, term))
    environment = _clean(getattr(intent, "environment", ""))
    detected_terms = {_normalize(term) for _, term in detected}
    if environment and _normalize(environment) not in detected_terms:
        detected.append(("environment", environment))
    action = _clean(getattr(intent, "action", ""))
    if action and _normalize(action) not in {_normalize(term) for _, term in detected}:
        if _normalize(action) in {
            "walking",
            "swimming",
            "flying",
            "flowing",
            "lightning strike",
            "water channel",
            "cable laying",
        }:
            detected.append(("action", action))
    shot_type = _clean(getattr(intent, "shot_type", ""))
    if shot_type and _normalize(shot_type) in {"wide", "close", "macro", "aerial"}:
        detected.append(("framing", shot_type))
    return tuple(detected)


def _constraint(
    kind: str,
    term: str,
    aliases: Sequence[str],
    source: str,
) -> MandatoryVisualConstraint:
    accepted = _dedupe((term, *aliases))
    return MandatoryVisualConstraint(kind, term, accepted, source)


def _aliases_for(term: str) -> tuple[str, ...]:
    normalized = _normalize(term)
    for canonical, aliases in _REQUIREMENT_ALIASES.items():
        if normalized == _normalize(canonical):
            return aliases
    return (term,)


def _query_seeds(
    canonical: str,
    constraints: Sequence[MandatoryVisualConstraint],
    maximum: int,
) -> tuple[str, ...]:
    subject = next((constraint for constraint in constraints if constraint.kind == "subject"), None)
    supporting = [constraint for constraint in constraints if constraint.kind != "subject"]
    if not canonical or not supporting:
        return (canonical,) if canonical else ()
    first_terms = [constraint.canonical_term for constraint in supporting]
    seeds = [_join(canonical, *first_terms)]
    for index, constraint in enumerate(supporting):
        subject_variant = (
            subject.accepted_terms[min(index + 1, len(subject.accepted_terms) - 1)]
            if subject else canonical
        )
        constraint_variant = constraint.accepted_terms[min(1, len(constraint.accepted_terms) - 1)]
        other_terms = [item.canonical_term for item in supporting if item is not constraint]
        seeds.append(_join(subject_variant, constraint_variant, *other_terms))
    return _dedupe(seeds)[:maximum]


def _matches(query: str, constraint: MandatoryVisualConstraint) -> bool:
    normalized = _normalize(query)
    return any(_normalize(term) in normalized for term in constraint.accepted_terms)


def _dedupe_constraints(
    constraints: Sequence[MandatoryVisualConstraint],
) -> list[MandatoryVisualConstraint]:
    seen: set[str] = set()
    result: list[MandatoryVisualConstraint] = []
    for constraint in constraints:
        key = _normalize(constraint.canonical_term)
        if key and key not in seen:
            seen.add(key)
            result.append(constraint)
    return result


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("-", " ").split())


def _normalize(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _join(*values: str) -> str:
    return _clean(" ".join(value for value in values if value))


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _clean(value)
        key = _normalize(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    return default if value is None else str(value).strip().lower() not in {"0", "false", "no", ""}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
