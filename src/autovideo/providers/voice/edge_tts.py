"""Edge TTS voice provider."""

from __future__ import annotations

import asyncio
from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult
from autovideo.providers.voice.base import VoiceRequest


class EdgeTTSVoiceProvider:
    name = "edge_tts"

    def __init__(self, *, voice_id: str, retry_attempts: int = 3) -> None:
        self.voice_id = voice_id
        self.retry_attempts = retry_attempts

    async def _synthesize_async(self, text: str, output_path: Path, voice_id: str) -> None:
        import edge_tts

        await edge_tts.Communicate(text, voice_id).save(str(output_path))

    async def _with_retry_async(self, text: str, output_path: Path, voice_id: str) -> None:
        import edge_tts

        last_err: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                await self._synthesize_async(text, output_path, voice_id)
                return
            except edge_tts.exceptions.NoAudioReceived as e:
                last_err = e
                await asyncio.sleep(2 ** (attempt - 1))
            except Exception as e:
                last_err = e
                await asyncio.sleep(2 ** (attempt - 1))
        raise ProviderExecutionError(
            self.name,
            f"Edge-TTS failed after {self.retry_attempts} attempts. Last error: {last_err}. "
            "Try: pip install --upgrade edge-tts",
        ) from last_err

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        voice_id = request.voice_id or self.voice_id
        asyncio.run(self._with_retry_async(request.text, request.output_path, voice_id))
        return ProviderResult(provider=self.name, value=request.output_path, metadata={"unit": request.unit.value})
