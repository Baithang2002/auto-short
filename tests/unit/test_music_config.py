from __future__ import annotations

import tempfile
import unittest
import warnings

from tests.unit import _path  # noqa: F401
from autovideo.config import (
    DEFAULT_MUSIC_PROVIDER_ORDER,
    AppConfig,
    MusicConfig,
    MusicConfigError,
    Settings,
    music_config_from_env,
    parse_provider_order,
)


class MusicConfigDefaultsTests(unittest.TestCase):
    def test_missing_variables_receive_safe_defaults(self) -> None:
        config = music_config_from_env({})

        self.assertEqual(config.provider_order, DEFAULT_MUSIC_PROVIDER_ORDER)
        self.assertEqual(config.retries, 2)
        self.assertEqual(config.timeout_sec, 20)
        self.assertAlmostEqual(config.volume, 0.22)
        self.assertEqual(config.fade_in_ms, 1500)
        self.assertEqual(config.fade_out_ms, 0)
        self.assertEqual(config.min_duration_sec, 20)
        self.assertEqual(config.max_duration_sec, 0)
        self.assertTrue(config.enable_generated)
        self.assertTrue(config.require_commercial_license)
        self.assertTrue(config.allow_attribution)

    def test_loading_is_deterministic(self) -> None:
        env = {"AUTO_VIDEO_MUSIC_RETRIES": "4", "AUTO_VIDEO_MUSIC_PROVIDER_ORDER": "pixabay,generated"}

        first = music_config_from_env(env)
        second = music_config_from_env(env)

        self.assertEqual(first, second)


class MusicConfigEnvOverrideTests(unittest.TestCase):
    def test_environment_overrides_every_field(self) -> None:
        config = music_config_from_env({
            "AUTO_VIDEO_MUSIC_PROVIDER_ORDER": "pixabay,mixkit,silence",
            "AUTO_VIDEO_MUSIC_RETRIES": "0",
            "AUTO_VIDEO_MUSIC_TIMEOUT": "5",
            "AUTO_VIDEO_MUSIC_VOLUME": "0.5",
            "AUTO_VIDEO_MUSIC_FADE_IN_MS": "750",
            "AUTO_VIDEO_MUSIC_FADE_OUT_MS": "1200",
            "AUTO_VIDEO_MUSIC_MIN_DURATION_SEC": "30",
            "AUTO_VIDEO_MUSIC_MAX_DURATION_SEC": "300",
            "AUTO_VIDEO_ENABLE_GENERATED_MUSIC": "false",
            "AUTO_VIDEO_REQUIRE_COMMERCIAL_LICENSE": "false",
            "AUTO_VIDEO_ALLOW_ATTRIBUTION": "false",
        })

        self.assertEqual(config.provider_order, ("pixabay", "mixkit", "silence"))
        self.assertEqual(config.retries, 0)
        self.assertEqual(config.timeout_sec, 5)
        self.assertAlmostEqual(config.volume, 0.5)
        self.assertEqual(config.fade_in_ms, 750)
        self.assertEqual(config.fade_out_ms, 1200)
        self.assertEqual(config.min_duration_sec, 30)
        self.assertEqual(config.max_duration_sec, 300)
        self.assertFalse(config.enable_generated)
        self.assertFalse(config.require_commercial_license)
        self.assertFalse(config.allow_attribution)

    def test_profile_order_used_when_env_missing(self) -> None:
        config = music_config_from_env({}, profile_order=("generated", "silence"))

        self.assertEqual(config.provider_order, ("generated", "silence"))

    def test_app_config_exposes_music_config_and_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings.from_project_root(tmp, env={
                "AUTO_VIDEO_MUSIC_PROVIDER_ORDER": "mixkit,generated,silence",
            })
            config = AppConfig.from_settings(settings)

            self.assertEqual(config.music.provider_order, ("mixkit", "generated", "silence"))
            self.assertEqual(config.provider_priority["music"], ("mixkit", "generated", "silence"))


class MusicConfigValidationTests(unittest.TestCase):
    def test_unknown_provider_name_fails_with_clear_error(self) -> None:
        with self.assertRaises(MusicConfigError) as ctx:
            music_config_from_env({"AUTO_VIDEO_MUSIC_PROVIDER_ORDER": "jamendo,spotify"})
        self.assertIn("spotify", str(ctx.exception))
        self.assertIn("AUTO_VIDEO_MUSIC_PROVIDER_ORDER", str(ctx.exception))

    def test_non_numeric_timeout_fails_with_clear_error(self) -> None:
        with self.assertRaises(MusicConfigError) as ctx:
            music_config_from_env({"AUTO_VIDEO_MUSIC_TIMEOUT": "soon"})
        self.assertIn("AUTO_VIDEO_MUSIC_TIMEOUT", str(ctx.exception))

    def test_out_of_range_volume_fails(self) -> None:
        with self.assertRaises(MusicConfigError):
            music_config_from_env({"AUTO_VIDEO_MUSIC_VOLUME": "2.5"})

    def test_invalid_boolean_fails(self) -> None:
        with self.assertRaises(MusicConfigError):
            music_config_from_env({"AUTO_VIDEO_ENABLE_GENERATED_MUSIC": "maybe"})

    def test_max_duration_below_min_duration_fails(self) -> None:
        with self.assertRaises(MusicConfigError):
            music_config_from_env({
                "AUTO_VIDEO_MUSIC_MIN_DURATION_SEC": "60",
                "AUTO_VIDEO_MUSIC_MAX_DURATION_SEC": "30",
            })

    def test_direct_construction_is_validated(self) -> None:
        with self.assertRaises(MusicConfigError):
            MusicConfig(retries=-1)

    def test_deprecated_local_provider_is_skipped_with_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            order = parse_provider_order("local,pixabay,generated")

        self.assertEqual(order, ("pixabay", "generated"))
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))


if __name__ == "__main__":
    unittest.main()
