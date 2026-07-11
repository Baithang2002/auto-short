"""Unit tests for the FormatProfile abstraction.

Verifies that the shorts_vertical profile matches the historical
module-level constants byte-for-byte, and that the registry contract
behaves as documented.
"""

from __future__ import annotations

import dataclasses
import unittest

from autovideo.format import (
    FormatProfile,
    get_default_format_profile,
    get_format_profile,
)


class TestShortsVerticalValues(unittest.TestCase):
    """The shorts_vertical profile must match pre-PR module constants exactly."""

    def setUp(self) -> None:
        self.profile = get_format_profile("shorts_vertical")

    def test_name(self) -> None:
        assert self.profile.name == "shorts_vertical"

    def test_target_duration_sec_matches_render_defaults(self) -> None:
        # Was DEFAULTS.render.target_duration_sec = 60
        assert self.profile.target_duration_sec == 60

    def test_min_duration_sec_matches_render_defaults(self) -> None:
        # Was DEFAULTS.render.shorts_min_duration_sec = 50
        assert self.profile.min_duration_sec == 50

    def test_max_duration_sec_matches_render_defaults(self) -> None:
        # Was DEFAULTS.render.shorts_max_duration_sec = 58
        assert self.profile.max_duration_sec == 58

    def test_scene_target_duration_sec_matches_module_constant(self) -> None:
        # Was auto_short.py: SHORTS_SCENE_TARGET_DURATION = 5.0
        assert self.profile.scene_target_duration_sec == 5.0

    def test_transition_duration_sec_matches_module_constant(self) -> None:
        # Was auto_short.py: SHORTS_TRANSITION_DURATION = 0.22
        assert self.profile.transition_duration_sec == 0.22

    def test_preferred_narration_tempo_matches_module_constant(self) -> None:
        # Was auto_short.py: SHORTS_PREFERRED_NARRATION_TEMPO = 1.06
        assert self.profile.preferred_narration_tempo == 1.06

    def test_narration_max_retime_tempo_matches_inline_value(self) -> None:
        # Was inline in normalize_voice_timing: tempo = min(1.30, raw_tempo)
        assert self.profile.narration_max_retime_tempo == 1.30

    def test_narration_min_retime_tempo_matches_inline_value(self) -> None:
        # Was inline in normalize_voice_timing: tempo = max(0.90, raw_tempo)
        assert self.profile.narration_min_retime_tempo == 0.90

    def test_narration_words_per_sec_min_matches_inline_value(self) -> None:
        # Was inline in narration_targets: round(target_duration * 2.25)
        assert self.profile.narration_words_per_sec_min == 2.25

    def test_narration_words_per_sec_max_matches_inline_value(self) -> None:
        # Was inline in narration_targets: round(target_duration * 2.55)
        assert self.profile.narration_words_per_sec_max == 2.55

    def test_narration_words_per_segment_min_matches_inline_value(self) -> None:
        # Was inline in narration_targets: max(n_segments * 10, ...)
        assert self.profile.narration_words_per_segment_min == 10


class TestRegistryLookup(unittest.TestCase):
    def test_get_shorts_vertical_returns_format_profile(self) -> None:
        profile = get_format_profile("shorts_vertical")
        assert isinstance(profile, FormatProfile)
        assert profile.name == "shorts_vertical"

    def test_get_unknown_raises_key_error_with_available_names(self) -> None:
        with self.assertRaises(KeyError) as exc_info:
            get_format_profile("long_form_documentary")
        # The error message must list registered profile names so a
        # contributor can immediately see what is available.
        assert "shorts_vertical" in str(exc_info.exception)

    def test_get_default_returns_shorts_vertical(self) -> None:
        profile = get_default_format_profile()
        assert profile.name == "shorts_vertical"

    def test_default_is_same_instance_as_registered_shorts_vertical(self) -> None:
        # Same object identity means one source of truth for the values.
        assert get_default_format_profile() is get_format_profile("shorts_vertical")


class TestFormatProfileImmutability(unittest.TestCase):
    def test_profile_is_frozen(self) -> None:
        profile = get_default_format_profile()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.max_duration_sec = 999  # type: ignore[misc]

    def test_profile_field_types_are_preserved(self) -> None:
        # Duration bounds were ints on RenderDefaults; keep them ints so
        # comparisons like `duration >= SHORTS_MIN_DURATION` behave identically.
        profile = get_default_format_profile()
        assert isinstance(profile.target_duration_sec, int)
        assert isinstance(profile.min_duration_sec, int)
        assert isinstance(profile.max_duration_sec, int)
        assert isinstance(profile.narration_words_per_segment_min, int)
        assert isinstance(profile.scene_target_duration_sec, float)
        assert isinstance(profile.transition_duration_sec, float)
        assert isinstance(profile.preferred_narration_tempo, float)
