import unittest

from autovideo.media import (
    EditorialCanonBuilder,
    KnowledgePackStore,
    SceneEntityPlanner,
    VisualDirector,
    isolated_query_candidates,
)


class SceneEntityPlannerTests(unittest.TestCase):
    def test_seven_wonders_assigns_one_entity_per_scene(self) -> None:
        segments = [
            {"narration": "These wonders span the world.", "broll": "seven wonders world map"},
            {"narration": "The Great Pyramid of Giza turned stone into a monument.", "broll": "Great Pyramid desert wide"},
            {"narration": "The Great Wall stretches across mountains.", "broll": "Great Wall broken marble column"},
            {"narration": "Petra was carved into desert cliffs.", "broll": "Petra Roman aqueduct"},
            {"narration": "The Colosseum still dominates Rome.", "broll": "Colosseum exterior"},
            {"narration": "Machu Picchu rises above the clouds.", "broll": "Machu Picchu mountain ruins"},
            {"narration": "The Taj Mahal turns symmetry into emotion.", "broll": "Taj Mahal reflecting pool"},
        ]
        canon, _lock, _scene, _domain = EditorialCanonBuilder().build(
            topic="Seven Wonders of the World",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        entity_plan = SceneEntityPlanner().plan(editorial_canon=canon, segments=segments)

        self.assertEqual(entity_plan.entities[0].canonical_entity, "World Map")
        self.assertEqual(entity_plan.entities[1].canonical_entity, "Great Pyramid of Giza")
        self.assertEqual(entity_plan.entities[2].canonical_entity, "Great Wall of China")
        self.assertEqual(entity_plan.entities[3].canonical_entity, "Petra")
        self.assertEqual(entity_plan.entities[4].canonical_entity, "Colosseum")
        self.assertEqual(entity_plan.entities[5].canonical_entity, "Machu Picchu")
        self.assertEqual(entity_plan.entities[6].canonical_entity, "Taj Mahal")

    def test_visual_director_rejects_mixed_entity_queries(self) -> None:
        segments = [
            {"narration": "These wonders span the world.", "broll": "seven wonders world map"},
            {"narration": "The Great Wall stretches across mountains.", "broll": "Great Wall broken marble column"},
            {"narration": "Petra was carved into desert cliffs.", "broll": "Petra Roman aqueduct"},
        ]
        canon, _lock, _scene, _domain = EditorialCanonBuilder().build(
            topic="Seven Wonders of the World",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        entity_plan = SceneEntityPlanner().plan(editorial_canon=canon, segments=segments)
        plan = VisualDirector().plan(
            topic="Seven Wonders of the World",
            segments=segments,
            editorial_canon=canon,
            scene_entity_plan=entity_plan,
        )

        great_wall = plan.intent_for_index(1)
        petra = plan.intent_for_index(2)

        self.assertEqual(great_wall.scene_entity.canonical_entity, "Great Wall of China")
        self.assertNotIn("marble column", " ".join(great_wall.search_queries).lower())
        self.assertTrue(great_wall.diagnostics["query_isolation_rejections"])
        self.assertEqual(petra.scene_entity.canonical_entity, "Petra")
        self.assertNotIn("roman aqueduct", " ".join(petra.search_queries).lower())
        self.assertTrue(petra.diagnostics["query_isolation_rejections"])

    def test_greenland_shark_remains_exact_species(self) -> None:
        segments = [
            {"narration": "A Greenland shark can live for centuries.", "broll": "reef shark underwater"},
            {"narration": "It moves slowly through cold Arctic water.", "broll": "Greenland shark deep sea"},
        ]
        canon, _lock, _scene, _domain = EditorialCanonBuilder().build(
            topic="Why the Greenland Shark Lives So Long",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        plan = VisualDirector().plan(
            topic="Why the Greenland Shark Lives So Long",
            segments=segments,
            editorial_canon=canon,
        )

        self.assertEqual(canon.primary_subject, "Greenland shark")
        self.assertTrue(all(intent.scene_entity.canonical_entity == "Greenland shark" for intent in plan.intents))
        self.assertNotIn("reef shark", " ".join(plan.intents[0].search_queries).lower())
        self.assertIn("Greenland shark", plan.intents[0].search_queries[0])
        self.assertIn("reef shark", plan.intents[0].negative_terms)

    def test_isolated_query_candidates_filters_unrelated_entities(self) -> None:
        entity_plan = SceneEntityPlanner().plan(
            editorial_canon=EditorialCanonBuilder().build(
                topic="Seven Wonders of the World",
                segments=[
                    {"narration": "map", "broll": "map"},
                    {"narration": "wall", "broll": "wall"},
                ],
                knowledge_domains=KnowledgePackStore().load(),
            )[0],
            segments=[
                {"narration": "map", "broll": "map"},
                {"narration": "The Great Wall crosses China.", "broll": "Great Wall aerial"},
            ],
        )
        accepted, rejected = isolated_query_candidates(
            scene_entity=entity_plan.entities[1],
            queries=[
                "Great Wall of China aerial documentary",
                "Great Wall of China broken marble column",
                "Petra Roman aqueduct",
            ],
            all_entities=entity_plan.entities,
        )

        self.assertIn("Great Wall of China aerial documentary", accepted)
        self.assertNotIn("Great Wall of China broken marble column", accepted)
        self.assertNotIn("Petra Roman aqueduct", accepted)
        self.assertEqual(len(rejected), 2)


if __name__ == "__main__":
    unittest.main()
