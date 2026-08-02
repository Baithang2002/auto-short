"""Focused tests for media-first critical discovery and script integration."""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.unit import _path  # noqa: F401

import auto_short
from autovideo.intelligence.topic_cards import TopicCard
from autovideo.intelligence import subject_definition_from_pipeline, verified_critical_scene_coverage
from autovideo.media import (
    CanonicalSceneEntityResolver,
    CandidateScore,
    DownloadedMediaEvidence,
    EditorialCanonBuilder,
    SceneConstraintPlanner,
    SceneEntityPlanner,
    SemanticVisualQueryEngine,
    StockCandidate,
    VerifiedMediaGateConfig,
    VisualDirector,
)


def _card() -> TopicCard:
    return TopicCard(
        id="wildlife-beaver-test",
        pillar="wildlife",
        subject="beaver",
        premise="How beavers build dams that reshape a stream",
        required_entity="beaver",
        required_action="carrying branches and placing them in a dam",
        hook_queries=("beaver carrying branch water", "beaver dam building close up"),
        reveal_queries=("beaver placing branches dam", "active beaver dam stream"),
        supporting_queries=("beaver pond",),
        fallback_visuals=("stick dam",),
        title_angles=("The animal that reroutes rivers",),
        source_difficulty="easy",
    )


def _candidate(provider_id: str, query: str, *, provider: str = "pexels") -> StockCandidate:
    return StockCandidate(
        provider=provider,
        provider_id=provider_id,
        query=query,
        title=f"beaver carrying branches building dam {provider_id}",
        description="beaver places a branch into an active dam",
        url=f"https://example.test/assets/{provider_id}?token=secret&expires=1",
        download_url=f"https://signed.example.test/{provider_id}.mp4?signature=secret",
        duration_sec=8.0,
        width=1080,
        height=1920,
        raw_metadata={
            "license": "Example License",
            "attribution": "Example Creator",
            "source_url": f"https://example.test/assets/{provider_id}",
        },
    )


def _score(_intent, candidate, **_kwargs) -> CandidateScore:
    value = 12.0 if "mismatch" in candidate.provider_id else 11.0
    return CandidateScore(
        score=value,
        breakdown={
            "_quality_gate_passed_value": True,
            "relevance_score": 9.0,
        },
    )


def _download(_url, path, **_kwargs):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"mock media")
    return True


def _verified_or_mismatch(request, _max_frames):
    mismatch = "mismatch" in request.media_path.name
    return DownloadedMediaEvidence(
        entity_match=not mismatch,
        entity_confidence=0.98 if not mismatch else 0.05,
        action_match=not mismatch,
        action_confidence=0.95 if not mismatch else 0.05,
        verified_entity="beaver" if not mismatch else "otter",
        verified_action=request.expected_action if not mismatch else "swimming",
        sampled_frames=("frame-1.jpg", "frame-2.jpg"),
        provider="gemini",
    )


class CriticalAssetTests(unittest.TestCase):
    def test_legacy_topic_writes_skipped_shape_without_provider_or_verifier_calls(self) -> None:
        loader = Mock()
        verifier = Mock()

        plan = auto_short.discover_critical_assets(
            "A legacy topic without a card",
            candidate_loader=loader,
            verifier=verifier,
        )

        self.assertEqual(plan["status"], "SKIPPED")
        self.assertEqual(plan["failure_classification"], "SKIPPED")
        loader.assert_not_called()
        verifier.assert_not_called()

    def test_discovers_two_locks_excludes_generated_and_keeps_stable_provenance(self) -> None:
        def loader(_provider, queries, _fallback):
            stem = "hook" if "carrying" in queries[0] else "reveal"
            return [
                _candidate(f"{stem}-mismatch", queries[0]),
                _candidate(f"{stem}-verified", queries[1]),
                _candidate(f"{stem}-generated", queries[0], provider="pollinations_image"),
            ]

        with tempfile.TemporaryDirectory() as directory, patch.object(
            auto_short, "score_candidate", side_effect=_score
        ), patch.dict(
            auto_short.os.environ,
            {"AUTO_VIDEO_CRITICAL_ASSET_MAX_ALTERNATIVES": "2"},
            clear=False,
        ):
            plan = auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels",),
                candidate_loader=loader,
                downloader=_download,
                verifier=_verified_or_mismatch,
                gate_config=VerifiedMediaGateConfig(
                    enabled=True,
                    critical_confidence_threshold=0.75,
                    critical_action_confidence_threshold=0.75,
                ),
            )

        locks = auto_short.critical_asset_overrides(plan)
        self.assertEqual(plan["status"], "VERIFIED")
        self.assertEqual(set(locks), {0, 1})
        self.assertTrue(all(lock["provider"] == "pexels" for lock in locks.values()))
        self.assertTrue(all("?" not in lock["source_url"] for lock in locks.values()))
        self.assertNotIn("download_url", json.dumps(plan))
        self.assertNotIn("pollinations", json.dumps(plan))
        self.assertTrue(all(len(role["attempts"]) == 2 for role in plan["roles"]))

    def test_discovery_ranking_receives_persistent_provider_ids(self) -> None:
        seen_used_ids = []

        def score(intent, candidate, **kwargs):
            seen_used_ids.append(set(kwargs.get("used_provider_ids") or ()))
            return _score(intent, candidate, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            auto_short, "_load_persistent_used", return_value={"pexels:historical"}
        ), patch.object(auto_short, "score_candidate", side_effect=score):
            auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels",),
                candidate_loader=lambda _provider, queries, _fallback: [
                    _candidate(f"fresh-{queries[0]}", queries[0])
                ],
                downloader=_download,
                verifier=_verified_or_mismatch,
            )

        self.assertTrue(seen_used_ids)
        self.assertTrue(all("pexels:historical" in used for used in seen_used_ids))

    def test_provider_outage_is_technical_but_healthy_no_results_is_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            technical = auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels",),
                candidate_loader=lambda *_args: (_ for _ in ()).throw(TimeoutError("timed out")),
                downloader=_download,
                verifier=_verified_or_mismatch,
            )
            content = auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels",),
                candidate_loader=lambda *_args: [],
                downloader=_download,
                verifier=_verified_or_mismatch,
            )

        self.assertEqual(technical["failure_classification"], "TECHNICAL_PROVIDER_FAILURE")
        self.assertEqual(content["failure_classification"], "CONTENT_ASSET_GAP")

    def test_auth_error_does_not_override_healthy_content_mismatch(self) -> None:
        def loader(provider, queries, _fallback):
            if provider == "europeana":
                raise RuntimeError("401 Unauthorized")
            return [_candidate(f"{provider}-candidate", queries[0], provider=provider)]

        mismatch = lambda request, _frames: DownloadedMediaEvidence(  # noqa: E731
            entity_match=True,
            entity_confidence=0.99,
            action_match=False,
            action_confidence=0.1,
            verified_entity="beaver",
            verified_action="swimming",
            sampled_frames=("frame.jpg",),
            provider="gemini",
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            auto_short, "score_candidate", side_effect=_score
        ):
            plan = auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels", "europeana"),
                candidate_loader=loader,
                downloader=_download,
                verifier=mismatch,
            )

        self.assertEqual(plan["failure_classification"], "CONTENT_ASSET_GAP")

    def test_verifier_quota_is_a_technical_failure(self) -> None:
        quota = lambda _request, _frames: DownloadedMediaEvidence(  # noqa: E731
            entity_match=False,
            error="429 RESOURCE_EXHAUSTED quota exceeded",
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            auto_short, "score_candidate", side_effect=_score
        ):
            plan = auto_short.discover_critical_assets(
                _card().premise,
                output_dir=Path(directory),
                card=_card(),
                providers=("pexels",),
                candidate_loader=lambda _provider, queries, _fallback: [
                    _candidate("candidate", queries[0])
                ],
                downloader=_download,
                verifier=quota,
            )

        self.assertEqual(plan["failure_classification"], "TECHNICAL_VERIFIER_FAILURE")

    def test_vision_generation_falls_back_to_next_configured_model(self) -> None:
        response = SimpleNamespace(text='{"entity_match": true}')
        generate = Mock(side_effect=[RuntimeError("503 UNAVAILABLE"), response])
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with patch.object(auto_short, "GEMINI_IMAGE_MODEL", "busy-model"), patch.object(
            auto_short, "GEMINI_MODELS", ["busy-model", "fallback-model"]
        ):
            result, model = auto_short._generate_gemini_vision_content(client, ["content"])

        self.assertIs(result, response)
        self.assertEqual(model, "fallback-model")
        self.assertEqual(
            [call.kwargs["model"] for call in generate.call_args_list],
            ["busy-model", "fallback-model"],
        )

    def test_locked_asset_reuses_provider_id_license_and_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hook.jpg"
            path.write_bytes(b"image")
            lock = {
                "provider": "wikimedia",
                "provider_id": "File:Beaver.jpg",
                "source_url": "https://commons.wikimedia.org/wiki/File:Beaver.jpg",
                "license": "CC BY-SA 4.0",
                "attribution": "Nature Author",
                "query": "beaver carrying branch",
                "local_path": str(path),
                "verification": {"decision": "verified"},
            }
            with patch.object(auto_short, "is_image", return_value=True):
                asset = auto_short._media_asset_from_critical_lock(lock, 0)

        self.assertEqual(asset.source_id, "wikimedia:File:Beaver.jpg")
        self.assertEqual(asset.metadata["selection"]["provider"], "wikimedia")
        self.assertEqual(asset.attribution["license"], "CC BY-SA 4.0")
        self.assertTrue(asset.metadata["critical_asset_lock"])

    def test_verified_lock_proves_source_coverage_without_reprobing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            media_path = Path(directory) / "hook.mp4"
            media_path.write_bytes(b"verified media")
            plan = {
                "status": "VERIFIED",
                "roles": [{
                    "scene_index": 0,
                    "status": "VERIFIED",
                    "expected_entity": "hummingbird",
                    "queries": ["hummingbird feeding while hovering"],
                    "selected": {
                        "provider": "pexels",
                        "query": "hummingbird feeding while hovering",
                        "score": 12.5,
                        "local_path": str(media_path),
                        "verification": {
                            "decision": "verified",
                            "verified_entity": "hummingbird",
                        },
                    },
                }],
            }
            coverage = verified_critical_scene_coverage(
                SimpleNamespace(
                    scene_index=0,
                    documentary_role="hook",
                    scene_importance="hook",
                    primary_subject="hummingbird",
                ),
                plan,
            )

        assert coverage is not None
        self.assertTrue(coverage.covered)
        self.assertEqual("verified_critical_asset_lock", coverage.coverage_basis)
        self.assertEqual(("pexels",), coverage.providers_attempted)

    def test_topic_card_identity_reaches_canonical_constraints_queries_and_exact_subject(self) -> None:
        card = TopicCard(
            id="wildlife-hummingbird-test",
            pillar="wildlife",
            subject="hummingbird",
            premise="How hummingbirds hover while feeding",
            required_entity="hummingbird",
            required_action="hovering while feeding from a flower or nectar feeder",
            hook_queries=("hummingbird feeding while hovering",),
            reveal_queries=("hummingbird wing motion slow motion",),
            supporting_queries=("hummingbird flower feeding",),
            fallback_visuals=("flower close up",),
            title_angles=("The bird that freezes in midair",),
            source_difficulty="easy",
        )
        segments = [
            {
                "narration": "A hummingbird hangs motionless beside a flower.",
                "broll": "hummingbird feeding while hovering",
                "broll_queries": ["hummingbird feeding while hovering"],
            },
            {
                "narration": "Its wings reverse direction to hold it in place.",
                "broll": "hummingbird wing motion slow motion",
                "broll_queries": ["hummingbird wing motion slow motion"],
            },
        ]
        canon, _lock, _roles, _domain = EditorialCanonBuilder().build(
            topic=card.premise,
            segments=segments,
            primary_subject_override=card.required_entity,
        )
        entity_plan = SceneEntityPlanner().plan(
            editorial_canon=canon,
            segments=segments,
        )
        shot_plan = auto_short.apply_topic_card_identity(
            VisualDirector().plan(
                topic=card.premise,
                segments=segments,
                editorial_canon=canon,
                scene_entity_plan=entity_plan,
            ),
            card,
        )
        canonical = CanonicalSceneEntityResolver().resolve(
            documentary_topic=card.premise,
            shot_plan=shot_plan,
        )
        constraints = SceneConstraintPlanner().plan(
            documentary_topic=card.premise,
            shot_plan=shot_plan,
            canonical_report=canonical,
        )
        semantic = SemanticVisualQueryEngine().plan(
            documentary_topic="",
            shot_plan=shot_plan,
            constraint_report=constraints,
        )
        subject = subject_definition_from_pipeline(
            editorial_canon=canon,
            canonical_report=canonical,
            shot_plan=shot_plan,
        )

        self.assertEqual("hummingbird", canonical.canonical_documentary_entity)
        self.assertTrue(all(scene.canonical_entity == "hummingbird" for scene in canonical.scenes))
        self.assertEqual(card.required_action, shot_plan.intents[0].action)
        constraint_scene = constraints.scene_for_index(0)
        semantic_scene = semantic.scene_for_index(0)
        assert constraint_scene is not None
        assert semantic_scene is not None
        self.assertTrue(any(
            item.source == "topic_card_required_action"
            for item in constraint_scene.constraints
        ))
        self.assertEqual("hummingbird", semantic_scene.canonical_visual_entity)
        self.assertTrue(any("hovering" in query for query in semantic_scene.provider_queries))
        self.assertEqual("hummingbird", subject.canonical_entity)
        self.assertTrue(subject.identity_defining)

    def test_reuse_requires_reverification_for_verified_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            plan_path = root / "critical_asset_plan.json"
            plan_path.write_text(json.dumps({
                "topic": _card().premise,
                "status": "VERIFIED",
                "roles": [
                    {"scene_index": 0, "status": "VERIFIED", "selected": {"local_path": str(first)}},
                    {"scene_index": 1, "status": "VERIFIED", "selected": {"local_path": str(second)}},
                ],
            }), encoding="utf-8")

            reusable = auto_short._critical_plan_outputs_valid(plan_path, _card().premise)
            must_rerun = auto_short._critical_plan_outputs_valid(
                plan_path,
                _card().premise,
                require_reverification=True,
            )

        self.assertTrue(reusable)
        self.assertFalse(must_rerun)

    def test_critical_stage_is_registered_before_script_generation(self) -> None:
        source = inspect.getsource(auto_short.main)

        self.assertLess(
            source.index('"critical_asset_discovery"'),
            source.index('"script_generation"'),
        )
        self.assertLess(source.index('"script_generation"'), source.index('"voice_generation"'))

    def test_12_segment_prompt_contains_locked_visuals_and_story_beats(self) -> None:
        plan = {
            "status": "VERIFIED",
            "roles": [
                {
                    "role": "hook",
                    "scene_index": 0,
                    "status": "VERIFIED",
                    "expected_entity": "beaver",
                    "expected_action": "carrying branches and placing them in a dam",
                    "selected": {
                        "query": "beaver carrying branch water",
                        "verification": {"verified_entity": "beaver", "verified_action": "carrying a branch"},
                    },
                },
                {
                    "role": "main_reveal",
                    "scene_index": 1,
                    "status": "VERIFIED",
                    "expected_entity": "beaver",
                    "expected_action": "carrying branches and placing them in a dam",
                    "selected": {
                        "query": "beaver placing branches dam",
                        "verification": {"verified_entity": "beaver", "verified_action": "placing branches"},
                    },
                },
            ],
        }
        narration = "Beavers reshape rushing streams by carrying branches into carefully built dams every single day."
        segments = [
            {
                "narration": narration,
                "broll": "beaver carrying branches placing dam",
                "broll_queries": ["beaver carrying branches placing dam"] * 4,
            }
            for _ in range(11)
        ]
        segments.append({
            "narration": "Follow Wonders of the Nature for more wild mechanisms hiding in plain sight.",
            "broll": "beaver carrying branches placing dam",
            "broll_queries": ["beaver carrying branches placing dam"] * 4,
        })
        response = json.dumps({
            "title": "The River Builder Hiding in Plain Sight",
            "description": "See how beavers reshape a stream.",
            "instagram_caption": "A river-changing secret in action. Follow for more.",
            "music_mood": "curious",
            "hashtags": ["#beaver", "#wildlife"],
            "segments": segments,
        })
        prompts = []
        with tempfile.TemporaryDirectory() as directory, patch.object(
            auto_short,
            "generate_script_raw",
            side_effect=lambda prompt: prompts.append(prompt) or response,
        ), patch.object(auto_short, "OUT_DIR", Path(directory)):
            script = auto_short.generate_script(
                _card().premise,
                12,
                auto_short.TARGET_DURATION,
                critical_asset_plan=plan,
            )

        prompt = prompts[0]
        for beat in (
            "opening image", "subject's need", "obstacle or tension", "first attempt",
            "visible mechanism", "cost or stakes", "escalation", "intimate detail",
            "reveal", "consequence", "quiet visual payoff", "existing short branded CTA",
        ):
            self.assertIn(beat, prompt)
        self.assertIn("confirmed beaver; visible action: carrying a branch", prompt)
        self.assertIn("Return ONE title only", prompt)
        self.assertIn("curious declarative statement", prompt)
        self.assertIn("This [animal/place/event] looks [simple/beautiful/harmless]", prompt)
        self.assertIn("first segment's visual searches must promise motion or impact", prompt)
        self.assertIn("Never make the first segment's primary b-roll a static landscape", prompt)
        self.assertIn("For segment 1, the first query must be the strongest motion/action/close-up query", prompt)
        self.assertEqual(len(script["segments"]), 12)
        self.assertEqual(script["category_id"], "15")

    def test_script_qa_rejects_academic_suffix_and_misaligned_lock(self) -> None:
        data = {
            "title": "How Beavers Work | Biology",
            "segments": [{
                "narration": "A concrete visual explanation gives this complete sentence enough spoken words today.",
                "broll": "generic forest",
                "broll_queries": ["generic forest"],
            }],
        }
        plan = {
            "status": "VERIFIED",
            "roles": [{
                "scene_index": 0,
                "role": "hook",
                "status": "VERIFIED",
                "expected_entity": "beaver",
                "expected_action": "carrying branches",
            }],
        }

        fatal, _soft = auto_short.script_quality_notes(data, 1, 5, plan)

        self.assertTrue(any("academic title suffix" in note for note in fatal))
        self.assertTrue(any("locked entity" in note for note in fatal))


if __name__ == "__main__":
    unittest.main()
