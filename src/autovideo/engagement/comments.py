"""Generate topic-specific pinned comments for published videos."""

from __future__ import annotations

import hashlib
import re
from typing import Mapping, Sequence


def generate_pinned_comment(
    *,
    topic: str,
    title: str = "",
    segments: Sequence[Mapping[str, object]] | None = None,
    allow_emojis: bool = True,
) -> str:
    """Create a deterministic but varied pinned comment for a video topic."""

    topic_text = _clean(topic or title or "this topic")
    category = _category(topic_text, segments or ())
    fact = _fact_line(category, topic_text, allow_emojis)
    question = _question(category, topic_text)
    cta_options = [
        "Tell me below.",
        "Drop your answer in the comments.",
        "Comment your pick for the next video.",
    ]
    cta = _pick(cta_options, topic_text + category)
    return f"{fact}\n\n{question}\n\n{cta}"


def _category(topic: str, segments: Sequence[Mapping[str, object]]) -> str:
    text = " ".join([
        topic,
        " ".join(str(segment.get("narration", "")) for segment in segments),
    ]).lower()
    if any(term in text for term in ("octopus", "shark", "penguin", "bee", "animal", "wildlife")):
        return "nature"
    if any(term in text for term in ("space", "planet", "saturn", "aurora", "solar", "galaxy")):
        return "space"
    if any(term in text for term in ("roman", "ancient", "empire", "titanic", "civilization")):
        return "history"
    if any(term in text for term in ("qr", "internet", "technology", "code", "computer")):
        return "technology"
    if any(term in text for term in ("ocean", "volcano", "lightning", "weather", "earth")):
        return "earth_science"
    return "general"


def _fact_line(category: str, topic: str, allow_emojis: bool) -> str:
    emoji = {
        "nature": "🐙 ",
        "space": "🌌 ",
        "history": "🏛️ ",
        "technology": "📱 ",
        "earth_science": "🌍 ",
        "general": "✨ ",
    }.get(category, "") if allow_emojis else ""
    templates = {
        "nature": [
            "{emoji}Nature is full of survival tricks hiding in plain sight.",
            "{emoji}Animals are often stranger than they look at first.",
        ],
        "space": [
            "{emoji}Space gets more surprising the closer you look.",
            "{emoji}The universe keeps turning simple questions into huge ones.",
        ],
        "history": [
            "{emoji}History is full of details that still shape the world today.",
            "{emoji}The past gets more interesting when you look closely.",
        ],
        "technology": [
            "{emoji}Everyday technology has more hidden design than most people notice.",
            "{emoji}Simple-looking tech often has a clever system underneath.",
        ],
        "earth_science": [
            "{emoji}Earth is always moving, building, and changing around us.",
            "{emoji}The planet has hidden systems working every second.",
        ],
        "general": [
            "{emoji}This topic has more going on than it seems.",
            "{emoji}Small details can completely change how you see this.",
        ],
    }
    return _pick(templates[category], topic).format(emoji=emoji)


def _question(category: str, topic: str) -> str:
    questions = {
        "nature": [
            "What animal should I cover next?",
            "Which sea creature or wild animal should be next?",
        ],
        "space": [
            "What space topic should be next?",
            "Which planet, moon, or cosmic mystery should I cover next?",
        ],
        "history": [
            "Which historical mystery fascinates you the most?",
            "What ancient engineering story should I cover next?",
        ],
        "technology": [
            "What everyday technology should I explain next?",
            "Which hidden tech system should be next?",
        ],
        "earth_science": [
            "What Earth science topic should I cover next?",
            "Which natural force should be next?",
        ],
        "general": [
            "What should I cover next?",
            "Which topic should be next?",
        ],
    }
    return _pick(questions[category], topic + "question")


def _pick(options: Sequence[str], seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
