"""Scene planning models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SceneType(str, Enum):
    STOCK_VIDEO = "stock_video"
    IMAGE = "image"
    TEXT_CARD = "text_card"
    TITLE_CARD = "title_card"
    CITATION = "citation"
    CHART = "chart"
    VS_CARD = "vs_card"
    PODCAST_WAVEFORM = "podcast_waveform"
    INTERVIEW = "interview"


@dataclass(frozen=True)
class VisualPlan:
    visual_type: SceneType = SceneType.STOCK_VIDEO
    queries: list[str] = field(default_factory=list)
    preferred_provider: str = ""
    fallback_strategy: str = "stock_then_generated"
    overlay_text: str = ""
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["visual_type"] = self.visual_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "VisualPlan":
        if not data:
            return cls()
        return cls(
            visual_type=SceneType(data.get("visual_type", SceneType.STOCK_VIDEO.value)),
            queries=[str(q) for q in data.get("queries", [])],
            preferred_provider=str(data.get("preferred_provider", "")),
            fallback_strategy=str(data.get("fallback_strategy", "stock_then_generated")),
            overlay_text=str(data.get("overlay_text", "")),
            properties=dict(data.get("properties", {})),
        )


@dataclass(frozen=True)
class AudioPlan:
    voice_id: str = ""
    sfx: list[str] = field(default_factory=list)
    music_mood: str = ""
    music_intensity: float = 0.0


@dataclass(frozen=True)
class CaptionPlan:
    style: str = "default"
    highlight_terms: list[str] = field(default_factory=list)
    burn_in: bool = True


@dataclass(frozen=True)
class RetentionPlan:
    beat_type: str = ""
    transition: str = ""
    pacing: str = ""


@dataclass(frozen=True)
class Scene:
    id: str
    index: int
    narration: str
    scene_type: SceneType = SceneType.STOCK_VIDEO
    duration_target_sec: float | None = None
    visual: VisualPlan = field(default_factory=VisualPlan)
    audio: AudioPlan = field(default_factory=AudioPlan)
    captions: CaptionPlan = field(default_factory=CaptionPlan)
    retention: RetentionPlan = field(default_factory=RetentionPlan)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scene_type"] = self.scene_type.value
        data["visual"] = self.visual.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        return cls(
            id=str(data["id"]),
            index=int(data["index"]),
            narration=str(data.get("narration", "")),
            scene_type=SceneType(data.get("scene_type", SceneType.STOCK_VIDEO.value)),
            duration_target_sec=data.get("duration_target_sec"),
            visual=VisualPlan.from_dict(data.get("visual")),
            audio=AudioPlan(**dict(data.get("audio", {}))),
            captions=CaptionPlan(**dict(data.get("captions", {}))),
            retention=RetentionPlan(**dict(data.get("retention", {}))),
            metadata=dict(data.get("metadata", {})),
        )
