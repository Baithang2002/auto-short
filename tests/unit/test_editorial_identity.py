"""Regression tests for topic-safe editorial identity planning."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import pipeline_daily

from autovideo.media import (
    EditorialCanonBuilder,
    EditorialIdentityDecision,
    EditorialIdentityGate,
    KnowledgePackStore,
    VisualDirector,
)


SOLAR_TOPIC = "How Solar Panels Turn Sunlight into Electricity"
SOLAR_SEGMENTS = [
    {
        "narration": "This simple dance turns a bright sunny day into useful power.",
        "broll": "solar panels on rooftop",
    },
    {
        "narration": "Electrons move through silicon layers and create direct current.",
        "broll": "silicon solar cell close up",
    },
]


class EditorialIdentityTests(unittest.TestCase):
    """Protect generic process topics from unrelated knowledge-pack takeover."""

    def setUp(self) -> None:
        self.domains = KnowledgePackStore().load()

    def test_generic_script_term_cannot_assign_bee_domain(self) -> None:
        canon, _lock, _roles, domain_report = EditorialCanonBuilder().build(
            topic=SOLAR_TOPIC,
            segments=SOLAR_SEGMENTS,
            knowledge_domains=self.domains,
        )

        self.assertEqual(canon.primary_subject.lower(), "solar panels")
        self.assertEqual(canon.diagnostics["matched_domain"], "")
        self.assertEqual(domain_report["selected_domain"], "generic")

    def test_identity_gate_approves_topic_anchored_generic_plan(self) -> None:
        canon, _lock, _roles, _domain_report = EditorialCanonBuilder().build(
            topic=SOLAR_TOPIC,
            segments=SOLAR_SEGMENTS,
            knowledge_domains=self.domains,
        )
        plan = VisualDirector().plan(
            topic=SOLAR_TOPIC,
            segments=SOLAR_SEGMENTS,
            editorial_canon=canon,
        )

        report = EditorialIdentityGate().evaluate(
            topic=SOLAR_TOPIC,
            editorial_canon=canon,
            shot_plan=plan,
        )

        self.assertEqual(report.decision, EditorialIdentityDecision.APPROVED)
        self.assertIn("solar", report.topic_terms)
        self.assertIn("panel", report.subject_terms)

    def test_identity_gate_rejects_honeybee_plan_for_solar_topic(self) -> None:
        bee_canon, _lock, _roles, _domain_report = EditorialCanonBuilder().build(
            topic="How Bees Communicate Through Dancing",
            segments=[{"narration": "Bees share food locations through a waggle dance.", "broll": "bee hive"}],
            knowledge_domains=self.domains,
        )
        bee_plan = VisualDirector().plan(
            topic=SOLAR_TOPIC,
            segments=SOLAR_SEGMENTS,
            editorial_canon=bee_canon,
        )

        report = EditorialIdentityGate().evaluate(
            topic=SOLAR_TOPIC,
            editorial_canon=bee_canon,
            shot_plan=bee_plan,
        )

        self.assertEqual(report.decision, EditorialIdentityDecision.REJECTED)
        self.assertTrue(any("Canon title" in reason for reason in report.reasons))

    def test_daily_scheduler_recovers_from_identity_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "editorial_identity_report.json"
            report_path.write_text(
                '{"topic":"Solar Panels","decision":"REJECTED","reasons":["subject mismatch"]}',
                encoding="utf-8",
            )
            with patch.object(pipeline_daily, "EDITORIAL_IDENTITY_REPORT", report_path):
                self.assertEqual(
                    (True, "subject mismatch"),
                    pipeline_daily.editorial_identity_deferred("solar panels"),
                )


if __name__ == "__main__":
    unittest.main()
