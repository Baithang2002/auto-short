"""
queue.py  -  Shared helpers for the review + upload queue.

This module keeps the legacy public API used by review_dashboard.py and
upload_worker.py, while delegating filesystem work to autovideo.storage.
"""

from __future__ import annotations

import sys
import datetime as dt
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).parent.resolve()
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovideo.storage import FilesystemQueue, JsonMetadataStore, QueueItem, QueueStage
from autovideo.storage.metadata_store import MetadataCorruptError, MetadataNotFoundError

VIDEOS_DIR = ROOT_DIR / "videos"
PENDING = VIDEOS_DIR / "pending"
APPROVED = VIDEOS_DIR / "approved"
REJECTED = VIDEOS_DIR / "rejected"
UPLOADED = VIDEOS_DIR / "uploaded"

PLATFORMS = ("youtube", "instagram", "facebook")

_STAGE_BY_DIR = {
    PENDING.resolve(): QueueStage.PENDING,
    APPROVED.resolve(): QueueStage.APPROVED,
    REJECTED.resolve(): QueueStage.REJECTED,
    UPLOADED.resolve(): QueueStage.UPLOADED,
}

_metadata_store = JsonMetadataStore()
_queue = FilesystemQueue(VIDEOS_DIR, metadata_store=_metadata_store)


def ensure_dirs() -> None:
    _queue.ensure_dirs()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------
def _with_legacy_fields(item: QueueItem) -> dict:
    meta = dict(item.metadata)
    meta["_folder"] = str(item.folder)
    meta["_meta_path"] = str(item.folder / _metadata_store.filename)
    return meta


def _read_meta(folder: Path) -> Optional[dict]:
    meta_path = folder / _metadata_store.filename
    if not meta_path.exists():
        return None
    try:
        meta = _metadata_store.read_dict(folder)
    except (MetadataCorruptError, MetadataNotFoundError, OSError):
        return None
    meta["_folder"] = str(folder)
    meta["_meta_path"] = str(meta_path)
    return meta


def _stage_from_dir(stage_dir: Path) -> QueueStage:
    resolved = Path(stage_dir).resolve()
    try:
        return _STAGE_BY_DIR[resolved]
    except KeyError as e:
        raise ValueError(f"Unknown queue stage directory: {stage_dir}") from e


def _list(stage_dir: Path) -> list[dict]:
    stage = _stage_from_dir(stage_dir)
    return [_with_legacy_fields(item) for item in _queue.list(stage)]


def list_pending() -> list[dict]:
    return _list(PENDING)


def list_approved() -> list[dict]:
    return _list(APPROVED)


def list_rejected() -> list[dict]:
    return _list(REJECTED)


def list_uploaded() -> list[dict]:
    return _list(UPLOADED)


def find(video_id: str) -> Optional[dict]:
    """Find a video in any stage by its id."""
    item = _queue.find(video_id)
    if item is None:
        return None
    return _with_legacy_fields(item)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------
def _save_meta(folder: Path, meta: dict) -> None:
    clean = {k: v for k, v in meta.items() if not k.startswith("_")}
    _metadata_store.write_dict(folder, clean)


def _move(src_dir: Path, dst_root: Path) -> Path:
    source = _stage_from_dir(src_dir.parent)
    destination = _stage_from_dir(dst_root)
    moved = _queue.move(src_dir.name, source, destination)
    return moved.folder


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------
def approve(video_id: str, edits: dict, schedule: dict) -> dict:
    """
    Move a pending video to approved/ with editable text + per-platform schedule.

    edits is a dict of fields the reviewer changed:
        title, description, instagram_caption, hashtags (list)
    schedule is a dict like:
        {"youtube": "2026-06-22T09:00:00", "instagram": "...", "facebook": "..."}
        Missing/blank entries mean "do not post to that platform".
    """
    item = _queue.approve(video_id, edits, schedule, PLATFORMS)
    return _with_legacy_fields(item)


def reject(video_id: str, reason: str = "") -> dict:
    item = _queue.reject(video_id, reason)
    return _with_legacy_fields(item)


def list_due_for_upload(now: Optional[dt.datetime] = None) -> list[tuple[dict, str]]:
    """
    Return [(meta, platform), ...] for every (video, platform) where the scheduled
    post time is in the past and has not already been uploaded.
    """
    return [(_with_legacy_fields(item), platform) for item, platform in _queue.list_due_for_upload(PLATFORMS, now)]


def record_upload_result(video_id: str, platform: str, result: dict) -> dict:
    """Record per-platform upload outcome. When all scheduled platforms are
    done, move from approved/ to uploaded/."""
    item = _queue.record_upload_result(video_id, platform, result, PLATFORMS)
    return _with_legacy_fields(item)
