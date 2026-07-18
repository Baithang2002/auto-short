"""Translate documentary intent into provider-friendly visual search language."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .scene_entities import SceneEntity


@dataclass(frozen=True)
class SemanticQueryConfig:
    """Limits and feature flags for provider query translation."""

    enabled: bool = True
    max_queries_per_scene: int = 4
    provider_specific_variants: bool = True
    expand_synonyms: bool = True
    max_normalized_entities: int = 4

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SemanticQueryConfig":
        """Load semantic-query settings from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_SEMANTIC_QUERY_ENGINE_ENABLED", True),
            max_queries_per_scene=max(1, _env_int(values, "AUTO_VIDEO_SEMANTIC_QUERY_MAX_PER_SCENE", 4)),
            provider_specific_variants=_env_bool(
                values,
                "AUTO_VIDEO_SEMANTIC_QUERY_PROVIDER_VARIANTS",
                True,
            ),
            expand_synonyms=_env_bool(values, "AUTO_VIDEO_SEMANTIC_QUERY_SYNONYM_EXPANSION", True),
            max_normalized_entities=max(1, _env_int(
                values,
                "AUTO_VIDEO_SEMANTIC_QUERY_MAX_ENTITIES",
                4,
            )),
        )


@dataclass(frozen=True)
class SemanticSceneQuery:
    """Provider-facing query translation for one immutable documentary scene."""

    scene_index: int
    canonical_visual_entity: str
    visual_entities: tuple[str, ...]
    provider_queries: tuple[str, ...]
    provider_variants: Mapping[str, tuple[str, ...]]
    normalization_decisions: tuple[str, ...]
    provider_entity: SceneEntity

    def queries_for(self, provider: str = "") -> tuple[str, ...]:
        """Return provider-specific queries, falling back to generic provider language."""

        if provider and provider in self.provider_variants:
            return self.provider_variants[provider]
        return self.provider_queries

    def to_dict(self) -> dict[str, Any]:
        """Serialize diagnostic data without changing ShotPlan or Canon artifacts."""

        return {
            "scene_index": self.scene_index,
            "canonical_visual_entity": self.canonical_visual_entity,
            "visual_entities": list(self.visual_entities),
            "provider_queries": list(self.provider_queries),
            "provider_variants": {
                provider: list(queries)
                for provider, queries in self.provider_variants.items()
            },
            "normalization_decisions": list(self.normalization_decisions),
            "provider_entity": self.provider_entity.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticSceneQuery":
        """Restore a semantic scene query from its JSON diagnostic artifact."""

        return cls(
            scene_index=int(data.get("scene_index", 0)),
            canonical_visual_entity=str(data.get("canonical_visual_entity", "")),
            visual_entities=tuple(str(item) for item in data.get("visual_entities", [])),
            provider_queries=tuple(str(item) for item in data.get("provider_queries", [])),
            provider_variants={
                str(provider): tuple(str(item) for item in queries)
                for provider, queries in dict(data.get("provider_variants", {})).items()
            },
            normalization_decisions=tuple(str(item) for item in data.get("normalization_decisions", [])),
            provider_entity=SceneEntity.from_dict(dict(data.get("provider_entity", {}))),
        )


@dataclass(frozen=True)
class SemanticQueryReport:
    """Complete provider-language translation artifact for one documentary."""

    documentary_topic: str
    primary_subject: str
    config: SemanticQueryConfig
    scenes: tuple[SemanticSceneQuery, ...]

    def scene_for_index(self, index: int) -> SemanticSceneQuery | None:
        """Return the provider query plan for a zero-based scene index."""

        return next((scene for scene in self.scenes if scene.scene_index == index), None)

    def to_dict(self) -> dict[str, Any]:
        """Serialize a stable ``semantic_query_report.json`` artifact."""

        return {
            "documentary_topic": self.documentary_topic,
            "primary_subject": self.primary_subject,
            "configuration": asdict(self.config),
            "scenes": [scene.to_dict() for scene in self.scenes],
        }

    def write_json(self, path: Path) -> Path:
        """Persist the semantic query diagnostics for future retrieval analysis."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticQueryReport":
        """Restore a report for resumable pipeline stages."""

        config_data = dict(data.get("configuration", {}))
        config = SemanticQueryConfig(
            enabled=bool(config_data.get("enabled", True)),
            max_queries_per_scene=int(config_data.get("max_queries_per_scene", 4)),
            provider_specific_variants=bool(config_data.get("provider_specific_variants", True)),
            expand_synonyms=bool(config_data.get("expand_synonyms", True)),
            max_normalized_entities=int(config_data.get("max_normalized_entities", 4)),
        )
        return cls(
            documentary_topic=str(data.get("documentary_topic", "")),
            primary_subject=str(data.get("primary_subject", "")),
            config=config,
            scenes=tuple(SemanticSceneQuery.from_dict(item) for item in data.get("scenes", [])),
        )


class SemanticVisualQueryEngine:
    """Translate scene entities into retrieval vocabulary without changing editorial identity."""

    def __init__(self, config: SemanticQueryConfig | None = None) -> None:
        self.config = config or SemanticQueryConfig()

    def plan(self, *, documentary_topic: str, shot_plan: Any) -> SemanticQueryReport:
        """Build provider-only query language from an existing ShotPlan."""

        scenes = tuple(
            self._scene_query(documentary_topic, intent)
            for intent in getattr(shot_plan, "intents", ())
        )
        return SemanticQueryReport(
            documentary_topic=documentary_topic,
            primary_subject=str(getattr(shot_plan, "primary_subject", "")),
            config=self.config,
            scenes=scenes,
        )

    def _scene_query(self, topic: str, intent: Any) -> SemanticSceneQuery:
        source_entity = getattr(intent, "scene_entity", None)
        raw_entity = str(getattr(source_entity, "canonical_entity", "") or getattr(intent, "primary_subject", ""))
        canonical, aliases, decisions = _canonical_visual_entity(raw_entity, topic)
        descriptors = _scene_descriptors(intent, topic, canonical)
        entities = _dedupe((canonical, *aliases))[:self.config.max_normalized_entities]
        queries = _build_queries(entities, descriptors, self.config.max_queries_per_scene)
        if not self.config.enabled:
            queries = tuple(getattr(intent, "search_queries", ())[:self.config.max_queries_per_scene])
            decisions = (*decisions, "semantic query engine disabled; retained ShotPlan queries")
        variants = _provider_variants(canonical, aliases, descriptors, queries) if self.config.provider_specific_variants else {}
        provider_entity = SceneEntity(
            canonical_entity=canonical,
            entity_type=str(getattr(source_entity, "entity_type", "visual_entity") or "visual_entity"),
            aliases=tuple(aliases),
            required_terms=(canonical,),
            optional_terms=tuple(_dedupe((
                *getattr(source_entity, "optional_terms", ()),
                *descriptors,
            ))),
            forbidden_terms=tuple(getattr(source_entity, "forbidden_terms", ())),
            confidence=float(getattr(source_entity, "confidence", 0.9) or 0.9),
        )
        return SemanticSceneQuery(
            scene_index=int(getattr(intent, "scene_index", 0)),
            canonical_visual_entity=canonical,
            visual_entities=entities,
            provider_queries=queries or (canonical,),
            provider_variants=variants,
            normalization_decisions=tuple(decisions),
            provider_entity=provider_entity,
        )


_ENTITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "rainforest": ("tropical rainforest", "Amazon rainforest", "jungle canopy", "forest river"),
    "camel": ("desert camel", "dromedary camel", "camel desert"),
    "penguin": ("Antarctic penguin", "emperor penguin", "penguin colony"),
    "volcano": ("volcanic eruption", "lava flow", "volcanic landscape"),
    "octopus": ("common octopus", "mimic octopus", "cephalopod"),
    "honeybee": ("honey bee", "bee colony", "worker bee"),
    "aurora borealis": ("northern lights", "aurora", "aurora sky"),
}


def _canonical_visual_entity(raw_entity: str, topic: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    text = _norm(f"{raw_entity} {topic}")
    for entity, aliases in _ENTITY_ALIASES.items():
        if _norm(entity) in text or any(_norm(alias) in text for alias in aliases):
            decision = f"normalized documentary phrase {raw_entity!r} to visual entity {entity!r}"
            return entity, aliases, (decision,)
    words = [word for word in _words(raw_entity) if word not in _TITLE_WORDS]
    fallback = " ".join(words[:3]) or raw_entity.strip() or "documentary subject"
    return fallback, (), ("no domain synonym matched; removed title framing words",)


def _scene_descriptors(intent: Any, topic: str, canonical: str) -> tuple[str, ...]:
    candidates = [
        str(getattr(intent, "action", "")),
        str(getattr(intent, "environment", "")),
        str(getattr(intent, "shot_type", "")),
        str(getattr(intent, "documentary_role", "")),
        *[str(item) for item in getattr(intent, "search_queries", ())],
    ]
    topic_words = set(_words(topic))
    canonical_words = set(_words(canonical))
    descriptors: list[str] = []
    for candidate in candidates:
        tokens = [
            token for token in _words(candidate)
            if token not in topic_words and token not in canonical_words and token in _VISUAL_TERMS
        ]
        if tokens:
            descriptors.append(" ".join(tokens[:3]))
    return _dedupe(descriptors)[:3]


def _build_queries(entities: Sequence[str], descriptors: Sequence[str], maximum: int) -> tuple[str, ...]:
    queries: list[str] = []
    for descriptor in descriptors:
        queries.append(_join(entities[0] if entities else "", descriptor))
    for entity in entities:
        queries.append(entity)
    return _dedupe(queries)[:maximum]


def _provider_variants(
    canonical: str,
    aliases: Sequence[str],
    descriptors: Sequence[str],
    generic: Sequence[str],
) -> Mapping[str, tuple[str, ...]]:
    descriptor = descriptors[0] if descriptors else ""
    first_alias = aliases[0] if aliases else canonical
    variants = {
        "pexels": _dedupe((_join(canonical, descriptor), _join(first_alias, descriptor), canonical)),
        "pixabay": _dedupe((_join(first_alias, descriptor), _join(canonical, descriptor), canonical)),
        "wikimedia": _dedupe((_join(first_alias, "photograph"), canonical, *generic)),
        "internet_archive": _dedupe((_join(canonical, "archival footage"), canonical, *generic)),
    }
    if canonical == "rainforest":
        variants["nasa"] = ("Amazon basin satellite", "tropical rainforest Earth from orbit")
    return variants


_TITLE_WORDS = {
    "how", "why", "what", "the", "world", "worlds", "largest", "smallest", "secret", "hidden",
    "survive", "survives", "actually", "still", "its", "and", "of", "in", "to", "a", "an",
}
_VISUAL_TERMS = {
    "aerial", "drone", "canopy", "rain", "rainfall", "mist", "river", "forest", "jungle", "tropical",
    "desert", "sand", "storm", "sunset", "sunlight", "water", "leaf", "macro", "close", "wide",
    "walking", "running", "swimming", "flying", "eruption", "lava", "underwater", "ice", "snow",
    "satellite", "orbit", "cloud", "clouds", "deforestation", "landscape", "colony", "reef",
}


def _words(value: object) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _norm(value: object) -> str:
    return " ".join(_words(value))


def _join(*values: str) -> str:
    return " ".join(value.strip() for value in values if value and value.strip())


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
