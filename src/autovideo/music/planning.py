"""Provider-agnostic music planning with license validation and fallback.

Mirrors the media selection boundary (``autovideo.media``): the planner walks
the configured provider chain (registry order — no provider names are
hard-coded here), validates every candidate's license, and returns a
:class:`MusicSelectionResult` that the mixing stage consumes. If every
provider fails, the result degrades to silence so rendering never stops.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from autovideo.config.music import MusicConfig
from autovideo.config.provider_registry import ProviderRegistry
from autovideo.music.licensing import LicensePolicy, validate_license
from autovideo.providers.base import ProviderError, ProviderUnavailableError
from autovideo.providers.music.base import MusicQuery, MusicTrack
from autovideo.providers.music.silence import SilenceMusicProvider

logger = logging.getLogger(__name__)

MUSIC_CAPABILITY = "music"


@dataclass(frozen=True)
class MusicProviderAttempt:
    """Diagnostic record for one provider attempt."""

    provider: str
    outcome: str  # "selected", "provider_error", "license_rejected"
    detail: str = ""


@dataclass(frozen=True)
class MusicSelectionResult:
    """Outcome of music planning for one video."""

    track: MusicTrack
    license_validated: bool
    attempts: tuple[MusicProviderAttempt, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_silence(self) -> bool:
        return self.track.is_silence

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track.to_dict(),
            "license_validated": self.license_validated,
            "attempts": [
                {"provider": a.provider, "outcome": a.outcome, "detail": a.detail} for a in self.attempts
            ],
            "diagnostics": dict(self.diagnostics),
        }


class MusicPlanner:
    """Select one license-validated music track via the provider registry.

    The provider order comes entirely from the registry (which is built from
    configuration); the planner never references a provider by name. Each
    provider gets ``config.retries + 1`` attempts before the chain moves on.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        config: MusicConfig,
        *,
        policy: LicensePolicy | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.policy = policy or LicensePolicy(
            require_commercial_use=config.require_commercial_license,
            allow_attribution=config.allow_attribution,
        )

    def build_query(
        self,
        mood: str,
        target_duration_sec: float,
        *,
        selection_key: str = "",
    ) -> MusicQuery:
        min_duration = max(self.config.min_duration_sec, int(target_duration_sec * 0.7))
        return MusicQuery(
            mood=mood or "",
            min_duration_sec=float(min_duration),
            max_duration_sec=float(self.config.max_duration_sec),
            target_duration_sec=float(target_duration_sec),
            selection_key=selection_key,
        )

    def select(
        self,
        mood: str,
        target_duration_sec: float,
        *,
        selection_key: str = "",
    ) -> MusicSelectionResult:
        """Walk the provider chain and return a validated selection.

        Never raises for expected provider failures; the terminal outcome is
        silence when every configured provider fails or is rejected.
        """
        query = self.build_query(mood, target_duration_sec, selection_key=selection_key)
        attempts: list[MusicProviderAttempt] = []

        for registered in self.registry.providers(MUSIC_CAPABILITY):
            provider = registered.provider
            track = self._try_provider(registered.name, provider, query, attempts)
            if track is None:
                continue
            validation = validate_license(track, self.policy)
            if not validation.ok:
                attempts.append(
                    MusicProviderAttempt(registered.name, "license_rejected", validation.summary)
                )
                logger.warning(
                    "music provider=%s track=%s rejected: %s",
                    registered.name,
                    track.provider_track_id,
                    validation.summary,
                )
                continue
            attempts.append(MusicProviderAttempt(registered.name, "selected"))
            logger.info(
                "music selected provider=%s track=%s license=%s",
                registered.name,
                track.provider_track_id,
                track.license.license,
            )
            return MusicSelectionResult(
                track=track,
                license_validated=True,
                attempts=tuple(attempts),
                diagnostics=self._diagnostics(query),
            )

        # Terminal degradation: silence. Rendering must never stop because
        # every music provider failed.
        logger.warning("music: all providers failed; falling back to silence")
        silence = SilenceMusicProvider().fetch_track(query)
        attempts.append(MusicProviderAttempt("silence", "selected", "terminal fallback"))
        return MusicSelectionResult(
            track=silence,
            license_validated=True,
            attempts=tuple(attempts),
            diagnostics=self._diagnostics(query),
        )

    def _try_provider(
        self,
        name: str,
        provider: object,
        query: MusicQuery,
        attempts: list[MusicProviderAttempt],
    ) -> MusicTrack | None:
        fetch = getattr(provider, "fetch_track", None)
        if fetch is None:
            attempts.append(MusicProviderAttempt(name, "provider_error", "does not implement fetch_track"))
            return None
        max_tries = max(1, self.config.retries + 1)
        for attempt_index in range(max_tries):
            try:
                return fetch(query)
            except ProviderError as e:
                attempts.append(MusicProviderAttempt(name, "provider_error", str(e)))
                if isinstance(e, ProviderUnavailableError) or not e.retryable:
                    return None
            except Exception as e:  # unexpected bug in a provider: isolate, keep the chain alive
                attempts.append(MusicProviderAttempt(name, "provider_error", f"unexpected: {e}"))
                logger.warning("music provider=%s unexpected error: %s", name, e)
                return None
        return None

    def _diagnostics(self, query: MusicQuery) -> dict[str, Any]:
        return {
            "provider_order": list(self.registry.provider_names(MUSIC_CAPABILITY)),
            "configured_order": list(self.config.provider_order),
            "mood": query.mood,
            "min_duration_sec": query.min_duration_sec,
            "max_duration_sec": query.max_duration_sec,
            "target_duration_sec": query.target_duration_sec,
            "selection_key_present": bool(query.selection_key),
            "policy": {
                "require_commercial_use": self.policy.require_commercial_use,
                "allow_attribution": self.policy.allow_attribution,
                "require_verified": self.policy.require_verified,
            },
        }
