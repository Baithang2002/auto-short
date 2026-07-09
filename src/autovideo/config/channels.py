"""Channel and render profile configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RenderProfileName(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"


@dataclass(frozen=True)
class RenderProfile:
    name: str
    width: int
    height: int
    fps: int = 30
    max_duration_sec: int | None = None
    caption_style: str = "default"
    voice_provider_priority: tuple[str, ...] = ("elevenlabs", "edge_tts", "speechify")
    llm_provider_priority: tuple[str, ...] = ("gemini", "sambanova", "groq", "openai")
    stock_provider_priority: tuple[str, ...] = ("pexels", "pixabay", "nasa")
    music_provider_priority: tuple[str, ...] = ("jamendo", "pixabay", "mixkit", "generated", "silence")
    upload_provider_priority: tuple[str, ...] = ("youtube", "instagram", "facebook")
    allow_external_api_calls: bool = True
    mock_uploads: bool = False
    fast_render: bool = False
    final_thumbnails: bool = False
    quality: str = "standard"


@dataclass(frozen=True)
class ChannelProfile:
    id: str
    name: str
    default_voice: str
    default_render_profile: str
    hashtags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def default_render_profiles() -> dict[str, RenderProfile]:
    return {
        RenderProfileName.DEVELOPMENT.value: RenderProfile(
            name=RenderProfileName.DEVELOPMENT.value,
            width=1080,
            height=1920,
            fps=30,
            max_duration_sec=58,
            voice_provider_priority=("elevenlabs", "edge_tts", "speechify"),
            allow_external_api_calls=True,
            mock_uploads=True,
            fast_render=True,
            final_thumbnails=False,
            quality="draft",
        ),
        RenderProfileName.PRODUCTION.value: RenderProfile(
            name=RenderProfileName.PRODUCTION.value,
            width=1080,
            height=1920,
            fps=30,
            max_duration_sec=58,
            voice_provider_priority=("elevenlabs", "edge_tts", "speechify"),
            allow_external_api_calls=True,
            mock_uploads=False,
            fast_render=False,
            final_thumbnails=True,
            quality="full",
        ),
        RenderProfileName.TESTING.value: RenderProfile(
            name=RenderProfileName.TESTING.value,
            width=1080,
            height=1920,
            fps=30,
            max_duration_sec=58,
            voice_provider_priority=("mock", "edge_tts"),
            llm_provider_priority=("mock",),
            stock_provider_priority=("mock",),
            music_provider_priority=("mock", "generated", "silence"),
            upload_provider_priority=("mock",),
            allow_external_api_calls=False,
            mock_uploads=True,
            fast_render=True,
            final_thumbnails=False,
            quality="test",
        ),
    }


def resolve_render_profile(name: str | None) -> RenderProfile:
    profiles = default_render_profiles()
    key = (name or RenderProfileName.DEVELOPMENT.value).strip().lower()
    if key == "ci":
        key = RenderProfileName.TESTING.value
    return profiles.get(key, profiles[RenderProfileName.DEVELOPMENT.value])
