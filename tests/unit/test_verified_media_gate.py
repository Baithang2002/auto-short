from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autovideo.media import (
    DownloadedMediaEvidence,
    VerificationDecision,
    VerificationPriority,
    VerificationRequest,
    VerifiedMediaGate,
    VerifiedMediaGateConfig,
    VerifiedMediaReport,
)


class VerifiedMediaGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.asset = Path(self.temp_dir.name) / "asset.mp4"
        self.asset.write_bytes(b"media")
        self.config = VerifiedMediaGateConfig(
            enabled=True,
            critical_confidence_threshold=0.85,
            critical_action_confidence_threshold=0.75,
            max_replacement_attempts=2,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _request(self, *, priority=VerificationPriority.CRITICAL, action="waggle dance"):
        return VerificationRequest(
            scene_index=2,
            media_path=self.asset,
            expected_entity="honeybee",
            expected_action=action,
            visual_goal="prove",
            priority=priority,
        )

    def test_accepts_exact_entity_and_required_action(self) -> None:
        gate = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                entity_match=True,
                entity_confidence=0.92,
                action_match=True,
                action_confidence=0.84,
                verified_entity="honeybee",
                verified_action="waggle dance",
                sampled_frames=("one.jpg", "two.jpg", "three.jpg"),
            ),
        )

        result = gate.evaluate(self._request())

        self.assertEqual(VerificationDecision.VERIFIED, result.decision)
        self.assertFalse(result.should_abort)

    def test_rejects_entity_without_required_action(self) -> None:
        gate = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                entity_match=True,
                entity_confidence=0.98,
                action_match=False,
                action_confidence=0.05,
                verified_entity="honeybee",
                reasoning="frames show bees on flowers, not a dance",
            ),
        )

        result = gate.evaluate(self._request())

        self.assertEqual(VerificationDecision.REJECTED, result.decision)
        self.assertTrue(result.should_abort)
        self.assertIn("action evidence", result.reason)

    def test_rejects_unrelated_book_cover(self) -> None:
        gate = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                entity_match=False,
                entity_confidence=0.0,
                action_match=False,
                verified_entity="book cover",
            ),
        )

        result = gate.evaluate(self._request(action=""))

        self.assertEqual(VerificationDecision.REJECTED, result.decision)
        self.assertIn("entity evidence", result.reason)

    def test_lower_priority_scene_can_be_marked_unverified_after_retries(self) -> None:
        gate = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                entity_match=False,
                entity_confidence=0.0,
            ),
        )

        result = gate.evaluate(
            self._request(priority=VerificationPriority.LOW), replacement_attempt=2
        )

        self.assertEqual(VerificationDecision.UNVERIFIED, result.decision)
        self.assertFalse(result.should_abort)

    def test_disabled_gate_preserves_compatibility_without_calling_verifier(self) -> None:
        calls = []
        gate = VerifiedMediaGate(
            VerifiedMediaGateConfig(enabled=False),
            verifier=lambda request, sample_count: calls.append(request),
        )

        result = gate.evaluate(self._request())

        self.assertEqual(VerificationDecision.UNVERIFIED, result.decision)
        self.assertEqual([], calls)

    def test_transient_vision_quota_failure_preserves_critical_asset_as_unverified(self) -> None:
        gate = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                entity_match=False,
                error="429 RESOURCE_EXHAUSTED: quota exceeded",
            ),
        )

        result = gate.evaluate(self._request())

        self.assertEqual(VerificationDecision.UNVERIFIED, result.decision)
        self.assertFalse(result.should_abort)
        self.assertIn("verification unavailable", result.reason)

    def test_report_records_replacement_attempts(self) -> None:
        rejected = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(False),
        ).evaluate(self._request())
        replacement_asset = Path(self.temp_dir.name) / "replacement.mp4"
        replacement_asset.write_bytes(b"replacement")
        accepted_request = VerificationRequest(
            scene_index=2,
            media_path=replacement_asset,
            expected_entity="honeybee",
            expected_action="waggle dance",
            visual_goal="prove",
            priority=VerificationPriority.CRITICAL,
        )
        accepted = VerifiedMediaGate(
            self.config,
            verifier=lambda request, sample_count: DownloadedMediaEvidence(
                True, entity_confidence=0.95, action_match=True, action_confidence=0.9
            ),
        ).evaluate(accepted_request, replacement_attempt=1)

        report = VerifiedMediaReport((accepted,), (rejected, accepted)).to_dict()

        self.assertEqual(1, report["summary"]["verified_count"])
        self.assertEqual(1, len(report["scenes"][0]["replacements"]))

    def test_environment_configuration_is_bounded(self) -> None:
        config = VerifiedMediaGateConfig.from_env({
            "AUTO_VIDEO_VERIFIED_MEDIA_GATE_ENABLED": "1",
            "AUTO_VIDEO_VERIFIED_MEDIA_CRITICAL_CONFIDENCE": "1.4",
            "AUTO_VIDEO_VERIFIED_MEDIA_FRAME_SAMPLES": "0",
            "AUTO_VIDEO_VERIFIED_MEDIA_MAX_REPLACEMENTS": "3",
        })

        self.assertTrue(config.enabled)
        self.assertEqual(1.0, config.critical_confidence_threshold)
        self.assertEqual(1, config.frame_sample_count)
        self.assertEqual(3, config.max_replacement_attempts)


if __name__ == "__main__":
    unittest.main()
