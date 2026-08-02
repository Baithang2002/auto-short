from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.intelligence.topic_cards import find_topic_card


class DurationPolicyTests(unittest.TestCase):
    def test_topic_card_duration_is_used_for_known_topics(self) -> None:
        card = find_topic_card("How hummingbirds hover while feeding")

        assert card is not None
        duration, reason = auto_short.automatic_duration_for_topic(card.premise, card)

        self.assertEqual(auto_short.SHORTS_MIN_DURATION, duration)
        self.assertIn(card.id, reason)

    def test_mechanism_fallback_stays_inside_production_duration_profile(self) -> None:
        duration, reason = auto_short.automatic_duration_for_topic(
            "how wind turbines turn wind into electricity"
        )

        self.assertEqual(auto_short.SHORTS_MIN_DURATION, duration)
        self.assertIn("mechanism", reason)

    def test_automatic_fallback_preserves_original_targets_when_profile_allows(self) -> None:
        with patch.object(auto_short, "SHORTS_MIN_DURATION", 40):
            mechanism, _ = auto_short.automatic_duration_for_topic("how turbines rotate")
            explainer, _ = auto_short.automatic_duration_for_topic("ancient map symbols")

        self.assertEqual(48, mechanism)
        self.assertEqual(46, explainer)

    def test_legacy_topics_get_conservative_fallback_duration(self) -> None:
        duration, reason = auto_short.automatic_duration_for_topic(
            "Why Lightning Never Strikes Twice"
        )

        self.assertEqual(52, duration)
        self.assertIn("curiosity", reason)

    def test_explicit_duration_remains_an_override_at_call_site(self) -> None:
        duration, reason = auto_short.resolve_target_duration(
            "How hummingbirds hover while feeding",
            explicit_duration=30,
            card=find_topic_card("How hummingbirds hover while feeding"),
        )

        self.assertEqual(30, duration)
        self.assertEqual("explicit --duration override", reason)

    def test_production_target_retimes_fast_voice_above_publish_minimum(self) -> None:
        per_scene_duration = 4.234
        voice_items = [
            {
                "idx": index,
                "voice": f"voice-{index}.mp3",
                "duration": per_scene_duration,
            }
            for index in range(10)
        ]

        def retime(voice_path, index, tempo):
            return f"voice-{index}-retimed.mp3", per_scene_duration / tempo

        def pad(voice_path, index, duration, padding):
            return f"voice-{index}-padded.mp3", duration + padding

        with patch.object(auto_short, "retime_voice", side_effect=retime), patch.object(
            auto_short, "pad_voice", side_effect=pad
        ):
            adjusted = auto_short.normalize_voice_timing(
                voice_items,
                target_duration=auto_short.SHORTS_MIN_DURATION,
            )

        transition_overlap = 9 * auto_short.SHORTS_TRANSITION_DURATION
        estimated_final_duration = sum(item["duration"] for item in adjusted) - transition_overlap
        self.assertGreaterEqual(estimated_final_duration, auto_short.SHORTS_MIN_DURATION)


if __name__ == "__main__":
    unittest.main()
