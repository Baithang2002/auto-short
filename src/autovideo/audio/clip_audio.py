"""Helpers for mixing authentic source-clip audio under narration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from autovideo.config.audio import ClipAudioConfig


@dataclass(frozen=True)
class ClipAudioDecision:
    """Diagnostic record for one segment's source-clip audio decision."""

    segment_index: int
    source_path: str
    clip_audio_extracted: bool
    clip_audio_used: bool
    clip_audio_muted: bool
    reason: str
    volume: float
    ducking_applied: bool
    fade_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clip_audio_filter(
    *,
    source_audio_label: str,
    voice_audio_label: str,
    duration_sec: float,
    config: ClipAudioConfig,
) -> tuple[str, str]:
    """Return an FFmpeg filter graph fragment and output label for mixed segment audio."""

    fade_sec = max(0.0, config.fade_ms / 1000.0)
    fade_out_start = max(0.0, float(duration_sec) - fade_sec)
    clip_filters = [
        f"{source_audio_label}atrim=0:{float(duration_sec):.3f}",
        "asetpts=PTS-STARTPTS",
        f"volume={config.volume:.4f}",
    ]
    if fade_sec > 0:
        clip_filters.extend([
            f"afade=t=in:st=0:d={fade_sec:.3f}",
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_sec:.3f}",
        ])
    clip_filters.append("aformat=channel_layouts=stereo[clipraw]")

    if config.ducking:
        graph = (
            f"{voice_audio_label}aformat=channel_layouts=stereo,asplit[voice_mix][voice_sc];"
            + ",".join(clip_filters)
            + ";[clipraw][voice_sc]sidechaincompress=threshold=0.035:ratio=4.0:"
            "attack=25:release=350[clipduck];"
            "[voice_mix][clipduck]amix=inputs=2:duration=first:dropout_transition=0:"
            "normalize=0,alimiter=limit=0.95[aout]"
        )
    else:
        graph = (
            f"{voice_audio_label}aformat=channel_layouts=stereo[voice_mix];"
            + ",".join(clip_filters)
            + ";[voice_mix][clipraw]amix=inputs=2:duration=first:dropout_transition=0:"
            "normalize=0,alimiter=limit=0.95[aout]"
        )
    return graph, "[aout]"


def build_audio_mix_report(
    decisions: list[ClipAudioDecision],
    *,
    music_volume: float,
    voice_volume: float = 1.0,
) -> dict[str, Any]:
    """Build the persisted audio mix diagnostic report."""

    return {
        "clip_audio_extracted": any(item.clip_audio_extracted for item in decisions),
        "clip_audio_used": any(item.clip_audio_used for item in decisions),
        "clip_audio_muted": any(item.clip_audio_muted for item in decisions),
        "ducking_applied": any(item.ducking_applied for item in decisions),
        "music_volume": music_volume,
        "voice_volume": voice_volume,
        "segments": [item.to_dict() for item in decisions],
    }
