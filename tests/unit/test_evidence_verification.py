import unittest

from autovideo.media import (
    EntityFidelity,
    EvidenceVerificationConfig,
    EvidenceVerificationEngine,
    StockCandidate,
    VisionVerificationResult,
    build_visual_intent,
    score_candidate,
    select_best_candidate,
)


class EvidenceVerificationTests(unittest.TestCase):
    def test_greenland_shark_outranks_generic_shark_without_query_proof(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "A Greenland shark can live for centuries.",
                "broll": "Greenland shark deep sea",
                "broll_queries": ["Greenland shark close underwater"],
                "primary_subject": "Greenland shark",
                "scene_entity": {
                    "canonical_entity": "Greenland shark",
                    "aliases": ["Somniosus microcephalus", "sleeper shark"],
                    "required_terms": ["Greenland shark"],
                    "optional_terms": ["Arctic", "deep sea"],
                    "forbidden_terms": ["reef shark", "great white"],
                },
            },
            "Why the Greenland Shark Lives So Long",
        )
        generic = StockCandidate(
            provider="pexels",
            provider_id="generic",
            query="Greenland shark close underwater",
            title="reef shark swimming underwater",
            description="tropical reef shark",
            width=1080,
            height=1920,
        )
        exact = StockCandidate(
            provider="wikimedia",
            provider_id="exact",
            query="Greenland shark close underwater",
            title="Greenland shark swimming in Arctic water",
            description="Somniosus microcephalus deep sea shark",
            width=1080,
            height=1920,
        )

        result = select_best_candidate(intent, [generic, exact], minimum_score=-20)

        self.assertEqual(result.provider_id, "exact")
        self.assertEqual(result.score.breakdown["_entity_fidelity_value"], EntityFidelity.EXACT_ENTITY.value)

        generic_score = score_candidate(intent, generic)
        self.assertEqual(generic_score.breakdown["_entity_fidelity_value"], EntityFidelity.GENERIC_CATEGORY.value)
        self.assertIn("entity fidelity too weak", " ".join(generic_score.rejection_reasons))

    def test_query_text_does_not_increase_entity_fidelity(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "A Greenland shark can live for centuries.",
                "broll_queries": ["Greenland shark close underwater"],
                "primary_subject": "Greenland shark",
                "scene_entity": {
                    "canonical_entity": "Greenland shark",
                    "aliases": ["Somniosus microcephalus"],
                    "required_terms": ["Greenland shark"],
                    "optional_terms": ["Arctic"],
                    "forbidden_terms": [],
                },
            },
            "Why the Greenland Shark Lives So Long",
        )
        query_only = StockCandidate(
            provider="pexels",
            provider_id="query-only",
            query="Greenland shark close underwater",
            title="underwater blue ocean",
            description="deep sea water",
            width=1080,
            height=1920,
        )

        score = score_candidate(intent, query_only)

        self.assertNotEqual(score.breakdown["_entity_fidelity_value"], EntityFidelity.EXACT_ENTITY.value)
        self.assertFalse(score.breakdown["_evidence_verification_value"]["diagnostics"]["query_used_as_evidence"])

    def test_butterfly_rejects_honeybee_as_wrong_keyword_match(self) -> None:
        intent = build_visual_intent(
            {
                "narration": "A butterfly changes shape completely.",
                "primary_subject": "butterfly",
                "scene_entity": {
                    "canonical_entity": "butterfly",
                    "aliases": ["monarch butterfly"],
                    "required_terms": ["butterfly"],
                    "optional_terms": ["caterpillar", "flower"],
                    "forbidden_terms": ["honeybee", "bee"],
                },
            },
            "How Butterflies Transform",
        )
        honeybee = StockCandidate(
            provider="pixabay",
            provider_id="bee",
            query="butterfly macro",
            title="honeybee on flower",
            description="bee pollinator closeup",
            width=1080,
            height=1920,
        )

        score = score_candidate(intent, honeybee)

        self.assertIn(score.breakdown["_entity_fidelity_value"], {
            EntityFidelity.RELATED_ENTITY.value,
            EntityFidelity.GENERIC_CATEGORY.value,
            EntityFidelity.UNKNOWN.value,
        })
        self.assertIn("entity fidelity too weak", " ".join(score.rejection_reasons))

    def test_optional_vision_can_upgrade_low_metadata_match(self) -> None:
        calls = []

        def verifier(requested_entity, candidate):
            calls.append((requested_entity, candidate.provider_id))
            return VisionVerificationResult(
                match=True,
                matched_entity=requested_entity,
                confidence=0.91,
                reasoning="representative frame shows requested subject",
                provider="gemini",
            )

        engine = EvidenceVerificationEngine(
            EvidenceVerificationConfig(
                enable_ai_visual_qa=True,
                ai_visual_qa_min_metadata_confidence=0.95,
                ai_visual_qa_max_candidates=1,
            ),
            vision_verifier=verifier,
        )
        intent = build_visual_intent(
            {
                "primary_subject": "Titanic",
                "scene_entity": {
                    "canonical_entity": "Titanic",
                    "aliases": ["RMS Titanic"],
                    "required_terms": ["Titanic"],
                    "optional_terms": ["shipwreck"],
                    "forbidden_terms": ["cruise ship"],
                },
            },
            "Why the Titanic Still Fascinates the World",
        )
        candidate = StockCandidate(
            provider="local",
            provider_id="frame",
            query="Titanic wreck",
            title="historic shipwreck",
            description="deep ocean wreck",
            width=1080,
            height=1920,
        )

        score = score_candidate(intent, candidate, evidence_engine=engine)

        self.assertEqual(calls, [("Titanic", "frame")])
        self.assertTrue(score.breakdown["_evidence_verification_value"]["vision_invoked"])
        self.assertGreaterEqual(score.breakdown["_metadata_confidence_value"], 0.9)

    def test_vision_failure_does_not_fail_metadata_accepted_candidate(self) -> None:
        def verifier(_requested_entity, _candidate):
            raise RuntimeError("quota exceeded")

        engine = EvidenceVerificationEngine(
            EvidenceVerificationConfig(
                enable_ai_visual_qa=True,
                ai_visual_qa_min_metadata_confidence=0.95,
                ai_visual_qa_max_candidates=1,
            ),
            vision_verifier=verifier,
        )
        intent = build_visual_intent(
            {
                "primary_subject": "Greenland shark",
                "scene_entity": {
                    "canonical_entity": "Greenland shark",
                    "aliases": ["Somniosus microcephalus"],
                    "required_terms": ["Greenland shark"],
                    "optional_terms": ["Arctic"],
                    "forbidden_terms": [],
                },
            },
            "Why the Greenland Shark Lives So Long",
        )
        candidate = StockCandidate(
            provider="wikimedia",
            provider_id="greenland",
            query="Greenland shark",
            title="Greenland shark in cold Arctic water",
            description="Somniosus microcephalus",
            width=1080,
            height=1920,
        )

        score = score_candidate(intent, candidate, evidence_engine=engine)

        self.assertTrue(score.quality_gate_passed)
        evidence = score.breakdown["_evidence_verification_value"]
        self.assertTrue(evidence["vision_invoked"])
        self.assertIn("quota exceeded", evidence["vision_result"]["error"])


if __name__ == "__main__":
    unittest.main()
