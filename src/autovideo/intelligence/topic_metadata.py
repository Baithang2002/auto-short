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
    category_id: str = "27"

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
        "eagle", "whale", "dolphin", "shark", "octopus", "alligator",
        "antelope", "badger", "bat", "beaver", "bison", "bobcat", "buffalo", "cheetah",
        "chimpanzee", "coyote", "crocodile", "deer", "elephant", "falcon", "giraffe",
        "gorilla", "hare", "hawk", "hippo", "hippopotamus", "jaguar", "kangaroo",
        "koala", "leopard", "lynx", "moose", "orangutan", "otter", "owl", "panda",
        "penguin", "rabbit", "raccoon", "rhino", "rhinoceros", "seal", "snake",
        "turtle", "walrus", "wolverine", "zebra",
    },
    TopicCategory.OCEAN_SCIENCE: {
        "ocean", "oceans", "current", "currents", "sea", "marine", "gulf", "stream",
        "underwater", "tide", "waves", "circulation",
    },
    TopicCategory.EARTH_SCIENCE: {
        "earth", "atmosphere", "magnetic", "magnetosphere", "geology", "planet", "climate",
        "weather", "ocean", "currents", "aurora", "northern", "lights", "lightning",
        "thunder", "thunderstorm",
    },
    TopicCategory.SPACE: {
        "space", "saturn", "mars", "jupiter", "venus", "planet", "planets", "solar",
        "sun", "stars", "galaxy", "nebula", "cosmic", "nasa", "aurora",
    },
    TopicCategory.ASTRONOMY: {
        "astronomy", "planet", "planets", "saturn", "mars", "jupiter", "venus", "stars",
        "galaxy", "nebula", "cosmos", "orbit", "solar",
    },
    TopicCategory.WEATHER: {
        "weather", "storm", "thunderstorm", "thunder", "lightning", "hurricane", "cloud",
        "clouds", "rain", "wind", "monsoon", "cyclone", "aurora",
    },
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
    TopicCategory.NATURE: {
        "nature", "forest", "mountain", "river", "plant", "plants", "earth", "canyon",
        "cave", "desert", "geyser", "glacier", "rainforest", "volcano", "waterfall",
        "wetland", "wildflower",
    },
}

_CATEGORY_HASHTAGS: dict[TopicCategory, tuple[str, ...]] = {
    TopicCategory.TECHNOLOGY: ("#technology", "#innovation", "#qrcode"),
    TopicCategory.HISTORY: ("#history", "#civilization", "#ancientrome"),
    TopicCategory.ENGINEERING: ("#engineering", "#architecture", "#infrastructure"),
    TopicCategory.WILDLIFE: ("#wildlife", "#animals", "#wildanimals"),
    TopicCategory.NATURE: ("#nature", "#naturalworld", "#planetearth"),
    TopicCategory.EARTH_SCIENCE: ("#earthscience", "#planetearth", "#geology"),
    TopicCategory.OCEAN_SCIENCE: ("#ocean", "#oceanscience", "#marinelife"),
    TopicCategory.SPACE: ("#space", "#spaceexploration", "#nasa"),
    TopicCategory.ASTRONOMY: ("#astronomy", "#space", "#cosmos"),
    TopicCategory.WEATHER: ("#weather", "#atmosphere", "#meteorology"),
    TopicCategory.CLIMATE: ("#climate", "#climatescience", "#environment"),
    TopicCategory.GEOGRAPHY: ("#geography", "#maps", "#worldgeography"),
    TopicCategory.PSYCHOLOGY: ("#psychology", "#brain", "#mind"),
    TopicCategory.BIOLOGY: ("#biology", "#life", "#lifescience"),
    TopicCategory.PHYSICS: ("#physics", "#energy", "#physicalscience"),
    TopicCategory.CHEMISTRY: ("#chemistry", "#chemicalreaction", "#molecules"),
    TopicCategory.ENVIRONMENT: ("#environment", "#conservation", "#ecosystem"),
}

_CATEGORY_KEYWORDS: dict[TopicCategory, tuple[str, ...]] = {
    TopicCategory.TECHNOLOGY: ("technology", "innovation", "qr code"),
    TopicCategory.HISTORY: ("history", "ancient rome", "civilization"),
    TopicCategory.ENGINEERING: ("engineering", "architecture", "infrastructure"),
    TopicCategory.WILDLIFE: ("wildlife", "wild animals", "animal behavior"),
    TopicCategory.NATURE: ("nature", "natural world", "planet earth"),
    TopicCategory.EARTH_SCIENCE: ("earth science", "planet earth", "geology"),
    TopicCategory.OCEAN_SCIENCE: ("ocean", "ocean science", "marine science"),
    TopicCategory.SPACE: ("space", "space exploration", "nasa"),
    TopicCategory.ASTRONOMY: ("astronomy", "space", "cosmos"),
    TopicCategory.WEATHER: ("weather", "atmosphere", "meteorology"),
    TopicCategory.CLIMATE: ("climate", "climate science", "environment"),
    TopicCategory.GEOGRAPHY: ("geography", "maps", "world geography"),
    TopicCategory.PSYCHOLOGY: ("psychology", "brain", "mind"),
    TopicCategory.BIOLOGY: ("biology", "life science", "living things"),
    TopicCategory.PHYSICS: ("physics", "energy", "physical science"),
    TopicCategory.CHEMISTRY: ("chemistry", "chemical reactions", "molecules"),
    TopicCategory.ENVIRONMENT: ("environment", "conservation", "ecosystem"),
}

_GENERAL_HASHTAGS = ("#shorts",)
_GENERAL_KEYWORDS = ("shorts",)
_TARGETED_TOPIC_PHRASES: tuple[tuple[str, str], ...] = (
    ("arctic fox", "arcticfox"),
    ("red fox", "redfox"),
    ("polar bear", "polarbear"),
    ("red panda", "redpanda"),
    ("snow leopard", "snowleopard"),
    ("whale shark", "whaleshark"),
    ("northern lights", "northernlights"),
    ("ocean current", "oceancurrents"),
    ("roman aqueduct", "romanaqueducts"),
    ("qr code", "qrcode"),
)
_TOPIC_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "behind", "by", "for", "from", "how",
    "in", "inside", "is", "it", "of", "on", "or", "the", "their", "these", "this", "to",
    "was", "were", "what", "when", "where", "why", "with",
}
_KNOWN_TITLE_SUFFIXES = tuple(category.value for category in TopicCategory) + (
    "Animals",
    "Ocean",
    "Facts",
    "Education",
)
DEFAULT_YOUTUBE_CATEGORY_ID = "27"
YOUTUBE_PETS_ANIMALS_CATEGORY_ID = "15"
YOUTUBE_SCIENCE_TECHNOLOGY_CATEGORY_ID = "28"
_YOUTUBE_SCIENCE_CATEGORIES = {
    TopicCategory.NATURE,
    TopicCategory.EARTH_SCIENCE,
    TopicCategory.OCEAN_SCIENCE,
    TopicCategory.SPACE,
    TopicCategory.ASTRONOMY,
    TopicCategory.WEATHER,
    TopicCategory.CLIMATE,
    TopicCategory.ENVIRONMENT,
}
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
    TopicCategory.WEATHER: {TopicCategory.EARTH_SCIENCE, TopicCategory.PHYSICS, TopicCategory.CLIMATE},
}


def classify_topic(
    topic: str,
    *,
    title: str = "",
    segments: Sequence[Mapping[str, object]] | None = None,
) -> TopicClassification:
    """Classify a video topic into primary and secondary metadata categories."""

    # The public topic and title define the video's subject. Segment text often
    # mentions incidental places, animals, or mechanisms and must not override it.
    tokens = set(_tokens(" ".join((topic, _strip_known_suffixes(title)))))
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
    focus_text = " ".join((video_topic, clean_title))
    hashtags = _dedupe_hashtags(
        _GENERAL_HASHTAGS
        + _category_hashtags(classification)
        + _topic_hashtags(focus_text)
        + _filter_existing_hashtags(_coerce_hashtags(existing_hashtags), classification)
    )
    keywords = _dedupe_keywords(
        _GENERAL_KEYWORDS
        + _category_keywords(classification)
        + _matched_topic_phrases(focus_text)
        + _topic_keywords(video_topic)
    )
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
        category_id=youtube_category_id_for(
            classification,
            topic=video_topic,
            title=title,
        ),
    )


def youtube_category_id_for(
    classification: TopicClassification,
    *,
    topic: str = "",
    title: str = "",
) -> str:
    """Map focused topic classifications to public YouTube category IDs."""
    if classification.primary == TopicCategory.WILDLIFE:
        return YOUTUBE_PETS_ANIMALS_CATEGORY_ID
    if classification.primary not in _YOUTUBE_SCIENCE_CATEGORIES:
        return DEFAULT_YOUTUBE_CATEGORY_ID
    if classification.primary == TopicCategory.NATURE:
        focus_tokens = set(_tokens(" ".join((topic, _strip_known_suffixes(title)))))
        if not focus_tokens & _CATEGORY_TERMS[TopicCategory.NATURE]:
            return DEFAULT_YOUTUBE_CATEGORY_ID
    return YOUTUBE_SCIENCE_TECHNOLOGY_CATEGORY_ID


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
    if tokens & {"lightning", "thunder", "thunderstorm", "storm"}:
        boosted[TopicCategory.WEATHER] = boosted.get(TopicCategory.WEATHER, 0) + 5
        boosted[TopicCategory.EARTH_SCIENCE] = boosted.get(TopicCategory.EARTH_SCIENCE, 0) + 2
    if "roman" in tokens or "aqueduct" in tokens or "aqueducts" in tokens:
        boosted[TopicCategory.HISTORY] = boosted.get(TopicCategory.HISTORY, 0) + 5
        boosted[TopicCategory.ENGINEERING] = boosted.get(TopicCategory.ENGINEERING, 0) + 3
    if tokens & _CATEGORY_TERMS[TopicCategory.WILDLIFE]:
        boosted[TopicCategory.WILDLIFE] = boosted.get(TopicCategory.WILDLIFE, 0) + 4
    return boosted


def _topic_title(title: str, classification: TopicClassification) -> str:
    del classification
    return _strip_known_suffixes(title)


def _strip_known_suffixes(title: str) -> str:
    clean = str(title or "").strip()
    suffixes = "|".join(
        re.escape(suffix)
        for suffix in sorted(_KNOWN_TITLE_SUFFIXES, key=len, reverse=True)
    )
    return re.sub(
        rf"(?:\s*\|\s*(?:{suffixes}))+\s*$",
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
    normalized = " ".join(_tokens(topic))
    words = [word for word in _tokens(topic) if len(word) > 2 and word not in _TOPIC_STOPWORDS]
    phrases = [normalized] if normalized else []
    phrases.extend(
        phrase
        for phrase, _hashtag in _TARGETED_TOPIC_PHRASES
        if _contains_phrase(normalized, phrase)
    )
    return tuple(phrases + words)


def _topic_hashtags(topic: str) -> tuple[str, ...]:
    normalized = " ".join(_tokens(topic))
    return tuple(
        f"#{hashtag}"
        for phrase, hashtag in _TARGETED_TOPIC_PHRASES
        if _contains_phrase(normalized, phrase)
    )


def _matched_topic_phrases(text: str) -> tuple[str, ...]:
    normalized = " ".join(_tokens(text))
    return tuple(
        phrase
        for phrase, _hashtag in _TARGETED_TOPIC_PHRASES
        if _contains_phrase(normalized, phrase)
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?:^|\s){re.escape(phrase)}(?:s)?(?:\s|$)", text))


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
        "#weather": TopicCategory.WEATHER,
        "#climate": TopicCategory.CLIMATE,
        "#geography": TopicCategory.GEOGRAPHY,
        "#psychology": TopicCategory.PSYCHOLOGY,
        "#biology": TopicCategory.BIOLOGY,
        "#chemistry": TopicCategory.CHEMISTRY,
        "#environment": TopicCategory.ENVIRONMENT,
        "#engineering": TopicCategory.ENGINEERING,
    }
    generic_academic = {"#education", "#learn", "#science"}
    filtered: list[str] = []
    for tag in tags:
        normalized = _normalize_hashtag(tag)
        if normalized in generic_academic:
            continue
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
