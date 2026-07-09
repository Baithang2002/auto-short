"""Music subsystem configuration: schema, defaults, and env-backed loading.

All music tunables live here so no provider name or magic number is
hard-coded inside business logic. Loading is deterministic: the same
environment always produces the same ``MusicConfig``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from autovideo.config.settings import Settings

SUPPORTED_MUSIC_PROVIDERS: tuple[str, ...] = ("jamendo", "pixabay", "mixkit", "generated", "silence")
_TEST_ONLY_PROVIDERS: tuple[str, ...] = ("mock",)
_DEPRECATED_PROVIDERS: tuple[str, ...] = ("local",)

DEFAULT_MUSIC_PROVIDER_ORDER: tuple[str, ...] = ("jamendo", "pixabay", "mixkit", "generated", "silence")


class MusicConfigError(ValueError):
    """Raised when music configuration is invalid. Message names the field."""


@dataclass(frozen=True)
class MusicConfig:
    """Validated music subsystem configuration."""

    provider_order: tuple[str, ...] = DEFAULT_MUSIC_PROVIDER_ORDER
    retries: int = 2
    timeout_sec: int = 20
    volume: float = 0.22  # linear gain relative to narration
    fade_in_ms: int = 1500
    fade_out_ms: int = 0  # 0 = automatic (duration-scaled), matching legacy behavior
    min_duration_sec: int = 20
    max_duration_sec: int = 0  # 0 = no upper bound
    enable_generated: bool = True
    require_commercial_license: bool = True
    allow_attribution: bool = True

    def __post_init__(self) -> None:
        _validate_order(self.provider_order)
        _require_range("AUTO_VIDEO_MUSIC_RETRIES", self.retries, 0, 10)
        _require_range("AUTO_VIDEO_MUSIC_TIMEOUT", self.timeout_sec, 1, 600)
        if not 0.0 <= self.volume <= 1.0:
            raise MusicConfigError(
                f"AUTO_VIDEO_MUSIC_VOLUME must be between 0.0 and 1.0 (linear gain), got {self.volume}"
            )
        _require_range("AUTO_VIDEO_MUSIC_FADE_IN_MS", self.fade_in_ms, 0, 30_000)
        _require_range("AUTO_VIDEO_MUSIC_FADE_OUT_MS", self.fade_out_ms, 0, 30_000)
        _require_range("AUTO_VIDEO_MUSIC_MIN_DURATION_SEC", self.min_duration_sec, 0, 3600)
        _require_range("AUTO_VIDEO_MUSIC_MAX_DURATION_SEC", self.max_duration_sec, 0, 36_000)
        if self.max_duration_sec and self.max_duration_sec < self.min_duration_sec:
            raise MusicConfigError(
                "AUTO_VIDEO_MUSIC_MAX_DURATION_SEC must be 0 (unlimited) or >= "
                f"AUTO_VIDEO_MUSIC_MIN_DURATION_SEC ({self.min_duration_sec}), got {self.max_duration_sec}"
            )


def _require_range(name: str, value: int, lo: int, hi: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise MusicConfigError(f"{name} must be an integer between {lo} and {hi}, got {value!r}")


def _validate_order(order: tuple[str, ...]) -> None:
    if not order:
        raise MusicConfigError(
            "AUTO_VIDEO_MUSIC_PROVIDER_ORDER must name at least one provider; "
            f"supported: {', '.join(SUPPORTED_MUSIC_PROVIDERS)}"
        )
    valid = set(SUPPORTED_MUSIC_PROVIDERS) | set(_TEST_ONLY_PROVIDERS)
    for name in order:
        if name not in valid:
            raise MusicConfigError(
                f"Unknown music provider {name!r} in AUTO_VIDEO_MUSIC_PROVIDER_ORDER; "
                f"supported: {', '.join(SUPPORTED_MUSIC_PROVIDERS)}"
            )


def parse_provider_order(raw: str, *, default: tuple[str, ...] = DEFAULT_MUSIC_PROVIDER_ORDER) -> tuple[str, ...]:
    """Parse a comma-separated provider order, dropping deprecated names.

    The deprecated ``local`` provider (manual music folders) is accepted for
    backward compatibility but skipped with a warning — the platform is fully
    automated and no longer selects from local libraries.
    """
    if not raw.strip():
        return default
    requested = [name.strip().lower() for name in raw.split(",") if name.strip()]
    kept: list[str] = []
    for name in requested:
        if name in _DEPRECATED_PROVIDERS:
            warnings.warn(
                f"Music provider {name!r} is deprecated and ignored; "
                f"supported providers: {', '.join(SUPPORTED_MUSIC_PROVIDERS)}",
                DeprecationWarning,
                stacklevel=2,
            )
            continue
        if name not in kept:
            kept.append(name)
    if not kept:
        return default
    return tuple(kept)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise MusicConfigError(f"{name} must be an integer, got {raw!r}") from e


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise MusicConfigError(f"{name} must be a number, got {raw!r}") from e


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise MusicConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def music_config_from_env(
    env: Mapping[str, str],
    *,
    profile_order: tuple[str, ...] | None = None,
    default_volume: float = 0.22,
) -> MusicConfig:
    """Build a validated MusicConfig from environment variables.

    Precedence: environment variables > render-profile defaults > platform
    defaults. Missing optional variables receive defaults; invalid values
    raise :class:`MusicConfigError` naming the offending variable.
    """
    if profile_order:
        base_order = parse_provider_order(",".join(profile_order), default=DEFAULT_MUSIC_PROVIDER_ORDER)
    else:
        base_order = DEFAULT_MUSIC_PROVIDER_ORDER
    order = parse_provider_order(env.get("AUTO_VIDEO_MUSIC_PROVIDER_ORDER", ""), default=base_order)
    enable_generated = _env_bool(env, "AUTO_VIDEO_ENABLE_GENERATED_MUSIC", True)
    return MusicConfig(
        provider_order=order,
        retries=_env_int(env, "AUTO_VIDEO_MUSIC_RETRIES", 2),
        timeout_sec=_env_int(env, "AUTO_VIDEO_MUSIC_TIMEOUT", 20),
        volume=_env_float(env, "AUTO_VIDEO_MUSIC_VOLUME", default_volume),
        fade_in_ms=_env_int(env, "AUTO_VIDEO_MUSIC_FADE_IN_MS", 1500),
        fade_out_ms=_env_int(env, "AUTO_VIDEO_MUSIC_FADE_OUT_MS", 0),
        min_duration_sec=_env_int(env, "AUTO_VIDEO_MUSIC_MIN_DURATION_SEC", 20),
        max_duration_sec=_env_int(env, "AUTO_VIDEO_MUSIC_MAX_DURATION_SEC", 0),
        enable_generated=enable_generated,
        require_commercial_license=_env_bool(env, "AUTO_VIDEO_REQUIRE_COMMERCIAL_LICENSE", True),
        allow_attribution=_env_bool(env, "AUTO_VIDEO_ALLOW_ATTRIBUTION", True),
    )


def music_config_from_settings(settings: "Settings", *, profile_order: tuple[str, ...] | None = None) -> MusicConfig:
    """Build a MusicConfig from a Settings object (env-backed)."""
    from autovideo.config.defaults import DEFAULTS

    return music_config_from_env(
        settings.env_values,
        profile_order=profile_order,
        default_volume=DEFAULTS.render.default_music_volume,
    )
