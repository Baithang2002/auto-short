"""Mixkit music provider backed by a curated catalog of direct download URLs.

Mixkit publishes free stock music under the Mixkit License (free for
commercial use, no attribution) but exposes no search API. To keep the
platform fully automated without scraping, this provider ships a small
curated catalog of direct CDN download URLs tagged by mood. Entries that
disappear upstream simply fail the download and the planner falls through
to the next provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack

MIXKIT_LICENSE = MusicLicense(
    license="Mixkit Stock Music Free License",
    commercial_use=True,
    attribution_required=False,
    source_url="https://mixkit.co/license/#musicFree",
    verified=True,
)

HttpDownload = Callable[[str, Path, int], None]


def _default_http_download(url: str, out_path: Path, timeout_sec: int) -> None:
    import requests

    with requests.get(url, stream=True, timeout=timeout_sec) as response:
        response.raise_for_status()
        with open(out_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)


@dataclass(frozen=True)
class MixkitCatalogEntry:
    """One curated Mixkit track."""

    track_id: str
    title: str
    url: str
    duration_sec: float
    moods: tuple[str, ...]


# Curated instrumental tracks from https://mixkit.co/free-stock-music/,
# tagged with the mood vocabulary the script stage produces.
DEFAULT_MIXKIT_CATALOG: tuple[MixkitCatalogEntry, ...] = (
    MixkitCatalogEntry(
        "123", "Serene View",
        "https://assets.mixkit.co/music/download/mixkit-serene-view-443.mp3",
        120.0, ("warm", "curious"),
    ),
    MixkitCatalogEntry(
        "209", "Valley Sunset",
        "https://assets.mixkit.co/music/download/mixkit-valley-sunset-127.mp3",
        152.0, ("inspiring", "warm"),
    ),
    MixkitCatalogEntry(
        "633", "Spirit In The Woods",
        "https://assets.mixkit.co/music/download/mixkit-spirit-in-the-woods-139.mp3",
        141.0, ("mysterious", "curious"),
    ),
    MixkitCatalogEntry(
        "574", "Driving Ambition",
        "https://assets.mixkit.co/music/download/mixkit-driving-ambition-32.mp3",
        142.0, ("dramatic", "urgent", "inspiring"),
    ),
    MixkitCatalogEntry(
        "621", "Forest Treasure",
        "https://assets.mixkit.co/music/download/mixkit-forest-treasure-138.mp3",
        183.0, ("mysterious", "warm"),
    ),
    MixkitCatalogEntry(
        "866", "Trailer Tension",
        "https://assets.mixkit.co/music/download/mixkit-trailer-tension-664.mp3",
        94.0, ("urgent", "dramatic"),
    ),
)


class MixkitMusicProvider:
    """Serve tracks from a curated Mixkit catalog.

    Deterministic: the first eligible catalog entry (stable catalog order)
    matching the mood and duration constraints is chosen.
    """

    name = "mixkit"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.MOOD_SEARCH,
        MusicCapability.COMMERCIAL_USE,
        MusicCapability.ATTRIBUTION_FREE,
    )

    def __init__(
        self,
        *,
        cache_dir: Path,
        catalog: tuple[MixkitCatalogEntry, ...] = DEFAULT_MIXKIT_CATALOG,
        download_timeout_sec: int = 120,
        enabled: bool = True,
        http_download: HttpDownload = _default_http_download,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.catalog = catalog
        self.download_timeout_sec = download_timeout_sec
        self.enabled = enabled
        self._http_download = http_download

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        if not self.enabled or not self.catalog:
            raise ProviderUnavailableError(self.name, "mixkit catalog is disabled or empty")

        mood = (query.mood or "").lower()
        eligible = [entry for entry in self.catalog if self._matches(entry, mood, query)]
        if not eligible:
            # Mood miss: fall back to any duration-eligible entry so the chain
            # still produces safe music rather than skipping the provider.
            eligible = [entry for entry in self.catalog if self._matches(entry, "", query)]
        if not eligible:
            raise ProviderExecutionError(self.name, f"no eligible catalog track for mood {query.mood!r}")

        entry = eligible[0]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.cache_dir / f"{entry.track_id}_{entry.title.replace(' ', '-')}.mp3"
        if not (out_path.exists() and out_path.stat().st_size > 50_000):
            try:
                self._http_download(entry.url, out_path, self.download_timeout_sec)
            except Exception as e:
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                raise ProviderExecutionError(self.name, f"download failed: {e}") from e

        return MusicTrack(
            provider=self.name,
            provider_track_id=entry.track_id,
            title=entry.title,
            artist="Mixkit",
            duration_sec=entry.duration_sec,
            local_path=out_path,
            source_url=entry.url,
            license=MIXKIT_LICENSE,
            mood=query.mood,
        )

    @staticmethod
    def _matches(entry: MixkitCatalogEntry, mood: str, query: MusicQuery) -> bool:
        if mood and mood not in entry.moods:
            return False
        if entry.duration_sec < query.min_duration_sec:
            return False
        if query.max_duration_sec and entry.duration_sec > query.max_duration_sec:
            return False
        return True
