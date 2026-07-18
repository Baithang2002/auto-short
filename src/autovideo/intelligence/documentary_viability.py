"""Pre-production topic viability scoring for automated documentaries."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from .topic_metadata import TopicCategory, classify_topic


class DocumentaryViabilityDecision(str, Enum):
    """Possible automation decisions for a candidate documentary topic."""

    APPROVED = "APPROVED"
    REVIEW = "REVIEW"
    SKIP = "SKIP"


@dataclass(frozen=True)
class DocumentaryViabilityConfig:
    """Configuration for the documentary viability gate."""

    enabled: bool = True
    minimum_viability_score: float = 0.62
    review_minimum_score: float = 0.45
    allow_review_topics: bool = True
    weights: Mapping[str, float] = field(default_factory=lambda: {
        "visual_availability": 0.28,
        "provider_coverage": 0.22,
        "documentary_potential": 0.22,
        "entity_risk": 0.13,
        "fallback_dependency": 0.15,
    })

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DocumentaryViabilityConfig":
        """Build config from environment variables with conservative defaults."""

        values = env or os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_DOCUMENTARY_VIABILITY_ENABLED", True),
            minimum_viability_score=_env_float(
                values,
                "AUTO_VIDEO_MIN_DOCUMENTARY_VIABILITY_SCORE",
                0.62,
            ),
            review_minimum_score=_env_float(
                values,
                "AUTO_VIDEO_REVIEW_DOCUMENTARY_VIABILITY_SCORE",
                0.45,
            ),
            allow_review_topics=_env_bool(
                values,
                "AUTO_VIDEO_ALLOW_REVIEW_TOPICS",
                True,
            ),
            weights=_weights_from_env(values),
        )


@dataclass(frozen=True)
class ViabilityCategoryScore:
    """One normalized viability score and its reasoning."""

    name: str
    score: float
    reasons: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Serialize the category score for diagnostics."""

        return {
            "name": self.name,
            "score": round(_clamp(self.score), 4),
            "reasons": list(self.reasons),
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
        }


@dataclass(frozen=True)
class DocumentaryViabilityReport:
    """Complete topic viability decision and diagnostics."""

    topic: str
    category_scores: tuple[ViabilityCategoryScore, ...]
    overall_score: float
    decision: DocumentaryViabilityDecision
    reasons: tuple[str, ...]
    expected_strengths: tuple[str, ...]
    expected_weaknesses: tuple[str, ...]
    recommended_strategy: tuple[str, ...]
    enabled: bool = True
    allow_review_topics: bool = True

    def to_dict(self) -> dict[str, object]:
        """Serialize the report to a stable JSON-friendly shape."""

        return {
            "topic": self.topic,
            "enabled": self.enabled,
            "allow_review_topics": self.allow_review_topics,
            "category_scores": {
                score.name: score.to_dict()
                for score in self.category_scores
            },
            "overall_score": round(_clamp(self.overall_score), 4),
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "expected_strengths": list(self.expected_strengths),
            "expected_weaknesses": list(self.expected_weaknesses),
            "recommended_production_strategy": list(self.recommended_strategy),
        }


class ViabilityEvaluator:
    """Base class for deterministic topic viability evaluators."""

    name = "base"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        """Evaluate one topic and return a normalized score."""

        raise NotImplementedError


class VisualAvailabilityEvaluator(ViabilityEvaluator):
    """Estimate whether the topic has concrete visible subjects."""

    name = "visual_availability"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        text = _norm(topic)
        concrete_hits = _matches(text, CONCRETE_VISUAL_TERMS)
        abstract_hits = _matches(text, ABSTRACT_VISUAL_TERMS)
        score = 0.48 + min(0.32, len(concrete_hits) * 0.08) - min(0.28, len(abstract_hits) * 0.07)
        if _has_any(text, PROCESS_VISUAL_TERMS):
            score += 0.08
        if _has_any(text, DIAGRAM_FRIENDLY_TERMS):
            score += 0.04
        reasons = []
        if concrete_hits:
            reasons.append("topic contains concrete visual subjects: " + ", ".join(concrete_hits[:5]))
        if abstract_hits:
            reasons.append("topic contains abstract or invisible concepts: " + ", ".join(abstract_hits[:5]))
        return ViabilityCategoryScore(
            name=self.name,
            score=_clamp(score),
            reasons=tuple(reasons),
            strengths=tuple(concrete_hits[:6]),
            weaknesses=tuple(abstract_hits[:6]),
        )


class ProviderCoverageEvaluator(ViabilityEvaluator):
    """Estimate likely coverage from currently implemented source categories."""

    name = "provider_coverage"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        classification = classify_topic(topic)
        categories = classification.all_categories
        base_scores = {
            TopicCategory.WILDLIFE: 0.78,
            TopicCategory.NATURE: 0.72,
            TopicCategory.EARTH_SCIENCE: 0.70,
            TopicCategory.OCEAN_SCIENCE: 0.68,
            TopicCategory.SPACE: 0.82,
            TopicCategory.ASTRONOMY: 0.82,
            TopicCategory.TECHNOLOGY: 0.58,
            TopicCategory.HISTORY: 0.74,
            TopicCategory.GEOGRAPHY: 0.74,
            TopicCategory.CLIMATE: 0.62,
            TopicCategory.WEATHER: 0.74,
            TopicCategory.ENGINEERING: 0.70,
            TopicCategory.PSYCHOLOGY: 0.38,
            TopicCategory.BIOLOGY: 0.58,
            TopicCategory.PHYSICS: 0.58,
            TopicCategory.CHEMISTRY: 0.42,
            TopicCategory.ENVIRONMENT: 0.62,
        }
        scores = [base_scores.get(category, 0.5) for category in categories]
        score = max(scores) if scores else 0.5
        text = _norm(topic)
        if _has_any(text, ARCHIVE_RICH_TERMS):
            score += 0.08
        if _has_any(text, NASA_RICH_TERMS):
            score += 0.10
        if _has_any(text, STOCK_RICH_TERMS):
            score += 0.06
        if _has_any(text, ("volcano", "volcanoes", "lava", "earthquake", "geology")):
            score += 0.08
        if _has_any(text, LOW_PROVIDER_TERMS):
            score -= 0.12
        providers = _provider_strategy_for(categories, text)
        return ViabilityCategoryScore(
            name=self.name,
            score=_clamp(score),
            reasons=("classified as " + ", ".join(category.value for category in categories),),
            strengths=providers,
            weaknesses=tuple(_matches(text, LOW_PROVIDER_TERMS)[:5]),
        )


class DocumentaryPotentialEvaluator(ViabilityEvaluator):
    """Estimate whether the topic naturally supports a documentary arc."""

    name = "documentary_potential"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        text = _norm(topic)
        structure_hits = _matches(text, STORY_STRUCTURE_TERMS)
        fact_hits = _matches(text, FACT_RICH_TERMS)
        weak_hits = _matches(text, THIN_STORY_TERMS)
        score = 0.50 + min(0.25, len(structure_hits) * 0.06) + min(0.18, len(fact_hits) * 0.045)
        score -= min(0.24, len(weak_hits) * 0.08)
        if text.startswith(("why ", "how ", "what happens", "the science")):
            score += 0.08
        return ViabilityCategoryScore(
            name=self.name,
            score=_clamp(score),
            reasons=tuple(
                item for item in (
                    "supports process/history/evidence arc" if structure_hits else "",
                    "may become mostly conceptual explanation" if weak_hits else "",
                )
                if item
            ),
            strengths=tuple((structure_hits + fact_hits)[:6]),
            weaknesses=tuple(weak_hits[:6]),
        )


class EntityRiskEvaluator(ViabilityEvaluator):
    """Estimate entity ambiguity; high score means low ambiguity risk."""

    name = "entity_risk"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        text = _norm(topic)
        ambiguous = _matches(text, AMBIGUOUS_ENTITY_TERMS)
        specific = _matches(text, SPECIFIC_ENTITY_TERMS)
        generic = _matches(text, GENERIC_ENTITY_TERMS)
        score = 0.72 + min(0.16, len(specific) * 0.04)
        score -= min(0.32, len(ambiguous) * 0.12)
        score -= min(0.14, len(generic) * 0.045)
        return ViabilityCategoryScore(
            name=self.name,
            score=_clamp(score),
            reasons=tuple(
                item for item in (
                    "specific named entity reduces drift risk" if specific else "",
                    "ambiguous entity may cause wrong media" if ambiguous else "",
                    "generic entity may need stricter subject proof" if generic else "",
                )
                if item
            ),
            strengths=tuple(specific[:6]),
            weaknesses=tuple((ambiguous + generic)[:6]),
        )


class FallbackDependencyEvaluator(ViabilityEvaluator):
    """Estimate likely dependence on diagrams, explainers, or composer fallback."""

    name = "fallback_dependency"

    def evaluate(self, topic: str) -> ViabilityCategoryScore:
        text = _norm(topic)
        fallback_hits = _matches(text, FALLBACK_HEAVY_TERMS)
        real_media_hits = _matches(text, CONCRETE_VISUAL_TERMS)
        score = 0.70 - min(0.36, len(fallback_hits) * 0.08) + min(0.18, len(real_media_hits) * 0.035)
        if _has_any(text, DIAGRAM_FRIENDLY_TERMS):
            score += 0.04
        return ViabilityCategoryScore(
            name=self.name,
            score=_clamp(score),
            reasons=tuple(
                item for item in (
                    "topic likely needs diagrams/explainers" if fallback_hits else "",
                    "topic has real-world visual anchors" if real_media_hits else "",
                )
                if item
            ),
            strengths=tuple(real_media_hits[:6]),
            weaknesses=tuple(fallback_hits[:6]),
        )


class DocumentaryViabilityEngine:
    """Evaluate whether a topic is suitable for automated documentary production."""

    def __init__(
        self,
        config: DocumentaryViabilityConfig | None = None,
        evaluators: Sequence[ViabilityEvaluator] | None = None,
    ) -> None:
        self.config = config or DocumentaryViabilityConfig()
        self.evaluators = tuple(evaluators or (
            VisualAvailabilityEvaluator(),
            ProviderCoverageEvaluator(),
            DocumentaryPotentialEvaluator(),
            EntityRiskEvaluator(),
            FallbackDependencyEvaluator(),
        ))

    def evaluate(self, topic: str) -> DocumentaryViabilityReport:
        """Score a topic and return an automation decision."""

        category_scores = tuple(evaluator.evaluate(topic) for evaluator in self.evaluators)
        overall = self._overall_score(category_scores)
        decision = self._decision(overall)
        strengths = _dedupe(
            strength
            for score in category_scores
            for strength in score.strengths
        )
        weaknesses = _dedupe(
            weakness
            for score in category_scores
            for weakness in score.weaknesses
        )
        return DocumentaryViabilityReport(
            topic=topic,
            category_scores=category_scores,
            overall_score=overall,
            decision=decision,
            reasons=self._reasons(category_scores, overall, decision),
            expected_strengths=strengths,
            expected_weaknesses=weaknesses,
            recommended_strategy=self._recommended_strategy(topic, category_scores),
            enabled=self.config.enabled,
            allow_review_topics=self.config.allow_review_topics,
        )

    def _overall_score(self, scores: Sequence[ViabilityCategoryScore]) -> float:
        total_weight = 0.0
        weighted = 0.0
        for score in scores:
            weight = float(self.config.weights.get(score.name, 1.0))
            total_weight += weight
            weighted += _clamp(score.score) * weight
        if total_weight <= 0:
            return 0.0
        return _clamp(weighted / total_weight)

    def _decision(self, overall: float) -> DocumentaryViabilityDecision:
        if overall >= self.config.minimum_viability_score:
            return DocumentaryViabilityDecision.APPROVED
        if overall >= self.config.review_minimum_score:
            return DocumentaryViabilityDecision.REVIEW
        return DocumentaryViabilityDecision.SKIP

    def _reasons(
        self,
        scores: Sequence[ViabilityCategoryScore],
        overall: float,
        decision: DocumentaryViabilityDecision,
    ) -> tuple[str, ...]:
        reasons = [f"overall viability score {overall:.2f} => {decision.value}"]
        for score in scores:
            if score.score < 0.50:
                reasons.append(f"{score.name} is weak ({score.score:.2f})")
            elif score.score >= 0.72:
                reasons.append(f"{score.name} is strong ({score.score:.2f})")
        return tuple(reasons)

    def _recommended_strategy(
        self,
        topic: str,
        scores: Sequence[ViabilityCategoryScore],
    ) -> tuple[str, ...]:
        text = _norm(topic)
        classification = classify_topic(topic)
        strategy = list(_provider_strategy_for(classification.all_categories, text))
        fallback_score = next((score.score for score in scores if score.name == "fallback_dependency"), 1.0)
        if fallback_score < 0.58:
            strategy.append("diagrams or Hybrid Composer likely required")
        if _has_any(text, ("map", "where", "country", "world", "continent", "location")):
            strategy.append("maps")
        if _has_any(text, DIAGRAM_FRIENDLY_TERMS):
            strategy.append("scientific diagrams")
        return _dedupe(strategy)


CONCRETE_VISUAL_TERMS = (
    "volcano", "volcanoes", "lava", "penguin", "penguins", "octopus", "shark",
    "greenland shark", "butterfly", "butterflies", "bee", "bees", "honeybee",
    "roman", "aqueduct", "great wall", "pyramid", "petra", "colosseum", "taj mahal",
    "machu picchu", "titanic", "ship", "hurricane", "storm", "lightning", "aurora",
    "northern lights", "saturn", "mars", "moon", "sun", "glacier", "desert", "ocean",
    "coral", "forest", "mountain", "river", "map", "satellite",
)

ABSTRACT_VISUAL_TERMS = (
    "consciousness", "memory", "anxiety", "emotion", "invisible", "thought",
    "dream", "dreams", "mind", "belief", "personality", "decision", "algorithm",
    "code", "data", "magnetic field", "force field", "radiation", "particles",
)

PROCESS_VISUAL_TERMS = (
    "created", "create", "forms", "formed", "built", "work", "works", "happens",
    "survive", "survives", "changed", "protecting", "moves", "flows",
)

DIAGRAM_FRIENDLY_TERMS = (
    "magnetic", "magnetosphere", "field", "core", "particle", "particles",
    "current", "currents", "atmosphere", "orbit", "gravity", "pressure",
)

ARCHIVE_RICH_TERMS = (
    "roman", "ancient", "empire", "aqueduct", "great wall", "pyramid", "titanic",
    "civilization", "civilisation", "war", "archive", "history",
)

NASA_RICH_TERMS = (
    "space", "planet", "sun", "solar", "aurora", "nasa", "moon", "mars",
    "saturn", "earth from space", "magnetosphere",
)

STOCK_RICH_TERMS = (
    "wildlife", "animal", "ocean", "forest", "storm", "lightning", "volcano",
    "penguin", "octopus", "butterfly", "bee",
)

LOW_PROVIDER_TERMS = (
    "consciousness", "memory formation", "anxiety", "invisible mechanism",
    "inside your brain", "cellular pathway", "molecule", "chemical bond",
)

STORY_STRUCTURE_TERMS = (
    "how", "why", "what happens", "built", "created", "survives", "changed",
    "protecting", "strongest", "oldest", "largest", "secret", "mystery",
)

FACT_RICH_TERMS = (
    "ancient", "science", "engineering", "history", "earth", "space", "ocean",
    "wildlife", "civilization", "civilisation", "survival",
)

THIN_STORY_TERMS = (
    "list", "facts about facts", "motivation", "mindset", "productivity",
    "feeling", "thoughts", "emotions",
)

AMBIGUOUS_ENTITY_TERMS = (
    "jaguar", "mercury", "apple", "python", "java", "titan", "seal", "crane",
    "turkey", "bass", "ray", "saturn", "shark", "butterfly effect",
)

SPECIFIC_ENTITY_TERMS = (
    "greenland shark", "great wall of china", "great pyramid", "roman aqueduct",
    "northern lights", "aurora borealis", "giant pacific octopus", "blue ringed octopus",
    "titanic", "machu picchu", "taj mahal", "colosseum", "chichen itza",
)

GENERIC_ENTITY_TERMS = (
    "animal", "fish", "bird", "insect", "ship", "city", "building", "technology",
)

FALLBACK_HEAVY_TERMS = (
    "invisible", "magnetic field", "force field", "consciousness", "memory",
    "anxiety", "algorithm", "data", "inside", "molecule", "chemical", "particle",
    "particles", "core", "radiation", "shield",
)


def _provider_strategy_for(categories: Sequence[TopicCategory], text: str) -> tuple[str, ...]:
    strategy: list[str] = []
    if _has_any(text, ("volcano", "volcanoes", "lava", "earthquake", "geology")):
        strategy.append("geology/science archives and stock")
    if any(category in categories for category in (TopicCategory.SPACE, TopicCategory.ASTRONOMY)):
        strategy.append("NASA imagery")
    if TopicCategory.WEATHER in categories or _has_any(text, ("hurricane", "storm", "lightning")):
        strategy.append("weather/science sources")
    if TopicCategory.OCEAN_SCIENCE in categories:
        strategy.append("ocean/science stock and archives")
    if any(category in categories for category in (TopicCategory.EARTH_SCIENCE, TopicCategory.PHYSICS)):
        strategy.append("scientific diagrams")
    if TopicCategory.WILDLIFE in categories:
        strategy.append("wildlife-heavy stock")
    if any(category in categories for category in (TopicCategory.HISTORY, TopicCategory.ENGINEERING, TopicCategory.GEOGRAPHY)):
        strategy.append("historical and public-domain archives")
    if TopicCategory.TECHNOLOGY in categories:
        strategy.append("technology stock plus diagrams")
    if not strategy:
        strategy.append("general stock with strict fallback review")
    return tuple(strategy)


def _weights_from_env(env: Mapping[str, str]) -> Mapping[str, float]:
    defaults = DocumentaryViabilityConfig().weights
    return {
        key: _env_float(env, f"AUTO_VIDEO_VIABILITY_WEIGHT_{key.upper()}", value)
        for key, value in defaults.items()
    }


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", ""}


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _matches(text: str, terms: Sequence[str]) -> list[str]:
    return [term for term in terms if _norm(term) in text]


def _has_any(text: str, terms: Sequence[str]) -> bool:
    return any(_norm(term) in text for term in terms)


def _dedupe(values: Sequence[str] | object) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
