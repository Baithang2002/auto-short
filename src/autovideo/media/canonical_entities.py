"""Resolve documentary phrasing into retrieval-safe visual entities.

The resolver deliberately does not generate provider queries.  It preserves the
editorial document verbatim while giving retrieval and verification stages a
small, concrete entity they can reliably search and prove.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .scene_entities import SceneEntity


@dataclass(frozen=True)
class CanonicalEntityResolverConfig:
    """Configuration for deterministic canonical scene-entity resolution."""

    enabled: bool = True
    max_entities_per_scene: int = 3
    expand_synonyms: bool = True
    minimum_confidence: float = 0.65

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "CanonicalEntityResolverConfig":
        """Load resolver settings from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_CANONICAL_ENTITY_RESOLVER_ENABLED", True),
            max_entities_per_scene=max(
                1,
                _env_int(values, "AUTO_VIDEO_CANONICAL_ENTITY_MAX_PER_SCENE", 3),
            ),
            expand_synonyms=_env_bool(
                values,
                "AUTO_VIDEO_CANONICAL_ENTITY_SYNONYM_EXPANSION",
                True,
            ),
            minimum_confidence=_clamp(
                _env_float(values, "AUTO_VIDEO_CANONICAL_ENTITY_MIN_CONFIDENCE", 0.65),
            ),
        )


@dataclass(frozen=True)
class CanonicalSceneEntity:
    """One retrieval-safe entity resolved from an immutable ShotIntent."""

    scene_index: int
    original_entity: str
    canonical_entity: str
    supporting_entities: tuple[str, ...]
    confidence: float
    explanation: str
    resolved_entity: SceneEntity

    def to_dict(self) -> dict[str, Any]:
        """Serialize the scene mapping for diagnostics and resumption."""

        return {
            "scene_index": self.scene_index,
            "original_scene_entity": self.original_entity,
            "canonical_scene_entity": self.canonical_entity,
            "supporting_entities": list(self.supporting_entities),
            "resolution_confidence": self.confidence,
            "normalization_explanation": self.explanation,
            "resolved_scene_entity": self.resolved_entity.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalSceneEntity":
        """Restore a persisted resolution artifact."""

        return cls(
            scene_index=int(data.get("scene_index", 0)),
            original_entity=str(data.get("original_scene_entity", "")),
            canonical_entity=str(data.get("canonical_scene_entity", "")),
            supporting_entities=tuple(
                str(item) for item in data.get("supporting_entities", [])
            ),
            confidence=float(data.get("resolution_confidence", 0.0)),
            explanation=str(data.get("normalization_explanation", "")),
            resolved_entity=SceneEntity.from_dict(
                dict(data.get("resolved_scene_entity", {}))
            ),
        )


@dataclass(frozen=True)
class CanonicalEntityReport:
    """Auditable document-wide canonical entity mapping."""

    documentary_topic: str
    primary_subject: str
    canonical_documentary_entity: str
    config: CanonicalEntityResolverConfig
    scenes: tuple[CanonicalSceneEntity, ...]

    def scene_for_index(self, index: int) -> CanonicalSceneEntity | None:
        """Return the resolution for a zero-based scene index."""

        return next((scene for scene in self.scenes if scene.scene_index == index), None)

    def to_dict(self) -> dict[str, Any]:
        """Serialize a stable ``canonical_entity_report.json`` artifact."""

        return {
            "documentary_topic": self.documentary_topic,
            "primary_subject": self.primary_subject,
            "canonical_documentary_entity": self.canonical_documentary_entity,
            "configuration": asdict(self.config),
            "scene_mapping": [scene.to_dict() for scene in self.scenes],
        }

    def write_json(self, path: Path) -> Path:
        """Write the report to the supplied artifact path."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalEntityReport":
        """Restore the report from its JSON representation."""

        config_data = dict(data.get("configuration", {}))
        return cls(
            documentary_topic=str(data.get("documentary_topic", "")),
            primary_subject=str(data.get("primary_subject", "")),
            canonical_documentary_entity=str(
                data.get("canonical_documentary_entity")
                or _documentary_entity(
                    str(data.get("documentary_topic", "")),
                    str(data.get("primary_subject", "")),
                )
            ),
            config=CanonicalEntityResolverConfig(
                enabled=bool(config_data.get("enabled", True)),
                max_entities_per_scene=int(config_data.get("max_entities_per_scene", 3)),
                expand_synonyms=bool(config_data.get("expand_synonyms", True)),
                minimum_confidence=float(config_data.get("minimum_confidence", 0.65)),
            ),
            scenes=tuple(
                CanonicalSceneEntity.from_dict(item)
                for item in data.get("scene_mapping", [])
            ),
        )


class CanonicalSceneEntityResolver:
    """Normalize scene entities without modifying documentary identity."""

    def __init__(self, config: CanonicalEntityResolverConfig | None = None) -> None:
        self.config = config or CanonicalEntityResolverConfig()

    def resolve(
        self,
        *,
        documentary_topic: str,
        shot_plan: Any,
    ) -> CanonicalEntityReport:
        """Resolve retrieval-safe scene entities for an existing ShotPlan."""

        scenes = tuple(
            self._resolve_scene(documentary_topic, intent)
            for intent in getattr(shot_plan, "intents", ())
        )
        primary_subject = str(getattr(shot_plan, "primary_subject", ""))
        return CanonicalEntityReport(
            documentary_topic=documentary_topic,
            primary_subject=primary_subject,
            canonical_documentary_entity=_documentary_entity(
                documentary_topic,
                primary_subject,
            ),
            config=self.config,
            scenes=scenes,
        )

    def _resolve_scene(self, topic: str, intent: Any) -> CanonicalSceneEntity:
        source_entity = getattr(intent, "scene_entity", None)
        original = str(
            getattr(source_entity, "canonical_entity", "")
            or getattr(intent, "primary_subject", "")
            or topic
        )
        narration = str(getattr(intent, "diagnostics", {}).get("narration", ""))
        # ShotIntent deliberately stores only planning data, not the script
        # narration. Its bounded query tiers are the durable scene-level
        # evidence available to this resolver during normal pipeline runs.
        scene_queries = tuple(str(item) for item in getattr(intent, "search_queries", ()))
        scene_corpus = " ".join(
            (
                original,
                narration,
                str(getattr(intent, "action", "")),
                str(getattr(intent, "environment", "")),
                *getattr(intent, "required_entities", ()),
                *scene_queries,
            )
        )
        canonical, synonyms, confidence, explanation = _resolve_entity(
            scene_corpus,
            topic,
            original,
        )
        if not self.config.enabled:
            canonical = original
            synonyms = ()
            confidence = 1.0
            explanation = "resolver disabled; retained original SceneEntity"
        supporting = _supporting_entities(
            canonical,
            intent,
            self.config.max_entities_per_scene - 1,
        )
        if not self.config.expand_synonyms:
            synonyms = ()
        resolved = SceneEntity(
            canonical_entity=canonical,
            entity_type=str(getattr(source_entity, "entity_type", "visual_entity") or "visual_entity"),
            aliases=tuple(synonyms),
            required_terms=(canonical,),
            optional_terms=_dedupe(
                (*supporting, *getattr(source_entity, "optional_terms", ()))
            ),
            forbidden_terms=tuple(getattr(source_entity, "forbidden_terms", ())),
            confidence=max(self.config.minimum_confidence, confidence),
        )
        return CanonicalSceneEntity(
            scene_index=int(getattr(intent, "scene_index", 0)),
            original_entity=original,
            canonical_entity=canonical,
            supporting_entities=supporting,
            confidence=confidence,
            explanation=explanation,
            resolved_entity=resolved,
        )


_ENTITY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "laboratory sled experiment",
        ("sled friction experiment",),
        ("laboratory", "miniature sled", "testing sled", "controlled experiment"),
    ),
    (
        "Egyptian wall painting",
        ("ancient Egyptian relief", "Egyptian tomb painting"),
        ("wall painting", "tomb painting", "egyptian relief"),
    ),
    (
        "wooden sled",
        ("ancient Egyptian sled", "stone transport sled"),
        ("wooden sled", "wooden sleds", "sled carrying", "pulling these heavy stone sleds"),
    ),
    (
        "wet sand friction",
        ("water on sand", "damp sand physics"),
        ("wet sand", "dampened sand", "damp sand", "water to the sand", "sand causes friction"),
    ),
    (
        "ancient Egyptian stone blocks",
        ("pyramid stone blocks", "Egyptian construction stones"),
        ("stone blocks", "stone block", "two ton stones", "heavy stone"),
    ),
    (
        "Egyptian pyramids",
        ("Great Pyramid of Giza", "Giza pyramids", "ancient Egyptian pyramids"),
        ("pyramid", "pyramids", "great pyramid"),
    ),
    ("Amazon River", ("Amazon basin river",), ("amazon river",)),
    ("deforestation", ("rainforest deforestation", "forest clearing"), ("deforestation", "forest clearing")),
    ("rainforest canopy", ("tropical canopy", "jungle canopy"), ("rainforest canopy", "jungle canopy")),
    ("greenland shark", ("Greenland shark", "sleeper shark"), ("greenland shark",)),
    ("octopus", ("common octopus", "mimic octopus", "giant pacific octopus"), ("octopus",)),
    ("camel", ("dromedary camel", "desert camel"), ("camel", "dromedary")),
    ("rainforest", ("tropical rainforest", "Amazon rainforest", "jungle"), ("rainforest", "jungle")),
    ("volcano", ("volcanic eruption", "lava flow"), ("volcano", "volcanic", "lava")),
    ("honeybee", ("honey bee", "bee colony"), ("honeybee", "honey bee")),
    ("penguin", ("emperor penguin", "penguin colony"), ("penguin",)),
    ("aurora borealis", ("northern lights", "aurora"), ("aurora", "northern lights")),
    ("roman ruins", ("ancient Rome", "Roman Empire"), ("roman empire", "ancient rome", "roman ruins")),
    ("deep ocean", ("deep sea", "ocean depths", "marine life"), ("deep ocean", "deep sea")),
)


_DOCUMENTARY_ENTITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Egyptian pyramids", ("pyramid", "pyramids", "great pyramid")),
    ("Amazon River", ("amazon river",)),
    ("rainforest", ("rainforest", "jungle")),
    ("greenland shark", ("greenland shark",)),
    ("octopus", ("octopus",)),
    ("camel", ("camel", "dromedary")),
    ("volcano", ("volcano", "volcanic", "lava")),
    ("honeybee", ("honeybee", "honey bee")),
    ("penguin", ("penguin",)),
    ("aurora borealis", ("aurora", "northern lights")),
    ("roman ruins", ("roman empire", "ancient rome", "roman ruins")),
    ("deep ocean", ("deep ocean", "deep sea")),
)


def _documentary_entity(topic: str, primary_subject: str) -> str:
    """Return a visual noun phrase for the documentary, never its title text."""

    corpus = _normalize(f"{topic} {primary_subject}")
    for canonical, markers in _DOCUMENTARY_ENTITY_RULES:
        if any(marker in corpus for marker in markers):
            return canonical
    fallback = _clean_fallback(primary_subject or topic)
    return fallback


def _resolve_entity(
    scene_corpus: str,
    topic: str,
    original: str,
) -> tuple[str, tuple[str, ...], float, str]:
    normalized = _normalize(scene_corpus)
    for canonical, aliases, markers in _ENTITY_RULES:
        if any(marker in normalized for marker in markers):
            return (
                canonical,
                aliases,
                0.95,
                f"matched visual concept {canonical!r}; removed documentary-title framing",
            )
    topic_normalized = _normalize(topic)
    for canonical, aliases, markers in _ENTITY_RULES:
        if any(marker in topic_normalized for marker in markers):
            return (
                canonical,
                aliases,
                0.85,
                f"inherited supported visual concept {canonical!r} from documentary topic",
            )
    fallback = _clean_fallback(original)
    return (
        fallback,
        (),
        0.7,
        "no concept rule matched; removed curiosity, stop, and marketing words",
    )


def _supporting_entities(canonical: str, intent: Any, maximum: int) -> tuple[str, ...]:
    if maximum <= 0:
        return ()
    candidates = (
        str(getattr(intent, "action", "")),
        str(getattr(intent, "environment", "")),
        str(getattr(intent, "shot_type", "")),
        str(getattr(intent, "documentary_role", "")),
    )
    return _dedupe(
        candidate for candidate in candidates
        if candidate and _normalize(candidate) != _normalize(canonical)
    )[:maximum]


_STOP_WORDS = {
    "a", "an", "and", "are", "actually", "about", "behind", "can", "did", "does", "do", "everything",
    "harshest", "hidden", "how", "in", "inside", "is", "its", "largest", "life", "of", "on", "secret",
    "still", "survive", "survives", "that", "the", "their", "this", "to", "truth", "when", "where", "why",
    "world", "worlds",
}


def _clean_fallback(value: str) -> str:
    words = [word for word in _words(value) if word not in _STOP_WORDS]
    return " ".join(words[:3]) or "documentary subject"


def _words(value: object) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _normalize(value: object) -> str:
    return " ".join(_words(value))


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.casefold()
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


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
