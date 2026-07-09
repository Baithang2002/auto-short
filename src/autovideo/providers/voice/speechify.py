"""Speechify voice provider."""

from __future__ import annotations

import base64
from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.voice.base import VoiceRequest


class SpeechifyVoiceProvider:
    name = "speechify"

    def __init__(self, *, api_key: str, voice_id: str, timeout_sec: int = 60) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.timeout_sec = timeout_sec
        self._dead = False

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        if self._dead or not self.api_key.strip():
            raise ProviderUnavailableError(self.name, "Speechify is not configured")

        import requests

        url = "https://api.speechify.ai/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "input": request.text,
            "voice_id": request.voice_id or self.voice_id,
            "audio_format": "mp3",
            "model": "simba-english",
        }
        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout_sec)
            response.raise_for_status()
            body = response.json()
            audio_b64 = body.get("audio_data")
            if not audio_b64:
                raise ProviderExecutionError(self.name, f"Speechify response missing audio_data: {response.text[:200]}")
            with open(request.output_path, "wb") as f:
                f.write(base64.b64decode(audio_b64))
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "402" in err_str or "Unauthorized" in err_str:
                self._dead = True
            if isinstance(e, ProviderExecutionError):
                raise
            raise ProviderExecutionError(self.name, err_str) from e
        return ProviderResult(provider=self.name, value=request.output_path, metadata={"unit": request.unit.value})
