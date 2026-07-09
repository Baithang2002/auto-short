"""Generated (synthesized) music provider."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack

GENERATED_LICENSE = MusicLicense(
    license="Generated (synthesized in-pipeline)",
    commercial_use=True,
    attribution_required=False,
    verified=True,
)

Synthesizer = Callable[[float, str], Path]
"""Callable that renders ``duration_sec`` seconds of audio for a mood and returns the file path."""


class GeneratedMusicProvider:
    """Synthesize an ambient chord pad when no real track is available.

    The synthesizer callable is injected (the legacy FFmpeg chord-pad
    generator in production, a stub in tests) so this module stays free of
    rendering logic and network access.
    """

    name = "generated"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.GENERATED,
        MusicCapability.OFFLINE,
        MusicCapability.COMMERCIAL_USE,
        MusicCapability.ATTRIBUTION_FREE,
        MusicCapability.CONTENT_ID_SAFE,
    )

    def __init__(self, synthesizer: Synthesizer | None, *, enabled: bool = True) -> None:
        self._synthesizer = synthesizer
        self.enabled = enabled and synthesizer is not None

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        if not self.enabled or self._synthesizer is None:
            raise ProviderUnavailableError(self.name, "generated music is disabled")
        duration = query.target_duration_sec or query.min_duration_sec
        try:
            path = self._synthesizer(float(duration), query.mood)
        except Exception as e:
            raise ProviderExecutionError(self.name, f"synthesis failed: {e}") from e
        if path is None or not Path(path).exists():
            raise ProviderExecutionError(self.name, "synthesizer produced no output file")
        return MusicTrack(
            provider=self.name,
            provider_track_id=f"generated-{(query.mood or 'mysterious').lower()}",
            title=f"Generated {query.mood or 'mysterious'} ambient bed",
            artist="autovideo",
            duration_sec=float(duration),
            local_path=Path(path),
            license=GENERATED_LICENSE,
            mood=query.mood,
        )
