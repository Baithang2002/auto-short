from __future__ import annotations

import unittest

from tests.unit import _path  # noqa: F401

from autovideo.intelligence import TopicCategory, build_topic_metadata, classify_topic


class TopicClassificationTests(unittest.TestCase):
    def test_qr_codes_classify_as_technology(self) -> None:
        result = classify_topic("How QR Codes Actually Work")

        self.assertEqual(result.primary, TopicCategory.TECHNOLOGY)
        self.assertNotIn(TopicCategory.WILDLIFE, result.all_categories)

    def test_northern_lights_classify_as_space_and_earth_science(self) -> None:
        result = classify_topic("How the Northern Lights Are Created")

        self.assertEqual(result.primary, TopicCategory.SPACE)
        self.assertIn(TopicCategory.EARTH_SCIENCE, result.secondary)

    def test_ocean_currents_classify_as_ocean_and_earth_science(self) -> None:
        result = classify_topic("The Science Behind Earth's Strongest Ocean Currents")

        self.assertEqual(result.primary, TopicCategory.OCEAN_SCIENCE)
        self.assertIn(TopicCategory.EARTH_SCIENCE, result.secondary)

    def test_lightning_classifies_as_weather_not_wildlife(self) -> None:
        result = classify_topic("How Lightning Is Created Inside Storm Clouds")

        self.assertEqual(result.primary, TopicCategory.WEATHER)
        self.assertIn(TopicCategory.EARTH_SCIENCE, result.secondary)
        self.assertNotIn(TopicCategory.WILDLIFE, result.all_categories)

    def test_roman_aqueducts_classify_as_history_and_engineering(self) -> None:
        result = classify_topic("How Roman Aqueducts Changed Civilization")

        self.assertEqual(result.primary, TopicCategory.HISTORY)
        self.assertIn(TopicCategory.ENGINEERING, result.secondary)

    def test_arctic_fox_classifies_as_wildlife(self) -> None:
        result = classify_topic("Arctic Fox Survival Tricks")

        self.assertEqual(result.primary, TopicCategory.WILDLIFE)

    def test_segment_incidental_tokens_do_not_override_focused_topic(self) -> None:
        result = classify_topic(
            "Red Panda Tree Climbing",
            segments=[{
                "narration": "Storm clouds affect ocean currents while NASA software records lightning.",
                "broll": "space technology weather ocean",
            }],
        )

        self.assertEqual(result.primary, TopicCategory.WILDLIFE)

    def test_segments_are_not_used_as_a_subject_when_topic_is_unfocused(self) -> None:
        result = classify_topic(
            "A Surprising Everyday Mystery",
            segments=[{"narration": "A snow leopard hunts an arctic fox.", "broll": "wildlife"}],
        )

        self.assertNotEqual(result.primary, TopicCategory.WILDLIFE)


class TopicMetadataTests(unittest.TestCase):
    def test_qr_metadata_uses_technology_tags_not_channel_nature_tags(self) -> None:
        metadata = build_topic_metadata(
            video_topic="How QR Codes Actually Work",
            title="Unlock The QR Code Secret | Nature",
            description="Learn how QR codes store data.",
            existing_hashtags="#nature #wildlife #shorts",
        )

        self.assertEqual(metadata.title, "Unlock The QR Code Secret")
        self.assertIn("#technology", metadata.hashtags)
        self.assertIn("#qrcode", metadata.hashtags)
        self.assertNotIn("#nature", metadata.hashtags)
        self.assertNotIn("#wildlife", metadata.hashtags)
        self.assertIn("qr code", metadata.youtube_tags)

    def test_northern_lights_metadata_uses_space_and_earth_science(self) -> None:
        metadata = build_topic_metadata(
            video_topic="How the Northern Lights Are Created",
            title="Northern Lights Explained",
        )

        self.assertIn("#space", metadata.hashtags)
        self.assertIn("#earthscience", metadata.hashtags)
        self.assertNotIn("#education", metadata.hashtags)
        self.assertEqual(metadata.category_id, "28")

    def test_ocean_currents_metadata_uses_ocean_science(self) -> None:
        metadata = build_topic_metadata(
            video_topic="The Science Behind Earth's Strongest Ocean Currents",
            title="The Ocean's Hidden Power",
        )

        self.assertIn("#ocean", metadata.hashtags)
        self.assertIn("#oceanscience", metadata.hashtags)
        self.assertIn("ocean science", metadata.youtube_tags)

    def test_roman_aqueduct_metadata_uses_history_and_engineering(self) -> None:
        metadata = build_topic_metadata(
            video_topic="How Roman Aqueducts Changed Civilization",
            title="Rome Built The Impossible",
            existing_hashtags="#nature #earth #physics #energy",
        )

        self.assertIn("#history", metadata.hashtags)
        self.assertIn("#engineering", metadata.hashtags)
        self.assertIn("ancient rome", metadata.youtube_tags)
        self.assertNotIn("#nature", metadata.hashtags)
        self.assertNotIn("#earth", metadata.hashtags)
        self.assertNotIn("#physics", metadata.hashtags)

    def test_legacy_output_shape_is_preserved(self) -> None:
        metadata = build_topic_metadata(
            video_topic="Arctic Fox Survival Tricks",
            title="Arctic Fox Survival Tricks",
            existing_hashtags="#shorts, #animals",
        )

        self.assertIsInstance(metadata.title, str)
        self.assertIsInstance(metadata.description, str)
        self.assertIsInstance(metadata.instagram_caption, str)
        self.assertIsInstance(metadata.hashtags, tuple)
        self.assertIsInstance(metadata.youtube_tags, str)
        self.assertIn("#shorts", metadata.hashtags)
        self.assertIn("#wildlife", metadata.hashtags)

    def test_wildlife_metadata_is_species_targeted_and_uses_pets_animals_category(self) -> None:
        metadata = build_topic_metadata(
            video_topic="How Snow Leopards Hunt on Mountain Cliffs",
            title="The Snow Leopard's Impossible Hunt | Biology",
            existing_hashtags="#science #education #shorts",
        )

        self.assertEqual(metadata.title, "The Snow Leopard's Impossible Hunt")
        self.assertEqual(metadata.classification.primary, TopicCategory.WILDLIFE)
        self.assertEqual(metadata.category_id, "15")
        self.assertIn("#snowleopard", metadata.hashtags)
        self.assertIn("snow leopard", metadata.youtube_tags)
        self.assertIn("#shorts", metadata.hashtags)
        self.assertNotIn("#science", metadata.hashtags)
        self.assertNotIn("#education", metadata.hashtags)

    def test_unknown_topic_keeps_safe_default_youtube_category(self) -> None:
        metadata = build_topic_metadata(
            video_topic="A Surprising Everyday Mystery",
            title="You Will Never Guess This",
        )

        self.assertEqual(metadata.category_id, "27")

    def test_known_legacy_suffixes_are_removed_without_replacement(self) -> None:
        suffixes = (
            "Earth Science", "Weather", "Climate", "Biology", "Chemistry", "Environment",
            "Geography", "Physics", "Astronomy", "Education",
        )
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                metadata = build_topic_metadata(
                    video_topic="How Lightning Forms",
                    title=f"Lightning in Slow Motion | {suffix}",
                )
                self.assertEqual(metadata.title, "Lightning in Slow Motion")


if __name__ == "__main__":
    unittest.main()
