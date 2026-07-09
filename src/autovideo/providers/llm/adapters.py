"""LLM provider adapters for legacy callables."""

from __future__ import annotations

from collections.abc import Callable

from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.llm.base import parse_json_result


class CallableLLMProvider:
    """Wrap an existing prompt callable without changing provider behavior."""

    def __init__(
        self,
        name: str,
        generate: Callable[[str], tuple[str | None, Exception | None]],
        *,
        models: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self._generate = generate
        self.models = models

    def generate_text(self, prompt: str) -> ProviderResult[str]:
        raw, error = self._generate(prompt)
        if raw:
            return ProviderResult(provider=self.name, value=raw, metadata={"models": list(self.models)})
        if error is None:
            raise ProviderUnavailableError(self.name, f"{self.name} is not configured")
        raise ProviderExecutionError(self.name, str(error))

    def generate_json(self, prompt: str) -> ProviderResult[dict]:
        result = self.generate_text(prompt)
        parsed = parse_json_result(result.provider, result.value)
        return ProviderResult(provider=result.provider, value=parsed.value, metadata=result.metadata)
