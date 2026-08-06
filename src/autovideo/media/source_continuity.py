"""Source Continuity Engine.

The candidate pool is scored per scene in isolation.  This engine is an
optimization layer that biases scene selection toward a single dominant
source (provider, video, creator, collection) so a finished documentary
feels like one continuous story instead of unrelated stock clips.

It never lowers the accuracy bar.  Continuity only re-ranks candidates
that already pass the same quality gates, and only when the dominant
source candidate scores within a small tolerance of the independent
best.  Hard constraints (entity / action / environment / visual
evidence / verified media gate) are enforced upstream and unchanged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .selection import (
    MediaSelectionResult,
    StockCandidate,
    select_best_candidate,
)

if TYPE_CHECKING:
    from .selection import VisualIntent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceContinuityConfig:
    """Runtime policy for the source continuity engine."""

    enabled: bool = True
    minimum_continuity_score: float = 0.0
    preferred_dominant_ratio: float = 0.7
    maximum_unnecessary_switches: int = 2
    continuity_bonus: float = 0.4
    reuse_verified_source: bool = True
    minimum_usage_before_lock: int = 2

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> "SourceContinuityConfig":
        def flag(name: str, default: bool) -> bool:
            raw = values.get(name)
            if raw is None:
                return default
            return str(raw).strip().lower() not in {"", "0", "false", "no", "off"}

        def number(name: str, default: float) -> float:
            try:
                return float(values.get(name, default))
            except (TypeError, ValueError):
                return default

        def whole(name: str, default: int) -> int:
            try:
                return max(0, int(values.get(name, default)))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=flag("AUTO_VIDEO_SOURCE_CONTINUITY_ENABLED", True),
            minimum_continuity_score=max(0.0, number(
                "AUTO_VIDEO_SOURCE_CONTINUITY_MIN_SCORE", 0.0
            )),
            preferred_dominant_ratio=max(0.0, min(1.0, number(
                "AUTO_VIDEO_SOURCE_CONTINUITY_PREFERRED_RATIO", 0.7
            ))),
            maximum_unnecessary_switches=whole(
                "AUTO_VIDEO_SOURCE_CONTINUITY_MAX_SWITCHES", 2
            ),
            continuity_bonus=max(0.0, number(
                "AUTO_VIDEO_SOURCE_CONTINUITY_BONUS", 0.4
            )),
            reuse_verified_source=flag(
                "AUTO_VIDEO_SOURCE_CONTINUITY_REUSE_VERIFIED", True
            ),
            minimum_usage_before_lock=whole(
                "AUTO_VIDEO_SOURCE_CONTINUITY_MIN_USAGE", 2
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "minimum_continuity_score": self.minimum_continuity_score,
            "preferred_dominant_ratio": self.preferred_dominant_ratio,
            "maximum_unnecessary_switches": self.maximum_unnecessary_switches,
            "continuity_bonus": self.continuity_bonus,
            "reuse_verified_source": self.reuse_verified_source,
            "minimum_usage_before_lock": self.minimum_usage_before_lock,
        }


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------

_YOUTUBE_VID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")


def _canonical_source_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = _YOUTUBE_VID_RE.search(text)
    if match:
        return f"youtube:{match.group(1)}"
    return text


@dataclass(frozen=True)
class SourceIdentity:
    """Canonical grouping key for one media source."""

    provider: str = ""
    source_key: str = ""
    creator: str = ""
    collection: str = ""
    source_url: str = ""

    @property
    def identity_key(self) -> str:
        return "|".join((
            str(self.provider).strip().lower(),
            str(self.source_key).strip().lower(),
            str(self.creator).strip().lower(),
            str(self.collection).strip().lower(),
            _canonical_source_url(self.source_url),
        ))

    def matches(self, other: "SourceIdentity | None") -> bool:
        if other is None:
            return False
        return bool(self.identity_key) and self.identity_key == other.identity_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_key": self.source_key,
            "creator": self.creator,
            "collection": self.collection,
            "source_url": self.source_url,
            "identity_key": self.identity_key,
        }


def identity_from_candidate(candidate: StockCandidate) -> SourceIdentity:
    """Derive a canonical source identity from a normalized stock candidate."""
    metadata = dict(candidate.raw_metadata or {})
    attribution = metadata.get("attribution", "") or metadata.get("creator", "")
    if isinstance(attribution, dict):
        attribution = attribution.get("name", "") or attribution.get("creator", "") or ""
    collection = metadata.get("collection", "") or metadata.get("album", "")
    source_url = candidate.url or candidate.download_url or ""
    source_key = candidate.provider_id or ""
    if candidate.provider == "yt_clip":
        match = _YOUTUBE_VID_RE.search(source_url)
        if match:
            source_key = match.group(1)
    return SourceIdentity(
        provider=candidate.provider or "",
        source_key=source_key,
        creator=str(attribution or ""),
        collection=str(collection or ""),
        source_url=source_url,
    )


def identity_from_selection(metadata: Mapping[str, Any]) -> SourceIdentity:
    """Derive a canonical source identity from a ``selection`` dict."""
    selection = dict(metadata.get("selection", {}) or {})
    provider = str(selection.get("provider") or metadata.get("provider") or "")
    provider_id = str(
        selection.get("provider_id")
        or metadata.get("provider_asset_id")
        or metadata.get("provider_id")
        or ""
    )
    source_url = str(
        selection.get("source_url")
        or metadata.get("source_url")
        or ""
    )
    attribution = selection.get("attribution") or metadata.get("attribution") or ""
    if isinstance(attribution, dict):
        attribution = attribution.get("name", "") or attribution.get("creator", "") or ""
    source_key = provider_id
    if provider == "yt_clip":
        match = _YOUTUBE_VID_RE.search(source_url)
        if match:
            source_key = match.group(1)
    return SourceIdentity(
        provider=provider,
        source_key=source_key,
        creator=str(attribution or ""),
        collection=str(selection.get("collection") or metadata.get("collection") or ""),
        source_url=source_url,
    )


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class SourceContinuityState:
    """Mutable per-documentary continuity tracking."""

    scene_sources: dict[int, SourceIdentity] = field(default_factory=dict)
    switches: list[dict[str, Any]] = field(default_factory=list)
    segment_offsets: dict[str, float] = field(default_factory=dict)
    last_recorded: "SourceIdentity | None" = field(default=None, repr=False, compare=False)

    def record(
        self,
        scene_index: int,
        identity: SourceIdentity,
        *,
        reason: str = "",
        replaced_source: SourceIdentity | None = None,
    ) -> None:
        previous = self.scene_sources.get(scene_index)
        self.scene_sources[scene_index] = identity
        if previous is not None and not previous.matches(identity):
            self.switches.append({
                "scene_index": scene_index,
                "from_source": previous.to_dict(),
                "to_source": identity.to_dict(),
                "reason": reason or "provider switched without continuity benefit",
            })
        elif self.last_recorded is not None and not self.last_recorded.matches(identity):
            self.switches.append({
                "scene_index": scene_index,
                "from_source": self.last_recorded.to_dict(),
                "to_source": identity.to_dict(),
                "reason": reason or "provider switched without continuity benefit",
            })
        self.last_recorded = identity

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for identity in self.scene_sources.values():
            key = identity.identity_key
            if key:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def dominant_identity(self) -> SourceIdentity | None:
        counts = self.source_counts()
        if not counts:
            return None
        top_key = max(counts, key=lambda key: counts[key])
        for identity in self.scene_sources.values():
            if identity.identity_key == top_key:
                return identity
        return None

    def next_segment_offset(self, identity: SourceIdentity, target_duration: float) -> float:
        key = identity.identity_key
        offset = self.segment_offsets.get(key, 0.0)
        self.segment_offsets[key] = offset + float(target_duration) + 3.0
        return offset


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceContinuityReport:
    """Run-level source continuity diagnostics."""

    dominant_source: dict[str, Any]
    scenes_per_source: list[dict[str, Any]]
    continuity_score: float
    average_continuity: float
    source_switches: tuple[dict[str, Any], ...]
    unnecessary_switches: tuple[dict[str, Any], ...]
    enabled: bool
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dominant_source": self.dominant_source,
            "scenes_per_source": self.scenes_per_source,
            "continuity_score": self.continuity_score,
            "average_continuity": self.average_continuity,
            "source_switches": list(self.source_switches),
            "unnecessary_switches": list(self.unnecessary_switches),
            "enabled": self.enabled,
            "config": self.config,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SourceContinuityEngine:
    """Group, score, and bias scene selection toward a dominant source."""

    def __init__(self, config: SourceContinuityConfig | None = None) -> None:
        self.config = config or SourceContinuityConfig()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SourceContinuityEngine":
        return cls(SourceContinuityConfig.from_env(env if env is not None else os.environ))

    # -- selection support ---------------------------------------------------

    def prefer_continuity(
        self,
        intent: "VisualIntent",
        candidates: Sequence[StockCandidate],
        result: MediaSelectionResult,
        state: SourceContinuityState,
        *,
        used_provider_ids: set[str] | None = None,
        target_duration_sec: float = 5.0,
        output_width: int = 1080,
        output_height: int = 1920,
        evidence_engine: Any = None,
        scene_index: int | None = None,
    ) -> tuple[MediaSelectionResult, str]:
        """Return the final selection result and a continuity decision reason.

        The dominant source is preferred only when a candidate from that
        source passes the *same* quality gates (``select_best_candidate``
        with an unchanged ``minimum_score``) and scores within
        ``continuity_bonus`` of the independent best.  Accuracy always wins:
        if no acceptable same-source candidate exists, the original result
        is returned unchanged.
        """
        if not self.config.enabled:
            return result, "disabled"
        if not result.selected_candidate:
            return result, "no selection"
        dominant = state.dominant_identity()
        if dominant is None:
            return result, "no dominant source yet"
        if identity_from_candidate(result.selected_candidate).matches(dominant):
            return result, "already dominant source"
        base_score = float(result.score.score) if result.score is not None else 0.0
        if base_score < self.config.minimum_continuity_score:
            return result, "below minimum continuity score"

        same_source = [
            candidate
            for candidate in candidates
            if identity_from_candidate(candidate).matches(dominant)
        ]
        if not same_source:
            return result, "dominant source not in candidate pool"
        if not self.config.reuse_verified_source and scene_index is not None:
            previous = state.scene_sources.get(scene_index)
            if previous is None or not previous.matches(dominant):
                return result, "source reuse disabled"

        alt = select_best_candidate(
            intent,
            same_source,
            used_provider_ids=used_provider_ids or set(),
            target_duration_sec=target_duration_sec,
            output_width=output_width,
            output_height=output_height,
            minimum_score=max(1.0, _minimum_for_result(result)),
            evidence_engine=evidence_engine,
        )
        if not alt.selected_candidate or alt.score is None:
            return result, "no acceptable dominant-source candidate"
        if alt.score.score < base_score - self.config.continuity_bonus:
            return result, "dominant-source candidate scored worse; accuracy wins"
        reason = "preferred dominant source for continuity"
        return alt, reason

    # -- report support ------------------------------------------------------

    def build_report(
        self,
        state: SourceContinuityState,
        total_scenes: int,
    ) -> SourceContinuityReport:
        counts = state.source_counts()
        dominant = state.dominant_identity()
        scenes_per_source: list[dict[str, Any]] = []
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            identity = next(
                (item for item in state.scene_sources.values() if item.identity_key == key),
                None,
            )
            scene_indices = [
                idx for idx, item in state.scene_sources.items() if item.identity_key == key
            ]
            scenes_per_source.append({
                "identity": identity.to_dict() if identity else {},
                "identity_key": key,
                "scene_count": count,
                "scenes": scene_indices,
                "coverage_ratio": round(count / total_scenes, 3) if total_scenes else 0.0,
            })

        continuity_score = 0.0
        if total_scenes:
            dominant_count = counts.get(dominant.identity_key, 0) if dominant else 0
            continuity_score = round(dominant_count / total_scenes, 3)

        unnecessary = [
            switch for switch in state.switches
            if switch.get("reason") == "provider switched without continuity benefit"
        ]
        return SourceContinuityReport(
            dominant_source=dominant.to_dict() if dominant else {},
            scenes_per_source=scenes_per_source,
            continuity_score=continuity_score,
            average_continuity=round(
                sum(entry["coverage_ratio"] for entry in scenes_per_source) / len(scenes_per_source)
                if scenes_per_source else 1.0,
                3,
            ),
            source_switches=tuple(state.switches),
            unnecessary_switches=tuple(unnecessary),
            enabled=self.config.enabled,
            config=self.config.to_dict(),
        )


def _minimum_for_result(result: MediaSelectionResult) -> float:
    if result.score is None:
        return 1.0
    return float(result.score.score)


__all__ = [
    "SourceContinuityConfig",
    "SourceContinuityEngine",
    "SourceContinuityReport",
    "SourceContinuityState",
    "SourceIdentity",
    "identity_from_candidate",
    "identity_from_selection",
]
