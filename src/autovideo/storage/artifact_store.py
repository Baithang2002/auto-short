"""Artifact filesystem helpers."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure_dir(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def copy_into(self, source: Path, destination_dir: Path, filename: str | None = None) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / (filename or source.name)
        shutil.copy2(str(source), str(target))
        return target

    def queue_root(self) -> Path:
        return self.ensure_dir("videos")

    def queue_item_path(self, stage: str, job_id: str) -> Path:
        return self.root / "videos" / stage / job_id

    def queue_item_dir(self, stage: str, job_id: str) -> Path:
        return self.ensure_dir("videos", stage, job_id)

    def output_path(self, *parts: str) -> Path:
        return self.root.joinpath("output", *parts)

    def temp_dir(self, job_id: str | None = None) -> Path:
        return self.ensure_dir("output", "tmp", *(filter(None, [job_id])))

    def rendered_video_path(self, filename: str = "final.mp4") -> Path:
        return self.output_path(filename)

    def captions_path(self, filename: str = "captions.ass") -> Path:
        return self.output_path(filename)

    def thumbnail_path(self, job_id: str, filename: str = "thumbnail.jpg") -> Path:
        return self.ensure_dir("output", "thumbnails", job_id) / filename

    def copy_video_variant(self, source: Path, job_folder: Path, *, youtube_safe: bool = False) -> Path:
        filename = "video_yt_safe.mp4" if youtube_safe else "video.mp4"
        return self.copy_into(Path(source), Path(job_folder), filename)

    def checksum(self, path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
