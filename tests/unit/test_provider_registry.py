from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401
from autovideo.config import AppConfig, ProviderRegistry, Settings, resolve_render_profile
from autovideo.providers.base import ProviderError, ProviderFallbackError, ProviderHealth, ProviderHealthStatus
from autovideo.providers.factory import build_voice_registry
from autovideo.providers.llm import CallableLLMProvider, MockLLMProvider
from autovideo.providers.voice import MockVoiceProvider, VoiceRequest


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_returns_enabled_providers_by_priority(self) -> None:
        registry = ProviderRegistry()
        registry.register("llm", "slow", object(), priority=50)
        fast = object()
        registry.register("llm", "fast", fast, priority=10)
        registry.register("llm", "disabled", object(), priority=1, enabled=False)

        providers = list(registry.providers("llm"))

        self.assertEqual(providers[0].name, "fast")
        self.assertEqual(providers[1].name, "slow")
        self.assertIs(registry.first("llm"), fast)

    def test_missing_capability_returns_none(self) -> None:
        self.assertIsNone(ProviderRegistry().first("voice"))

    def test_registry_filters_by_profile_feature_and_health(self) -> None:
        registry = ProviderRegistry()
        healthy = object()
        unavailable = object()
        registry.register(
            "voice",
            "healthy",
            healthy,
            priority=10,
            profiles=("development",),
            features=("scene_narration",),
            health=ProviderHealth(ProviderHealthStatus.HEALTHY),
        )
        registry.register(
            "voice",
            "unavailable",
            unavailable,
            priority=1,
            profiles=("development",),
            features=("scene_narration",),
            health=ProviderHealth(ProviderHealthStatus.UNAVAILABLE),
        )

        providers = list(registry.providers("voice", profile="development", feature="scene_narration"))

        self.assertEqual([p.name for p in providers], ["healthy"])
        self.assertIs(registry.first("voice", profile="development", feature="scene_narration"), healthy)

    def test_registry_executes_fallback_chain(self) -> None:
        registry = ProviderRegistry()

        class FailingProvider:
            def run(self) -> str:
                raise ProviderError("first", "failed")

        class WorkingProvider:
            def run(self) -> str:
                return "ok"

        registry.register("llm", "first", FailingProvider(), priority=1)
        registry.register("llm", "second", WorkingProvider(), priority=2)

        result = registry.execute("llm", lambda provider: provider.run())

        self.assertEqual(result, "ok")

    def test_registry_raises_typed_fallback_error(self) -> None:
        registry = ProviderRegistry()

        class FailingProvider:
            def run(self) -> str:
                raise ProviderError("first", "failed")

        registry.register("llm", "first", FailingProvider())

        with self.assertRaises(ProviderFallbackError):
            registry.execute("llm", lambda provider: provider.run())

    def test_render_profile_selection_and_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_RENDER_PROFILE": "production",
                "AUTO_VIDEO_VOICE_PROVIDER": "edge_tts",
                "CHANNEL_NAME": "Custom Channel",
                "AUTO_VIDEO_RETRY_ATTEMPTS": "5",
            })

            config = AppConfig.from_settings(settings)

            self.assertEqual(config.render_profile.name, "production")
            self.assertEqual(config.provider_priority["voice"][0], "edge_tts")
            self.assertIn("elevenlabs", config.provider_priority["voice"])
            self.assertEqual(config.channel_name, "Custom Channel")
            self.assertEqual(config.retry_attempts, 5)

    def test_testing_profile_disables_external_calls_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={"AUTO_VIDEO_RENDER_PROFILE": "testing"})
            config = AppConfig.from_settings(settings)

            self.assertEqual(config.render_profile.name, "testing")
            self.assertFalse(config.feature_flags["allow_external_api_calls"])
            self.assertEqual(config.provider_priority["voice"][0], "mock")

    def test_production_voice_registry_prefers_elevenlabs_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_RENDER_PROFILE": "production",
                "ELEVENLABS_API_KEY": "test-key",
                "ELEVENLABS_VOICE_ID": "voice-1",
            })
            config = AppConfig.from_settings(settings)

            registry = build_voice_registry(config)

            self.assertEqual(registry.provider_names("voice", profile="production")[:2], ("elevenlabs", "edge_tts"))

    def test_development_voice_registry_prefers_elevenlabs_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "ELEVENLABS_API_KEY": "test-key",
                "ELEVENLABS_VOICE_ID": "voice-1",
            })
            config = AppConfig.from_settings(settings)

            registry = build_voice_registry(config)

            names = registry.provider_names("voice", profile="development")
            self.assertEqual(names[:2], ("elevenlabs", "edge_tts"))

    def test_development_voice_registry_falls_back_without_elevenlabs_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={})
            config = AppConfig.from_settings(settings)

            registry = build_voice_registry(config)

            names = registry.provider_names("voice", profile="development")
            self.assertEqual(names[0], "edge_tts")
            self.assertNotIn("elevenlabs", names)

    def test_mock_voice_provider_supports_scene_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = MockVoiceProvider()

            result = provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="scene-1"))

            self.assertEqual(result.provider, "mock")
            self.assertTrue(output.exists())
            self.assertEqual(provider.requests[0].scene_id, "scene-1")

    def test_callable_llm_maps_unconfigured_provider_to_typed_error(self) -> None:
        provider = CallableLLMProvider("missing", lambda _prompt: (None, None))

        with self.assertRaises(ProviderError):
            provider.generate_text("prompt")

    def test_mock_llm_returns_json_without_external_call(self) -> None:
        provider = MockLLMProvider('{"ok": true}')

        result = provider.generate_json("prompt")

        self.assertEqual(result.value, {"ok": True})

    def test_resolve_render_profile_accepts_ci_alias(self) -> None:
        self.assertEqual(resolve_render_profile("ci").name, "testing")


if __name__ == "__main__":
    unittest.main()
