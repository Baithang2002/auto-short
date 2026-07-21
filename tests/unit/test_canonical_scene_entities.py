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

    def test_normalizes_pyramid_title_and_scene_entities(self) -> None:
        topic = "How Ancient Egyptians Built Stone Pyramids"
        report = CanonicalSceneEntityResolver().resolve(
            documentary_topic=topic,
            shot_plan=_plan(
                topic,
                _intent(0, topic, narration="Workers moved massive stone blocks."),
                _intent(1, topic, narration="Water made wet sand easier to pull across."),
                _intent(2, topic, narration="Wall paintings show a wooden sled."),
                _intent(3, topic, narration="Engineers tested a miniature sled in a laboratory."),
            ),
        )

        self.assertEqual("Egyptian pyramids", report.canonical_documentary_entity)
        self.assertEqual("ancient Egyptian stone blocks", report.scene_for_index(0).canonical_entity)
        self.assertEqual("wet sand friction", report.scene_for_index(1).canonical_entity)
        self.assertEqual("Egyptian wall painting", report.scene_for_index(2).canonical_entity)
        self.assertEqual("laboratory sled experiment", report.scene_for_index(3).canonical_entity)
        self.assertTrue(all(
            scene.canonical_entity.casefold() != topic.casefold()
            for scene in report.scenes
        ))

    def test_instructional_title_prefixes_never_become_retrieval_entities(self) -> None:
        resolver = CanonicalSceneEntityResolver()
        topics = (
            "How Coral Reefs Grow",
            "Why Coral Reefs Matter",
            "When Coral Reefs Bleach",
            "Where Coral Reefs Thrive",
            "The Secret of Coral Reefs",
            "Inside Coral Reefs",
            "The Truth About Coral Reefs",
        )

        for topic in topics:
            with self.subTest(topic=topic):
                report = resolver.resolve(
                    documentary_topic=topic,
                    shot_plan=_plan(topic, _intent(0, topic)),
                )
                entity = report.scene_for_index(0).canonical_entity
                self.assertNotEqual(topic.casefold(), entity.casefold())
                self.assertFalse(entity.startswith((
                    "how ", "why ", "when ", "where ", "the secret of ",
                    "inside ", "the truth about ",
                )))
                self.assertIn("coral reefs", entity)

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
        self.assertEqual("rainforest", restored.canonical_documentary_entity)


if __name__ == "__main__":
    unittest.main()
