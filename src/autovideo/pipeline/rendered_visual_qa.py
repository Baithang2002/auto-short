"""Bounded final-render visual checks for unattended publishing.

This policy intentionally has no knowledge of FFmpeg, Gemini, or Timeline.
The pipeline supplies one representative final-render frame per selected scene
and injects a verifier.  That keeps the policy unit-testable while allowing
the production adapter to inspect exactly what viewers will see after crop,
captions, and composition.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence


class RenderedVisualDecision(str, Enum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RenderedVisualQAConfig:
    enabled: bool = False
    max_scenes: int = 4
    minimum_confidence: float = 0.80

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "RenderedVisualQAConfig":
        env = values if values is not None else os.environ
        return cls(
            enabled=_env_bool(env, "AUTO_VIDEO_RENDERED_VISUAL_QA_ENABLED", False),
            max_scenes=max(1, _env_int(env, "AUTO_VIDEO_RENDERED_VISUAL_QA_MAX_SCENES", 4)),
            minimum_confidence=_clamp(_env_float(
                env, "AUTO_VIDEO_RENDERED_VISUAL_QA_MIN_CONFIDENCE", 0.80
            )),
        )


@dataclass(frozen=True)
class RenderedSceneRequest:
    scene_index: int
    expected_entity: str
    visual_goal: str
    media_mode: str
    timestamp_sec: float
    frame_path: Path
    priority: str = "supporting"


@dataclass(frozen=True)
class RenderedVisualEvidence:
    match: bool
    confidence: float = 0.0
    matched_entity: str = ""
    reasoning: str = ""
    provider: str = ""
    error: str = ""


@dataclass(frozen=True)
class RenderedVisualSceneResult:
    request: RenderedSceneRequest
    decision: RenderedVisualDecision
    evidence: RenderedVisualEvidence | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        evidence = self.evidence
        return {
            "scene_index": self.request.scene_index,
            "expected_entity": self.request.expected_entity,
            "visual_goal": self.request.visual_goal,
            "media_mode": self.request.media_mode,
            "priority": self.request.priority,
            "timestamp_sec": round(self.request.timestamp_sec, 3),
            "frame_path": str(self.request.frame_path),
            "decision": self.decision.value,
            "reason": self.reason,
            "match": evidence.match if evidence else None,
            "confidence": round(evidence.confidence, 4) if evidence else 0.0,
            "matched_entity": evidence.matched_entity if evidence else "",
            "reasoning": evidence.reasoning if evidence else "",
            "provider": evidence.provider if evidence else "",
            "error": evidence.error if evidence else "",
        }


RenderedFrameVerifier = Callable[[RenderedSceneRequest], RenderedVisualEvidence | None]


@dataclass(frozen=True)
class RenderedVisualQAReport:
    config: RenderedVisualQAConfig
    scenes: tuple[RenderedVisualSceneResult, ...] = ()

    @property
    def has_mismatch(self) -> bool:
        return any(scene.decision is RenderedVisualDecision.MISMATCH for scene in self.scenes)

    def to_dict(self) -> dict[str, object]:
        counts = {decision.value: 0 for decision in RenderedVisualDecision}
        for scene in self.scenes:
            counts[scene.decision.value] += 1
        return {
            "enabled": self.config.enabled,
            "scenes": [scene.to_dict() for scene in self.scenes],
            "summary": {
                "checked_scene_count": len(self.scenes),
                "mismatch_count": counts[RenderedVisualDecision.MISMATCH.value],
                "unavailable_count": counts[RenderedVisualDecision.UNAVAILABLE.value],
                "verified_count": counts[RenderedVisualDecision.VERIFIED.value],
                "skipped_count": counts[RenderedVisualDecision.SKIPPED.value],
            },
            "configuration": asdict(self.config),
        }

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class RenderedVisualQAGate:
    """Evaluate final rendered frames without changing the render pipeline."""

    def __init__(self, config: RenderedVisualQAConfig, verifier: RenderedFrameVerifier | None = None):
        self.config = config
        self._verifier = verifier

    def evaluate(self, requests: Sequence[RenderedSceneRequest]) -> RenderedVisualQAReport:
        selected = tuple(requests[:self.config.max_scenes])
        if not self.config.enabled:
            return RenderedVisualQAReport(
                self.config,
                tuple(
                    RenderedVisualSceneResult(
                        request, RenderedVisualDecision.SKIPPED, None,
                        "rendered visual QA disabled",
                    )
                    for request in selected
                ),
            )
        if self._verifier is None:
            return RenderedVisualQAReport(
                self.config,
                tuple(
                    RenderedVisualSceneResult(
                        request, RenderedVisualDecision.UNAVAILABLE, None,
                        "rendered-frame verifier is unavailable",
                    )
                    for request in selected
                ),
            )
        results = []
        for request in selected:
            if not request.frame_path.exists():
                results.append(RenderedVisualSceneResult(
                    request, RenderedVisualDecision.UNAVAILABLE, None,
                    "representative rendered frame is unavailable",
                ))
                continue
            evidence = self._verifier(request)
            if evidence is None or evidence.error:
                results.append(RenderedVisualSceneResult(
                    request, RenderedVisualDecision.UNAVAILABLE, evidence,
                    f"rendered-frame verification unavailable: {evidence.error if evidence else 'no evidence'}",
                ))
                continue
            if evidence.match and evidence.confidence >= self.config.minimum_confidence:
                results.append(RenderedVisualSceneResult(
                    request, RenderedVisualDecision.VERIFIED, evidence,
                    "rendered frame passed entity verification",
                ))
                continue
            results.append(RenderedVisualSceneResult(
                request, RenderedVisualDecision.MISMATCH, evidence,
                "rendered frame does not prove the planned entity",
            ))
        return RenderedVisualQAReport(self.config, tuple(results))


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    return default if value is None else str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
