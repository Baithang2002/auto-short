"""Validated, retrieval-focused topic cards for nature documentaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


PILLAR_ALLOCATION: Mapping[str, float] = MappingProxyType({
    "wildlife": 0.80,
    "ocean": 0.20,
})
DEFAULT_TOPIC_CARD_PATH = Path(__file__).resolve().parent / "knowledge" / "focused_nature_topic_cards.json"
DEFAULT_TOPIC_CARD_SOURCE = "src/autovideo/intelligence/knowledge/focused_nature_topic_cards.json"


@dataclass(frozen=True)
class TopicCard:
    """One nature topic with enough structure to drive visual retrieval."""

    id: str
    pillar: str
    subject: str
    premise: str
    required_entity: str
    required_action: str
    hook_queries: tuple[str, ...]
    reveal_queries: tuple[str, ...]
    supporting_queries: tuple[str, ...]
    fallback_visuals: tuple[str, ...]
    title_angles: tuple[str, ...]
    source_difficulty: str
    recommended_duration_sec: int = 48

    @property
    def topic(self) -> str:
        """Return the standalone premise consumed by existing topic pipelines."""

        return self.premise


@dataclass(frozen=True)
class TopicCardCatalog:
    """A validated card collection and its editorial pillar allocation."""

    cards: tuple[TopicCard, ...]
    allocation: Mapping[str, float]


def load_topic_card_catalog(path: Path = DEFAULT_TOPIC_CARD_PATH) -> TopicCardCatalog:
    """Load a card catalog, rejecting incomplete retrieval plans and duplicate IDs."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a topic-card object")

    allocation = _parse_allocation(payload.get("allocation"), path)
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise ValueError(f"{path} must contain a nonempty cards list")

    cards: list[TopicCard] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_cards):
        if not isinstance(raw, dict):
            raise ValueError(f"{path} card {index} must be an object")
        card = _parse_card(raw, path, index)
        card_key = card.id.casefold()
        if card_key in seen_ids:
            raise ValueError(f"{path} contains duplicate topic-card id {card.id!r}")
        seen_ids.add(card_key)
        cards.append(card)
    return TopicCardCatalog(cards=tuple(cards), allocation=MappingProxyType(allocation))


def load_topic_cards(path: Path = DEFAULT_TOPIC_CARD_PATH) -> tuple[TopicCard, ...]:
    """Load only the cards when allocation metadata is not needed by the caller."""

    return load_topic_card_catalog(path).cards


def find_topic_card(topic: str, path: Path = DEFAULT_TOPIC_CARD_PATH) -> TopicCard | None:
    """Return the card whose premise exactly matches a normalized topic."""

    key = _normalize_topic(topic)
    if not key:
        return None
    return next(
        (card for card in load_topic_cards(path) if _normalize_topic(card.premise) == key),
        None,
    )


def _parse_allocation(raw: Any, path: Path) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain allocation metadata")
    if set(raw) != set(PILLAR_ALLOCATION):
        raise ValueError(f"{path} allocation must define exactly {tuple(PILLAR_ALLOCATION)}")

    allocation: dict[str, float] = {}
    for pillar, expected in PILLAR_ALLOCATION.items():
        value = raw.get(pillar)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} allocation for {pillar!r} must be numeric")
        allocation[pillar] = float(value)
        if abs(allocation[pillar] - expected) > 1e-9:
            raise ValueError(f"{path} allocation for {pillar!r} must be {expected:.0%}")
    return allocation


def _parse_card(raw: Mapping[str, Any], path: Path, index: int) -> TopicCard:
    location = f"{path} card {index}"
    pillar = _required_string(raw, "pillar", location)
    if pillar not in PILLAR_ALLOCATION:
        raise ValueError(f"{location} has unsupported pillar {pillar!r}")
    return TopicCard(
        id=_required_string(raw, "id", location),
        pillar=pillar,
        subject=_required_string(raw, "subject", location),
        premise=_required_string(raw, "premise", location),
        required_entity=_required_string(raw, "required_entity", location),
        required_action=_required_string(raw, "required_action", location),
        hook_queries=_required_strings(raw, "hook_queries", location),
        reveal_queries=_required_strings(raw, "reveal_queries", location),
        supporting_queries=_required_strings(raw, "supporting_queries", location),
        fallback_visuals=_required_strings(raw, "fallback_visuals", location),
        title_angles=_required_strings(raw, "title_angles", location),
        source_difficulty=_required_string(raw, "source_difficulty", location),
        recommended_duration_sec=_duration(raw.get("recommended_duration_sec", 48), location),
    )


def _required_string(raw: Mapping[str, Any], field: str, location: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} field {field!r} must be a nonempty string")
    return value.strip()


def _required_strings(raw: Mapping[str, Any], field: str, location: str) -> tuple[str, ...]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{location} field {field!r} must be a nonempty string list")
    cleaned = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(cleaned) != len(value):
        raise ValueError(f"{location} field {field!r} must contain only nonempty strings")
    return cleaned


def _duration(value: Any, location: str) -> int:
    """Parse an advisory story-length hint.

    ``recommended_duration_sec`` is a soft hint for planning/logging only.
    The story decides the finished length, so no hard window is enforced;
    the value only needs to be a non-negative number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} field 'recommended_duration_sec' must be numeric")
    duration = int(value)
    if duration < 0:
        raise ValueError(f"{location} field 'recommended_duration_sec' must be non-negative")
    return duration


def _normalize_topic(value: str) -> str:
    return " ".join(str(value or "").casefold().split())
