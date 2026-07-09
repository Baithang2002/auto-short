"""Typed publishing artifacts returned by upload providers."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class PublishStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PublishTarget:
    """Destination platform and non-secret credential reference."""

    platform: str
    credentials_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishTarget":
        return cls(
            platform=str(data["platform"]),
            credentials_ref=str(data.get("credentials_ref", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class PublishResult:
    """Provider-neutral result of a publish/upload attempt."""

    status: PublishStatus
    platform: str = ""
    url: str = ""
    platform_id: str = ""
    error: str = ""
    attempted_at: dt.datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["attempted_at"] = self.attempted_at.isoformat() if self.attempted_at else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishResult":
        attempted_at = data.get("attempted_at")
        if isinstance(attempted_at, str) and attempted_at:
            attempted_at = dt.datetime.fromisoformat(attempted_at)
        elif not isinstance(attempted_at, dt.datetime):
            attempted_at = None
        return cls(
            status=PublishStatus(data["status"]),
            platform=str(data.get("platform", "")),
            url=str(data.get("url", "")),
            platform_id=str(data.get("platform_id", "")),
            error=str(data.get("error", "")),
            attempted_at=attempted_at,
            metadata=dict(data.get("metadata", {})),
        )
