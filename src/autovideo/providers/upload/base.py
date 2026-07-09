"""Upload provider interface."""

from __future__ import annotations

from typing import Protocol

from autovideo.domain.metadata import UploadMetadata
from autovideo.providers.base import ProviderResult


class UploadProvider(Protocol):
    name: str
    platform: str

    def upload(self, metadata: UploadMetadata) -> ProviderResult[dict]:
        """Upload a prepared package and return platform result metadata."""
