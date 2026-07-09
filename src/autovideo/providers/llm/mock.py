"""Mock LLM provider for tests and CI."""

from __future__ import annotations

from autovideo.providers.base import ProviderResult
from autovideo.providers.llm.base import parse_json_result


class MockLLMProvider:
    name = "mock"

    def __init__(self, response: str = "{}") -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate_text(self, prompt: str) -> ProviderResult[str]:
        self.prompts.append(prompt)
        return ProviderResult(provider=self.name, value=self.response)

    def generate_json(self, prompt: str) -> ProviderResult[dict]:
        result = self.generate_text(prompt)
        parsed = parse_json_result(result.provider, result.value)
        return ProviderResult(provider=result.provider, value=parsed.value)
