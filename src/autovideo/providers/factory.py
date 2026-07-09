"""Provider registry construction from application configuration."""

from __future__ import annotations

from typing import Callable
from pathlib import Path

from autovideo.config import AppConfig, ProviderRegistry
from autovideo.providers.base import ProviderHealth, ProviderHealthStatus
from autovideo.providers.music.generated import GeneratedMusicProvider, Synthesizer
from autovideo.providers.music.jamendo import JamendoMusicProvider
from autovideo.providers.music.mixkit import MixkitMusicProvider
from autovideo.providers.music.pixabay import PixabayMusicProvider
from autovideo.providers.music.silence import SilenceMusicProvider
from autovideo.providers.voice import EdgeTTSVoiceProvider, ElevenLabsVoiceProvider, MockVoiceProvider, SpeechifyVoiceProvider


def _priority_map(names: tuple[str, ...]) -> dict[str, int]:
    return {name: index for index, name in enumerate(names)}


def build_voice_registry(config: AppConfig, *, include_real_providers: bool = True) -> ProviderRegistry:
    registry = ProviderRegistry()
    priority = _priority_map(config.provider_priority["voice"])
    profile_name = config.render_profile.name

    def register(name: str, provider: object, *, enabled: bool = True, health_message: str = "") -> None:
        registry.register(
            "voice",
            name,
            provider,
            priority=priority.get(name, 100),
            enabled=enabled,
            profiles=(profile_name,),
            features=("whole_narration", "chapter_narration", "scene_narration"),
            health=ProviderHealth(
                ProviderHealthStatus.HEALTHY if enabled else ProviderHealthStatus.UNAVAILABLE,
                health_message,
            ),
        )

    if "mock" in priority:
        register("mock", MockVoiceProvider(), enabled=not config.feature_flags["allow_external_api_calls"])

    if include_real_providers:
        register(
            "elevenlabs",
            ElevenLabsVoiceProvider(
                api_key=config.api_keys["elevenlabs"],
                voice_id=config.elevenlabs_voice_id,
                model=config.elevenlabs_model,
                timeout_sec=config.download_timeout_sec,
            ),
            enabled=bool("elevenlabs" in priority and config.api_keys["elevenlabs"].strip() and config.elevenlabs_voice_id.strip()),
            health_message="missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID",
        )
        register(
            "edge_tts",
            EdgeTTSVoiceProvider(voice_id=config.edge_tts_voice, retry_attempts=config.retry_attempts),
            enabled="edge_tts" in priority,
        )
        register(
            "speechify",
            SpeechifyVoiceProvider(
                api_key=config.api_keys["speechify"],
                voice_id=config.speechify_voice_id,
                timeout_sec=config.download_timeout_sec,
            ),
            enabled=bool("speechify" in priority and config.api_keys["speechify"].strip()),
            health_message="missing SPEECHIFY_API_KEY",
        )
    return registry


def build_music_registry(
    config: AppConfig,
    *,
    generated_synthesizer: Synthesizer | None = None,
    include_real_providers: bool = True,
    mock_provider_factory: Callable[[], object] | None = None,
) -> ProviderRegistry:
    """Build the music provider registry from configuration.

    Provider order, enablement, and credentials all come from ``config`` —
    no provider name is hard-coded in any caller. The silence provider is
    always registered last as the emergency fallback so planning can never
    fail outright.

    Args:
        config: Application configuration (env-backed).
        generated_synthesizer: Callable rendering ``(duration_sec, mood)`` to
            an audio file path; wired to the FFmpeg chord-pad generator in
            production. The generated provider is disabled when None.
        include_real_providers: When False, network-backed providers are not
            constructed (used by tests).
        mock_provider_factory: Optional factory for a test-only ``mock``
            provider when the profile priority includes one.
    """
    registry = ProviderRegistry()
    music = config.music
    priority = _priority_map(music.provider_order)
    profile_name = config.render_profile.name
    music_dir = config.settings.music_dir

    def register(name: str, provider: object, *, enabled: bool = True, health_message: str = "") -> None:
        registry.register(
            "music",
            name,
            provider,
            priority=priority.get(name, 100),
            enabled=enabled,
            profiles=(profile_name,),
            features=tuple(str(c.value) for c in getattr(provider, "capabilities", ())),
            health=ProviderHealth(
                ProviderHealthStatus.HEALTHY if enabled else ProviderHealthStatus.UNAVAILABLE,
                health_message,
            ),
        )

    if "mock" in priority and mock_provider_factory is not None:
        register("mock", mock_provider_factory())

    if include_real_providers:
        jamendo_key = config.api_keys.get("jamendo", "")
        register(
            "jamendo",
            JamendoMusicProvider(
                jamendo_key,
                cache_dir=Path(music_dir) / "_jamendo_cache",
                timeout_sec=music.timeout_sec,
                download_timeout_sec=config.download_timeout_sec,
                require_commercial=music.require_commercial_license,
            ),
            enabled=bool("jamendo" in priority and jamendo_key.strip() and "your_jamendo" not in jamendo_key),
            health_message="missing JAMENDO_CLIENT_ID",
        )
        pixabay_key = config.api_keys.get("pixabay", "")
        register(
            "pixabay",
            PixabayMusicProvider(
                pixabay_key,
                cache_dir=Path(music_dir) / "_pixabay_cache",
                timeout_sec=music.timeout_sec,
                download_timeout_sec=config.download_timeout_sec,
            ),
            enabled=bool("pixabay" in priority and pixabay_key.strip() and "your_pixabay" not in pixabay_key),
            health_message="missing PIXABAY_API_KEY",
        )
        register(
            "mixkit",
            MixkitMusicProvider(
                cache_dir=Path(music_dir) / "_mixkit_cache",
                download_timeout_sec=config.download_timeout_sec,
            ),
            enabled="mixkit" in priority,
        )

    register(
        "generated",
        GeneratedMusicProvider(generated_synthesizer, enabled=music.enable_generated),
        enabled=bool(
            "generated" in priority and music.enable_generated and generated_synthesizer is not None
        ),
        health_message="generated music disabled or no synthesizer wired",
    )

    # Silence is always available: the terminal fallback that keeps rendering alive.
    register("silence", SilenceMusicProvider(), enabled=True)
    return registry
