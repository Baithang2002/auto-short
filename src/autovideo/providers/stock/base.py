"""Stock media provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autovideo.domain.asset import Asset
from autovideo.providers.base import ProviderResult


@dataclass(frozen=True)
class StockQuery:
    queries: list[str]
    orientation: str
    target_duration_sec: float


class StockProvider(Protocol):
    name: str

    def fetch(self, query: StockQuery, output_dir: Path) -> ProviderResult[Asset]:
        """Fetch or generate a stock media asset."""
