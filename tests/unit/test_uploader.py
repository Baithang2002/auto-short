from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import uploader


class UploaderTests(unittest.TestCase):
    def test_resolve_metadata_infers_topic_category_for_existing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "upload_metadata.json"
            metadata_path.write_text(json.dumps({
                "niche": "Arctic Fox Survival Tricks",
                "title": "Arctic Fox Survival Tricks",
                "youtube_title": "Arctic Fox Survival Tricks",
            }), encoding="utf-8")
            args = SimpleNamespace(title="", description="")

            with patch.object(uploader, "META_PATH", metadata_path):
                metadata = uploader._resolve_metadata(args)

        self.assertEqual(metadata["category_id"], "15")

    def test_resolve_metadata_infers_category_from_cli_title(self) -> None:
        args = SimpleNamespace(title="Snow Leopard Hunting #shorts", description="")

        with patch.object(uploader, "META_PATH", Path("missing-metadata.json")):
            metadata = uploader._resolve_metadata(args)

        self.assertEqual(metadata["category_id"], "15")

    def test_data_api_upload_receives_category_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            with patch("yt_data_api.is_api_available", return_value=True), \
                 patch(
                     "yt_data_api.upload_youtube_via_api",
                     return_value={"status": "error", "error": "test stop"},
                 ) as api_upload:
                uploader.upload_youtube(
                    None,
                    video,
                    "Wildlife title",
                    "Description #shorts",
                    category_id="15",
                )

        self.assertEqual(api_upload.call_args.kwargs["category_id"], "15")

    def test_main_exits_nonzero_when_requested_upload_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            metadata = {
                "title": "Failed upload",
                "youtube_title": "Failed upload #shorts",
            }

            with patch.object(sys, "argv", ["uploader.py", "--upload", str(video), "--platforms", "youtube"]), \
                 patch("subprocess.run", return_value=SimpleNamespace(stdout="55.0")), \
                 patch.object(uploader, "SESSION_DIR", Path(directory)), \
                 patch.object(uploader, "_resolve_metadata", return_value=metadata), \
                 patch.object(
                     uploader,
                     "run_uploads",
                     return_value={"youtube": {"status": "error", "error": "quota exceeded"}},
                 ), \
                 patch.object(uploader, "_append_log"):
                with self.assertRaises(SystemExit) as raised:
                    uploader.main()

        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
