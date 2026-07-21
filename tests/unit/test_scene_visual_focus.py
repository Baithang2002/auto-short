from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.unit import _path  # noqa: F401

from autovideo.media import (
    KnowledgePackStore,
    MediaMode,
    SceneConstraintPlanner,
    SceneEntity,
    SceneVisualFocus,
    SceneVisualFocusPlanner,
    SceneVisualFocusReport,
    SemanticVisualQueryEngine,
    ShotPlan,
    SubjectContinuityEngine,
    VisualFocusRole,
)


def _intent(index: int, narration: str, broll: str) -> SimpleNamespace:
    return SimpleNamespace(
        scene_index=index,
        action=broll,
        environment="desert",
        diagnostics={"narration": narration},
        search_queries=(broll,),
        required_entities=("lightning", "fulgurite"),
        scene_entity=SceneEntity("lightning", "primary_subject", aliases=("lightning",)),
        media_mode=MediaMode.SHOW,
        visual_goal="show",
    )


def _domain_plan(
    domain_id: str,
    anchor: str,
    intent: SimpleNamespace,
) -> SimpleNamespace:
    return SimpleNamespace(
        domain_id=domain_id,
        primary_subject=anchor,
        intents=(intent,),
    )


class SceneVisualFocusTests(unittest.TestCase):
    def test_knowledge_pack_promotes_fulgurite_over_documentary_anchor(self) -> None:
        plan = SimpleNamespace(
            domain_id="lightning_weather",
            primary_subject="lightning",
            intents=(
                _intent(
                    0,
                    "A lightning strike turns desert sand into a hollow fulgurite tube.",
                    "hollow tube lightning desert",
                ),
            ),
        )

        report = SceneVisualFocusPlanner().plan(
            documentary_topic="How Lightning Turns Sand to Glass",
            shot_plan=plan,
            knowledge_domains=KnowledgePackStore().load(),
        )

        focus = report.scene_for_index(0)
        self.assertEqual("fulgurite tube", focus.required_visual_entity)
        self.assertEqual(VisualFocusRole.RESULT, focus.role)
        self.assertFalse(focus.requires_documentary_anchor)
        self.assertEqual("fulgurite tube", focus.to_scene_entity().canonical_entity)

        focused_intent = _intent(
            0,
            "A lightning strike turns desert sand into a hollow fulgurite tube.",
            "hollow tube lightning desert",
        )
        focused_intent.primary_subject = focus.required_visual_entity
        focused_intent.scene_entity = focus.to_scene_entity(focused_intent.scene_entity)
        focused_plan = SimpleNamespace(intents=(focused_intent,), primary_subject="lightning")
        constraints = SceneConstraintPlanner().plan(
            documentary_topic="How Lightning Turns Sand to Glass",
            shot_plan=focused_plan,
        )
        queries = SemanticVisualQueryEngine().plan(
            documentary_topic="",
            shot_plan=focused_plan,
            constraint_report=constraints,
        ).scene_for_index(0).provider_queries
        self.assertTrue(queries)
        self.assertTrue(all(
            any(term in query for term in (
                "fulgurite tube",
                "hollow fulgurite",
                "lightning glass tube",
            ))
            for query in queries
        ))

    def test_unmatched_scene_retains_anchor_as_subject_focus(self) -> None:
        plan = _domain_plan(
            "lightning_weather",
            "lightning",
            _intent(0, "Lightning forks across a dark storm sky.", "lightning storm"),
        )

        focus = SceneVisualFocusPlanner().plan(
            documentary_topic="Why Lightning Strikes",
            shot_plan=plan,
            knowledge_domains=KnowledgePackStore().load(),
        ).scene_for_index(0)

        self.assertEqual("lightning", focus.required_visual_entity)
        self.assertEqual(VisualFocusRole.SUBJECT, focus.role)
        self.assertTrue(focus.requires_documentary_anchor)

    def test_context_never_replaces_the_primary_subject(self) -> None:
        intent = _intent(
            0,
            "A Greenland shark survives in the freezing Arctic ocean.",
            "Greenland shark underwater ocean",
        )
        intent.environment = "Arctic ocean"
        intent.required_entities = ("Greenland shark", "shark", "ocean", "Arctic")
        intent.scene_entity = SceneEntity("Greenland shark", "primary_subject")
        focus = SceneVisualFocusPlanner().plan(
            documentary_topic="The Shark That Lives Four Centuries",
            shot_plan=_domain_plan("shark_survival", "Greenland shark", intent),
            knowledge_domains=KnowledgePackStore().load(),
        ).scene_for_index(0)

        self.assertEqual("Greenland shark", focus.required_visual_entity)
        self.assertTrue(focus.requires_documentary_anchor)
        self.assertIn("context: ocean, Arctic", focus.reason)

    def test_named_process_can_replace_anchor_across_domains(self) -> None:
        intent = _intent(
            0,
            "A honeybee uses a waggle dance to guide the colony to food.",
            "honeybee waggle dance hive",
        )
        intent.environment = "hive"
        intent.required_entities = ("honeybee", "hive")
        intent.scene_entity = SceneEntity("honeybee", "primary_subject")
        focus = SceneVisualFocusPlanner().plan(
            documentary_topic="How Bees Communicate Through Dancing",
            shot_plan=_domain_plan("bee_communication", "honeybee", intent),
            knowledge_domains=KnowledgePackStore().load(),
        ).scene_for_index(0)

        self.assertEqual("waggle dance", focus.required_visual_entity)
        self.assertEqual(VisualFocusRole.PROCESS, focus.role)
        self.assertFalse(focus.requires_documentary_anchor)

    def test_result_focus_is_excluded_from_anchor_continuity_metric(self) -> None:
        plan = ShotPlan.from_dict({
            "topic": "How Lightning Turns Sand to Glass",
            "primary_subject": "lightning",
            "subject_persistence_target": 0.85,
            "intents": [],
        })
        assets = [
            SimpleNamespace(metadata={
                "selection": {"media_mode": "show", "subject_visible": True},
            }),
            SimpleNamespace(metadata={
                "selection": {"media_mode": "show", "subject_visible": False},
            }),
        ]
        focus_report = SceneVisualFocusReport(
            documentary_topic=plan.topic,
            primary_subject="lightning",
            scenes=(
                SceneVisualFocus(0, "lightning", "lightning", VisualFocusRole.SUBJECT),
                SceneVisualFocus(
                    1,
                    "lightning",
                    "fulgurite",
                    VisualFocusRole.RESULT,
                    requires_documentary_anchor=False,
                ),
            ),
        )

        report = SubjectContinuityEngine().report_from_assets(
            plan,
            assets,
            scene_visual_focus_report=focus_report,
        )

        self.assertEqual(1.0, report.subject_visible_percentage)
        self.assertEqual(1, len(report.scene_focus_exemptions))


if __name__ == "__main__":
    unittest.main()
