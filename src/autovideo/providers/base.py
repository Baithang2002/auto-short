"""Shared provider primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    provider: str
    value: T
    metadata: dict[str, object] | None = None


class ProviderHealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderHealth:
    status: ProviderHealthStatus = ProviderHealthStatus.UNKNOWN
    message: str = ""


class ProviderError(RuntimeError):
    """Base error surfaced from provider adapters."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    """Raised when a provider is not configured or currently unavailable."""


class ProviderExecutionError(ProviderError):
    """Raised when a provider call fails after provider-specific handling."""


class ProviderFallbackError(ProviderError):
    """Raised when every provider in a fallback chain fails."""

    def __init__(self, capability: str, errors: dict[str, ProviderError]) -> None:
        summary = "; ".join(f"{name}={err}" for name, err in errors.items())
        super().__init__(capability, f"All providers failed for {capability}: {summary}", retryable=False)
        self.capability = capability
        self.errors = errors
