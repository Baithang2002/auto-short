"""Renderer contracts for Timeline-based video production."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autovideo.domain import MasteredVideo, Timeline


@dataclass(frozen=True)
class RenderResult:
    """Artifacts produced by rendering a Timeline."""

    mastered_video: MasteredVideo
    final_path: Path
    youtube_safe_path: Path
    captioned_path: Path
    combined_path: Path
    final_duration_sec: float
    youtube_safe_duration_sec: float
    music_path: str | Path | None = None
    segment_paths: list[Path] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Renderer(ABC):
    """Abstract renderer interface.

    Workflow code should depend on this contract instead of FFmpeg-specific
    functions. Concrete implementations may use FFmpeg, MoviePy, cloud
    renderers, or future timeline-native renderers.
    """

    @abstractmethod
    def validate(self, timeline: Timeline) -> None:
        """Validate that this renderer can render the supplied timeline."""

    @abstractmethod
    def render(self, timeline: Timeline) -> RenderResult:
        """Render a full artifact set from a Timeline."""

    @abstractmethod
    def render_master(self, timeline: Timeline) -> MasteredVideo:
        """Render and return the canonical mastered video artifact."""
