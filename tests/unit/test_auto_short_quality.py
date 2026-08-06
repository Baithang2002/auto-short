from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from tests.unit import _path  # noqa: F401

import auto_short


class AutoShortQualityTests(unittest.TestCase):
    def test_dependency_check_accepts_non_gemini_llm_fallback(self) -> None:
        old_values = (
            auto_short.GEMINI_API_KEY,
            auto_short.SAMBANOVA_API_KEY,
            auto_short.GROQ_API_KEY,
            auto_short.PEXELS_API_KEY,
        )
        auto_short.GEMINI_API_KEY = ""
        auto_short.SAMBANOVA_API_KEY = "sambanova-key"
        auto_short.GROQ_API_KEY = ""
        auto_short.PEXELS_API_KEY = ""
        try:
            with patch.object(auto_short.shutil, "which", return_value="ffmpeg"):
                auto_short.check_deps()
        finally:
            (
                auto_short.GEMINI_API_KEY,
                auto_short.SAMBANOVA_API_KEY,
                auto_short.GROQ_API_KEY,
                auto_short.PEXELS_API_KEY,
            ) = old_values

    def test_voice_registry_is_reused_for_all_segments(self) -> None:
        registry = object()
        first_track = Mock(provider="audiolab")
        second_track = Mock(provider="audiolab")
        for track in (first_track, second_track):
            track.to_legacy_item.side_effect = lambda index, segment, current=track: {
                "idx": index,
                "segment": segment,
                "voice": f"voice-{index}.mp3",
                "duration": 1.0,
                "voice_track": current,
            }
        segments = [{"narration": "one"}, {"narration": "two"}]

        with patch.object(auto_short, "_voice_provider_registry", return_value=registry), patch.object(
            auto_short,
            "_make_voice_track",
            side_effect=[first_track, second_track],
        ) as make_track:
            auto_short.make_all_voices(segments, auto_short._FORMAT_PROFILE)

        self.assertEqual(2, make_track.call_count)
        self.assertTrue(all(call.kwargs["registry"] is registry for call in make_track.call_args_list))
        self.assertEqual("", make_track.call_args_list[0].kwargs["preferred_provider"])
        self.assertEqual("audiolab", make_track.call_args_list[1].kwargs["preferred_provider"])

    def test_broll_query_list_adds_shot_variety(self) -> None:
        queries = auto_short.broll_query_list(
            {
                "narration": "This tiny fox vanishes into snowy arctic tundra.",
                "broll": "arctic fox hunting",
                "broll_queries": ["arctic fox close up"],
            },
            "arctic fox survival",
        )
        self.assertEqual(queries[0], "wild arctic fox hunting")
        self.assertIn("wild arctic fox close up", queries)
        self.assertIn("arctic fox hunting in snow", queries)
        self.assertIn("wild arctic fox in snow", queries)
        self.assertIn("arctic fox hunting close up", queries)
        self.assertIn("arctic fox hunting in snowy arctic", queries)
        self.assertIn("arctic fox hunting wide shot", queries)

    def test_query_qualification_keeps_cold_wildlife_specific(self) -> None:
        qualified = auto_short._qualify_query("arctic fox close up", "arctic survival")

        self.assertIn("wildlife", qualified)
        self.assertIn("snow", qualified)

    def test_landscape_query_qualification_does_not_force_wildlife(self) -> None:
        qualified = auto_short._qualify_query("arctic tundra snow landscape", "arctic survival")

        self.assertIn("snow", qualified)
        self.assertNotIn("wildlife", qualified)

    def test_lightning_query_qualification_uses_weather_terms(self) -> None:
        qualified = auto_short._qualify_query("lightning strike over city", "weather science")

        self.assertIn("storm", qualified)
        self.assertIn("sky", qualified)
        self.assertNotIn("wildlife", qualified)

    def test_lightning_broad_fallback_does_not_use_animal_terms(self) -> None:
        terms = auto_short._broad_fallback_terms(
            "lightning strike",
            "A lightning bolt forms inside a thunderstorm cloud.",
        )

        self.assertIn("lightning storm sky", terms)
        self.assertNotIn("wildlife close up", terms)
        self.assertNotIn("animals in wild", terms)
        self.assertNotIn("nature documentary", terms)

    def test_unknown_topic_broad_fallback_does_not_default_to_wildlife(self) -> None:
        terms = auto_short._broad_fallback_terms(
            "how glass is manufactured",
            "Molten material cools into a transparent sheet.",
            "How Glass Is Made",
        )

        self.assertIn("how glass is manufactured", terms)
        self.assertFalse(any("wildlife" in term or "animals" in term for term in terms))

    def test_pexels_relevance_penalizes_wrong_arctic_fox_matches(self) -> None:
        good = {"url": "https://www.pexels.com/video/arctic-fox-running-in-snow-123/"}
        bad = {"url": "https://www.pexels.com/video/husky-dog-inside-zoo-cage-456/"}

        self.assertGreater(
            auto_short.pexels_relevance_score(good, "arctic fox close up"),
            auto_short.pexels_relevance_score(bad, "arctic fox close up"),
        )

    def test_short_complete_story_is_not_rejected_by_duration(self) -> None:
        profile = auto_short._FORMAT_PROFILE
        script = {
            "title": "A Tiny Lantern Switches On at Dusk",
            "segments": [
                {"narration": "A quiet survival trick appears in plain sight tonight.", "broll": "firefly glowing", "beat_role": "hook"},
                {"narration": "Its light is cold, made by a precise chemical trick.", "broll": "firefly close up", "beat_role": "setup"},
                {"narration": "The flash is a message between mates in the grass.", "broll": "firefly in meadow", "beat_role": "discovery"},
                {"narration": "Follow Wonders of the Nature for more wild secrets.", "broll": "night forest", "beat_role": "conclusion_cta"},
            ],
        }

        fatal, soft = auto_short.script_quality_notes(script, profile=profile)

        self.assertEqual([], fatal)
        self.assertFalse(any("duration" in note or "second" in note for note in fatal + soft))

    def test_missing_hook_is_rejected(self) -> None:
        profile = auto_short._FORMAT_PROFILE
        script = {
            "title": "A Tiny Lantern Switches On at Dusk",
            "segments": [
                {"narration": "A chemical reaction creates cold light in the night.", "broll": "firefly close up", "beat_role": "discovery"},
                {"narration": "Follow Wonders of the Nature for more wild secrets.", "broll": "night forest", "beat_role": "conclusion_cta"},
            ],
        }

        fatal, _soft = auto_short.script_quality_notes(script, profile=profile)

        self.assertTrue(any("missing a hook" in note for note in fatal))

    def test_title_style_requires_a_curious_declarative_statement(self) -> None:
        self.assertTrue(any("question-led" in note for note in auto_short._title_style_notes("Why Fireflies Glow")))
        self.assertTrue(any("question mark" in note for note in auto_short._title_style_notes("Fireflies Glow?")))
        self.assertTrue(any("absolute" in note for note in auto_short._title_style_notes("Fireflies Never Stop Glowing")))
        self.assertTrue(any("absolute" in note for note in auto_short._title_style_notes("A Beaver Completely Rewrites a River")))
        self.assertEqual([], auto_short._title_style_notes("A Tiny Lantern Switches On at Dusk"))


class CeilingTrimTests(unittest.TestCase):
    def _long_story(self) -> dict:
        return {
            "title": "A Long Overbuilt Chameleon Story",
            "description": "desc",
            "instagram_caption": "cap",
            "music_mood": "curious",
            "hashtags": ["#chameleon"],
            "segments": [
                {"narration": "A hidden predator stalks still leaves with perfect patience every single day without blinking once.", "broll": "chameleon on branch", "broll_queries": ["chameleon close up"], "beat_role": "hook", "beat_importance": 10},
                {"narration": "Most reptiles simply blend in with their background for a moment before moving away quickly to escape.", "broll": "chameleon leaves", "broll_queries": ["chameleon wide"], "beat_role": "context", "beat_importance": 4},
                {"narration": "The chameleon changes its whole skin color using tiny cells that expand and contract under its skin.", "broll": "chameleon skin", "broll_queries": ["chameleon detail"], "beat_role": "setup", "beat_importance": 5},
                {"narration": "Light lands on these cells and bounces back in a completely different color to confuse the watchful eye.", "broll": "chameleon colors", "broll_queries": ["chameleon macro"], "beat_role": "discovery", "beat_importance": 6},
                {"narration": "The shift is not instant so the chameleon must stay very still while its skin slowly changes tone.", "broll": "chameleon still", "broll_queries": ["chameleon branch"], "beat_role": "conflict", "beat_importance": 6},
                {"narration": "Each change is controlled by mood heat light and the need to vanish from a hungry enemy completely.", "broll": "chameleon mood", "broll_queries": ["chameleon hunting"], "beat_role": "escalation", "beat_importance": 6},
                {"narration": "In a flash the pattern shifts and the chameleon becomes nearly invisible against the dried brown bark.", "broll": "chameleon hidden", "broll_queries": ["chameleon camouflage"], "beat_role": "climax", "beat_importance": 9},
                {"narration": "Follow Wonders of the Nature for more wild survival secrets and amazing hidden animals every single day.", "broll": "jungle canopy", "broll_queries": ["jungle wide"], "beat_role": "conclusion_cta", "beat_importance": 9},
            ],
        }

    def test_llm_trim_success_preserves_metadata(self) -> None:
        profile = auto_short._FORMAT_PROFILE
        script = self._long_story()
        trimmed_copy = {
            "segments": script["segments"][:5],
            "title": "New title",
            "description": "",
            "instagram_caption": "",
            "music_mood": "",
            "hashtags": [],
        }
        with patch.object(auto_short, "_script_draft", return_value=trimmed_copy) as draft:
            applied = auto_short._try_trim_story(
                "chameleons", script, None, profile, budget=50.0
            )
        self.assertTrue(applied)
        self.assertEqual(1, draft.call_count)
        self.assertEqual(5, len(script["segments"]))
        self.assertEqual("New title", script["title"])
        self.assertEqual("desc", script["description"])
        self.assertEqual("cap", script["instagram_caption"])

    def test_deterministic_fallback_when_llm_keeps_failing(self) -> None:
        profile = auto_short._FORMAT_PROFILE
        script = self._long_story()
        with patch.object(
            auto_short,
            "_script_draft",
            side_effect=RuntimeError("Generated script is still malformed: segment 1 is missing broll"),
        ):
            applied = auto_short._try_trim_story(
                "chameleons", script, None, profile, budget=50.0
            )
        self.assertTrue(applied)
        roles = {str(seg.get("beat_role")) for seg in script["segments"]}
        self.assertIn("hook", roles)
        self.assertIn("climax", roles)
        self.assertIn("conclusion_cta", roles)
        self.assertGreaterEqual(len(script["segments"]), profile.min_story_beats)
        estimated = auto_short.story_planning.estimate_story_duration(script, profile, conservative=True)
        self.assertLessEqual(estimated, 50.0)

    def test_deterministic_fallback_respects_critical_asset_and_cannot_remove(self) -> None:
        profile = auto_short._FORMAT_PROFILE
        script = self._long_story()
        script["segments"][1]["critical_asset_dependency"] = "true"
        script["segments"][2]["beat_can_remove"] = "false"
        with patch.object(
            auto_short,
            "_script_draft",
            side_effect=RuntimeError("Generated script is still malformed"),
        ):
            applied = auto_short._try_trim_story(
                "chameleons", script, None, profile, budget=50.0
            )
        self.assertTrue(applied)
        roles = [str(seg.get("beat_role")) for seg in script["segments"]]
        self.assertIn("context", roles)
        self.assertIn("setup", roles)


if __name__ == "__main__":
    unittest.main()
