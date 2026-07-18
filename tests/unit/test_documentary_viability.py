import unittest

from autovideo.intelligence import (
    DocumentaryViabilityConfig,
    DocumentaryViabilityDecision,
    DocumentaryViabilityEngine,
)


class DocumentaryViabilityTests(unittest.TestCase):
    def test_concrete_provider_rich_topic_is_approved(self) -> None:
        report = DocumentaryViabilityEngine().evaluate("Why Volcanoes Create New Land")

        self.assertEqual(report.decision, DocumentaryViabilityDecision.APPROVED)
        self.assertGreaterEqual(report.overall_score, 0.62)
        self.assertIn("geology/science archives and stock", report.recommended_strategy)

    def test_abstract_explainer_topic_requires_review(self) -> None:
        report = DocumentaryViabilityEngine().evaluate("The Invisible Shield Protecting Your Life")

        self.assertEqual(report.decision, DocumentaryViabilityDecision.REVIEW)
        self.assertLess(report.overall_score, 0.62)
        self.assertIn("diagrams or Hybrid Composer likely required", report.recommended_strategy)

    def test_very_abstract_topic_can_be_skipped(self) -> None:
        config = DocumentaryViabilityConfig(
            minimum_viability_score=0.62,
            review_minimum_score=0.55,
        )
        report = DocumentaryViabilityEngine(config).evaluate("Consciousness and Invisible Memory Formation")

        self.assertEqual(report.decision, DocumentaryViabilityDecision.SKIP)

    def test_entity_ambiguity_reduces_score(self) -> None:
        engine = DocumentaryViabilityEngine()
        ambiguous = engine.evaluate("Jaguar speed secrets")
        specific = engine.evaluate("Greenland Shark survival secrets")

        ambiguous_entity = ambiguous.to_dict()["category_scores"]["entity_risk"]["score"]
        specific_entity = specific.to_dict()["category_scores"]["entity_risk"]["score"]
        self.assertLess(ambiguous_entity, specific_entity)

    def test_config_loads_from_environment_mapping(self) -> None:
        config = DocumentaryViabilityConfig.from_env({
            "AUTO_VIDEO_DOCUMENTARY_VIABILITY_ENABLED": "0",
            "AUTO_VIDEO_MIN_DOCUMENTARY_VIABILITY_SCORE": "0.70",
            "AUTO_VIDEO_REVIEW_DOCUMENTARY_VIABILITY_SCORE": "0.40",
            "AUTO_VIDEO_ALLOW_REVIEW_TOPICS": "false",
            "AUTO_VIDEO_VIABILITY_WEIGHT_VISUAL_AVAILABILITY": "0.5",
        })

        self.assertFalse(config.enabled)
        self.assertEqual(config.minimum_viability_score, 0.70)
        self.assertEqual(config.review_minimum_score, 0.40)
        self.assertFalse(config.allow_review_topics)
        self.assertEqual(config.weights["visual_availability"], 0.5)

    def test_report_serializes_stable_shape(self) -> None:
        report = DocumentaryViabilityEngine().evaluate("How Penguins Survive Antarctica").to_dict()

        self.assertEqual(report["decision"], "APPROVED")
        self.assertIn("overall_score", report)
        self.assertIn("visual_availability", report["category_scores"])
        self.assertIn("recommended_production_strategy", report)


if __name__ == "__main__":
    unittest.main()
