from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.media import (
    CapabilityRequirement,
    ProviderCapability,
    ProviderCapabilityRegistry,
    QueryPlanner,
    SceneType,
    SourcePlanner,
    StockCandidate,
    build_visual_intent,
    classify_scene_type,
    default_provider_capability_registry,
    select_best_candidate,
)


class MediaPlanningTests(unittest.TestCase):
    def test_query_planning_from_visual_intent(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Solar particles hit Earth's atmosphere and create northern lights.",
                "broll": "northern lights over earth",
                "broll_queries": ["aurora borealis sky", "earth atmosphere from space"],
            },
            "How the Northern Lights Are Created",
        )

        plan = QueryPlanner().plan(intent)

        self.assertIn("NASA astronomy", plan.primary_query)
        self.assertEqual(plan.visual_style, "real_celestial_media")
        self.assertIn("astronomy", {req.capability for req in plan.capability_requirements})

    def test_capability_generation_for_major_content_categories(self) -> None:
        cases = [
            ("Arctic fox pounces through snow.", "arctic fox hunting", "wildlife_video"),
            ("Ocean currents move heat across the planet.", "ocean current aerial", "ocean_video"),
            ("Roman roads still cut across Europe.", "ancient roman road", "history_video"),
            ("QR codes store data in black squares.", "qr code phone scan", "technology"),
            ("Your brain replays embarrassing memories.", "brain memory visual", "abstract_concepts"),
        ]

        planner = QueryPlanner()
        for narration, broll, expected in cases:
            with self.subTest(expected=expected):
                intent = build_visual_intent({"narration": narration, "broll": broll}, broll)
                plan = planner.plan(intent)
                self.assertIn(expected, {req.capability for req in plan.capability_requirements})

    def test_scene_type_classification_for_major_categories(self) -> None:
        cases = [
            ("Arctic fox pounces through snow.", "arctic fox hunting", SceneType.WILDLIFE),
            ("Ocean currents move heat across the planet.", "ocean current aerial", SceneType.OCEAN),
            ("Satellites show ocean temperature data.", "satellite ocean current data", SceneType.SATELLITE),
            ("Solar particles create auroras in the atmosphere.", "aurora borealis", SceneType.ASTRONOMY),
            ("Lightning forms inside a thunderstorm cloud.", "lightning storm clouds", SceneType.WEATHER),
            ("Roman aqueducts carried water into cities.", "roman aqueduct ruins", SceneType.HISTORY),
            ("QR codes store data in tiny squares.", "qr code scan", SceneType.TECHNOLOGY),
            ("A diagram shows how memory loops work.", "brain memory diagram", SceneType.DIAGRAM),
        ]

        for narration, broll, expected in cases:
            with self.subTest(scene_type=expected):
                intent = build_visual_intent({"narration": narration, "broll": broll}, broll)
                self.assertEqual(classify_scene_type(intent), expected)

    def test_provider_capability_registration(self) -> None:
        registry = default_provider_capability_registry(
            local_enabled=False,
            mixkit_enabled=True,
            coverr_enabled=True,
            videvo_enabled=True,
            noaa_enabled=True,
            esa_enabled=True,
            wikimedia_enabled=True,
        )

        self.assertIn("pexels", {provider.provider_id for provider in registry.all()})
        self.assertIn("mixkit", {provider.provider_id for provider in registry.all()})
        self.assertIn("coverr", {provider.provider_id for provider in registry.all()})
        self.assertIn("videvo", {provider.provider_id for provider in registry.all()})
        self.assertIn("wikimedia", {provider.provider_id for provider in registry.all()})
        self.assertIn("noaa", {provider.provider_id for provider in registry.all()})
        self.assertIn("esa", {provider.provider_id for provider in registry.all()})
        self.assertIn("astronomy", registry.get("nasa").capabilities)
        self.assertIn("illustrations", registry.get("gemini_image").capabilities)
        nasa = registry.get("nasa")
        self.assertIn("image", nasa.media_types)
        self.assertIn("space", nasa.domains)
        self.assertGreater(nasa.confidence, 0)

    def test_archive_provider_capability_metadata_registration(self) -> None:
        registry = default_provider_capability_registry(
            local_enabled=False,
            usgs_enabled=True,
            usfws_enabled=True,
            loc_enabled=True,
            smithsonian_enabled=True,
            nps_enabled=True,
            europeana_enabled=True,
            flickr_commons_enabled=True,
        )

        self.assertIn("geology", registry.get("usgs").domains)
        self.assertIn("wildlife_images", registry.get("usfws").capabilities)
        self.assertEqual(registry.get("loc").media_types, ("image",))
        self.assertIn("history", registry.get("smithsonian").domains)
        self.assertIn("geology", registry.get("nps").capabilities)
        self.assertTrue(registry.get("europeana").requires_api_key)
        self.assertTrue(registry.get("flickr_commons").requires_api_key)

    def test_provider_agnostic_ranking_uses_capabilities_not_names(self) -> None:
        registry = ProviderCapabilityRegistry()
        registry.register(ProviderCapability("provider_a", ("generic_stock_video",), base_priority=0))
        registry.register(ProviderCapability("provider_b", ("space_video", "astronomy"), base_priority=50))
        ranked = registry.rank([CapabilityRequirement("astronomy", required=True, weight=4)])

        self.assertEqual(ranked[0][0].provider_id, "provider_b")

    def test_nasa_ranks_before_stock_for_astronomy(self) -> None:
        intent = build_visual_intent(
            {"narration": "Saturn's atmosphere hides violent storms.", "broll": "saturn planet atmosphere"},
            "What Happens If You Fall Into Saturn",
        )
        query_plan = QueryPlanner().plan(intent)
        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False)).plan(query_plan)

        order = strategy.provider_order
        self.assertLess(order.index("nasa"), order.index("pexels"))
        self.assertLess(order.index("nasa"), order.index("pixabay"))

    def test_stock_ranks_before_nasa_for_nature(self) -> None:
        intent = build_visual_intent(
            {"narration": "The arctic fox pounces through snow.", "broll": "arctic fox pouncing snow"},
            "Arctic Fox Survival Tricks",
        )
        query_plan = QueryPlanner().plan(intent)
        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False)).plan(query_plan)

        order = strategy.provider_order
        self.assertLess(order.index("pexels"), order.index("nasa"))
        self.assertLess(order.index("pixabay"), order.index("nasa"))

    def test_wildlife_routes_to_pexels_pixabay_then_mixkit_when_available(self) -> None:
        intent = build_visual_intent(
            {"narration": "The arctic fox pounces through snow.", "broll": "arctic fox pouncing snow"},
            "Arctic Fox Survival Tricks",
        )
        registry = default_provider_capability_registry(
            local_enabled=False,
            mixkit_enabled=True,
            gemini_image_enabled=False,
        )
        strategy = SourcePlanner(registry).plan(QueryPlanner().plan(intent))
        order = strategy.provider_order

        self.assertLess(order.index("pexels"), order.index("pixabay"))
        self.assertLess(order.index("pixabay"), order.index("mixkit"))

    def test_wildlife_routes_to_usfws_archive_before_generic_stock_when_enabled(self) -> None:
        intent = build_visual_intent(
            {"narration": "Octopuses solve puzzles with flexible arms.", "broll": "octopus underwater close up"},
            "Why Octopuses Are So Intelligent",
        )
        registry = default_provider_capability_registry(
            local_enabled=False,
            usfws_enabled=True,
            gemini_image_enabled=False,
        )
        strategy = SourcePlanner(registry).plan(QueryPlanner().plan(intent))

        self.assertEqual(strategy.query_plan.scene_type, SceneType.WILDLIFE)
        self.assertLess(strategy.provider_order.index("usfws"), strategy.provider_order.index("pexels"))

    def test_octopus_hiding_in_rocks_stays_wildlife_not_geology(self) -> None:
        intent = build_visual_intent(
            {"narration": "The octopus hides between rocks to escape predators.", "broll": "octopus hiding in rocks"},
            "Why Octopuses Are So Intelligent",
        )
        query_plan = QueryPlanner().plan(intent)

        self.assertEqual(query_plan.scene_type, SceneType.WILDLIFE)
        self.assertIn("wildlife nature", query_plan.primary_query)
        self.assertNotIn("geology earth science archive", query_plan.primary_query)

    def test_ocean_routes_to_noaa_before_stock_when_configured(self) -> None:
        intent = build_visual_intent(
            {"narration": "Ocean currents move heat across the planet.", "broll": "ocean current aerial"},
            "The Science Behind Earth's Strongest Ocean Currents",
        )
        registry = default_provider_capability_registry(
            local_enabled=False,
            noaa_enabled=True,
            gemini_image_enabled=False,
        )
        strategy = SourcePlanner(registry).plan(QueryPlanner().plan(intent))
        order = strategy.provider_order

        self.assertLess(order.index("noaa"), order.index("pexels"))
        self.assertEqual(strategy.query_plan.scene_type, SceneType.OCEAN)

    def test_astronomy_routes_to_nasa_before_esa(self) -> None:
        intent = build_visual_intent(
            {"narration": "Saturn's atmosphere hides violent storms.", "broll": "saturn planet atmosphere"},
            "What Happens If You Fall Into Saturn",
        )
        registry = default_provider_capability_registry(
            local_enabled=False,
            esa_enabled=True,
            gemini_image_enabled=False,
        )
        strategy = SourcePlanner(registry).plan(QueryPlanner().plan(intent))

        self.assertLess(strategy.provider_order.index("nasa"), strategy.provider_order.index("esa"))

    def test_history_routes_to_wikimedia_before_pixabay(self) -> None:
        intent = build_visual_intent(
            {"narration": "Roman aqueducts carried water across valleys.", "broll": "roman aqueduct ruins"},
            "How Roman Aqueducts Changed Civilization",
        )
        strategy = SourcePlanner(
            default_provider_capability_registry(
                local_enabled=False,
                gemini_image_enabled=False,
                wikimedia_enabled=True,
            )
        ).plan(QueryPlanner().plan(intent))

        self.assertLess(strategy.provider_order.index("wikimedia"), strategy.provider_order.index("pixabay"))

    def test_history_routes_to_archives_before_stock_when_enabled(self) -> None:
        intent = build_visual_intent(
            {"narration": "Roman aqueducts carried water across valleys.", "broll": "roman aqueduct ruins"},
            "How Roman Aqueducts Changed Civilization",
        )
        strategy = SourcePlanner(
            default_provider_capability_registry(
                local_enabled=False,
                gemini_image_enabled=False,
                wikimedia_enabled=True,
                loc_enabled=True,
                smithsonian_enabled=True,
                europeana_enabled=True,
            )
        ).plan(QueryPlanner().plan(intent))

        order = strategy.provider_order
        self.assertLess(order.index("loc"), order.index("pexels"))
        self.assertLess(order.index("wikimedia"), order.index("pexels"))
        self.assertLess(order.index("smithsonian"), order.index("pixabay"))

    def test_volcano_routes_to_usgs_nasa_wikimedia_before_stock(self) -> None:
        intent = build_visual_intent(
            {"narration": "Lava cools into basalt and creates brand new land.", "broll": "volcano lava flow"},
            "How Volcanoes Actually Create New Land",
        )
        strategy = SourcePlanner(
            default_provider_capability_registry(
                local_enabled=False,
                gemini_image_enabled=False,
                wikimedia_enabled=True,
                usgs_enabled=True,
                nps_enabled=True,
            )
        ).plan(QueryPlanner().plan(intent))

        order = strategy.provider_order
        self.assertEqual(strategy.query_plan.scene_type, SceneType.VOLCANO)
        self.assertIn("volcano lava geology archive", strategy.query_plan.primary_query)
        self.assertLess(order.index("usgs"), order.index("pexels"))
        self.assertLess(order.index("nasa"), order.index("pexels"))
        self.assertLess(order.index("wikimedia"), order.index("pixabay"))

    def test_technology_routes_to_pexels_coverr_then_videvo(self) -> None:
        intent = build_visual_intent(
            {"narration": "QR codes store data in black and white squares.", "broll": "qr code phone scan"},
            "How QR Codes Actually Work",
        )
        registry = default_provider_capability_registry(
            local_enabled=False,
            coverr_enabled=True,
            videvo_enabled=True,
            gemini_image_enabled=False,
        )
        strategy = SourcePlanner(registry).plan(QueryPlanner().plan(intent))
        order = strategy.provider_order

        self.assertLess(order.index("pexels"), order.index("coverr"))
        self.assertLess(order.index("coverr"), order.index("videvo"))

    def test_gemini_image_is_fallback_unless_illustration_required(self) -> None:
        registry = default_provider_capability_registry(local_enabled=False)
        stock_intent = build_visual_intent(
            {"narration": "Ocean currents move warm water.", "broll": "ocean current aerial"},
            "Ocean Currents",
        )
        stock_strategy = SourcePlanner(registry).plan(QueryPlanner().plan(stock_intent))
        self.assertGreater(stock_strategy.provider_order.index("gemini_image"), stock_strategy.provider_order.index("pexels"))

        diagram_intent = build_visual_intent(
            {"narration": "A diagram explains how a QR code stores data.", "broll": "qr code diagram"},
            "How QR Codes Work",
        )
        diagram_strategy = SourcePlanner(registry).plan(QueryPlanner().plan(diagram_intent))
        self.assertLess(diagram_strategy.provider_order.index("gemini_image"), diagram_strategy.provider_order.index("nasa"))

    def test_ocean_explainer_routes_to_diagram_before_generic_stock(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "The global ocean conveyor belt moves heat through a hidden circulation loop.",
                "broll": "global ocean conveyor belt animation",
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )

        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False)).plan(QueryPlanner().plan(intent))

        self.assertLess(strategy.provider_order.index("gemini_image"), strategy.provider_order.index("pexels"))
        self.assertIn("diagram", strategy.query_plan.primary_query)

    def test_provider_diagnostics_include_source_coverage_metadata(self) -> None:
        intent = build_visual_intent(
            {"narration": "Lava cools into basalt and creates brand new land.", "broll": "volcano lava flow"},
            "How Volcanoes Actually Create New Land",
        )
        strategy = SourcePlanner(
            default_provider_capability_registry(
                local_enabled=False,
                gemini_image_enabled=False,
                usgs_enabled=True,
            )
        ).plan(QueryPlanner().plan(intent))

        usgs_diag = next(item for item in strategy.diagnostics["search_strategy"] if item["provider"] == "usgs")
        self.assertIn("image", usgs_diag["media_types"])
        self.assertIn("geology", usgs_diag["domains"])
        self.assertIn("USGS", usgs_diag["licensing"])
        self.assertGreater(usgs_diag["provider_confidence"], 0)

    def test_ocean_satellite_scene_routes_to_nasa_capability(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Satellites show gyres and eddies moving across the ocean.",
                "broll": "satellite ocean current data",
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )

        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False, gemini_image_enabled=False)).plan(QueryPlanner().plan(intent))

        self.assertLess(strategy.provider_order.index("nasa"), strategy.provider_order.index("pexels"))

    def test_ocean_rotation_explainer_does_not_route_to_nasa_as_astronomy(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Earth's spin creates the Coriolis effect, bending ocean currents into giant swirls.",
                "broll": "earth spinning animation",
                "broll_queries": ["earth rotation animation satellite view", "ocean current swirl diagram"],
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )

        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False, gemini_image_enabled=False)).plan(QueryPlanner().plan(intent))

        self.assertEqual(strategy.query_plan.primary_query, "coriolis effect ocean current diagram")
        self.assertNotIn("NASA astronomy", strategy.query_plan.primary_query)
        self.assertLess(strategy.provider_order.index("pexels"), strategy.provider_order.index("nasa"))
        self.assertLess(strategy.provider_order.index("pixabay"), strategy.provider_order.index("nasa"))

    def test_ocean_current_query_qualification_does_not_add_animals(self) -> None:
        query = auto_short._qualify_query(
            "global ocean conveyor belt animation",
            "The Science Behind Earth's Strongest Ocean Currents",
        )

        self.assertIn("ocean", query)
        self.assertNotIn("animal", query)

    def test_gemini_image_requires_explicit_image_model(self) -> None:
        old_key = auto_short.GEMINI_API_KEY
        old_model = auto_short.GEMINI_IMAGE_MODEL
        try:
            auto_short.GEMINI_API_KEY = "key"
            auto_short.GEMINI_IMAGE_MODEL = ""
            self.assertFalse(auto_short.is_gemini_image_available())

            auto_short.GEMINI_IMAGE_MODEL = "image-model"
            self.assertTrue(auto_short.is_gemini_image_available())
        finally:
            auto_short.GEMINI_API_KEY = old_key
            auto_short.GEMINI_IMAGE_MODEL = old_model

    def test_generic_behavior_keeps_stock_before_image_fallback(self) -> None:
        intent = build_visual_intent(
            {"narration": "A strange fact changes how you see the world.", "broll": "nature landscape"},
            "Amazing Facts",
        )
        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False)).plan(QueryPlanner().plan(intent))

        self.assertLess(strategy.provider_order.index("pexels"), strategy.provider_order.index("pixabay"))
        self.assertGreater(strategy.provider_order.index("gemini_image"), strategy.provider_order.index("pixabay"))

    def test_fetch_broll_routes_space_to_nasa_before_stock(self) -> None:
        old_pexels = auto_short.PEXELS_API_KEY
        old_pixabay = auto_short.PIXABAY_API_KEY
        auto_short.PEXELS_API_KEY = "pexels"
        auto_short.PIXABAY_API_KEY = "pixabay"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                nasa_path = Path(tmp) / "nasa.mp4"
                pexels_path = Path(tmp) / "pexels.mp4"
                pixabay_path = Path(tmp) / "pixabay.mp4"
                for path in (nasa_path, pexels_path, pixabay_path):
                    path.write_bytes(b"video")
                with patch.object(auto_short, "is_gemini_image_available", return_value=False), patch.object(
                    auto_short,
                    "_fetch_adaptive_broll",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "_valid_media_path",
                    side_effect=lambda path: path is not None,
                ), patch.object(
                    auto_short,
                    "fetch_nasa_video",
                    return_value=nasa_path,
                ) as nasa, patch.object(
                    auto_short,
                    "fetch_pexels_video",
                    return_value=pexels_path,
                ) as pexels, patch.object(auto_short, "fetch_pixabay_video", return_value=pixabay_path):
                    out = auto_short.fetch_broll(
                        ["saturn atmosphere", "planet rings"],
                        0,
                        fallback="What Happens If You Fall Into Saturn",
                        narration="Saturn's atmosphere would crush a falling spacecraft.",
                        used_set=set(),
                        no_interactive=True,
                    )
        finally:
            auto_short.PEXELS_API_KEY = old_pexels
            auto_short.PIXABAY_API_KEY = old_pixabay

        self.assertEqual(out, nasa_path)
        nasa.assert_called_once()
        pexels.assert_not_called()

    def test_fetch_broll_routes_ocean_to_stock_before_nasa(self) -> None:
        old_pexels = auto_short.PEXELS_API_KEY
        old_pixabay = auto_short.PIXABAY_API_KEY
        auto_short.PEXELS_API_KEY = "pexels"
        auto_short.PIXABAY_API_KEY = "pixabay"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pexels_path = Path(tmp) / "pexels.mp4"
                nasa_path = Path(tmp) / "nasa.mp4"
                pexels_path.write_bytes(b"video")
                nasa_path.write_bytes(b"video")
                with patch.object(auto_short, "is_gemini_image_available", return_value=False), patch.object(
                    auto_short,
                    "_fetch_adaptive_broll",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "_valid_media_path",
                    side_effect=lambda path: path is not None,
                ), patch.object(
                    auto_short,
                    "fetch_noaa_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_usgs_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_wikimedia_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_library_of_congress_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_smithsonian_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_europeana_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_internet_archive_media",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "fetch_pexels_video",
                    return_value=pexels_path,
                ) as pexels, patch.object(
                    auto_short,
                    "fetch_nasa_video",
                    return_value=nasa_path,
                ) as nasa:
                    out = auto_short.fetch_broll(
                        ["ocean current aerial", "underwater ocean flow"],
                        1,
                        fallback="The Science Behind Earth's Strongest Ocean Currents",
                        narration="Ocean currents move heat across the planet.",
                        used_set=set(),
                        no_interactive=True,
                    )
        finally:
            auto_short.PEXELS_API_KEY = old_pexels
            auto_short.PIXABAY_API_KEY = old_pixabay

        self.assertEqual(out, pexels_path)
        pexels.assert_called_once()
        nasa.assert_not_called()

    def test_fetch_broll_passes_same_canonical_intent_to_provider(self) -> None:
        old_pexels = auto_short.PEXELS_API_KEY
        auto_short.PEXELS_API_KEY = "pexels"
        try:
            with patch.object(auto_short, "is_gemini_image_available", return_value=False), patch.object(
                auto_short,
                "_fetch_adaptive_broll",
                return_value=None,
            ), patch.object(
                auto_short,
                "_valid_media_path",
                side_effect=lambda path: path is not None,
            ), patch.object(
                auto_short,
                "fetch_noaa_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_usgs_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_wikimedia_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_library_of_congress_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_smithsonian_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_europeana_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_internet_archive_media",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_pexels_video",
                return_value=Path("pexels.mp4"),
            ) as pexels:
                auto_short.fetch_broll(
                    ["ocean current satellite map"],
                    2,
                    fallback="The Science Behind Earth's Strongest Ocean Currents",
                    narration="A satellite map reveals the current's full path.",
                    used_set=set(),
                    no_interactive=True,
                )
        finally:
            auto_short.PEXELS_API_KEY = old_pexels

        intent = pexels.call_args.kwargs["intent"]
        self.assertIn("satellite map", " ".join(intent.queries))
        self.assertIn("full path", intent.narration)
        self.assertEqual(intent.topic, "The Science Behind Earth's Strongest Ocean Currents")

    def test_planning_diagnostics_merge_with_selection_metadata(self) -> None:
        auto_short._MEDIA_PLANNING_DIAGNOSTICS.clear()
        auto_short._MEDIA_SELECTION_DIAGNOSTICS.clear()
        intent = build_visual_intent(
            {"narration": "Auroras glow when solar particles hit air.", "broll": "aurora borealis sky"},
            "How the Northern Lights Are Created",
        )
        strategy = SourcePlanner(default_provider_capability_registry(local_enabled=False)).plan(QueryPlanner().plan(intent))
        result = select_best_candidate(
            intent,
            [
                StockCandidate(
                    provider="nasa",
                    provider_id="aurora",
                    query="aurora borealis",
                    title="aurora borealis from space",
                    duration_sec=8,
                    width=1080,
                    height=1920,
                )
            ],
        )

        auto_short._remember_media_planning(0, strategy)
        auto_short._remember_media_selection(0, result)

        metadata = auto_short._MEDIA_SELECTION_DIAGNOSTICS[0]
        self.assertIn("selection", metadata)
        self.assertIn("query_plan", metadata)
        self.assertIn("search_strategy", metadata)
        self.assertEqual(len(metadata["selection_attempts"]), 1)
        self.assertTrue(metadata["selection_attempts"][0]["accepted"])


if __name__ == "__main__":
    unittest.main()
