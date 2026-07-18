from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.unit import _path  # noqa: F401

from autovideo.media import (
    CanonicalEntityResolverConfig,
    CanonicalEntityReport,
    CanonicalSceneEntityResolver,
    SceneEntity,
)


def _intent(
    index: int,
    entity: str,
    *,
    narration: str = "",
    action: str = "",
    environment: str = "",
    role: str = "overview",
) -> SimpleNamespace:
    return SimpleNamespace(
        scene_index=index,
        scene_entity=SceneEntity(entity, "primary_subject", required_terms=(entity,)),
        primary_subject=entity,
        required_entities=(),
        action=action,
        environment=environment,
        shot_type="wide",
        documentary_role=role,
        diagnostics={"narration": narration},
    )


def _plan(topic: str, *intents: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(intents=intents, primary_subject=topic)


class CanonicalSceneEntityResolverTests(unittest.TestCase):
    def test_removes_camel_documentary_title_framing(self) -> None:
        topic = "How Camels Survive the World's Harshest Deserts"
        report = CanonicalSceneEntityResolver().resolve(
            documentary_topic=topic,
            shot_plan=_plan(topic, _intent(0, topic, environment="desert")),
        )

        scene = report.scene_for_index(0)
        self.assertIsNotNone(scene)
        self.assertEqual("camel", scene.canonical_entity)
        self.assertEqual(("camel",), scene.resolved_entity.required_terms)
        self.assertNotIn("world", scene.canonical_entity)

    def test_resolves_rainforest_scene_specific_entities_before_topic(self) -> None:
        topic = "The World's Largest Rainforests"
        report = CanonicalSceneEntityResolver().resolve(
            documentary_topic=topic,
            shot_plan=_plan(
                topic,
                _intent(0, topic, narration="A rainforest covers the horizon."),
                _intent(1, "rainforest canopy", narration="Leaves form a canopy."),
                _intent(2, "Amazon River", narration="The Amazon River feeds the forest."),
                _intent(3, "deforestation", narration="Clearing destroys forest."),
            ),
        )

        self.assertEqual("rainforest", report.scene_for_index(0).canonical_entity)
        self.assertEqual("rainforest canopy", report.scene_for_index(1).canonical_entity)
        self.assertEqual("Amazon River", report.scene_for_index(2).canonical_entity)
        self.assertEqual("deforestation", report.scene_for_index(3).canonical_entity)

    def test_resolves_known_historical_and_environmental_topics(self) -> None:
        resolver = CanonicalSceneEntityResolver()
        cases = {
            "Why Volcanoes Erupt": "volcano",
            "The Roman Empire": "roman ruins",
            "Life in the Deep Ocean": "deep ocean",
        }
        for topic, expected in cases.items():
            with self.subTest(topic=topic):
                report = resolver.resolve(
                    documentary_topic=topic,
                    shot_plan=_plan(topic, _intent(0, topic)),
                )
                self.assertEqual(expected, report.scene_for_index(0).canonical_entity)

    def test_disabled_resolver_preserves_existing_entity(self) -> None:
        topic = "How Camels Survive the World's Harshest Deserts"
        report = CanonicalSceneEntityResolver(
            CanonicalEntityResolverConfig(enabled=False)
        ).resolve(documentary_topic=topic, shot_plan=_plan(topic, _intent(0, topic)))

        self.assertEqual(topic, report.scene_for_index(0).canonical_entity)

    def test_report_round_trips(self) -> None:
        topic = "The World's Largest Rainforests"
        report = CanonicalSceneEntityResolver().resolve(
            documentary_topic=topic,
            shot_plan=_plan(topic, _intent(0, topic)),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = report.write_json(Path(directory) / "canonical_entity_report.json")
            restored = CanonicalEntityReport.from_dict(
                __import__("json").loads(path.read_text(encoding="utf-8"))
            )

        self.assertEqual("rainforest", restored.scene_for_index(0).canonical_entity)
        self.assertEqual(topic, restored.documentary_topic)


if __name__ == "__main__":
    unittest.main()
