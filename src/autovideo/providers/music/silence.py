"""Silence provider — the emergency fallback that always succeeds."""

from __future__ import annotations

from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack

SILENCE_LICENSE = MusicLicense(
    license="None (no music)",
    commercial_use=True,
    attribution_required=False,
    verified=True,
)


class SilenceMusicProvider:
    """Terminal fallback: render the video without background music.

    Returns a :class:`MusicTrack` whose ``local_path`` is None, which the
    mixing stage interprets as "skip the music mix". This guarantees that
    rendering never stops because every music provider failed.
    """

    name = "silence"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.SILENCE,
        MusicCapability.OFFLINE,
        MusicCapability.COMMERCIAL_USE,
        MusicCapability.ATTRIBUTION_FREE,
        MusicCapability.CONTENT_ID_SAFE,
    )
    enabled = True

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        return MusicTrack(
            provider=self.name,
            provider_track_id="silence",
            title="No background music",
            duration_sec=None,
            local_path=None,
            license=SILENCE_LICENSE,
            mood=query.mood,
        )
