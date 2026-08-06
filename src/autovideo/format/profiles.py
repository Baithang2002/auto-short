"""Immutable format-profile type.

A ``FormatProfile`` owns the duration- and scene-shaped configuration
that was previously scattered as module-level constants in
``auto_short.py``. It does *not* own codec, resolution, or provider
concerns -- those remain on ``autovideo.render.profiles.RenderProfile``
(environment axis) and ``autovideo.config.channels.RenderProfile``
(provider axis).

The two profile types are deliberately independent. They compose at the
pipeline entry point without either being modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FormatProfileName = Literal[
    "shorts_vertical",
    "tiktok_vertical",
    "reels_vertical",
]


@dataclass(frozen=True)
class FormatProfile:
    """Immutable format-shaped configuration.

    The profile is the *single* source of duration policy in the
    pipeline. The story/script determines the natural length; the profile
    only constrains the platform ceiling.

    Owns
    ----
    * ``max_duration_sec`` -- the only hard ceiling. Every trim, retime,
      and validation reads this one value.
    * ``target_duration_sec`` -- a story-driven *hint* only. ``None``
      means "the story decides"; it is never fed to writers as a clamp.
    * ``min_duration_sec`` -- a *soft* quality/analytics indicator.
      Never rejects a video and never triggers padding.
    * ``min_story_beats`` -- the content floor for story completeness.
    * Scene transition duration, narration tempo bounds, and narration
      word-rate bounds.

    Does not own
    ------------
    * Video codec, bitrate, or resolution -- owned by environment ``RenderProfile``.
    * Provider preferences or mock behavior -- owned by environment ``RenderProfile``.
    * Music volume, fades, or licensing -- owned by ``MusicConfig``.
    """

    name: FormatProfileName
    max_duration_sec: int
    target_duration_sec: int | None = None
    min_duration_sec: float = 0.0
    min_story_beats: int = 4
    scene_target_duration_sec: float = 5.0
    transition_duration_sec: float = 0.22
    preferred_narration_tempo: float = 1.03
    narration_max_retime_tempo: float = 1.05
    narration_min_retime_tempo: float = 0.90
    narration_words_per_sec_min: float = 2.00
    narration_words_per_sec_max: float = 2.25
    narration_words_per_segment_min: int = 8
