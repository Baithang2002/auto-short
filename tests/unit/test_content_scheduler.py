import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.unit import _path  # noqa: F401

from autovideo.intelligence import (
    AutonomousContentScheduler,
    ContentHistoryRecord,
    ContentHistoryStore,
    ContentSchedulerConfig,
    DocumentaryViabilityDecision,
    JsonTopicSource,
    TextTopicSource,
    TopicCandidate,
    load_topic_sources,
    topic_identity,
)


class _ViabilityEngine:
    def __init__(self, scores: dict[str, tuple[float, DocumentaryViabilityDecision]]) -> None:
        self.scores = scores

    def evaluate(self, topic: str):
        score, decision = self.scores[topic]
        return type("Report", (), {"overall_score": score, "decision": decision})()


class ContentSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 18, tzinfo=UTC)
        self.config = ContentSchedulerConfig(
            topic_cooldown_days=90,
            subject_cooldown_days=180,
            category_cooldown_days=7,
            evergreen_topics=(),
        )

    def test_text_source_ignores_comments_blank_lines_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topics.txt"
            path.write_text("# heading\n\nOctopus facts\n octopus facts \nVolcanoes\n", encoding="utf-8")

            candidates = TextTopicSource(path).load()

        self.assertEqual([candidate.topic for candidate in candidates], ["Octopus facts", "Volcanoes"])

    def test_json_source_supports_strings_and_topic_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topics.json"
            path.write_text(json.dumps({"topics": ["Aurora", {"topic": "Roman roads"}]}), encoding="utf-8")

            candidates = JsonTopicSource(path).load()

        self.assertEqual([candidate.topic for candidate in candidates], ["Aurora", "Roman roads"])

    def test_scheduler_selects_highest_ranked_approved_candidate(self) -> None:
        volcano = "Why Volcanoes Create New Land"
        penguins = "How Penguins Survive Antarctica"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                volcano: (0.91, DocumentaryViabilityDecision.APPROVED),
                penguins: (0.77, DocumentaryViabilityDecision.APPROVED),
            }),
            self.config,
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(volcano, "topics.txt"), TopicCandidate(penguins, "topics.txt")])

        self.assertEqual(result.selected.topic, volcano)
        self.assertEqual(result.selected.decision.value, "SELECTED")

    def test_review_is_selected_when_no_approved_topic_exists(self) -> None:
        review = "Invisible forces in your mind"
        skipped = "Consciousness and invisible memory formation"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                review: (0.55, DocumentaryViabilityDecision.REVIEW),
                skipped: (0.30, DocumentaryViabilityDecision.SKIP),
            }),
            self.config,
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(review, "topics.txt"), TopicCandidate(skipped, "topics.txt")])

        decisions = {candidate.topic: candidate.decision.value for candidate in result.candidates}
        self.assertEqual(result.selected.topic, review)
        self.assertEqual(result.selected.selection_path, "review_fallback")
        self.assertEqual(decisions[review], "SELECTED")
        self.assertEqual(decisions[skipped], "REJECTED")

    def test_primary_subject_cooldown_is_overridden_only_by_emergency_fallback(self) -> None:
        topic = "Why Octopuses Are So Intelligent"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({topic: (0.92, DocumentaryViabilityDecision.APPROVED)}),
            self.config,
            now=lambda: self.now,
        )
        history = [ContentHistoryRecord(
            topic="The Hidden World of Octopuses",
            primary_subject="octopus",
            category="Wildlife",
            documentary_angle="reveal",
            viability_score=0.9,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
        )]

        result = scheduler.schedule([TopicCandidate(topic, "topics.txt")], history)

        self.assertEqual(result.selected.topic, topic)
        self.assertEqual(result.selected.selection_path, "emergency_source_fallback")
        self.assertIn("primary subject is inside cooldown", result.candidates[0].reasons)

    def test_recent_category_is_penalized_but_not_rejected(self) -> None:
        topic = "Why an Octopus Changes Color"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({topic: (0.90, DocumentaryViabilityDecision.APPROVED)}),
            self.config,
            now=lambda: self.now,
        )
        history = [ContentHistoryRecord(
            topic="How a Shark Survives the Ocean",
            primary_subject="shark",
            category="Wildlife",
            documentary_angle="process",
            viability_score=0.8,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
        )]

        result = scheduler.schedule([TopicCandidate(topic, "topics.txt")], history)

        self.assertEqual(result.selected.topic, topic)
        self.assertEqual(result.selected.category_diversity_score, 0.0)

    def test_history_persists_deferral_then_marks_selected_topic_generated(self) -> None:
        selected = "Why Volcanoes Create New Land"
        review = "Invisible forces in your mind"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                selected: (0.90, DocumentaryViabilityDecision.APPROVED),
                review: (0.55, DocumentaryViabilityDecision.REVIEW),
            }),
            self.config,
            now=lambda: self.now,
        )
        result = scheduler.schedule([TopicCandidate(selected, "topics.txt"), TopicCandidate(review, "topics.txt")])

        with tempfile.TemporaryDirectory() as tmp:
            store = ContentHistoryStore(Path(tmp) / "content_history.json")
            store.record_decisions(result, run_id="run-1")
            self.assertTrue(store.mark_generated(run_id="run-1", generated_at="2026-07-18T00:01:00Z"))
            records = store.load()

        statuses = {record.topic: record.status for record in records}
        self.assertEqual(statuses[selected], "generated")
        self.assertEqual(statuses[review], "deferred")

    def test_topic_identity_keeps_specific_subject_modifier(self) -> None:
        self.assertEqual(topic_identity("How Greenland Sharks Live for Centuries").primary_subject, "greenland shark")
        self.assertEqual(topic_identity("How Vampire Squid Survive the Deep").primary_subject, "vampire squid")

    def test_topic_identity_prefers_subject_over_leading_descriptor(self) -> None:
        self.assertEqual(
            topic_identity("The Immortal Jellyfish That Can Live Forever").primary_subject,
            "jellyfish",
        )

    def test_evergreen_pool_is_selected_when_sources_have_no_viable_topics(self) -> None:
        rejected = "Consciousness and invisible memory formation"
        evergreen = "Why Volcanoes Create New Land"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                rejected: (0.30, DocumentaryViabilityDecision.SKIP),
                evergreen: (0.90, DocumentaryViabilityDecision.APPROVED),
            }),
            ContentSchedulerConfig(evergreen_topics=(evergreen,)),
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(rejected, "topics.txt")])

        self.assertEqual(result.selected.topic, evergreen)
        self.assertEqual(result.selected.source, "evergreen")

    def test_empty_sources_use_configured_evergreen_pool(self) -> None:
        evergreen = "How Penguins Survive Antarctica"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({evergreen: (0.80, DocumentaryViabilityDecision.APPROVED)}),
            ContentSchedulerConfig(evergreen_topics=(evergreen,)),
            now=lambda: self.now,
        )

        result = scheduler.schedule([])

        self.assertEqual(result.selected.topic, evergreen)
        self.assertEqual(result.selected.selection_path, "evergreen_fallback")

    def test_config_reads_topic_sources_and_evergreen_pool(self) -> None:
        config = ContentSchedulerConfig.from_env({
            "AUTO_VIDEO_SCHEDULER_TOPIC_SOURCES": "ideas.json,topics.txt",
            "AUTO_VIDEO_SCHEDULER_EVERGREEN_TOPICS": "Aurora,Volcanoes",
            "AUTO_VIDEO_SCHEDULER_MAX_CANDIDATES": "12",
        })

        self.assertEqual(config.topic_sources, ("ideas.json", "topics.txt"))
        self.assertEqual(config.evergreen_topics, ("Aurora", "Volcanoes"))
        self.assertEqual(config.max_candidates, 12)

    def test_multiple_sources_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "topics.txt").write_text("Octopus\n", encoding="utf-8")
            (root / "topics.json").write_text(json.dumps(["octopus", "Volcano"]), encoding="utf-8")

            candidates = load_topic_sources([TextTopicSource(root / "topics.txt"), JsonTopicSource(root / "topics.json")])

        self.assertEqual([candidate.topic for candidate in candidates], ["Octopus", "Volcano"])


if __name__ == "__main__":
    unittest.main()
