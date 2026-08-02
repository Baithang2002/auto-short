from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from tests.unit import _path  # noqa: F401
from autovideo.config import AppConfig, ProviderRegistry, Settings, resolve_render_profile
from autovideo.providers.base import ProviderError, ProviderExecutionError, ProviderFallbackError, ProviderHealth, ProviderHealthStatus, ProviderUnavailableError
from autovideo.providers.factory import build_voice_registry
from autovideo.providers.llm import CallableLLMProvider, MockLLMProvider
from autovideo.providers.voice import AudioLabVoiceProvider, ElevenLabsVoiceProvider, MockVoiceProvider, VoiceRequest


class _FakeElevenLabsResponse:
    def __init__(self, status_code: int, content: bytes = b"audio", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.text = f"status {status_code}"
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


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

    def test_elevenlabs_voice_ids_rotate_by_github_run_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "ELEVENLABS_VOICE_IDS": "voice-1, voice-2, voice-3, voice-4",
                "GITHUB_RUN_NUMBER": "5",
            })

            config = AppConfig.from_settings(settings)

            self.assertEqual(config.elevenlabs_voice_ids, ("voice-1", "voice-2", "voice-3", "voice-4"))
            self.assertEqual(config.elevenlabs_voice_index, 1)
            self.assertEqual(config.elevenlabs_voice_id, "voice-2")

    def test_elevenlabs_accounts_include_numbered_fallback_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "ELEVENLABS_API_KEY": "primary-key",
                "ELEVENLABS_VOICE_IDS": "voice-1, voice-2",
                "ELEVENLABS_VOICE_ROTATION_INDEX": "1",
                "ELEVENLABS_API_KEY_2": "backup-key",
                "ELEVENLABS_VOICE_ID_2": "backup-voice",
            })

            config = AppConfig.from_settings(settings)

            self.assertEqual(
                config.elevenlabs_accounts,
                (("primary-key", "voice-2"), ("backup-key", "backup-voice")),
            )

    def test_elevenlabs_account_fallback_uses_second_account_on_quota_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = ElevenLabsVoiceProvider(
                model="eleven_multilingual_v2",
                accounts=(("primary-key", "primary-voice"), ("backup-key", "backup-voice")),
            )

            with patch("requests.post", side_effect=[
                _FakeElevenLabsResponse(429),
                _FakeElevenLabsResponse(200, b"backup-audio"),
            ]) as post:
                result = provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

            self.assertEqual(output.read_bytes(), b"backup-audio")
            self.assertEqual(dict(result.metadata or {}).get("voice_id"), "backup-voice")
            self.assertIn("primary-voice", post.call_args_list[0].args[0])
            self.assertIn("backup-voice", post.call_args_list[1].args[0])

    def test_elevenlabs_account_fallback_errors_after_all_accounts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = ElevenLabsVoiceProvider(
                model="eleven_multilingual_v2",
                accounts=(("primary-key", "primary-voice"), ("backup-key", "backup-voice")),
            )

            with patch("requests.post", side_effect=[
                _FakeElevenLabsResponse(402),
                _FakeElevenLabsResponse(429),
            ]):
                with self.assertRaises(ProviderExecutionError):
                    provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

    def test_voice_mix_rotates_provider_by_run_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_VOICE_MIX": "elevenlabs,edge_tts",
                "GITHUB_RUN_NUMBER": "5",
            })

            config = AppConfig.from_settings(settings)

            self.assertEqual(config.voice_rotation_provider, "edge_tts")
            self.assertEqual(config.provider_priority["voice"][0], "edge_tts")

    def test_voice_mix_can_force_the_first_provider_for_local_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_VOICE_MIX": "elevenlabs,edge_tts",
                "ELEVENLABS_VOICE_ROTATION_INDEX": "0",
            })

            config = AppConfig.from_settings(settings)

            self.assertEqual(config.voice_rotation_provider, "elevenlabs")
            self.assertEqual(config.provider_priority["voice"][0], "elevenlabs")

    def test_single_elevenlabs_voice_id_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={"ELEVENLABS_VOICE_ID": "voice-1"})

            config = AppConfig.from_settings(settings)

            self.assertEqual(config.elevenlabs_voice_ids, ("voice-1",))
            self.assertEqual(config.elevenlabs_voice_id, "voice-1")

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

    def test_voice_registry_orders_audiolab_after_elevenlabs_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_RENDER_PROFILE": "production",
                "ELEVENLABS_API_KEY": "test-key",
                "ELEVENLABS_VOICE_ID": "voice-1",
                "AUDIOLAB_API_KEY": "audiolab-key",
            })
            config = AppConfig.from_settings(settings)

            registry = build_voice_registry(config)

            names = registry.provider_names("voice", profile="production")
            self.assertEqual(names[:3], ("elevenlabs", "audiolab", "edge_tts"))

    def test_testing_voice_registry_never_registers_external_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_RENDER_PROFILE": "testing",
                "AUTO_VIDEO_VOICE_PROVIDER": "audiolab,mock",
                "AUDIOLAB_API_KEY": "must-not-be-used",
            })
            config = AppConfig.from_settings(settings)

            registry = build_voice_registry(config)

            self.assertEqual(("mock",), registry.provider_names("voice", profile="testing"))

    def test_audiolab_provider_writes_raw_audio_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = AudioLabVoiceProvider(api_key="audiolab-key", voice_id="auto", model="tts/auto")

            with patch("requests.post", return_value=_FakeElevenLabsResponse(200, b"ID3mp3-audio")) as post:
                result = provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

            self.assertEqual(output.read_bytes(), b"ID3mp3-audio")
            self.assertEqual(result.provider, "audiolab")
            self.assertEqual(result.metadata["voice_id"], "auto")
            self.assertEqual(post.call_args.args[0], "https://api.tryaudiolab.ai/v1/audio/speech")
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer audiolab-key")
            self.assertEqual(post.call_args.kwargs["json"]["model"], "tts/auto")
            self.assertEqual(post.call_args.kwargs["json"]["input"], "hello")

    def test_audiolab_rejects_non_audio_success_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = AudioLabVoiceProvider(api_key="audiolab-key")
            response = _FakeElevenLabsResponse(
                200,
                b'{"error":"not audio"}',
                {"Content-Type": "application/json"},
            )

            with patch("requests.post", return_value=response):
                with self.assertRaises(ProviderExecutionError):
                    provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

            self.assertFalse(output.exists())

    def test_audiolab_rejects_corrupt_mp3_without_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = AudioLabVoiceProvider(api_key="audiolab-key")

            with patch("requests.post", return_value=_FakeElevenLabsResponse(200, b"not-an-mp3")):
                with self.assertRaises(ProviderExecutionError):
                    provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

            self.assertFalse(output.exists())

    def test_audiolab_marks_quota_error_and_becomes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "voice.mp3"
            provider = AudioLabVoiceProvider(api_key="audiolab-key")

            with patch("requests.post", return_value=_FakeElevenLabsResponse(402)):
                with self.assertRaises(ProviderExecutionError):
                    provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))

            with self.assertRaises(ProviderUnavailableError):
                provider.synthesize(VoiceRequest(text="hello", output_path=output, scene_id="1"))


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
