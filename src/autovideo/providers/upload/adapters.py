"""Upload provider adapters."""

from __future__ import annotations

from collections.abc import Callable

from autovideo.domain.metadata import UploadMetadata
from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError


class CallableUploadProvider:
    def __init__(
        self,
        name: str,
        platform: str,
        upload: Callable[[UploadMetadata], dict],
        *,
        enabled: bool = True,
    ) -> None:
        self.name = name
        self.platform = platform
        self._upload = upload
        self.enabled = enabled

    def upload(self, metadata: UploadMetadata) -> ProviderResult[dict]:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, f"{self.name} uploads are disabled")
        try:
            result = self._upload(metadata)
        except Exception as e:
            raise ProviderExecutionError(self.name, str(e)) from e
        return ProviderResult(provider=self.name, value=result)


class MockUploadProvider:
    name = "mock_upload"
    platform = "mock"

    def upload(self, metadata: UploadMetadata) -> ProviderResult[dict]:
        return ProviderResult(provider=self.name, value={"status": "ok", "mock": True, "id": metadata.id})
