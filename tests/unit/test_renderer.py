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
    TimelineBuildOptions,
    VoiceTrack,
    build_timeline,
)
from autovideo.domain.errors import RendererValidationError
from autovideo.render import (
    FfmpegRenderServices,
    FfmpegTimelineRenderer,
    LegacyRendererAdapter,
    Renderer,
    RendererValidator,
    render_profile_for,
)


class RendererTests(unittest.TestCase):
    def test_ffmpeg_renderer_uses_timeline_and_preserves_legacy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeline = self._sample_timeline(root)
            services = _FakeRenderServices(root)
            renderer = FfmpegTimelineRenderer(
                out_dir=root,
                profile=render_profile_for("production", music_volume=0.12),
                services=services.services(),
            )

            result = renderer.render(timeline)

            self.assertIsInstance(renderer, Renderer)
            self.assertEqual(result.mastered_video.video_path, root / "final.mp4")
            self.assertEqual(result.youtube_safe_path, root / "final_yt_safe.mp4")
            self.assertEqual(result.captioned_path, root / "captioned.mp4")
            self.assertEqual(result.combined_path, root / "combined.mp4")
            self.assertEqual(result.mastered_video.format_profile, "shorts_vertical")
            self.assertEqual(result.mastered_video.platform_variants["youtube_safe"], root / "final_yt_safe.mp4")
            self.assertEqual(services.segment_calls[0]["compare_pair"], None)
            self.assertEqual(services.music_call["mood"], "curious")
            self.assertEqual(services.music_call["music_volume"], 0.12)
            self.assertEqual(services.music_call["selection_key"], "Renderer Test|renderer")
            self.assertIn(root / "final_yt_safe.mp4", services.run_ff_outputs)

    def test_render_master_returns_mastered_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renderer = FfmpegTimelineRenderer(
                out_dir=root,
                profile=render_profile_for("production"),
                services=_FakeRenderServices(root).services(),
            )

            mastered = renderer.render_master(self._sample_timeline(root))

            self.assertEqual(mastered.video_path, root / "final.mp4")
            self.assertTrue(mastered.music_included)

    def test_renderer_validator_rejects_missing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeline = self._sample_timeline(root)
            timeline.assets["media-0-broll_0"].local_path.unlink()

            with self.assertRaises(RendererValidationError):
                RendererValidator(render_profile_for("production")).validate(timeline)

    def test_renderer_validator_rejects_profile_dimension_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeline = self._sample_timeline(root)

            with self.assertRaises(RendererValidationError):
                RendererValidator(render_profile_for("production", width=1920, height=1080)).validate(timeline)

    def test_render_profiles_load_known_environments_with_legacy_defaults(self) -> None:
        for name in ("development", "production", "testing"):
            profile = render_profile_for(name)
            self.assertEqual(profile.name, name)
            self.assertEqual(profile.video_codec, "libx264")
            self.assertEqual(profile.video_preset, "veryfast")
            self.assertEqual(profile.pixel_format, "yuv420p")
            self.assertEqual(profile.audio_codec, "aac")

    def test_legacy_adapter_still_matches_renderer_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeline = self._sample_timeline(root)

            items = LegacyRendererAdapter(timeline).render_items()

            self.assertEqual(items[0].voice_path, root / "voice_0.mp3")
            self.assertEqual(items[0].media_path, root / "broll_0.mp4")
            self.assertEqual(items[1].duration_sec, 5.0)

    def _sample_timeline(self, root: Path):
        broll_0 = root / "broll_0.mp4"
        broll_1 = root / "broll_1.mp4"
        voice_0 = root / "voice_0.mp3"
        voice_1 = root / "voice_1.mp3"
        for path in (broll_0, broll_1, voice_0, voice_1):
            path.write_bytes(b"fixture")

        script = Script(
            title="Renderer Test",
            music_mood="curious",
            niche="renderer",
            segments=[
                ScriptSegment(
                    narration="A sharp hook opens the renderer test.",
                    broll="broll one",
                    broll_queries=["broll one"],
                ),
                ScriptSegment(
                    narration="The second beat proves compatibility.",
                    broll="broll two",
                    broll_queries=["broll two"],
                ),
            ],
        )
        timeline = build_timeline(
            script=script,
            voice_tracks=[
                VoiceTrack(audio_path=voice_0, duration_sec=4.0, provider="mock", scene_id="0"),
                VoiceTrack(audio_path=voice_1, duration_sec=5.0, provider="mock", scene_id="1"),
            ],
            media_assets=[
                MediaAsset(local_path=broll_0, source=MediaSource.LOCAL),
                MediaAsset(local_path=broll_1, source=MediaSource.LOCAL),
            ],
            options=TimelineBuildOptions(width=1080, height=1920, fps=30, check_asset_files=True),
        )
        timeline.metadata["requested_music_volume"] = 0.12
        timeline.metadata["music_selection_key"] = "Renderer Test|renderer"
        return timeline


class _FakeRenderServices:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.segment_calls: list[dict[str, object]] = []
        self.run_ff_outputs: list[Path] = []
        self.music_call: dict[str, object] = {}

    def services(self) -> FfmpegRenderServices:
        return FfmpegRenderServices(
            build_segment=self.build_segment,
            concat_segments=self.concat_segments,
            media_duration=self.media_duration,
            build_ass=self.build_ass,
            burn_captions=self.burn_captions,
            add_background_music=self.add_background_music,
            run_ff=self.run_ff,
            move_file=self.move_file,
            print_status=lambda _message: None,
        )

    def build_segment(self, idx, broll, voice, duration, compare_pair=None):
        self.segment_calls.append({
            "idx": idx,
            "broll": broll,
            "voice": voice,
            "duration": duration,
            "compare_pair": compare_pair,
        })
        path = self.root / f"seg_{idx}.mp4"
        path.write_bytes(b"segment")
        return path

    def concat_segments(self, seg_paths):
        combined = self.root / "combined.mp4"
        combined.write_bytes(b"combined")
        return combined

    def media_duration(self, path):
        name = Path(path).name
        if name == "combined.mp4":
            return 8.78
        if name == "captioned.mp4":
            return 8.78
        if name in {"final.mp4", "final_yt_safe.mp4"}:
            return 8.78
        return 4.0

    def build_ass(self, meta, video_duration=None):
        captions = self.root / "captions.ass"
        captions.write_text(str(meta), encoding="utf-8")
        return captions

    def burn_captions(self):
        captioned = self.root / "captioned.mp4"
        captioned.write_bytes(b"captioned")
        return captioned

    def add_background_music(
        self,
        video_path,
        duration,
        mood,
        music_path=None,
        music_volume=0.0,
        selection_key="",
    ):
        self.music_call = {
            "video_path": video_path,
            "duration": duration,
            "mood": mood,
            "music_path": music_path,
            "music_volume": music_volume,
            "selection_key": selection_key,
        }
        final = self.root / "final.mp4"
        final.write_bytes(b"final")
        return final, "generated"

    def run_ff(self, args):
        output = Path(args[-1])
        output.write_bytes(b"rendered")
        self.run_ff_outputs.append(output)
        return ""

    def move_file(self, source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        Path(source).unlink()


if __name__ == "__main__":
    unittest.main()
