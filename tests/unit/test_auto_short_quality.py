from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from tests.unit import _path  # noqa: F401

import auto_short


class AutoShortQualityTests(unittest.TestCase):
    def test_dependency_check_accepts_non_gemini_llm_fallback(self) -> None:
        old_values = (
            auto_short.GEMINI_API_KEY,
            auto_short.SAMBANOVA_API_KEY,
            auto_short.GROQ_API_KEY,
            auto_short.PEXELS_API_KEY,
        )
        auto_short.GEMINI_API_KEY = ""
        auto_short.SAMBANOVA_API_KEY = "sambanova-key"
        auto_short.GROQ_API_KEY = ""
        auto_short.PEXELS_API_KEY = ""
        try:
            with patch.object(auto_short.shutil, "which", return_value="ffmpeg"):
                auto_short.check_deps()
        finally:
            (
                auto_short.GEMINI_API_KEY,
                auto_short.SAMBANOVA_API_KEY,
                auto_short.GROQ_API_KEY,
                auto_short.PEXELS_API_KEY,
            ) = old_values

    def test_voice_registry_is_reused_for_all_segments(self) -> None:
        registry = object()
        first_track = Mock(provider="audiolab")
        second_track = Mock(provider="audiolab")
        for track in (first_track, second_track):
            track.to_legacy_item.side_effect = lambda index, segment, current=track: {
                "idx": index,
                "segment": segment,
                "voice": f"voice-{index}.mp3",
                "duration": 1.0,
                "voice_track": current,
            }
        segments = [{"narration": "one"}, {"narration": "two"}]

        with patch.object(auto_short, "_voice_provider_registry", return_value=registry), patch.object(
            auto_short,
            "_make_voice_track",
            side_effect=[first_track, second_track],
        ) as make_track:
            auto_short.make_all_voices(segments, target_duration=1)

        self.assertEqual(2, make_track.call_count)
        self.assertTrue(all(call.kwargs["registry"] is registry for call in make_track.call_args_list))
        self.assertEqual("", make_track.call_args_list[0].kwargs["preferred_provider"])
        self.assertEqual("audiolab", make_track.call_args_list[1].kwargs["preferred_provider"])

    def test_broll_query_list_adds_shot_variety(self) -> None:
        queries = auto_short.broll_query_list(
            {
                "narration": "This tiny fox vanishes into snowy arctic tundra.",
                "broll": "arctic fox hunting",
                "broll_queries": ["arctic fox close up"],
            },
            "arctic fox survival",
        )

        self.assertEqual(queries[0], "wild arctic fox hunting")
        self.assertIn("wild arctic fox close up", queries)
        self.assertIn("arctic fox hunting in snow", queries)
        self.assertIn("wild arctic fox in snow", queries)
        self.assertIn("arctic fox hunting close up", queries)
        self.assertIn("arctic fox hunting in snowy arctic", queries)
        self.assertIn("arctic fox hunting wide shot", queries)

    def test_query_qualification_keeps_cold_wildlife_specific(self) -> None:
        qualified = auto_short._qualify_query("arctic fox close up", "arctic survival")

        self.assertIn("wildlife", qualified)
        self.assertIn("snow", qualified)

    def test_landscape_query_qualification_does_not_force_wildlife(self) -> None:
        qualified = auto_short._qualify_query("arctic tundra snow landscape", "arctic survival")

        self.assertIn("snow", qualified)
        self.assertNotIn("wildlife", qualified)

    def test_lightning_query_qualification_uses_weather_terms(self) -> None:
        qualified = auto_short._qualify_query("lightning strike over city", "weather science")

        self.assertIn("storm", qualified)
        self.assertIn("sky", qualified)
        self.assertNotIn("wildlife", qualified)

    def test_lightning_broad_fallback_does_not_use_animal_terms(self) -> None:
        terms = auto_short._broad_fallback_terms(
            "lightning strike",
            "A lightning bolt forms inside a thunderstorm cloud.",
        )

        self.assertIn("lightning storm sky", terms)
        self.assertNotIn("wildlife close up", terms)
        self.assertNotIn("animals in wild", terms)
        self.assertNotIn("nature documentary", terms)

    def test_unknown_topic_broad_fallback_does_not_default_to_wildlife(self) -> None:
        terms = auto_short._broad_fallback_terms(
            "how glass is manufactured",
            "Molten material cools into a transparent sheet.",
            "How Glass Is Made",
        )

        self.assertIn("how glass is manufactured", terms)
        self.assertFalse(any("wildlife" in term or "animals" in term for term in terms))

    def test_pexels_relevance_penalizes_wrong_arctic_fox_matches(self) -> None:
        good = {"url": "https://www.pexels.com/video/arctic-fox-running-in-snow-123/"}
        bad = {"url": "https://www.pexels.com/video/husky-dog-inside-zoo-cage-456/"}

        self.assertGreater(
            auto_short.pexels_relevance_score(good, "arctic fox close up"),
            auto_short.pexels_relevance_score(bad, "arctic fox close up"),
        )

    def test_narration_targets_provide_enough_words_without_long_scenes(self) -> None:
        min_total, max_total, min_segment, max_segment = auto_short.narration_targets(55, 11)

        self.assertGreaterEqual(min_total, 110)
        self.assertLessEqual(min_segment, 11)
        self.assertLessEqual(max_segment, 15)
        self.assertGreater(max_total, min_total)

    def test_title_style_requires_a_curious_declarative_statement(self) -> None:
        self.assertTrue(any("question-led" in note for note in auto_short._title_style_notes("Why Fireflies Glow")))
        self.assertTrue(any("question mark" in note for note in auto_short._title_style_notes("Fireflies Glow?")))
        self.assertTrue(any("absolute" in note for note in auto_short._title_style_notes("Fireflies Never Stop Glowing")))
        self.assertTrue(any("absolute" in note for note in auto_short._title_style_notes("A Beaver Completely Rewrites a River")))
        self.assertEqual([], auto_short._title_style_notes("A Tiny Lantern Switches On at Dusk"))


if __name__ == "__main__":
    unittest.main()
