"""Timeline models shared by Shorts, long-form, podcasts, and education videos."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from autovideo.domain.media import MediaAsset
from autovideo.domain.script import ScriptSegment
from autovideo.domain.voice import VoiceTrack


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    CAPTION = "caption"
    OVERLAY = "overlay"


@dataclass(frozen=True)
class TimelineItem:
    """A timed item on a flattened timeline track.

    This class is kept as the compatibility bridge for PR #4 callers. PR #5
    adds richer scene and track models, but the flattened item API remains
    stable for old tests and future renderer adapters.
    """

    id: str
    track_type: TrackType
    track_name: str
    start_sec: float
    end_sec: float
    asset_id: str | None = None
    scene_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["track_type"] = self.track_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimelineItem":
        return cls(
            id=str(data["id"]),
            track_type=TrackType(data["track_type"]),
            track_name=str(data["track_name"]),
            start_sec=float(data["start_sec"]),
            end_sec=float(data["end_sec"]),
            asset_id=data.get("asset_id"),
            scene_id=data.get("scene_id"),
            properties=dict(data.get("properties", {})),
        )


@dataclass(frozen=True)
class Timeline:
    """Declarative intermediate representation for a video."""

    id: str
    episode_id: str
    width: int
    height: int
    fps: int
    items: list[TimelineItem] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    format_profile: str = "shorts_vertical"
    assets: dict[str, MediaAsset] = field(default_factory=dict)
    scenes: list["TimelineScene"] = field(default_factory=list)
    tracks: list["TimelineTrack"] = field(default_factory=list)
    captions: list["CaptionEntry"] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        ends = [item.end_sec for item in self.items]
        ends.extend(scene.end_sec for scene in self.scenes)
        ends.extend(caption.end_sec for caption in self.captions)
        for track in self.tracks:
            ends.extend(item.end_sec for item in track.items)
        return max(ends, default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "format_profile": self.format_profile,
            "items": [item.to_dict() for item in self.items],
            "chapters": self.chapters,
            "assets": {
                asset_id: asset.to_dict()
                for asset_id, asset in self.assets.items()
            },
            "scenes": [scene.to_dict() for scene in self.scenes],
            "tracks": [track.to_dict() for track in self.tracks],
            "captions": [caption.to_dict() for caption in self.captions],
            "transitions": self.transitions,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        return cls(
            id=str(data["id"]),
            episode_id=str(data["episode_id"]),
            width=int(data["width"]),
            height=int(data["height"]),
            fps=int(data["fps"]),
            items=[TimelineItem.from_dict(item) for item in data.get("items", [])],
            chapters=list(data.get("chapters", [])),
            format_profile=str(data.get("format_profile", "shorts_vertical")),
            assets={
                str(asset_id): MediaAsset.from_dict(asset)
                for asset_id, asset in data.get("assets", {}).items()
            },
            scenes=[TimelineScene.from_dict(scene) for scene in data.get("scenes", [])],
            tracks=[TimelineTrack.from_dict(track) for track in data.get("tracks", [])],
            captions=[CaptionEntry.from_dict(caption) for caption in data.get("captions", [])],
            transitions=list(data.get("transitions", [])),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the timeline to JSON without losing compatibility fields."""

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> "Timeline":
        """Deserialize a timeline from JSON produced by :meth:`to_json`."""

        return cls.from_dict(json.loads(payload))

    def write_json(self, path: Path) -> Path:
        """Write a JSON timeline artifact for debugging and future resumability."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path


@dataclass(frozen=True)
class TimelineScene:
    """One planned scene connecting script, voice, and selected media."""

    id: str
    index: int
    start_sec: float
    end_sec: float
    script_segment: ScriptSegment
    voice_track: VoiceTrack
    media_asset_id: str
    transition: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "script_segment": self.script_segment.to_legacy_dict(),
            "voice_track": self.voice_track.to_dict(),
            "media_asset_id": self.media_asset_id,
            "transition": self.transition,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimelineScene":
        return cls(
            id=str(data["id"]),
            index=int(data["index"]),
            start_sec=float(data["start_sec"]),
            end_sec=float(data["end_sec"]),
            script_segment=ScriptSegment.from_legacy_dict(dict(data["script_segment"])),
            voice_track=VoiceTrack.from_dict(dict(data["voice_track"])),
            media_asset_id=str(data["media_asset_id"]),
            transition=dict(data.get("transition", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class TimelineTrack:
    """Named sequence of non-overlapping timed items."""

    name: str
    track_type: TrackType
    items: list[TimelineItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "track_type": self.track_type.value,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimelineTrack":
        return cls(
            name=str(data["name"]),
            track_type=TrackType(data["track_type"]),
            items=[TimelineItem.from_dict(item) for item in data.get("items", [])],
        )


@dataclass(frozen=True)
class CaptionEntry:
    """Timed caption text for a scene."""

    id: str
    start_sec: float
    end_sec: float
    text: str
    scene_id: str = ""
    highlight_words: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "text": self.text,
            "scene_id": self.scene_id,
            "highlight_words": list(self.highlight_words),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptionEntry":
        return cls(
            id=str(data["id"]),
            start_sec=float(data["start_sec"]),
            end_sec=float(data["end_sec"]),
            text=str(data["text"]),
            scene_id=str(data.get("scene_id", "")),
            highlight_words=[str(word) for word in data.get("highlight_words", [])],
            metadata=dict(data.get("metadata", {})),
        )
