"""Focused tests for daily topic recovery behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

import pipeline_daily


class PipelineDailyTests(unittest.TestCase):
    def test_prepare_qualified_script_seeds_exact_approved_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            script_dir = state_dir / "qualified_scripts"
            output_dir = root / "output"
            script_dir.mkdir(parents=True)
            approved = script_dir / "approved.json"
            approved.write_text('{"niche": "Sand dunes"}', encoding="utf-8")
            state_path = state_dir / "topic_bank_state.json"
            pipeline_daily.TopicBankStateStore(state_path).mark_qualified(
                "How Sand Dunes Move",
                coverage_ratio=1.0,
                script_path="state/qualified_scripts/approved.json",
            )
            scheduler_result = SimpleNamespace(
                selected=SimpleNamespace(topic_bank_status="qualified"),
            )

            with patch.object(pipeline_daily, "SCRIPT_DIR", root), \
                 patch.object(pipeline_daily, "STATE_DIR", state_dir), \
                 patch.object(pipeline_daily, "OUT_DIR", output_dir), \
                 patch.object(pipeline_daily, "TOPIC_BANK_STATE", state_path), \
                 patch.object(pipeline_daily, "QUALIFIED_SCRIPT_DIR", script_dir), \
                 patch.object(pipeline_daily, "LAST_SCRIPT", output_dir / "last_script.json"):
                source = pipeline_daily.prepare_qualified_script(
                    "How Sand Dunes Move",
                    scheduler_result,
                )
                cached = (output_dir / "last_script.json").read_text(encoding="utf-8")

        self.assertEqual(source, approved)
        self.assertEqual(cached, '{"niche": "Sand dunes"}')

    def test_candidate_quality_deferred_detects_fallback_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "fallback_quality_report.json"
            report_path.write_text(json.dumps({"quality_gate_passed": False}), encoding="utf-8")

            with patch.object(pipeline_daily, "FALLBACK_QUALITY_REPORT", report_path):
                deferred, reason = pipeline_daily.candidate_quality_deferred("Weak topic")

        self.assertTrue(deferred)
        self.assertIn("fallback quality", reason)

    def test_candidate_quality_deferred_detects_exact_subject_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "exact_subject_gate_report.json"
            report_path.write_text(json.dumps({
                "topic": "Greenland Shark",
                "decision": "DEFERRED",
                "failure_reason": "only generic substitutes were selected",
            }), encoding="utf-8")

            with patch.object(pipeline_daily, "EXACT_SUBJECT_GATE_REPORT", report_path), \
                 patch.object(pipeline_daily, "FALLBACK_QUALITY_REPORT", Path(directory) / "missing_fallback.json"):
                deferred, reason = pipeline_daily.candidate_quality_deferred("Greenland Shark")

        self.assertTrue(deferred)
        self.assertIn("generic substitutes", reason)

    def test_run_daily_retries_quality_failure_then_publishes(self) -> None:
        scheduled = iter((
            ("Weak topic", "run-1", None),
            ("Strong topic", "run-2", None),
        ))
        process_results = iter((SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)))

        with patch.object(pipeline_daily, "already_posted_today", return_value=False), \
             patch.object(pipeline_daily, "max_topic_attempts", return_value=3), \
             patch.object(pipeline_daily, "schedule_topic", side_effect=lambda _excluded: next(scheduled)), \
             patch.object(pipeline_daily, "clear_attempt_reports"), \
             patch.object(pipeline_daily.subprocess, "run", side_effect=lambda *args, **kwargs: next(process_results)) as run, \
             patch.object(
                 pipeline_daily,
                 "candidate_quality_deferred",
                 side_effect=((True, "fallback quality gate deferred topic"), (False, "")),
             ), \
             patch.object(pipeline_daily, "append_log"), \
             patch.object(pipeline_daily.ContentHistoryStore, "mark_deferred", return_value=True) as deferred, \
             patch.object(pipeline_daily.ContentHistoryStore, "mark_generated", return_value=True) as generated:
            result = pipeline_daily.run_daily()

        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        deferred.assert_called_once_with(
            run_id="run-1",
            reason="fallback quality gate deferred topic",
            status="quality_deferred",
        )
        generated.assert_called_once_with(run_id="run-2")

    def test_run_daily_stops_immediately_for_critical_failure(self) -> None:
        scheduler_result = SimpleNamespace(
            selected=SimpleNamespace(viability_score=0.9, ranking_score=0.8),
            config=pipeline_daily.ContentSchedulerConfig(
                coverage_proven_topics=("Topic",),
                evergreen_topics=(),
            ),
        )
        with patch.object(pipeline_daily, "already_posted_today", return_value=False), \
             patch.object(pipeline_daily, "max_topic_attempts", return_value=3), \
             patch.object(
                 pipeline_daily,
                 "schedule_topic",
                 return_value=("Topic", "run-1", scheduler_result),
             ), \
             patch.object(pipeline_daily, "clear_attempt_reports"), \
             patch.object(pipeline_daily.subprocess, "run", return_value=SimpleNamespace(returncode=2)) as run, \
             patch.object(pipeline_daily, "candidate_quality_deferred", return_value=(False, "")), \
             patch.object(pipeline_daily, "append_log"), \
             patch.object(
                 pipeline_daily.ContentHistoryStore,
                 "mark_deferred",
                 return_value=True,
             ) as failed:
            result = pipeline_daily.run_daily()

        self.assertEqual(result, 2)
        self.assertEqual(run.call_count, 1)
        failed.assert_called_once_with(
            run_id="run-1",
            reason="critical technical failure exit=2",
            status="technical_failed",
        )

    def test_max_topic_attempts_preserves_legacy_recovery_setting(self) -> None:
        with patch.dict(
            pipeline_daily.os.environ,
            {"AUTO_VIDEO_SOURCE_COVERAGE_MAX_RECOVERIES": "4"},
            clear=True,
        ):
            self.assertEqual(pipeline_daily.max_topic_attempts(), 5)

        with patch.dict(
            pipeline_daily.os.environ,
            {"AUTO_VIDEO_DAILY_MAX_TOPIC_ATTEMPTS": "6"},
            clear=True,
        ):
            self.assertEqual(pipeline_daily.max_topic_attempts(), 6)

        with patch.dict(
            pipeline_daily.os.environ,
            {"AUTO_VIDEO_SOURCE_COVERAGE_MAX_RECOVERIES": "invalid"},
            clear=True,
        ):
            self.assertEqual(pipeline_daily.max_topic_attempts(), 3)

    def test_schedule_topic_excludes_attempted_proven_and_evergreen_topics(self) -> None:
        captured = {}

        class FakeScheduler:
            def __init__(self, config):
                captured["config"] = config

            def schedule(self, _candidates, _history, _bank_statuses):
                return SimpleNamespace(
                    selected=SimpleNamespace(topic="Fresh proven"),
                    write_json=lambda _path: None,
                )

        class FakeStore:
            def __init__(self, _path):
                pass

            def load(self):
                return []

            def record_decisions(self, _result, *, run_id):
                captured["run_id"] = run_id

        class FakeTopicBankStore:
            def __init__(self, _path):
                pass

            def bootstrap(self, **_kwargs):
                return None

            def status_map(self, _topics):
                return {}

            def write_report(self, _path, _topics):
                return None

        config = pipeline_daily.ContentSchedulerConfig(
            topic_sources=("topics.txt",),
            coverage_proven_topics=("Used topic", "Fresh proven"),
            evergreen_topics=("Used topic", "Fresh evergreen"),
        )

        with patch.object(pipeline_daily.ContentSchedulerConfig, "from_env", return_value=config), \
             patch.object(pipeline_daily, "topic_source_for_path", return_value=SimpleNamespace()), \
             patch.object(pipeline_daily, "load_topic_sources", return_value=[]), \
             patch.object(pipeline_daily, "AutonomousContentScheduler", FakeScheduler), \
             patch.object(pipeline_daily, "ContentHistoryStore", FakeStore), \
             patch.object(pipeline_daily, "TopicBankStateStore", FakeTopicBankStore):
            topic, _run_id, _result = pipeline_daily.schedule_topic({"Used topic"})

        self.assertEqual(topic, "Fresh proven")
        self.assertEqual(captured["config"].coverage_proven_topics, ("Fresh proven",))
        self.assertEqual(captured["config"].evergreen_topics, ("Fresh evergreen",))

    def test_successful_daily_run_promotes_topic_to_proven(self) -> None:
        config = pipeline_daily.ContentSchedulerConfig(
            coverage_proven_topics=("Fresh topic",),
            evergreen_topics=(),
        )
        scheduler_result = SimpleNamespace(
            selected=SimpleNamespace(viability_score=0.9, ranking_score=0.8),
            config=config,
        )

        with patch.object(pipeline_daily, "already_posted_today", return_value=False), \
             patch.object(pipeline_daily, "max_topic_attempts", return_value=1), \
             patch.object(
                 pipeline_daily,
                 "schedule_topic",
                 return_value=("Fresh topic", "run-1", scheduler_result),
             ), \
             patch.object(pipeline_daily, "clear_attempt_reports"), \
             patch.object(
                 pipeline_daily.subprocess,
                 "run",
                 return_value=SimpleNamespace(returncode=0),
             ), \
             patch.object(pipeline_daily, "append_log"), \
             patch.object(
                 pipeline_daily.ContentHistoryStore,
                 "mark_generated",
                 return_value=True,
             ), \
             patch.object(
                 pipeline_daily.TopicBankStateStore,
                 "mark_success",
             ) as mark_success, \
             patch.object(pipeline_daily, "update_topic_bank_report"):
            result = pipeline_daily.run_daily()

        self.assertEqual(result, 0)
        mark_success.assert_called_once_with("Fresh topic")

    def test_quality_deferral_quarantines_topic(self) -> None:
        config = pipeline_daily.ContentSchedulerConfig(
            topic_bank_quarantine_days=21,
            coverage_proven_topics=("Weak topic",),
            evergreen_topics=(),
        )
        scheduler_result = SimpleNamespace(
            selected=SimpleNamespace(viability_score=0.9, ranking_score=0.8),
            config=config,
        )

        with patch.object(pipeline_daily, "already_posted_today", return_value=False), \
             patch.object(pipeline_daily, "max_topic_attempts", return_value=1), \
             patch.object(
                 pipeline_daily,
                 "schedule_topic",
                 return_value=("Weak topic", "run-1", scheduler_result),
             ), \
             patch.object(pipeline_daily, "clear_attempt_reports"), \
             patch.object(
                 pipeline_daily.subprocess,
                 "run",
                 return_value=SimpleNamespace(returncode=1),
             ), \
             patch.object(
                 pipeline_daily,
                 "candidate_quality_deferred",
                 return_value=(True, "coverage too weak"),
             ), \
             patch.object(pipeline_daily, "append_log"), \
             patch.object(
                 pipeline_daily.ContentHistoryStore,
                 "mark_deferred",
                 return_value=True,
             ), \
             patch.object(
                 pipeline_daily.TopicBankStateStore,
                 "mark_failure",
             ) as mark_failure, \
             patch.object(pipeline_daily, "update_topic_bank_report"):
            result = pipeline_daily.run_daily()

        self.assertEqual(result, 1)
        mark_failure.assert_called_once_with(
            "Weak topic",
            reason="coverage too weak",
            quarantine_days=21,
        )


if __name__ == "__main__":
    unittest.main()
