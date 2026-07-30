from __future__ import annotations

import unittest

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.intelligence.topic_cards import find_topic_card


class DurationPolicyTests(unittest.TestCase):
    def test_topic_card_duration_is_used_for_known_topics(self) -> None:
        card = find_topic_card("How hummingbirds hover while feeding")

        assert card is not None
        duration, reason = auto_short.automatic_duration_for_topic(card.premise, card)

        self.assertEqual(48, duration)
        self.assertIn(card.id, reason)

    def test_legacy_topics_get_conservative_fallback_duration(self) -> None:
        duration, reason = auto_short.automatic_duration_for_topic(
            "Why Lightning Never Strikes Twice"
        )

        self.assertEqual(52, duration)
        self.assertIn("curiosity", reason)

    def test_explicit_duration_remains_an_override_at_call_site(self) -> None:
        duration, reason = auto_short.resolve_target_duration(
            "How hummingbirds hover while feeding",
            explicit_duration=30,
            card=find_topic_card("How hummingbirds hover while feeding"),
        )

        self.assertEqual(30, duration)
        self.assertEqual("explicit --duration override", reason)


if __name__ == "__main__":
    unittest.main()
