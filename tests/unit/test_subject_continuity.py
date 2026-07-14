import unittest
from types import SimpleNamespace

from autovideo.media import (
    MediaMode,
    ShotPlan,
    StockCandidate,
    SubjectContinuityEngine,
    VisualDirector,
    build_visual_intent,
    score_candidate,
)


class SubjectContinuityTests(unittest.TestCase):
    def test_engine_adds_plan_level_subject_fields(self) -> None:
        segments = [
            {
                "narration": "Sharks survived for hundreds of millions of years.",
                "broll": "shark swimming underwater",
            },
            {
                "narration": "Their teeth and skeleton reveal why.",
                "broll": "shark tooth fossil",
            },
        ]

        plan = SubjectContinuityEngine().apply(
            VisualDirector().plan(topic="Why Sharks Survived", segments=segments),
            segments=segments,
        )

        self.assertEqual(plan.primary_subject, "shark")
        self.assertGreaterEqual(plan.subject_persistence_target, 0.85)
        self.assertIn("shark tooth", plan.supporting_subjects)
        self.assertIn("diver", " ".join(plan.forbidden_substitutions).lower())
        self.assertTrue(all(intent.primary_subject == "shark" for intent in plan.intents))

        restored = ShotPlan.from_dict(plan.to_dict())
        self.assertEqual(restored.primary_subject, "shark")
        self.assertIn("shark tooth", restored.supporting_subjects)

    def test_primary_subject_scores_above_forbidden_substitution(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "Sharks glide through the ocean.",
                "broll": "shark swimming underwater",
                "primary_subject": "shark",
                "supporting_subjects": ["shark fin", "shark tooth"],
                "forbidden_substitutions": ["diver", "jellyfish", "random fish"],
                "media_mode": MediaMode.SHOW.value,
            },
            "Why Sharks Survived",
        )
        shark = StockCandidate(
            provider="pexels",
            provider_id="shark-1",
            query="shark swimming underwater",
            title="Great white shark swimming underwater",
            description="Real shark in blue ocean",
            width=1080,
            height=1920,
        )
        diver = StockCandidate(
            provider="pexels",
            provider_id="diver-1",
            query="shark swimming underwater",
            title="Scuba diver above reef",
            description="A diver swims with generic reef fish",
            width=1080,
            height=1920,
        )

        shark_score = score_candidate(intent, shark)
        diver_score = score_candidate(intent, diver)

        self.assertGreater(shark_score.score, diver_score.score)
        self.assertTrue(shark_score.breakdown["_subject_visible_value"])
        self.assertIn("primary subject continuity missing", diver_score.rejection_reasons)
        self.assertTrue(
            any(reason.startswith("forbidden subject substitution accepted: diver")
                for reason in diver_score.rejection_reasons)
        )

    def test_report_counts_show_scene_subject_visibility(self) -> None:
        segments = [
            {"narration": "Penguins huddle together.", "broll": "penguins huddling"},
            {"narration": "Wind shapes the ice.", "broll": "antarctic wind"},
        ]
        plan = SubjectContinuityEngine().apply(
            VisualDirector().plan(topic="How Penguins Survive Antarctica", segments=segments),
            segments=segments,
        )
        assets = [
            SimpleNamespace(metadata={
                "selection": {
                    "media_mode": "show",
                    "subject_visible": True,
                    "provider": "pexels",
                }
            }),
            SimpleNamespace(metadata={
                "selection": {
                    "media_mode": "show",
                    "subject_visible": False,
                    "provider": "wikimedia",
                    "continuity_reason": "primary subject absent from provider metadata",
                }
            }),
        ]

        report = SubjectContinuityEngine().report_from_assets(plan, assets)

        self.assertEqual(report.primary_subject, "penguin")
        self.assertEqual(report.subject_visible_percentage, 0.5)
        self.assertEqual(len(report.reasons_for_continuity_breaks), 1)


if __name__ == "__main__":
    unittest.main()
