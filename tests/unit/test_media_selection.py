from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.domain import MediaSource
from autovideo.media import (
    SceneImportance,
    StockCandidate,
    build_visual_intent,
    candidate_from_local_path,
    candidate_from_nasa_item,
    candidate_from_remote_item,
    candidate_from_pexels_video,
    candidate_from_pixabay_hit,
    score_candidate,
    select_best_candidate,
    select_first_available_provider,
)


class MediaSelectionTests(unittest.TestCase):
    def test_visual_intent_extracts_subject_action_environment_and_shot(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "The arctic fox pounces through deep snow.",
                "broll": "arctic fox pouncing close up",
                "broll_queries": ["arctic fox hunting in snow"],
            },
            "Arctic Fox Survival Tricks",
        )

        self.assertEqual(intent.primary_subject, "arctic fox")
        self.assertIn("pouncing", intent.action_terms)
        self.assertIn("arctic", intent.environment_terms)
        self.assertIn("snow", intent.environment_terms)
        self.assertEqual(intent.shot_type, "close")

    def test_visual_intent_uses_broll_when_query_list_is_missing(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Earth's magnetic field redirects solar particles.",
                "broll": "earth magnetic field diagram",
            },
            "How the Northern Lights Are Created",
        )

        self.assertEqual(intent.queries, ("earth magnetic field diagram",))

    def test_candidate_normalization_preserves_provider_metadata(self) -> None:
        pexels = candidate_from_pexels_video(
            {
                "id": 123,
                "url": "https://pexels.com/video/arctic-fox-snow-123/",
                "duration": 12,
                "user": {"name": "creator"},
                "video_files": [{"link": "https://cdn/fox.mp4", "width": 1080, "height": 1920}],
            },
            "arctic fox snow",
        )
        pixabay = candidate_from_pixabay_hit(
            {"id": 456, "tags": "arctic fox, snow", "duration": 10, "user": "creator"},
            {"url": "https://cdn/pixabay.mp4", "width": 1080, "height": 1920},
            "arctic fox snow",
        )

        self.assertEqual(pexels.provider, "pexels")
        self.assertEqual(pexels.provider_id, "123")
        self.assertEqual(pexels.download_url, "https://cdn/fox.mp4")
        self.assertEqual(pixabay.provider, "pixabay")
        self.assertEqual(pixabay.provider_id, "456")

    def test_remote_candidate_normalization_preserves_license_metadata(self) -> None:
        candidate = candidate_from_remote_item(
            "wikimedia",
            {
                "provider_asset_id": "Roman_aqueduct.webm",
                "title": "Roman aqueduct ruins",
                "description": "Ancient Roman aqueduct archive footage",
                "source_url": "https://commons.wikimedia.org/wiki/File:Roman_aqueduct.webm",
                "download_url": "https://upload.wikimedia.org/roman.webm",
                "license": "CC BY-SA 4.0",
                "attribution": "Example Creator",
                "width": 1920,
                "height": 1080,
                "capability": "history_video",
            },
            "roman aqueduct ruins",
        )

        self.assertEqual(candidate.provider, "wikimedia")
        self.assertEqual(candidate.provider_id, "Roman_aqueduct.webm")
        self.assertEqual(candidate.raw_metadata["license"], "CC BY-SA 4.0")
        self.assertEqual(candidate.raw_metadata["attribution"], "Example Creator")

    def test_nasa_candidate_can_normalize_still_images(self) -> None:
        candidate = candidate_from_nasa_item(
            {
                "href": "https://images.nasa.gov/details/PIA123",
                "data": [
                    {
                        "nasa_id": "PIA123",
                        "title": "Aurora over Earth",
                        "media_type": "image",
                    }
                ],
            },
            "https://images-assets.nasa.gov/PIA123/PIA123~large.jpg",
            "aurora over earth",
            is_image=True,
        )

        self.assertTrue(candidate.is_image)
        self.assertEqual(candidate.provider, "nasa")
        self.assertEqual(candidate.provider_id, "PIA123")
        self.assertEqual(candidate.raw_metadata["capability"], "scientific_media")

    def test_remote_provider_item_infers_image_media_type(self) -> None:
        item = auto_short._remote_item_from_provider(
            "usgs",
            {
                "id": "volcano-1",
                "title": "Kilauea lava flow",
                "download_url": "https://example.test/kilauea.jpg?download=1",
                "media_type": "image/jpeg",
            },
            "kilauea lava flow",
        )

        self.assertTrue(item["is_image"])
        candidate = candidate_from_remote_item("usgs", item, "kilauea lava flow")
        self.assertTrue(candidate.is_image)

    def test_selection_metadata_includes_normalized_provider_fields(self) -> None:
        intent = build_visual_intent(
            {"narration": "Roman aqueducts carried water across valleys.", "broll": "roman aqueduct ruins"},
            "How Roman Aqueducts Changed Civilization",
        )
        candidate = candidate_from_remote_item(
            "wikimedia",
            {
                "provider_asset_id": "Roman_aqueduct.webm",
                "title": "Roman aqueduct ruins",
                "source_url": "https://commons.wikimedia.org/wiki/File:Roman_aqueduct.webm",
                "download_url": "https://upload.wikimedia.org/roman.webm",
                "license": "CC BY-SA 4.0",
                "attribution": "Example Creator",
                "width": 1080,
                "height": 1920,
                "capability": "history_video",
            },
            "roman aqueduct ruins",
        )

        result = select_best_candidate(intent, [candidate], minimum_score=0)
        metadata = result.to_metadata()

        self.assertEqual(metadata["provider"], "wikimedia")
        self.assertEqual(metadata["provider_asset_id"], "Roman_aqueduct.webm")
        self.assertEqual(metadata["source_url"], "https://commons.wikimedia.org/wiki/File:Roman_aqueduct.webm")
        self.assertEqual(metadata["license"], "CC BY-SA 4.0")
        self.assertEqual(metadata["attribution"], "Example Creator")

    def test_library_of_congress_candidate_normalization_from_result(self) -> None:
        candidate = auto_short._loc_candidate_from_result(
            {
                "id": "loc-roman-aqueduct",
                "title": "Roman aqueduct ruins",
                "url": "https://www.loc.gov/item/example/",
                "image_url": [
                    "https://www.loc.gov/thumb.jpg",
                    "https://www.loc.gov/roman-aqueduct-large.jpg",
                ],
                "rights": "No known restrictions",
            },
            "roman aqueduct ruins",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.provider, "loc")
        self.assertTrue(candidate.is_image)
        self.assertEqual(candidate.download_url, "https://www.loc.gov/roman-aqueduct-large.jpg")
        self.assertEqual(candidate.raw_metadata["license"], "No known restrictions")

    def test_scoring_rewards_exact_relevance_and_penalizes_wrong_matches(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "The arctic fox pounces through snow.",
                "broll": "arctic fox pouncing",
                "broll_queries": ["arctic fox pouncing in snow"],
            },
            "Arctic Fox Survival Tricks",
        )
        good = StockCandidate(
            provider="pexels",
            provider_id="good",
            query="arctic fox pouncing in snow",
            title="wild arctic fox pouncing in snow",
            duration_sec=8,
            width=1080,
            height=1920,
        )
        bad = StockCandidate(
            provider="pexels",
            provider_id="bad",
            query="arctic fox pouncing in snow",
            title="husky dog inside zoo cage with person",
            duration_sec=8,
            width=1920,
            height=1080,
        )

        good_score = score_candidate(intent, good, target_duration_sec=5)
        bad_score = score_candidate(intent, bad, target_duration_sec=5)

        self.assertGreater(good_score.score, bad_score.score)
        self.assertIn("penalized term: dog", bad_score.rejection_reasons)
        self.assertIn("penalized term: zoo", bad_score.rejection_reasons)

    def test_duplicate_provider_ids_are_penalized(self) -> None:
        intent = build_visual_intent(
            {"narration": "Arctic fox walks in snow.", "broll": "arctic fox walking"},
            "Arctic Fox Survival Tricks",
        )
        candidate = StockCandidate(
            provider="pexels",
            provider_id="123",
            query="arctic fox walking",
            title="arctic fox walking in snow",
            duration_sec=6,
            width=1080,
            height=1920,
        )

        score = score_candidate(intent, candidate, used_provider_ids={"pexels:123"})

        self.assertLess(score.breakdown["dedup"], 0)
        self.assertIn("duplicate provider id", score.rejection_reasons)

    def test_duplicate_provider_id_does_not_pass_normal_selection_threshold(self) -> None:
        intent = build_visual_intent(
            {"narration": "QR code timing patterns define the grid.", "broll": "qr code timing patterns"},
            "How QR Codes Actually Work",
        )
        duplicate = StockCandidate(
            provider="pexels",
            provider_id="8384432",
            query="qr code timing patterns",
            title="qr code phone scan close up",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_best_candidate(
            intent,
            [duplicate],
            used_provider_ids={"pexels:8384432"},
            minimum_score=1.5,
        )

        self.assertIsNone(result.selected_candidate)
        self.assertTrue(any("below minimum" in warning for warning in result.warnings))

    def test_marine_life_is_penalized_when_ocean_current_is_not_about_animals(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Ocean currents move heat through massive water flows.",
                "broll": "ocean current aerial",
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )
        current_clip = StockCandidate(
            provider="pexels",
            provider_id="current",
            query="ocean current aerial",
            title="ocean water current waves moving",
            duration_sec=8,
            width=1080,
            height=1920,
        )
        fish_clip = StockCandidate(
            provider="pexels",
            provider_id="fish",
            query="ocean current aerial",
            title="school of fish underwater animal",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        current_score = score_candidate(intent, current_clip)
        fish_score = score_candidate(intent, fish_clip)

        self.assertGreater(current_score.score, fish_score.score)
        self.assertIn("penalized term: fish", fish_score.rejection_reasons)

    def test_portrait_candidate_is_preferred_over_landscape_for_shorts(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "QR codes store data in tiny black and white squares.",
                "broll": "qr code phone scan close up",
                "scene_importance": SceneImportance.HOOK.value,
            },
            "How QR Codes Actually Work",
        )
        landscape = StockCandidate(
            provider="pexels",
            provider_id="landscape",
            query="qr code phone scan close up",
            title="qr code phone scan close up technology",
            duration_sec=8,
            width=1920,
            height=1080,
        )
        portrait = StockCandidate(
            provider="pexels",
            provider_id="portrait",
            query="qr code phone scan close up",
            title="qr code phone scan close up technology",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_best_candidate(intent, [landscape, portrait], minimum_score=0)
        metadata = result.to_metadata()

        self.assertEqual(result.selected_candidate.provider_id, "portrait")
        self.assertGreater(metadata["portrait_score"], 8.0)
        self.assertEqual(metadata["scene_importance"], "hook")

    def test_square_candidate_is_preferred_over_landscape_when_portrait_missing(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Ocean currents move heat through giant water flows.",
                "broll": "ocean current water flow",
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )
        landscape = StockCandidate(
            provider="pexels",
            provider_id="landscape",
            query="ocean current water flow",
            title="ocean current water flow",
            duration_sec=8,
            width=1920,
            height=1080,
        )
        square = StockCandidate(
            provider="pexels",
            provider_id="square",
            query="ocean current water flow",
            title="ocean current water flow",
            duration_sec=8,
            width=1200,
            height=1200,
        )

        result = select_best_candidate(intent, [landscape, square], minimum_score=0)

        self.assertEqual(result.selected_candidate.provider_id, "square")

    def test_ultra_wide_clip_gets_portrait_safety_penalty(self) -> None:
        intent = build_visual_intent(
            {"narration": "Solar wind hits Earth's atmosphere.", "broll": "aurora borealis"},
            "How the Northern Lights Are Created",
        )
        candidate = StockCandidate(
            provider="pexels",
            provider_id="wide",
            query="aurora borealis",
            title="aurora borealis sky",
            duration_sec=8,
            width=2560,
            height=720,
        )

        score = score_candidate(intent, candidate)

        self.assertLess(score.breakdown["portrait_score"], 3.0)
        self.assertIn("ultra-wide clip", score.rejection_reasons)

    def test_hook_scene_requires_higher_confidence_than_supporting_scene(self) -> None:
        hook = build_visual_intent(
            {
                "narration": "This square can hide a whole web address.",
                "broll": "qr code",
                "scene_importance": SceneImportance.HOOK.value,
            },
            "How QR Codes Actually Work",
        )
        candidate = StockCandidate(
            provider="pexels",
            provider_id="weak",
            query="technology",
            title="abstract digital technology background",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_best_candidate(
            hook,
            [candidate],
            minimum_score=auto_short._minimum_score_for_intent(hook),
        )

        self.assertIsNone(result.selected_candidate)
        self.assertIn("below minimum", result.warnings[0])

    def test_generic_technology_footage_is_penalized_for_qr_code_scene(self) -> None:
        intent = build_visual_intent(
            {"narration": "QR codes store data in black and white modules.", "broll": "qr code scan"},
            "How QR Codes Actually Work",
        )
        generic = StockCandidate(
            provider="coverr",
            provider_id="generic",
            query="qr code scan",
            title="abstract digital data network technology background",
            duration_sec=8,
            width=1080,
            height=1920,
        )
        qr = StockCandidate(
            provider="coverr",
            provider_id="qr",
            query="qr code scan",
            title="qr code phone scan close up",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        generic_score = score_candidate(intent, generic)
        qr_score = score_candidate(intent, qr)

        self.assertGreater(qr_score.score, generic_score.score)
        self.assertIn("abstract tech footage without qr code", generic_score.rejection_reasons)
        self.assertIn("qr code not proven in provider metadata", generic_score.rejection_reasons)

    def test_qr_scene_rejects_query_only_match_without_provider_evidence(self) -> None:
        intent = build_visual_intent(
            {"narration": "QR codes store data in black and white modules.", "broll": "qr code scan"},
            "How QR Codes Actually Work",
        )
        query_only = StockCandidate(
            provider="pexels",
            provider_id="phone-scan",
            query="qr code scan close up",
            title="person scanning phone at restaurant",
            description="mobile payment lifestyle",
            duration_sec=8,
            width=1080,
            height=1920,
        )
        qr = StockCandidate(
            provider="pexels",
            provider_id="qr-closeup",
            query="qr code scan close up",
            title="qr code close up on phone screen",
            description="quick response code scan",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        weak_score = score_candidate(intent, query_only)
        strong_score = score_candidate(intent, qr)

        self.assertLess(weak_score.breakdown["relevance_score"], 3.0)
        self.assertIn("qr code not proven in provider metadata", weak_score.rejection_reasons)
        self.assertGreater(strong_score.score, weak_score.score + 8.0)

    def test_low_confidence_hook_candidate_falls_through_to_next_provider(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "This simple pattern can unlock a hidden web address.",
                "broll": "qr code scan",
                "scene_importance": SceneImportance.HOOK.value,
            },
            "How QR Codes Actually Work",
        )
        weak_first = StockCandidate(
            provider="pexels",
            provider_id="weak",
            query="qr code scan",
            title="abstract technology background",
            duration_sec=8,
            width=1920,
            height=1080,
        )
        strong_second = StockCandidate(
            provider="pixabay",
            provider_id="strong",
            query="qr code scan",
            title="qr code phone scan close up technology",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_first_available_provider(
            intent,
            [("pexels", [weak_first]), ("pixabay", [strong_second])],
            minimum_score=auto_short._minimum_score_for_intent(intent),
        )

        self.assertEqual(result.selected_candidate.provider, "pixabay")
        self.assertTrue(any("below minimum" in warning for warning in result.warnings))

    def test_weather_scene_rejects_animal_clip_and_uses_weather_provider(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "A lightning bolt forms inside a thunderstorm cloud.",
                "broll": "lightning storm clouds",
                "scene_importance": SceneImportance.HOOK.value,
            },
            "How Lightning Is Created",
        )
        animal = StockCandidate(
            provider="pexels",
            provider_id="animal",
            query="lightning storm clouds",
            title="wild animal walking through nature",
            duration_sec=8,
            width=1080,
            height=1920,
        )
        weather = StockCandidate(
            provider="pixabay",
            provider_id="storm",
            query="lightning storm clouds",
            title="lightning thunderstorm clouds in night sky",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_first_available_provider(
            intent,
            [("pexels", [animal]), ("pixabay", [weather])],
            minimum_score=auto_short._minimum_score_for_intent(intent),
        )

        self.assertEqual(result.selected_candidate.provider_id, "storm")
        self.assertTrue(any("wrong-domain" in reason for _, reasons in result.rejected for reason in reasons))

    def test_portrait_quality_cannot_inflate_weak_relevance_confidence(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "This satellite map reveals the global ocean current.",
                "broll": "ocean current satellite map",
                "media_mode": "explain",
            },
            "The Science Behind Earth's Strongest Ocean Currents",
        )
        generic_ocean = StockCandidate(
            provider="pexels",
            provider_id="portrait-waves",
            query="ocean current satellite map",
            title="portrait beach waves and sunset",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_best_candidate(intent, [generic_ocean], minimum_score=0)

        self.assertIsNone(result.selected_candidate)
        self.assertEqual(result.confidence, "rejected")
        self.assertFalse(result.score.quality_gate_passed)
        self.assertIn("no explanatory evidence", " ".join(result.score.rejection_reasons))

    def test_requested_diagram_rejects_generic_earth_footage(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Earth's magnetic field redirects the solar wind.",
                "broll": "earth magnetic field diagram",
                "media_mode": "explain",
            },
            "How the Northern Lights Are Created",
        )
        generic_earth = StockCandidate(
            provider="nasa",
            provider_id="earth-view",
            query="earth magnetic field diagram",
            title="Earth seen from space",
            description="Blue planet rotating in orbit",
            duration_sec=8,
            width=1920,
            height=1080,
        )

        result = select_best_candidate(intent, [generic_earth], minimum_score=0)

        self.assertIsNone(result.selected_candidate)
        self.assertIn(
            "requested explanatory format not proven: diagram",
            result.score.rejection_reasons,
        )

    def test_show_mode_accepts_authentic_subject_media_without_diagram_proof(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Penguins do not freeze because dense feathers trap warm air.",
                "broll": "penguin feathers close up diagram",
                "media_mode": "show",
            },
            "How Penguins Survive Antarctica",
        )
        candidate = StockCandidate(
            provider="wikimedia",
            provider_id="penguin-feathers",
            query="penguin feathers close up diagram",
            title="King Penguin feathers close up",
            description="Close-up photograph of penguin plumage",
            duration_sec=None,
            width=1080,
            height=1600,
            is_image=True,
        )

        result = select_best_candidate(intent, [candidate], minimum_score=0)

        self.assertIs(result.selected_candidate, candidate)
        self.assertEqual(result.to_metadata()["media_mode"], "show")
        self.assertNotIn(
            "requested explanatory format not proven: diagram",
            result.score.rejection_reasons,
        )

    def test_solar_storm_scene_uses_astronomy_domain(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "A solar storm sends particles toward Earth.",
                "broll": "bright sun solar flare",
            },
            "How the Northern Lights Are Created",
        )
        candidate = StockCandidate(
            provider="nasa",
            provider_id="solar-flare",
            query="bright sun solar flare",
            title="solar flare erupts from the sun",
            duration_sec=8,
            width=1920,
            height=1080,
        )

        metadata = select_best_candidate(intent, [candidate], minimum_score=0).to_metadata()

        self.assertEqual(metadata["visual_domain"], "astronomy")

    def test_astronomy_rejects_human_lab_clip_despite_gas_evidence(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Oxygen and nitrogen atoms collide high in the atmosphere.",
                "broll": "colorful gas particles colliding",
            },
            "How the Northern Lights Are Created",
        )
        lab_clip = StockCandidate(
            provider="nasa",
            provider_id="gas-lab",
            query="colorful gas particles colliding",
            title="scientist person holding a gas experiment in a laboratory",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        result = select_best_candidate(intent, [lab_clip], minimum_score=0)

        self.assertIsNone(result.selected_candidate)
        self.assertIn("wrong-domain content for astronomy", result.score.rejection_reasons)

    def test_selection_diagnostics_include_independent_quality_gate(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Roman aqueducts carried water across valleys.",
                "broll": "roman aqueduct ruins",
            },
            "How Roman Aqueducts Changed Civilization",
        )
        candidate = StockCandidate(
            provider="wikimedia",
            provider_id="roman-aqueduct",
            query="roman aqueduct ruins",
            title="ancient Roman aqueduct ruins",
            duration_sec=8,
            width=1080,
            height=1920,
        )

        metadata = select_best_candidate(intent, [candidate], minimum_score=0).to_metadata()

        self.assertTrue(metadata["quality_gate_passed"])
        self.assertEqual(metadata["visual_domain"], "history")
        self.assertGreater(metadata["evidence_score"], 0)
        self.assertIn("accepted", metadata["acceptance_reason"])

    def test_provider_order_is_preserved_when_later_provider_scores_higher(self) -> None:
        intent = build_visual_intent(
            {"narration": "Arctic fox walks in snow.", "broll": "arctic fox walking"},
            "Arctic Fox Survival Tricks",
        )
        pexels = StockCandidate(
            provider="pexels",
            provider_id="p1",
            query="arctic fox walking",
            title="arctic fox in snow",
            duration_sec=5,
            width=1080,
            height=1920,
        )
        pixabay = StockCandidate(
            provider="pixabay",
            provider_id="x1",
            query="arctic fox walking",
            title="wild arctic fox walking close up in snow",
            duration_sec=5,
            width=1080,
            height=1920,
        )

        result = select_first_available_provider(
            intent,
            [("pexels", [pexels]), ("pixabay", [pixabay])],
            target_duration_sec=5,
        )

        self.assertEqual(result.selected_candidate.provider, "pexels")

    def test_metadata_preservation_for_selected_media_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "arctic_fox.mp4"
            path.write_bytes(b"video")
            intent = build_visual_intent(
                {"narration": "Arctic fox walks in snow.", "broll": "arctic fox walking"},
                "Arctic Fox Survival Tricks",
            )
            result = select_best_candidate(
                intent,
                [candidate_from_local_path(path, "arctic fox walking")],
                target_duration_sec=5,
                minimum_score=0,
            )

            asset = auto_short._media_asset_from_path(
                path,
                source=MediaSource.LOCAL,
                idx=0,
                metadata=result.to_metadata(),
            )

            self.assertIn("selection", asset.metadata)
            self.assertEqual(asset.metadata["selection"]["provider"], "local")
            self.assertEqual(asset.metadata["selection"]["candidate_count"], 1)
            self.assertIn("portrait_score", asset.metadata["selection"])
            self.assertIn("relevance_score", asset.metadata["selection"])
            self.assertIn("confidence_level", asset.metadata["selection"])
            self.assertIn("fallback_level", asset.metadata["selection"])

    def test_pexels_compatibility_wrapper_downloads_only_selected_candidate(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "videos": [
                {
                    "id": "bad",
                    "url": "https://pexels.com/video/husky-dog-zoo-cage-person-bad/",
                    "duration": 8,
                    "user": {"name": "creator"},
                    "video_files": [{"link": "https://cdn/bad.mp4", "width": 1920, "height": 1080}],
                },
                {
                    "id": "good",
                    "url": "https://pexels.com/video/wild-arctic-fox-pouncing-in-snow-good/",
                    "duration": 8,
                    "user": {"name": "creator"},
                    "video_files": [{"link": "https://cdn/good.mp4", "width": 1080, "height": 1920}],
                },
            ]
        }

        old_key = auto_short.PEXELS_API_KEY
        auto_short.PEXELS_API_KEY = "test-key"
        auto_short._MEDIA_SELECTION_DIAGNOSTICS.clear()
        try:
            with patch("requests.get", return_value=response), patch.object(
                auto_short,
                "_download_to",
                return_value=True,
            ) as download:
                path = auto_short.fetch_pexels_video(
                    ["arctic fox pouncing"],
                    0,
                    set(),
                    target_duration=5,
                    fallback="Arctic Fox Survival Tricks",
                    narration="The arctic fox pounces through snow.",
                )
        finally:
            auto_short.PEXELS_API_KEY = old_key

        self.assertEqual(path, auto_short.OUT_DIR / "broll_0.mp4")
        download.assert_called_once_with("https://cdn/good.mp4", auto_short.OUT_DIR / "broll_0.mp4")
        self.assertEqual(
            auto_short._MEDIA_SELECTION_DIAGNOSTICS[0]["selection"]["provider_id"],
            "good",
        )

    def test_configured_json_provider_wrapper_downloads_selected_candidate(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "items": [
                {
                    "id": "generic",
                    "title": "person in office",
                    "download_url": "https://cdn/generic.mp4",
                    "width": 1920,
                    "height": 1080,
                    "duration": 8,
                },
                {
                    "id": "qr",
                    "title": "qr code phone scan close up technology",
                    "download_url": "https://cdn/qr.mp4",
                    "width": 1080,
                    "height": 1920,
                    "duration": 8,
                    "license": "Coverr License",
                    "attribution": "Coverr",
                },
            ]
        }
        old_url = auto_short.COVERR_API_URL
        auto_short.COVERR_API_URL = "https://provider.test/search"
        auto_short._MEDIA_SELECTION_DIAGNOSTICS.clear()
        try:
            with patch("requests.get", return_value=response), patch.object(
                auto_short,
                "_download_to",
                return_value=True,
            ) as download:
                path = auto_short.fetch_coverr_video(
                    ["qr code phone scan"],
                    3,
                    set(),
                    target_duration=5,
                    fallback="How QR Codes Actually Work",
                    narration="QR codes store data in black and white squares.",
                )
        finally:
            auto_short.COVERR_API_URL = old_url

        self.assertEqual(path, auto_short.OUT_DIR / "broll_3.mp4")
        download.assert_called_once_with("https://cdn/qr.mp4", auto_short.OUT_DIR / "broll_3.mp4")
        self.assertEqual(auto_short._MEDIA_SELECTION_DIAGNOSTICS[3]["selection"]["provider"], "coverr")

    def test_adaptive_retrieval_adds_landscape_exact_subject_candidates(self) -> None:
        portrait_response = Mock()
        portrait_response.raise_for_status.return_value = None
        portrait_response.json.return_value = {
            "videos": [
                {
                    "id": "fish",
                    "url": "https://pexels.com/video/underwater-fish-1/",
                    "duration": 8,
                    "user": {"name": "creator"},
                    "video_files": [{"link": "https://cdn/fish.mp4", "width": 1080, "height": 1920}],
                }
            ]
        }
        landscape_response = Mock()
        landscape_response.raise_for_status.return_value = None
        landscape_response.json.return_value = {
            "videos": [
                {
                    "id": "octo",
                    "url": "https://pexels.com/video/octopus-camouflages-in-coral-reef-octo/",
                    "duration": 9,
                    "user": {"name": "creator"},
                    "video_files": [{"link": "https://cdn/octo.mp4", "width": 1920, "height": 1080}],
                }
            ]
        }
        strategy = SimpleNamespace(
            provider_plans=(SimpleNamespace(provider_id="pexels", queries=("octopus camouflage",), score=1.0),)
        )
        intent = build_visual_intent(
            {
                "broll": "octopus camouflage",
                "broll_queries": ["octopus camouflage"],
                "primary_subject": "octopus",
                "supporting_subjects": ["common octopus", "mimic octopus"],
                "media_mode": "show",
            },
            "The Octopus That Becomes Anything Underwater",
        )
        old_key = auto_short.PEXELS_API_KEY
        old_out = auto_short.OUT_DIR
        old_min = auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES
        auto_short.PEXELS_API_KEY = "test-key"
        auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES = 5
        auto_short._ADAPTIVE_SEARCH_DIAGNOSTICS.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auto_short.OUT_DIR = Path(tmp)
                with patch("requests.get", side_effect=[portrait_response, landscape_response]), patch.object(
                    auto_short,
                    "_download_to",
                    return_value=True,
                ) as download:
                    path = auto_short._fetch_adaptive_broll(
                        strategy,
                        idx=4,
                        fallback="The Octopus That Becomes Anything Underwater",
                        narration="The octopus disappears into coral.",
                        used_set=set(),
                        target_duration=5,
                        intent=intent,
                    )
        finally:
            auto_short.PEXELS_API_KEY = old_key
            auto_short.OUT_DIR = old_out
            auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES = old_min

        self.assertEqual(path.name, "broll_4.mp4")
        download.assert_called_once_with("https://cdn/octo.mp4", path)
        report = auto_short._ADAPTIVE_SEARCH_DIAGNOSTICS[4]
        self.assertEqual(report["portrait_candidates_found"], 1)
        self.assertEqual(report["landscape_candidates_found"], 1)
        self.assertTrue(report["provider_expansion_triggered"])
        self.assertEqual(report["final_provider_selected"], "pexels")
        self.assertEqual(report["selected_provider_id"], "octo")

    def test_adaptive_retrieval_expands_to_pixabay_when_pexels_is_weak(self) -> None:
        pexels_portrait = Mock()
        pexels_portrait.raise_for_status.return_value = None
        pexels_portrait.json.return_value = {
            "videos": [
                {
                    "id": "reef",
                    "url": "https://pexels.com/video/generic-underwater-reef/",
                    "duration": 8,
                    "user": {"name": "creator"},
                    "video_files": [{"link": "https://cdn/reef.mp4", "width": 1080, "height": 1920}],
                }
            ]
        }
        pexels_landscape = Mock()
        pexels_landscape.raise_for_status.return_value = None
        pexels_landscape.json.return_value = {"videos": []}
        pixabay_response = Mock()
        pixabay_response.raise_for_status.return_value = None
        pixabay_response.json.return_value = {
            "hits": [
                {
                    "id": 152482,
                    "tags": "octopus, underwater, sea, coconut octopus, ocean, nature, animal",
                    "duration": 52,
                    "user": "creator",
                    "pageURL": "https://pixabay.com/videos/id-152482/",
                    "videos": {
                        "medium": {"url": "https://cdn/pixabay-octo.mp4", "width": 1080, "height": 1920}
                    },
                }
            ]
        }
        strategy = SimpleNamespace(
            provider_plans=(
                SimpleNamespace(provider_id="pexels", queries=("octopus underwater",), score=1.0),
                SimpleNamespace(provider_id="pixabay", queries=("octopus underwater",), score=1.0),
            )
        )
        intent = build_visual_intent(
            {
                "broll": "octopus underwater",
                "broll_queries": ["octopus underwater"],
                "primary_subject": "octopus",
                "supporting_subjects": ["common octopus"],
                "media_mode": "show",
            },
            "The Octopus That Becomes Anything Underwater",
        )
        old_pexels = auto_short.PEXELS_API_KEY
        old_pixabay = auto_short.PIXABAY_API_KEY
        old_out = auto_short.OUT_DIR
        old_min = auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES
        auto_short.PEXELS_API_KEY = "pexels"
        auto_short.PIXABAY_API_KEY = "pixabay"
        auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES = 1
        auto_short._ADAPTIVE_SEARCH_DIAGNOSTICS.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auto_short.OUT_DIR = Path(tmp)
                with patch(
                    "requests.get",
                    side_effect=[pexels_portrait, pexels_landscape, pixabay_response],
                ), patch.object(auto_short, "_download_to", return_value=True) as download:
                    path = auto_short._fetch_adaptive_broll(
                        strategy,
                        idx=5,
                        fallback="The Octopus That Becomes Anything Underwater",
                        narration="The octopus moves across the sea floor.",
                        used_set=set(),
                        target_duration=5,
                        intent=intent,
                    )
        finally:
            auto_short.PEXELS_API_KEY = old_pexels
            auto_short.PIXABAY_API_KEY = old_pixabay
            auto_short.OUT_DIR = old_out
            auto_short.AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES = old_min

        self.assertEqual(path.name, "broll_5.mp4")
        download.assert_called_once_with("https://cdn/pixabay-octo.mp4", path)
        report = auto_short._ADAPTIVE_SEARCH_DIAGNOSTICS[5]
        self.assertTrue(report["provider_expansion_triggered"])
        self.assertEqual([item["provider"] for item in report["providers_searched"]], ["pexels", "pixabay"])
        self.assertEqual(report["final_provider_selected"], "pixabay")

    def test_qr_explainer_fallback_creates_portrait_image_with_diagnostics(self) -> None:
        old_out_dir = auto_short.OUT_DIR
        auto_short._MEDIA_SELECTION_DIAGNOSTICS.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auto_short.OUT_DIR = Path(tmp)
                self.assertTrue(auto_short._needs_qr_explainer_fallback(
                    "animated QR code structure",
                    "The three corner squares orient your phone.",
                    "How QR Codes Actually Work",
                ))
                path = auto_short._generate_qr_explainer_image("animated QR code structure", 2)

                self.assertTrue(path.exists())
                with Image.open(path) as img:
                    self.assertEqual(img.size, (auto_short.WIDTH, auto_short.HEIGHT))
        finally:
            auto_short.OUT_DIR = old_out_dir

    def test_local_explainer_fallback_is_portrait_safe(self) -> None:
        old_out_dir = auto_short.OUT_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auto_short.OUT_DIR = Path(tmp)
                path = auto_short._generate_local_explainer_image(
                    "glowing solar particles animation",
                    1,
                )

                self.assertTrue(path.exists())
                with Image.open(path) as image:
                    self.assertEqual(image.size, (auto_short.WIDTH, auto_short.HEIGHT))
        finally:
            auto_short.OUT_DIR = old_out_dir

    def test_fetch_broll_uses_hybrid_composer_after_provider_rejections(self) -> None:
        old_out_dir = auto_short.OUT_DIR
        old_pexels = auto_short.PEXELS_API_KEY
        auto_short._MEDIA_SELECTION_DIAGNOSTICS.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                auto_short.OUT_DIR = Path(tmp)
                auto_short.PEXELS_API_KEY = "test-key"
                provider_mocks = {
                    name: Mock(return_value=None)
                    for name in (
                        "fetch_pexels_video",
                        "fetch_pixabay_video",
                        "fetch_nasa_video",
                        "fetch_mixkit_video",
                        "fetch_coverr_video",
                        "fetch_videvo_video",
                        "fetch_wikimedia_media",
                        "fetch_noaa_media",
                        "fetch_esa_media",
                        "fetch_usgs_media",
                        "fetch_smithsonian_media",
                        "fetch_nps_media",
                        "fetch_usfws_media",
                        "fetch_library_of_congress_media",
                        "fetch_europeana_media",
                        "fetch_flickr_commons_media",
                        "fetch_internet_archive_media",
                    )
                }
                with patch.multiple(auto_short, **provider_mocks), patch.object(
                    auto_short,
                    "_fetch_adaptive_broll",
                    return_value=None,
                ), patch.object(
                    auto_short,
                    "is_gemini_image_available",
                    return_value=False,
                ), patch.object(auto_short, "_save_persistent_used"):
                    path = auto_short.fetch_broll(
                        ["earth magnetic field diagram"],
                        2,
                        fallback="How the Northern Lights Are Created",
                        narration="Earth's magnetic field redirects solar particles.",
                        used_set=set(),
                        no_interactive=True,
                    )

                self.assertTrue(path.exists())
                self.assertEqual(
                    auto_short._MEDIA_SELECTION_DIAGNOSTICS[2]["selection"]["fallback_level"],
                    "hybrid_composition",
                )
                self.assertEqual(
                    auto_short._MEDIA_SELECTION_DIAGNOSTICS[2]["selection"]["provider"],
                    "hybrid_composer",
                )
        finally:
            auto_short.OUT_DIR = old_out_dir
            auto_short.PEXELS_API_KEY = old_pexels

    def test_fetch_broll_skips_missing_provider_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.mp4"
            valid = Path(tmp) / "valid.mp4"
            valid.write_bytes(b"video")
            strategy = SimpleNamespace(
                provider_plans=(
                    SimpleNamespace(provider_id="pexels", queries=("aurora",), score=1.0),
                    SimpleNamespace(provider_id="pixabay", queries=("aurora",), score=1.0),
                )
            )

            with patch.object(auto_short, "_build_search_strategy", return_value=strategy), patch.object(
                auto_short,
                "_fetch_adaptive_broll",
                return_value=None,
            ), patch.object(
                auto_short,
                "fetch_pexels_video",
                return_value=missing,
            ) as pexels, patch.object(
                auto_short,
                "fetch_pixabay_video",
                return_value=valid,
            ) as pixabay, patch.object(auto_short, "_save_persistent_used"):
                out = auto_short.fetch_broll(
                    ["aurora"],
                    1,
                    fallback="mind-blowing facts about our solar system",
                    narration="Earth's magnetism creates auroras.",
                    used_set=set(),
                    no_interactive=True,
                )

        self.assertEqual(out, valid)
        pexels.assert_called_once()
        pixabay.assert_called_once()

    def test_wikimedia_candidate_normalization_from_page(self) -> None:
        candidate = auto_short._wikimedia_candidate_from_page(
            {
                "title": "File:Roman aqueduct.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/roman.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Roman_aqueduct.jpg",
                        "mime": "image/jpeg",
                        "width": 1200,
                        "height": 800,
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Artist": {"value": "Example Creator"},
                        },
                    }
                ],
            },
            "roman aqueduct ruins",
        )

        self.assertEqual(candidate.provider, "wikimedia")
        self.assertTrue(candidate.is_image)
        self.assertEqual(candidate.raw_metadata["license"], "CC BY-SA 4.0")

    def test_wikimedia_candidate_rejects_non_raster_archive_formats(self) -> None:
        for title, mime, url in (
            ("File:Aqueduct-de-nimes.svg", "image/svg+xml", "https://upload.wikimedia.org/aqueduct.svg"),
            ("File:Vegetable Mould.djvu", "image/vnd.djvu", "https://upload.wikimedia.org/book.djvu"),
            ("File:Archive scan.pdf", "application/pdf", "https://upload.wikimedia.org/scan.pdf"),
        ):
            with self.subTest(title=title):
                candidate = auto_short._wikimedia_candidate_from_page(
                    {
                        "title": title,
                        "imageinfo": [
                            {
                                "url": url,
                                "mime": mime,
                                "width": 1200,
                                "height": 800,
                            }
                        ],
                    },
                    "roman aqueduct",
                )

                self.assertIsNone(candidate)

    def test_valid_media_path_rejects_mislabeled_image_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broll_1.jpg"
            path.write_bytes(b"not really a jpeg")

            self.assertFalse(auto_short._valid_media_path(path))


if __name__ == "__main__":
    unittest.main()
