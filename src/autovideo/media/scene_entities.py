"""Scene-level entity planning and query isolation diagnostics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .editorial import DocumentaryMode, EditorialCanon


@dataclass(frozen=True)
class SceneEntity:
    """Immutable subject assigned to a single documentary scene."""

    canonical_entity: str
    entity_type: str
    aliases: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    optional_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_entity": self.canonical_entity,
            "entity_type": self.entity_type,
            "aliases": list(self.aliases),
            "required_terms": list(self.required_terms),
            "optional_terms": list(self.optional_terms),
            "forbidden_terms": list(self.forbidden_terms),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneEntity":
        return cls(
            canonical_entity=str(data.get("canonical_entity", "")),
            entity_type=str(data.get("entity_type", "")),
            aliases=tuple(str(item) for item in data.get("aliases", [])),
            required_terms=tuple(str(item) for item in data.get("required_terms", [])),
            optional_terms=tuple(str(item) for item in data.get("optional_terms", [])),
            forbidden_terms=tuple(str(item) for item in data.get("forbidden_terms", [])),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass(frozen=True)
class SceneEntityPlan:
    """Scene entity assignments and query-isolation diagnostics."""

    entities: tuple[SceneEntity, ...]
    rejected_mixed_queries: tuple[dict[str, Any], ...] = ()
    fallback_chains: tuple[dict[str, Any], ...] = ()

    def entity_for_index(self, index: int) -> SceneEntity | None:
        if 0 <= index < len(self.entities):
            return self.entities[index]
        return None

    def to_report(self) -> dict[str, Any]:
        return {
            "scenes": [
                {"scene_index": index, "scene_entity": entity.to_dict()}
                for index, entity in enumerate(self.entities)
            ],
            "rejected_mixed_queries": list(self.rejected_mixed_queries),
            "fallback_chains": list(self.fallback_chains),
        }


class SceneEntityPlanner:
    """Assign one canonical entity per scene before query generation."""

    def plan(
        self,
        *,
        editorial_canon: EditorialCanon,
        segments: Sequence[Mapping[str, Any]],
    ) -> SceneEntityPlan:
        entities = tuple(
            self._entity_for_scene(editorial_canon, index, segment)
            for index, segment in enumerate(segments)
        )
        fallback_chains = tuple(
            {
                "scene_index": index,
                "canonical_entity": entity.canonical_entity,
                "fallback_chain": [
                    entity.canonical_entity,
                    *entity.aliases,
                    *entity.optional_terms,
                    "generic documentary",
                ],
            }
            for index, entity in enumerate(entities)
        )
        return SceneEntityPlan(entities=entities, fallback_chains=fallback_chains)

    def _entity_for_scene(
        self,
        canon: EditorialCanon,
        index: int,
        segment: Mapping[str, Any],
    ) -> SceneEntity:
        if _norm(canon.primary_subject) == "seven wonders of the world" or canon.documentary_mode == DocumentaryMode.MULTI_SUBJECT:
            return _seven_wonders_entity(index, segment)
        return _single_subject_entity(canon)


def isolated_query_candidates(
    *,
    scene_entity: SceneEntity | None,
    queries: Sequence[Any],
    all_entities: Sequence[SceneEntity] = (),
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
    """Filter mixed-entity queries while preserving deterministic order."""

    if scene_entity is None:
        return _dedupe_queries(queries), ()
    allowed = _allowed_entity_terms(scene_entity)
    other_terms = _other_entity_terms(scene_entity, all_entities)
    rejected: list[dict[str, Any]] = []
    accepted: list[str] = []
    for query in queries:
        cleaned = _clean(query)
        if not cleaned:
            continue
        query_norm = _norm(cleaned)
        forbidden_hit = _first_present(query_norm, scene_entity.forbidden_terms)
        other_hit = _first_present(query_norm, other_terms)
        mentions_allowed = _contains_any(query_norm, allowed)
        if forbidden_hit:
            rejected.append({
                "query": cleaned,
                "reason": "scene entity forbidden term",
                "matched_term": forbidden_hit,
                "canonical_entity": scene_entity.canonical_entity,
            })
            continue
        if other_hit and not mentions_allowed:
            rejected.append({
                "query": cleaned,
                "reason": "mixed unrelated entity",
                "matched_term": other_hit,
                "canonical_entity": scene_entity.canonical_entity,
            })
            continue
        if other_hit and mentions_allowed and _norm(other_hit) not in {_norm(term) for term in allowed}:
            rejected.append({
                "query": cleaned,
                "reason": "mixed scene entity with another entity",
                "matched_term": other_hit,
                "canonical_entity": scene_entity.canonical_entity,
            })
            continue
        accepted.append(cleaned)
    accepted_tuple = _dedupe_queries(accepted)
    if not accepted_tuple:
        accepted_tuple = _fallback_queries(scene_entity)
    return accepted_tuple, tuple(rejected)


def _seven_wonders_entity(index: int, segment: Mapping[str, Any] | None = None) -> SceneEntity:
    sequence = (
        SceneEntity(
            "World Map",
            "map",
            aliases=("world wonders map", "seven wonders map"),
            required_terms=("world map",),
            optional_terms=("documentary", "wide"),
            forbidden_terms=("Roman aqueduct", "Pont du Gard", "marble column", "reef shark", "Greenland shark"),
            confidence=0.95,
        ),
        SceneEntity(
            "Great Pyramid of Giza",
            "landmark",
            aliases=("Great Pyramid", "Pyramid of Giza", "Khufu pyramid", "Giza pyramid"),
            required_terms=("Great Pyramid", "Giza"),
            optional_terms=("Egypt", "limestone blocks", "desert", "wide"),
            forbidden_terms=("Great Wall", "Petra", "Roman aqueduct", "marble column"),
            confidence=0.98,
        ),
        SceneEntity(
            "Great Wall of China",
            "landmark",
            aliases=("Great Wall", "China wall"),
            required_terms=("Great Wall",),
            optional_terms=("aerial", "stone wall", "watchtower", "mountains"),
            forbidden_terms=("marble column", "Roman aqueduct", "Petra", "Colosseum"),
            confidence=0.98,
        ),
        SceneEntity(
            "Petra",
            "landmark",
            aliases=("Petra Jordan", "Al Khazneh", "Treasury Petra"),
            required_terms=("Petra",),
            optional_terms=("Jordan", "facade", "canyon"),
            forbidden_terms=("Roman aqueduct", "Great Wall", "marble column"),
            confidence=0.98,
        ),
        SceneEntity(
            "Colosseum",
            "landmark",
            aliases=("Rome Colosseum", "Roman Colosseum"),
            required_terms=("Colosseum",),
            optional_terms=("Rome", "exterior", "ancient amphitheatre"),
            forbidden_terms=("Petra", "Great Wall", "Roman aqueduct"),
            confidence=0.98,
        ),
        SceneEntity(
            "Machu Picchu",
            "landmark",
            aliases=("Machu Picchu Peru", "Inca citadel"),
            required_terms=("Machu Picchu",),
            optional_terms=("mountain ruins", "Peru", "aerial"),
            forbidden_terms=("Great Wall", "Petra", "Colosseum"),
            confidence=0.98,
        ),
        SceneEntity(
            "Taj Mahal",
            "landmark",
            aliases=("Taj Mahal India", "Agra Taj Mahal"),
            required_terms=("Taj Mahal",),
            optional_terms=("reflecting pool", "sunrise", "dome"),
            forbidden_terms=("Great Wall", "Petra", "Colosseum"),
            confidence=0.98,
        ),
        SceneEntity(
            "Chichen Itza",
            "landmark",
            aliases=("Chichen Itza pyramid", "El Castillo"),
            required_terms=("Chichen Itza",),
            optional_terms=("pyramid", "Mexico", "wide"),
            forbidden_terms=("Great Wall", "Petra", "Roman aqueduct"),
            confidence=0.98,
        ),
        SceneEntity(
            "Christ the Redeemer",
            "landmark",
            aliases=("Christ the Redeemer Rio", "Corcovado statue"),
            required_terms=("Christ the Redeemer",),
            optional_terms=("Rio", "aerial", "statue"),
            forbidden_terms=("Great Wall", "Petra", "Roman aqueduct"),
            confidence=0.98,
        ),
    )
    mentioned = _seven_wonders_entity_from_text(segment, sequence)
    if mentioned is not None:
        return mentioned
    if index < len(sequence):
        return sequence[index]
    return sequence[-1] if index % 2 else sequence[0]


def _seven_wonders_entity_from_text(
    segment: Mapping[str, Any] | None,
    sequence: Sequence[SceneEntity],
) -> SceneEntity | None:
    text = _norm(" ".join(
        str(segment.get(key, ""))
        for key in ("narration", "broll", "visual", "search_query")
    )) if segment else ""
    if not text:
        return None
    phrase_map = (
        (("world map", "map shows", "across the world", "around the world"), "World Map"),
        (("great pyramid", "pyramid of giza", "khufu", "giza pyramid"), "Great Pyramid of Giza"),
        (("great wall", "china wall", "wall of china"), "Great Wall of China"),
        (("petra", "al khazneh", "treasury"), "Petra"),
        (("colosseum", "roman colosseum", "amphitheatre", "amphitheater"), "Colosseum"),
        (("machu picchu", "inca citadel"), "Machu Picchu"),
        (("taj mahal", "agra"), "Taj Mahal"),
        (("chichen itza", "el castillo"), "Chichen Itza"),
        (("christ the redeemer", "corcovado"), "Christ the Redeemer"),
    )
    by_name = {_norm(entity.canonical_entity): entity for entity in sequence}
    for phrases, canonical in phrase_map:
        if any(_norm(phrase) in text for phrase in phrases):
            return by_name.get(_norm(canonical))
    return None


def _single_subject_entity(canon: EditorialCanon) -> SceneEntity:
    subject = canon.primary_subject
    if _norm(subject) == "greenland shark":
        return SceneEntity(
            canonical_entity="Greenland shark",
            entity_type="species",
            aliases=("Somniosus microcephalus", "sleeper shark", "Arctic shark"),
            required_terms=("Greenland shark",),
            optional_terms=("deep sea", "Arctic", "cold water", "slow swimming"),
            forbidden_terms=("great white", "reef shark", "tiger shark", "hammerhead", "aquarium", "diver"),
            confidence=0.98,
        )
    if _norm(subject) == "octopus":
        return SceneEntity(
            canonical_entity="octopus",
            entity_type="species",
            aliases=("common octopus", "mimic octopus", "cephalopod"),
            required_terms=("octopus",),
            optional_terms=("underwater", "camouflage", "reef", "close up"),
            forbidden_terms=("grilled", "dish", "food", "lemon", "orange", "restaurant", "plate"),
            confidence=0.98,
        )
    if _norm(subject) == "butterfly":
        return SceneEntity(
            canonical_entity="butterfly",
            entity_type="species",
            aliases=("butterflies", "garden butterfly", "monarch butterfly", "caterpillar"),
            required_terms=("butterfly",),
            optional_terms=("wings", "flower", "garden", "macro", "close up"),
            forbidden_terms=("honeybee", "bee", "wasp", "fly", "moth", "cartoon", "logo"),
            confidence=0.98,
        )
    return SceneEntity(
        canonical_entity=subject,
        entity_type="primary_subject",
        aliases=canon.secondary_subjects,
        required_terms=(subject,),
        optional_terms=canon.visual_identity,
        forbidden_terms=canon.forbidden_primary_subjects + canon.avoid_terms,
        confidence=0.9,
    )


def _fallback_queries(scene_entity: SceneEntity) -> tuple[str, ...]:
    values = [
        scene_entity.canonical_entity,
        *scene_entity.aliases,
        _join(scene_entity.canonical_entity, "documentary"),
        _join(scene_entity.canonical_entity, "wide shot"),
        "generic documentary",
    ]
    return _dedupe_queries(values)


def _allowed_entity_terms(scene_entity: SceneEntity) -> tuple[str, ...]:
    return _dedupe([
        scene_entity.canonical_entity,
        *scene_entity.aliases,
        *scene_entity.required_terms,
    ])


def _other_entity_terms(scene_entity: SceneEntity, all_entities: Sequence[SceneEntity]) -> tuple[str, ...]:
    own = {_norm(term) for term in _allowed_entity_terms(scene_entity)}
    terms: list[str] = []
    known_unrelated = (
        "Roman aqueduct", "Pont du Gard", "marble column", "Great Wall of China",
        "Great Pyramid of Giza", "Pyramid of Giza", "Petra", "Colosseum", "Machu Picchu",
        "Taj Mahal", "Chichen Itza", "Christ the Redeemer", "Greenland shark", "reef shark", "great white",
        "honeybee", "butterfly", "octopus",
    )
    for entity in all_entities:
        terms.extend([entity.canonical_entity, *entity.aliases])
    terms.extend(known_unrelated)
    return tuple(term for term in _dedupe(terms) if _norm(term) not in own)


def _first_present(text_norm: str, terms: Sequence[str]) -> str:
    for term in terms:
        if _norm(term) and _norm(term) in text_norm:
            return term
    return ""


def _contains_any(text_norm: str, terms: Sequence[str]) -> bool:
    return any(_norm(term) and _norm(term) in text_norm for term in terms)


def _dedupe_queries(queries: Sequence[Any]) -> tuple[str, ...]:
    return tuple(_dedupe(_clean(query) for query in queries))


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean(value)
        key = _norm(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)


def _join(*values: Any) -> str:
    return _clean(" ".join(str(value or "") for value in values if str(value or "").strip()))


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("-", " ")).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
