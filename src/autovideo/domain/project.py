"""Project-level domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class VideoFormat(str, Enum):
    SHORT = "short"
    DOCUMENTARY = "documentary"
    PODCAST = "podcast"
    EDUCATIONAL = "educational"


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    RENDERING = "rendering"
    REVIEW = "review"
    APPROVED = "approved"
    UPLOADED = "uploaded"
    FAILED = "failed"


@dataclass(frozen=True)
class VideoProject:
    id: str
    channel_id: str
    title: str
    video_format: VideoFormat
    status: ProjectStatus = ProjectStatus.DRAFT
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["video_format"] = self.video_format.value
        data["status"] = self.status.value
        return data
