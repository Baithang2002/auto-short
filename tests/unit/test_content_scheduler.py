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
    SchedulingDecision,
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
            coverage_proven_topics=(),
            evergreen_topics=(),
        )

    def test_history_marks_preflight_deferred_topic_as_reconsiderable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContentHistoryStore(Path(directory) / "content_history.json")
            store.save([ContentHistoryRecord(
                topic="Rainforests",
                primary_subject="rainforest",
                category="Nature",
                documentary_angle="overview",
                viability_score=0.7,
                decision="SELECTED",
                status="scheduled",
                reason="scheduled",
                recorded_at="2026-07-18T00:00:00Z",
                run_id="run-1",
            )])
            self.assertTrue(store.mark_deferred(run_id="run-1", reason="coverage too weak"))
            record = store.load()[0]
        self.assertEqual("coverage_deferred", record.status)
        self.assertEqual("coverage too weak", record.reason)

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
            ContentSchedulerConfig(
                coverage_proven_topics=(),
                evergreen_topics=(evergreen,),
            ),
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(rejected, "topics.txt")])

        self.assertEqual(result.selected.topic, evergreen)
        self.assertEqual(result.selected.source, "evergreen")

    def test_empty_sources_use_configured_evergreen_pool(self) -> None:
        evergreen = "How Penguins Survive Antarctica"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({evergreen: (0.80, DocumentaryViabilityDecision.APPROVED)}),
            ContentSchedulerConfig(
                coverage_proven_topics=(),
                evergreen_topics=(evergreen,),
            ),
            now=lambda: self.now,
        )

        result = scheduler.schedule([])

        self.assertEqual(result.selected.topic, evergreen)
        self.assertEqual(result.selected.selection_path, "evergreen_fallback")

    def test_coverage_proven_topics_are_prioritized_before_weak_source_topics(self) -> None:
        proven = "How Bees Make Honey"
        source = "how shipping containers changed world trade"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                proven: (0.70, DocumentaryViabilityDecision.APPROVED),
                source: (0.90, DocumentaryViabilityDecision.APPROVED),
            }),
            ContentSchedulerConfig(
                coverage_proven_topics=(proven,),
                coverage_proven_bonus=0.25,
                evergreen_topics=(),
            ),
            now=lambda: self.now,
        )

        result = scheduler.schedule([TopicCandidate(source, "topics.txt")])

        self.assertEqual(result.selected.topic, proven)
        self.assertEqual(result.selected.source, "coverage_proven")
        self.assertIn("provider-reliability bonus", "; ".join(result.selected.reasons))

    def test_coverage_proven_priority_still_respects_subject_cooldown(self) -> None:
        proven = "How Bees Make Honey"
        source = "Why Volcanoes Create New Land"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                proven: (0.95, DocumentaryViabilityDecision.APPROVED),
                source: (0.72, DocumentaryViabilityDecision.APPROVED),
            }),
            ContentSchedulerConfig(
                coverage_proven_topics=(proven,),
                coverage_proven_bonus=0.25,
                evergreen_topics=(),
            ),
            now=lambda: self.now,
        )
        history = [ContentHistoryRecord(
            topic="Why Bees Build Wax Hives",
            primary_subject="bee",
            category="Wildlife",
            documentary_angle="process",
            viability_score=0.7,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
        )]

        result = scheduler.schedule([TopicCandidate(source, "topics.txt")], history)

        self.assertEqual(result.selected.topic, source)
        decisions = {candidate.topic: candidate.decision for candidate in result.candidates}
        self.assertEqual(decisions[proven], SchedulingDecision.DEFERRED)

    def test_exact_generated_topic_is_never_reselected_by_emergency_fallback(self) -> None:
        topic = "How Bees Make Honey"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({topic: (0.95, DocumentaryViabilityDecision.APPROVED)}),
            ContentSchedulerConfig(
                coverage_proven_topics=(topic,),
                evergreen_topics=(),
                maximum_similarity_threshold=1.0,
            ),
            now=lambda: self.now,
        )
        history = [ContentHistoryRecord(
            topic=topic,
            primary_subject="bee",
            category="Wildlife",
            documentary_angle="process",
            viability_score=0.8,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
        )]

        result = scheduler.schedule([], history)

        self.assertIsNone(result.selected)
        self.assertEqual(result.candidates[0].decision, SchedulingDecision.REJECTED)
        self.assertIn("exact topic was already generated", result.candidates[0].reasons)

    def test_default_coverage_proven_bank_is_large_and_nature_safe(self) -> None:
        config = ContentSchedulerConfig()

        self.assertGreaterEqual(len(config.coverage_proven_topics), 100)
        self.assertIn("How Bees Make Honey", config.coverage_proven_topics)
        self.assertIn("How The Amazon Rainforest Makes Rain", config.coverage_proven_topics)
        self.assertIn("How the Northern Lights Are Created", config.coverage_proven_topics)
        self.assertIn("Why The Grand Canyon Looks So Huge", config.coverage_proven_topics)

    def test_topic_bank_category_rotation_prefers_a_fresh_category(self) -> None:
        wildlife = "How Bees Make Honey"
        nature = "Why Volcanoes Create New Land"
        scheduler = AutonomousContentScheduler(
            _ViabilityEngine({
                wildlife: (0.95, DocumentaryViabilityDecision.APPROVED),
                nature: (0.80, DocumentaryViabilityDecision.APPROVED),
            }),
            ContentSchedulerConfig(
                coverage_proven_topics=(wildlife, nature),
                coverage_proven_bonus=0.10,
                topic_bank_category_cooldown_days=7,
                topic_bank_category_diversity_penalty=0.40,
                evergreen_topics=(),
            ),
            now=lambda: self.now,
        )
        history = [ContentHistoryRecord(
            topic="How Dolphins Use Sound to Hunt",
            primary_subject="dolphin",
            category="Wildlife",
            documentary_angle="process",
            viability_score=0.8,
            decision="SELECTED",
            status="generated",
            reason="",
            recorded_at="2026-07-17T00:00:00Z",
            generated_at="2026-07-17T00:00:00Z",
        )]

        result = scheduler.schedule([], history)

        self.assertEqual(result.selected.topic, nature)
        wildlife_candidate = next(candidate for candidate in result.candidates if candidate.topic == wildlife)
        self.assertEqual(wildlife_candidate.topic_bank_category, "Wildlife")
        self.assertEqual(wildlife_candidate.topic_bank_category_diversity_score, 0.0)
        self.assertIn("topic-bank category", "; ".join(wildlife_candidate.reasons))

    def test_config_reads_topic_sources_and_evergreen_pool(self) -> None:
        config = ContentSchedulerConfig.from_env({
            "AUTO_VIDEO_SCHEDULER_TOPIC_SOURCES": "ideas.json,topics.txt",
            "AUTO_VIDEO_SCHEDULER_EVERGREEN_TOPICS": "Aurora,Volcanoes",
            "AUTO_VIDEO_SCHEDULER_COVERAGE_PROVEN_TOPICS": "Bees,Volcanoes",
            "AUTO_VIDEO_SCHEDULER_FORBID_REPEATED_TOPICS": "true",
            "AUTO_VIDEO_SCHEDULER_TOPIC_BANK_CATEGORY_COOLDOWN_DAYS": "4",
            "AUTO_VIDEO_SCHEDULER_MAX_CANDIDATES": "12",
        })

        self.assertEqual(config.topic_sources, ("ideas.json", "topics.txt"))
        self.assertEqual(config.evergreen_topics, ("Aurora", "Volcanoes"))
        self.assertEqual(config.coverage_proven_topics, ("Bees", "Volcanoes"))
        self.assertTrue(config.forbid_repeated_topics)
        self.assertEqual(config.topic_bank_category_cooldown_days, 4)
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
