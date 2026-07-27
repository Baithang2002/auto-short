"""Tests for the bounded background topic qualification sweep."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import topic_qualify
from autovideo.intelligence import TopicBankStateStore


class TopicQualifyTests(unittest.TestCase):
    def test_select_candidates_uses_only_candidate_statuses(self) -> None:
        topics = (
            "How Bees Make Honey",
            "How Sand Dunes Move",
            "How the Northern Lights Form",
            "Why Volcanoes Erupt",
        )
        statuses = {
            "how bees make honey": "quarantined",
            "how sand dunes move": "candidate",
            "how the northern lights form": "qualified",
            "why volcanoes erupt": "candidate",
        }

        selected = topic_qualify.select_qualification_candidates(
            topics,
            statuses,
            limit=5,
        )

        self.assertEqual(set(selected), {"How Sand Dunes Move", "Why Volcanoes Erupt"})

    def test_classify_attempt_reads_approved_coverage_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "source_coverage_report.json"
            report.write_text(
                json.dumps({
                    "topic": "How Sand Dunes Move",
                    "decision": "APPROVED",
                    "coverage_ratio": 1.0,
                }),
                encoding="utf-8",
            )
            with patch.object(topic_qualify, "SOURCE_COVERAGE_REPORT", report), \
                 patch.object(
                     topic_qualify,
                     "EDITORIAL_IDENTITY_REPORT",
                     Path(directory) / "missing.json",
                 ):
                outcome = topic_qualify.classify_attempt("How Sand Dunes Move", 0)

        self.assertEqual(outcome, ("qualified", "source coverage approved", 1.0))

    def test_classify_attempt_treats_missing_quality_report_as_technical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                topic_qualify,
                "SOURCE_COVERAGE_REPORT",
                Path(directory) / "missing_coverage.json",
            ), patch.object(
                topic_qualify,
                "EDITORIAL_IDENTITY_REPORT",
                Path(directory) / "missing_editorial.json",
            ):
                outcome, reason, ratio = topic_qualify.classify_attempt("Topic", 1)

        self.assertEqual(outcome, "technical_failure")
        self.assertIn("without a quality decision", reason)
        self.assertIsNone(ratio)

    def test_run_preflight_stops_before_voice_and_upload_stages(self) -> None:
        with patch.object(topic_qualify, "_clear_attempt_state"), \
             patch.object(
                 topic_qualify.subprocess,
                 "run",
                 return_value=SimpleNamespace(returncode=0),
             ) as run:
            return_code, error = topic_qualify.run_preflight(
                "How Sand Dunes Move",
                timeout_sec=60,
            )

        command = run.call_args.args[0]
        self.assertEqual(return_code, 0)
        self.assertEqual(error, "")
        self.assertIn("--coverage-preflight-only", command)
        self.assertIn("--no-interactive", command)
        self.assertNotIn("pipeline.py", command)

    def test_persist_qualified_script_uses_topic_stable_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            source = output / "last_script.json"
            source.write_text('{"niche": "Sand dunes"}', encoding="utf-8")
            with patch.object(topic_qualify, "SCRIPT_DIR", root), \
                 patch.object(topic_qualify, "LAST_SCRIPT", source), \
                 patch.object(
                     topic_qualify,
                     "QUALIFIED_SCRIPT_DIR",
                     root / "state" / "qualified_scripts",
                ):
                relative = topic_qualify._persist_qualified_script("How Sand Dunes Move")

            persisted = root / relative
            persisted_text = persisted.read_text(encoding="utf-8")

        self.assertTrue(relative.startswith("state/qualified_scripts/"))
        self.assertEqual(persisted_text, '{"niche": "Sand dunes"}')

    def test_run_qualification_fills_buffer_and_quarantines_weak_topic(self) -> None:
        topics = ("Weak topic", "Strong topic", "Second strong topic")
        outcomes = {
            "Weak topic": ("deferred", "coverage too weak", 0.2),
            "Strong topic": ("qualified", "source coverage approved", 1.0),
            "Second strong topic": ("qualified", "source coverage approved", 0.8),
        }
        config = topic_qualify.TopicQualificationConfig(
            target_buffer=2,
            max_attempts=3,
            timeout_sec=60,
            quarantine_days=14,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "topic_bank_state.json"
            with patch.object(
                topic_qualify.ContentSchedulerConfig,
                "from_env",
                return_value=topic_qualify.ContentSchedulerConfig(
                    coverage_proven_topics=topics,
                    evergreen_topics=(),
                ),
            ), patch.object(
                topic_qualify.TopicQualificationConfig,
                "from_env",
                return_value=config,
            ), patch.object(
                topic_qualify,
                "load_candidates",
                return_value=topics,
            ), patch.object(
                topic_qualify,
                "_bootstrap_store",
            ), patch.object(
                topic_qualify,
                "TOPIC_BANK_STATE",
                state_path,
            ), patch.object(
                topic_qualify,
                "TOPIC_BANK_REPORT",
                root / "topic_bank_status_report.json",
            ), patch.object(
                topic_qualify,
                "QUALIFICATION_REPORT",
                root / "topic_qualification_report.json",
            ), patch.object(
                topic_qualify,
                "OUT_DIR",
                root,
            ), patch.object(
                topic_qualify,
                "run_preflight",
                return_value=(0, ""),
            ) as preflight, patch.object(
                topic_qualify,
                "classify_attempt",
                side_effect=lambda topic, _return_code: outcomes[topic],
            ), patch.object(
                topic_qualify,
                "_persist_qualified_script",
                side_effect=lambda topic: f"state/qualified_scripts/{topic}.json",
            ):
                result = topic_qualify.run_qualification()

            records = {
                record.topic: record
                for record in TopicBankStateStore(state_path).load()
            }
            report = json.loads(
                (root / "topic_qualification_report.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(preflight.call_count, 3)
        self.assertEqual(records["Weak topic"].status.value, "quarantined")
        self.assertEqual(records["Strong topic"].status.value, "qualified")
        self.assertEqual(records["Second strong topic"].status.value, "qualified")
        self.assertTrue(report["buffer_ready"])
        self.assertEqual(report["qualified_after"], 2)


if __name__ == "__main__":
    unittest.main()
