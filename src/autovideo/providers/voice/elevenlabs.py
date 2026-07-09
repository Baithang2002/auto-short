"""ElevenLabs voice provider."""

from __future__ import annotations

from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.voice.base import VoiceRequest


class ElevenLabsVoiceProvider:
    name = "elevenlabs"

    def __init__(self, *, api_key: str, voice_id: str, model: str, timeout_sec: int = 120) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.timeout_sec = timeout_sec

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        if not self.api_key.strip() or not (request.voice_id or self.voice_id).strip():
            raise ProviderUnavailableError(self.name, "ElevenLabs is not configured")

        import requests

        voice_id = request.voice_id or self.voice_id
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        data = {
            "text": request.text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
            },
        }
        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout_sec)
            response.raise_for_status()
            request.output_path.write_bytes(response.content)
        except Exception as e:
            raise ProviderExecutionError(self.name, str(e)) from e
        return ProviderResult(provider=self.name, value=request.output_path, metadata={"unit": request.unit.value})
