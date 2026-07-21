"""Unit tests for strict exact-subject availability policy."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.unit import _path  # noqa: F401

from autovideo.intelligence import (
    ExactSubjectAvailabilityGate,
    ExactSubjectGateConfig,
    ExactSubjectGateDecision,
    SubjectDefinition,
    subject_definition_from_pipeline,
)


def _asset(
    *,
    entity: str,
    fidelity: str,
    provider: str = "pexels",
    provider_id: str = "asset-1",
) -> dict[str, object]:
    return {
        "local_path": "output/broll.mp4",
        "source": provider,
        "metadata": {
            "selection": {
                "provider": provider,
                "provider_id": provider_id,
                "selected_entity": entity,
                "entity_fidelity": fidelity,
                "evidence_verification": {
                    "requested_entity": "Greenland shark",
                    "selected_entity": entity,
                    "entity_fidelity": fidelity,
                    "metadata_confidence": 1.0 if fidelity == "exact_entity" else 0.25,
                },
            },
        },
    }


class ExactSubjectAvailabilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.subject = SubjectDefinition(
            canonical_entity="Greenland shark",
            aliases=("sleeper shark",),
            scientific_names=("Somniosus microcephalus",),
            identity_defining=True,
            reason="specific species",
        )

    def test_exact_metadata_match_passes_identity_specific_documentary(self) -> None:
        report = ExactSubjectAvailabilityGate().evaluate(
            topic="The Shark That Lives Four Centuries",
            subject=self.subject,
            media_assets=[_asset(entity="Greenland shark", fidelity="exact_entity")],
        )

        self.assertEqual(ExactSubjectGateDecision.PASSED, report.decision)
        self.assertEqual("metadata_exact", report.verified_matches[0].proof)
        self.assertEqual("continue", report.scheduler_action)

    def test_approved_scientific_name_passes_when_enabled(self) -> None:
        report = ExactSubjectAvailabilityGate().evaluate(
            topic="The Shark That Lives Four Centuries",
            subject=self.subject,
            media_assets=[_asset(entity="Somniosus microcephalus", fidelity="exact_alias")],
        )

        self.assertEqual(ExactSubjectGateDecision.PASSED, report.decision)
        self.assertEqual("metadata_alias", report.verified_matches[0].proof)

    def test_scientific_name_is_not_accepted_when_disabled(self) -> None:
        report = ExactSubjectAvailabilityGate(
            ExactSubjectGateConfig(allow_scientific_names=False)
        ).evaluate(
            topic="The Shark That Lives Four Centuries",
            subject=self.subject,
            media_assets=[_asset(entity="Somniosus microcephalus", fidelity="exact_alias")],
        )

        self.assertEqual(ExactSubjectGateDecision.DEFERRED, report.decision)
        self.assertEqual("defer_topic_and_select_recovery", report.scheduler_action)

    def test_generic_shark_is_recorded_and_cannot_prove_greenland_shark(self) -> None:
        report = ExactSubjectAvailabilityGate().evaluate(
            topic="The Shark That Lives Four Centuries",
            subject=self.subject,
            media_assets=[_asset(entity="shark", fidelity="generic_category")],
        )

        self.assertEqual(ExactSubjectGateDecision.DEFERRED, report.decision)
        self.assertEqual(0, len(report.verified_matches))
        self.assertEqual(1, len(report.rejected_generic_matches))
        self.assertIn("generic", report.failure_reason)

    def test_generic_documentary_category_is_skipped(self) -> None:
        report = ExactSubjectAvailabilityGate().evaluate(
            topic="Why Volcanoes Create New Land",
            subject=SubjectDefinition(
                canonical_entity="volcano",
                identity_defining=False,
                reason="generic documentary category",
            ),
            media_assets=[_asset(entity="volcano", fidelity="exact_entity")],
        )

        self.assertEqual(ExactSubjectGateDecision.SKIPPED, report.decision)
        self.assertTrue(report.passed)

    def test_frame_verified_asset_can_satisfy_the_gate(self) -> None:
        report = ExactSubjectAvailabilityGate().evaluate(
            topic="The Shark That Lives Four Centuries",
            subject=self.subject,
            media_assets=[_asset(entity="", fidelity="unknown")],
            verified_media_report={
                "scenes": [{
                    "scene_index": 0,
                    "decision": "verified",
                    "verified_entity": "Greenland shark",
                }],
            },
        )

        self.assertEqual(ExactSubjectGateDecision.PASSED, report.decision)
        self.assertEqual("frame_verified", report.verified_matches[0].proof)

    def test_environment_configuration_is_bounded(self) -> None:
        config = ExactSubjectGateConfig.from_env({
            "AUTO_VIDEO_EXACT_SUBJECT_GATE_ENABLED": "false",
            "AUTO_VIDEO_EXACT_SUBJECT_GATE_MIN_VERIFIED_MATCHES": "0",
            "AUTO_VIDEO_EXACT_SUBJECT_GATE_ALLOW_ALIASES": "0",
        })

        self.assertFalse(config.enabled)
        self.assertEqual(1, config.minimum_verified_exact_matches)
        self.assertFalse(config.allow_aliases)

    def test_subject_definition_uses_existing_scene_entity_aliases(self) -> None:
        scene_entity = SimpleNamespace(
            canonical_entity="Greenland shark",
            entity_type="species",
            aliases=("sleeper shark", "Somniosus microcephalus"),
        )
        shot_plan = SimpleNamespace(
            primary_subject="Greenland shark",
            intents=(SimpleNamespace(scene_entity=scene_entity),),
        )
        editorial = SimpleNamespace(primary_subject="Greenland shark")

        definition = subject_definition_from_pipeline(
            editorial_canon=editorial,
            canonical_report=None,
            shot_plan=shot_plan,
        )

        self.assertTrue(definition.identity_defining)
        self.assertEqual(("sleeper shark",), definition.aliases)
        self.assertEqual(("Somniosus microcephalus",), definition.scientific_names)


if __name__ == "__main__":
    unittest.main()
