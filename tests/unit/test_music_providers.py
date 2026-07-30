from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401
from autovideo.config import AppConfig, Settings
from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.factory import build_music_registry
from autovideo.providers.music import (
    GeneratedMusicProvider,
    JamendoMusicProvider,
    MixkitCatalogEntry,
    MixkitMusicProvider,
    MusicQuery,
    PixabayMusicProvider,
    SilenceMusicProvider,
    YouTubeAudioLibraryProvider,
)


def _fake_download(payload: bytes = b"x" * 60_000):
    def download(url: str, out_path: Path, timeout_sec: int) -> None:
        Path(out_path).write_bytes(payload)

    return download


QUERY = MusicQuery(mood="inspiring", min_duration_sec=30.0, target_duration_sec=45.0)


class JamendoProviderTests(unittest.TestCase):
    def _provider(self, tmp: str, results: list[dict], calls: list[dict] | None = None) -> JamendoMusicProvider:
        def http_get(url, params, timeout_sec):
            if calls is not None:
                calls.append(dict(params))
            return {"results": results}

        return JamendoMusicProvider(
            "client-123",
            cache_dir=Path(tmp),
            http_get=http_get,
            http_download=_fake_download(),
        )

    def test_unconfigured_provider_raises_unavailable(self) -> None:
        provider = JamendoMusicProvider("", cache_dir=Path("."))
        with self.assertRaises(ProviderUnavailableError):
            provider.fetch_track(QUERY)

    def test_fetch_produces_structured_license_metadata(self) -> None:
        results = [{
            "id": 42,
            "name": "Skyward",
            "artist_name": "Nova",
            "duration": 120,
            "audiodownload": "https://jamendo.example/dl/42.mp3",
            "shareurl": "https://jamendo.example/t/42",
            "license_ccurl": "https://creativecommons.org/licenses/by-sa/3.0/",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            track = self._provider(tmp, results).fetch_track(QUERY)

        self.assertEqual(track.provider, "jamendo")
        self.assertEqual(track.provider_track_id, "42")
        self.assertEqual(track.title, "Skyward")
        self.assertEqual(track.artist, "Nova")
        self.assertEqual(track.duration_sec, 120.0)
        self.assertEqual(track.license.license, "CC-BY-SA-3.0")
        self.assertTrue(track.license.commercial_use)
        self.assertTrue(track.license.attribution_required)
        self.assertTrue(track.license.verified)
        payload = track.to_dict()
        for key in ("provider", "provider_track_id", "title", "artist", "duration",
                    "license", "commercial_use", "attribution_required", "verified", "source_url"):
            self.assertIn(key, payload)
        self.assertEqual(payload["platform"], "jamendo")
        self.assertEqual(
            payload["license_metadata"]["attribution_text"],
            "Skyward by Nova (Jamendo, CC-BY-SA-3.0)",
        )

    def test_noncommercial_license_is_marked_not_commercial(self) -> None:
        results = [{
            "id": 7,
            "name": "Quiet",
            "artist_name": "A",
            "duration": 90,
            "audiodownload": "https://jamendo.example/dl/7.mp3",
            "license_ccurl": "https://creativecommons.org/licenses/by-nc/3.0/",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            track = self._provider(tmp, results).fetch_track(QUERY)

        self.assertFalse(track.license.commercial_use)

    def test_short_or_unlicensed_tracks_are_skipped(self) -> None:
        results = [
            {"id": 1, "duration": 10, "audiodownload": "u", "license_ccurl": "c"},
            {"id": 2, "duration": 120, "audiodownload": "", "license_ccurl": "c"},
            {"id": 3, "duration": 120, "audiodownload": "u", "license_ccurl": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProviderExecutionError):
                self._provider(tmp, results).fetch_track(QUERY)

    def test_commercial_filter_is_sent_to_api(self) -> None:
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, [], calls)
            with self.assertRaises(ProviderExecutionError):
                provider.fetch_track(QUERY)
        self.assertTrue(all(call.get("ccnc") == "false" for call in calls))
        self.assertTrue(all(call.get("ccnd") == "false" for call in calls))

    def test_no_derivatives_tracks_are_skipped(self) -> None:
        results = [
            {
                "id": "nd",
                "name": "No Edit",
                "artist_name": "A",
                "duration": 120,
                "audiodownload": "https://jamendo.example/dl/nd.mp3",
                "license_ccurl": "https://creativecommons.org/licenses/by-nd/4.0/",
            },
            {
                "id": "safe",
                "name": "Safe Edit",
                "artist_name": "B",
                "duration": 120,
                "audiodownload": "https://jamendo.example/dl/safe.mp3",
                "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            track = self._provider(tmp, results).fetch_track(QUERY)

        self.assertEqual(track.provider_track_id, "safe")

    def test_selection_key_varies_eligible_track_without_key_changing_legacy_pick(self) -> None:
        results = [
            {
                "id": "1",
                "name": "First",
                "artist_name": "A",
                "duration": 120,
                "audiodownload": "https://jamendo.example/dl/1.mp3",
                "license_ccurl": "https://creativecommons.org/licenses/by/3.0/",
            },
            {
                "id": "2",
                "name": "Second",
                "artist_name": "B",
                "duration": 120,
                "audiodownload": "https://jamendo.example/dl/2.mp3",
                "license_ccurl": "https://creativecommons.org/licenses/by/3.0/",
            },
            {
                "id": "3",
                "name": "Third",
                "artist_name": "C",
                "duration": 120,
                "audiodownload": "https://jamendo.example/dl/3.mp3",
                "license_ccurl": "https://creativecommons.org/licenses/by/3.0/",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, results)
            legacy = provider.fetch_track(QUERY)
            varied = {
                provider.fetch_track(MusicQuery(
                    mood="inspiring",
                    min_duration_sec=30.0,
                    target_duration_sec=45.0,
                    selection_key=f"topic-{idx}",
                )).provider_track_id
                for idx in range(8)
            }

        self.assertEqual(legacy.provider_track_id, "1")
        self.assertGreater(len(varied), 1)


class YouTubeAudioLibraryProviderTests(unittest.TestCase):
    def _write_library(self, root: Path, tracks: list[dict]) -> tuple[Path, Path]:
        assets = root / "private_audio"
        assets.mkdir()
        for track in tracks:
            file_name = track.get("file", "")
            if file_name and ".." not in file_name:
                (assets / file_name).write_bytes(b"local audio")
        manifest = root / "youtube_audio_library.json"
        manifest.write_text(json.dumps({"tracks": tracks}), encoding="utf-8")
        return manifest, assets

    def test_local_manifest_track_preserves_structured_provenance(self) -> None:
        tracks = [{
            "id": "ytal-42",
            "title": "Quiet Discovery",
            "artist": "Example Artist",
            "file": "quiet-discovery.mp3",
            "duration_sec": 92,
            "moods": ["inspiring", "warm"],
            "license": {
                "name": "YouTube Audio Library License",
                "commercial_use": True,
                "attribution_required": True,
                "attribution_text": "Quiet Discovery by Example Artist",
                "source_url": "https://support.google.com/youtube/answer/3376882",
                "verified": True,
                "derivatives_allowed": True,
            },
            "platform": {
                "name": "youtube",
                "track_id": "ytal-42",
                "source_url": "https://studio.youtube.com/channel/example/music",
            },
        }]
        with tempfile.TemporaryDirectory() as tmp:
            manifest, assets = self._write_library(Path(tmp), tracks)
            track = YouTubeAudioLibraryProvider(manifest, assets).fetch_track(QUERY)

        self.assertEqual(track.provider, "youtube_audio_library")
        self.assertEqual(track.platform, "youtube")
        self.assertEqual(track.provider_track_id, "ytal-42")
        self.assertTrue(track.license.attribution_required)
        self.assertEqual(track.license.attribution_text, "Quiet Discovery by Example Artist")
        self.assertEqual(track.metadata["platform"]["library"], "YouTube Audio Library")
        payload = track.to_dict()
        self.assertEqual(payload["license_metadata"]["derivatives_allowed"], True)
        self.assertEqual(payload["attribution"]["text"], "Quiet Discovery by Example Artist")
        self.assertEqual(payload["platform_metadata"]["library"], "YouTube Audio Library")

    def test_provider_is_unavailable_without_local_configuration(self) -> None:
        with self.assertRaises(ProviderUnavailableError):
            YouTubeAudioLibraryProvider(None, None).fetch_track(QUERY)

    def test_manifest_cannot_escape_private_asset_directory(self) -> None:
        tracks = [{
            "id": "outside",
            "title": "Outside",
            "file": "../outside.mp3",
            "duration_sec": 90,
            "license": {
                "name": "YouTube Audio Library License",
                "commercial_use": True,
                "verified": True,
                "derivatives_allowed": True,
            },
        }]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outside.mp3").write_bytes(b"outside")
            manifest, assets = self._write_library(root, tracks)
            with self.assertRaises(ProviderExecutionError):
                YouTubeAudioLibraryProvider(manifest, assets).fetch_track(QUERY)

class PixabayProviderTests(unittest.TestCase):
    def test_fetch_returns_verified_attribution_free_license(self) -> None:
        hits = [{
            "id": 9,
            "duration": 100,
            "tags": "uplifting, corporate",
            "user": "creator",
            "pageURL": "https://pixabay.example/9",
            "audio": {"url": "https://pixabay.example/dl/9.mp3"},
        }]

        def http_get(url, params, timeout_sec):
            return {"hits": hits}

        with tempfile.TemporaryDirectory() as tmp:
            provider = PixabayMusicProvider(
                "key-1", cache_dir=Path(tmp), http_get=http_get, http_download=_fake_download()
            )
            track = provider.fetch_track(QUERY)
            self.assertTrue(track.local_path and track.local_path.exists())

        self.assertEqual(track.provider, "pixabay")
        self.assertTrue(track.license.commercial_use)
        self.assertFalse(track.license.attribution_required)
        self.assertTrue(track.license.verified)

    def test_placeholder_key_is_unavailable(self) -> None:
        provider = PixabayMusicProvider("your_pixabay_key", cache_dir=Path("."))
        with self.assertRaises(ProviderUnavailableError):
            provider.fetch_track(QUERY)

    def test_selection_key_varies_pixabay_track_without_key_changing_legacy_pick(self) -> None:
        hits = [
            {
                "id": 1,
                "duration": 100,
                "tags": "uplifting one",
                "audio": {"url": "https://pixabay.example/dl/1.mp3"},
            },
            {
                "id": 2,
                "duration": 100,
                "tags": "uplifting two",
                "audio": {"url": "https://pixabay.example/dl/2.mp3"},
            },
            {
                "id": 3,
                "duration": 100,
                "tags": "uplifting three",
                "audio": {"url": "https://pixabay.example/dl/3.mp3"},
            },
        ]

        def http_get(url, params, timeout_sec):
            return {"hits": hits}

        with tempfile.TemporaryDirectory() as tmp:
            provider = PixabayMusicProvider(
                "key-1", cache_dir=Path(tmp), http_get=http_get, http_download=_fake_download()
            )
            legacy = provider.fetch_track(QUERY)
            varied = {
                provider.fetch_track(MusicQuery(
                    mood="inspiring",
                    min_duration_sec=30.0,
                    target_duration_sec=45.0,
                    selection_key=f"topic-{idx}",
                )).provider_track_id
                for idx in range(8)
            }

        self.assertEqual(legacy.provider_track_id, "1")
        self.assertGreater(len(varied), 1)


class MixkitProviderTests(unittest.TestCase):
    def test_deterministic_catalog_pick_by_mood_and_duration(self) -> None:
        catalog = (
            MixkitCatalogEntry("1", "Too Short", "https://mixkit.example/1.mp3", 10.0, ("inspiring",)),
            MixkitCatalogEntry("2", "Pick Me", "https://mixkit.example/2.mp3", 120.0, ("inspiring",)),
            MixkitCatalogEntry("3", "Also Fine", "https://mixkit.example/3.mp3", 130.0, ("inspiring",)),
        )
        with tempfile.TemporaryDirectory() as tmp:
            provider = MixkitMusicProvider(
                cache_dir=Path(tmp), catalog=catalog, http_download=_fake_download()
            )
            first = provider.fetch_track(QUERY)
            second = provider.fetch_track(QUERY)

        self.assertEqual(first.provider_track_id, "2")
        self.assertEqual(second.provider_track_id, "2")
        self.assertTrue(first.license.verified)
        self.assertFalse(first.license.attribution_required)

    def test_mood_miss_falls_back_to_any_eligible_entry(self) -> None:
        catalog = (
            MixkitCatalogEntry("5", "Other Mood", "https://mixkit.example/5.mp3", 120.0, ("warm",)),
        )
        with tempfile.TemporaryDirectory() as tmp:
            provider = MixkitMusicProvider(
                cache_dir=Path(tmp), catalog=catalog, http_download=_fake_download()
            )
            track = provider.fetch_track(QUERY)

        self.assertEqual(track.provider_track_id, "5")


class GeneratedAndSilenceProviderTests(unittest.TestCase):
    def test_generated_provider_uses_injected_synthesizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bed = Path(tmp) / "bed.m4a"

            def synth(duration_sec: float, mood: str) -> Path:
                bed.write_bytes(b"audio")
                return bed

            track = GeneratedMusicProvider(synth).fetch_track(QUERY)

        self.assertEqual(track.provider, "generated")
        self.assertEqual(track.duration_sec, 45.0)
        self.assertTrue(track.license.commercial_use)
        self.assertTrue(track.license.verified)

    def test_generated_provider_without_synthesizer_is_unavailable(self) -> None:
        with self.assertRaises(ProviderUnavailableError):
            GeneratedMusicProvider(None).fetch_track(QUERY)

    def test_silence_provider_always_succeeds(self) -> None:
        track = SilenceMusicProvider().fetch_track(QUERY)

        self.assertTrue(track.is_silence)
        self.assertIsNone(track.local_path)
        self.assertTrue(track.license.verified)


class MusicRegistryTests(unittest.TestCase):
    def _config(self, env: dict) -> AppConfig:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings.from_project_root(self._tmp.name, env=env)
        return AppConfig.from_settings(settings)

    def test_registration_follows_configured_order_and_enablement(self) -> None:
        config = self._config({
            "JAMENDO_CLIENT_ID": "client-1",
            "PIXABAY_API_KEY": "key-1",
        })
        registry = build_music_registry(config, generated_synthesizer=lambda d, m: Path("bed.m4a"))

        self.assertEqual(
            registry.provider_names("music"),
            ("jamendo", "pixabay", "mixkit", "generated", "silence"),
        )

    def test_providers_without_credentials_are_disabled(self) -> None:
        config = self._config({})
        registry = build_music_registry(config, generated_synthesizer=lambda d, m: Path("bed.m4a"))

        names = registry.provider_names("music")
        self.assertNotIn("jamendo", names)
        self.assertNotIn("pixabay", names)
        self.assertNotIn("youtube_audio_library", names)
        self.assertIn("mixkit", names)
        self.assertIn("generated", names)
        self.assertIn("silence", names)

    def test_generated_music_can_be_disabled_by_env(self) -> None:
        config = self._config({"AUTO_VIDEO_ENABLE_GENERATED_MUSIC": "false"})
        registry = build_music_registry(config, generated_synthesizer=lambda d, m: Path("bed.m4a"))

        self.assertNotIn("generated", registry.provider_names("music"))
        self.assertIn("silence", registry.provider_names("music"))

    def test_silence_is_always_registered_last(self) -> None:
        config = self._config({"AUTO_VIDEO_MUSIC_PROVIDER_ORDER": "mixkit,silence"})
        registry = build_music_registry(config, generated_synthesizer=None)

        names = registry.provider_names("music")
        self.assertEqual(names[-1], "silence")

    def test_configured_youtube_audio_library_is_registered_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "private_audio"
            assets.mkdir()
            (assets / "track.mp3").write_bytes(b"audio")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
            settings = Settings.from_project_root(root, env={
                "AUTO_VIDEO_YOUTUBE_AUDIO_LIBRARY_DIR": str(assets),
                "AUTO_VIDEO_YOUTUBE_AUDIO_LIBRARY_MANIFEST": str(manifest),
            })
            config = AppConfig.from_settings(settings)
            registry = build_music_registry(
                config,
                generated_synthesizer=None,
                include_real_providers=False,
            )

        self.assertEqual(registry.provider_names("music")[0], "youtube_audio_library")


if __name__ == "__main__":
    unittest.main()
