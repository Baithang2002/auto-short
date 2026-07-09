"""Renderer-level Timeline validation."""

from __future__ import annotations

from pathlib import Path

from autovideo.domain import Timeline, TrackType
from autovideo.domain.errors import RendererValidationError

from .profiles import RenderProfile


class RendererValidator:
    """Validate Timeline compatibility with a concrete render profile."""

    def __init__(self, profile: RenderProfile) -> None:
        self.profile = profile

    def validate(self, timeline: Timeline) -> None:
        errors: list[str] = []
        if timeline.width <= 0 or timeline.height <= 0:
            errors.append("timeline dimensions must be positive")
        if timeline.fps <= 0:
            errors.append("timeline fps must be positive")
        if timeline.width != self.profile.width or timeline.height != self.profile.height:
            errors.append(
                f"timeline dimensions {timeline.width}x{timeline.height} do not match "
                f"render profile {self.profile.width}x{self.profile.height}"
            )
        if timeline.fps != self.profile.fps:
            errors.append(f"timeline fps {timeline.fps} does not match render profile {self.profile.fps}")
        if timeline.duration_sec <= 0:
            errors.append("timeline duration must be positive")
        if not timeline.scenes:
            errors.append("timeline has no scenes")
        if not timeline.tracks:
            errors.append("timeline has no tracks")
        if not any(track.track_type == TrackType.VIDEO and track.items for track in timeline.tracks):
            errors.append("timeline has no populated video track")
        if not any(track.track_type == TrackType.AUDIO and track.items for track in timeline.tracks):
            errors.append("timeline has no populated audio track")
        if not any(track.track_type == TrackType.CAPTION and track.items for track in timeline.tracks):
            errors.append("timeline has no populated caption track")
        if not timeline.captions:
            errors.append("timeline has no caption entries")

        previous_start = -1.0
        for scene in sorted(timeline.scenes, key=lambda candidate: candidate.index):
            if scene.start_sec < 0 or scene.end_sec < 0:
                errors.append(f"scene {scene.id} has negative timing")
            if scene.end_sec <= scene.start_sec:
                errors.append(f"scene {scene.id} has invalid duration")
            if scene.start_sec < previous_start:
                errors.append(f"scene {scene.id} is out of order")
            previous_start = scene.start_sec
            asset = timeline.assets.get(scene.media_asset_id)
            if asset is None:
                errors.append(f"scene {scene.id} references missing media asset {scene.media_asset_id}")
            elif not _exists(asset.local_path):
                errors.append(f"scene {scene.id} media asset does not exist: {asset.local_path}")
            if not _exists(scene.voice_track.audio_path):
                errors.append(f"scene {scene.id} voice track does not exist: {scene.voice_track.audio_path}")
            if scene.voice_track.duration_sec <= 0:
                errors.append(f"scene {scene.id} voice duration must be positive")

        for caption in timeline.captions:
            if caption.start_sec < 0 or caption.end_sec < 0:
                errors.append(f"caption {caption.id} has negative timing")
            if caption.end_sec <= caption.start_sec:
                errors.append(f"caption {caption.id} has invalid duration")
            if caption.end_sec > timeline.duration_sec + 1.0:
                errors.append(f"caption {caption.id} extends beyond timeline duration")

        for track in timeline.tracks:
            last_end = -1.0
            for item in sorted(track.items, key=lambda candidate: candidate.start_sec):
                if item.start_sec < 0 or item.end_sec < 0:
                    errors.append(f"track item {item.id} has negative timing")
                if item.end_sec <= item.start_sec:
                    errors.append(f"track item {item.id} has invalid duration")
                if item.start_sec < last_end - 0.001:
                    errors.append(f"track {track.name} has overlapping item {item.id}")
                last_end = max(last_end, item.end_sec)

        if errors:
            raise RendererValidationError("; ".join(errors))


def _exists(path: Path | str | None) -> bool:
    return bool(path) and Path(path).exists()
