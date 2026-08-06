from __future__ import annotations

import unittest
from unittest.mock import patch
import os

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.intelligence.topic_cards import find_topic_card


class DurationPolicyTests(unittest.TestCase):
    def test_advisory_for_known_topic_without_recommendation_defaults_to_ceiling(self) -> None:
        card = find_topic_card("How hummingbirds hover while feeding")

        assert card is not None
        duration, reason = auto_short.automatic_duration_for_topic(card.premise, card)

        expected = card.recommended_duration_sec or auto_short.SHORTS_MAX_DURATION
        self.assertEqual(expected, duration)
        if card.recommended_duration_sec:
            self.assertIn(card.id, reason)
        else:
            self.assertIn("platform ceiling advisory", reason)

    def test_story_driven_fallback_uses_platform_ceiling_advisory(self) -> None:
        duration, reason = auto_short.automatic_duration_for_topic("how turbines rotate")

        self.assertEqual(auto_short.SHORTS_MAX_DURATION, duration)
        self.assertIn("platform ceiling advisory", reason)

    def test_story_driven_length_is_default_and_never_a_clamp(self) -> None:
        duration, reason = auto_short.resolve_target_duration(
            "How hummingbirds hover while feeding"
        )

        self.assertIsNone(duration)
        self.assertIn("story-driven", reason)

    def test_explicit_duration_remains_an_override_at_call_site(self) -> None:
        duration, reason = auto_short.resolve_target_duration(
            "How hummingbirds hover while feeding",
            explicit_duration=30,
            card=find_topic_card("How hummingbirds hover while feeding"),
        )

        self.assertEqual(30, duration)
        self.assertEqual("explicit --duration override", reason)

    def test_short_story_is_never_padded_or_rewritten(self) -> None:
        voice_items = [{"idx": 0, "voice": "voice-0.mp3", "duration": 3.0}]
        with patch.object(
            auto_short,
            "retime_voice",
            side_effect=AssertionError("short stories must not be retimed"),
        ), patch.object(
            auto_short,
            "pad_voice",
            side_effect=AssertionError("padding was removed"),
        ):
            adjusted = auto_short.fit_narration_to_ceiling(
                voice_items, auto_short._FORMAT_PROFILE
            )

        self.assertEqual([3.0], [item["duration"] for item in adjusted])

    def test_ceiling_retime_fast_voice_is_bounded(self) -> None:
        per_scene_duration = 5.0
        voice_items = [
            {"idx": index, "voice": f"voice-{index}.mp3", "duration": per_scene_duration}
            for index in range(12)
        ]
        tempos = []

        def retime(voice_path, index, tempo):
            tempos.append(tempo)
            return f"voice-{index}-retimed.mp3", per_scene_duration / tempo

        with patch.object(auto_short, "retime_voice", side_effect=retime), patch.object(
            auto_short,
            "pad_voice",
            side_effect=AssertionError("padding was removed"),
        ):
            adjusted = auto_short.fit_narration_to_ceiling(
                voice_items, auto_short._FORMAT_PROFILE
            )

        self.assertEqual(12, len(adjusted))
        self.assertEqual(12, len(tempos))
        self.assertTrue(
            all(
                1.0 < tempo <= auto_short._FORMAT_PROFILE.narration_max_retime_tempo
                for tempo in tempos
            )
        )
        self.assertTrue(all(item["duration"] < per_scene_duration for item in adjusted))

    def test_over_ceiling_after_retime_raises(self) -> None:
        voice_items = [{"idx": index, "voice": f"voice-{index}.mp3", "duration": 15.0} for index in range(10)]

        def retime(voice_path, index, tempo):
            # Bounded retime cannot bring 150s voice under 60s ceiling.
            return f"voice-{index}-retimed.mp3", 15.0 / tempo

        with patch.object(auto_short, "retime_voice", side_effect=retime), patch.object(
            auto_short,
            "pad_voice",
            side_effect=AssertionError("padding was removed"),
        ):
            with self.assertRaises(RuntimeError):
                auto_short.fit_narration_to_ceiling(
                    voice_items, auto_short._FORMAT_PROFILE
                )

    def test_over_ceiling_without_retime_raises(self) -> None:
        voice_items = [{"idx": 0, "voice": "voice-0.mp3", "duration": 70.0}]
        with patch.object(
            auto_short,
            "retime_voice",
            side_effect=AssertionError("retime should not be called when disabled"),
        ), patch.object(
            auto_short,
            "pad_voice",
            side_effect=AssertionError("padding was removed"),
        ), patch.dict(
            os.environ,
            {"AUTO_VIDEO_ALLOW_NARRATION_RETIME": "0"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                auto_short.fit_narration_to_ceiling(
                    voice_items, auto_short._FORMAT_PROFILE
                )

    def test_within_tolerance_does_not_retime(self) -> None:
        # 60.5s voice for a single segment is within the default 1s tolerance.
        voice_items = [{"idx": 0, "voice": "voice-0.mp3", "duration": 60.5}]
        with patch.object(
            auto_short,
            "retime_voice",
            side_effect=AssertionError("retime should not be called within tolerance"),
        ):
            adjusted = auto_short.fit_narration_to_ceiling(
                voice_items, auto_short._FORMAT_PROFILE
            )
        self.assertEqual([60.5], [item["duration"] for item in adjusted])


if __name__ == "__main__":
    unittest.main()