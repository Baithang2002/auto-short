"""Evidence verification for selected media candidates.

The verifier intentionally separates provider metadata proof from search-query
intent. Search queries can retrieve candidates, but they must not prove that a
candidate actually depicts the requested entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class EntityFidelity(str, Enum):
    EXACT_ENTITY = "exact_entity"
    EXACT_ALIAS = "exact_alias"
    CLOSE_SUBSTITUTE = "close_substitute"
    RELATED_ENTITY = "related_entity"
    GENERIC_CATEGORY = "generic_category"
    ENVIRONMENT_ONLY = "environment_only"
    UNKNOWN = "unknown"


_FIDELITY_SCORE = {
    EntityFidelity.EXACT_ENTITY: 1.0,
    EntityFidelity.EXACT_ALIAS: 0.88,
    EntityFidelity.CLOSE_SUBSTITUTE: 0.68,
    EntityFidelity.RELATED_ENTITY: 0.45,
    EntityFidelity.GENERIC_CATEGORY: 0.25,
    EntityFidelity.ENVIRONMENT_ONLY: 0.18,
    EntityFidelity.UNKNOWN: 0.0,
}


@dataclass(frozen=True)
class EvidenceVerificationConfig:
    enable_ai_visual_qa: bool = False
    ai_visual_qa_provider: str = "gemini"
    ai_visual_qa_min_metadata_confidence: float = 0.90
    ai_visual_qa_max_candidates: int = 3


@dataclass(frozen=True)
class VisionVerificationResult:
    match: bool
    matched_entity: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    provider: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "matched_entity": self.matched_entity,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "provider": self.provider,
            "error": self.error,
        }


@dataclass(frozen=True)
class EvidenceVerificationResult:
    requested_entity: str
    selected_entity: str
    entity_fidelity: EntityFidelity
    metadata_confidence: float
    metadata_evidence: tuple[str, ...] = ()
    vision_invoked: bool = False
    vision_result: VisionVerificationResult | None = None
    accepted: bool = False
    fallback_reason: str = ""
    candidate_id: str = ""
    candidate_ranking: int | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def score_bonus(self) -> float:
        return {
            EntityFidelity.EXACT_ENTITY: 5.5,
            EntityFidelity.EXACT_ALIAS: 4.0,
            EntityFidelity.CLOSE_SUBSTITUTE: 1.5,
            EntityFidelity.RELATED_ENTITY: -1.0,
            EntityFidelity.GENERIC_CATEGORY: -3.0,
            EntityFidelity.ENVIRONMENT_ONLY: -4.0,
            EntityFidelity.UNKNOWN: -5.0,
        }[self.entity_fidelity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_entity": self.requested_entity,
            "selected_entity": self.selected_entity,
            "entity_fidelity": self.entity_fidelity.value,
            "metadata_confidence": self.metadata_confidence,
            "metadata_evidence": list(self.metadata_evidence),
            "vision_invoked": self.vision_invoked,
            "vision_result": self.vision_result.to_dict() if self.vision_result else None,
            "vision_confidence": self.vision_result.confidence if self.vision_result else None,
            "accepted": self.accepted,
            "fallback_reason": self.fallback_reason,
            "candidate_id": self.candidate_id,
            "candidate_ranking": self.candidate_ranking,
            "diagnostics": self.diagnostics,
        }


class CandidateLike(Protocol):
    provider: str
    provider_id: str
    title: str
    description: str
    url: str
    download_url: str
    local_path: Path | None
    raw_metadata: dict[str, Any]


VisionVerifier = Callable[[str, CandidateLike], VisionVerificationResult | None]


class EvidenceVerificationEngine:
    def __init__(
        self,
        config: EvidenceVerificationConfig | None = None,
        vision_verifier: VisionVerifier | None = None,
    ) -> None:
        self.config = config or EvidenceVerificationConfig()
        self.vision_verifier = vision_verifier
        self._vision_calls = 0

    def verify(
        self,
        *,
        requested_entity: str,
        aliases: tuple[str, ...] = (),
        close_substitutes: tuple[str, ...] = (),
        related_entities: tuple[str, ...] = (),
        generic_categories: tuple[str, ...] = (),
        environment_terms: tuple[str, ...] = (),
        forbidden_entities: tuple[str, ...] = (),
        candidate: CandidateLike,
        candidate_ranking: int | None = None,
    ) -> EvidenceVerificationResult:
        metadata_text = candidate_metadata_text(candidate)
        fidelity, selected_entity, evidence = _metadata_fidelity(
            metadata_text=metadata_text,
            requested_entity=requested_entity,
            aliases=aliases,
            close_substitutes=close_substitutes,
            related_entities=related_entities,
            generic_categories=generic_categories,
            environment_terms=environment_terms,
            forbidden_entities=forbidden_entities,
        )
        metadata_confidence = _FIDELITY_SCORE[fidelity]
        vision_invoked = False
        vision_result: VisionVerificationResult | None = None
        highly_specific = _highly_specific_entity(requested_entity)
        if (
            self.config.enable_ai_visual_qa
            and self.vision_verifier is not None
            and self._vision_calls < max(0, self.config.ai_visual_qa_max_candidates)
            and (
                metadata_confidence < self.config.ai_visual_qa_min_metadata_confidence
                or highly_specific
            )
        ):
            vision_invoked = True
            self._vision_calls += 1
            try:
                vision_result = self.vision_verifier(requested_entity, candidate)
            except Exception as exc:  # Vision must never fail the pipeline.
                vision_result = VisionVerificationResult(
                    match=False,
                    provider=self.config.ai_visual_qa_provider,
                    error=str(exc),
                )
            if vision_result and vision_result.match and vision_result.confidence >= 0.70:
                selected_entity = vision_result.matched_entity or selected_entity or requested_entity
                metadata_confidence = max(metadata_confidence, min(1.0, vision_result.confidence))
                if fidelity in {EntityFidelity.GENERIC_CATEGORY, EntityFidelity.ENVIRONMENT_ONLY, EntityFidelity.UNKNOWN}:
                    fidelity = EntityFidelity.EXACT_ALIAS if selected_entity else EntityFidelity.CLOSE_SUBSTITUTE

        accepted = fidelity in {
            EntityFidelity.EXACT_ENTITY,
            EntityFidelity.EXACT_ALIAS,
            EntityFidelity.CLOSE_SUBSTITUTE,
        }
        if vision_result and vision_invoked and not vision_result.error and not vision_result.match and highly_specific:
            accepted = False
        fallback_reason = "" if accepted else _fallback_reason(fidelity)
        return EvidenceVerificationResult(
            requested_entity=requested_entity,
            selected_entity=selected_entity,
            entity_fidelity=fidelity,
            metadata_confidence=round(metadata_confidence, 3),
            metadata_evidence=evidence,
            vision_invoked=vision_invoked,
            vision_result=vision_result,
            accepted=accepted,
            fallback_reason=fallback_reason,
            candidate_id=f"{candidate.provider}:{candidate.provider_id}",
            candidate_ranking=candidate_ranking,
            diagnostics={
                "metadata_text_fields_only": True,
                "query_used_as_evidence": False,
                "highly_specific_entity": highly_specific,
            },
        )


def candidate_metadata_text(candidate: CandidateLike) -> str:
    """Return provider metadata text without candidate.query."""

    raw = candidate.raw_metadata if isinstance(candidate.raw_metadata, Mapping) else {}
    raw_values = []
    for key, value in raw.items():
        key_norm = _norm(key)
        if key_norm in {"query", "search_query", "selected_query"}:
            continue
        if isinstance(value, (str, int, float)):
            raw_values.append(str(value))
        elif isinstance(value, (list, tuple)):
            raw_values.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    values = [
        candidate.provider,
        candidate.provider_id,
        candidate.title,
        candidate.description,
        candidate.url,
        candidate.download_url,
        Path(candidate.local_path).name if candidate.local_path else "",
        *raw_values,
    ]
    return _norm(" ".join(values))


def _metadata_fidelity(
    *,
    metadata_text: str,
    requested_entity: str,
    aliases: tuple[str, ...],
    close_substitutes: tuple[str, ...],
    related_entities: tuple[str, ...],
    generic_categories: tuple[str, ...],
    environment_terms: tuple[str, ...],
    forbidden_entities: tuple[str, ...],
) -> tuple[EntityFidelity, str, tuple[str, ...]]:
    forbidden = _first_match(metadata_text, forbidden_entities)
    exact = _first_match(metadata_text, (requested_entity,))
    if exact and not forbidden:
        return EntityFidelity.EXACT_ENTITY, requested_entity, (exact,)
    alias = _first_match(metadata_text, aliases)
    if alias and not forbidden:
        return EntityFidelity.EXACT_ALIAS, alias, (alias,)
    close = _first_match(metadata_text, close_substitutes)
    if close and not forbidden:
        return EntityFidelity.CLOSE_SUBSTITUTE, close, (close,)
    related = _first_match(metadata_text, related_entities)
    if related:
        return EntityFidelity.RELATED_ENTITY, related, (related,)
    generic = _first_match(metadata_text, generic_categories)
    if generic:
        return EntityFidelity.GENERIC_CATEGORY, generic, (generic,)
    environment = _first_match(metadata_text, environment_terms)
    if environment:
        return EntityFidelity.ENVIRONMENT_ONLY, environment, (environment,)
    return EntityFidelity.UNKNOWN, "", ()


def _highly_specific_entity(entity: str) -> bool:
    text = _norm(entity)
    if len([token for token in text.split() if token]) >= 2:
        return True
    proper_specific = {
        "titanic",
        "petra",
        "colosseum",
        "chichen itza",
        "machu picchu",
        "taj mahal",
        "greenland shark",
        "blue ringed octopus",
    }
    return text in proper_specific


def _fallback_reason(fidelity: EntityFidelity) -> str:
    return {
        EntityFidelity.RELATED_ENTITY: "only related entity proven in metadata",
        EntityFidelity.GENERIC_CATEGORY: "only generic category proven in metadata",
        EntityFidelity.ENVIRONMENT_ONLY: "only habitat/environment proven in metadata",
        EntityFidelity.UNKNOWN: "requested entity not proven in metadata",
        EntityFidelity.EXACT_ENTITY: "",
        EntityFidelity.EXACT_ALIAS: "",
        EntityFidelity.CLOSE_SUBSTITUTE: "",
    }[fidelity]


def _first_match(text: str, terms: tuple[str, ...]) -> str:
    padded = f" {text} "
    for term in terms:
        normalized = _norm(term)
        if not normalized:
            continue
        if " " in normalized:
            if normalized in text:
                return term
        elif f" {normalized} " in padded:
            return term
    return ""


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
