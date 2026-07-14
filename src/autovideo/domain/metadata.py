"""Upload metadata model compatible with current metadata.json files."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UploadMetadata:
    id: str
    title: str
    video_path: Path
    niche: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    pinned_comment: str = ""
    facebook_description: str = ""
    instagram_caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    youtube_tags: str = ""
    duration_sec: float = 0.0
    orientation: str = "portrait"
    status: str = "pending"
    video_file: str = "video.mp4"
    video_file_yt: str = "video_yt_safe.mp4"
    video_path_yt: Path | None = None
    music_mood: str | None = None
    music_path: str | None = None
    music_volume: float | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_legacy_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["video_path"] = str(self.video_path)
        data["video_path_yt"] = str(self.video_path_yt) if self.video_path_yt else ""
        extra = data.pop("extra", {})
        data.update(extra)
        return data

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "UploadMetadata":
        known = {
            "id", "title", "video_path", "niche", "youtube_title",
            "youtube_description", "pinned_comment", "facebook_description", "instagram_caption",
            "hashtags", "youtube_tags", "duration_sec", "orientation", "status",
            "video_file", "video_file_yt", "video_path_yt", "music_mood",
            "music_path", "music_volume", "segments",
        }
        extra = {k: v for k, v in data.items() if k not in known and not k.startswith("_")}
        video_path = data.get("video_path") or data.get("path") or ""
        video_path_yt = data.get("video_path_yt") or ""
        hashtags = data.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.replace(",", " ").split() if tag.strip()]
        segments = data.get("segments") or []
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            video_path=Path(video_path),
            niche=str(data.get("niche", "")),
            youtube_title=str(data.get("youtube_title", "")),
            youtube_description=str(data.get("youtube_description", "")),
            pinned_comment=str(data.get("pinned_comment", "")),
            facebook_description=str(data.get("facebook_description", "")),
            instagram_caption=str(data.get("instagram_caption", "")),
            hashtags=list(hashtags),
            youtube_tags=str(data.get("youtube_tags", "")),
            duration_sec=float(data.get("duration_sec", 0.0) or 0.0),
            orientation=str(data.get("orientation", "portrait")),
            status=str(data.get("status", "pending")),
            video_file=str(data.get("video_file", "video.mp4")),
            video_file_yt=str(data.get("video_file_yt", "video_yt_safe.mp4")),
            video_path_yt=Path(video_path_yt) if video_path_yt else None,
            music_mood=data.get("music_mood"),
            music_path=data.get("music_path"),
            music_volume=data.get("music_volume"),
            segments=list(segments),
            extra=extra,
        )
