"""Unit tests for the story-driven duration/quality planning module.

These cover the pure helpers in ``autovideo.format.story``: beat metadata,
word-count estimation, ceiling budgeting, structural validation, quality
scoring, analytics, and env toggles.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

from autovideo.format import story as story_planning
from autovideo.format import get_format_profile

SHORTS = get_format_profile("shorts_vertical")


def _segments(roles: list[str]) -> list[dict]:
    return [
        {
            "narration": "A concrete visual explanation gives a complete sentence with enough spoken words.",
            "broll": "beaver carrying branches",
            "broll_queries": ["beaver carrying branch water"],
            "beat_role": role,
        }
        for role in roles
    ]


def _script(roles: list[str]) -> dict:
    return {
        "title": "The River Builder Hiding in Plain Sight",
        "segments": _segments(roles),
    }


class TestWordAndDurationHelpers(unittest.TestCase):
    def test_count_words_counts_contractions_as_one_word(self) -> None:
        assert story_planning.count_words("It's a fox's den today") == 5

    def test_estimate_story_duration_is_conservative(self) -> None:
        script = _script(["hook", "discovery", "conclusion_cta"])
        words = sum(story_planning.segment_words(script["segments"]))
        conservative = story_planning.estimate_story_duration(script, SHORTS, conservative=True)
        optimistic = story_planning.estimate_story_duration(script, SHORTS, conservative=False)
        assert conservative >= optimistic
        assert abs(conservative - words / 2.00) < 0.001
        assert abs(optimistic - words / 2.25) < 0.001

    def test_voice_budget_leaves_room_for_transitions_and_margin(self) -> None:
        budget = story_planning.voice_budget_seconds(SHORTS, 10, renderer_tolerance_sec=1.0)
        transitions = 9 * SHORTS.transition_duration_sec
        assert budget == 60.0 + 1.0 - transitions - 0.5

    def test_voice_budget_matches_renderer_acceptance_for_near_ceiling_narration(self) -> None:
        budget = story_planning.voice_budget_seconds(SHORTS, 4, renderer_tolerance_sec=1.0)
        transitions = 3 * SHORTS.transition_duration_sec
        assert budget >= 59.0

    def test_voice_budget_never_drops_below_one_second(self) -> None:
        assert story_planning.voice_budget_seconds(SHORTS, 100) >= 1.0

    def test_story_roles_normalize_and_fall_back_to_discovery(self) -> None:
        roles = story_planning.story_roles(_script(["hook", "context", "made_up_role"]))
        assert roles == ["hook", "context", "discovery"]


class TestValidateBeatStructure(unittest.TestCase):
    def test_complete_story_passes_clean(self) -> None:
        fatal, soft = story_planning.validate_beat_structure(
            _script(["hook", "setup", "climax", "conclusion_cta"]), SHORTS
        )
        assert fatal == []
        assert soft == []

    def test_missing_hook_is_fatal(self) -> None:
        fatal, _ = story_planning.validate_beat_structure(
            _script(["setup", "discovery", "conclusion_cta"]), SHORTS
        )
        assert any("missing a hook" in note for note in fatal)

    def test_missing_conclusion_cta_is_fatal(self) -> None:
        fatal, _ = story_planning.validate_beat_structure(
            _script(["hook", "setup", "discovery"]), SHORTS
        )
        assert any("missing a conclusion" in note for note in fatal)

    def test_below_beat_floor_is_fatal(self) -> None:
        fatal, _ = story_planning.validate_beat_structure(
            _script(["hook", "conclusion_cta"]), SHORTS
        )
        assert any("below the 4-beat floor" in note for note in fatal)

    def test_empty_narration_and_missing_broll_are_fatal(self) -> None:
        script = {
            "title": "T",
            "segments": [
                {"narration": "", "broll": "beaver", "beat_role": "hook"},
                {"narration": "Too short.", "broll": "", "beat_role": "conclusion_cta"},
            ],
        }
        fatal, _ = story_planning.validate_beat_structure(script, SHORTS)
        assert any("no narration" in note for note in fatal)
        assert any("missing broll" in note for note in fatal)

    def test_short_complete_story_never_judged_by_duration(self) -> None:
        script = {
            "title": "A Tiny Lantern Switches On at Dusk",
            "segments": [
                {"narration": "A quiet survival trick appears in plain sight.", "broll": "firefly", "beat_role": "hook"},
                {"narration": "Its light is cold, made by a precise trick.", "broll": "firefly", "beat_role": "setup"},
                {"narration": "The flash is a message between the grass.", "broll": "firefly", "beat_role": "discovery"},
                {"narration": "Follow Wonders of the Nature for more secrets.", "broll": "forest", "beat_role": "conclusion_cta"},
            ],
        }
        fatal, soft = story_planning.validate_beat_structure(script, SHORTS)
        assert fatal == []
        assert not any("duration" in note or "second" in note for note in fatal + soft)


class TestMergeSuggestions(unittest.TestCase):
    def test_protected_roles_never_ranked_as_trim_candidates(self) -> None:
        script = _script(
            ["hook", "context", "setup", "discovery", "climax", "conclusion_cta"]
        )
        candidates = story_planning.merge_suggestions(script["segments"])
        protected = {c["role"] for c in candidates[-3:]}
        assert protected == {"hook", "climax", "conclusion_cta"}

    def test_low_priority_supporting_beats_rank_first(self) -> None:
        script = _script(
            ["hook", "context", "setup", "discovery", "interesting_fact", "conclusion_cta"]
        )
        candidates = story_planning.merge_suggestions(script["segments"])
        assert candidates[0]["role"] == "interesting_fact"
        assert candidates[1]["role"] == "context"

    def test_can_remove_false_protects_beat(self) -> None:
        segments = _segments(["hook", "context", "conclusion_cta"])
        segments[1]["beat_can_remove"] = "false"
        candidates = story_planning.merge_suggestions(segments)
        assert candidates[-1]["index"] == 1


class TestQualityScoring(unittest.TestCase):
    def test_parse_quality_scores_clamps_to_0_10(self) -> None:
        scores = story_planning.parse_quality_scores('{"hook_strength": 99, "repetition": -5, "summary": "x"}')
        assert scores["hook_strength"] == 10.0
        assert scores["repetition"] == 0.0

    def test_parse_rejects_non_dict_and_bad_json(self) -> None:
        assert story_planning.parse_quality_scores("[1, 2]") == {}
        assert story_planning.parse_quality_scores("not json") == {}

    def test_aggregate_averages_only_numeric_criteria(self) -> None:
        scores = story_planning.parse_quality_scores(
            '{"hook_strength": 9, "narrative_coherence": 9, "logical_flow": 9,'
            ' "repetition": 9, "educational_value": 9, "emotional_progression": 9,'
            ' "ending_quality": 9, "hook_present": "yes", "ending_present": "yes",'
            ' "beats_coherent": "yes", "summary": "s"}'
        )
        assert story_planning.aggregate_quality_score(scores) == 9.0

    def test_aggregate_is_none_when_unscorable(self) -> None:
        assert story_planning.aggregate_quality_score({}) is None

    def test_structurally_broken_when_hook_or_ending_absent(self) -> None:
        broken = {"hook_present": False, "ending_present": True}
        assert story_planning.is_structurally_broken(broken)
        broken = {"hook_present": True, "ending_present": False}
        assert story_planning.is_structurally_broken(broken)
        assert not story_planning.is_structurally_broken({"hook_present": True, "ending_present": True})


class TestStoryAnalytics(unittest.TestCase):
    def test_analytics_shape(self) -> None:
        script = _script(["hook", "discovery", "conclusion_cta"])
        report = story_planning.story_analytics(
            script,
            SHORTS,
            estimated_seconds=30.0,
            actual_narration_seconds=28.0,
            final_video_seconds=27.0,
            quality_score=8.5,
            trim_applied=True,
            narration_overflow=False,
            renderer_tail_trim=False,
        )
        assert report["profile"] == "shorts_vertical"
        assert report["platform_max_duration_sec"] == 60
        assert report["beat_count"] == 3
        assert report["story_quality_score"] == 8.5
        assert report["semantic_trim_applied"] is True
        assert report["role_distribution"]["hook"] == 1


class TestEnvToggles(unittest.TestCase):
    def test_planner_disabled_via_env(self) -> None:
        with patch.dict(os.environ, {"AUTO_VIDEO_STORY_PLANNER": "0"}, clear=False):
            assert story_planning.planner_enabled() is False
        with patch.dict(os.environ, {"AUTO_VIDEO_STORY_PLANNER": "1"}, clear=False):
            assert story_planning.planner_enabled() is True

    def test_quality_gate_strict_via_env(self) -> None:
        with patch.dict(os.environ, {"AUTO_VIDEO_STORY_QUALITY_STRICT": "1"}, clear=False):
            assert story_planning.quality_gate_soft() is False
        with patch.dict(os.environ, {"AUTO_VIDEO_STORY_QUALITY_STRICT": "0"}, clear=False):
            assert story_planning.quality_gate_soft() is True

    def test_min_story_score_parses_and_clamps(self) -> None:
        with patch.dict(os.environ, {"AUTO_VIDEO_MIN_STORY_SCORE": "9.5"}, clear=False):
            assert story_planning.min_story_score() == 9.5
        with patch.dict(os.environ, {"AUTO_VIDEO_MIN_STORY_SCORE": "banana"}, clear=False):
            assert story_planning.min_story_score() == 8.0
        with patch.dict(os.environ, {"AUTO_VIDEO_MIN_STORY_SCORE": "100"}, clear=False):
            assert story_planning.min_story_score() == 10.0


if __name__ == "__main__":
    unittest.main()