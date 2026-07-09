"""Central defaults for legacy scripts and new application code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefaults:
    gemini_models: tuple[str, ...] = (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    )
    groq_models: tuple[str, ...] = (
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    )
    openai_models: tuple[str, ...] = ("gpt-4o-mini", "gpt-4o")
    sambanova_models: tuple[str, ...] = (
        "Meta-Llama-3.1-405B-Instruct",
        "DeepSeek-R1-Distill-Llama-70B",
        "Meta-Llama-3.3-70B-Instruct",
        "Meta-Llama-3.1-70B-Instruct",
        "Qwen2.5-72B-Instruct",
    )
    gemini_image_model: str = "gemini-2.5-flash"
    edge_tts_voice: str = "en-US-AndrewNeural"
    speechify_voice_id: str = "george"
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_voice_id: str = ""
    request_timeout_sec: int = 30
    download_timeout_sec: int = 120
    retry_attempts: int = 3


@dataclass(frozen=True)
class RenderDefaults:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    target_duration_sec: int = 60
    avg_segment_duration_sec: float = 6.5
    shorts_min_duration_sec: int = 50
    shorts_max_duration_sec: int = 58
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
