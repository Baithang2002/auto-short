from __future__ import annotations

import tempfile
import unittest
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

    def test_sampling_is_bounded_and_evenly_distributed(self) -> None:
        self.assertEqual((0,), sample_scene_indexes(12, 1))
        self.assertEqual((0, 2, 4, 7, 9, 11), sample_scene_indexes(12, 6))
        self.assertEqual(tuple(range(4)), sample_scene_indexes(4, 6))

    def test_report_round_trips_to_json(self) -> None:
        report = SourceCoverageEvaluator().evaluate("Rainforests", [_scene(0, covered=True)])
        with tempfile.TemporaryDirectory() as directory:
            path = report.write_json(Path(directory) / "source_coverage_report.json")
            self.assertIn('"decision": "APPROVED"', path.read_text(encoding="utf-8"))

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
