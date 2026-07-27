"""Tests for persistent topic-bank burn-in state."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.unit import _path  # noqa: F401

from autovideo.intelligence import TopicBankStateStore


class TopicBankStateStoreTests(unittest.TestCase):
    def test_unknown_topics_begin_as_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TopicBankStateStore(Path(directory) / "topic_bank_state.json")

            statuses = store.status_map(["How Bees Make Honey"])

        self.assertEqual(statuses["how bees make honey"], "candidate")

    def test_quality_failure_quarantines_topic_until_cooldown_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TopicBankStateStore(Path(directory) / "topic_bank_state.json")
            store.mark_failure(
                "How Bees Make Honey",
                reason="verified media gate rejected hook",
                quarantine_days=14,
                attempted_at="2026-07-18T00:00:00Z",
            )

            active = store.status_map(
                ["How Bees Make Honey"],
                now=datetime(2026, 7, 25, tzinfo=UTC),
            )
            expired = store.status_map(
                ["How Bees Make Honey"],
                now=datetime(2026, 8, 2, tzinfo=UTC),
            )

        self.assertEqual(active["how bees make honey"], "quarantined")
        self.assertEqual(expired["how bees make honey"], "candidate")

    def test_success_promotes_topic_and_clears_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topic_bank_state.json"
            store = TopicBankStateStore(path)
            store.mark_failure(
                "How Bees Make Honey",
                reason="coverage failed",
                quarantine_days=14,
                attempted_at="2026-07-18T00:00:00Z",
            )
            store.mark_success(
                "How Bees Make Honey",
                attempted_at="2026-07-19T00:00:00Z",
            )

            record = store.load()[0]

        self.assertEqual(record.status.value, "proven")
        self.assertEqual(record.success_count, 1)
        self.assertEqual(record.failure_count, 1)
        self.assertEqual(record.quarantine_until, "")
        self.assertEqual(record.last_failure_reason, "")

    def test_qualification_creates_ready_topic_then_success_marks_proven(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TopicBankStateStore(Path(directory) / "topic_bank_state.json")
            store.mark_qualified(
                "How Sand Dunes Move",
                coverage_ratio=1.0,
                script_path="state/qualified_scripts/sand.json",
                attempted_at="2026-07-18T00:00:00Z",
            )
            qualified = store.load()[0]
            store.mark_success(
                "How Sand Dunes Move",
                attempted_at="2026-07-19T00:00:00Z",
            )
            proven = store.load()[0]

        self.assertEqual(qualified.status.value, "qualified")
        self.assertEqual(qualified.qualification_count, 1)
        self.assertEqual(qualified.last_coverage_ratio, 1.0)
        self.assertEqual(
            qualified.qualified_script_path,
            "state/qualified_scripts/sand.json",
        )
        self.assertEqual(proven.status.value, "proven")
        self.assertEqual(proven.qualification_count, 1)
        self.assertEqual(proven.qualified_script_path, "")

    def test_bootstrap_migrates_existing_history_without_overwriting_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TopicBankStateStore(Path(directory) / "topic_bank_state.json")
            store.bootstrap(
                generated=(("Why Volcanoes Create New Land", "2026-07-17T00:00:00Z"),),
                deferred=(("How Bees Make Honey", "coverage too weak", "2026-07-18T00:00:00Z"),),
                quarantine_days=14,
            )
            store.bootstrap(
                generated=(),
                deferred=(("How Bees Make Honey", "second reason", "2026-07-19T00:00:00Z"),),
                quarantine_days=14,
            )

            records = {record.topic: record for record in store.load()}

        self.assertEqual(records["Why Volcanoes Create New Land"].status.value, "proven")
        self.assertEqual(records["How Bees Make Honey"].status.value, "quarantined")
        self.assertEqual(records["How Bees Make Honey"].failure_count, 1)
        self.assertEqual(records["How Bees Make Honey"].last_failure_reason, "coverage too weak")

    def test_report_includes_unattempted_and_recorded_topics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TopicBankStateStore(root / "topic_bank_state.json")
            store.mark_success("How Bees Make Honey", attempted_at="2026-07-18T00:00:00Z")
            report_path = store.write_report(
                root / "topic_bank_status_report.json",
                ["How Bees Make Honey", "Why Volcanoes Create New Land"],
                now=datetime(2026, 7, 18, tzinfo=UTC),
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["summary"]["proven"], 1)
        self.assertEqual(report["summary"]["candidate"], 1)
        self.assertEqual(report["summary"]["quarantined"], 0)


if __name__ == "__main__":
    unittest.main()
