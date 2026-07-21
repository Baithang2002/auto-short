from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.unit import _path  # noqa: F401

from autovideo.media import (
    SceneConstraintConfig,
    SceneConstraintPlanner,
    SceneConstraintReport,
    SceneEntity,
    SemanticVisualQueryEngine,
)


def _intent(
    entity: str,
    *,
    narration: str,
    action: str,
    environment: str,
    broll: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        scene_index=0,
        primary_subject=entity,
        scene_entity=SceneEntity(entity, "primary_subject", aliases=(entity,)),
        action=action,
        environment=environment,
        shot_type="wide",
        documentary_role="process",
        visual_goal="show",
        diagnostics={"narration": narration},
        search_queries=(broll,),
    )


class SceneConstraintTests(unittest.TestCase):
    def test_underwater_volcano_queries_preserve_subject_environment_and_mechanism(self) -> None:
        intent = _intent(
            "volcano",
            narration="An underwater volcano erupts through the dark ocean.",
            action="eruption",
            environment="underwater",
            broll="underwater volcano eruption wide",
        )
        plan = SimpleNamespace(intents=(intent,), primary_subject="volcano")
        constraints = SceneConstraintPlanner().plan(
            documentary_topic="How Underwater Volcanoes Create New Land",
            shot_plan=plan,
        )
        scene = constraints.scene_for_index(0)
        self.assertTrue({"subject", "environment", "mechanism", "framing"}.issubset(
            {item.kind for item in scene.constraints}
        ))

        report = SemanticVisualQueryEngine().plan(
            documentary_topic="",
            shot_plan=plan,
            constraint_report=constraints,
        )
        queries = report.scene_for_index(0).provider_queries
        self.assertTrue(queries)
        self.assertTrue(all("volcan" in query.lower() for query in queries))
        underwater_terms = (
            "underwater",
            "submarine",
            "subsea",
            "deep ocean",
            "ocean floor",
            "undersea",
        )
        eruption_terms = ("eruption", "erupting", "lava plume", "volcanic vent")
        self.assertTrue(all(any(term in query.lower() for term in underwater_terms) for query in queries))
        self.assertTrue(all(any(term in query.lower() for term in eruption_terms) for query in queries))
        self.assertTrue(all(
            all(any(term in query.lower() for term in underwater_terms)
                for query in provider_queries)
            for provider_queries in report.scene_for_index(0).provider_variants.values()
        ))

    def test_waggle_dance_is_not_reduced_to_generic_bee(self) -> None:
        intent = _intent(
            "honeybee",
            narration="A honeybee uses a waggle dance to tell the colony where food is.",
            action="waggle dance",
            environment="hive",
            broll="honeybee waggle dance hive",
        )
        constraints = SceneConstraintPlanner().plan(
            documentary_topic="How Bees Communicate Through Dancing",
            shot_plan=SimpleNamespace(intents=(intent,), primary_subject="honeybee"),
        )
        scene = constraints.scene_for_index(0)
        accepted, rejected = scene.filter_queries(("honeybee", "honeybee waggle dance hive wide"))
        self.assertEqual(("honeybee waggle dance hive wide",), accepted)
        self.assertEqual("missing mandatory visual constraints", rejected[0]["reason"])

    def test_green_sky_and_supercell_are_preserved(self) -> None:
        intent = _intent(
            "storm",
            narration="A rotating supercell can turn the sky an eerie green.",
            action="storm",
            environment="storm",
            broll="green sky supercell storm",
        )
        constraints = SceneConstraintPlanner().plan(
            documentary_topic="The Extreme Storm That Creates a Green Sky",
            shot_plan=SimpleNamespace(intents=(intent,), primary_subject="storm"),
        )
        scene = constraints.scene_for_index(0)
        self.assertIn("green sky", [item.canonical_term for item in scene.constraints])
        self.assertIn("supercell", [item.canonical_term for item in scene.constraints])
        accepted, _ = scene.filter_queries(("storm", "green sky supercell storm wide"))
        self.assertEqual(("green sky supercell storm wide",), accepted)

    def test_disabled_mode_keeps_existing_query_behavior(self) -> None:
        intent = _intent(
            "volcano",
            narration="An underwater volcano erupts.",
            action="eruption",
            environment="underwater",
            broll="volcano lava",
        )
        report = SceneConstraintPlanner(SceneConstraintConfig(enabled=False)).plan(
            documentary_topic="Underwater Volcanoes",
            shot_plan=SimpleNamespace(intents=(intent,), primary_subject="volcano"),
        )
        scene = report.scene_for_index(0)
        self.assertEqual((), scene.constraints)
        self.assertEqual(("volcano lava",), scene.query_seeds)

    def test_report_round_trips(self) -> None:
        intent = _intent(
            "camel",
            narration="A camel walks across desert dunes.",
            action="walking",
            environment="desert",
            broll="camel walking desert dunes",
        )
        report = SceneConstraintPlanner().plan(
            documentary_topic="How Camels Survive the Desert",
            shot_plan=SimpleNamespace(intents=(intent,), primary_subject="camel"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = report.write_json(Path(directory) / "scene_constraint_report.json")
            restored = SceneConstraintReport.from_dict(
                __import__("json").loads(path.read_text(encoding="utf-8"))
            )
        self.assertEqual("camel", restored.scene_for_index(0).canonical_entity)
        self.assertEqual(report.scene_for_index(0).query_seeds, restored.scene_for_index(0).query_seeds)


if __name__ == "__main__":
    unittest.main()
