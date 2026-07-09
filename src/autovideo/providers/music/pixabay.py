"""Pixabay music provider (Content ID-free, commercial-use catalog)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack

PIXABAY_API_URL = "https://pixabay.com/api/"

PIXABAY_MUSIC_QUERIES: dict[str, tuple[str, ...]] = {
    "mysterious": ("dark ambient", "mysterious cinematic"),
    "inspiring": ("uplifting", "inspirational background"),
    "dramatic": ("epic dramatic", "cinematic action"),
    "warm": ("soft acoustic", "gentle piano"),
    "curious": ("ambient electronic", "light background"),
    "urgent": ("intense action", "tense suspense"),
}
_DEFAULT_QUERIES: tuple[str, ...] = ("ambient music",)

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


PIXABAY_LICENSE = MusicLicense(
    license="Pixabay Content License",
    commercial_use=True,
    attribution_required=False,
    source_url="https://pixabay.com/service/license-summary/",
    verified=True,
)


class PixabayMusicProvider:
    """Fetch royalty-free tracks from Pixabay's audio library.

    Pixabay audio is free for commercial use, requires no attribution, and is
    not enrolled in Content ID by policy. Deterministic: the first eligible
    hit per query is chosen (stable given identical API responses).
    """

    name = "pixabay"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.MOOD_SEARCH,
        MusicCapability.COMMERCIAL_USE,
        MusicCapability.ATTRIBUTION_FREE,
        MusicCapability.CONTENT_ID_SAFE,
    )

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: Path,
        timeout_sec: int = 20,
        download_timeout_sec: int = 120,
        http_get: HttpGet = _default_http_get,
        http_download: HttpDownload = _default_http_download,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.cache_dir = Path(cache_dir)
        self.timeout_sec = timeout_sec
        self.download_timeout_sec = download_timeout_sec
        self._http_get = http_get
        self._http_download = http_download

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and "your_pixabay" not in self.api_key

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, "PIXABAY_API_KEY is not configured")

        queries = PIXABAY_MUSIC_QUERIES.get((query.mood or "").lower(), _DEFAULT_QUERIES)
        last_error: Exception | None = None
        for search_query in queries:
            try:
                hits = self._search(search_query)
            except Exception as e:
                last_error = e
                continue
            candidate = self._pick(hits, query)
            if candidate is not None:
                return self._download(candidate, query)

        if last_error is not None:
            raise ProviderExecutionError(self.name, f"search failed: {last_error}") from last_error
        raise ProviderExecutionError(self.name, f"no eligible track for mood {query.mood!r}")

    def _search(self, search_query: str) -> list[dict[str, Any]]:
        payload = self._http_get(
            PIXABAY_API_URL,
            {
                "key": self.api_key,
                "q": search_query,
                "media_type": "music",
                "per_page": 20,
                "safesearch": "true",
            },
            self.timeout_sec,
        )
        return list(payload.get("hits", []) or [])

    def _pick(self, hits: list[dict[str, Any]], query: MusicQuery) -> dict[str, Any] | None:
        eligible: list[dict[str, Any]] = []
        for hit in hits:
            duration = float(hit.get("duration") or 0)
            if duration < query.min_duration_sec:
                continue
            if query.max_duration_sec and duration > query.max_duration_sec:
                continue
            download_url = (hit.get("audio", {}) or {}).get("url") or hit.get("audioURL") or hit.get("url") or ""
            if download_url:
                eligible.append({**hit, "_download_url": download_url})
        return _stable_pick(eligible, query.selection_key)

    def _download(self, hit: dict[str, Any], query: MusicQuery) -> MusicTrack:
        track_id = str(hit.get("id") or "track")
        tags = str(hit.get("tags", "music"))
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", tags)[:30] or "music"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.cache_dir / f"{track_id}_{safe_title}.mp3"

        if not (out_path.exists() and out_path.stat().st_size > 50_000):
            try:
                self._http_download(str(hit["_download_url"]), out_path, self.download_timeout_sec)
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
            title=tags,
            artist=str(hit.get("user") or ""),
            duration_sec=float(hit.get("duration") or 0) or None,
            local_path=out_path,
            source_url=str(hit.get("pageURL") or ""),
            license=PIXABAY_LICENSE,
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
