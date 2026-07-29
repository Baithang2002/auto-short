from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import uploader


class UploaderTests(unittest.TestCase):
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
