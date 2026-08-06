"""Unit tests for YouTubeClipProvider and yt_clip fallback integration."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit._path import SRC  # noqa: F401
from autovideo.media.planning import default_provider_capability_registry
from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.stock import yt_clip
from autovideo.providers.stock.yt_clip import (
    YouTubeClipProvider,
    _yt_clip_json_object,
    _yt_clip_vision_verifier_for,
    fetch_yt_clip,
    is_yt_dlp_available,
)


def _fake_search_result(entries):
    return json.dumps({"entries": entries})


def _make_subprocess_fake(output_dir, entries):
    """Fake subprocess.run that emulates yt-dlp search/download and ffmpeg."""

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd)
        if "--dump-single-json" in argv:
            return MagicMock(
                returncode=0,
                stdout=_fake_search_result(entries),
                stderr="",
            )
        if "-o" in argv:
            out_path = Path(argv[argv.index("-o") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake-media-bytes")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "-frames:v" in argv:
            frame_path = Path(argv[-1])
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_bytes(b"fake-frame")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "-t" in argv and "libx264" in argv:
            (output_dir / "broll_0.mp4").write_bytes(b"fake-broll")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "-show_entries" in argv:
            return MagicMock(returncode=0, stdout="10.0", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return fake_run


class TestYouTubeClipProvider(unittest.TestCase):

    def test_yt_clip_provider_capability_registration(self):
        registry = default_provider_capability_registry(yt_clip_enabled=True)
        yt_cap = registry.get("yt_clip")
        self.assertIsNotNone(yt_cap)
        self.assertEqual(yt_cap.provider_id, "yt_clip")
        self.assertEqual(yt_cap.base_priority, 0)
        self.assertEqual(yt_cap.confidence, 0.7)
        self.assertIn("wildlife_video", yt_cap.capabilities)

    def test_is_yt_dlp_available(self):
        with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
            self.assertTrue(is_yt_dlp_available())

    def test_yt_clip_provider_disabled(self):
        provider = YouTubeClipProvider(enabled=False)
        query = MagicMock(queries=["test query"], target_duration_sec=3.0)
        with self.assertRaises(ProviderUnavailableError):
            provider.fetch(query, Path("/tmp"))

    def test_yt_clip_json_object(self):
        self.assertIsNone(_yt_clip_json_object("not json"))
        self.assertIsNone(_yt_clip_json_object("{}"))
        parsed = _yt_clip_json_object(
            '{"match": true, "confidence": 0.95, "brief_reasoning": "clear wolf"}'
        )
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed["match"])
        fenced = _yt_clip_json_object(
            '```json\n{"match": false, "confidence": 0.1}\n```'
        )
        self.assertIsNotNone(fenced)
        self.assertFalse(fenced["match"])

    def test_yt_clip_vision_verifier_disabled_without_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertIsNone(_yt_clip_vision_verifier_for("wolf"))

    def test_yt_clip_vision_verifier_disabled_by_flag(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}, clear=False):
            os.environ["AUTO_VIDEO_YT_CLIP_VERIFY"] = "0"
            self.assertIsNone(_yt_clip_vision_verifier_for("wolf"))
            os.environ.pop("AUTO_VIDEO_YT_CLIP_VERIFY", None)

    def test_fetch_yt_clip_skips_unverified_candidate_and_accepts_verified(self):
        entries = [
            {"id": "vid_dog", "webpage_url": "https://www.youtube.com/watch?v=vid_dog", "title": "dog"},
            {"id": "vid_wolf", "webpage_url": "https://www.youtube.com/watch?v=vid_wolf", "title": "wolf"},
        ]
        verifier_calls = []

        def fake_verifier(entity, frames):
            verifier_calls.append((entity, frames))
            if len(verifier_calls) == 1:
                return (False, 0.95)
            return (True, 0.95)

        with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("autovideo.providers.stock.yt_clip.subprocess.run",
                   side_effect=_make_subprocess_fake(Path("."), entries)), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_vision_verifier_for",
                   return_value=fake_verifier), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_extract_frames",
                   return_value=[Path("frame.jpg")]):
            out = fetch_yt_clip(["wolf running snow"], 0, Path("."), used_set=set())

        self.assertEqual(out, Path("broll_0.mp4"))
        self.assertEqual(len(verifier_calls), 2)
        self.assertEqual(verifier_calls[0][0], "wolf")
        self.assertEqual(out.read_bytes(), b"fake-broll")

    def test_fetch_yt_clip_returns_none_when_all_candidates_rejected(self):
        entries = [
            {"id": "vid_dog", "webpage_url": "https://www.youtube.com/watch?v=vid_dog", "title": "dog"},
        ]

        def fake_verifier(entity, frames):
            return (False, 0.99)

        with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("autovideo.providers.stock.yt_clip.subprocess.run",
                   side_effect=_make_subprocess_fake(Path("."), entries)), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_vision_verifier_for",
                   return_value=fake_verifier), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_extract_frames",
                   return_value=[Path("frame.jpg")]):
            out = fetch_yt_clip(["wolf running"], 0, Path("."), used_set=set())

        self.assertIsNone(out)

    def test_fetch_yt_clip_skips_candidate_when_vision_unavailable(self):
        entries = [
            {"id": "vid_a", "webpage_url": "https://www.youtube.com/watch?v=vid_a", "title": "a"},
            {"id": "vid_b", "webpage_url": "https://www.youtube.com/watch?v=vid_b", "title": "b"},
        ]

        def fake_verifier(entity, frames):
            return None

        with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("autovideo.providers.stock.yt_clip.subprocess.run",
                   side_effect=_make_subprocess_fake(Path("."), entries)), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_vision_verifier_for",
                   return_value=fake_verifier), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_extract_frames",
                   return_value=[Path("frame.jpg")]):
            out = fetch_yt_clip(["wolf running"], 0, Path("."), used_set=set())

        self.assertIsNone(out)

    def test_fetch_yt_clip_accepts_direct_source_url(self):
        def fake_verifier(entity, frames):
            return (True, 0.95)

        with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             patch("autovideo.providers.stock.yt_clip.subprocess.run",
                   side_effect=_make_subprocess_fake(Path("."), [])), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_vision_verifier_for",
                   return_value=fake_verifier), \
             patch("autovideo.providers.stock.yt_clip._yt_clip_extract_frames",
                   return_value=[Path("frame.jpg")]):
            out = fetch_yt_clip(
                ["wolf running"],
                0,
                Path("."),
                used_set=set(),
                source_url="https://www.youtube.com/watch?v=vid_wolf",
            )

        self.assertEqual(out, Path("broll_0.mp4"))

    def test_fetch_yt_clip_slices_local_source_and_preserves_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "gannet_source.mp4"
            source.write_bytes(b"source")
            commands = []

            def fake_run(cmd, *args, **kwargs):
                commands.append(list(cmd))
                out_path = Path(cmd[-1])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"sliced")
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
                 patch("autovideo.providers.stock.yt_clip.subprocess.run", side_effect=fake_run):
                out = fetch_yt_clip(
                    ["gannet diving"],
                    0,
                    root,
                    target_duration=6.0,
                    source_url=str(source),
                    segment_offset_sec=24.0,
                    preserve_audio=True,
                )

            self.assertEqual(out, root / "broll_0.mp4")
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertIn("-ss", command)
            self.assertIn("24.00", command)
            self.assertIn("-map", command)
            self.assertIn("0:a:0?", command)
            self.assertNotIn("-an", command)


if __name__ == "__main__":
    unittest.main()
