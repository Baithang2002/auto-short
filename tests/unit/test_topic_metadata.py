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

    def test_roman_aqueducts_classify_as_history_and_engineering(self) -> None:
        result = classify_topic("How Roman Aqueducts Changed Civilization")

        self.assertEqual(result.primary, TopicCategory.HISTORY)
        self.assertIn(TopicCategory.ENGINEERING, result.secondary)

    def test_arctic_fox_classifies_as_wildlife(self) -> None:
        result = classify_topic("Arctic Fox Survival Tricks")

        self.assertEqual(result.primary, TopicCategory.WILDLIFE)


class TopicMetadataTests(unittest.TestCase):
    def test_qr_metadata_uses_technology_tags_not_channel_nature_tags(self) -> None:
        metadata = build_topic_metadata(
            video_topic="How QR Codes Actually Work",
            title="Unlock The QR Code Secret | Nature",
            description="Learn how QR codes store data.",
            existing_hashtags="#nature #wildlife #shorts",
        )

        self.assertEqual(metadata.title, "Unlock The QR Code Secret | Technology")
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
        self.assertIn("#earth", metadata.hashtags)
        self.assertIn("#science", metadata.hashtags)

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


if __name__ == "__main__":
    unittest.main()
