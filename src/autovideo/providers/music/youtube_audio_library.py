"""Offline provider for manually curated YouTube Audio Library tracks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autovideo.providers.base import ProviderExecutionError, ProviderUnavailableError
from autovideo.providers.music.base import MusicCapability, MusicLicense, MusicQuery, MusicTrack


class YouTubeAudioLibraryProvider:
    """Select local tracks described by an operator-maintained JSON manifest.

    This provider only reads local files. Source URLs in the manifest are
    retained as provenance and are never requested.
    """

    name = "youtube_audio_library"
    capabilities: tuple[MusicCapability, ...] = (
        MusicCapability.MOOD_SEARCH,
        MusicCapability.COMMERCIAL_USE,
        MusicCapability.CONTENT_ID_SAFE,
        MusicCapability.OFFLINE,
    )

    def __init__(
        self,
        manifest_path: Path | None,
        assets_dir: Path | None,
    ) -> None:
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None

    @property
    def enabled(self) -> bool:
        return self.manifest_path is not None and self.assets_dir is not None

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        if not self.enabled or self.manifest_path is None or self.assets_dir is None:
            raise ProviderUnavailableError(
                self.name,
                "YouTube Audio Library manifest and private asset directory are not configured",
            )
        if not self.manifest_path.is_file() or not self.assets_dir.is_dir():
            raise ProviderUnavailableError(
                self.name,
                "YouTube Audio Library manifest or private asset directory does not exist",
            )

        entries = self._read_manifest()
        tracks = [track for entry in entries if (track := self._track_from_entry(entry)) is not None]
        duration_matches = [track for track in tracks if _duration_matches(track, query)]
        mood = (query.mood or "").strip().lower()
        mood_matches = [
            track
            for track in duration_matches
            if mood and mood in {str(value).lower() for value in track.metadata.get("moods", ())}
        ]
        candidates = mood_matches or duration_matches
        if not candidates:
            raise ProviderExecutionError(
                self.name,
                f"no eligible local manifest track for mood {query.mood!r}",
            )
        return _stable_pick(candidates, query.selection_key)

    def _read_manifest(self) -> list[Mapping[str, Any]]:
        assert self.manifest_path is not None
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderExecutionError(self.name, f"invalid local manifest: {exc}") from exc

        raw_tracks = payload.get("tracks", ()) if isinstance(payload, Mapping) else payload
        if not isinstance(raw_tracks, list):
            raise ProviderExecutionError(self.name, "manifest must be a list or contain a tracks list")
        return [entry for entry in raw_tracks if isinstance(entry, Mapping)]

    def _track_from_entry(self, entry: Mapping[str, Any]) -> MusicTrack | None:
        assert self.assets_dir is not None
        relative_file = str(entry.get("file") or entry.get("path") or "").strip()
        if not relative_file:
            return None
        assets_root = self.assets_dir.resolve()
        local_path = (assets_root / relative_file).resolve()
        try:
            local_path.relative_to(assets_root)
        except ValueError:
            return None
        if not local_path.is_file():
            return None

        platform_raw = entry.get("platform")
        platform_data = dict(platform_raw) if isinstance(platform_raw, Mapping) else {}
        track_id = str(
            entry.get("id")
            or platform_data.get("track_id")
            or local_path.stem
        ).strip()
        title = str(entry.get("title") or local_path.stem).strip()
        artist = str(entry.get("artist") or "").strip()
        license_info = _license_from_entry(entry)
        if license_info.no_derivatives:
            return None

        try:
            duration = float(entry.get("duration_sec") or entry.get("duration") or 0)
        except (TypeError, ValueError):
            return None
        raw_moods = entry.get("moods") or entry.get("mood") or ()
        if isinstance(raw_moods, str):
            raw_moods = [raw_moods]
        moods = tuple(str(value).strip().lower() for value in raw_moods if str(value).strip())
        source_url = str(
            entry.get("source_url")
            or platform_data.get("source_url")
            or license_info.source_url
            or ""
        ).strip()
        attribution = {
            "required": license_info.attribution_required,
            "text": license_info.attribution_text,
        }
        platform_metadata = {
            **platform_data,
            "name": "youtube",
            "library": "YouTube Audio Library",
            "track_id": track_id,
            "source_url": source_url,
        }
        return MusicTrack(
            provider=self.name,
            provider_track_id=track_id,
            title=title,
            artist=artist,
            duration_sec=duration or None,
            local_path=local_path,
            source_url=source_url,
            license=license_info,
            mood="",
            platform="youtube",
            metadata={
                "moods": moods,
                "attribution": attribution,
                "platform": platform_metadata,
                "manifest": str(self.manifest_path),
            },
        )


def _license_from_entry(entry: Mapping[str, Any]) -> MusicLicense:
    license_raw = entry.get("license")
    license_data = dict(license_raw) if isinstance(license_raw, Mapping) else {}
    attribution_raw = entry.get("attribution")
    attribution_data = dict(attribution_raw) if isinstance(attribution_raw, Mapping) else {}
    license_name = (
        license_data.get("name")
        or license_data.get("license")
        or (license_raw if isinstance(license_raw, str) else "")
    )
    derivatives_allowed = license_data.get(
        "derivatives_allowed",
        entry.get("derivatives_allowed"),
    )
    if not isinstance(derivatives_allowed, bool):
        derivatives_allowed = None
    return MusicLicense(
        license=str(license_name or ""),
        commercial_use=bool(license_data.get("commercial_use", entry.get("commercial_use", False))),
        attribution_required=bool(
            attribution_data.get(
                "required",
                license_data.get("attribution_required", entry.get("attribution_required", False)),
            )
        ),
        attribution_text=str(
            attribution_data.get(
                "text",
                license_data.get("attribution_text", entry.get("attribution_text", "")),
            )
            or ""
        ),
        source_url=str(
            license_data.get("source_url")
            or license_data.get("url")
            or entry.get("license_url")
            or ""
        ),
        verified=bool(license_data.get("verified", entry.get("license_verified", False))),
        derivatives_allowed=derivatives_allowed,
    )


def _duration_matches(track: MusicTrack, query: MusicQuery) -> bool:
    duration = float(track.duration_sec or 0)
    if duration < query.min_duration_sec:
        return False
    return not query.max_duration_sec or duration <= query.max_duration_sec


def _stable_pick(candidates: list[MusicTrack], selection_key: str) -> MusicTrack:
    if not selection_key:
        return candidates[0]
    digest = hashlib.sha1(selection_key.encode("utf-8")).hexdigest()
    return candidates[int(digest[:8], 16) % len(candidates)]
