from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401

from autovideo.pipeline import (
    RenderedSceneRequest,
    RenderedVisualDecision,
    RenderedVisualEvidence,
    RenderedVisualQAGate,
    RenderedVisualQAConfig,
)


class RenderedVisualQATests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.frame = Path(self.directory.name) / "scene.jpg"
        self.frame.write_bytes(b"frame")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _request(self) -> RenderedSceneRequest:
        return RenderedSceneRequest(
            scene_index=1,
            expected_entity="penguin",
            visual_goal="show",
            media_mode="show",
            timestamp_sec=5.0,
            frame_path=self.frame,
            priority="critical",
        )

    def test_rejects_a_mismatched_final_frame(self) -> None:
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True),
            verifier=lambda request: RenderedVisualEvidence(
                match=False, confidence=0.95, matched_entity="horse"
            ),
        ).evaluate([self._request()])
        self.assertTrue(report.has_mismatch)
        self.assertEqual(RenderedVisualDecision.MISMATCH, report.scenes[0].decision)

    def test_keeps_vision_outage_as_an_auditable_soft_failure(self) -> None:
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True),
            verifier=lambda request: RenderedVisualEvidence(False, error="quota exceeded"),
        ).evaluate([self._request()])
        self.assertEqual(RenderedVisualDecision.UNAVAILABLE, report.scenes[0].decision)
        self.assertFalse(report.has_mismatch)

    def test_treats_low_confidence_positive_result_as_unavailable(self) -> None:
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True),
            verifier=lambda request: RenderedVisualEvidence(True, confidence=0.50),
        ).evaluate([self._request()])
        self.assertEqual(RenderedVisualDecision.UNAVAILABLE, report.scenes[0].decision)
        self.assertFalse(report.has_mismatch)

    def test_treats_low_confidence_negative_result_as_unavailable(self) -> None:
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True),
            verifier=lambda request: RenderedVisualEvidence(False, confidence=0.50),
        ).evaluate([self._request()])
        self.assertEqual(RenderedVisualDecision.UNAVAILABLE, report.scenes[0].decision)
        self.assertFalse(report.has_mismatch)

    def test_keeps_high_confidence_negative_result_as_mismatch(self) -> None:
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True),
            verifier=lambda request: RenderedVisualEvidence(False, confidence=0.95),
        ).evaluate([self._request()])
        self.assertEqual(RenderedVisualDecision.MISMATCH, report.scenes[0].decision)
        self.assertTrue(report.has_mismatch)

    def test_limits_scene_samples_to_configured_budget(self) -> None:
        requests = [self._request() for _ in range(3)]
        report = RenderedVisualQAGate(
            RenderedVisualQAConfig(enabled=True, max_scenes=2),
            verifier=lambda request: RenderedVisualEvidence(True, confidence=0.95),
        ).evaluate(requests)
        self.assertEqual(2, len(report.scenes))


if __name__ == "__main__":
    unittest.main()
