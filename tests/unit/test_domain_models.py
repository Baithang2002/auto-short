from __future__ import annotations

import unittest
import datetime as dt
from pathlib import Path

from tests.unit import _path  # noqa: F401
from autovideo.domain import (
    Asset,
    AssetStatus,
    AssetType,
    MasteredVideo,
    MediaAsset,
    MediaSource,
    PublishResult,
    PublishStatus,
    PublishTarget,
    Scene,
    SceneType,
    Script,
    ScriptSegment,
    Timeline,
    TimelineItem,
    TrackType,
    UploadMetadata,
    VisualPlan,
    VoiceTrack,
)
from autovideo.domain.errors import ValidationError


class DomainModelTests(unittest.TestCase):
    def test_asset_round_trip(self) -> None:
        asset = Asset(
            id="asset-1",
            asset_type=AssetType.VIDEO,
            provider="pexels",
            local_path=Path("output/broll.mp4"),
            duration_sec=6.5,
            status=AssetStatus.AVAILABLE,
        )

        restored = Asset.from_dict(asset.to_dict())

        self.assertEqual(restored.id, asset.id)
        self.assertEqual(restored.asset_type, AssetType.VIDEO)
        self.assertEqual(restored.local_path, Path("output/broll.mp4"))
        self.assertEqual(restored.status, AssetStatus.AVAILABLE)

    def test_scene_keeps_visual_plan(self) -> None:
        scene = Scene(
            id="scene-1",
            index=0,
            narration="A short narration.",
            scene_type=SceneType.STOCK_VIDEO,
            visual=VisualPlan(
                visual_type=SceneType.STOCK_VIDEO,
                queries=["deep ocean", "underwater life"],
            ),
        )

        restored = Scene.from_dict(scene.to_dict())

        self.assertEqual(restored.visual.queries, ["deep ocean", "underwater life"])
        self.assertEqual(restored.visual.visual_type, SceneType.STOCK_VIDEO)

    def test_timeline_duration_uses_latest_item_end(self) -> None:
        timeline = Timeline(
            id="timeline-1",
            episode_id="episode-1",
            width=1080,
            height=1920,
            fps=30,
            items=[
                TimelineItem("v1", TrackType.VIDEO, "broll", 0.0, 4.0),
                TimelineItem("a1", TrackType.AUDIO, "voice", 0.0, 5.25),
            ],
        )

        self.assertEqual(timeline.duration_sec, 5.25)

    def test_timeline_round_trip(self) -> None:
        timeline = Timeline(
            id="timeline-1",
            episode_id="episode-1",
            width=1080,
            height=1920,
            fps=30,
            items=[
                TimelineItem(
                    "v1",
                    TrackType.VIDEO,
                    "broll",
                    0.0,
                    4.0,
                    asset_id="asset-1",
                    scene_id="scene-1",
                    properties={"fit": "cover"},
                ),
            ],
            chapters=[{"title": "Hook", "start_sec": 0.0}],
        )

        restored = Timeline.from_dict(timeline.to_dict())

        self.assertEqual(restored.items[0].track_type, TrackType.VIDEO)
        self.assertEqual(restored.items[0].properties["fit"], "cover")
        self.assertEqual(restored.chapters[0]["title"], "Hook")

    def test_script_preserves_legacy_fields(self) -> None:
        legacy = {
            "title": "Ocean Mystery",
            "description": "A short story.",
            "hashtags": "#ocean #shorts",
            "music_mood": "mysterious",
            "segments": [
                {
                    "narration": "A hidden wave moves under the sea.",
                    "broll": "ocean waves",
                    "broll_queries": "deep ocean waves",
                    "legacy_segment_score": 0.9,
                }
            ],
            "legacy_script_id": "abc",
        }

        script = Script.from_legacy_dict(legacy, niche="ocean facts")
        restored = script.to_legacy_dict()

        self.assertEqual(script.segments[0].broll_queries, ["deep ocean waves"])
        self.assertEqual(restored["legacy_script_id"], "abc")
        self.assertEqual(restored["segments"][0]["legacy_segment_score"], 0.9)
        self.assertEqual(restored["niche"], "ocean facts")

    def test_script_validation_rejects_missing_boundary_data(self) -> None:
        with self.assertRaises(ValidationError):
            Script.from_legacy_dict({"title": "Missing segments", "segments": []})
        with self.assertRaises(ValidationError):
            ScriptSegment.from_legacy_dict({"narration": "", "broll": "forest"})

    def test_voice_track_round_trip_and_legacy_item(self) -> None:
        track = VoiceTrack(
            audio_path=Path("output/voice_0.mp3"),
            duration_sec=4.25,
            provider="edge_tts",
            scene_id="0",
            metadata={"unit": "scene"},
        )

        restored = VoiceTrack.from_dict(track.to_dict())
        retimed = restored.with_retimed_audio(Path("output/voice_0_retimed.mp3"), 5.0)
        legacy = retimed.to_legacy_item(index=0, segment={"narration": "Line"})

        self.assertEqual(restored.provider, "edge_tts")
        self.assertTrue(retimed.retimed)
        self.assertEqual(legacy["voice"], Path("output/voice_0_retimed.mp3"))
        self.assertIs(legacy["voice_track"], retimed)

    def test_media_asset_round_trip(self) -> None:
        asset = MediaAsset(
            local_path=Path("output/broll_0.mp4"),
            source=MediaSource.PEXELS,
            source_id="123",
            duration_sec=8.0,
            dimensions=(1080, 1920),
            attribution={"author": "creator"},
        )

        restored = MediaAsset.from_dict(asset.to_dict())

        self.assertEqual(restored.local_path, Path("output/broll_0.mp4"))
        self.assertEqual(restored.source, MediaSource.PEXELS)
        self.assertEqual(restored.dimensions, (1080, 1920))
        self.assertEqual(MediaAsset.from_dict({"local_path": "x.mp4", "source": "future"}).source, MediaSource.UNKNOWN)

    def test_mastered_video_round_trip(self) -> None:
        mastered = MasteredVideo(
            video_path=Path("output/final.mp4"),
            duration_sec=57.8,
            format_profile="shorts_vertical",
            music_included=True,
            platform_variants={"youtube_safe": Path("output/final_yt_safe.mp4")},
        )

        restored = MasteredVideo.from_dict(mastered.to_dict())

        self.assertEqual(restored.video_path, Path("output/final.mp4"))
        self.assertEqual(restored.platform_variants["youtube_safe"], Path("output/final_yt_safe.mp4"))
        self.assertTrue(restored.music_included)

    def test_publish_artifacts_round_trip(self) -> None:
        target = PublishTarget(platform="youtube", credentials_ref="youtube-default")
        attempted_at = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.timezone.utc)
        result = PublishResult(
            status=PublishStatus.OK,
            platform=target.platform,
            url="https://youtube.example/watch?v=abc",
            platform_id="abc",
            attempted_at=attempted_at,
        )

        restored_target = PublishTarget.from_dict(target.to_dict())
        restored_result = PublishResult.from_dict(result.to_dict())

        self.assertEqual(restored_target.platform, "youtube")
        self.assertEqual(restored_result.status, PublishStatus.OK)
        self.assertEqual(restored_result.attempted_at, attempted_at)

    def test_upload_metadata_preserves_unknown_legacy_fields(self) -> None:
        legacy = {
            "id": "video-1",
            "title": "Title",
            "video_path": "videos/pending/video-1/video.mp4",
            "status": "pending",
            "custom_legacy_field": "kept",
        }

        metadata = UploadMetadata.from_legacy_dict(legacy)
        restored = metadata.to_legacy_dict()

        self.assertEqual(restored["custom_legacy_field"], "kept")
        self.assertEqual(Path(restored["video_path"]), Path("videos/pending/video-1/video.mp4"))


if __name__ == "__main__":
    unittest.main()
