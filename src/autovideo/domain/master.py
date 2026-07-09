"""Typed master-video artifact."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autovideo.domain.errors import ValidationError


@dataclass(frozen=True)
class MasteredVideo:
    """Final rendered video and platform-specific variants."""

    video_path: Path
    duration_sec: float
    format_profile: str
    music_included: bool = False
    platform_variants: dict[str, Path] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_sec < 0:
            raise ValidationError("MasteredVideo.duration_sec must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["video_path"] = str(self.video_path)
        data["platform_variants"] = {
            platform: str(path) for platform, path in self.platform_variants.items()
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MasteredVideo":
        return cls(
            video_path=Path(data["video_path"]),
            duration_sec=float(data.get("duration_sec", data.get("duration", 0.0)) or 0.0),
            format_profile=str(data.get("format_profile", "")),
            music_included=bool(data.get("music_included", False)),
            platform_variants={
                str(platform): Path(path) for platform, path in dict(data.get("platform_variants", {})).items()
            },
            metadata=dict(data.get("metadata", {})),
        )
