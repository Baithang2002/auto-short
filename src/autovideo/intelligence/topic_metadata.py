"""Deterministic topic classification and upload metadata helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence


class TopicCategory(str, Enum):
    """Supported high-level topic categories for video metadata."""

    WILDLIFE = "Wildlife"
    NATURE = "Nature"
    EARTH_SCIENCE = "Earth Science"
    OCEAN_SCIENCE = "Ocean Science"
    SPACE = "Space"
    ASTRONOMY = "Astronomy"
    TECHNOLOGY = "Technology"
    HISTORY = "History"
    GEOGRAPHY = "Geography"
    CLIMATE = "Climate"
    WEATHER = "Weather"
    ENGINEERING = "Engineering"
    PSYCHOLOGY = "Psychology"
    BIOLOGY = "Biology"
    PHYSICS = "Physics"
    CHEMISTRY = "Chemistry"
    ENVIRONMENT = "Environment"


@dataclass(frozen=True)
class TopicClassification:
    """Primary and secondary topic categories inferred from a video topic."""

    primary: TopicCategory
    secondary: tuple[TopicCategory, ...] = ()

    @property
    def all_categories(self) -> tuple[TopicCategory, ...]:
        return (self.primary, *self.secondary)


@dataclass(frozen=True)
class TopicMetadata:
    """Topic-aware metadata values that preserve the legacy output shape."""

    classification: TopicClassification
    title: str
    description: str
    instagram_caption: str
    hashtags: tuple[str, ...]
    keywords: tuple[str, ...]

    @property
    def youtube_tags(self) -> str:
        return ",".join(self.keywords)


_CATEGORY_TERMS: dict[TopicCategory, set[str]] = {
    TopicCategory.TECHNOLOGY: {
        "qr", "code", "codes", "barcode", "computer", "phone", "screen", "data", "digital",
        "algorithm", "software", "internet", "robot", "chip", "ai", "technology",
    },
    TopicCategory.HISTORY: {
        "roman", "rome", "empire", "ancient", "history", "civilization", "civilisation",
        "aqueduct", "aqueducts", "road", "roads", "medieval", "archaeology",
    },
    TopicCategory.ENGINEERING: {
        "engineering", "engineer", "built", "build", "bridge", "road", "roads", "aqueduct",
        "aqueducts", "concrete", "structure", "structures", "architecture", "design",
    },
    TopicCategory.WILDLIFE: {
        "fox", "animal", "animals", "wildlife", "bear", "wolf", "lion", "tiger", "bird",
        "eagle", "whale", "dolphin", "shark", "octopus", "arctic",
    },
    TopicCategory.OCEAN_SCIENCE: {
        "ocean", "oceans", "current", "currents", "sea", "marine", "gulf", "stream",
        "underwater", "tide", "waves", "circulation",
    },
    TopicCategory.EARTH_SCIENCE: {
        "earth", "atmosphere", "magnetic", "magnetosphere", "geology", "planet", "climate",
        "weather", "ocean", "currents", "aurora", "northern", "lights",
    },
    TopicCategory.SPACE: {
        "space", "saturn", "mars", "jupiter", "venus", "planet", "planets", "solar",
        "sun", "stars", "galaxy", "nebula", "cosmic", "nasa", "aurora",
    },
    TopicCategory.ASTRONOMY: {
        "astronomy", "planet", "planets", "saturn", "mars", "jupiter", "venus", "stars",
        "galaxy", "nebula", "cosmos", "orbit", "solar",
    },
    TopicCategory.WEATHER: {"weather", "storm", "hurricane", "cloud", "rain", "wind", "aurora"},
    TopicCategory.CLIMATE: {"climate", "temperature", "warming", "ice", "glacier", "carbon"},
    TopicCategory.PSYCHOLOGY: {
        "brain", "memory", "memories", "psychology", "embarrassing", "emotion", "mind",
        "behavior", "behaviour",
    },
    TopicCategory.BIOLOGY: {
        "biology", "cell", "cells", "dna", "evolution", "species", "body", "animal", "life",
    },
    TopicCategory.PHYSICS: {
        "physics", "gravity", "force", "energy", "motion", "light", "particle", "particles",
        "magnetic", "solar",
    },
    TopicCategory.CHEMISTRY: {"chemistry", "chemical", "molecule", "molecules", "reaction"},
    TopicCategory.GEOGRAPHY: {"map", "maps", "continent", "country", "river", "mountain", "earth"},
    TopicCategory.ENVIRONMENT: {
        "environment", "ecosystem", "pollution", "conservation", "forest", "habitat", "nature",
    },
    TopicCategory.NATURE: {"nature", "forest", "mountain", "river", "plant", "plants", "earth"},
}

_CATEGORY_HASHTAGS: dict[TopicCategory, tuple[str, ...]] = {
    TopicCategory.TECHNOLOGY: ("#technology", "#innovation", "#qrcode", "#science"),
    TopicCategory.HISTORY: ("#history", "#civilization", "#ancientrome", "#education"),
    TopicCategory.ENGINEERING: ("#engineering", "#architecture", "#infrastructure", "#science"),
    TopicCategory.WILDLIFE: ("#wildlife", "#nature", "#animals", "#arctic"),
    TopicCategory.NATURE: ("#nature", "#earth", "#science", "#didyouknow"),
    TopicCategory.EARTH_SCIENCE: ("#earth", "#earthscience", "#science", "#planetearth"),
    TopicCategory.OCEAN_SCIENCE: ("#ocean", "#oceanscience", "#earth", "#science"),
    TopicCategory.SPACE: ("#space", "#science", "#earth", "#nasa"),
    TopicCategory.ASTRONOMY: ("#astronomy", "#space", "#science", "#cosmos"),
    TopicCategory.WEATHER: ("#weather", "#earth", "#science", "#atmosphere"),
    TopicCategory.CLIMATE: ("#climate", "#earth", "#science", "#environment"),
    TopicCategory.GEOGRAPHY: ("#geography", "#earth", "#maps", "#education"),
    TopicCategory.PSYCHOLOGY: ("#psychology", "#brain", "#science", "#mind"),
    TopicCategory.BIOLOGY: ("#biology", "#science", "#life", "#nature"),
    TopicCategory.PHYSICS: ("#physics", "#science", "#energy", "#learn"),
    TopicCategory.CHEMISTRY: ("#chemistry", "#science", "#learn", "#education"),
    TopicCategory.ENVIRONMENT: ("#environment", "#earth", "#nature", "#science"),
}

_CATEGORY_KEYWORDS: dict[TopicCategory, tuple[str, ...]] = {
    TopicCategory.TECHNOLOGY: ("technology", "innovation", "qr code", "science"),
    TopicCategory.HISTORY: ("history", "ancient rome", "civilization", "education"),
    TopicCategory.ENGINEERING: ("engineering", "architecture", "infrastructure", "science"),
    TopicCategory.WILDLIFE: ("wildlife", "nature", "animals", "arctic"),
    TopicCategory.NATURE: ("nature", "earth", "science", "facts"),
    TopicCategory.EARTH_SCIENCE: ("earth", "earth science", "science", "planet earth"),
    TopicCategory.OCEAN_SCIENCE: ("ocean", "ocean science", "earth", "science"),
    TopicCategory.SPACE: ("space", "science", "earth", "nasa"),
    TopicCategory.ASTRONOMY: ("astronomy", "space", "science", "cosmos"),
    TopicCategory.WEATHER: ("weather", "earth", "science", "atmosphere"),
    TopicCategory.CLIMATE: ("climate", "earth", "science", "environment"),
    TopicCategory.GEOGRAPHY: ("geography", "earth", "maps", "education"),
    TopicCategory.PSYCHOLOGY: ("psychology", "brain", "science", "mind"),
    TopicCategory.BIOLOGY: ("biology", "science", "life", "nature"),
    TopicCategory.PHYSICS: ("physics", "science", "energy", "learn"),
    TopicCategory.CHEMISTRY: ("chemistry", "science", "learn", "education"),
    TopicCategory.ENVIRONMENT: ("environment", "earth", "nature", "science"),
}

_GENERAL_HASHTAGS = ("#shorts", "#facts", "#didyouknow", "#science", "#education")
_GENERAL_KEYWORDS = ("shorts", "facts", "did you know", "science", "education")
_ALLOWED_SECONDARY: dict[TopicCategory, set[TopicCategory]] = {
    TopicCategory.HISTORY: {TopicCategory.ENGINEERING, TopicCategory.GEOGRAPHY},
    TopicCategory.SPACE: {TopicCategory.EARTH_SCIENCE, TopicCategory.ASTRONOMY, TopicCategory.PHYSICS},
    TopicCategory.OCEAN_SCIENCE: {TopicCategory.EARTH_SCIENCE, TopicCategory.CLIMATE},
    TopicCategory.WILDLIFE: {TopicCategory.NATURE, TopicCategory.BIOLOGY, TopicCategory.ENVIRONMENT},
    TopicCategory.TECHNOLOGY: {TopicCategory.ENGINEERING, TopicCategory.PHYSICS},
    TopicCategory.EARTH_SCIENCE: {
        TopicCategory.SPACE,
        TopicCategory.OCEAN_SCIENCE,
        TopicCategory.WEATHER,
        TopicCategory.CLIMATE,
    },
}


def classify_topic(
    topic: str,
    *,
    title: str = "",
    segments: Sequence[Mapping[str, object]] | None = None,
) -> TopicClassification:
    """Classify a video topic into primary and secondary metadata categories."""

    text_parts = [topic, title]
    for segment in segments or ():
        text_parts.append(str(segment.get("narration", "")))
        text_parts.append(str(segment.get("broll", "")))
    tokens = set(_tokens(" ".join(text_parts)))
    scores: dict[TopicCategory, int] = {}
    for category, terms in _CATEGORY_TERMS.items():
        score = len(tokens & terms)
        if score:
            scores[category] = score

    scores = _apply_category_boosts(tokens, scores)
    if not scores:
        return TopicClassification(TopicCategory.NATURE, ())

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
    primary = ordered[0][0]
    allowed_secondary = _ALLOWED_SECONDARY.get(primary, set())
    secondary = tuple(
        category
        for category, score in ordered[1:]
        if score >= 2 and category != primary and category in allowed_secondary
    )
    return TopicClassification(primary=primary, secondary=secondary[:3])


def build_topic_metadata(
    *,
    video_topic: str,
    title: str,
    description: str = "",
    instagram_caption: str = "",
    segments: Sequence[Mapping[str, object]] | None = None,
    existing_hashtags: Iterable[str] = (),
) -> TopicMetadata:
    """Build topic-aware upload metadata while preserving the legacy fields."""

    classification = classify_topic(video_topic, title=title, segments=segments)
    clean_title = _topic_title(title or video_topic, classification)
    hashtags = _dedupe_hashtags(
        _GENERAL_HASHTAGS
        + _category_hashtags(classification)
        + _filter_existing_hashtags(_coerce_hashtags(existing_hashtags), classification)
    )
    keywords = _dedupe_keywords(_GENERAL_KEYWORDS + _category_keywords(classification) + _topic_keywords(video_topic))
    description_text = _description_for(
        video_topic=video_topic,
        title=clean_title,
        description=description,
        classification=classification,
    )
    caption_text = _instagram_caption_for(
        video_topic=video_topic,
        instagram_caption=instagram_caption,
        classification=classification,
    )
    return TopicMetadata(
        classification=classification,
        title=clean_title,
        description=description_text,
        instagram_caption=caption_text,
        hashtags=hashtags[:15],
        keywords=keywords[:15],
    )


def _apply_category_boosts(
    tokens: set[str],
    scores: dict[TopicCategory, int],
) -> dict[TopicCategory, int]:
    boosted = dict(scores)
    if {"qr", "code"} <= tokens or "qrcode" in tokens:
        boosted[TopicCategory.TECHNOLOGY] = boosted.get(TopicCategory.TECHNOLOGY, 0) + 6
    if {"northern", "lights"} <= tokens or "aurora" in tokens:
        boosted[TopicCategory.SPACE] = boosted.get(TopicCategory.SPACE, 0) + 7
        boosted[TopicCategory.EARTH_SCIENCE] = boosted.get(TopicCategory.EARTH_SCIENCE, 0) + 3
    if "ocean" in tokens and ("current" in tokens or "currents" in tokens):
        boosted[TopicCategory.OCEAN_SCIENCE] = boosted.get(TopicCategory.OCEAN_SCIENCE, 0) + 5
        boosted[TopicCategory.EARTH_SCIENCE] = boosted.get(TopicCategory.EARTH_SCIENCE, 0) + 2
    if "roman" in tokens or "aqueduct" in tokens or "aqueducts" in tokens:
        boosted[TopicCategory.HISTORY] = boosted.get(TopicCategory.HISTORY, 0) + 5
        boosted[TopicCategory.ENGINEERING] = boosted.get(TopicCategory.ENGINEERING, 0) + 3
    if "fox" in tokens or "wildlife" in tokens:
        boosted[TopicCategory.WILDLIFE] = boosted.get(TopicCategory.WILDLIFE, 0) + 4
    return boosted


def _topic_title(title: str, classification: TopicClassification) -> str:
    clean = _strip_known_suffixes(title)
    category_label = classification.primary.value
    if category_label.lower() in clean.lower():
        return clean
    candidate = f"{clean} | {category_label}"
    return candidate if len(candidate) <= 95 else clean


def _strip_known_suffixes(title: str) -> str:
    clean = str(title or "").strip()
    return re.sub(
        r"\s*\|\s*(Nature|Wildlife|Animals|Space|Ocean|History|Facts|Technology)\s*$",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()


def _description_for(
    *,
    video_topic: str,
    title: str,
    description: str,
    classification: TopicClassification,
) -> str:
    base = str(description or "").strip()
    if base:
        return base
    category = classification.primary.value.lower()
    return f"{title} explains {video_topic} through a concise {category} story built for Shorts."


def _instagram_caption_for(
    *,
    video_topic: str,
    instagram_caption: str,
    classification: TopicClassification,
) -> str:
    base = str(instagram_caption or "").strip()
    if base:
        return base
    return f"A quick {classification.primary.value.lower()} explainer: {video_topic}."


def _category_hashtags(classification: TopicClassification) -> tuple[str, ...]:
    tags: list[str] = []
    for category in classification.all_categories:
        tags.extend(_CATEGORY_HASHTAGS.get(category, ()))
    return tuple(tags)


def _category_keywords(classification: TopicClassification) -> tuple[str, ...]:
    keywords: list[str] = []
    for category in classification.all_categories:
        keywords.extend(_CATEGORY_KEYWORDS.get(category, ()))
    return tuple(keywords)


def _topic_keywords(topic: str) -> tuple[str, ...]:
    words = [word for word in _tokens(topic) if len(word) > 2]
    phrases = [" ".join(words[:3])] if len(words) >= 2 else []
    return tuple(phrases + words)


def _dedupe_hashtags(tags: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for tag in tags:
        normalized = _normalize_hashtag(tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return tuple(output)


def _coerce_hashtags(tags: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(tags, str):
        return tuple(part for part in re.split(r"[\s,]+", tags) if part.strip())
    return tuple(tags)


def _filter_existing_hashtags(
    tags: Iterable[str],
    classification: TopicClassification,
) -> tuple[str, ...]:
    allowed_categories = set(classification.all_categories)
    broad_map = {
        "#nature": TopicCategory.NATURE,
        "#wildlife": TopicCategory.WILDLIFE,
        "#animals": TopicCategory.WILDLIFE,
        "#earth": TopicCategory.EARTH_SCIENCE,
        "#space": TopicCategory.SPACE,
        "#ocean": TopicCategory.OCEAN_SCIENCE,
        "#history": TopicCategory.HISTORY,
        "#physics": TopicCategory.PHYSICS,
        "#energy": TopicCategory.PHYSICS,
        "#technology": TopicCategory.TECHNOLOGY,
        "#qrcode": TopicCategory.TECHNOLOGY,
    }
    filtered: list[str] = []
    for tag in tags:
        normalized = _normalize_hashtag(tag)
        category = broad_map.get(normalized)
        if category is not None and category not in allowed_categories:
            continue
        filtered.append(normalized)
    return tuple(filtered)


def _dedupe_keywords(keywords: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for keyword in keywords:
        cleaned = " ".join(str(keyword or "").lower().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return tuple(output)


def _normalize_hashtag(tag: str) -> str:
    raw = str(tag or "").strip().lower()
    if not raw:
        return ""
    raw = raw if raw.startswith("#") else f"#{raw}"
    compact = re.sub(r"[^a-z0-9#]+", "", raw)
    return compact if compact != "#" else ""


def _tokens(text: str) -> list[str]:
    return [token for token in re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split() if token]
