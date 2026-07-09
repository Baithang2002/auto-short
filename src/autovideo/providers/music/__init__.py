from .adapters import CallableMusicProvider
from .base import (
    MusicCapability,
    MusicLicense,
    MusicProvider,
    MusicQuery,
    MusicTrack,
    MusicTrackProvider,
)
from .generated import GeneratedMusicProvider
from .jamendo import JamendoMusicProvider
from .mixkit import MixkitCatalogEntry, MixkitMusicProvider
from .pixabay import PixabayMusicProvider
from .silence import SilenceMusicProvider

__all__ = [
    "CallableMusicProvider",
    "GeneratedMusicProvider",
    "JamendoMusicProvider",
    "MixkitCatalogEntry",
    "MixkitMusicProvider",
    "MusicCapability",
    "MusicLicense",
    "MusicProvider",
    "MusicQuery",
    "MusicTrack",
    "MusicTrackProvider",
    "PixabayMusicProvider",
    "SilenceMusicProvider",
]
