"""Render profile configuration for renderer implementations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from autovideo.config.channels import resolve_render_profile

RenderProfileName = Literal["development", "production", "testing"]


@dataclass(frozen=True)
class RenderProfile:
    """Renderer settings selected by execution environment."""

    name: RenderProfileName
    width: int = 1080
    height: int = 1920
    fps: int = 30
    shorts_max_duration_sec: float = 58.0
    transition_duration_sec: float = 0.22
    music_volume: float = 0.22
    video_codec: str = "libx264"
    video_preset: str = "veryfast"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "160k"
    audio_sample_rate: str = "44100"
    movflags: str = "+faststart"


def render_profile_for(
    name: str | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    shorts_max_duration_sec: float | None = None,
    transition_duration_sec: float = 0.22,
    music_volume: float = 0.22,
) -> RenderProfile:
    """Load a render profile without changing legacy defaults."""

    configured = resolve_render_profile(
        name
        or os.environ.get("AUTO_VIDEO_RENDER_PROFILE")
        or os.environ.get("RENDER_PROFILE")
        or os.environ.get("ENVIRONMENT")
    )
    profile_name = configured.name
    return RenderProfile(
        name=profile_name,  # type: ignore[arg-type]
        width=width if width is not None else configured.width,
        height=height if height is not None else configured.height,
        fps=fps if fps is not None else configured.fps,
        shorts_max_duration_sec=shorts_max_duration_sec or configured.max_duration_sec or 58.0,
        transition_duration_sec=transition_duration_sec,
        music_volume=music_volume,
    )
