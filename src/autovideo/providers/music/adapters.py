"""Music provider adapters for legacy fetch callables."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from autovideo.domain.asset import Asset, AssetStatus, AssetType
from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.music.base import MusicQuery


class CallableMusicProvider:
    def __init__(self, name: str, fetch: Callable[[MusicQuery, Path], Path | None], *, enabled: bool = True) -> None:
        self.name = name
        self._fetch = fetch
        self.enabled = enabled

    def fetch(self, query: MusicQuery, output_dir: Path) -> ProviderResult[Asset]:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, f"{self.name} is not configured")
        try:
            path = self._fetch(query, output_dir)
        except Exception as e:
            raise ProviderExecutionError(self.name, str(e)) from e
        if path is None:
            raise ProviderUnavailableError(self.name, f"{self.name} found no track")
        asset = Asset(
            id=Path(path).stem,
            asset_type=AssetType.MUSIC,
            provider=self.name,
            local_path=Path(path),
            status=AssetStatus.AVAILABLE,
        )
        return ProviderResult(provider=self.name, value=asset)
