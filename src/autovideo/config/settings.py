"""Application settings and environment-backed configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from autovideo.config.audio import ClipAudioConfig, clip_audio_config_from_env
from autovideo.config.channels import RenderProfile, resolve_render_profile
from autovideo.config.defaults import DEFAULTS
from autovideo.config.music import MusicConfig, music_config_from_settings


@dataclass(frozen=True)
class Settings:
    project_root: Path
    output_dir: Path
    videos_dir: Path
    input_clips_dir: Path
    music_dir: Path
    assets_dir: Path
    env_values: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @classmethod
    def from_project_root(cls, project_root: Path | str, env: Mapping[str, str] | None = None) -> "Settings":
        root = Path(project_root).resolve()
        env_values = env if env is not None else os.environ
        return cls(
            project_root=root,
            output_dir=root / "output",
            videos_dir=root / "videos",
            input_clips_dir=root / "input_clips",
            music_dir=root / "music",
            assets_dir=root / "assets",
            env_values=env_values,
        )

    def env(self, name: str, default: str = "") -> str:
        return self.env_values.get(name, default)

    def env_bool(self, name: str, default: bool = False) -> bool:
        raw = self.env(name, "")
        if not raw:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    render_profile: RenderProfile
    provider_priority: dict[str, tuple[str, ...]]
    model_defaults: dict[str, tuple[str, ...]]
    api_keys: dict[str, str]
    feature_flags: dict[str, bool]
    retry_attempts: int
    request_timeout_sec: int
    download_timeout_sec: int
    channel_name: str
    default_niche: str
    edge_tts_voice: str
    speechify_voice_id: str
    elevenlabs_voice_id: str
    elevenlabs_model: str
    clip_audio: ClipAudioConfig = field(default_factory=ClipAudioConfig)
    music: MusicConfig = field(default_factory=MusicConfig)

    @classmethod
    def from_settings(cls, settings: Settings) -> "AppConfig":
        profile = resolve_render_profile(
            settings.env("AUTO_VIDEO_RENDER_PROFILE")
            or settings.env("RENDER_PROFILE")
            or settings.env("ENVIRONMENT")
        )
        voice_override = settings.env("AUTO_VIDEO_VOICE_PROVIDER") or settings.env("VOICE_PROVIDER")
        voice_priority = profile.voice_provider_priority
        if voice_override:
            requested = tuple(p.strip().lower() for p in voice_override.split(",") if p.strip())
            if requested:
                voice_priority = requested + tuple(p for p in voice_priority if p not in requested)

        allow_external = settings.env_bool("AUTO_VIDEO_ALLOW_EXTERNAL_API_CALLS", profile.allow_external_api_calls)
        mock_uploads = settings.env_bool("AUTO_VIDEO_MOCK_UPLOADS", profile.mock_uploads)
        music_config = music_config_from_settings(settings, profile_order=profile.music_provider_priority)
        clip_audio_config = clip_audio_config_from_env(settings.env_values)

        return cls(
            settings=settings,
            render_profile=profile,
            provider_priority={
                "llm": profile.llm_provider_priority,
                "voice": voice_priority,
                "stock": profile.stock_provider_priority,
                "music": music_config.provider_order,
                "upload": profile.upload_provider_priority,
            },
            model_defaults={
                "gemini": DEFAULTS.providers.gemini_models,
                "groq": DEFAULTS.providers.groq_models,
                "openai": DEFAULTS.providers.openai_models,
                "sambanova": DEFAULTS.providers.sambanova_models,
            },
            api_keys={
                "gemini": settings.env("GEMINI_API_KEY"),
                "pexels": settings.env("PEXELS_API_KEY"),
                "openai": settings.env("OPENAI_API_KEY"),
                "groq": settings.env("GROQ_API_KEY"),
                "speechify": settings.env("SPEECHIFY_API_KEY"),
                "jamendo": settings.env("JAMENDO_CLIENT_ID"),
                "pixabay": settings.env("PIXABAY_API_KEY"),
                "sambanova": settings.env("SAMBANOVA_API_KEY"),
                "elevenlabs": settings.env("ELEVENLABS_API_KEY"),
            },
            feature_flags={
                "allow_external_api_calls": allow_external,
                "mock_uploads": mock_uploads,
                "fast_render": profile.fast_render,
                "final_thumbnails": profile.final_thumbnails,
            },
            retry_attempts=int(settings.env("AUTO_VIDEO_RETRY_ATTEMPTS", str(DEFAULTS.providers.retry_attempts))),
            request_timeout_sec=int(settings.env("AUTO_VIDEO_REQUEST_TIMEOUT_SEC", str(DEFAULTS.providers.request_timeout_sec))),
            download_timeout_sec=int(settings.env("AUTO_VIDEO_DOWNLOAD_TIMEOUT_SEC", str(DEFAULTS.providers.download_timeout_sec))),
            channel_name=settings.env("CHANNEL_NAME", DEFAULTS.channel.channel_name),
            default_niche=settings.env("DEFAULT_NICHE", DEFAULTS.channel.default_niche),
            edge_tts_voice=settings.env("EDGE_TTS_VOICE", DEFAULTS.providers.edge_tts_voice),
            speechify_voice_id=settings.env("SPEECHIFY_VOICE_ID", DEFAULTS.providers.speechify_voice_id),
            elevenlabs_voice_id=settings.env("ELEVENLABS_VOICE_ID", DEFAULTS.providers.elevenlabs_voice_id),
            elevenlabs_model=settings.env("ELEVENLABS_MODEL", DEFAULTS.providers.elevenlabs_model),
            clip_audio=clip_audio_config,
            music=music_config,
        )
