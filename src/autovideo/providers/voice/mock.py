"""Mock voice provider for tests and CI."""

from __future__ import annotations

from pathlib import Path

from autovideo.providers.base import ProviderResult
from autovideo.providers.voice.base import VoiceRequest


class MockVoiceProvider:
    name = "mock"

    def __init__(self, payload: bytes = b"mock-audio") -> None:
        self.payload = payload
        self.requests: list[VoiceRequest] = []

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        self.requests.append(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(self.payload)
        return ProviderResult(provider=self.name, value=request.output_path, metadata={"unit": request.unit.value})
