from __future__ import annotations

import unittest
from pathlib import Path

from tests.unit import _path  # noqa: F401
from autovideo.config import MusicConfig, ProviderRegistry
from autovideo.music import LicensePolicy, MusicPlanner, validate_license
from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music import MusicLicense, MusicQuery, MusicTrack
from autovideo.providers.music.silence import SilenceMusicProvider

SAFE_LICENSE = MusicLicense(
    license="Pixabay Content License",
    commercial_use=True,
    attribution_required=False,
    verified=True,
)


def _track(provider: str, *, license_info: MusicLicense = SAFE_LICENSE) -> MusicTrack:
    return MusicTrack(
        provider=provider,
        provider_track_id=f"{provider}-1",
        title=f"{provider} track",
        duration_sec=90.0,
        local_path=Path(f"/tmp/{provider}.mp3"),
        license=license_info,
    )


class StubProvider:
    def __init__(self, name: str, *, track: MusicTrack | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.capabilities = ()
        self._track = track
        self._error = error
        self.calls = 0

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._track is not None
        return self._track


def _registry(*providers: StubProvider, with_silence: bool = False) -> ProviderRegistry:
    registry = ProviderRegistry()
    for index, provider in enumerate(providers):
        registry.register("music", provider.name, provider, priority=index)
    if with_silence:
        registry.register("music", "silence", SilenceMusicProvider(), priority=99)
    return registry


class MusicPlannerFallbackTests(unittest.TestCase):
    def test_first_healthy_provider_wins(self) -> None:
        primary = StubProvider("primary", track=_track("primary"))
        secondary = StubProvider("secondary", track=_track("secondary"))
        planner = MusicPlanner(_registry(primary, secondary), MusicConfig())

        result = planner.select("inspiring", 45.0)

        self.assertEqual(result.track.provider, "primary")
        self.assertTrue(result.license_validated)
        self.assertEqual(secondary.calls, 0)

    def test_failed_provider_falls_through_to_next(self) -> None:
        broken = StubProvider("broken", error=ProviderExecutionError("broken", "boom"))
        backup = StubProvider("backup", track=_track("backup"))
        planner = MusicPlanner(_registry(broken, backup), MusicConfig(retries=1))

        result = planner.select("inspiring", 45.0)

        self.assertEqual(result.track.provider, "backup")
        self.assertEqual(broken.calls, 2)  # retries + 1
        outcomes = [(a.provider, a.outcome) for a in result.attempts]
        self.assertIn(("broken", "provider_error"), outcomes)
        self.assertIn(("backup", "selected"), outcomes)

    def test_unavailable_provider_is_not_retried(self) -> None:
        missing = StubProvider("missing", error=ProviderUnavailableError("missing", "no key"))
        backup = StubProvider("backup", track=_track("backup"))
        planner = MusicPlanner(_registry(missing, backup), MusicConfig(retries=3))

        result = planner.select("inspiring", 45.0)

        self.assertEqual(missing.calls, 1)
        self.assertEqual(result.track.provider, "backup")

    def test_all_providers_failing_degrades_to_silence(self) -> None:
        broken = StubProvider("broken", error=ProviderExecutionError("broken", "boom"))
        planner = MusicPlanner(_registry(broken), MusicConfig(retries=0))

        result = planner.select("inspiring", 45.0)

        self.assertTrue(result.is_silence)
        self.assertTrue(result.license_validated)
        self.assertEqual(result.attempts[-1].provider, "silence")

    def test_unexpected_provider_bug_does_not_break_the_chain(self) -> None:
        buggy = StubProvider("buggy", error=KeyError("oops"))
        backup = StubProvider("backup", track=_track("backup"))
        planner = MusicPlanner(_registry(buggy, backup), MusicConfig(retries=2))

        result = planner.select("inspiring", 45.0)

        self.assertEqual(result.track.provider, "backup")
        self.assertEqual(buggy.calls, 1)

    def test_generated_fallback_is_used_when_remote_providers_fail(self) -> None:
        remote = StubProvider("remote", error=ProviderExecutionError("remote", "down"))
        generated = StubProvider(
            "generated",
            track=_track("generated", license_info=MusicLicense(
                license="Generated", commercial_use=True, attribution_required=False, verified=True,
            )),
        )
        planner = MusicPlanner(_registry(remote, generated, with_silence=True), MusicConfig(retries=0))

        result = planner.select("dramatic", 50.0)

        self.assertEqual(result.track.provider, "generated")
        self.assertFalse(result.is_silence)


class MusicPlannerLicenseTests(unittest.TestCase):
    def test_track_failing_license_policy_triggers_next_provider(self) -> None:
        noncommercial = MusicLicense(
            license="CC-BY-NC-3.0", commercial_use=False, attribution_required=True, verified=True,
        )
        risky = StubProvider("risky", track=_track("risky", license_info=noncommercial))
        safe = StubProvider("safe", track=_track("safe"))
        planner = MusicPlanner(_registry(risky, safe), MusicConfig())

        result = planner.select("warm", 45.0)

        self.assertEqual(result.track.provider, "safe")
        outcomes = [(a.provider, a.outcome) for a in result.attempts]
        self.assertIn(("risky", "license_rejected"), outcomes)

    def test_attribution_disallowed_rejects_attribution_tracks(self) -> None:
        cc_by = MusicLicense(
            license="CC-BY-3.0", commercial_use=True, attribution_required=True, verified=True,
        )
        attributed = StubProvider("attributed", track=_track("attributed", license_info=cc_by))
        planner = MusicPlanner(
            _registry(attributed),
            MusicConfig(allow_attribution=False),
        )

        result = planner.select("warm", 45.0)

        self.assertTrue(result.is_silence)

    def test_unverified_license_is_rejected(self) -> None:
        unverified = MusicLicense(license="", commercial_use=True, verified=False)
        shady = StubProvider("shady", track=_track("shady", license_info=unverified))
        planner = MusicPlanner(_registry(shady), MusicConfig())

        result = planner.select("warm", 45.0)

        self.assertTrue(result.is_silence)

    def test_validate_license_reports_reasons(self) -> None:
        track = _track("x", license_info=MusicLicense(
            license="CC-BY-NC-3.0", commercial_use=False, attribution_required=True, verified=True,
        ))
        outcome = validate_license(track, LicensePolicy(require_commercial_use=True, allow_attribution=False))

        self.assertFalse(outcome.ok)
        self.assertEqual(len(outcome.reasons), 2)


class MusicPlannerQueryTests(unittest.TestCase):
    def test_query_derives_min_duration_from_target(self) -> None:
        planner = MusicPlanner(_registry(), MusicConfig(min_duration_sec=20))

        query = planner.build_query("curious", 50.0)

        self.assertEqual(query.min_duration_sec, 35.0)  # 70% of target beats the floor
        self.assertEqual(query.target_duration_sec, 50.0)

    def test_query_respects_min_duration_floor(self) -> None:
        planner = MusicPlanner(_registry(), MusicConfig(min_duration_sec=20))

        query = planner.build_query("curious", 10.0)

        self.assertEqual(query.min_duration_sec, 20.0)

    def test_query_carries_optional_selection_key(self) -> None:
        planner = MusicPlanner(_registry(), MusicConfig(min_duration_sec=20))

        query = planner.build_query("curious", 50.0, selection_key="Title|Topic")

        self.assertEqual(query.selection_key, "Title|Topic")

    def test_selection_result_serializes_for_diagnostics(self) -> None:
        provider = StubProvider("primary", track=_track("primary"))
        planner = MusicPlanner(_registry(provider), MusicConfig())

        payload = planner.select("inspiring", 45.0).to_dict()

        self.assertEqual(payload["track"]["provider"], "primary")
        self.assertTrue(payload["license_validated"])
        self.assertIn("attempts", payload)
        self.assertIn("policy", payload["diagnostics"])


if __name__ == "__main__":
    unittest.main()
