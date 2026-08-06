"""ElevenLabs voice provider."""

from __future__ import annotations

from pathlib import Path

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.voice.base import VoiceRequest


_QUOTA_STATUS_CODES = frozenset({401, 402, 429})


class ElevenLabsVoiceProvider:
    name = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = "",
        model: str = "eleven_multilingual_v2",
        timeout_sec: int = 120,
        accounts: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.model = model
        self.timeout_sec = timeout_sec
        configured_accounts = tuple(
            (key.strip(), voice.strip())
            for key, voice in accounts
            if key.strip() and voice.strip()
        )
        if configured_accounts:
            self.accounts = configured_accounts
        elif api_key.strip() and voice_id.strip():
            self.accounts = ((api_key.strip(), voice_id.strip()),)
        else:
            self.accounts = ()
        self._preferred_index = 0

    def synthesize(self, request: VoiceRequest) -> ProviderResult[Path]:
        import requests

        if not self.accounts:
            raise ProviderUnavailableError(self.name, "ElevenLabs is not configured")

        errors: list[str] = []
        start_index = self._preferred_index
        for offset in range(len(self.accounts)):
            index = (start_index + offset) % len(self.accounts)
            api_key, account_voice_id = self.accounts[index]
            active_voice_id = account_voice_id
            if not active_voice_id:
                continue
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{active_voice_id}"
            headers = {
                "xi-api-key": api_key,
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
            response: requests.Response | None = None
            try:
                response = requests.post(url, json=data, headers=headers, timeout=self.timeout_sec)
                response.raise_for_status()
                request.output_path.write_bytes(response.content)
            except Exception as exc:
                detail = str(exc)
                if (
                    isinstance(exc, requests.HTTPError)
                    and response is not None
                    and response.status_code in _QUOTA_STATUS_CODES
                ):
                    self._preferred_index = (index + 1) % len(self.accounts)
                    errors.append(f"account {index + 1}/{len(self.accounts)} HTTP {response.status_code}")
                    continue
                errors.append(f"account {index + 1}/{len(self.accounts)}: {detail[:200]}")
                if not isinstance(exc, requests.HTTPError):
                    continue
                raise ProviderExecutionError(self.name, "; ".join(errors)) from exc
            return ProviderResult(
                provider=self.name,
                value=request.output_path,
                metadata={"unit": request.unit.value, "voice_id": active_voice_id},
            )
        raise ProviderExecutionError(self.name, "; ".join(errors) or "no configured accounts usable")
