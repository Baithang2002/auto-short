"""Bounded source-coverage policy for pre-production documentary planning."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class SourceCoverageDecision(str, Enum):
    """Decision emitted before expensive production stages begin."""

    APPROVED = "APPROVED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"


class ProviderProbeStatus(str, Enum):
    """Explicit outcome of one bounded provider availability probe."""

    SUCCESS = "SUCCESS"
    NO_RESULTS = "NO_RESULTS"
    UNCONFIGURED = "UNCONFIGURED"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INVALID_MEDIA = "INVALID_MEDIA"
    PROVIDER_ERROR = "PROVIDER_ERROR"


TECHNICAL_PROVIDER_STATUSES = frozenset({
    ProviderProbeStatus.UNCONFIGURED,
    ProviderProbeStatus.AUTH_ERROR,
    ProviderProbeStatus.RATE_LIMITED,
    ProviderProbeStatus.TIMEOUT,
    ProviderProbeStatus.INVALID_MEDIA,
    ProviderProbeStatus.PROVIDER_ERROR,
})


@dataclass(frozen=True)
class ProviderProbeOutcome:
    """Structured diagnostic for one provider probe."""

    provider: str
    status: ProviderProbeStatus
    candidates_found: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "candidates_found": self.candidates_found,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SourceCoverageConfig:
    """Bounded coverage policy independent of provider implementations."""

    enabled: bool = True
    minimum_scene_coverage_ratio: float = 0.67
    max_scenes: int = 6
    max_providers_per_scene: int = 3
    max_queries_per_scene: int = 1
    provider_timeout_sec: float = 6.0
    supporting_scene_score_ratio: float = 0.65

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SourceCoverageConfig":
        """Load the preflight limits from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_SOURCE_COVERAGE_ENABLED", True),
            minimum_scene_coverage_ratio=_clamp(_env_float(
                values,
                "AUTO_VIDEO_SOURCE_COVERAGE_MIN_RATIO",
                0.67,
            )),
            max_scenes=max(1, _env_int(values, "AUTO_VIDEO_SOURCE_COVERAGE_MAX_SCENES", 6)),
            max_providers_per_scene=max(1, _env_int(
                values,
                "AUTO_VIDEO_SOURCE_COVERAGE_MAX_PROVIDERS_PER_SCENE",
                3,
            )),
            max_queries_per_scene=max(1, _env_int(
                values,
                "AUTO_VIDEO_SOURCE_COVERAGE_MAX_QUERIES_PER_SCENE",
                1,
            )),
            provider_timeout_sec=max(1.0, _env_float(
                values,
                "AUTO_VIDEO_SOURCE_COVERAGE_PROVIDER_TIMEOUT_SEC",
                6.0,
            )),
            supporting_scene_score_ratio=_clamp(_env_float(
                values,
                "AUTO_VIDEO_SOURCE_COVERAGE_SUPPORTING_SCORE_RATIO",
                0.65,
            )),
        )


@dataclass(frozen=True)
class SceneCoverage:
    """Coverage outcome for one sampled ShotPlan scene."""

    scene_index: int
    canonical_entity: str
    documentary_role: str
    scene_importance: str
    query: str
    providers_attempted: tuple[str, ...]
    candidates_found: int
    accepted_candidates: int
    best_score: float | None
    covered: bool
    provider_outcomes: tuple[ProviderProbeOutcome, ...] = ()
    reasons: tuple[str, ...] = ()
    coverage_basis: str = "authentic_media"
    required_score: float | None = None

    @property
    def critical(self) -> bool:
        """Return whether a missing scene undermines the documentary."""

        return self.scene_importance.upper() in {"HOOK", "MAIN_REVEAL"}

    def to_dict(self) -> dict[str, object]:
        """Serialize this scene's bounded probe result."""

        payload = asdict(self)
        payload["providers_attempted"] = list(self.providers_attempted)
        payload["provider_outcomes"] = [outcome.to_dict() for outcome in self.provider_outcomes]
        payload["reasons"] = list(self.reasons)
        payload["critical"] = self.critical
        return payload


@dataclass(frozen=True)
class SourceCoverageReport:
    """Auditable topic-level decision based on sampled source coverage."""

    topic: str
    decision: SourceCoverageDecision
    scenes: tuple[SceneCoverage, ...]
    config: SourceCoverageConfig
    reasons: tuple[str, ...] = ()

    @property
    def coverage_ratio(self) -> float:
        """Return the fraction of sampled scenes with an acceptable candidate."""

        if not self.scenes:
            return 0.0
        return sum(scene.covered for scene in self.scenes) / len(self.scenes)

    @property
    def provider_outcomes(self) -> tuple[ProviderProbeOutcome, ...]:
        """Return all provider outcomes across sampled scenes."""

        return tuple(
            outcome
            for scene in self.scenes
            for outcome in scene.provider_outcomes
        )

    @property
    def failure_classification(self) -> str:
        """Separate content gaps from provider/infrastructure failures.

        A scene is technically inconclusive only when no provider completed a
        healthy probe. Successful probes and explicit NO_RESULTS outcomes are
        enough to classify the topic as a content gap and rotate candidates;
        authentication, quota, timeout, or provider failures remain terminal
        when they are the only evidence available for an uncovered scene.
        """

        if self.decision == SourceCoverageDecision.SKIPPED:
            return "SKIPPED"
        if self.decision != SourceCoverageDecision.DEFERRED:
            return "NONE"
        outcomes = self.provider_outcomes
        if not outcomes:
            return "CONTENT_COVERAGE_GAP"
        uncovered = [scene for scene in self.scenes if not scene.covered]
        if not uncovered:
            return "CONTENT_COVERAGE_GAP"
        technically_inconclusive = [
            scene
            for scene in uncovered
            if any(
                outcome.status in TECHNICAL_PROVIDER_STATUSES
                for outcome in scene.provider_outcomes
            )
            and not any(
                outcome.status in {
                    ProviderProbeStatus.SUCCESS,
                    ProviderProbeStatus.NO_RESULTS,
                }
                for outcome in scene.provider_outcomes
            )
        ]
        if not technically_inconclusive:
            return "CONTENT_COVERAGE_GAP"
        critical_uncovered = [scene for scene in uncovered if scene.critical]
        if critical_uncovered and any(scene in technically_inconclusive for scene in critical_uncovered):
            return "TECHNICAL_PROVIDER_FAILURE"
        if len(technically_inconclusive) == len(uncovered):
            return "TECHNICAL_PROVIDER_FAILURE"
        return "CONTENT_COVERAGE_GAP"

    def to_dict(self) -> dict[str, object]:
        """Serialize a stable ``source_coverage_report.json`` artifact."""

        probe_summary = {
            status.value: sum(outcome.status == status for outcome in self.provider_outcomes)
            for status in ProviderProbeStatus
            if any(outcome.status == status for outcome in self.provider_outcomes)
        }
        return {
            "topic": self.topic,
            "decision": self.decision.value,
            "failure_classification": self.failure_classification,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sampled_scene_count": len(self.scenes),
            "covered_scene_count": sum(scene.covered for scene in self.scenes),
            "critical_uncovered_scenes": [
                scene.scene_index for scene in self.scenes if scene.critical and not scene.covered
            ],
            "reasons": list(self.reasons),
            "provider_probe_summary": probe_summary,
            "configuration": asdict(self.config),
            "scenes": [scene.to_dict() for scene in self.scenes],
        }

    def write_json(self, path: Path) -> Path:
        """Write the report as a durable preflight artifact."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class SourceCoverageEvaluator:
    """Apply a deterministic approval policy to bounded scene probes."""

    def __init__(self, config: SourceCoverageConfig | None = None) -> None:
        self.config = config or SourceCoverageConfig()

    def evaluate(self, topic: str, scenes: Sequence[SceneCoverage]) -> SourceCoverageReport:
        """Return approval when sampled scenes have sufficient real-media coverage."""

        if not self.config.enabled:
            return SourceCoverageReport(
                topic=topic,
                decision=SourceCoverageDecision.SKIPPED,
                scenes=tuple(scenes),
                config=self.config,
                reasons=("source coverage preflight disabled by configuration",),
            )
        if not scenes:
            return SourceCoverageReport(
                topic=topic,
                decision=SourceCoverageDecision.DEFERRED,
                scenes=(),
                config=self.config,
                reasons=("no scenes were available for source coverage probing",),
            )
        covered_count = sum(scene.covered for scene in scenes)
        ratio = covered_count / len(scenes)
        critical = [scene for scene in scenes if scene.critical and not scene.covered]
        reasons: list[str] = []
        if critical:
            reasons.append("critical scenes lack acceptable authentic-media candidates")
        # Compare and report counts, not only rounded percentages: with 6 scenes
        # and a 0.67 policy, 4/6 == 66.67% must fail while 5/6 == 83.3% must
        # pass. The old message rounded both sides and printed the confusing
        # "67% is below required 67%" diagnostic.
        minimum_covered = math.ceil(self.config.minimum_scene_coverage_ratio * len(scenes))
        if covered_count < minimum_covered:
            reasons.append(
                f"scene coverage {covered_count}/{len(scenes)} ({ratio:.0%}) is below required "
                f"{minimum_covered}/{len(scenes)} ({self.config.minimum_scene_coverage_ratio:.0%})"
            )
        return SourceCoverageReport(
            topic=topic,
            decision=SourceCoverageDecision.DEFERRED if reasons else SourceCoverageDecision.APPROVED,
            scenes=tuple(scenes),
            config=self.config,
            reasons=tuple(reasons or ("sampled scene coverage meets the policy",)),
        )


def verified_critical_scene_coverage(
    intent: Any,
    critical_asset_plan: Mapping[str, Any] | None,
) -> SceneCoverage | None:
    """Return coverage proven by an existing downloaded, frame-verified lock."""

    if not isinstance(critical_asset_plan, Mapping):
        return None
    if str(critical_asset_plan.get("status") or "").upper() != "VERIFIED":
        return None
    scene_index = int(getattr(intent, "scene_index", 0))
    role = next((
        item for item in critical_asset_plan.get("roles", ())
        if isinstance(item, Mapping)
        and _optional_int(item.get("scene_index")) == scene_index
        and str(item.get("status") or "").upper() == "VERIFIED"
    ), None)
    if role is None:
        return None
    selected = role.get("selected")
    if not isinstance(selected, Mapping):
        return None
    verification = selected.get("verification")
    if not isinstance(verification, Mapping):
        return None
    if str(verification.get("decision") or "").casefold() != "verified":
        return None
    local_path = Path(str(selected.get("local_path") or ""))
    if not local_path.is_file():
        return None
    provider = str(selected.get("provider") or "")
    if not provider or any(
        marker in provider.casefold()
        for marker in ("generated", "pollinations", "hybrid", "local_explainer")
    ):
        return None

    expected_entity = str(
        role.get("expected_entity")
        or verification.get("expected_entity")
        or verification.get("verified_entity")
        or getattr(intent, "primary_subject", "")
    )
    query = str(selected.get("query") or next(iter(role.get("queries") or ()), expected_entity))
    score = _optional_float(selected.get("score"))
    return SceneCoverage(
        scene_index=scene_index,
        canonical_entity=expected_entity,
        documentary_role=str(getattr(intent, "documentary_role", "")),
        scene_importance=str(getattr(intent, "scene_importance", "")),
        query=query,
        providers_attempted=(provider,),
        candidates_found=1,
        accepted_candidates=1,
        best_score=score,
        covered=True,
        provider_outcomes=(ProviderProbeOutcome(
            provider=provider,
            status=ProviderProbeStatus.SUCCESS,
            candidates_found=1,
            detail="downloaded critical asset passed frame verification",
        ),),
        reasons=("reused downloaded frame-verified critical asset lock",),
        coverage_basis="verified_critical_asset_lock",
        required_score=None,
    )


def sample_scene_indexes(total_scenes: int, maximum: int) -> tuple[int, ...]:
    """Choose evenly distributed zero-based scene indexes within a fixed budget."""

    if total_scenes <= 0 or maximum <= 0:
        return ()
    if maximum == 1:
        return (0,)
    if total_scenes <= maximum:
        return tuple(range(total_scenes))
    indexes = {0, total_scenes - 1}
    for position in range(maximum):
        indexes.add(round(position * (total_scenes - 1) / (maximum - 1)))
    return tuple(sorted(indexes))[:maximum]


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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
