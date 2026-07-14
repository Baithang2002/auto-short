from __future__ import annotations

import unittest

from tests.unit import _path  # noqa: F401

from autovideo.media import VisualGrammarEngine


class VisualGrammarEngineTests(unittest.TestCase):
    def test_historical_maritime_is_reusable_not_titanic_specific(self) -> None:
        engine = VisualGrammarEngine(
            topic="Why an ocean liner shipwreck still matters",
            total_scenes=8,
        )

        decision = engine.decide(
            scene_index=0,
            narration="A passenger ship struck an iceberg and the hull failed.",
            queries=("historic passenger ship iceberg",),
        )

        self.assertEqual(decision.grammar_id, "historical_maritime")
        self.assertEqual(decision.composition_template, "ship_collision_diagram")
        self.assertEqual(decision.composition_confidence, "high")
        self.assertTrue(decision.should_compose)
        self.assertIn("ocean liner iceberg collision diagram", decision.repaired_queries)

    def test_generic_documentary_rejects_low_confidence_motion_graphics(self) -> None:
        engine = VisualGrammarEngine(topic="A surprising everyday fact", total_scenes=8)

        decision = engine.decide(
            scene_index=0,
            narration="This surprising fact changed how people think.",
            queries=("surprising fact",),
        )

        self.assertEqual(decision.grammar_id, "generic_documentary")
        self.assertFalse(decision.should_compose)
        self.assertEqual(decision.composition_confidence, "low")

    def test_composition_budget_limits_composed_scenes(self) -> None:
        engine = VisualGrammarEngine(topic="How Volcanoes Create New Land", total_scenes=8)
        decisions = [
            engine.decide(
                scene_index=index,
                narration="Lava cools into new volcanic land.",
                queries=("volcano lava geology archive",),
            )
            for index in range(3)
        ]

        self.assertTrue(engine.allow_composition(decisions[0], scene_type="volcanic_land")[0])
        engine.register_composed_asset(scene_type="volcanic_land")
        self.assertTrue(engine.allow_composition(decisions[1], scene_type="volcanic_land")[0])
        engine.register_composed_asset(scene_type="volcanic_land")

        allowed, reason = engine.allow_composition(decisions[2], scene_type="volcanic_land")

        self.assertFalse(allowed)
        self.assertEqual(reason, "composition budget exceeded")

    def test_report_contains_budget_and_decisions(self) -> None:
        engine = VisualGrammarEngine(topic="How Roman Aqueducts Changed Civilization", total_scenes=4)
        decision = engine.decide(
            scene_index=0,
            narration="Roman arches carried water by gravity.",
            queries=("Roman aqueduct arches",),
        )
        engine.register_real_asset(provider="wikimedia")

        report = engine.report()

        self.assertEqual(report["selected_grammar"]["grammar_id"], "ancient_engineering")
        self.assertEqual(report["composition_budget"]["real_count"], 1)
        self.assertEqual(report["composition_budget"]["archive_count"], 1)
        self.assertEqual(report["grammar_decisions"][0]["composition_template"], decision.composition_template)


if __name__ == "__main__":
    unittest.main()
