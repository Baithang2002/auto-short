"""
queue.py  -  Shared helpers for the review + upload queue.

Folder layout (under ./videos/):
    pending/<id>/   video.mp4 + metadata.json                 (waiting for human review)
    approved/<id>/  video.mp4 + metadata.json (with schedule) (waiting to upload)
    rejected/<id>/  video.mp4 + metadata.json (with reason)
    uploaded/<id>/  video.mp4 + metadata.json (with results)

Used by:
  - auto_short.py       (writes into pending/)
  - review_dashboard.py (moves pending -> approved / rejected)
  - upload_worker.py    (moves approved -> uploaded when due)
"""

from __future__ import annotations

import json
import shutil
import datetime as dt
from pathlib import Path
from typing import Optional

ROOT_DIR    = Path(__file__).parent.resolve()
VIDEOS_DIR  = ROOT_DIR / "videos"
PENDING     = VIDEOS_DIR / "pending"
APPROVED    = VIDEOS_DIR / "approved"
REJECTED    = VIDEOS_DIR / "rejected"
UPLOADED    = VIDEOS_DIR / "uploaded"

PLATFORMS = ("youtube", "instagram", "facebook")


def ensure_dirs() -> None:
    for d in (PENDING, APPROVED, REJECTED, UPLOADED):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------
def _read_meta(folder: Path) -> Optional[dict]:
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta["_folder"]    = str(folder)
    meta["_meta_path"] = str(meta_path)
    return meta


def _list(stage_dir: Path) -> list[dict]:
    ensure_dirs()
    items = []
    for child in sorted(stage_dir.iterdir()):
        if not child.is_dir():
            continue
        meta = _read_meta(child)
        if meta:
            items.append(meta)
    # newest first
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items


def list_pending()  -> list[dict]: return _list(PENDING)
def list_approved() -> list[dict]: return _list(APPROVED)
def list_rejected() -> list[dict]: return _list(REJECTED)
def list_uploaded() -> list[dict]: return _list(UPLOADED)


def find(video_id: str) -> Optional[dict]:
    """Find a video in any stage by its id."""
    for stage in (PENDING, APPROVED, REJECTED, UPLOADED):
        folder = stage / video_id
        if folder.exists():
            return _read_meta(folder)
    return None


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------
def _save_meta(folder: Path, meta: dict) -> None:
    meta = {k: v for k, v in meta.items() if not k.startswith("_")}
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _move(src_dir: Path, dst_root: Path) -> Path:
    """Move a video folder.  Uses copy-then-delete so it survives
    Windows file-lock errors (WinError 32) from open video handles."""
    import time, gc

    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / src_dir.name
    if dst.exists():
        shutil.rmtree(dst)

    # Copy first — this always works even when a file is open.
    shutil.copytree(str(src_dir), str(dst))

    # Now try to remove the source; retry a few times if a handle is held.
    for attempt in range(5):
        try:
            gc.collect()                       # release any lingering refs
            shutil.rmtree(str(src_dir))
            break
        except PermissionError:
            if attempt == 4:
                # Give up removing source — the copy succeeded, so the
                # state transition is complete.  Log but don't crash.
                import logging
                logging.warning("Could not remove source %s (file locked)", src_dir)
            time.sleep(0.5)
    return dst


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
    src = PENDING / video_id
    if not src.exists():
        raise FileNotFoundError(f"No pending video with id {video_id}")
    meta = _read_meta(src) or {}

    # Apply edits
    for k in ("title", "description", "instagram_caption"):
        if k in edits and edits[k] is not None:
            meta[k] = edits[k]
    if "hashtags" in edits and edits["hashtags"] is not None:
        tags = edits["hashtags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace(",", " ").split() if t.strip()]
        meta["hashtags"] = tags

    meta["status"] = "approved"
    meta["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    meta["schedule"] = {p: (schedule.get(p) or "").strip() for p in PLATFORMS}
    meta["upload_results"] = {}

    dst = _move(src, APPROVED)
    _save_meta(dst, meta)
    return _read_meta(dst)


def reject(video_id: str, reason: str = "") -> dict:
    src = PENDING / video_id
    if not src.exists():
        raise FileNotFoundError(f"No pending video with id {video_id}")
    meta = _read_meta(src) or {}
    meta["status"] = "rejected"
    meta["rejected_at"] = dt.datetime.now().isoformat(timespec="seconds")
    meta["reject_reason"] = reason or ""
    dst = _move(src, REJECTED)
    _save_meta(dst, meta)
    return _read_meta(dst)


def list_due_for_upload(now: Optional[dt.datetime] = None) -> list[tuple[dict, str]]:
    """
    Return [(meta, platform), ...] for every (video, platform) where the scheduled
    post time is in the past and has not already been uploaded.
    """
    now = now or dt.datetime.now()
    due: list[tuple[dict, str]] = []
    for meta in list_approved():
        schedule = meta.get("schedule", {}) or {}
        results  = meta.get("upload_results", {}) or {}
        for platform in PLATFORMS:
            when = (schedule.get(platform) or "").strip()
            if not when:
                continue
            if results.get(platform, {}).get("status") == "ok":
                continue
            try:
                when_dt = dt.datetime.fromisoformat(when)
            except ValueError:
                continue
            if when_dt <= now:
                due.append((meta, platform))
    return due


def record_upload_result(video_id: str, platform: str, result: dict) -> dict:
    """Record per-platform upload outcome. When all scheduled platforms are
    done, move from approved/ to uploaded/."""
    folder = APPROVED / video_id
    if not folder.exists():
        raise FileNotFoundError(f"No approved video with id {video_id}")
    meta = _read_meta(folder) or {}
    meta.setdefault("upload_results", {})[platform] = {
        **result,
        "at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    _save_meta(folder, meta)

    # If every scheduled platform has succeeded, finalize.
    scheduled = [p for p in PLATFORMS if (meta.get("schedule", {}) or {}).get(p)]
    done = all(meta["upload_results"].get(p, {}).get("status") == "ok" for p in scheduled)
    if scheduled and done:
        meta["status"] = "uploaded"
        meta["uploaded_at"] = dt.datetime.now().isoformat(timespec="seconds")
        dst = _move(folder, UPLOADED)
        _save_meta(dst, meta)
        return _read_meta(dst)
    return meta
