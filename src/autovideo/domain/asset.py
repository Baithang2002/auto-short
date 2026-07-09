"""Asset domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AssetType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    VOICE = "voice"
    MUSIC = "music"
    SFX = "sfx"
    CHART = "chart"
    CARD = "card"
    THUMBNAIL = "thumbnail"
    CAPTION = "caption"


class AssetStatus(str, Enum):
    PLANNED = "planned"
    AVAILABLE = "available"
    FAILED = "failed"


@dataclass(frozen=True)
class LicenseInfo:
    name: str = ""
    attribution: str = ""
    source_url: str = ""
    commercial_use: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LicenseInfo":
        if not data:
            return cls()
        return cls(
            name=str(data.get("name", "")),
            attribution=str(data.get("attribution", "")),
            source_url=str(data.get("source_url", "")),
            commercial_use=bool(data.get("commercial_use", True)),
        )


@dataclass(frozen=True)
class Asset:
    id: str
    asset_type: AssetType
    provider: str = ""
    local_path: Path | None = None
    source_url: str = ""
    license: LicenseInfo = field(default_factory=LicenseInfo)
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    checksum: str = ""
    reusable: bool = False
    status: AssetStatus = AssetStatus.PLANNED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_type"] = self.asset_type.value
        data["status"] = self.status.value
        data["local_path"] = str(self.local_path) if self.local_path else ""
        data["license"] = self.license.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Asset":
        local_path = data.get("local_path")
        return cls(
            id=str(data["id"]),
            asset_type=AssetType(data["asset_type"]),
            provider=str(data.get("provider", "")),
            local_path=Path(local_path) if local_path else None,
            source_url=str(data.get("source_url", "")),
            license=LicenseInfo.from_dict(data.get("license")),
            duration_sec=data.get("duration_sec"),
            width=data.get("width"),
            height=data.get("height"),
            checksum=str(data.get("checksum", "")),
            reusable=bool(data.get("reusable", False)),
            status=AssetStatus(data.get("status", AssetStatus.PLANNED.value)),
        )
