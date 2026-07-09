"""LLM provider interface."""

from __future__ import annotations

import json
from typing import Protocol

from autovideo.providers.base import ProviderResult


class LLMProvider(Protocol):
    name: str

    def generate_text(self, prompt: str) -> ProviderResult[str]:
        """Return raw text for a prompt."""

    def generate_json(self, prompt: str) -> ProviderResult[dict]:
        """Return a parsed JSON object for a prompt."""


def parse_json_result(provider: str, text: str) -> ProviderResult[dict]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()
    return ProviderResult(provider=provider, value=json.loads(raw))
