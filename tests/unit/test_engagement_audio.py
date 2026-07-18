import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

from autovideo.audio import ClipAudioDecision, build_audio_mix_report, clip_audio_filter
from autovideo.config.audio import clip_audio_config_from_env
from autovideo.engagement import generate_pinned_comment


class EngagementAudioTests(unittest.TestCase):
    def test_clip_audio_config_reads_environment_defaults(self) -> None:
        config = clip_audio_config_from_env({
            "AUTO_VIDEO_USE_CLIP_AUDIO": "true",
            "AUTO_VIDEO_CLIP_AUDIO_VOLUME": "0.35",
            "AUTO_VIDEO_CLIP_AUDIO_DUCKING": "false",
            "AUTO_VIDEO_CLIP_AUDIO_FADE_MS": "400",
            "AUTO_VIDEO_CLIP_AUDIO_NOISE_GATE": "true",
        })

        self.assertTrue(config.use_clip_audio)
        self.assertEqual(config.volume, 0.35)
        self.assertFalse(config.ducking)
        self.assertEqual(config.fade_ms, 400)
        self.assertTrue(config.noise_gate)

    def test_clip_audio_is_disabled_by_default_to_protect_narration(self) -> None:
        config = clip_audio_config_from_env({})

        self.assertFalse(config.use_clip_audio)

    def test_clip_audio_filter_includes_ducking_when_enabled(self) -> None:
        config = clip_audio_config_from_env({
            "AUTO_VIDEO_CLIP_AUDIO_VOLUME": "0.25",
            "AUTO_VIDEO_CLIP_AUDIO_DUCKING": "true",
        })

        graph, label = clip_audio_filter(
            source_audio_label="[0:a]",
            voice_audio_label="[1:a]",
            duration_sec=5.0,
            config=config,
        )

        self.assertEqual(label, "[aout]")
        self.assertIn("sidechaincompress", graph)
        self.assertIn("volume=0.2500", graph)
        self.assertIn("amix=inputs=2", graph)

    def test_audio_mix_report_summarizes_segment_decisions(self) -> None:
        report = build_audio_mix_report([
            ClipAudioDecision(
                segment_index=0,
                source_path="clip.mp4",
                clip_audio_extracted=True,
                clip_audio_used=True,
                clip_audio_muted=False,
                reason="audio stream passed quality gate",
                volume=0.3,
                ducking_applied=True,
                fade_ms=250,
            )
        ], music_volume=0.12)

        self.assertTrue(report["clip_audio_extracted"])
        self.assertTrue(report["clip_audio_used"])
        self.assertFalse(report["clip_audio_muted"])
        self.assertEqual(report["music_volume"], 0.12)
        self.assertEqual(report["segments"][0]["segment_index"], 0)

    def test_pinned_comment_is_topic_specific_and_discussion_oriented(self) -> None:
        comment = generate_pinned_comment(
            topic="The Octopus That Becomes Anything Underwater",
            title="The Octopus Master",
            segments=[{"narration": "Octopuses change color to hide."}],
            allow_emojis=False,
        )

        self.assertIn("animal", comment.lower())
        self.assertIn("should", comment.lower())
        self.assertIn("comment", comment.lower())

    def test_uploader_engagement_report_is_best_effort(self) -> None:
        import uploader

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "youtube_engagement_report.json"
            with patch.object(uploader, "ENGAGEMENT_REPORT_PATH", report_path), \
                    patch.object(uploader, "OUT_DIR", Path(tmp)), \
                    patch("yt_data_api.post_pinned_comment_via_api", return_value={
                        "status": "ok",
                        "comment_id": "comment-1",
                        "pin_success": False,
                        "retry_attempts": 1,
                        "pin_error": "unsupported",
                    }):
                result = uploader._post_upload_engagement(
                    {"status": "ok", "video_id": "abc123"},
                    {"title": "Octopus", "pinned_comment": "What animal should I cover next?"},
                )

            self.assertEqual(result["comment_id"], "comment-1")
            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["comment_id"], "comment-1")
            self.assertFalse(payload["pin_success"])


if __name__ == "__main__":
    unittest.main()
