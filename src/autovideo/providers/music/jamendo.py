"""Jamendo music provider (Creative Commons catalog)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack

JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"

# Mood vocabulary produced by the script stage -> Jamendo tag vocabulary.
JAMENDO_MOOD_TAGS: dict[str, tuple[str, ...]] = {
    "mysterious": ("ambient", "dark", "cinematic"),
    "inspiring": ("uplifting", "inspirational", "cinematic"),
    "dramatic": ("epic", "cinematic", "tension"),
    "warm": ("acoustic", "soft", "relaxing"),
    "curious": ("ambient", "soundscape", "dreamy"),
    "urgent": ("epic", "intense", "drum"),
}
_DEFAULT_TAGS: tuple[str, ...] = ("ambient", "cinematic")

HttpGet = Callable[[str, Mapping[str, Any], int], Any]
HttpDownload = Callable[[str, Path, int], None]


def _default_http_get(url: str, params: Mapping[str, Any], timeout_sec: int) -> Any:
    import requests

    response = requests.get(url, params=dict(params), timeout=timeout_sec)
    response.raise_for_status()
    return response.json()


def _default_http_download(url: str, out_path: Path, timeout_sec: int) -> None:
    import requests

    with requests.get(url, stream=True, timeout=timeout_sec) as response:
        response.raise_for_status()
        with open(out_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)


def _license_from_ccurl(ccurl: str) -> MusicLicense:
    """Derive structured license metadata from a Jamendo CC license URL."""
    lowered = ccurl.lower()
    commercial = bool(ccurl) and "-nc" not in lowered
    name = "CC"
    match = re.search(r"licenses/([a-z\-]+)/([0-9.]+)", lowered)
    if match:
        name = f"CC-{match.group(1).upper()}-{match.group(2)}"
    return MusicLicense(
        license=name,
        commercial_use=commercial,
        attribution_required=True,  # every non-CC0 Creative Commons license requires attribution
        source_url=ccurl,
        verified=bool(ccurl),
    )


class JamendoMusicProvider:
    """Fetch instrumental Creative Commons tracks from the Jamendo API.

    Deterministic: candidates are filtered, then the most popular eligible
    track is chosen (stable given identical API responses).
    """

    name = "jamendo"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.MOOD_SEARCH,
        MusicCapability.COMMERCIAL_USE,
    )

    def __init__(
        self,
        client_id: str,
        *,
        cache_dir: Path,
        timeout_sec: int = 20,
        download_timeout_sec: int = 120,
        require_commercial: bool = True,
        http_get: HttpGet = _default_http_get,
        http_download: HttpDownload = _default_http_download,
    ) -> None:
        self.client_id = (client_id or "").strip()
        self.cache_dir = Path(cache_dir)
        self.timeout_sec = timeout_sec
        self.download_timeout_sec = download_timeout_sec
        self.require_commercial = require_commercial
        self._http_get = http_get
        self._http_download = http_download

    @property
    def enabled(self) -> bool:
        return bool(self.client_id) and "your_jamendo" not in self.client_id

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, "JAMENDO_CLIENT_ID is not configured")

        primary = JAMENDO_MOOD_TAGS.get((query.mood or "").lower(), _DEFAULT_TAGS)
        tag_sets: tuple[tuple[str, ...], ...] = (
            primary,
            (primary[0],),
            ("ambient",),
            ("cinematic",),
        )

        last_error: Exception | None = None
        for tag_set in tag_sets:
            try:
                results = self._search(tag_set)
            except Exception as e:  # transport failure: try broader tags, then give up
                last_error = e
                continue
            track = self._pick(results, query)
            if track is not None:
                return self._download(track, query)

        if last_error is not None:
            raise ProviderExecutionError(self.name, f"search failed: {last_error}") from last_error
        raise ProviderExecutionError(self.name, f"no eligible track for mood {query.mood!r}")

    def _search(self, tag_set: tuple[str, ...]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "client_id": self.client_id,
            "format": "json",
            "limit": 20,
            "include": "musicinfo licenses",
            "audioformat": "mp32",
            "vocalinstrumental": "instrumental",
            "audiodlallowed": "true",
            "fuzzytags": ",".join(tag_set),
            "order": "popularity_total_desc",
        }
        if self.require_commercial:
            params["ccnc"] = "false"  # exclude NonCommercial licenses at the API
        payload = self._http_get(JAMENDO_TRACKS_URL, params, self.timeout_sec)
        return list(payload.get("results", []) or [])

    def _pick(self, results: list[dict[str, Any]], query: MusicQuery) -> dict[str, Any] | None:
        eligible: list[dict[str, Any]] = []
        for track in results:
            duration = float(track.get("duration") or 0)
            if not track.get("audiodownload"):
                continue
            if duration < query.min_duration_sec:
                continue
            if query.max_duration_sec and duration > query.max_duration_sec:
                continue
            if not track.get("license_ccurl"):
                continue  # only keep tracks with explicit license metadata
            eligible.append(track)
        return _stable_pick(eligible, query.selection_key)

    def _download(self, track: dict[str, Any], query: MusicQuery) -> MusicTrack:
        title = str(track.get("name") or "track")
        artist = str(track.get("artist_name") or "unknown")
        track_id = str(track.get("id") or "")
        ccurl = str(track.get("license_ccurl") or "")
        license_info = _license_from_ccurl(ccurl)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:40] or "track"
        out_path = self.cache_dir / f"{track_id}_{safe_title}.mp3"

        if not (out_path.exists() and out_path.stat().st_size > 50_000):
            try:
                self._http_download(str(track["audiodownload"]), out_path, self.download_timeout_sec)
            except Exception as e:
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                raise ProviderExecutionError(self.name, f"download failed: {e}") from e

        return MusicTrack(
            provider=self.name,
            provider_track_id=track_id,
            title=title,
            artist=artist,
            duration_sec=float(track.get("duration") or 0) or None,
            local_path=out_path,
            source_url=str(track.get("shareurl") or track.get("audiodownload") or ""),
            license=MusicLicense(
                license=license_info.license,
                commercial_use=license_info.commercial_use,
                attribution_required=license_info.attribution_required,
                attribution_text=f"{title} by {artist} (Jamendo, {license_info.license})",
                source_url=ccurl,
                verified=license_info.verified,
            ),
            mood=query.mood,
        )


def _stable_pick(candidates: list[dict[str, Any]], selection_key: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    if not selection_key:
        return candidates[0]
    digest = hashlib.sha1(selection_key.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(candidates)
    return candidates[index]
