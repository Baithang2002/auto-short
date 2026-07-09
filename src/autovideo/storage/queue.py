"""Filesystem queue abstraction.

This intentionally mirrors the existing layout:
videos/pending, videos/approved, videos/rejected, videos/uploaded.
"""

from __future__ import annotations

import shutil
import gc
import logging
import time
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from autovideo.storage.metadata_store import (
    JsonMetadataStore,
    MetadataCorruptError,
    MetadataNotFoundError,
)

LOGGER = logging.getLogger("autovideo.storage")


class QueueStage(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    UPLOADED = "uploaded"


@dataclass(frozen=True)
class QueueItem:
    id: str
    stage: QueueStage
    folder: Path
    metadata: dict


class FilesystemQueue:
    def __init__(self, videos_dir: Path, metadata_store: JsonMetadataStore | None = None) -> None:
        self.videos_dir = Path(videos_dir)
        self.metadata_store = metadata_store or JsonMetadataStore()

    def stage_dir(self, stage: QueueStage) -> Path:
        return self.videos_dir / stage.value

    def _log(self, *, job_id: str, operation: str, status: str, started: float, warnings: list[str] | None = None) -> None:
        LOGGER.info(
            "storage_operation",
            extra={
                "job_id": job_id,
                "operation": operation,
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "warnings": warnings or [],
            },
        )

    def _with_log(self, job_id: str, operation: str, fn: Callable[[], object]) -> object:
        started = time.perf_counter()
        try:
            result = fn()
            self._log(job_id=job_id, operation=operation, status="ok", started=started)
            return result
        except Exception as e:
            self._log(job_id=job_id, operation=operation, status="error", started=started, warnings=[type(e).__name__])
            raise

    def ensure_dirs(self) -> None:
        for stage in QueueStage:
            self.stage_dir(stage).mkdir(parents=True, exist_ok=True)

    def list(self, stage: QueueStage) -> list[QueueItem]:
        started = time.perf_counter()
        self.ensure_dirs()
        items: list[QueueItem] = []
        warnings: list[str] = []
        for folder in sorted(self.stage_dir(stage).iterdir()):
            if not folder.is_dir():
                continue
            meta_path = folder / self.metadata_store.filename
            if not meta_path.exists():
                continue
            try:
                metadata = self.metadata_store.read_dict(folder)
            except (MetadataCorruptError, MetadataNotFoundError, OSError) as e:
                LOGGER.warning(
                    "storage_operation",
                    extra={
                        "job_id": folder.name,
                        "operation": f"list_{stage.value}",
                        "status": "skipped",
                        "elapsed_ms": 0,
                        "warnings": [type(e).__name__],
                    },
                )
                warnings.append(f"{folder.name}:{type(e).__name__}")
                continue
            items.append(QueueItem(
                id=str(metadata.get("id") or folder.name),
                stage=stage,
                folder=folder,
                metadata=metadata,
            ))
        items.sort(key=lambda item: str(item.metadata.get("created_at", "")), reverse=True)
        self._log(job_id="*", operation=f"list_{stage.value}", status="ok", started=started, warnings=warnings)
        return items

    def find(self, video_id: str) -> QueueItem | None:
        def op() -> QueueItem | None:
            self.ensure_dirs()
            for stage in QueueStage:
                folder = self.stage_dir(stage) / video_id
                if folder.exists() and (folder / self.metadata_store.filename).exists():
                    try:
                        metadata = self.metadata_store.read_dict(folder)
                    except (MetadataCorruptError, MetadataNotFoundError, OSError):
                        continue
                    return QueueItem(video_id, stage, folder, metadata)
            return None

        return self._with_log(video_id, "find", op)  # type: ignore[return-value]

    def move(self, video_id: str, source: QueueStage, destination: QueueStage) -> QueueItem:
        def op() -> QueueItem:
            return self._move_unlogged(video_id, source, destination)

        return self._with_log(video_id, f"move_{source.value}_to_{destination.value}", op)  # type: ignore[return-value]

    def _move_unlogged(self, video_id: str, source: QueueStage, destination: QueueStage) -> QueueItem:
        self.ensure_dirs()
        src = self.stage_dir(source) / video_id
        dst = self.stage_dir(destination) / video_id
        if not src.exists():
            raise FileNotFoundError(f"No {source.value} item with id {video_id}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        for attempt in range(5):
            try:
                gc.collect()
                shutil.rmtree(src)
                break
            except PermissionError:
                if attempt == 4:
                    LOGGER.warning(
                        "storage_operation",
                        extra={
                            "job_id": video_id,
                            "operation": "remove_source_after_move",
                            "status": "warning",
                            "elapsed_ms": 0,
                            "warnings": ["PermissionError"],
                        },
                    )
                time.sleep(0.5)
        metadata = self.metadata_store.read_dict(dst)
        metadata["status"] = destination.value
        self.metadata_store.write_dict(dst, metadata)
        return QueueItem(video_id, destination, dst, metadata)

    def create_pending(self, video_id: str, metadata: dict, artifacts: dict[str, Path] | None = None) -> QueueItem:
        def op() -> QueueItem:
            self.ensure_dirs()
            folder = self.stage_dir(QueueStage.PENDING) / video_id
            if folder.exists():
                raise FileExistsError(f"Queue item already exists: {video_id}")
            folder.mkdir(parents=True)
            for filename, source in (artifacts or {}).items():
                shutil.copy2(str(source), str(folder / filename))
            payload = dict(metadata)
            payload.setdefault("id", video_id)
            payload.setdefault("status", QueueStage.PENDING.value)
            self.metadata_store.write_dict(folder, payload, validate=True)
            return QueueItem(video_id, QueueStage.PENDING, folder, payload)

        return self._with_log(video_id, "create_pending", op)  # type: ignore[return-value]

    def update_metadata(self, video_id: str, stage: QueueStage, updater: Callable[[dict], dict]) -> QueueItem:
        def op() -> QueueItem:
            folder = self.stage_dir(stage) / video_id
            if not folder.exists():
                raise FileNotFoundError(f"No {stage.value} item with id {video_id}")
            metadata = self.metadata_store.read_dict(folder)
            updated = updater(dict(metadata))
            self.metadata_store.write_dict(folder, updated)
            return QueueItem(video_id, stage, folder, updated)

        return self._with_log(video_id, f"update_{stage.value}_metadata", op)  # type: ignore[return-value]

    def update_status(self, video_id: str, stage: QueueStage, status: str) -> QueueItem:
        return self.update_metadata(video_id, stage, lambda meta: {**meta, "status": status})

    def _read_metadata_or_empty(self, folder: Path) -> dict:
        try:
            return self.metadata_store.read_dict(folder)
        except (MetadataCorruptError, MetadataNotFoundError, OSError):
            return {}

    def approve(self, video_id: str, edits: dict, schedule: dict, platforms: tuple[str, ...]) -> QueueItem:
        def op() -> QueueItem:
            src = self.stage_dir(QueueStage.PENDING) / video_id
            if not src.exists():
                raise FileNotFoundError(f"No pending video with id {video_id}")
            metadata = self._read_metadata_or_empty(src)
            for key in ("title", "description", "instagram_caption"):
                if key in edits and edits[key] is not None:
                    metadata[key] = edits[key]
            if "hashtags" in edits and edits["hashtags"] is not None:
                tags = edits["hashtags"]
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.replace(",", " ").split() if t.strip()]
                metadata["hashtags"] = tags
            metadata["status"] = QueueStage.APPROVED.value
            metadata["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
            metadata["schedule"] = {p: (schedule.get(p) or "").strip() for p in platforms}
            metadata["upload_results"] = {}
            self.metadata_store.write_dict(src, metadata)
            return self.move(video_id, QueueStage.PENDING, QueueStage.APPROVED)

        return self._with_log(video_id, "approve", op)  # type: ignore[return-value]

    def reject(self, video_id: str, reason: str = "") -> QueueItem:
        def op() -> QueueItem:
            src = self.stage_dir(QueueStage.PENDING) / video_id
            if not src.exists():
                raise FileNotFoundError(f"No pending video with id {video_id}")
            metadata = self._read_metadata_or_empty(src)
            metadata["status"] = QueueStage.REJECTED.value
            metadata["rejected_at"] = dt.datetime.now().isoformat(timespec="seconds")
            metadata["reject_reason"] = reason or ""
            self.metadata_store.write_dict(src, metadata)
            return self.move(video_id, QueueStage.PENDING, QueueStage.REJECTED)

        return self._with_log(video_id, "reject", op)  # type: ignore[return-value]

    def list_due_for_upload(self, platforms: tuple[str, ...], now: dt.datetime | None = None) -> list[tuple[QueueItem, str]]:
        now = now or dt.datetime.now()
        due: list[tuple[QueueItem, str]] = []
        for item in self.list(QueueStage.APPROVED):
            schedule = item.metadata.get("schedule", {}) or {}
            results = item.metadata.get("upload_results", {}) or {}
            for platform in platforms:
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
                    due.append((item, platform))
        return due

    def record_upload_result(self, video_id: str, platform: str, result: dict, platforms: tuple[str, ...]) -> QueueItem:
        def op() -> QueueItem:
            def apply_result(meta: dict) -> dict:
                meta.setdefault("upload_results", {})[platform] = {
                    **result,
                    "at": dt.datetime.now().isoformat(timespec="seconds"),
                }
                return meta

            folder = self.stage_dir(QueueStage.APPROVED) / video_id
            if not folder.exists():
                raise FileNotFoundError(f"No approved video with id {video_id}")
            metadata = apply_result(self._read_metadata_or_empty(folder))
            self.metadata_store.write_dict(folder, metadata)
            item = QueueItem(video_id, QueueStage.APPROVED, folder, metadata)
            scheduled = [p for p in platforms if (item.metadata.get("schedule", {}) or {}).get(p)]
            done = all(item.metadata.get("upload_results", {}).get(p, {}).get("status") == "ok" for p in scheduled)
            if scheduled and done:
                def mark_uploaded(meta: dict) -> dict:
                    meta["status"] = QueueStage.UPLOADED.value
                    meta["uploaded_at"] = dt.datetime.now().isoformat(timespec="seconds")
                    return meta

                self.update_metadata(video_id, QueueStage.APPROVED, mark_uploaded)
                return self.move(video_id, QueueStage.APPROVED, QueueStage.UPLOADED)
            return item

        return self._with_log(video_id, f"record_upload_result_{platform}", op)  # type: ignore[return-value]
