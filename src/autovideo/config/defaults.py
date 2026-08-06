"""Central defaults for legacy scripts and new application code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefaults:
    gemini_models: tuple[str, ...] = (
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    )
    groq_models: tuple[str, ...] = (
        "llama-3.3-70b-versatile",
        "qwen/qwen3.6-27b",
        "llama-3.1-8b-instant",
    )
    sambanova_models: tuple[str, ...] = (
        "DeepSeek-V3.2",
        "Meta-Llama-3.3-70B-Instruct",
        "gemma-4-31B-it",
    )
    gemini_image_model: str = "gemini-3.5-flash"
    edge_tts_voice: str = "en-US-AndrewNeural"
    edge_tts_rate: str = "-5%"
    speechify_voice_id: str = "george"
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_voice_id: str = ""
    audiolab_model: str = "tts/auto"
    audiolab_voice_id: str = "auto"
    request_timeout_sec: int = 30
    download_timeout_sec: int = 120
    retry_attempts: int = 3


@dataclass(frozen=True)
class RenderDefaults:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    # Duration policy is owned by the active FormatProfile; these are
    # legacy-compatibility defaults only. target is story-driven (None),
    # min is a soft indicator (0 = never rejects/pads), and the shorts
    # max is the platform ceiling default (the FormatProfile supplies the
    # authoritative value at runtime).
    target_duration_sec: int | None = None
    avg_segment_duration_sec: float = 6.5
    shorts_min_duration_sec: int = 0
    shorts_max_duration_sec: int = 60
    default_music_volume: float = 0.22


@dataclass(frozen=True)
class ChannelDefaults:
    default_niche: str = "mind-blowing facts about space"
    channel_name: str = "Wonders of the Nature"


@dataclass(frozen=True)
class AppDefaults:
    providers: ProviderDefaults = ProviderDefaults()
    render: RenderDefaults = RenderDefaults()
    channel: ChannelDefaults = ChannelDefaults()


DEFAULTS = AppDefaults()
