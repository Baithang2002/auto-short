"""Voice provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from autovideo.providers.base import ProviderResult


class NarrationUnit(str, Enum):
    WHOLE = "whole"
    CHAPTER = "chapter"
    SCENE = "scene"


@dataclass(frozen=True)
class VoiceRequest:
    text: str
    output_path: Path
    voice_id: str = ""
    unit: NarrationUnit = NarrationUnit.SCENE
    chapter_id: str = ""
    scene_id: str = ""
    metadata: dict[str, object] | None = None


class VoiceProvider(Protocol):
    name: str

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        """Generate a voice file and return its path."""
