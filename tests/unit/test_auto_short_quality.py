from __future__ import annotations

import unittest

from tests.unit import _path  # noqa: F401

import auto_short


class AutoShortQualityTests(unittest.TestCase):
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

        self.assertGreaterEqual(min_total, 120)
        self.assertLessEqual(min_segment, 13)
        self.assertLessEqual(max_segment, 17)
        self.assertGreater(max_total, min_total)


if __name__ == "__main__":
    unittest.main()
