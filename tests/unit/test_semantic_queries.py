from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from tests.unit import _path  # noqa: F401

from autovideo.media import (
    CanonicalSceneEntityResolver,
    SceneEntity,
    SemanticQueryConfig,
    SemanticVisualQueryEngine,
)


def _intent(entity: str) -> SimpleNamespace:
    return SimpleNamespace(
        scene_index=0,
        primary_subject=entity,
        scene_entity=SceneEntity(entity, "primary_subject", required_terms=(entity,)),
        action="walking",
        environment="desert",
        shot_type="wide",
        documentary_role="overview",
        search_queries=("camel desert walking",),
    )


class SemanticVisualQueryEngineTests(unittest.TestCase):
    def test_configuration_loads_from_environment(self) -> None:
        config = SemanticQueryConfig.from_env({
            "AUTO_VIDEO_SEMANTIC_QUERY_ENGINE_ENABLED": "false",
            "AUTO_VIDEO_SEMANTIC_QUERY_MAX_PER_SCENE": "6",
        })

        self.assertFalse(config.enabled)
        self.assertEqual(6, config.max_queries_per_scene)

    def test_uses_canonical_entity_not_documentary_title(self) -> None:
        topic = "How Camels Survive the World's Harshest Deserts"
        report = SemanticVisualQueryEngine().plan(
            documentary_topic=topic,
            shot_plan=SimpleNamespace(intents=(_intent("camel"),), primary_subject=topic),
        )

        scene = report.scene_for_index(0)
        self.assertEqual("camel", scene.canonical_visual_entity)
        self.assertTrue(any(query.startswith("camel") for query in scene.provider_queries))
        self.assertFalse(any("harshest" in query.lower() for query in scene.provider_queries))

    def test_produces_provider_variants_from_clean_entity(self) -> None:
        report = SemanticVisualQueryEngine().plan(
            documentary_topic="The World's Largest Rainforests",
            shot_plan=SimpleNamespace(
                intents=(
                    SimpleNamespace(
                        scene_index=0,
                        primary_subject="rainforest",
                        scene_entity=SceneEntity("rainforest", "primary_subject"),
                        action="",
                        environment="canopy",
                        shot_type="aerial",
                        documentary_role="overview",
                        search_queries=("rainforest aerial",),
                    ),
                ),
                primary_subject="The World's Largest Rainforests",
            ),
        )

        scene = report.scene_for_index(0)
        self.assertIn("pexels", scene.provider_variants)
        self.assertIn("nasa", scene.provider_variants)
        self.assertTrue(all("rainforest" in query.lower() or "amazon" in query.lower()
                            for query in scene.queries_for("pexels")))

    def test_scene_specific_canonical_entity_is_not_overwritten_by_topic(self) -> None:
        topic = "The World's Largest Rainforests"
        original = SimpleNamespace(
            scene_index=0,
            primary_subject=topic,
            scene_entity=SceneEntity("Amazon River", "location"),
            required_entities=(),
            action="flowing",
            environment="rainforest",
            shot_type="wide",
            documentary_role="overview",
            diagnostics={"narration": "The Amazon River feeds the forest."},
            search_queries=("Amazon River wide",),
        )
        canonical = CanonicalSceneEntityResolver().resolve(
            documentary_topic=topic,
            shot_plan=SimpleNamespace(intents=(original,), primary_subject=topic),
        ).scene_for_index(0)
        resolved = SimpleNamespace(
            **{
                **original.__dict__,
                "primary_subject": canonical.canonical_entity,
                "scene_entity": canonical.resolved_entity,
            }
        )
        report = SemanticVisualQueryEngine().plan(
            documentary_topic="",
            shot_plan=SimpleNamespace(intents=(resolved,), primary_subject=topic),
        )
        report = replace(report, documentary_topic=topic)

        self.assertEqual("amazon river", report.scene_for_index(0).canonical_visual_entity)
        self.assertTrue(all("rainforest" not in query.lower()
                            for query in report.scene_for_index(0).provider_queries))

    def test_pyramid_title_never_reaches_provider_queries(self) -> None:
        topic = "How Ancient Egyptians Built Stone Pyramids"
        source = SimpleNamespace(
            scene_index=0,
            primary_subject=topic,
            scene_entity=SceneEntity("ancient Egyptian stone blocks", "process"),
            action="moving",
            environment="desert",
            shot_type="wide",
            documentary_role="process",
            search_queries=("ancient Egyptian stone blocks moving",),
        )
        report = SemanticVisualQueryEngine().plan(
            documentary_topic="",
            shot_plan=SimpleNamespace(intents=(source,), primary_subject="Egyptian pyramids"),
        )

        scene = report.scene_for_index(0)
        self.assertEqual("ancient egyptian stone blocks", scene.canonical_visual_entity)
        self.assertTrue(scene.provider_queries)
        self.assertTrue(all(topic.casefold() not in query.casefold() for query in scene.provider_queries))
        self.assertTrue(all("stone blocks" in query.lower() for query in scene.provider_queries))


if __name__ == "__main__":
    unittest.main()
