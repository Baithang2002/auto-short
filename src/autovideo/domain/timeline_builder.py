"""Timeline construction and validation.

The builder is the canonical boundary between planning and rendering. It has no
provider or FFmpeg dependencies; it only turns validated domain artifacts into a
declarative timeline that the legacy renderer adapter can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from autovideo.domain.errors import TimelineValidationError, ValidationError
from autovideo.domain.media import MediaAsset
from autovideo.domain.metadata import UploadMetadata
from autovideo.domain.script import Script
from autovideo.domain.timeline import (
    CaptionEntry,
    Timeline,
    TimelineItem,
    TimelineScene,
    TimelineTrack,
    TrackType,
)
from autovideo.domain.voice import VoiceTrack


@dataclass(frozen=True)
class TimelineBuildOptions:
    """Render-independent options needed to construct a Shorts timeline."""

    width: int = 1080
    height: int = 1920
    fps: int = 30
    format_profile: str = "shorts_vertical"
    transition_duration_sec: float = 0.0
    episode_id: str = "legacy-auto-short"
    timeline_id: str = "timeline-legacy-auto-short"
    check_asset_files: bool = True


class TimelineValidator:
    """Validate timeline invariants before rendering."""

    def validate(self, timeline: Timeline, *, check_asset_files: bool = True) -> None:
        """Raise :class:`TimelineValidationError` when the timeline is invalid."""

        if timeline.width <= 0 or timeline.height <= 0:
            raise TimelineValidationError("Timeline dimensions must be positive")
        if timeline.fps <= 0:
            raise TimelineValidationError("Timeline fps must be positive")
        if not timeline.scenes:
            raise TimelineValidationError("Timeline must contain at least one scene")
        if not timeline.tracks:
            raise TimelineValidationError("Timeline must contain at least one track")

        self._validate_assets(timeline, check_asset_files=check_asset_files)
        self._validate_scenes(timeline)
        self._validate_tracks(timeline)
        self._validate_captions(timeline)

    def _validate_assets(self, timeline: Timeline, *, check_asset_files: bool) -> None:
        for asset_id, asset in timeline.assets.items():
            if not asset_id:
                raise TimelineValidationError("Timeline asset id cannot be empty")
            if check_asset_files and not asset.local_path.exists():
                raise TimelineValidationError(f"Timeline asset is missing: {asset.local_path}")

        for scene in timeline.scenes:
            if scene.media_asset_id not in timeline.assets:
                raise TimelineValidationError(
                    f"Scene {scene.id} references missing media asset {scene.media_asset_id}"
                )

    def _validate_scenes(self, timeline: Timeline) -> None:
        previous_start = -1.0
        expected_index = 0
        for scene in timeline.scenes:
            if scene.index != expected_index:
                raise TimelineValidationError("Timeline scene indexes must be contiguous")
            expected_index += 1
            if scene.start_sec < 0 or scene.end_sec < 0:
                raise TimelineValidationError(f"Scene {scene.id} has a negative timestamp")
            if scene.end_sec <= scene.start_sec:
                raise TimelineValidationError(f"Scene {scene.id} has invalid duration")
            if scene.start_sec < previous_start:
                raise TimelineValidationError("Timeline scenes must be ordered by start time")
            if scene.voice_track.duration_sec <= 0:
                raise TimelineValidationError(f"Scene {scene.id} has invalid voice duration")
            previous_start = scene.start_sec

    def _validate_tracks(self, timeline: Timeline) -> None:
        for track in timeline.tracks:
            if not track.items:
                raise TimelineValidationError(f"Timeline track is empty: {track.name}")
            self._validate_ordered_items(track.items, f"track {track.name}")

    def _validate_ordered_items(self, items: Iterable[TimelineItem], context: str) -> None:
        previous_end = 0.0
        first = True
        for item in sorted(items, key=lambda candidate: (candidate.start_sec, candidate.end_sec)):
            if item.start_sec < 0 or item.end_sec < 0:
                raise TimelineValidationError(f"{context} item {item.id} has a negative timestamp")
            if item.end_sec <= item.start_sec:
                raise TimelineValidationError(f"{context} item {item.id} has invalid duration")
            if not first and item.start_sec < previous_end - 0.001:
                raise TimelineValidationError(f"{context} has overlapping items near {item.id}")
            first = False
            previous_end = item.end_sec

    def _validate_captions(self, timeline: Timeline) -> None:
        if not timeline.captions:
            raise TimelineValidationError("Timeline caption track is empty")

        previous_end = 0.0
        for caption in timeline.captions:
            if caption.start_sec < 0 or caption.end_sec < 0:
                raise TimelineValidationError(f"Caption {caption.id} has a negative timestamp")
            if caption.end_sec <= caption.start_sec:
                raise TimelineValidationError(f"Caption {caption.id} has invalid timing")
            if caption.start_sec < previous_end - 0.001:
                raise TimelineValidationError(f"Caption {caption.id} overlaps a previous caption")
            if not caption.text.strip():
                raise TimelineValidationError(f"Caption {caption.id} is empty")
            previous_end = caption.end_sec


def build_timeline(
    *,
    script: Script,
    voice_tracks: list[VoiceTrack],
    media_assets: list[MediaAsset],
    upload_metadata: UploadMetadata | None = None,
    options: TimelineBuildOptions | None = None,
) -> Timeline:
    """Build and validate a Timeline from typed stage artifacts.

    Args:
        script: Validated script artifact.
        voice_tracks: One voice track per script segment.
        media_assets: One primary media asset per script segment.
        upload_metadata: Optional publish metadata used for traceability only.
        options: Timeline construction options.

    Returns:
        A validated Timeline.

    Raises:
        ValidationError: If the typed inputs do not align.
        TimelineValidationError: If the constructed timeline violates invariants.
    """

    options = options or TimelineBuildOptions()
    if len(script.segments) != len(voice_tracks):
        raise ValidationError("Timeline requires one VoiceTrack per ScriptSegment")
    if len(script.segments) != len(media_assets):
        raise ValidationError("Timeline requires one MediaAsset per ScriptSegment")

    assets: dict[str, MediaAsset] = {}
    scenes: list[TimelineScene] = []
    video_items: list[TimelineItem] = []
    voice_items: list[TimelineItem] = []
    caption_items: list[TimelineItem] = []
    captions: list[CaptionEntry] = []
    transitions: list[dict] = []

    cursor = 0.0
    for index, (segment, voice, media) in enumerate(
        zip(script.segments, voice_tracks, media_assets)
    ):
        scene_id = f"scene-{index}"
        media_asset_id = _asset_id("media", index, media.local_path)
        assets[media_asset_id] = media

        start_sec = cursor
        end_sec = start_sec + voice.duration_sec
        transition = {
            "type": "crossfade",
            "duration_sec": options.transition_duration_sec,
        } if options.transition_duration_sec > 0 and index > 0 else {}

        scene = TimelineScene(
            id=scene_id,
            index=index,
            start_sec=start_sec,
            end_sec=end_sec,
            script_segment=segment,
            voice_track=voice,
            media_asset_id=media_asset_id,
            transition=transition,
            metadata={
                "upload_id": upload_metadata.id if upload_metadata else "",
                "legacy_segment": segment.to_legacy_dict(),
                **{
                    key: value
                    for key, value in media.metadata.items()
                    if key in {"compare_pair"}
                },
            },
        )
        scenes.append(scene)

        video_items.append(TimelineItem(
            id=f"video-{index}",
            track_type=TrackType.VIDEO,
            track_name="visuals",
            start_sec=start_sec,
            end_sec=end_sec,
            asset_id=media_asset_id,
            scene_id=scene_id,
            properties={"fit": "cover", **media.metadata},
        ))
        voice_items.append(TimelineItem(
            id=f"voice-{index}",
            track_type=TrackType.AUDIO,
            track_name="voice",
            start_sec=start_sec,
            end_sec=end_sec,
            asset_id=None,
            scene_id=scene_id,
            properties=voice.to_dict(),
        ))
        caption_items.append(TimelineItem(
            id=f"caption-{index}",
            track_type=TrackType.CAPTION,
            track_name="captions",
            start_sec=start_sec,
            end_sec=end_sec,
            asset_id=None,
            scene_id=scene_id,
            properties={"text": segment.narration},
        ))
        captions.append(CaptionEntry(
            id=f"caption-{index}",
            start_sec=start_sec,
            end_sec=end_sec,
            text=segment.narration,
            scene_id=scene_id,
        ))

        if index > 0 and options.transition_duration_sec > 0:
            transitions.append({
                "type": "crossfade",
                "from_scene_id": f"scene-{index - 1}",
                "to_scene_id": scene_id,
                "duration_sec": options.transition_duration_sec,
            })

        cursor = end_sec

    tracks = [
        TimelineTrack("visuals", TrackType.VIDEO, video_items),
        TimelineTrack("voice", TrackType.AUDIO, voice_items),
        TimelineTrack("captions", TrackType.CAPTION, caption_items),
    ]
    timeline = Timeline(
        id=options.timeline_id,
        episode_id=options.episode_id,
        width=options.width,
        height=options.height,
        fps=options.fps,
        items=[*video_items, *voice_items, *caption_items],
        chapters=[{"title": script.title, "start_sec": 0.0}],
        format_profile=options.format_profile,
        assets=assets,
        scenes=scenes,
        tracks=tracks,
        captions=captions,
        transitions=transitions,
        metadata={
            "script_title": script.title,
            "script_niche": script.niche,
            "music_mood": script.music_mood,
            "upload_metadata_id": upload_metadata.id if upload_metadata else "",
            "legacy_transition_duration_sec": options.transition_duration_sec,
            "legacy_estimated_render_duration_sec": max(
                0.0,
                cursor - max(0, len(script.segments) - 1) * options.transition_duration_sec,
            ),
        },
    )

    TimelineValidator().validate(timeline, check_asset_files=options.check_asset_files)
    return timeline


def _asset_id(prefix: str, index: int, path: Path) -> str:
    stem = path.stem.replace(" ", "-")[:40] or "asset"
    return f"{prefix}-{index}-{stem}"
