from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401

from autovideo.pipeline import (
    PublishQualityArtifacts,
    PublishQualityConfig,
    PublishQualityGate,
    PublishQualityVerdict,
    upload_allowed_from_report,
)


class PublishQualityGateTests(unittest.TestCase):
    def make_artifacts(self, root: Path, **overrides: object) -> PublishQualityArtifacts:
        video = root / "final.mp4"
        captions = root / "captions.ass"
        timeline = root / "timeline.json"
        manifest = root / "media_manifest.json"
        ffprobe = root / "ffprobe.json"
        fallback = root / "fallback_quality_report.json"
        audio = root / "audio_mix_report.json"
        evidence = root / "evidence_verification_report.json"
        contact = root / "contact_sheet.jpg"
        video.write_bytes(b"video")
        captions.write_text("[Script Info]", encoding="utf-8")
        timeline.write_text("{}", encoding="utf-8")
        self.write_json(manifest, {
            "assets": [
                {"metadata": {"selection": {
                    "provider": "pexels",
                    "provider_id": "one",
                    "confidence_level": "HIGH",
                }}},
                {"metadata": {"selection": {
                    "provider": "pixabay",
                    "provider_id": "two",
                    "confidence_level": "HIGH",
                }}},
            ],
        })
        self.write_json(ffprobe, {
            "format": {"duration": "54.0"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        })
        self.write_json(fallback, {"quality_gate_passed": True})
        self.write_json(audio, {"segments": []})
        self.write_json(evidence, {"1": {"vision_result": "match"}})
        contact.write_bytes(b"sheet")
        values: dict[str, object] = {
            "video_path": video,
            "captions_path": captions,
            "timeline_path": timeline,
            "media_manifest_path": manifest,
            "ffprobe_path": ffprobe,
            "fallback_quality_path": fallback,
            "audio_mix_path": audio,
            "evidence_verification_path": evidence,
            "contact_sheet_path": contact,
            "decode_verified": True,
        }
        values.update(overrides)
        return PublishQualityArtifacts(**values)  # type: ignore[arg-type]

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_approves_complete_quality_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = PublishQualityGate().evaluate(self.make_artifacts(Path(directory)))
        self.assertEqual(PublishQualityVerdict.APPROVED, report.verdict)
        self.assertTrue(report.upload_allowed)

    def test_defers_verified_entity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self.make_artifacts(root)
            self.write_json(artifacts.evidence_verification_path, {
                "3": {"vision_result": "no_match"},
            })
            report = PublishQualityGate().evaluate(artifacts)
        self.assertEqual(PublishQualityVerdict.DEFERRED, report.verdict)
        self.assertEqual("evidence_verification", next(
            check.name for check in report.checks if check.severity.value == "DEFER"
        ))

    def test_defers_source_clip_audio_under_narration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self.make_artifacts(root)
            self.write_json(artifacts.audio_mix_path, {
                "segments": [{"segment_index": 2, "clip_audio_used": True}],
            })
            report = PublishQualityGate().evaluate(artifacts)
        self.assertEqual(PublishQualityVerdict.DEFERRED, report.verdict)
        self.assertIn("audio_quality", [check.name for check in report.checks if check.severity.value == "DEFER"])

    def test_blocks_missing_caption_or_failed_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = self.make_artifacts(root, decode_verified=False)
            artifacts.captions_path.unlink()
            report = PublishQualityGate().evaluate(artifacts)
        self.assertEqual(PublishQualityVerdict.BLOCKED, report.verdict)
        self.assertEqual(2, sum(check.severity.value == "BLOCK" for check in report.checks))

    def test_disabled_gate_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = PublishQualityGate(PublishQualityConfig(enabled=False)).evaluate(
                self.make_artifacts(Path(directory))
            )
        self.assertEqual(PublishQualityVerdict.SKIPPED, report.verdict)

    def test_configuration_uses_explicit_environment_mapping(self) -> None:
        config = PublishQualityConfig.from_env({
            "AUTO_VIDEO_PUBLISH_QUALITY_GATE_ENABLED": "false",
            "AUTO_VIDEO_PUBLISH_QUALITY_MAX_HYBRID_RATIO": "0.2",
        })
        self.assertFalse(config.enabled)
        self.assertEqual(0.2, config.max_hybrid_composer_ratio)

    def test_upload_enforcement_uses_persisted_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publish_quality_report.json"
            self.write_json(path, {"verdict": "DEFERRED"})
            self.assertEqual((True, "publish quality enforcement disabled"), upload_allowed_from_report(path, enforce=False))
            self.assertEqual((False, "publish quality verdict DEFERRED"), upload_allowed_from_report(path, enforce=True))
            self.write_json(path, {"verdict": "APPROVED"})
            self.assertEqual((True, "publish quality verdict APPROVED"), upload_allowed_from_report(path, enforce=True))
