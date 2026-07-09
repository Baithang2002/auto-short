"""Typed script artifacts passed from scripting into voice and media stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autovideo.domain.errors import ValidationError


class MusicMood(str, Enum):
    MYSTERIOUS = "mysterious"
    INSPIRING = "inspiring"
    DRAMATIC = "dramatic"
    WARM = "warm"
    CURIOUS = "curious"
    URGENT = "urgent"


@dataclass(frozen=True)
class ScriptSegment:
    """One narrated segment from the script stage."""

    narration: str
    broll: str
    broll_queries: list[str] = field(default_factory=list)
    estimated_duration_sec: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.narration.strip():
            raise ValidationError("ScriptSegment.narration is required")

    def to_legacy_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data.update({
            "narration": self.narration,
            "broll": self.broll,
            "broll_queries": list(self.broll_queries),
        })
        if self.estimated_duration_sec is not None:
            data["estimated_duration"] = self.estimated_duration_sec
        return data

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "ScriptSegment":
        known = {"narration", "broll", "broll_queries", "estimated_duration", "estimated_duration_sec"}
        raw_queries = data.get("broll_queries") or []
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        estimated = data.get("estimated_duration_sec", data.get("estimated_duration"))
        return cls(
            narration=str(data.get("narration", "")),
            broll=str(data.get("broll", "")),
            broll_queries=[str(query) for query in raw_queries],
            estimated_duration_sec=float(estimated) if estimated is not None else None,
            metadata={k: v for k, v in data.items() if k not in known},
        )


@dataclass(frozen=True)
class Script:
    """Structured script artifact consumed by downstream stages."""

    title: str
    description: str = ""
    instagram_caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    music_mood: str = ""
    segments: list[ScriptSegment] = field(default_factory=list)
    niche: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("Script.title is required")
        if not self.segments:
            raise ValidationError("Script.segments must contain at least one segment")

    def to_legacy_dict(self) -> dict[str, Any]:
        data = dict(self.metadata)
        data.update({
            "title": self.title,
            "description": self.description,
            "instagram_caption": self.instagram_caption,
            "hashtags": list(self.hashtags),
            "music_mood": self.music_mood,
            "segments": [segment.to_legacy_dict() for segment in self.segments],
        })
        if self.niche:
            data["niche"] = self.niche
        return data

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any], *, niche: str = "") -> "Script":
        known = {
            "title",
            "description",
            "instagram_caption",
            "hashtags",
            "music_mood",
            "segments",
            "niche",
        }
        hashtags = data.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag for tag in hashtags.replace(",", " ").split() if tag]
        return cls(
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            instagram_caption=str(data.get("instagram_caption", "")),
            hashtags=[str(tag) for tag in hashtags],
            music_mood=str(data.get("music_mood", "")),
            segments=[ScriptSegment.from_legacy_dict(segment) for segment in data.get("segments", [])],
            niche=str(data.get("niche") or niche),
            metadata={k: v for k, v in data.items() if k not in known},
        )
