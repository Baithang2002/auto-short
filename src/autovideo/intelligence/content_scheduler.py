"""Deterministic autonomous scheduling for viable, non-repetitive topics."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .documentary_viability import (
    DocumentaryViabilityDecision,
    DocumentaryViabilityEngine,
    DocumentaryViabilityReport,
)
from .topic_metadata import TopicCategory, classify_topic
from .topic_sources import TopicCandidate


class SchedulingDecision(str, Enum):
    """The scheduler's outcome for a single candidate."""

    SELECTED = "SELECTED"
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ContentSchedulerConfig:
    """Configuration for deterministic autonomous topic selection."""

    enabled: bool = True
    topic_sources: tuple[str, ...] = ("topics.txt", "topics.json")
    max_candidates: int = 50
    topic_cooldown_days: int = 90
    subject_cooldown_days: int = 180
    category_cooldown_days: int = 7
    category_diversity_weight: float = 0.15
    uniqueness_weight: float = 0.25
    viability_weight: float = 0.60
    maximum_similarity_threshold: float = 0.72
    allow_review_topics: bool = False
    evergreen_topics: tuple[str, ...] = (
        "Why Volcanoes Create New Land",
        "How Penguins Survive Antarctica",
        "How the Northern Lights Are Created",
        "How Roman Aqueducts Changed Civilization",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ContentSchedulerConfig":
        """Load scheduler configuration without changing manual CLI behavior."""

        values = env or os.environ
        source_value = values.get(
            "AUTO_VIDEO_SCHEDULER_TOPIC_SOURCES",
            values.get("AUTO_VIDEO_SCHEDULER_TOPIC_SOURCE", "topics.txt,topics.json"),
        )
        sources = tuple(item.strip() for item in source_value.split(",") if item.strip())
        evergreen = tuple(
            item.strip()
            for item in values.get(
                "AUTO_VIDEO_SCHEDULER_EVERGREEN_TOPICS",
                "Why Volcanoes Create New Land,How Penguins Survive Antarctica,"
                "How the Northern Lights Are Created,How Roman Aqueducts Changed Civilization",
            ).split(",")
            if item.strip()
        )
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_AUTONOMOUS_SCHEDULER_ENABLED", True),
            topic_sources=sources or ("topics.txt",),
            max_candidates=max(1, _env_int(values, "AUTO_VIDEO_SCHEDULER_MAX_CANDIDATES", 50)),
            topic_cooldown_days=max(0, _env_int(values, "AUTO_VIDEO_SCHEDULER_TOPIC_COOLDOWN_DAYS", 90)),
            subject_cooldown_days=max(0, _env_int(values, "AUTO_VIDEO_SCHEDULER_SUBJECT_COOLDOWN_DAYS", 180)),
            category_cooldown_days=max(0, _env_int(values, "AUTO_VIDEO_SCHEDULER_CATEGORY_COOLDOWN_DAYS", 7)),
            category_diversity_weight=_non_negative_float(
                values,
                "AUTO_VIDEO_SCHEDULER_CATEGORY_DIVERSITY_WEIGHT",
                0.15,
            ),
            uniqueness_weight=_non_negative_float(
                values,
                "AUTO_VIDEO_SCHEDULER_UNIQUENESS_WEIGHT",
                0.25,
            ),
            viability_weight=_non_negative_float(
                values,
                "AUTO_VIDEO_SCHEDULER_VIABILITY_WEIGHT",
                0.60,
            ),
            maximum_similarity_threshold=_clamp(_env_float(
                values,
                "AUTO_VIDEO_SCHEDULER_MAX_SIMILARITY_THRESHOLD",
                0.72,
            )),
            allow_review_topics=_env_bool(values, "AUTO_VIDEO_SCHEDULER_ALLOW_REVIEW_TOPICS", False),
            evergreen_topics=evergreen,
        )


@dataclass(frozen=True)
class TopicIdentity:
    """Topic metadata used for durable uniqueness and diversity decisions."""

    primary_subject: str
    category: str
    documentary_angle: str
    subject_tokens: tuple[str, ...]
    topic_tokens: tuple[str, ...]


@dataclass(frozen=True)
class ContentHistoryRecord:
    """One durable scheduler decision or successfully generated documentary."""

    topic: str
    primary_subject: str
    category: str
    documentary_angle: str
    viability_score: float
    decision: str
    status: str
    reason: str
    recorded_at: str
    source: str = ""
    run_id: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize history while retaining backward-readable JSON."""

        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ContentHistoryRecord":
        """Deserialize one history record defensively."""

        return cls(
            topic=str(raw.get("topic", "")),
            primary_subject=str(raw.get("primary_subject", "")),
            category=str(raw.get("category", "")),
            documentary_angle=str(raw.get("documentary_angle", "")),
            viability_score=float(raw.get("viability_score", 0.0) or 0.0),
            decision=str(raw.get("decision", "")),
            status=str(raw.get("status", "")),
            reason=str(raw.get("reason", "")),
            recorded_at=str(raw.get("recorded_at", "")),
            source=str(raw.get("source", "")),
            run_id=str(raw.get("run_id", "")),
            generated_at=str(raw.get("generated_at", "")),
        )


@dataclass(frozen=True)
class ScheduledCandidate:
    """A complete, inspectable ranking result for one topic candidate."""

    topic: str
    source: str
    primary_subject: str
    category: str
    documentary_angle: str
    viability_score: float
    viability_decision: str
    uniqueness_score: float
    category_diversity_score: float
    similarity_score: float
    cooldown_active: bool
    ranking_score: float | None
    decision: SchedulingDecision
    selection_path: str = ""
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Serialize a candidate ranking for scheduler diagnostics."""

        payload = asdict(self)
        payload["decision"] = self.decision.value
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True)
class SchedulerResult:
    """Selection result, rankings, and diagnostics for one scheduling run."""

    selected: ScheduledCandidate | None
    candidates: tuple[ScheduledCandidate, ...]
    config: ContentSchedulerConfig
    evaluated_at: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the stable ``scheduler_report.json`` payload."""

        return {
            "evaluated_at": self.evaluated_at,
            "final_selected_topic": self.selected.topic if self.selected else None,
            "final_selected_candidate": self.selected.to_dict() if self.selected else None,
            "configuration": {
                "enabled": self.config.enabled,
                "topic_sources": list(self.config.topic_sources),
                "max_candidates": self.config.max_candidates,
                "topic_cooldown_days": self.config.topic_cooldown_days,
                "subject_cooldown_days": self.config.subject_cooldown_days,
                "category_cooldown_days": self.config.category_cooldown_days,
                "category_diversity_weight": self.config.category_diversity_weight,
                "uniqueness_weight": self.config.uniqueness_weight,
                "viability_weight": self.config.viability_weight,
                "maximum_similarity_threshold": self.config.maximum_similarity_threshold,
                "allow_review_topics": self.config.allow_review_topics,
                "evergreen_topics": list(self.config.evergreen_topics),
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def write_json(self, path: Path) -> Path:
        """Write the scheduler audit report to a predictable artifact path."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class ContentHistoryStore:
    """Filesystem-backed, atomic memory for scheduler decisions and completed runs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[ContentHistoryRecord]:
        """Load all valid history records, returning an empty history when absent."""

        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        records = payload.get("records", ()) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError(f"{self.path} must contain a records list")
        return [ContentHistoryRecord.from_dict(record) for record in records if isinstance(record, dict)]

    def save(self, records: Sequence[ContentHistoryRecord]) -> None:
        """Atomically save history without introducing a database dependency."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "records": [record.to_dict() for record in records]}, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def record_decisions(
        self,
        result: SchedulerResult,
        *,
        run_id: str,
    ) -> None:
        """Persist deferrals/rejections and the selected topic for the current run."""

        records = self.load()
        records = _upsert_non_generated_records(records, result.candidates, result.evaluated_at)
        if result.selected:
            records.append(_history_record(result.selected, result.evaluated_at, run_id=run_id))
        self.save(records)

    def mark_generated(self, *, run_id: str, generated_at: str | None = None) -> bool:
        """Mark the topic selected by a run as generated after queue/upload success."""

        records = self.load()
        timestamp = generated_at or _utc_now()
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            if record.run_id == run_id and record.status == "scheduled":
                records[index] = ContentHistoryRecord(
                    **{**record.to_dict(), "status": "generated", "generated_at": timestamp},
                )
                self.save(records)
                return True
        return False


class AutonomousContentScheduler:
    """Select the strongest viable topic while enforcing novelty and diversity."""

    def __init__(
        self,
        viability_engine: DocumentaryViabilityEngine | None = None,
        config: ContentSchedulerConfig | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.viability_engine = viability_engine or DocumentaryViabilityEngine()
        self.config = config or ContentSchedulerConfig()
        self._now = now or (lambda: datetime.now(UTC))

    def schedule(
        self,
        candidates: Iterable[TopicCandidate],
        history: Sequence[ContentHistoryRecord] = (),
    ) -> SchedulerResult:
        """Evaluate and rank candidates using only viability and scheduling state."""

        timestamp = self._now().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        source_candidates = tuple(candidates)[:self.config.max_candidates]
        evaluated: list[ScheduledCandidate] = []
        for candidate in source_candidates:
            viability = self.viability_engine.evaluate(candidate.topic)
            identity = topic_identity(candidate.topic)
            evaluated.append(self._evaluate_candidate(candidate, identity, viability, history))

        selected = self._choose_approved(evaluated)
        if selected is None:
            selected = self._choose_review_fallback(evaluated)
        if selected is None:
            evergreen = self._evaluate_evergreen(history)
            evaluated.extend(evergreen)
            selected = self._choose_approved(evergreen)
            if selected is None:
                selected = self._choose_review_fallback(evergreen)
            if selected is not None:
                selected = _with_selection_path(
                    selected,
                    "evergreen_fallback",
                    "fallback: no APPROVED source topic available",
                )
            if selected is None and evergreen:
                selected = self._promote_evergreen(evergreen)
        if selected is None and evaluated:
            selected = _promote(
                _highest_ranked(evaluated),
                "emergency_source_fallback",
                "fallback: evergreen pool is empty; selected the strongest available source topic",
            )
        if selected:
            evaluated = [
                selected if candidate.topic == selected.topic and candidate.source == selected.source else candidate
                for candidate in evaluated
            ]
        return SchedulerResult(selected=selected, candidates=tuple(evaluated), config=self.config, evaluated_at=timestamp)

    @staticmethod
    def _choose_approved(candidates: Sequence[ScheduledCandidate]) -> ScheduledCandidate | None:
        return _highest_ranked(
            candidate
            for candidate in candidates
            if candidate.decision == SchedulingDecision.SELECTED
            and candidate.viability_decision == DocumentaryViabilityDecision.APPROVED.value
        )

    def _choose_review_fallback(self, candidates: Sequence[ScheduledCandidate]) -> ScheduledCandidate | None:
        eligible: list[ScheduledCandidate] = []
        for candidate in candidates:
            if candidate.viability_decision != DocumentaryViabilityDecision.REVIEW.value:
                continue
            if candidate.cooldown_active:
                continue
            if candidate.decision == SchedulingDecision.SELECTED:
                eligible.append(_with_selection_path(candidate, "review_fallback", "fallback: no APPROVED topic available"))
            elif candidate.decision == SchedulingDecision.DEFERRED:
                eligible.append(_promote(candidate, "review_fallback", "fallback: no APPROVED topic available"))
        return _highest_ranked(eligible)

    def _evaluate_evergreen(
        self,
        history: Sequence[ContentHistoryRecord],
    ) -> list[ScheduledCandidate]:
        candidates = [
            TopicCandidate(topic=topic, source="evergreen")
            for topic in self.config.evergreen_topics
        ]
        evaluated = []
        for candidate in candidates:
            viability = self.viability_engine.evaluate(candidate.topic)
            evaluated.append(self._evaluate_candidate(candidate, topic_identity(candidate.topic), viability, history))
        return evaluated

    @staticmethod
    def _promote_evergreen(candidates: Sequence[ScheduledCandidate]) -> ScheduledCandidate:
        fallback = _highest_ranked(candidates)
        if fallback is None:
            raise RuntimeError("evergreen fallback selection requires at least one candidate")
        return _promote(
            fallback,
            "evergreen_fallback",
            "fallback: no viable source topic available; selected evergreen topic",
        )

    def _evaluate_candidate(
        self,
        candidate: TopicCandidate,
        identity: TopicIdentity,
        viability: DocumentaryViabilityReport,
        history: Sequence[ContentHistoryRecord],
    ) -> ScheduledCandidate:
        active_history = [record for record in history if record.status in {"scheduled", "generated"}]
        similarity = max((_topic_similarity(identity.topic_tokens, _tokens(record.topic)) for record in active_history), default=0.0)
        subject_match = any(
            identity.primary_subject and identity.primary_subject == record.primary_subject
            for record in active_history
        )
        topic_cooldown = _within_cooldown(
            candidate.topic,
            active_history,
            self.config.topic_cooldown_days,
            self._now(),
            lambda record: record.topic,
        )
        subject_cooldown = _within_cooldown(
            identity.primary_subject,
            active_history,
            self.config.subject_cooldown_days,
            self._now(),
            lambda record: record.primary_subject,
        )
        category_recent = _within_cooldown(
            identity.category,
            active_history,
            self.config.category_cooldown_days,
            self._now(),
            lambda record: record.category,
        )
        uniqueness = _clamp(1.0 - max(similarity, 1.0 if subject_match else 0.0))
        category_diversity = 0.0 if category_recent else 1.0
        reasons: list[str] = []

        if viability.decision == DocumentaryViabilityDecision.SKIP:
            reasons.append("viability engine rejected topic")
            decision = SchedulingDecision.REJECTED
        elif viability.decision == DocumentaryViabilityDecision.REVIEW and not self.config.allow_review_topics:
            reasons.append("viability engine deferred REVIEW topic")
            decision = SchedulingDecision.DEFERRED
        elif topic_cooldown:
            reasons.append("identical topic is inside cooldown")
            decision = SchedulingDecision.DEFERRED
        elif subject_cooldown:
            reasons.append("primary subject is inside cooldown")
            decision = SchedulingDecision.DEFERRED
        elif similarity >= self.config.maximum_similarity_threshold:
            reasons.append("topic is too similar to a previous documentary")
            decision = SchedulingDecision.DEFERRED
        else:
            decision = SchedulingDecision.SELECTED
            if category_recent:
                reasons.append("recent category receives a diversity penalty")
            if similarity > 0:
                reasons.append("partial historical topic similarity penalized")

        rank = None
        if decision == SchedulingDecision.SELECTED:
            rank = _weighted_score(
                viability.overall_score,
                uniqueness,
                category_diversity,
                self.config,
            )
        return ScheduledCandidate(
            topic=candidate.topic,
            source=candidate.source,
            primary_subject=identity.primary_subject,
            category=identity.category,
            documentary_angle=identity.documentary_angle,
            viability_score=round(viability.overall_score, 4),
            viability_decision=viability.decision.value,
            uniqueness_score=round(uniqueness, 4),
            category_diversity_score=category_diversity,
            similarity_score=round(similarity, 4),
            cooldown_active=topic_cooldown or subject_cooldown,
            ranking_score=round(rank, 4) if rank is not None else None,
            decision=decision,
            reasons=tuple(reasons),
        )


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "behind", "can", "create", "creates", "do", "does",
    "earth", "facts", "for", "from", "get", "hidden", "how", "if", "in", "into", "is", "it",
    "its", "life", "of", "on", "or", "our", "really", "secret", "still", "that", "the", "this",
    "to", "under", "what", "when", "why", "with", "world", "you", "your", "strange", "weird",
    "mind", "blowing", "actually", "about", "through", "than", "they", "their", "them", "inside",
    "biggest", "deepest", "extreme", "frozen", "immortal", "killer", "largest", "massive", "silent",
    "strangest", "underwater", "zombie",
}
_MODIFIERS = {
    "arctic", "blue", "giant", "greenland", "mimic", "northern", "pacific", "pistol", "roman",
    "vampire",
}
_SUBJECT_CLAUSE_MARKERS = {"and", "because", "can", "that", "where", "which", "who"}


def topic_identity(topic: str) -> TopicIdentity:
    """Derive a stable primary-subject approximation without provider or LLM input."""

    tokens = _tokens(topic)
    meaningful = [token for token in tokens if token not in _STOP_WORDS]
    category = classify_topic(topic).primary.value
    if not meaningful:
        primary = "general"
    else:
        subject_tokens = _subject_tokens(tokens, meaningful)
        primary = subject_tokens[0] if tokens[:1] in {("how",), ("why",)} else subject_tokens[-1]
        for index, token in enumerate(subject_tokens):
            if token in _MODIFIERS and index + 1 < len(subject_tokens):
                primary = f"{token} {subject_tokens[index + 1]}"
                break
    return TopicIdentity(
        primary_subject=primary,
        category=category,
        documentary_angle=_documentary_angle(tokens),
        subject_tokens=tuple(meaningful),
        topic_tokens=tuple(tokens),
    )


def _subject_tokens(tokens: Sequence[str], meaningful: Sequence[str]) -> list[str]:
    for index, token in enumerate(tokens):
        if token in _SUBJECT_CLAUSE_MARKERS:
            prefix = [item for item in tokens[:index] if item not in _STOP_WORDS]
            if prefix:
                return prefix
    return list(meaningful)


def _documentary_angle(tokens: Sequence[str]) -> str:
    if tokens[:2] == ("what", "happens"):
        return "scenario"
    if tokens and tokens[0] == "how":
        return "process"
    if tokens and tokens[0] == "why":
        return "explanation"
    if any(token in {"secret", "hidden", "mystery"} for token in tokens):
        return "reveal"
    if any(token in {"survive", "survives", "survival"} for token in tokens):
        return "survival"
    return "overview"


def _upsert_non_generated_records(
    records: list[ContentHistoryRecord],
    candidates: Sequence[ScheduledCandidate],
    recorded_at: str,
) -> list[ContentHistoryRecord]:
    result = list(records)
    for candidate in candidates:
        if candidate.decision == SchedulingDecision.SELECTED:
            continue
        record = _history_record(candidate, recorded_at)
        matching = next((
            index
            for index in range(len(result) - 1, -1, -1)
            if result[index].topic.lower() == record.topic.lower()
            and result[index].status in {"deferred", "rejected"}
        ), None)
        if matching is None:
            result.append(record)
        else:
            result[matching] = record
    return result


def _history_record(candidate: ScheduledCandidate, recorded_at: str, *, run_id: str = "") -> ContentHistoryRecord:
    status = "scheduled" if candidate.decision == SchedulingDecision.SELECTED else candidate.decision.value.lower()
    return ContentHistoryRecord(
        topic=candidate.topic,
        primary_subject=candidate.primary_subject,
        category=candidate.category,
        documentary_angle=candidate.documentary_angle,
        viability_score=candidate.viability_score,
        decision=candidate.decision.value,
        status=status,
        reason="; ".join(candidate.reasons),
        recorded_at=recorded_at,
        source=candidate.source,
        run_id=run_id,
    )


def _within_cooldown(
    value: str,
    records: Sequence[ContentHistoryRecord],
    cooldown_days: int,
    now: datetime,
    selector: Callable[[ContentHistoryRecord], str],
) -> bool:
    if not value or cooldown_days <= 0:
        return False
    threshold = now.astimezone(UTC) - timedelta(days=cooldown_days)
    for record in records:
        if _normalise(value) != _normalise(selector(record)):
            continue
        recorded = _parse_timestamp(record.generated_at or record.recorded_at)
        if recorded and recorded >= threshold:
            return True
    return False


def _weighted_score(viability: float, uniqueness: float, diversity: float, config: ContentSchedulerConfig) -> float:
    total = config.viability_weight + config.uniqueness_weight + config.category_diversity_weight
    if total <= 0:
        return 0.0
    return _clamp(
        (
            viability * config.viability_weight
            + uniqueness * config.uniqueness_weight
            + diversity * config.category_diversity_weight
        ) / total
    )


def _highest_ranked(candidates: Iterable[ScheduledCandidate]) -> ScheduledCandidate | None:
    ranked = sorted(
        candidates,
        key=lambda candidate: (-(candidate.ranking_score or candidate.viability_score), candidate.topic.lower()),
    )
    return ranked[0] if ranked else None


def _promote(candidate: ScheduledCandidate, selection_path: str, reason: str) -> ScheduledCandidate:
    return ScheduledCandidate(
        **{
            **candidate.to_dict(),
            "decision": SchedulingDecision.SELECTED,
            "selection_path": selection_path,
            "ranking_score": candidate.ranking_score or candidate.viability_score,
            "reasons": tuple((*candidate.reasons, reason)),
        },
    )


def _with_selection_path(candidate: ScheduledCandidate, selection_path: str, reason: str) -> ScheduledCandidate:
    return ScheduledCandidate(
        **{
            **candidate.to_dict(),
            "decision": candidate.decision,
            "selection_path": selection_path,
            "reasons": tuple((*candidate.reasons, reason)),
        },
    )


def _topic_similarity(first: Sequence[str], second: Sequence[str]) -> float:
    first_terms = set(first) - _STOP_WORDS
    second_terms = set(second) - _STOP_WORDS
    if not first_terms or not second_terms:
        return 0.0
    return len(first_terms & second_terms) / len(first_terms | second_terms)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_singularize(token) for token in re.findall(r"[a-z0-9]+", value.lower()))


def _singularize(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith(("ches", "oes", "ses", "shes", "xes", "zes")) and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _normalise(value: str) -> str:
    return " ".join(_tokens(value))


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", ""}


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


def _non_negative_float(env: Mapping[str, str], name: str, default: float) -> float:
    return max(0.0, _env_float(env, name, default))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
