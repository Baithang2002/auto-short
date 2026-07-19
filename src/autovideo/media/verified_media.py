"""Final, downloaded-media verification policy.

The selection layer deliberately works from provider metadata.  This module is
the separate last gate that decides whether the *downloaded* media is suitable
for the planned scene.  Provider and renderer concerns stay outside this
module: callers inject a frame-aware verifier and decide how to retrieve a
replacement candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence


class VerificationPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationDecision(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VerifiedMediaGateConfig:
    """Runtime policy for final downloaded-media verification."""

    enabled: bool = False
    critical_confidence_threshold: float = 0.85
    critical_action_confidence_threshold: float = 0.75
    max_replacement_attempts: int = 2
    critical_scene_policy: str = "abort"
    frame_sample_count: int = 3
    allow_unverified_lower_priority: bool = True
    allow_unverified_when_vision_unavailable: bool = True

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> "VerifiedMediaGateConfig":
        def flag(name: str, default: bool) -> bool:
            raw = values.get(name)
            if raw is None:
                return default
            return str(raw).strip().lower() not in {"", "0", "false", "no", "off"}

        def number(name: str, default: float) -> float:
            try:
                return float(values.get(name, default))
            except (TypeError, ValueError):
                return default

        def whole(name: str, default: int) -> int:
            try:
                return max(0, int(values.get(name, default)))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=flag("AUTO_VIDEO_VERIFIED_MEDIA_GATE_ENABLED", False),
            critical_confidence_threshold=max(0.0, min(1.0, number(
                "AUTO_VIDEO_VERIFIED_MEDIA_CRITICAL_CONFIDENCE", 0.85
            ))),
            critical_action_confidence_threshold=max(0.0, min(1.0, number(
                "AUTO_VIDEO_VERIFIED_MEDIA_CRITICAL_ACTION_CONFIDENCE", 0.75
            ))),
            max_replacement_attempts=whole("AUTO_VIDEO_VERIFIED_MEDIA_MAX_REPLACEMENTS", 2),
            critical_scene_policy=str(values.get(
                "AUTO_VIDEO_VERIFIED_MEDIA_CRITICAL_POLICY", "abort"
            )).strip().lower() or "abort",
            frame_sample_count=max(1, whole("AUTO_VIDEO_VERIFIED_MEDIA_FRAME_SAMPLES", 3)),
            allow_unverified_lower_priority=flag(
                "AUTO_VIDEO_VERIFIED_MEDIA_ALLOW_UNVERIFIED_LOWER_PRIORITY", True
            ),
            allow_unverified_when_vision_unavailable=flag(
                "AUTO_VIDEO_VERIFIED_MEDIA_ALLOW_VISION_UNAVAILABLE", True
            ),
        )


@dataclass(frozen=True)
class VerificationRequest:
    scene_index: int
    media_path: Path
    expected_entity: str
    expected_action: str = ""
    visual_goal: str = ""
    priority: VerificationPriority = VerificationPriority.MEDIUM


@dataclass(frozen=True)
class DownloadedMediaEvidence:
    """Result from a frame-aware verifier.

    Confidence is intentionally separate for entity and action.  An image can
    correctly show an octopus but cannot prove "camouflage"; treating that as
    the same signal caused earlier false acceptance.
    """

    entity_match: bool
    entity_confidence: float = 0.0
    action_match: bool | None = None
    action_confidence: float = 0.0
    verified_entity: str = ""
    verified_action: str = ""
    reasoning: str = ""
    sampled_frames: tuple[str, ...] = ()
    provider: str = ""
    error: str = ""


@dataclass(frozen=True)
class VerifiedMediaSceneResult:
    request: VerificationRequest
    decision: VerificationDecision
    evidence: DownloadedMediaEvidence | None
    reason: str
    replacement_attempt: int = 0

    @property
    def should_abort(self) -> bool:
        return (
            self.request.priority is VerificationPriority.CRITICAL
            and self.decision is VerificationDecision.REJECTED
        )

    def to_dict(self) -> dict[str, object]:
        evidence = self.evidence
        return {
            "scene_index": self.request.scene_index,
            "media_path": str(self.request.media_path),
            "expected_entity": self.request.expected_entity,
            "expected_action": self.request.expected_action,
            "visual_goal": self.request.visual_goal,
            "priority": self.request.priority.value,
            "decision": self.decision.value,
            "reason": self.reason,
            "replacement_attempt": self.replacement_attempt,
            "verified_entity": evidence.verified_entity if evidence else "",
            "verified_action": evidence.verified_action if evidence else "",
            "entity_confidence": evidence.entity_confidence if evidence else 0.0,
            "action_confidence": evidence.action_confidence if evidence else 0.0,
            "sampled_frames": list(evidence.sampled_frames) if evidence else [],
            "provider": evidence.provider if evidence else "",
            "reasoning": evidence.reasoning if evidence else "",
            "error": evidence.error if evidence else "",
        }


FrameVerifier = Callable[[VerificationRequest, int], DownloadedMediaEvidence | None]


class VerifiedMediaGate:
    """Apply acceptance policy to evidence produced from downloaded media."""

    def __init__(self, config: VerifiedMediaGateConfig, verifier: FrameVerifier | None = None):
        self.config = config
        self._verifier = verifier

    def evaluate(
        self,
        request: VerificationRequest,
        *,
        replacement_attempt: int = 0,
    ) -> VerifiedMediaSceneResult:
        if not self.config.enabled:
            return VerifiedMediaSceneResult(
                request, VerificationDecision.UNVERIFIED, None,
                "verified media gate disabled", replacement_attempt,
            )
        if not request.media_path.exists():
            return self._failed(request, "downloaded media file is missing", replacement_attempt)
        if self._verifier is None:
            return self._failed(request, "frame verifier is unavailable", replacement_attempt)

        evidence = self._verifier(request, self.config.frame_sample_count)
        if evidence is None:
            return self._failed(request, "frame verifier produced no evidence", replacement_attempt)
        if evidence.error:
            if (
                self.config.allow_unverified_when_vision_unavailable
                and _is_transient_verifier_error(evidence.error)
            ):
                return VerifiedMediaSceneResult(
                    request,
                    VerificationDecision.UNVERIFIED,
                    evidence,
                    f"frame verification unavailable: {evidence.error}",
                    replacement_attempt,
                )
            return self._failed(request, f"frame verifier failed: {evidence.error}", replacement_attempt, evidence)

        entity_ok = evidence.entity_match and evidence.entity_confidence >= self._entity_threshold(request)
        action_required = bool(request.expected_action.strip())
        action_ok = (
            not action_required
            or (
                evidence.action_match is True
                and evidence.action_confidence >= self._action_threshold(request)
            )
        )
        if entity_ok and action_ok:
            return VerifiedMediaSceneResult(
                request, VerificationDecision.VERIFIED, evidence,
                "downloaded media passed entity and action verification", replacement_attempt,
            )

        failures = []
        if not entity_ok:
            failures.append("entity evidence below threshold")
        if not action_ok:
            failures.append("action evidence below threshold")
        return self._failed(request, "; ".join(failures), replacement_attempt, evidence)

    def must_abort(self, result: VerifiedMediaSceneResult) -> bool:
        """Keep the critical-scene policy explicit at the orchestration edge."""

        return result.should_abort and self.config.critical_scene_policy == "abort"

    def _entity_threshold(self, request: VerificationRequest) -> float:
        return self.config.critical_confidence_threshold if request.priority is VerificationPriority.CRITICAL else 0.0

    def _action_threshold(self, request: VerificationRequest) -> float:
        return self.config.critical_action_confidence_threshold if request.priority is VerificationPriority.CRITICAL else 0.0

    def _failed(
        self,
        request: VerificationRequest,
        reason: str,
        replacement_attempt: int,
        evidence: DownloadedMediaEvidence | None = None,
    ) -> VerifiedMediaSceneResult:
        decision = VerificationDecision.REJECTED
        if (
            request.priority is not VerificationPriority.CRITICAL
            and self.config.allow_unverified_lower_priority
            and replacement_attempt >= self.config.max_replacement_attempts
        ):
            decision = VerificationDecision.UNVERIFIED
            reason = f"{reason}; lower-priority fallback allowed"
        return VerifiedMediaSceneResult(request, decision, evidence, reason, replacement_attempt)


def _is_transient_verifier_error(error: str) -> bool:
    """Return true only when vision could not judge an otherwise valid asset."""

    text = str(error or "").casefold()
    return any(marker in text for marker in (
        "resource_exhausted",
        "quota",
        "rate limit",
        "429",
        "timeout",
        "timed out",
        "network",
        "unavailable",
        "connection",
    ))


@dataclass(frozen=True)
class VerifiedMediaReport:
    scenes: tuple[VerifiedMediaSceneResult, ...] = ()
    attempts: tuple[VerifiedMediaSceneResult, ...] = ()

    def to_dict(self) -> dict[str, object]:
        verified = sum(scene.decision is VerificationDecision.VERIFIED for scene in self.scenes)
        scene_rows = []
        for scene in self.scenes:
            row = scene.to_dict()
            row["replacements"] = [
                attempt.to_dict()
                for attempt in self.attempts
                if (
                    attempt.request.scene_index == scene.request.scene_index
                    and attempt.request.media_path != scene.request.media_path
                )
            ]
            scene_rows.append(row)
        return {
            "scenes": scene_rows,
            "summary": {
                "scene_count": len(self.scenes),
                "verified_count": verified,
                "unverified_count": sum(scene.decision is VerificationDecision.UNVERIFIED for scene in self.scenes),
                "rejected_count": sum(scene.decision is VerificationDecision.REJECTED for scene in self.scenes),
                "verified_coverage": round(verified / len(self.scenes), 3) if self.scenes else 0.0,
            },
        }
