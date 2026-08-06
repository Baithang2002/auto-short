"""Format-profile registry and default lookup.

Profiles express platform ceilings only. The story/script determines the
natural length; ``max_duration_sec`` is the sole hard constraint.

Registered formats
------------------
* ``shorts_vertical`` -- YouTube Shorts (60s ceiling).
* ``tiktok_vertical`` -- TikTok (180s ceiling).
* ``reels_vertical`` -- Instagram Reels (90s ceiling).

Each profile's ceiling can be overridden at runtime via
``AUTO_VIDEO_FORMAT_MAX_DURATION_SEC``, and the active profile can be
selected via ``AUTO_VIDEO_FORMAT``.
"""

from __future__ import annotations

import os

from .profiles import FormatProfile

# Values below are the historical Shorts constants from auto_short.py,
# minus the fixed 50-58s window. Any change to these values will change
# published output.
_SHORTS_VERTICAL = FormatProfile(
    name="shorts_vertical",
    max_duration_sec=60,
    target_duration_sec=None,
    min_duration_sec=0.0,
    min_story_beats=4,
    scene_target_duration_sec=5.0,
    transition_duration_sec=0.22,
    preferred_narration_tempo=1.03,
    narration_max_retime_tempo=1.05,
    narration_min_retime_tempo=0.90,
    narration_words_per_sec_min=2.00,
    narration_words_per_sec_max=2.25,
    narration_words_per_segment_min=8,
)

_TIKTOK_VERTICAL = FormatProfile(
    name="tiktok_vertical",
    max_duration_sec=180,
    target_duration_sec=None,
    min_duration_sec=0.0,
    min_story_beats=4,
    scene_target_duration_sec=6.0,
    transition_duration_sec=0.22,
    preferred_narration_tempo=1.03,
    narration_max_retime_tempo=1.05,
    narration_min_retime_tempo=0.90,
    narration_words_per_sec_min=2.00,
    narration_words_per_sec_max=2.25,
    narration_words_per_segment_min=8,
)

_REELS_VERTICAL = FormatProfile(
    name="reels_vertical",
    max_duration_sec=90,
    target_duration_sec=None,
    min_duration_sec=0.0,
    min_story_beats=4,
    scene_target_duration_sec=5.0,
    transition_duration_sec=0.22,
    preferred_narration_tempo=1.03,
    narration_max_retime_tempo=1.05,
    narration_min_retime_tempo=0.90,
    narration_words_per_sec_min=2.00,
    narration_words_per_sec_max=2.25,
    narration_words_per_segment_min=8,
)

_REGISTRY: dict[str, FormatProfile] = {
    "shorts_vertical": _SHORTS_VERTICAL,
    "tiktok_vertical": _TIKTOK_VERTICAL,
    "reels_vertical": _REELS_VERTICAL,
}


def _apply_env_overrides(profile: FormatProfile) -> FormatProfile:
    """Apply ``AUTO_VIDEO_FORMAT_MAX_DURATION_SEC`` to a profile."""
    raw = os.environ.get("AUTO_VIDEO_FORMAT_MAX_DURATION_SEC", "").strip()
    if not raw:
        return profile
    try:
        ceiling = int(raw)
    except ValueError:
        raise ValueError(
            "AUTO_VIDEO_FORMAT_MAX_DURATION_SEC must be a positive integer "
            f"number of seconds, got {raw!r}"
        )
    if ceiling <= 0:
        raise ValueError(
            "AUTO_VIDEO_FORMAT_MAX_DURATION_SEC must be a positive integer "
            f"number of seconds, got {raw!r}"
        )
    return FormatProfile(**{**profile.__dict__, "max_duration_sec": ceiling})


def get_format_profile(name: str) -> FormatProfile:
    """Return a registered format profile by name, applying env overrides.

    Parameters
    ----------
    name:
        Format profile name: ``"shorts_vertical"``, ``"tiktok_vertical"``,
        or ``"reels_vertical"``.

    Returns
    -------
    FormatProfile
        The registered profile (with any env ceiling override applied).

    Raises
    ------
    KeyError
        If ``name`` is not registered. The error message lists all
        available profile names.
    """
    try:
        return _apply_env_overrides(_REGISTRY[name])
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(
            f"Unknown format profile: {name!r}. Available: {available}"
        ) from exc


def resolve_format_profile() -> FormatProfile:
    """Return the active format profile for this run.

    Reads ``AUTO_VIDEO_FORMAT`` (default ``"shorts_vertical"``). Unknown
    values fail fast so a typo never silently falls back to the Shorts
    ceiling.
    """
    name = os.environ.get("AUTO_VIDEO_FORMAT", "shorts_vertical").strip()
    return get_format_profile(name)


def get_default_format_profile() -> FormatProfile:
    """Return the default format profile (``shorts_vertical``).

    This is the profile the pipeline uses when no explicit format is
    selected.
    """
    return _SHORTS_VERTICAL
