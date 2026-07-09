"""Provider registry and fallback execution."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Generic, Iterable, TypeVar

from autovideo.providers.base import (
    ProviderError,
    ProviderFallbackError,
    ProviderHealth,
    ProviderHealthStatus,
)

T = TypeVar("T")


@dataclass(frozen=True)
class RegisteredProvider(Generic[T]):
    name: str
    provider: T
    priority: int = 100
    enabled: bool = True
    profiles: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()
    config: dict[str, object] = field(default_factory=dict)
    health: ProviderHealth = ProviderHealth()


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, list[RegisteredProvider[object]]] = defaultdict(list)

    def register(
        self,
        capability: str,
        name: str,
        provider: object,
        *,
        priority: int = 100,
        enabled: bool = True,
        profiles: Iterable[str] = (),
        features: Iterable[str] = (),
        config: dict[str, object] | None = None,
        health: ProviderHealth | None = None,
    ) -> None:
        item = RegisteredProvider(
            name=name,
            provider=provider,
            priority=priority,
            enabled=enabled,
            profiles=tuple(profiles),
            features=frozenset(features),
            config=dict(config or {}),
            health=health or ProviderHealth(),
        )
        self._providers[capability].append(item)
        self._providers[capability].sort(key=lambda p: p.priority)

    def providers(
        self,
        capability: str,
        *,
        profile: str | None = None,
        feature: str | None = None,
        include_unhealthy: bool = False,
    ) -> Iterable[RegisteredProvider[object]]:
        items = []
        for item in self._providers.get(capability, []):
            if not item.enabled:
                continue
            if profile and item.profiles and profile not in item.profiles:
                continue
            if feature and feature not in item.features:
                continue
            if not include_unhealthy and item.health.status == ProviderHealthStatus.UNAVAILABLE:
                continue
            items.append(item)
        return tuple(items)

    def first(self, capability: str, *, profile: str | None = None, feature: str | None = None) -> object | None:
        for item in self.providers(capability, profile=profile, feature=feature):
            return item.provider
        return None

    def provider_names(self, capability: str, *, profile: str | None = None) -> tuple[str, ...]:
        return tuple(item.name for item in self.providers(capability, profile=profile))

    def health(self, capability: str, name: str) -> ProviderHealth | None:
        for item in self._providers.get(capability, []):
            if item.name == name:
                return item.health
        return None

    def execute(
        self,
        capability: str,
        operation: Callable[[object], T],
        *,
        profile: str | None = None,
        feature: str | None = None,
    ) -> T:
        errors: dict[str, ProviderError] = {}
        for item in self.providers(capability, profile=profile, feature=feature):
            try:
                return operation(item.provider)
            except ProviderError as e:
                errors[item.name] = e
                if not e.retryable:
                    break
            except Exception as e:
                errors[item.name] = ProviderError(item.name, str(e))
        raise ProviderFallbackError(capability, errors)
