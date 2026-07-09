"""Adapter from Timeline IR to the current imperative FFmpeg renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autovideo.domain.timeline import Timeline, TimelineScene


@dataclass(frozen=True)
class LegacyRenderItem:
    """One scene worth of inputs for the existing ``build_segment`` function."""

    index: int
    narration: str
    voice_path: Path
    duration_sec: float
    media_path: Path | None
    compare_pair: tuple[Path, Path] | None = None


class LegacyRendererAdapter:
    """Convert a Timeline into the legacy renderer's current input shape."""

    def __init__(self, timeline: Timeline) -> None:
        self.timeline = timeline

    def render_items(self) -> list[LegacyRenderItem]:
        """Return scene render items in timeline order."""

        items: list[LegacyRenderItem] = []
        for scene in sorted(self.timeline.scenes, key=lambda candidate: candidate.index):
            media_asset = self.timeline.assets.get(scene.media_asset_id)
            compare_pair = _compare_pair(scene)
            items.append(LegacyRenderItem(
                index=scene.index,
                narration=scene.script_segment.narration,
                voice_path=scene.voice_track.audio_path,
                duration_sec=scene.voice_track.duration_sec,
                media_path=media_asset.local_path if media_asset else None,
                compare_pair=compare_pair,
            ))
        return items

    def caption_meta(self) -> list[tuple[str, float]]:
        """Return the legacy ``[(text, duration)]`` caption input."""

        return [
            (item.narration, item.duration_sec)
            for item in self.render_items()
        ]


def _compare_pair(scene: TimelineScene) -> tuple[Path, Path] | None:
    raw = scene.metadata.get("compare_pair")
    if not raw or len(raw) != 2:
        return None
    return Path(raw[0]), Path(raw[1])
