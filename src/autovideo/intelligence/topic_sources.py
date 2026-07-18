"""Composable filesystem-backed topic sources for autonomous scheduling."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class TopicCandidate:
    """One normalized topic candidate and the source that supplied it."""

    topic: str
    source: str


class TopicSource(ABC):
    """Interface for a source of candidate documentary topics."""

    @abstractmethod
    def load(self) -> tuple[TopicCandidate, ...]:
        """Return normalized candidates from this source."""


@dataclass(frozen=True)
class TextTopicSource(TopicSource):
    """Read one topic per line, ignoring blank lines and comments."""

    path: Path

    def load(self) -> tuple[TopicCandidate, ...]:
        if not self.path.exists():
            return ()
        return _dedupe_candidates(
            TopicCandidate(topic=line, source=self.path.name)
            for raw in self.path.read_text(encoding="utf-8").splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        )


@dataclass(frozen=True)
class JsonTopicSource(TopicSource):
    """Read topics from a JSON list or an object containing a ``topics`` list."""

    path: Path

    def load(self) -> tuple[TopicCandidate, ...]:
        if not self.path.exists():
            return ()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        entries = payload.get("topics", ()) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ValueError(f"{self.path} must contain a topic list or a topics list")
        return _dedupe_candidates(
            candidate
            for item in entries
            if (candidate := _candidate_from_json(item, self.path.name)) is not None
        )


def load_topic_sources(sources: Iterable[TopicSource]) -> tuple[TopicCandidate, ...]:
    """Load and deduplicate candidates from independently configured sources."""

    return _dedupe_candidates(candidate for source in sources for candidate in source.load())


def topic_source_for_path(path: Path) -> TopicSource:
    """Create the built-in source appropriate for a filesystem path."""

    if path.suffix.lower() == ".json":
        return JsonTopicSource(path)
    return TextTopicSource(path)


def _candidate_from_json(item: Any, source: str) -> TopicCandidate | None:
    if isinstance(item, str):
        topic = item.strip()
    elif isinstance(item, dict):
        topic = str(item.get("topic", "")).strip()
    else:
        return None
    return TopicCandidate(topic=topic, source=source) if topic else None


def _dedupe_candidates(candidates: Iterable[TopicCandidate]) -> tuple[TopicCandidate, ...]:
    seen: set[str] = set()
    result: list[TopicCandidate] = []
    for candidate in candidates:
        key = " ".join(candidate.topic.lower().split())
        if key and key not in seen:
            seen.add(key)
            result.append(candidate)
    return tuple(result)
