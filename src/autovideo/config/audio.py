"""Audio mixing configuration loaded from environment values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ClipAudioConfig:
    """Configuration for preserving usable audio from selected source clips."""

    # Source clips are not a trustworthy narration source.  They remain opt-in
    # because stock and archive footage can contain speech or music.
    use_clip_audio: bool = False
    volume: float = 0.30
    ducking: bool = True
    fade_ms: int = 250
    noise_gate: bool = True


def clip_audio_config_from_env(env: Mapping[str, str]) -> ClipAudioConfig:
    """Build clip-audio configuration from environment variables."""

    return ClipAudioConfig(
        use_clip_audio=_env_bool(env, "AUTO_VIDEO_USE_CLIP_AUDIO", False),
        volume=_clamp(_env_float(env, "AUTO_VIDEO_CLIP_AUDIO_VOLUME", 0.30), 0.0, 1.0),
        ducking=_env_bool(env, "AUTO_VIDEO_CLIP_AUDIO_DUCKING", True),
        fade_ms=max(0, _env_int(env, "AUTO_VIDEO_CLIP_AUDIO_FADE_MS", 250)),
        noise_gate=_env_bool(env, "AUTO_VIDEO_CLIP_AUDIO_NOISE_GATE", True),
    )


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "")
    if not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "")
    if not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "")
    if not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))
