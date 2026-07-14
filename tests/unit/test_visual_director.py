import tempfile
import unittest
from pathlib import Path

from autovideo.media import (
    KnowledgePackStore,
    MediaMode,
    QueryTier,
    ShotPlan,
    VisualDirector,
    VisualGoal,
)


class VisualDirectorTests(unittest.TestCase):
    def test_octopus_plan_requires_octopus_not_generic_ocean(self) -> None:
        plan = VisualDirector().plan(
            topic="Why Octopuses Are So Intelligent",
            segments=[
                {
                    "narration": "An octopus can solve puzzles and open jars.",
                    "broll": "octopus opening jar",
                    "broll_queries": ["ocean animal"],
                }
            ],
        )

        intent = plan.intent_for_index(0)

        self.assertEqual(plan.domain_id, "octopus_intelligence")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.primary_subject, "octopus")
        self.assertIn("octopus", " ".join(intent.search_queries).lower())
        self.assertIn("empty ocean", intent.negative_terms)

    def test_roman_aqueduct_expands_named_entities(self) -> None:
        plan = VisualDirector().plan(
            topic="How Roman Aqueducts Changed Civilization",
            segments=[
                {
                    "narration": "Massive stone arches carried water across valleys.",
                    "broll": "stone bricks texture detail",
                    "broll_queries": ["stone bricks texture detail"],
                }
            ],
        )

        queries = " | ".join(plan.intent_for_index(0).search_queries)

        self.assertEqual(plan.domain_id, "roman_aqueducts")
        self.assertEqual(plan.intent_for_index(0).search_queries[0], "Roman aqueduct bridge")
        self.assertIn("Roman aqueduct", queries)
        self.assertIn("Pont du Gard", queries)
        self.assertNotEqual(plan.intent_for_index(0).search_queries[0], "stone bricks texture detail")

    def test_bee_communication_prioritizes_hive_and_waggle_dance(self) -> None:
        plan = VisualDirector().plan(
            topic="How Bees Communicate Through Dancing",
            segments=[
                {
                    "narration": "A honeybee dances inside the hive to point others toward food.",
                    "broll": "bee on flower",
                    "broll_queries": ["bee on flower"],
                }
            ],
        )

        queries = " ".join(plan.intent_for_index(0).search_queries).lower()

        self.assertEqual(plan.domain_id, "bee_communication")
        self.assertEqual(plan.intent_for_index(0).search_queries[0], "bee waggle dance")
        self.assertIn("waggle dance", queries)
        self.assertIn("hive", queries)
        self.assertIn("flower only", plan.avoid_terms)

    def test_volcano_plan_rejects_ocean_generic_identity(self) -> None:
        plan = VisualDirector().plan(
            topic="Why Volcanoes Create New Land",
            segments=[
                {
                    "narration": "Cooling lava hardens into brand new land.",
                    "broll": "coastline ocean smoke",
                    "broll_queries": ["coastline ocean smoke"],
                }
            ],
        )

        queries = " ".join(plan.intent_for_index(0).search_queries).lower()

        self.assertEqual(plan.domain_id, "volcanic_land")
        self.assertEqual(plan.intent_for_index(0).search_queries[0], "volcano eruption lava flow")
        self.assertIn("lava", queries)
        self.assertIn("volcano", queries)
        self.assertIn("generic ocean", plan.avoid_terms)

    def test_metaphor_terms_do_not_become_visual_queries(self) -> None:
        plan = VisualDirector().plan(
            topic="Why Octopuses Are So Intelligent",
            segments=[
                {
                    "narration": "They are like intelligent aliens on our planet.",
                    "broll": "alien looking octopus",
                    "broll_queries": ["alien looking octopus"],
                }
            ],
        )

        queries = " ".join(plan.intent_for_index(0).search_queries).lower()

        self.assertIn("octopus", queries)
        self.assertNotIn("alien", queries)

    def test_visual_goal_is_present_for_every_intent(self) -> None:
        plan = VisualDirector().plan(
            topic="How Volcanoes Create New Land",
            segments=[
                {"narration": "Lava can build land overnight.", "broll": "lava flow"},
                {"narration": "Because it cools into rock, the coastline grows.", "broll": "cooling lava"},
                {"narration": "Follow Wonders of the Nature for more.", "broll": "volcano landscape"},
            ],
        )

        self.assertEqual(plan.intents[0].visual_goal, VisualGoal.REVEAL)
        self.assertEqual(plan.intents[1].visual_goal, VisualGoal.EXPLAIN)
        self.assertEqual(plan.intents[2].visual_goal, VisualGoal.TRANSITION)
        self.assertEqual(plan.intents[0].media_mode.value, MediaMode.REVEAL.value)
        self.assertEqual(plan.intents[1].media_mode.value, MediaMode.EXPLAIN.value)
        self.assertEqual(plan.intents[2].media_mode.value, MediaMode.CTA.value)

    def test_style_rules_are_global_documentary_rules(self) -> None:
        plan = VisualDirector().plan(
            topic="How Roman Aqueducts Changed Civilization",
            segments=[{"narration": "Roman arches carried water.", "broll": "Roman aqueduct arches"}],
        )

        self.assertIn("wide", plan.style_rules.shot_variety)
        self.assertTrue(plan.style_rules.explainer_limits["black_cards_are_final_fallback"])
        self.assertIn("alternate_wide_and_close", plan.style_rules.framing_diversity)
        self.assertIn("hook_exact_subject", plan.style_rules.visual_rhythm)

    def test_query_budget_bounds_each_tier(self) -> None:
        plan = VisualDirector(query_budget={QueryTier.EXACT.value: 1, QueryTier.ENTITY.value: 1}).plan(
            topic="How Roman Aqueducts Changed Civilization",
            segments=[{"narration": "Roman aqueduct arches carried water.", "broll": "Roman aqueduct arches"}],
        )

        intent = plan.intent_for_index(0)
        budgets = {tier.tier: len(tier.queries) for tier in intent.query_tiers}

        self.assertLessEqual(budgets[QueryTier.EXACT], 1)
        self.assertLessEqual(budgets[QueryTier.ENTITY], 1)

    def test_knowledge_pack_store_loads_json_packs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packs.json"
            path.write_text(
                """
                {
                  "domains": [{
                    "id": "test_domain",
                    "label": "Test",
                    "trigger_terms": ["test"],
                    "primary_subject": "test subject",
                    "required_entities": ["test subject"],
                    "named_entities": ["named test"],
                    "visual_identity": ["test visuals"],
                    "avoid_terms": ["bad visual"],
                    "exact_queries": ["test exact"],
                    "mechanism_queries": ["test mechanism"],
                    "context_queries": ["test context"],
                    "fallback_queries": ["test fallback"]
                  }]
                }
                """,
                encoding="utf-8",
            )

            plan = VisualDirector(KnowledgePackStore(path)).plan(
                topic="test topic",
                segments=[{"narration": "test narration", "broll": "generic"}],
            )

        self.assertEqual(plan.domain_id, "test_domain")
        self.assertEqual(plan.required_subjects, ("test subject",))

    def test_shot_plan_round_trip_preserves_visual_goal(self) -> None:
        plan = VisualDirector().plan(
            topic="How Bees Communicate Through Dancing",
            segments=[{"narration": "Bees reveal directions with a waggle dance.", "broll": "bee dance"}],
        )

        restored = ShotPlan.from_dict(plan.to_dict())

        self.assertEqual(restored.domain_id, plan.domain_id)
        self.assertEqual(restored.intents[0].visual_goal, plan.intents[0].visual_goal)
        self.assertEqual(restored.intents[0].media_mode, plan.intents[0].media_mode)
        self.assertEqual(restored.intents[0].search_queries, plan.intents[0].search_queries)

    def test_scene_specific_queries_are_not_overridden_by_domain_defaults(self) -> None:
        plan = VisualDirector().plan(
            topic="mind-blowing facts about our solar system",
            segments=[
                {
                    "narration": "Jupiter acted like a giant vacuum cleaner, pulling dangerous rocks away from Earth.",
                    "broll": "jupiter planet rotating",
                },
                {
                    "narration": "A rogue Mars-sized world smashed into Earth, creating our massive protective moon.",
                    "broll": "planet colliding with earth",
                },
                {
                    "narration": "Earth's magnetic field catches solar wind and charged particles.",
                    "broll": "earth magnetosphere solar wind",
                },
                {
                    "narration": "Those particles light up the upper atmosphere as auroras.",
                    "broll": "aurora borealis timelapse",
                },
            ],
        )

        first_queries = [intent.search_queries[0].lower() for intent in plan.intents]

        self.assertEqual(len(first_queries), len(set(first_queries)))
        self.assertIn("jupiter", first_queries[0])
        self.assertIn("planet colliding", first_queries[1])
        self.assertIn("magnetosphere", first_queries[2])
        self.assertIn("aurora", first_queries[3])

    def test_adjacent_duplicate_queries_are_diversified_when_alternates_exist(self) -> None:
        plan = VisualDirector().plan(
            topic="How Roman Aqueducts Changed Civilization",
            segments=[
                {
                    "narration": "Roman arches carried water across valleys.",
                    "broll": "Roman aqueduct bridge",
                    "broll_queries": ["Pont du Gard aqueduct"],
                },
                {
                    "narration": "Stone channels used gravity to move water without pumps.",
                    "broll": "Roman aqueduct bridge",
                    "broll_queries": ["Roman water channel"],
                },
            ],
        )

        first_queries = [intent.search_queries[0] for intent in plan.intents]

        self.assertNotEqual(first_queries[0], first_queries[1])

    def test_single_storm_reference_does_not_hijack_wildlife_topic(self) -> None:
        plan = VisualDirector().plan(
            topic="Why Sharks Have Survived for 400 Million Years",
            segments=[
                {
                    "narration": "Sharks survived ancient extinctions, giant waves, and storms.",
                    "broll": "prehistoric shark swimming ocean",
                },
                {
                    "narration": "Their skin is covered in tiny tooth-like scales.",
                    "broll": "shark skin close up",
                },
                {
                    "narration": "Electroreception helps them detect prey in dark water.",
                    "broll": "shark swimming underwater close up",
                },
            ],
        )

        queries = " ".join(query for intent in plan.intents for query in intent.search_queries).lower()

        self.assertNotEqual(plan.domain_id, "lightning_weather")
        self.assertNotIn("lightning strike", queries)
        self.assertNotIn("thunderstorm", queries)


if __name__ == "__main__":
    unittest.main()
