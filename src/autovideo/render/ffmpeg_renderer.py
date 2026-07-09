"""FFmpeg renderer implementation backed by the legacy rendering functions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from autovideo.domain import MasteredVideo, Timeline

from .base import RenderResult, Renderer
from .legacy_adapter import LegacyRendererAdapter
from .profiles import RenderProfile
from .validation import RendererValidator


@dataclass(frozen=True)
class FfmpegRenderServices:
    """Injected legacy functions used by the FFmpeg renderer."""

    build_segment: Callable[..., Path]
    concat_segments: Callable[[list[Path]], Path]
    media_duration: Callable[[Path], float]
    build_ass: Callable[..., Path]
    burn_captions: Callable[[], Path]
    add_background_music: Callable[..., tuple[Path, str | Path | None]]
    run_ff: Callable[[list[Any]], str]
    move_file: Callable[[str, str], Any] = shutil.move
    print_status: Callable[[str], None] = print


class FfmpegTimelineRenderer(Renderer):
    """Render Timeline objects through the current FFmpeg pipeline."""

    def __init__(
        self,
        *,
        out_dir: Path,
        profile: RenderProfile,
        services: FfmpegRenderServices,
        adapter_factory: Callable[[Timeline], LegacyRendererAdapter] = LegacyRendererAdapter,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.profile = profile
        self.services = services
        self.adapter_factory = adapter_factory
        self.validator = RendererValidator(profile)

    def validate(self, timeline: Timeline) -> None:
        self.validator.validate(timeline)

    def render_master(self, timeline: Timeline) -> MasteredVideo:
        return self.render(timeline).mastered_video

    def render(self, timeline: Timeline) -> RenderResult:
        self.validate(timeline)
        adapter = self.adapter_factory(timeline)
        seg_paths: list[Path] = []
        meta = adapter.caption_meta()

        for render_item in adapter.render_items():
            self.services.print_status(
                f"[4/5] Segment {render_item.index+1}: assembling ({render_item.duration_sec:.1f}s)..."
            )
            seg_paths.append(self.services.build_segment(
                render_item.index,
                render_item.media_path,
                render_item.voice_path,
                render_item.duration_sec,
                compare_pair=render_item.compare_pair,
            ))

        self.services.print_status("[5/5] Stitching + burning captions...")
        self.services.concat_segments(seg_paths)
        combined_path = self.out_dir / "combined.mp4"
        try:
            combined_dur = self.services.media_duration(combined_path)
        except (OSError, RuntimeError):
            combined_dur = sum(duration for _, duration in meta)

        if combined_dur > self.profile.shorts_max_duration_sec:
            self.services.print_status(
                f"[!] Combined video is {combined_dur:.1f}s - trimming to "
                f"{self.profile.shorts_max_duration_sec}s before captions."
            )
            trimmed = self.out_dir / "combined_trimmed.mp4"
            self.services.run_ff([
                "ffmpeg", "-y", "-i", str(combined_path),
                "-t", f"{self.profile.shorts_max_duration_sec:.3f}",
                "-c", "copy",
                str(trimmed),
            ])
            trimmed.replace(combined_path)
            combined_dur = self.profile.shorts_max_duration_sec

        self.services.build_ass(meta, video_duration=combined_dur)
        captioned = self.services.burn_captions()

        try:
            captioned_duration = self.services.media_duration(captioned)
        except (OSError, RuntimeError):
            captioned_duration = sum(duration for _, duration in meta)

        final, music_used = self.services.add_background_music(
            captioned,
            captioned_duration,
            timeline.metadata.get("music_mood"),
            music_path=timeline.metadata.get("requested_music_path") or None,
            music_volume=float(timeline.metadata.get("requested_music_volume", self.profile.music_volume)),
            selection_key=str(timeline.metadata.get("music_selection_key") or ""),
        )

        try:
            total = self.services.media_duration(final)
        except (OSError, RuntimeError):
            total = captioned_duration

        final_yt_safe = self.out_dir / "final_yt_safe.mp4"
        self.services.print_status(f"[i] Producing YT-safe variant (no music) -> {final_yt_safe.name}")
        self.services.run_ff([
            "ffmpeg", "-y", "-i", str(captioned),
            "-c:v", self.profile.video_codec,
            "-preset", self.profile.video_preset,
            "-pix_fmt", self.profile.pixel_format,
            "-c:a", self.profile.audio_codec,
            "-b:a", self.profile.audio_bitrate,
            "-ar", self.profile.audio_sample_rate,
            "-movflags", self.profile.movflags,
            str(final_yt_safe),
        ])

        new_total = self._trim_if_long(final)
        if new_total is not None:
            total = new_total
        self._trim_if_long(final_yt_safe)
        try:
            yt_total = self.services.media_duration(final_yt_safe)
        except (OSError, RuntimeError):
            yt_total = total
        self.services.print_status(f"    Shorts-safe durations: IG/FB={total:.1f}s  YT={yt_total:.1f}s")

        mastered_video = MasteredVideo(
            video_path=final,
            duration_sec=total,
            format_profile=timeline.format_profile,
            music_included=bool(music_used),
            platform_variants={"youtube_safe": final_yt_safe},
        )
        return RenderResult(
            mastered_video=mastered_video,
            final_path=final,
            youtube_safe_path=final_yt_safe,
            captioned_path=captioned,
            combined_path=combined_path,
            final_duration_sec=total,
            youtube_safe_duration_sec=yt_total,
            music_path=music_used,
            segment_paths=seg_paths,
            metadata={"renderer": "ffmpeg", "render_profile": self.profile.name},
        )

    def _trim_if_long(self, path: Path) -> float | None:
        try:
            dur = self.services.media_duration(path)
        except (OSError, RuntimeError):
            return None
        if dur <= self.profile.shorts_max_duration_sec:
            return dur
        self.services.print_status(
            f"[!] {path.name} is {dur:.1f}s - exceeds {self.profile.shorts_max_duration_sec}s; trimming."
        )
        trimmed = path.with_name(path.stem + "_trimmed.mp4")
        self.services.run_ff([
            "ffmpeg", "-y", "-i", str(path),
            "-t", f"{self.profile.shorts_max_duration_sec:.3f}",
            "-c:v", self.profile.video_codec,
            "-preset", self.profile.video_preset,
            "-pix_fmt", self.profile.pixel_format,
            "-c:a", self.profile.audio_codec,
            "-b:a", self.profile.audio_bitrate,
            "-ar", self.profile.audio_sample_rate,
            "-movflags", self.profile.movflags,
            str(trimmed),
        ])
        self.services.move_file(str(trimmed), str(path))
        try:
            return self.services.media_duration(path)
        except (OSError, RuntimeError):
            return self.profile.shorts_max_duration_sec
