"""Deterministic post-render quality policy for safe unattended publishing."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class PublishQualityVerdict(str, Enum):
    """The publish decision produced after inspecting render artifacts."""

    APPROVED = "APPROVED"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class QualitySeverity(str, Enum):
    """Severity associated with one post-render quality finding."""

    PASS = "PASS"
    WARNING = "WARNING"
    DEFER = "DEFER"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class PublishQualityConfig:
    """Configuration for the post-render publish quality policy."""

    enabled: bool = True
    min_duration_sec: float = 45.0
    max_duration_sec: float = 60.0
    max_low_confidence_ratio: float = 0.34
    max_duplicate_asset_ratio: float = 0.25
    max_hybrid_composer_ratio: float = 0.50
    allow_clip_audio: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PublishQualityConfig":
        """Load the quality policy from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values, "AUTO_VIDEO_PUBLISH_QUALITY_GATE_ENABLED", True),
            min_duration_sec=max(0.0, _env_float(values, "AUTO_VIDEO_PUBLISH_QUALITY_MIN_DURATION_SEC", 45.0)),
            max_duration_sec=max(0.0, _env_float(values, "AUTO_VIDEO_PUBLISH_QUALITY_MAX_DURATION_SEC", 60.0)),
            max_low_confidence_ratio=_clamp(_env_float(
                values,
                "AUTO_VIDEO_PUBLISH_QUALITY_MAX_LOW_CONFIDENCE_RATIO",
                0.34,
            )),
            max_duplicate_asset_ratio=_clamp(_env_float(
                values,
                "AUTO_VIDEO_PUBLISH_QUALITY_MAX_DUPLICATE_ASSET_RATIO",
                0.25,
            )),
            max_hybrid_composer_ratio=_clamp(_env_float(
                values,
                "AUTO_VIDEO_PUBLISH_QUALITY_MAX_HYBRID_RATIO",
                0.50,
            )),
            allow_clip_audio=_env_bool(values, "AUTO_VIDEO_PUBLISH_QUALITY_ALLOW_CLIP_AUDIO", False),
        )


@dataclass(frozen=True)
class PublishQualityArtifacts:
    """Artifact paths consumed by the post-render quality policy."""

    video_path: Path
    captions_path: Path
    timeline_path: Path
    media_manifest_path: Path
    ffprobe_path: Path
    fallback_quality_path: Path
    audio_mix_path: Path
    evidence_verification_path: Path
    contact_sheet_path: Path
    decode_verified: bool


@dataclass(frozen=True)
class PublishQualityCheck:
    """One auditable policy result."""

    name: str
    severity: QualitySeverity
    message: str
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the check without exposing implementation objects."""

        payload = asdict(self)
        payload["severity"] = self.severity.value
        return payload


@dataclass(frozen=True)
class PublishQualityReport:
    """Persisted decision and full diagnostics for one rendered video."""

    verdict: PublishQualityVerdict
    checks: tuple[PublishQualityCheck, ...]
    config: PublishQualityConfig

    @property
    def upload_allowed(self) -> bool:
        """Return whether this report permits a quality-gated upload."""

        return self.verdict in {PublishQualityVerdict.APPROVED, PublishQualityVerdict.SKIPPED}

    def to_dict(self) -> dict[str, Any]:
        """Serialize a stable ``publish_quality_report.json`` artifact."""

        return {
            "verdict": self.verdict.value,
            "upload_allowed": self.upload_allowed,
            "checks": [check.to_dict() for check in self.checks],
            "summary": {
                severity.value.lower(): sum(1 for check in self.checks if check.severity == severity)
                for severity in QualitySeverity
            },
            "configuration": asdict(self.config),
        }

    def write_json(self, path: Path) -> Path:
        """Persist the report for the queue, uploader, and audit trail."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class PublishQualityGate:
    """Evaluate rendered artifacts without calling providers or modifying media."""

    def __init__(self, config: PublishQualityConfig | None = None) -> None:
        self.config = config or PublishQualityConfig()

    def evaluate(self, artifacts: PublishQualityArtifacts) -> PublishQualityReport:
        """Return an approved, deferred, blocked, or skipped publish decision."""

        if not self.config.enabled:
            return PublishQualityReport(
                verdict=PublishQualityVerdict.SKIPPED,
                checks=(PublishQualityCheck(
                    name="quality_gate",
                    severity=QualitySeverity.PASS,
                    message="publish quality gate disabled by configuration",
                ),),
                config=self.config,
            )
        checks = (
            self._render_integrity(artifacts),
            self._required_artifacts(artifacts),
            self._fallback_quality(artifacts),
            self._media_quality(artifacts),
            self._evidence_quality(artifacts),
            self._audio_quality(artifacts),
            self._contact_sheet(artifacts),
        )
        verdict = _verdict_from_checks(checks)
        return PublishQualityReport(verdict=verdict, checks=checks, config=self.config)

    def _render_integrity(self, artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        if not artifacts.video_path.exists():
            return _block("render_integrity", "rendered video is missing")
        if not artifacts.decode_verified:
            return _block("render_integrity", "FFmpeg decode verification failed")
        payload = _read_json(artifacts.ffprobe_path)
        if not payload or payload.get("error"):
            return _block("render_integrity", "ffprobe report is missing or invalid")
        duration = _number((payload.get("format") or {}).get("duration"))
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if duration is None or not video_streams or not audio_streams:
            return _block("render_integrity", "render lacks required duration, video, or audio stream")
        if duration < self.config.min_duration_sec or duration > self.config.max_duration_sec:
            return _defer(
                "render_duration",
                "render duration is outside the configured publishing range",
                duration_sec=round(duration, 3),
                minimum_sec=self.config.min_duration_sec,
                maximum_sec=self.config.max_duration_sec,
            )
        return _pass("render_integrity", "decoded video has audio/video streams and acceptable duration", duration_sec=round(duration, 3))

    @staticmethod
    def _required_artifacts(artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        required = {
            "captions": artifacts.captions_path,
            "timeline": artifacts.timeline_path,
            "media_manifest": artifacts.media_manifest_path,
        }
        missing = [name for name, path in required.items() if not path.exists() or path.stat().st_size == 0]
        if missing:
            return _block("required_artifacts", "required production artifacts are missing", missing=missing)
        return _pass("required_artifacts", "captions, timeline, and media manifest are present")

    @staticmethod
    def _fallback_quality(artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        payload = _read_json(artifacts.fallback_quality_path)
        if not payload:
            return _defer("fallback_quality", "fallback quality report is unavailable")
        if not bool(payload.get("quality_gate_passed", False)):
            return _defer("fallback_quality", "pre-render fallback quality gate failed", **payload)
        return _pass("fallback_quality", "pre-render fallback quality gate passed")

    def _media_quality(self, artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        payload = _read_json(artifacts.media_manifest_path)
        assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
        if not assets:
            return _block("media_quality", "media manifest contains no assets")
        confidences: list[str] = []
        provider_ids: list[str] = []
        hybrid_count = 0
        for asset in assets:
            metadata = asset.get("metadata") if isinstance(asset, dict) else {}
            selection = metadata.get("selection") if isinstance(metadata, dict) else {}
            if not isinstance(selection, dict):
                selection = {}
            confidence = str(selection.get("confidence_level") or selection.get("confidence") or "").upper()
            confidences.append(confidence)
            provider = str(selection.get("provider") or metadata.get("provider") or "")
            provider_id = str(selection.get("provider_id") or metadata.get("provider_asset_id") or "")
            if provider_id:
                provider_ids.append(f"{provider}:{provider_id}")
            if provider in {"hybrid", "hybrid_composer"} or str(selection.get("fallback_level")) in {
                "hybrid_composer",
                "hybrid_composition",
            }:
                hybrid_count += 1
        total = len(assets)
        low_ratio = sum(value in {"LOW", "FALLBACK", ""} for value in confidences) / total
        duplicate_count = sum(count - 1 for count in Counter(provider_ids).values() if count > 1)
        duplicate_ratio = duplicate_count / total
        hybrid_ratio = hybrid_count / total
        metrics = {
            "asset_count": total,
            "low_confidence_ratio": round(low_ratio, 4),
            "duplicate_asset_ratio": round(duplicate_ratio, 4),
            "hybrid_composer_ratio": round(hybrid_ratio, 4),
        }
        if low_ratio > self.config.max_low_confidence_ratio:
            return _defer("media_quality", "too many selected assets have low confidence", **metrics)
        if duplicate_ratio > self.config.max_duplicate_asset_ratio:
            return _defer("media_quality", "duplicate selected assets exceed the configured limit", **metrics)
        if hybrid_ratio > self.config.max_hybrid_composer_ratio:
            return _defer("media_quality", "hybrid-composed scenes exceed the configured limit", **metrics)
        return _pass("media_quality", "media confidence, diversity, and composition ratio are acceptable", **metrics)

    @staticmethod
    def _evidence_quality(artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        payload = _read_json(artifacts.evidence_verification_path)
        if not payload:
            return _defer("evidence_verification", "evidence verification report is unavailable")
        mismatches = [
            scene_id
            for scene_id, scene in payload.items()
            if isinstance(scene, dict) and str(scene.get("vision_result", "")).lower() == "no_match"
        ]
        if mismatches:
            return _defer(
                "evidence_verification",
                "post-download visual verification reported entity mismatches",
                mismatched_scenes=mismatches,
            )
        return _pass("evidence_verification", "no verified post-download entity mismatches")

    def _audio_quality(self, artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        payload = _read_json(artifacts.audio_mix_path)
        if not payload:
            return _defer("audio_quality", "audio mix report is unavailable")
        spoken_source_segments = [
            item.get("segment_index")
            for item in payload.get("segments", [])
            if isinstance(item, dict) and item.get("clip_audio_used")
        ]
        if spoken_source_segments and not self.config.allow_clip_audio:
            return _defer(
                "audio_quality",
                "source clip audio is mixed under narration",
                segments=spoken_source_segments,
            )
        return _pass("audio_quality", "source clip audio is muted or explicitly allowed")

    @staticmethod
    def _contact_sheet(artifacts: PublishQualityArtifacts) -> PublishQualityCheck:
        if not artifacts.contact_sheet_path.exists() or artifacts.contact_sheet_path.stat().st_size == 0:
            return PublishQualityCheck(
                name="contact_sheet",
                severity=QualitySeverity.WARNING,
                message="contact sheet is unavailable for human audit",
            )
        return _pass("contact_sheet", "contact sheet is available for human audit")


def upload_allowed_from_report(report_path: Path, *, enforce: bool) -> tuple[bool, str]:
    """Return whether the uploader may proceed under the configured enforcement mode."""

    if not enforce:
        return True, "publish quality enforcement disabled"
    payload = _read_json(report_path)
    verdict = str(payload.get("verdict", "")) if payload else ""
    if verdict in {PublishQualityVerdict.APPROVED.value, PublishQualityVerdict.SKIPPED.value}:
        return True, f"publish quality verdict {verdict}"
    return False, f"publish quality verdict {verdict or 'missing'}"


def _verdict_from_checks(checks: tuple[PublishQualityCheck, ...]) -> PublishQualityVerdict:
    if any(check.severity == QualitySeverity.BLOCK for check in checks):
        return PublishQualityVerdict.BLOCKED
    if any(check.severity == QualitySeverity.DEFER for check in checks):
        return PublishQualityVerdict.DEFERRED
    return PublishQualityVerdict.APPROVED


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pass(name: str, message: str, **metrics: Any) -> PublishQualityCheck:
    return PublishQualityCheck(name=name, severity=QualitySeverity.PASS, message=message, metrics=metrics)


def _defer(name: str, message: str, **metrics: Any) -> PublishQualityCheck:
    return PublishQualityCheck(name=name, severity=QualitySeverity.DEFER, message=message, metrics=metrics)


def _block(name: str, message: str, **metrics: Any) -> PublishQualityCheck:
    return PublishQualityCheck(name=name, severity=QualitySeverity.BLOCK, message=message, metrics=metrics)


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


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
