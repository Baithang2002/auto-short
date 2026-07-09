"""Episode domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .scene import Scene


@dataclass(frozen=True)
class Episode:
    id: str
    project_id: str
    topic: str
    target_duration_sec: int
    aspect_ratio: str
    language: str = "en"
    scenes: list[Scene] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "topic": self.topic,
            "target_duration_sec": self.target_duration_sec,
            "aspect_ratio": self.aspect_ratio,
            "language": self.language,
            "scenes": [scene.to_dict() for scene in self.scenes],
            "metadata": self.metadata,
        }
