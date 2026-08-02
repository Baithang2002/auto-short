from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.unit import _path  # noqa: F401

from autovideo.intelligence import (
    SceneCoverage,
    SourceCoverageConfig,
    SourceCoverageDecision,
    SourceCoverageEvaluator,
    sample_scene_indexes,
)
from autovideo.intelligence.source_coverage import ProviderProbeOutcome, ProviderProbeStatus
import pipeline_daily


def _scene(index: int, *, covered: bool, importance: str = "SUPPORTING") -> SceneCoverage:
    return SceneCoverage(
        scene_index=index,
        canonical_entity="rainforest",
        documentary_role="overview",
        scene_importance=importance,
        query="rainforest canopy aerial",
        providers_attempted=("pexels", "pixabay"),
        candidates_found=4 if covered else 0,
        accepted_candidates=1 if covered else 0,
        best_score=12.0 if covered else None,
        covered=covered,
    )


class SourceCoverageTests(unittest.TestCase):
    def test_approves_sufficient_coverage(self) -> None:
        report = SourceCoverageEvaluator().evaluate("Rainforests", [
            _scene(0, covered=True, importance="HOOK"),
            _scene(1, covered=True),
            _scene(2, covered=True),
            _scene(3, covered=False),
        ])
        self.assertEqual(SourceCoverageDecision.APPROVED, report.decision)
        self.assertEqual(0.75, report.coverage_ratio)

    def test_defers_when_a_critical_scene_has_no_coverage(self) -> None:
        report = SourceCoverageEvaluator().evaluate("Rainforests", [
            _scene(0, covered=False, importance="HOOK"),
            _scene(1, covered=True),
            _scene(2, covered=True),
        ])
        self.assertEqual(SourceCoverageDecision.DEFERRED, report.decision)
        self.assertIn(0, report.to_dict()["critical_uncovered_scenes"])

    def test_defers_when_coverage_ratio_is_weak(self) -> None:
        report = SourceCoverageEvaluator().evaluate("Rainforests", [
            _scene(0, covered=True, importance="HOOK"),
            _scene(1, covered=False),
            _scene(2, covered=False),
        ])
        self.assertEqual(SourceCoverageDecision.DEFERRED, report.decision)

    def test_disabled_policy_is_skipped(self) -> None:
        report = SourceCoverageEvaluator(SourceCoverageConfig(enabled=False)).evaluate(
            "Rainforests",
            [_scene(0, covered=False)],
        )
        self.assertEqual(SourceCoverageDecision.SKIPPED, report.decision)

    def test_supporting_score_ratio_is_configurable_and_bounded(self) -> None:
        config = SourceCoverageConfig.from_env({
            "AUTO_VIDEO_SOURCE_COVERAGE_SUPPORTING_SCORE_RATIO": "1.5",
        })
        self.assertEqual(1.0, config.supporting_scene_score_ratio)

    def test_default_probe_budget_checks_three_providers_per_scene(self) -> None:
        config = SourceCoverageConfig.from_env({})

        self.assertEqual(3, config.max_providers_per_scene)

    def test_sampling_is_bounded_and_evenly_distributed(self) -> None:
        self.assertEqual((0,), sample_scene_indexes(12, 1))
        self.assertEqual((0, 2, 4, 7, 9, 11), sample_scene_indexes(12, 6))
        self.assertEqual(tuple(range(4)), sample_scene_indexes(4, 6))

    def test_report_round_trips_to_json(self) -> None:
        report = SourceCoverageEvaluator().evaluate("Rainforests", [_scene(0, covered=True)])
        with tempfile.TemporaryDirectory() as directory:
            path = report.write_json(Path(directory) / "source_coverage_report.json")
            self.assertIn('"decision": "APPROVED"', path.read_text(encoding="utf-8"))

    def test_all_provider_probe_failures_are_classified_as_technical(self) -> None:
        scene = _scene(0, covered=False, importance="HOOK")
        scene = replace(
            scene,
            provider_outcomes=(
                ProviderProbeOutcome("pexels", ProviderProbeStatus.RATE_LIMITED),
                ProviderProbeOutcome("wikimedia", ProviderProbeStatus.TIMEOUT),
            ),
        )

        report = SourceCoverageEvaluator().evaluate("Rainforests", [scene])

        self.assertEqual("TECHNICAL_PROVIDER_FAILURE", report.failure_classification)
        self.assertEqual(
            {"RATE_LIMITED": 1, "TIMEOUT": 1},
            report.to_dict()["provider_probe_summary"],
        )

    def test_healthy_no_results_remains_a_content_coverage_gap(self) -> None:
        scene = _scene(0, covered=False, importance="HOOK")
        scene = replace(
            scene,
            provider_outcomes=(
                ProviderProbeOutcome("wikimedia", ProviderProbeStatus.NO_RESULTS),
            ),
        )

        report = SourceCoverageEvaluator().evaluate("Rainforests", [scene])

        self.assertEqual("CONTENT_COVERAGE_GAP", report.failure_classification)

    def test_mixed_technical_and_no_results_can_rotate_to_another_topic(self) -> None:
        scene = _scene(0, covered=False, importance="HOOK")
        scene = replace(
            scene,
            provider_outcomes=(
                ProviderProbeOutcome("coverr", ProviderProbeStatus.PROVIDER_ERROR),
                ProviderProbeOutcome("pixabay", ProviderProbeStatus.NO_RESULTS),
            ),
        )

        report = SourceCoverageEvaluator().evaluate("Rainforests", [scene])

        self.assertEqual("CONTENT_COVERAGE_GAP", report.failure_classification)

    def test_successful_probe_with_rejected_candidates_remains_content_gap(self) -> None:
        scene = replace(
            _scene(0, covered=False, importance="HOOK"),
            provider_outcomes=(
                ProviderProbeOutcome("pexels", ProviderProbeStatus.SUCCESS, candidates_found=10),
                ProviderProbeOutcome("coverr", ProviderProbeStatus.PROVIDER_ERROR),
            ),
        )

        report = SourceCoverageEvaluator().evaluate("Rainforests", [scene])

        self.assertEqual("CONTENT_COVERAGE_GAP", report.failure_classification)

    def test_zero_minimum_ratio_allows_noncritical_uncovered_scene(self) -> None:
        config = SourceCoverageConfig(minimum_scene_coverage_ratio=0.0)

        report = SourceCoverageEvaluator(config).evaluate(
            "Rainforests",
            [_scene(0, covered=False, importance="SUPPORTING")],
        )

        self.assertEqual(SourceCoverageDecision.APPROVED, report.decision)

    def test_daily_recovery_recognizes_matching_deferred_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "source_coverage_report.json"
            report_path.write_text(
                '{"topic": "Rainforests", "decision": "DEFERRED", "reasons": ["weak coverage"]}',
                encoding="utf-8",
            )
            with patch.object(pipeline_daily, "SOURCE_COVERAGE_REPORT", report_path):
                self.assertEqual((True, "weak coverage"), pipeline_daily.source_coverage_deferred("Rainforests"))
                self.assertEqual((False, ""), pipeline_daily.source_coverage_deferred("Volcanoes"))
