"""Bounded source-coverage policy for pre-production documentary planning."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class SourceCoverageDecision(str, Enum):
    """Decision emitted before expensive production stages begin."""

    APPROVED = "APPROVED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class SourceCoverageConfig:
    """Bounded coverage policy independent of provider implementations."""

    enabled: bool = True
    minimum_scene_coverage_ratio: float = 0.67
    max_scenes: int = 6
    max_providers_per_scene: int = 2
    max_queries_per_scene: int = 1
    provider_timeout_sec: float = 6.0

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
                2,
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
    reasons: tuple[str, ...] = ()

    @property
    def critical(self) -> bool:
        """Return whether a missing scene undermines the documentary."""

        return self.scene_importance.upper() in {"HOOK", "MAIN_REVEAL"}

    def to_dict(self) -> dict[str, object]:
        """Serialize this scene's bounded probe result."""

        payload = asdict(self)
        payload["providers_attempted"] = list(self.providers_attempted)
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

    def to_dict(self) -> dict[str, object]:
        """Serialize a stable ``source_coverage_report.json`` artifact."""

        return {
            "topic": self.topic,
            "decision": self.decision.value,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sampled_scene_count": len(self.scenes),
            "covered_scene_count": sum(scene.covered for scene in self.scenes),
            "critical_uncovered_scenes": [
                scene.scene_index for scene in self.scenes if scene.critical and not scene.covered
            ],
            "reasons": list(self.reasons),
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
        ratio = sum(scene.covered for scene in scenes) / len(scenes)
        critical = [scene for scene in scenes if scene.critical and not scene.covered]
        reasons: list[str] = []
        if critical:
            reasons.append("critical scenes lack acceptable authentic-media candidates")
        if ratio < self.config.minimum_scene_coverage_ratio:
            reasons.append(
                f"scene coverage {ratio:.0%} is below required "
                f"{self.config.minimum_scene_coverage_ratio:.0%}"
            )
        return SourceCoverageReport(
            topic=topic,
            decision=SourceCoverageDecision.DEFERRED if reasons else SourceCoverageDecision.APPROVED,
            scenes=tuple(scenes),
            config=self.config,
            reasons=tuple(reasons or ("sampled scene coverage meets the policy",)),
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


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
