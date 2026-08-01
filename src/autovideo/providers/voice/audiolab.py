"""AudioLab Router voice provider.

AudioLab Router is an OpenAI-compatible speech endpoint that auto-routes
text-to-speech requests across 150+ models. The response is raw audio bytes.
"""

from __future__ import annotations

from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.voice.base import VoiceRequest


_QUOTA_STATUS_CODES = frozenset({401, 402, 429})

_AUDIOLAB_SPEECH_URL = "https://api.tryaudiolab.ai/v1/audio/speech"


class AudioLabVoiceProvider:
    name = "audiolab"

    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = "auto",
        model: str = "tts/auto",
        timeout_sec: int = 60,
    ) -> None:
        self.api_key = api_key.strip()
        self.voice_id = voice_id.strip() or "auto"
        self.model = model.strip() or "tts/auto"
        self.timeout_sec = timeout_sec
        self._dead = False

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        if self._dead or not self.api_key:
            raise ProviderUnavailableError(self.name, "AudioLab is not configured")

        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": self.model,
            "input": request.text,
            "voice": request.voice_id or self.voice_id,
            "response_format": "mp3",
        }
        try:
            response = requests.post(
                _AUDIOLAB_SPEECH_URL,
                json=data,
                headers=headers,
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            request.output_path.write_bytes(response.content)
        except Exception as exc:
            err_str = str(exc)
            if "401" in err_str or "402" in err_str or "429" in err_str or "Unauthorized" in err_str:
                self._dead = True
            if isinstance(exc, ProviderExecutionError):
                raise
            raise ProviderExecutionError(self.name, err_str) from exc
        return ProviderResult(
            provider=self.name,
            value=request.output_path,
            metadata={"unit": request.unit.value, "voice": request.voice_id or self.voice_id},
        )
