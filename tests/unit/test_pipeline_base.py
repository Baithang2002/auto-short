"""Tests for shared pipeline CLI pass-through behavior."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import _pipeline_base as pipeline_base
from _pipeline_base import build_stage1_extra, build_standard_parser


class PipelineBaseTests(unittest.TestCase):
    def test_reuse_script_flag_is_forwarded_to_generator(self) -> None:
        parser = build_standard_parser()
        args = parser.parse_args(["Topic", "--reuse-script", "--no-interactive"])

        extra = build_stage1_extra(args)

        self.assertIn("--reuse-script", extra)
        self.assertIn("--no-interactive", extra)

    def test_quality_deferred_upload_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            meta = {
                "video_path": str(video),
                "title": "Deferred video",
                "youtube_title": "Deferred video #shorts",
                "hashtags": ["#shorts"],
            }

            with patch.object(sys, "argv", ["pipeline.py", "Topic"]), \
                 patch.object(pipeline_base, "run_stage1", return_value=meta), \
                 patch.object(
                     pipeline_base,
                     "upload_allowed_from_report",
                     return_value=(False, "publish quality verdict DEFERRED"),
                 ), \
                 patch.object(pipeline_base, "run_stage2") as run_stage2, \
                 patch.dict(
                     pipeline_base.os.environ,
                     {"AUTO_VIDEO_ENFORCE_PUBLISH_QUALITY_GATE": "true"},
                     clear=False,
                 ):
                with self.assertRaises(SystemExit) as raised:
                    pipeline_base.main("auto_short.py")

        self.assertEqual(raised.exception.code, 1)
        run_stage2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
