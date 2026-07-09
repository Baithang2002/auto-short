from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401

from autovideo.domain import (
    MediaAsset,
    MediaSource,
    Script,
    ScriptSegment,
    Timeline,
    TimelineBuildOptions,
    TimelineValidator,
    VoiceTrack,
    build_timeline,
)
from autovideo.domain.errors import TimelineValidationError, ValidationError
from autovideo.render import LegacyRendererAdapter


class TimelineBuilderTests(unittest.TestCase):
    def test_build_timeline_constructs_tracks_and_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, voices, media = self._sample_inputs(Path(tmp))

            timeline = build_timeline(
                script=script,
                voice_tracks=voices,
                media_assets=media,
                options=TimelineBuildOptions(
                    width=1080,
                    height=1920,
                    fps=30,
                    transition_duration_sec=0.22,
                ),
            )

            self.assertEqual(len(timeline.scenes), 2)
            self.assertEqual(len(timeline.tracks), 3)
            self.assertEqual(timeline.scenes[1].start_sec, 4.0)
            self.assertEqual(timeline.transitions[0]["duration_sec"], 0.22)
            self.assertEqual(timeline.captions[0].text, "A sharp hook opens the story.")

    def test_timeline_round_trip_preserves_all_information(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, voices, media = self._sample_inputs(Path(tmp))
            timeline = build_timeline(
                script=script,
                voice_tracks=voices,
                media_assets=media,
                options=TimelineBuildOptions(check_asset_files=True),
            )

            restored = Timeline.from_json(timeline.to_json())

            self.assertEqual(restored.to_dict(), timeline.to_dict())
            self.assertEqual(restored.assets["media-0-broll_0"].source, MediaSource.LOCAL)

    def test_validator_rejects_missing_assets_and_bad_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, voices, media = self._sample_inputs(Path(tmp))

            missing = MediaAsset(local_path=Path(tmp) / "missing.mp4", source=MediaSource.LOCAL)
            with self.assertRaises(TimelineValidationError):
                build_timeline(
                    script=script,
                    voice_tracks=voices,
                    media_assets=[missing, media[1]],
                )

            timeline = build_timeline(script=script, voice_tracks=voices, media_assets=media)
            bad_caption = timeline.captions[1].__class__(
                id="caption-bad",
                start_sec=1.0,
                end_sec=1.5,
                text="overlap",
            )
            bad_payload = timeline.to_dict()
            bad_payload["captions"] = [timeline.captions[0].to_dict(), bad_caption.to_dict()]
            bad_timeline = Timeline.from_dict(bad_payload)
            with self.assertRaises(TimelineValidationError):
                TimelineValidator().validate(bad_timeline, check_asset_files=True)

    def test_builder_rejects_unaligned_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, voices, media = self._sample_inputs(Path(tmp))

            with self.assertRaises(ValidationError):
                build_timeline(script=script, voice_tracks=voices[:1], media_assets=media)

    def test_legacy_adapter_matches_existing_renderer_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, voices, media = self._sample_inputs(Path(tmp))
            media[0] = MediaAsset(
                local_path=media[0].local_path,
                source=MediaSource.LOCAL,
                metadata={"compare_pair": [str(media[0].local_path), str(media[1].local_path)]},
            )
            timeline = build_timeline(script=script, voice_tracks=voices, media_assets=media)

            adapter = LegacyRendererAdapter(timeline)
            render_items = adapter.render_items()

            self.assertEqual(render_items[0].voice_path, voices[0].audio_path)
            self.assertEqual(render_items[0].duration_sec, voices[0].duration_sec)
            self.assertEqual(render_items[0].media_path, media[0].local_path)
            self.assertEqual(adapter.caption_meta()[1], ("The second beat pays it off.", 5.0))
            self.assertEqual(render_items[0].compare_pair, (media[0].local_path, media[1].local_path))

    def _sample_inputs(
        self,
        root: Path,
    ) -> tuple[Script, list[VoiceTrack], list[MediaAsset]]:
        broll_0 = root / "broll_0.mp4"
        broll_1 = root / "broll_1.mp4"
        voice_0 = root / "voice_0.mp3"
        voice_1 = root / "voice_1.mp3"
        for path in (broll_0, broll_1, voice_0, voice_1):
            path.write_bytes(b"fixture")

        script = Script(
            title="Test Timeline",
            music_mood="curious",
            niche="timeline tests",
            segments=[
                ScriptSegment(
                    narration="A sharp hook opens the story.",
                    broll="broll one",
                    broll_queries=["broll one close up"],
                ),
                ScriptSegment(
                    narration="The second beat pays it off.",
                    broll="broll two",
                    broll_queries=["broll two wide"],
                ),
            ],
        )
        voices = [
            VoiceTrack(audio_path=voice_0, duration_sec=4.0, provider="mock", scene_id="0"),
            VoiceTrack(audio_path=voice_1, duration_sec=5.0, provider="mock", scene_id="1"),
        ]
        media = [
            MediaAsset(local_path=broll_0, source=MediaSource.LOCAL),
            MediaAsset(local_path=broll_1, source=MediaSource.LOCAL),
        ]
        return script, voices, media


if __name__ == "__main__":
    unittest.main()
