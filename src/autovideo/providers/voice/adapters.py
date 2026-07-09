"""Voice provider adapters for existing synthesis functions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.voice.base import VoiceRequest


class CallableVoiceProvider:
    """Wrap an existing text-to-file synthesizer."""

    def __init__(
        self,
        name: str,
        synthesize: Callable[[str, Path, str], None],
        *,
        enabled: bool = True,
        default_voice_id: str = "",
    ) -> None:
        self.name = name
        self._synthesize = synthesize
        self.enabled = enabled
        self.default_voice_id = default_voice_id

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, f"{self.name} is not configured")
        voice_id = request.voice_id or self.default_voice_id
        try:
            self._synthesize(request.text, request.output_path, voice_id)
        except ProviderUnavailableError:
            raise
        except Exception as e:
            raise ProviderExecutionError(self.name, str(e)) from e
        return ProviderResult(
            provider=self.name,
            value=request.output_path,
            metadata={
                "unit": request.unit.value,
                "chapter_id": request.chapter_id,
                "scene_id": request.scene_id,
            },
        )
