"""Application settings and environment-backed configuration."""

from __future__ import annotations

import os
import datetime as dt
import hashlib
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
    edge_tts_rate: str
    speechify_voice_id: str
    elevenlabs_voice_id: str
    elevenlabs_voice_ids: tuple[str, ...]
    elevenlabs_voice_index: int
    elevenlabs_accounts: tuple[tuple[str, str], ...]
    voice_rotation_provider: str
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
        voice_rotation_provider = ""
        voice_mix = _provider_mix(settings.env("AUTO_VIDEO_VOICE_MIX"))
        if not voice_override and voice_mix:
            mix_index = _voice_rotation_index(settings, len(voice_mix))
            voice_rotation_provider = voice_mix[mix_index]
            voice_priority = (voice_rotation_provider,) + tuple(
                p for p in voice_priority if p != voice_rotation_provider
            )

        allow_external = settings.env_bool("AUTO_VIDEO_ALLOW_EXTERNAL_API_CALLS", profile.allow_external_api_calls)
        mock_uploads = settings.env_bool("AUTO_VIDEO_MOCK_UPLOADS", profile.mock_uploads)
        music_config = music_config_from_settings(settings, profile_order=profile.music_provider_priority)
        clip_audio_config = clip_audio_config_from_env(settings.env_values)
        elevenlabs_voice_ids = _voice_id_list(
            settings.env("ELEVENLABS_VOICE_IDS")
            or settings.env("ELEVENLABS_VOICE_ID", DEFAULTS.providers.elevenlabs_voice_id)
        )
        elevenlabs_voice_index = _voice_rotation_index(settings, len(elevenlabs_voice_ids))
        selected_elevenlabs_voice_id = (
            elevenlabs_voice_ids[elevenlabs_voice_index]
            if elevenlabs_voice_ids
            else DEFAULTS.providers.elevenlabs_voice_id
        )
        elevenlabs_accounts = _elevenlabs_accounts_from_env(
            settings,
            primary_voice_id=selected_elevenlabs_voice_id,
        )

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
            edge_tts_rate=settings.env("EDGE_TTS_RATE", DEFAULTS.providers.edge_tts_rate),
            speechify_voice_id=settings.env("SPEECHIFY_VOICE_ID", DEFAULTS.providers.speechify_voice_id),
            elevenlabs_voice_id=selected_elevenlabs_voice_id,
            elevenlabs_voice_ids=elevenlabs_voice_ids,
            elevenlabs_voice_index=elevenlabs_voice_index,
            elevenlabs_accounts=elevenlabs_accounts,
            voice_rotation_provider=voice_rotation_provider,
            elevenlabs_model=settings.env("ELEVENLABS_MODEL", DEFAULTS.providers.elevenlabs_model),
            clip_audio=clip_audio_config,
            music=music_config,
        )


def _voice_id_list(raw: str) -> tuple[str, ...]:
    """Parse one or more ElevenLabs voice ids from environment text."""

    return tuple(item.strip() for item in str(raw or "").split(",") if item.strip())


def _elevenlabs_accounts_from_env(
    settings: Settings,
    *,
    primary_voice_id: str,
) -> tuple[tuple[str, str], ...]:
    """Build (api_key, voice_id) accounts from numbered environment pairs.

    The primary account is ``ELEVENLABS_API_KEY`` + the rotated primary voice.
    Fallback accounts use ``ELEVENLABS_API_KEY_2`` + ``ELEVENLABS_VOICE_ID_2``,
    ``_3``, and so on. Scanning stops at the first gap so an unconfigured
    fallback account is harmless until its credentials are added.
    """

    accounts: list[tuple[str, str]] = []
    for index in range(1, 11):
        if index == 1:
            key = settings.env("ELEVENLABS_API_KEY").strip()
            voice_id = primary_voice_id.strip()
        else:
            key = settings.env(f"ELEVENLABS_API_KEY_{index}").strip()
            voice_id = settings.env(f"ELEVENLABS_VOICE_ID_{index}").strip()
        if not key and not voice_id:
            break
        if key and voice_id:
            accounts.append((key, voice_id))
    return tuple(accounts)


def _provider_mix(raw: str) -> tuple[str, ...]:
    """Parse the optional per-run voice provider rotation list."""

    return tuple(item.strip().lower() for item in str(raw or "").split(",") if item.strip())


def _voice_rotation_index(settings: Settings, voice_count: int) -> int:
    """Resolve the per-video ElevenLabs voice rotation index."""

    if voice_count <= 1:
        return 0
    explicit = settings.env("ELEVENLABS_VOICE_ROTATION_INDEX")
    if explicit.strip():
        return int(explicit) % voice_count
    seed = (
        settings.env("AUTO_VIDEO_VOICE_ROTATION_SEED")
        or settings.env("GITHUB_RUN_NUMBER")
        or settings.env("GITHUB_RUN_ID")
    )
    if seed.strip():
        try:
            return int(seed) % voice_count
        except ValueError:
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            return int(digest[:8], 16) % voice_count
    return dt.datetime.now(dt.timezone.utc).toordinal() % voice_count
