from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit import _path  # noqa: F401

import yt_data_api


class YouTubeDataApiTests(unittest.TestCase):
    def test_category_id_reaches_videos_insert_body(self) -> None:
        request = MagicMock()
        request.next_chunk.return_value = (None, {"id": "video123"})
        videos = MagicMock()
        videos.insert.return_value = request
        youtube = MagicMock()
        youtube.videos.return_value = videos
        build = MagicMock(return_value=youtube)

        discovery = types.ModuleType("googleapiclient.discovery")
        setattr(discovery, "build", build)
        http = types.ModuleType("googleapiclient.http")
        setattr(http, "MediaFileUpload", MagicMock(return_value=object()))
        errors = types.ModuleType("googleapiclient.errors")
        setattr(errors, "HttpError", type("HttpError", (Exception,), {}))
        package = types.ModuleType("googleapiclient")

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            with patch.object(yt_data_api, "_get_creds", return_value=object()), patch.dict(
                sys.modules,
                {
                    "googleapiclient": package,
                    "googleapiclient.discovery": discovery,
                    "googleapiclient.http": http,
                    "googleapiclient.errors": errors,
                },
            ):
                result = yt_data_api.upload_youtube_via_api(
                    video,
                    "Wildlife Short",
                    "Description #shorts",
                    category_id="15",
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(videos.insert.call_args.kwargs["body"]["snippet"]["categoryId"], "15")

    def test_invalid_category_uses_safe_default(self) -> None:
        self.assertEqual(yt_data_api._normalize_category_id("not-a-category"), "27")

    def test_comment_scope_failure_is_skipped_without_upload_failure(self) -> None:
        errors = types.ModuleType("googleapiclient.errors")
        setattr(errors, "HttpError", type("HttpError", (Exception,), {}))
        discovery = types.ModuleType("googleapiclient.discovery")
        youtube = MagicMock()
        youtube.commentThreads.return_value.insert.return_value.execute.side_effect = RuntimeError(
            "invalid_scope: Bad Request"
        )
        setattr(discovery, "build", MagicMock(return_value=youtube))
        package = types.ModuleType("googleapiclient")

        with patch.object(yt_data_api, "_get_comment_creds", return_value=object()), patch.dict(
            sys.modules,
            {
                "googleapiclient": package,
                "googleapiclient.discovery": discovery,
                "googleapiclient.errors": errors,
            },
        ):
            result = yt_data_api.post_pinned_comment_via_api(
                "video123",
                "What should I cover next?",
            )

        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["pin_success"])


if __name__ == "__main__":
    unittest.main()
