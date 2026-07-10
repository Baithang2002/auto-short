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

FormatProfileName = Literal["shorts_vertical"]


@dataclass(frozen=True)
class FormatProfile:
    """Immutable format-shaped configuration.

    Owns
    ----
    * Duration bounds (target, min, max) for the finished video.
    * Scene target duration and per-scene transition duration.
    * Narration tempo bounds (retime floor and ceiling, preferred tempo).
    * Narration word-rate bounds (words per second, minimum per segment).

    Does not own
    ------------
    * Video codec, bitrate, or resolution -- owned by environment ``RenderProfile``.
    * Provider preferences or mock behavior -- owned by environment ``RenderProfile``.
    * Music volume, fades, or licensing -- owned by ``MusicConfig``.
    """

    name: FormatProfileName
    target_duration_sec: int
    min_duration_sec: int
    max_duration_sec: int
    scene_target_duration_sec: float
    transition_duration_sec: float
    preferred_narration_tempo: float
    narration_max_retime_tempo: float
    narration_min_retime_tempo: float
    narration_words_per_sec_min: float
    narration_words_per_sec_max: float
    narration_words_per_segment_min: int
