from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401
from autovideo.domain.errors import ValidationError
from autovideo.storage import ArtifactStore, FilesystemQueue, JsonMetadataStore, QueueStage
from autovideo.storage.metadata_store import MetadataCorruptError
from autovideo.storage.schemas import validate_upload_metadata_dict


class FilesystemStorageTests(unittest.TestCase):
    def test_metadata_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "video-1"
            store = JsonMetadataStore()
            payload = {
                "id": "video-1",
                "title": "Test",
                "video_path": "video.mp4",
                "status": "pending",
            }

            store.write_dict(folder, payload)

            self.assertEqual(store.read_dict(folder), payload)

    def test_queue_lists_and_moves_item_without_changing_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            queue = FilesystemQueue(videos)
            queue.ensure_dirs()

            pending = videos / "pending" / "video-1"
            pending.mkdir(parents=True)
            (pending / "video.mp4").write_bytes(b"fake")
            JsonMetadataStore().write_dict(pending, {
                "id": "video-1",
                "title": "Test",
                "video_path": str(pending / "video.mp4"),
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
            })

            self.assertEqual(len(queue.list(QueueStage.PENDING)), 1)
            moved = queue.move("video-1", QueueStage.PENDING, QueueStage.APPROVED)

            self.assertEqual(moved.stage, QueueStage.APPROVED)
            self.assertFalse(pending.exists())
            self.assertTrue((videos / "approved" / "video-1" / "metadata.json").exists())
            self.assertEqual(moved.metadata["status"], "approved")

    def test_create_pending_rejects_duplicate_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            queue = FilesystemQueue(videos)
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"fake")
            metadata = {
                "id": "video-1",
                "title": "Test",
                "video_path": str(videos / "pending" / "video-1" / "video.mp4"),
                "status": "pending",
            }

            queue.create_pending("video-1", metadata, artifacts={"video.mp4": source})

            with self.assertRaises(FileExistsError):
                queue.create_pending("video-1", metadata, artifacts={"video.mp4": source})

    def test_queue_approve_reject_and_upload_transitions_preserve_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            queue = FilesystemQueue(videos)
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"fake")
            metadata = {
                "id": "video-1",
                "title": "Original",
                "video_path": str(videos / "pending" / "video-1" / "video.mp4"),
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
                "legacy_extra": {"keep": True},
            }
            queue.create_pending("video-1", metadata, artifacts={"video.mp4": source})

            approved = queue.approve(
                "video-1",
                {"title": "Edited", "hashtags": "#one, #two"},
                {"youtube": "2026-01-01T00:00:00", "instagram": "", "facebook": ""},
                ("youtube", "instagram", "facebook"),
            )

            self.assertEqual(approved.stage, QueueStage.APPROVED)
            self.assertEqual(approved.metadata["title"], "Edited")
            self.assertEqual(approved.metadata["hashtags"], ["#one", "#two"])
            self.assertEqual(approved.metadata["legacy_extra"], {"keep": True})
            due = queue.list_due_for_upload(("youtube", "instagram", "facebook"), now=None)
            self.assertEqual([(item.id, platform) for item, platform in due], [("video-1", "youtube")])

            uploaded = queue.record_upload_result(
                "video-1",
                "youtube",
                {"status": "ok", "remote_id": "abc"},
                ("youtube", "instagram", "facebook"),
            )

            self.assertEqual(uploaded.stage, QueueStage.UPLOADED)
            self.assertFalse((videos / "approved" / "video-1").exists())
            self.assertTrue((videos / "uploaded" / "video-1" / "metadata.json").exists())
            self.assertEqual(uploaded.metadata["upload_results"]["youtube"]["status"], "ok")

            metadata_2 = {
                "id": "video-2",
                "title": "Reject",
                "video_path": str(videos / "pending" / "video-2" / "video.mp4"),
                "status": "pending",
            }
            queue.create_pending("video-2", metadata_2, artifacts={"video.mp4": source})
            rejected = queue.reject("video-2", "not ready")

            self.assertEqual(rejected.stage, QueueStage.REJECTED)
            self.assertEqual(rejected.metadata["reject_reason"], "not ready")

    def test_queue_skips_missing_and_corrupted_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            queue = FilesystemQueue(videos)
            valid = videos / "pending" / "valid"
            valid.mkdir(parents=True)
            JsonMetadataStore().write_dict(valid, {
                "id": "valid",
                "title": "Test",
                "video_path": str(valid / "video.mp4"),
                "status": "pending",
            })
            missing = videos / "pending" / "missing"
            missing.mkdir(parents=True)
            corrupt = videos / "pending" / "corrupt"
            corrupt.mkdir(parents=True)
            (corrupt / "metadata.json").write_text("{", encoding="utf-8")

            with self.assertLogs("autovideo.storage", level="WARNING"):
                items = queue.list(QueueStage.PENDING)

            self.assertEqual([item.id for item in items], ["valid"])

    def test_queue_transitions_can_recover_corrupted_metadata_like_legacy_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            queue = FilesystemQueue(videos)
            pending = videos / "pending" / "video-1"
            pending.mkdir(parents=True)
            (pending / "video.mp4").write_bytes(b"fake")
            (pending / "metadata.json").write_text("{", encoding="utf-8")

            approved = queue.approve(
                "video-1",
                {"title": "Recovered"},
                {"youtube": "2026-01-01T00:00:00"},
                ("youtube", "instagram", "facebook"),
            )

            self.assertEqual(approved.stage, QueueStage.APPROVED)
            self.assertEqual(approved.metadata["title"], "Recovered")
            self.assertEqual(approved.metadata["status"], "approved")

    def test_legacy_video_queue_facade_preserves_public_shape(self) -> None:
        import video_queue

        with tempfile.TemporaryDirectory() as tmp:
            videos = Path(tmp) / "videos"
            old_values = {
                "VIDEOS_DIR": video_queue.VIDEOS_DIR,
                "PENDING": video_queue.PENDING,
                "APPROVED": video_queue.APPROVED,
                "REJECTED": video_queue.REJECTED,
                "UPLOADED": video_queue.UPLOADED,
                "_STAGE_BY_DIR": video_queue._STAGE_BY_DIR,
                "_metadata_store": video_queue._metadata_store,
                "_queue": video_queue._queue,
            }
            try:
                video_queue.VIDEOS_DIR = videos
                video_queue.PENDING = videos / "pending"
                video_queue.APPROVED = videos / "approved"
                video_queue.REJECTED = videos / "rejected"
                video_queue.UPLOADED = videos / "uploaded"
                video_queue._STAGE_BY_DIR = {
                    video_queue.PENDING.resolve(): QueueStage.PENDING,
                    video_queue.APPROVED.resolve(): QueueStage.APPROVED,
                    video_queue.REJECTED.resolve(): QueueStage.REJECTED,
                    video_queue.UPLOADED.resolve(): QueueStage.UPLOADED,
                }
                video_queue._metadata_store = JsonMetadataStore()
                video_queue._queue = FilesystemQueue(videos, metadata_store=video_queue._metadata_store)

                pending = videos / "pending" / "video-1"
                pending.mkdir(parents=True)
                JsonMetadataStore().write_dict(pending, {
                    "id": "video-1",
                    "title": "Test",
                    "video_path": str(pending / "video.mp4"),
                    "status": "pending",
                })

                listed = video_queue.list_pending()

                self.assertEqual(listed[0]["id"], "video-1")
                self.assertEqual(listed[0]["_folder"], str(pending))
                self.assertEqual(listed[0]["_meta_path"], str(pending / "metadata.json"))
                approved = video_queue.approve("video-1", {"title": "Edited"}, {"youtube": ""})
                self.assertEqual(approved["status"], "approved")
                self.assertTrue((videos / "approved" / "video-1").exists())
            finally:
                for name, value in old_values.items():
                    setattr(video_queue, name, value)

    def test_metadata_store_preserves_unknown_fields_and_detects_wrong_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "video-1"
            store = JsonMetadataStore()
            payload = {
                "id": "video-1",
                "title": "Test",
                "video_path": "video.mp4",
                "status": "pending",
                "unknown_future_field": {"nested": True},
            }

            store.write_dict(folder, payload)

            self.assertEqual(store.read_dict(folder)["unknown_future_field"], {"nested": True})
            (folder / "metadata.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(MetadataCorruptError):
                store.read_dict(folder)

    def test_metadata_store_retries_during_concurrent_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "video-1"
            folder.mkdir()
            store = JsonMetadataStore()
            (folder / "metadata.json").write_text('{"id":', encoding="utf-8")

            def rewrite() -> None:
                time.sleep(0.02)
                store.write_dict(folder, {
                    "id": "video-1",
                    "title": "Recovered",
                    "video_path": "video.mp4",
                    "status": "pending",
                })

            thread = threading.Thread(target=rewrite)
            thread.start()
            try:
                metadata = store.read_dict(folder, retries=10)
            finally:
                thread.join()

            self.assertEqual(metadata["title"], "Recovered")

    def test_artifact_store_paths_copy_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore(root)
            source = root / "source.mp4"
            source.write_bytes(b"fake-video")

            job_folder = store.queue_item_dir("pending", "video-1")
            copied = store.copy_video_variant(source, job_folder)
            copied_yt = store.copy_video_variant(source, job_folder, youtube_safe=True)

            self.assertEqual(job_folder, root / "videos" / "pending" / "video-1")
            self.assertEqual(copied.name, "video.mp4")
            self.assertEqual(copied_yt.name, "video_yt_safe.mp4")
            self.assertEqual(store.checksum(source), store.checksum(copied))

    def test_validate_metadata_requires_legacy_core_fields(self) -> None:
        with self.assertRaises(ValidationError):
            validate_upload_metadata_dict({"id": "video-1"})


if __name__ == "__main__":
    unittest.main()
