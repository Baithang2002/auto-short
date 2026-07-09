from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
