import unittest

from autovideo.media import (
    DocumentaryMode,
    EditorialCanonBuilder,
    KnowledgePackStore,
    SubjectContinuityEngine,
    VisualDirector,
)


class EditorialCanonTests(unittest.TestCase):
    def test_octopus_title_locks_subject_even_when_sharks_are_supporting(self) -> None:
        segments = [
            {
                "narration": "An octopus can become invisible before hungry sharks arrive.",
                "broll": "shark predator underwater",
            },
            {
                "narration": "Then it squeezes through a narrow underwater gap.",
                "broll": "octopus squeezing through rock",
            },
        ]

        canon, lock_report, _scene_report, domain_report = EditorialCanonBuilder().build(
            topic="The Octopus That Becomes Anything Underwater",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        plan = VisualDirector().plan(
            topic="The Octopus That Becomes Anything Underwater",
            segments=segments,
            editorial_canon=canon,
        )
        plan = SubjectContinuityEngine().apply(plan, segments=segments, editorial_canon=canon)

        self.assertEqual(canon.primary_subject, "octopus")
        self.assertEqual(plan.primary_subject, "octopus")
        self.assertTrue(all(intent.primary_subject == "octopus" for intent in plan.intents))
        self.assertIn("shark", " ".join(canon.forbidden_primary_subjects).lower())
        self.assertIn("grilled", plan.intents[1].negative_terms)
        self.assertTrue(lock_report.attempted_overrides)
        self.assertTrue(any(item["primary_subject"] == "shark" for item in domain_report["domain_scores"]))

    def test_antarctica_place_documentary_does_not_become_penguin_documentary(self) -> None:
        segments = [
            {"narration": "The largest desert on Earth is frozen.", "broll": "penguins on ice"},
            {"narration": "A map reveals it covers Antarctica.", "broll": "Antarctica world map"},
            {"narration": "Ice sheets hold the secret.", "broll": "Antarctica ice sheet"},
        ]

        canon, _lock_report, scene_report, _domain_report = EditorialCanonBuilder().build(
            topic="The World's Largest Desert Is Frozen",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        plan = VisualDirector().plan(
            topic="The World's Largest Desert Is Frozen",
            segments=segments,
            editorial_canon=canon,
        )

        self.assertEqual(canon.primary_subject, "Antarctica")
        self.assertEqual(canon.documentary_mode, DocumentaryMode.PLACE)
        self.assertIn("penguin", " ".join(canon.forbidden_primary_subjects).lower())
        self.assertEqual(scene_report["expected_scene_roles"][1], "map")
        self.assertEqual(plan.intents[1].documentary_role, "map")
        self.assertIn("Antarctica", plan.intents[1].search_queries[0])
        self.assertIn("map", plan.intents[1].search_queries[0])

    def test_seven_wonders_preserves_multi_subject_identity(self) -> None:
        segments = [
            {"narration": "Seven wonders pull us across the world.", "broll": "ancient ruins"},
            {"narration": "A map shows how far apart they are.", "broll": "world map"},
            {"narration": "Some arches survived, but this is not only Rome.", "broll": "Roman aqueduct"},
        ]

        canon, lock_report, scene_report, _domain_report = EditorialCanonBuilder().build(
            topic="Seven Wonders of the World",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        plan = VisualDirector().plan(
            topic="Seven Wonders of the World",
            segments=segments,
            editorial_canon=canon,
        )

        self.assertEqual(canon.primary_subject, "Seven Wonders of the World")
        self.assertEqual(canon.documentary_mode, DocumentaryMode.MULTI_SUBJECT)
        self.assertIn("Great Wall of China", canon.secondary_subjects)
        self.assertIn("Roman aqueduct", canon.forbidden_primary_subjects)
        self.assertEqual(scene_report["expected_scene_roles"][1], "map")
        self.assertEqual(plan.primary_subject, "Seven Wonders of the World")
        self.assertEqual(plan.intents[1].scene_entity.canonical_entity, "World Map")
        self.assertIn("World Map", plan.intents[1].search_queries[0])
        self.assertTrue(lock_report.attempted_overrides)

    def test_butterfly_title_locks_subject_instead_of_full_framed_title(self) -> None:
        segments = [
            {"narration": "A butterfly hides a tiny secret in its wings.", "broll": "honeybee on flower"},
            {"narration": "Its wing scales scatter light like living glass.", "broll": "butterfly wing macro"},
        ]

        canon, _lock_report, _scene_report, _domain_report = EditorialCanonBuilder().build(
            topic="The Hidden Secret of Garden Butterflies",
            segments=segments,
            knowledge_domains=KnowledgePackStore().load(),
        )
        plan = VisualDirector().plan(
            topic="The Hidden Secret of Garden Butterflies",
            segments=segments,
            editorial_canon=canon,
        )

        self.assertEqual(canon.primary_subject, "butterfly")
        self.assertNotEqual(canon.primary_subject, "The Hidden Secret of Garden Butterflies")
        self.assertIn("honeybee", " ".join(canon.forbidden_primary_subjects).lower())
        self.assertEqual(plan.intents[0].scene_entity.canonical_entity, "butterfly")
        self.assertIn("butterfly", plan.intents[0].search_queries[0].lower())
        self.assertIn("honeybee", plan.intents[0].negative_terms)


if __name__ == "__main__":
    unittest.main()
