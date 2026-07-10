"""Format-profile registry and default lookup.

Only ``shorts_vertical`` is registered in this release. Additional
formats (long-form documentary, educational horizontal, podcast) are
added in subsequent PRs by extending ``_REGISTRY`` and
``FormatProfileName``. The lookup contract does not change.
"""

from __future__ import annotations

from .profiles import FormatProfile

# Values below are the historical Shorts constants from auto_short.py.
# Any change to these values will change published Shorts output.
_SHORTS_VERTICAL = FormatProfile(
    name="shorts_vertical",
    target_duration_sec=60,
    min_duration_sec=50,
    max_duration_sec=58,
    scene_target_duration_sec=5.0,
    transition_duration_sec=0.22,
    preferred_narration_tempo=1.06,
    narration_max_retime_tempo=1.30,
    narration_min_retime_tempo=0.90,
    narration_words_per_sec_min=2.25,
    narration_words_per_sec_max=2.55,
    narration_words_per_segment_min=10,
)

_REGISTRY: dict[str, FormatProfile] = {
    "shorts_vertical": _SHORTS_VERTICAL,
}


def get_format_profile(name: str) -> FormatProfile:
    """Return a registered format profile by name.

    Parameters
    ----------
    name:
        Format profile name. Currently ``"shorts_vertical"`` is the only
        registered value.

    Returns
    -------
    FormatProfile
        The registered profile.

    Raises
    ------
    KeyError
        If ``name`` is not registered. The error message lists all
        available profile names.
    """
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(
            f"Unknown format profile: {name!r}. Available: {available}"
        ) from exc


def get_default_format_profile() -> FormatProfile:
    """Return the default format profile (``shorts_vertical``).

    This is the profile the pipeline uses when no explicit format is
    selected. Until additional formats are registered and a selection
    mechanism is added, this returns the same profile as
    ``get_format_profile("shorts_vertical")``.
    """
    return _SHORTS_VERTICAL
