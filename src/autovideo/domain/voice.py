"""Typed voice artifacts produced by narration providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from autovideo.domain.errors import ValidationError


@dataclass(frozen=True)
class VoiceTrack:
    """Voiceover audio for a script segment, chapter, or whole video."""

    audio_path: Path
    duration_sec: float
    provider: str = ""
    voice_id: str = ""
    scene_id: str = ""
    chapter_id: str = ""
    retimed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_sec < 0:
            raise ValidationError("VoiceTrack.duration_sec must be non-negative")

    def with_retimed_audio(self, audio_path: Path, duration_sec: float) -> "VoiceTrack":
        return replace(self, audio_path=audio_path, duration_sec=duration_sec, retimed=True)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["audio_path"] = str(self.audio_path)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceTrack":
        return cls(
            audio_path=Path(data["audio_path"]),
            duration_sec=float(data.get("duration_sec", data.get("duration", 0.0)) or 0.0),
            provider=str(data.get("provider", "")),
            voice_id=str(data.get("voice_id", "")),
            scene_id=str(data.get("scene_id", "")),
            chapter_id=str(data.get("chapter_id", "")),
            retimed=bool(data.get("retimed", False)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_legacy_item(self, *, index: int, segment: dict[str, Any]) -> dict[str, Any]:
        return {
            "idx": index,
            "segment": segment,
            "voice": self.audio_path,
            "duration": self.duration_sec,
            "voice_track": self,
        }
