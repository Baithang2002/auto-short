from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.media import CompositionKind, HybridVisualComposer


class HybridVisualComposerTests(unittest.TestCase):
    def test_solar_wind_uses_scientific_diagram_components(self) -> None:
        plan = HybridVisualComposer().plan(
            topic="How the Northern Lights Are Created",
            narration="Solar wind hits Earth's magnetosphere and lights the upper atmosphere.",
            queries=["aurora solar wind magnetosphere"],
        )

        self.assertEqual(plan.kind, CompositionKind.SCIENTIFIC_DIAGRAM)
        self.assertEqual(plan.scene_type, "space_weather")
        self.assertIn("earth magnetosphere", plan.components)
        self.assertIn("scientific_diagram", plan.source_media_types)

    def test_domain_topics_get_specialized_composition_plans(self) -> None:
        cases = [
            ("Why Volcanoes Create New Land", "cooling lava hardens into land", "volcanic_land"),
            ("How Roman Aqueducts Changed Civilization", "arches carried water by gravity", "roman_aqueduct"),
            ("How Bees Communicate Through Dancing", "bees dance inside the hive", "bee_communication"),
        ]

        composer = HybridVisualComposer()

        for topic, narration, expected_scene in cases:
            with self.subTest(topic=topic):
                plan = composer.plan(topic=topic, narration=narration, queries=[topic])
                self.assertEqual(plan.scene_type, expected_scene)
                self.assertGreaterEqual(len(plan.components), 3)

    def test_compose_creates_portrait_safe_renderer_compatible_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = HybridVisualComposer(width=360, height=640).compose(
                topic="How Roman Aqueducts Changed Civilization",
                narration="Roman aqueducts moved water without pumps.",
                queries=["Roman aqueduct gravity channel"],
                output_dir=Path(tmp),
                idx=2,
                shot_intent=SimpleNamespace(visual_goal="explain"),
            )

            self.assertTrue(result.local_path.exists())
            self.assertEqual(result.metadata["provider"], "hybrid_composer")
            self.assertEqual(result.metadata["fallback_level"], "hybrid_composition")
            self.assertEqual(result.metadata["hybrid_composition"]["visual_goal"], "explain")
            with Image.open(result.local_path) as image:
                self.assertEqual(image.size, (360, 640))

    def test_fetch_broll_uses_hybrid_composer_before_plain_explainer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with (
                patch.object(auto_short, "OUT_DIR", out_dir),
                patch.object(auto_short, "is_gemini_image_available", return_value=False),
                patch.object(auto_short, "_build_search_strategy") as build_strategy,
                patch.object(auto_short, "_generate_local_explainer_image") as local_card,
            ):
                build_strategy.return_value = SimpleNamespace(provider_plans=[])

                path = auto_short.fetch_broll(
                    ["solar wind magnetosphere"],
                    0,
                    fallback="How the Northern Lights Are Created",
                    narration="Solar wind hits Earth's magnetosphere.",
                    used_set=set(),
                    no_interactive=True,
                )

            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).name, "hybrid_visual_0.png")
            local_card.assert_not_called()
            selection = auto_short._MEDIA_SELECTION_DIAGNOSTICS[0]["selection"]
            self.assertEqual(selection["provider"], "hybrid_composer")
            self.assertEqual(selection["hybrid_composition"]["scene_type"], "space_weather")


if __name__ == "__main__":
    unittest.main()
