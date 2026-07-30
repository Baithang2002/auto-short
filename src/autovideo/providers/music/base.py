"""Music provider interface, capabilities, and license-aware track metadata."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from autovideo.domain.asset import Asset
from autovideo.providers.base import ProviderResult


class MusicCapability(str, Enum):
    """Capabilities a music provider can advertise to the planner."""

    MOOD_SEARCH = "mood_search"
    COMMERCIAL_USE = "commercial_use"
    ATTRIBUTION_FREE = "attribution_free"
    CONTENT_ID_SAFE = "content_id_safe"
    OFFLINE = "offline"
    GENERATED = "generated"
    SILENCE = "silence"


@dataclass(frozen=True)
class MusicLicense:
    """Structured license metadata attached to every selected track.

    ``verified`` is True only when the provider exposed explicit license
    metadata for the track (or the license is intrinsic, e.g. generated audio).
    """

    license: str = ""
    commercial_use: bool = False
    attribution_required: bool = False
    attribution_text: str = ""
    source_url: str = ""
    verified: bool = False
    derivatives_allowed: bool | None = None

    @property
    def no_derivatives(self) -> bool:
        if self.derivatives_allowed is False:
            return True
        normalized = re.sub(
            r"[_\s]+",
            "-",
            " ".join((self.license, self.source_url)).strip().upper(),
        )
        return ("NODERIVATIVES" in normalized or "NO-DERIVATIVES" in normalized) or bool(
            re.search(r"(?:^|[-/])ND(?:$|[-/.])", normalized)
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MusicLicense":
        if not data:
            return cls()
        return cls(
            license=str(data.get("license", "")),
            commercial_use=bool(data.get("commercial_use", False)),
            attribution_required=bool(data.get("attribution_required", False)),
            attribution_text=str(data.get("attribution_text", "")),
            source_url=str(data.get("source_url", "")),
            verified=bool(data.get("verified", False)),
            derivatives_allowed=data.get("derivatives_allowed"),
        )


@dataclass(frozen=True)
class MusicTrack:
    """One selected music track plus its provenance and license metadata.

    ``local_path`` is None only for the silence provider, which represents
    "render without background music".
    """

    provider: str
    provider_track_id: str
    title: str
    artist: str = ""
    duration_sec: float | None = None
    local_path: Path | None = None
    source_url: str = ""
    license: MusicLicense = field(default_factory=MusicLicense)
    mood: str = ""
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_silence(self) -> bool:
        return self.local_path is None

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform or self.provider
        platform_metadata = self.metadata.get("platform")
        if not isinstance(platform_metadata, dict):
            platform_metadata = {"name": platform}
        return {
            "provider": self.provider,
            "provider_track_id": self.provider_track_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration_sec,
            "local_path": str(self.local_path) if self.local_path else None,
            "source_url": self.source_url,
            "license": self.license.license,
            "license_metadata": self.license.to_dict(),
            "commercial_use": self.license.commercial_use,
            "attribution_required": self.license.attribution_required,
            "attribution_text": self.license.attribution_text,
            "attribution": {
                "required": self.license.attribution_required,
                "text": self.license.attribution_text,
            },
            "verified": self.license.verified,
            "derivatives_allowed": self.license.derivatives_allowed,
            "platform": platform,
            "platform_metadata": dict(platform_metadata),
            "mood": self.mood,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MusicQuery:
    mood: str
    min_duration_sec: float
    max_duration_sec: float = 0.0
    target_duration_sec: float = 0.0
    content_id_safe: bool = True
    selection_key: str = ""


class MusicProvider(Protocol):
    name: str

    def fetch(self, query: MusicQuery, output_dir: Path) -> ProviderResult[Asset]:
        """Fetch or generate a music asset."""


class MusicTrackProvider(Protocol):
    """License-aware music provider contract used by the MusicPlanner.

    Implementations raise ``ProviderUnavailableError`` when unconfigured and
    ``ProviderExecutionError`` on expected fetch failures so the planner can
    fall through to the next provider in the configured chain.
    """

    name: str
    capabilities: tuple[MusicCapability, ...]

    def fetch_track(self, query: MusicQuery) -> MusicTrack:
        """Fetch or generate one track matching the query."""
