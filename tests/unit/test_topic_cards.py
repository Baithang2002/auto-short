"""Tests for structured nature cards and their scheduler integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.unit import _path  # noqa: F401

import topic_qualify
from autovideo.intelligence import (
    AutonomousContentScheduler,
    ContentHistoryRecord,
    ContentHistoryStore,
    ContentSchedulerConfig,
    DEFAULT_TOPIC_CARD_PATH,
    DEFAULT_TOPIC_CARD_SOURCE,
    DocumentaryViabilityDecision,
    PILLAR_ALLOCATION,
    TopicCandidate,
    TopicCard,
    load_topic_card_catalog,
    topic_source_for_path,
)
from autovideo.intelligence.topic_cards import find_topic_card


class _ViabilityEngine:
    def evaluate(self, _topic: str):
        return type(
            "Report",
            (),
            {
                "overall_score": 0.9,
                "decision": DocumentaryViabilityDecision.APPROVED,
            },
        )()


def _card(card_id: str, premise: str, *, subject: str = "beaver") -> TopicCard:
    return TopicCard(
        id=card_id,
        pillar="wildlife",
        subject=subject,
        premise=premise,
        required_entity=subject,
        required_action="performing the defining action",
        hook_queries=(f"{subject} close up",),
        reveal_queries=(f"{subject} action",),
        supporting_queries=(f"{subject} habitat",),
        fallback_visuals=(f"{subject} habitat wide shot",),
        title_angles=(premise,),
        source_difficulty="easy",
    )


def _catalog_payload(cards: list[dict[str, object]]) -> dict[str, object]:
    return {"allocation": dict(PILLAR_ALLOCATION), "cards": cards}


def _raw_card(card_id: str = "wildlife-test") -> dict[str, object]:
    return {
        "id": card_id,
        "pillar": "wildlife",
        "subject": "beaver",
        "premise": "How beavers build dams",
        "required_entity": "beaver",
        "required_action": "building a dam",
        "hook_queries": ["beaver carrying branch"],
        "reveal_queries": ["beaver building dam"],
        "supporting_queries": ["beaver pond"],
        "fallback_visuals": ["stick dam in stream"],
        "title_angles": ["The animal that reshapes streams"],
        "source_difficulty": "easy",
    }


class TopicCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 18, tzinfo=UTC)
        self.config = ContentSchedulerConfig(
            max_candidates=10,
            coverage_proven_topics=(),
            evergreen_topics=(),
            maximum_similarity_threshold=0.95,
        )

    def test_default_catalog_has_declared_allocation_and_representative_cards(self) -> None:
        catalog = load_topic_card_catalog()

        self.assertEqual(dict(catalog.allocation), dict(PILLAR_ALLOCATION))
        self.assertEqual(len(catalog.cards), 7)
        counts = {
            pillar: sum(card.pillar == pillar for card in catalog.cards)
            for pillar in PILLAR_ALLOCATION
        }
        self.assertEqual(counts, {
            "wildlife": 5,
            "ocean": 2,
        })

    def test_loader_rejects_empty_critical_retrieval_fields(self) -> None:
        raw = _raw_card()
        raw["reveal_queries"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            path.write_text(json.dumps(_catalog_payload([raw])), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "reveal_queries"):
                load_topic_card_catalog(path)

    def test_loader_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            path.write_text(
                json.dumps(_catalog_payload([_raw_card(), _raw_card("WILDLIFE-TEST")])),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate topic-card id"):
                load_topic_card_catalog(path)

    def test_loader_excludes_non_nature_pillars(self) -> None:
        raw = _raw_card()
        raw["pillar"] = "technology"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            path.write_text(json.dumps(_catalog_payload([raw])), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported pillar"):
                load_topic_card_catalog(path)

    def test_json_topic_source_emits_premise_and_retains_card(self) -> None:
        candidate = topic_source_for_path(DEFAULT_TOPIC_CARD_PATH).load()[0]
        card = candidate.card
        assert card is not None

        self.assertEqual(candidate.topic, card.premise)
        self.assertEqual(card.pillar, "wildlife")
        self.assertTrue(card.required_entity)

    def test_qualification_loader_consumes_card_premises(self) -> None:
        config = ContentSchedulerConfig(
            coverage_proven_topics=(),
            evergreen_topics=(),
        )

        topics = topic_qualify.load_candidates(config)
        first_card_premise = load_topic_card_catalog().cards[0].premise

        self.assertIn(DEFAULT_TOPIC_CARD_SOURCE, config.topic_sources)
        self.assertIn(first_card_premise, topics)

    def test_card_match_normalizes_case_and_whitespace_but_not_subject_only(self) -> None:
        premise = load_topic_card_catalog().cards[0].premise

        matched = find_topic_card(f"  {premise.upper()}  ")
        assert matched is not None
        self.assertEqual(matched.premise, premise)
        self.assertIsNone(find_topic_card("Arctic fox"))

    def test_qualified_and_proven_are_prioritized_before_candidate_limit(self) -> None:
        ready = "How an Arctic fox changes its coat"
        waiting = "How beavers build dams"
        for status in ("qualified", "proven"):
            with self.subTest(status=status):
                scheduler = AutonomousContentScheduler(
                    _ViabilityEngine(),  # type: ignore[arg-type]
                    ContentSchedulerConfig(
                        max_candidates=1,
                        coverage_proven_topics=(),
                        evergreen_topics=(),
                    ),
                    now=lambda: self.now,
                )

                result = scheduler.schedule(
                    [
                        TopicCandidate(waiting, "cards.json"),
                        TopicCandidate(ready, "cards.json"),
                    ],
                    topic_bank_statuses={waiting: "candidate", ready: status},
                )

                assert result.selected is not None
                self.assertEqual(result.selected.topic, ready)
                self.assertEqual(len(result.candidates), 1)
                self.assertEqual(result.selected.topic_bank_status, status)

    def test_card_cooldown_uses_subject_and_premise_not_subject_alone(self) -> None:
        earlier = _card("beaver-dams", "How beavers build dams that reshape a stream")
        fresh = _card("beaver-lodges", "How beavers build lodges that stay dry inside")
        history = [ContentHistoryRecord(
            topic=earlier.topic,
            primary_subject=earlier.subject,
            category=earlier.pillar,
            documentary_angle="process",
            viability_score=0.9,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
            card_id=earlier.id,
            card_pillar=earlier.pillar,
            card_subject=earlier.subject,
            card_premise=earlier.premise,
        )]
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine(),  # type: ignore[arg-type]
            self.config,
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(fresh.topic, "cards.json", fresh)], history)

        assert result.selected is not None
        self.assertEqual(result.selected.topic, fresh.topic)
        self.assertFalse(result.selected.cooldown_active)
        assert result.selected.topic_card is not None
        self.assertEqual(result.selected.topic_card.id, fresh.id)

    def test_card_metadata_is_persisted_for_future_cooldown_checks(self) -> None:
        card = _card("beaver-dams", "How beavers build dams that reshape a stream")
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine(),  # type: ignore[arg-type]
            self.config,
            now=lambda: self.now,
        )
        result = scheduler.schedule([TopicCandidate(card.topic, "cards.json", card)])

        with tempfile.TemporaryDirectory() as directory:
            store = ContentHistoryStore(Path(directory) / "history.json")
            store.record_decisions(result, run_id="card-run")
            record = store.load()[0]

        self.assertEqual(record.card_id, card.id)
        self.assertEqual(record.card_subject, card.subject)
        self.assertEqual(record.card_premise, card.premise)


if __name__ == "__main__":
    unittest.main()
