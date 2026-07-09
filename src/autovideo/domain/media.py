"""Typed media assets selected for a script segment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class MediaSource(str, Enum):
    LOCAL = "local"
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    NASA = "nasa"
    GEMINI_IMAGE = "gemini_image"
    DALLE = "dalle"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MediaAsset:
    """Media selected for a timeline segment."""

    local_path: Path
    source: MediaSource = MediaSource.UNKNOWN
    source_id: str = ""
    duration_sec: float | None = None
    dimensions: tuple[int, int] | None = None
    is_image: bool = False
    attribution: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_path"] = str(self.local_path)
        data["source"] = self.source.value
        data["dimensions"] = list(self.dimensions) if self.dimensions else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaAsset":
        dimensions = data.get("dimensions")
        try:
            source = MediaSource(data.get("source", MediaSource.UNKNOWN.value))
        except ValueError:
            source = MediaSource.UNKNOWN
        return cls(
            local_path=Path(data["local_path"]),
            source=source,
            source_id=str(data.get("source_id", "")),
            duration_sec=data.get("duration_sec"),
            dimensions=tuple(dimensions) if dimensions else None,
            is_image=bool(data.get("is_image", False)),
            attribution=dict(data.get("attribution", {})),
            metadata=dict(data.get("metadata", {})),
        )
