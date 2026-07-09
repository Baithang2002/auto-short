"""Deterministic media selection for script segments.

This module scores provider metadata only. It intentionally does not use LLMs,
computer vision, frame extraction, or network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "their",
    "this",
    "to",
    "with",
}
_SHOT_TERMS = {
    "aerial",
    "close",
    "closeup",
    "detail",
    "macro",
    "tracking",
    "wide",
}
_ACTION_TERMS = {
    "build",
    "building",
    "changing",
    "dig",
    "digging",
    "eat",
    "eating",
    "fall",
    "falling",
    "hunt",
    "hunting",
    "jump",
    "jumping",
    "listen",
    "listening",
    "pounce",
    "pouncing",
    "remember",
    "scan",
    "scanning",
    "survive",
    "survives",
    "walk",
    "walking",
    "work",
    "working",
}
_ENVIRONMENT_TERMS = {
    "ancient",
    "arctic",
    "city",
    "desert",
    "forest",
    "ice",
    "ocean",
    "polar",
    "road",
    "roman",
    "saturn",
    "snow",
    "space",
    "summer",
    "tundra",
    "underwater",
    "winter",
}
_GENERIC_TERMS = {
    "animal",
    "animals",
    "background",
    "documentary",
    "landscape",
    "nature",
    "scenery",
    "stock",
    "wildlife",
}
_GENERIC_LANDSCAPE_TERMS = {
    "beach",
    "coast",
    "coastline",
    "drone",
    "landscape",
    "mountain",
    "mountains",
    "nature",
    "ocean",
    "scenery",
    "sunset",
    "travel",
    "vacation",
    "waves",
}
_GENERIC_PEOPLE_TERMS = {
    "business",
    "crowd",
    "family",
    "friends",
    "girl",
    "lifestyle",
    "man",
    "office",
    "people",
    "person",
    "student",
    "team",
    "tourist",
    "travel",
    "woman",
    "workplace",
}
_GENERIC_TECH_TERMS = {
    "abstract",
    "binary",
    "blue",
    "data",
    "digital",
    "network",
    "server",
    "technology",
}
_SPECIFIC_VISUAL_TERMS = {
    "astronomy",
    "aurora",
    "bridge",
    "code",
    "current",
    "diagram",
    "earth",
    "engineering",
    "flow",
    "history",
    "map",
    "mechanism",
    "ocean",
    "qr",
    "roman",
    "satellite",
    "science",
    "solar",
}
_QR_VISUAL_TERMS = {
    "qr",
    "qrcode",
    "quickresponse",
}
_BAD_TERMS = {
    "human": -4.0,
    "person": -4.0,
    "people": -4.0,
    "man": -4.0,
    "woman": -4.0,
    "boy": -4.0,
    "girl": -4.0,
    "dog": -4.0,
    "cat": -4.0,
    "pet": -4.0,
    "pets": -4.0,
    "fish": -3.5,
    "jellyfish": -4.0,
    "shark": -3.5,
    "whale": -3.5,
    "dolphin": -3.5,
    "turtle": -3.5,
    "husky": -4.0,
    "zoo": -5.0,
    "cage": -5.0,
    "caged": -5.0,
    "enclosure": -5.0,
}
_CONFLICTING_ENVIRONMENTS = (
    ({"arctic", "snow", "ice", "polar", "tundra", "winter"}, {"ocean", "underwater", "desert", "city"}),
    ({"space", "saturn"}, {"forest", "ocean", "desert", "city", "road"}),
    ({"roman", "ancient", "road"}, {"space", "ocean", "arctic"}),
)


class SceneImportance(str, Enum):
    """Importance of one scene in a short-form story."""

    HOOK = "hook"
    MAIN_REVEAL = "main_reveal"
    SUPPORTING = "supporting"
    TRANSITION = "transition"
    CTA = "cta"


@dataclass(frozen=True)
class VisualIntent:
    """Structured visual need for one script segment."""

    topic: str
    narration: str
    primary_subject: str
    queries: tuple[str, ...]
    action_terms: tuple[str, ...] = ()
    environment_terms: tuple[str, ...] = ()
    shot_type: str = ""
    keywords: tuple[str, ...] = ()
    scene_importance: SceneImportance = SceneImportance.SUPPORTING


@dataclass(frozen=True)
class StockCandidate:
    """Normalized stock-provider candidate metadata."""

    provider: str
    provider_id: str
    query: str
    title: str = ""
    description: str = ""
    url: str = ""
    download_url: str = ""
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    local_path: Path | None = None
    is_image: bool = False
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{self.provider}:{self.provider_id}"

    @property
    def searchable_text(self) -> str:
        values = [
            self.provider,
            self.provider_id,
            self.query,
            self.title,
            self.description,
            self.url,
            self.download_url,
            str(self.local_path or ""),
            " ".join(str(value) for value in self.raw_metadata.values() if isinstance(value, (str, int))),
        ]
        return _normalize_text(" ".join(values))


@dataclass(frozen=True)
class CandidateScore:
    """Score and explanation for a stock candidate."""

    score: float
    explanation: tuple[str, ...] = ()
    breakdown: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: tuple[str, ...] = ()

    @property
    def confidence(self) -> str:
        if self.score >= 9.0:
            return "high"
        if self.score >= 5.0:
            return "medium"
        if self.score >= 1.0:
            return "low"
        return "rejected"


@dataclass(frozen=True)
class MediaSelectionResult:
    """Selected candidate and diagnostics for future visual QA."""

    selected_candidate: StockCandidate | None
    score: CandidateScore | None
    candidate_count: int
    warnings: tuple[str, ...] = ()
    rejected: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def provider(self) -> str:
        return self.selected_candidate.provider if self.selected_candidate else ""

    @property
    def provider_id(self) -> str:
        return self.selected_candidate.provider_id if self.selected_candidate else ""

    @property
    def query_used(self) -> str:
        return self.selected_candidate.query if self.selected_candidate else ""

    @property
    def confidence(self) -> str:
        return self.score.confidence if self.score else "rejected"

    def to_metadata(self) -> dict[str, Any]:
        """Return compact diagnostics safe for MediaAsset.metadata."""

        raw = self.selected_candidate.raw_metadata if self.selected_candidate else {}
        source_url = self.selected_candidate.url if self.selected_candidate else ""
        license_name = raw.get("license", "") if isinstance(raw, dict) else ""
        attribution = raw.get("attribution", "") if isinstance(raw, dict) else ""
        scene_type = raw.get("scene_type", "") if isinstance(raw, dict) else ""
        capability = raw.get("capability", "") if isinstance(raw, dict) else ""
        return {
            "provider": self.provider,
            "provider_asset_id": self.provider_id,
            "source_url": source_url,
            "license": license_name,
            "attribution": attribution,
            "scene_type": scene_type,
            "capability": capability,
            "confidence": self.confidence,
            "confidence_level": self.confidence.upper(),
            "selected_query": self.query_used,
            "portrait_score": _score_value(self.score, "portrait_score"),
            "relevance_score": _score_value(self.score, "relevance_score"),
            "scene_importance": _score_text(self.score, "scene_importance"),
            "selection_reason": _score_text(self.score, "selection_reason"),
            "rejection_reason": _score_text(self.score, "rejection_reason"),
            "fallback_level": _fallback_level(self.warnings),
            "selection": {
                "query": self.query_used,
                "provider": self.provider,
                "provider_id": self.provider_id,
                "source_url": source_url,
                "license": license_name,
                "attribution": attribution,
                "scene_type": scene_type,
                "capability": capability,
                "score": round(self.score.score, 3) if self.score else None,
                "confidence": self.confidence,
                "confidence_level": self.confidence.upper(),
                "portrait_score": _score_value(self.score, "portrait_score"),
                "relevance_score": _score_value(self.score, "relevance_score"),
                "scene_importance": _score_text(self.score, "scene_importance"),
                "selection_reason": _score_text(self.score, "selection_reason"),
                "rejection_reason": _score_text(self.score, "rejection_reason"),
                "fallback_level": _fallback_level(self.warnings),
                "warnings": list(self.warnings),
                "rejection_reasons": list(self.score.rejection_reasons) if self.score else [],
                "candidate_count": self.candidate_count,
                "score_breakdown": dict(self.score.breakdown) if self.score else {},
            }
        }


def build_visual_intent(segment: Mapping[str, Any] | Any, topic: str) -> VisualIntent:
    """Build deterministic visual intent from a script segment."""

    narration = _segment_value(segment, "narration")
    broll = _segment_value(segment, "broll")
    raw_queries = _segment_value(segment, "broll_queries")
    if isinstance(raw_queries, str):
        queries = (raw_queries,)
    elif raw_queries:
        queries = tuple(str(query) for query in raw_queries if str(query).strip())
    else:
        queries = ()
    primary_text = broll or (queries[0] if queries else topic)
    combined = _normalize_text(" ".join([topic, narration, broll, " ".join(queries)]))
    tokens = _tokens(combined)
    action_terms = tuple(sorted({token for token in tokens if token in _ACTION_TERMS}))
    environment_terms = tuple(sorted({token for token in tokens if token in _ENVIRONMENT_TERMS}))
    shot_type = _shot_type(combined)
    subject = _primary_subject(primary_text, topic)
    keywords = tuple(sorted({
        token
        for token in _tokens(f"{subject} {primary_text} {topic}")
        if token not in _STOPWORDS and token not in _SHOT_TERMS
    }))
    return VisualIntent(
        topic=str(topic or ""),
        narration=narration,
        primary_subject=subject,
        queries=queries or ((broll or topic),),
        action_terms=action_terms,
        environment_terms=environment_terms,
        shot_type=shot_type,
        keywords=keywords,
        scene_importance=_scene_importance(_segment_value(segment, "scene_importance")),
    )


def candidate_from_local_path(path: Path, query: str) -> StockCandidate:
    """Normalize a local media path into a stock candidate."""

    path = Path(path)
    return StockCandidate(
        provider="local",
        provider_id=str(path.resolve()),
        query=query,
        title=path.stem.replace("_", " ").replace("-", " "),
        local_path=path,
        raw_metadata={
            "filename": path.name,
            "license": "operator-provided",
            "attribution": "",
            "source_url": str(path),
        },
    )


def candidate_from_pexels_video(video: Mapping[str, Any], query: str) -> StockCandidate | None:
    """Normalize a Pexels video response."""

    file_data = _best_video_file(video.get("video_files") or [], prefer_portrait=True)
    if not file_data:
        return None
    provider_id = str(video.get("id") or "")
    return StockCandidate(
        provider="pexels",
        provider_id=provider_id,
        query=query,
        title=str(video.get("url") or ""),
        description=str((video.get("user") or {}).get("name") or ""),
        url=str(video.get("url") or ""),
        download_url=str(file_data.get("link") or ""),
        duration_sec=_float_or_none(video.get("duration")),
        width=_int_or_none(file_data.get("width")),
        height=_int_or_none(file_data.get("height")),
        raw_metadata={
            "id": provider_id,
            "url": video.get("url", ""),
            "source_url": video.get("url", ""),
            "license": "Pexels License",
            "attribution": str((video.get("user") or {}).get("name") or ""),
        },
    )


def candidate_from_pixabay_hit(
    hit: Mapping[str, Any],
    rendition: Mapping[str, Any],
    query: str,
) -> StockCandidate:
    """Normalize a Pixabay video hit and selected rendition."""

    provider_id = str(hit.get("id") or "")
    return StockCandidate(
        provider="pixabay",
        provider_id=provider_id,
        query=query,
        title=str(hit.get("tags") or ""),
        description=str(hit.get("user") or ""),
        url=str(hit.get("pageURL") or ""),
        download_url=str(rendition.get("url") or ""),
        duration_sec=_float_or_none(hit.get("duration")),
        width=_int_or_none(rendition.get("width")),
        height=_int_or_none(rendition.get("height")),
        raw_metadata={
            "id": provider_id,
            "tags": hit.get("tags", ""),
            "source_url": hit.get("pageURL", ""),
            "license": "Pixabay Content License",
            "attribution": str(hit.get("user") or ""),
        },
    )


def candidate_from_nasa_item(
    item: Mapping[str, Any],
    download_url: str,
    query: str,
) -> StockCandidate:
    """Normalize a NASA search result and resolved asset URL."""

    data = ((item.get("data") or [{}])[0]) if isinstance(item.get("data"), list) else {}
    provider_id = str(data.get("nasa_id") or "")
    return StockCandidate(
        provider="nasa",
        provider_id=provider_id,
        query=query,
        title=str(data.get("title") or ""),
        description=str(data.get("description") or data.get("keywords") or ""),
        url=str(item.get("href") or ""),
        download_url=download_url,
        duration_sec=None,
        raw_metadata={
            "id": provider_id,
            "title": data.get("title", ""),
            "source_url": item.get("href", ""),
            "license": "NASA media usage guidelines",
            "attribution": "NASA",
        },
    )


def candidate_from_remote_item(
    provider: str,
    item: Mapping[str, Any],
    query: str,
) -> StockCandidate | None:
    """Normalize a generic stock/archive provider item.

    New provider wrappers should adapt their API response into this compact
    shape instead of leaking vendor-specific keys into selection.
    """

    provider_id = str(
        item.get("provider_asset_id")
        or item.get("id")
        or item.get("asset_id")
        or item.get("title")
        or ""
    ).strip()
    download_url = str(item.get("download_url") or item.get("url") or item.get("src") or "").strip()
    if not provider_id or not download_url:
        return None
    return StockCandidate(
        provider=provider,
        provider_id=provider_id,
        query=query,
        title=str(item.get("title") or ""),
        description=str(item.get("description") or item.get("tags") or ""),
        url=str(item.get("source_url") or item.get("page_url") or item.get("url") or ""),
        download_url=download_url,
        duration_sec=_float_or_none(item.get("duration_sec") or item.get("duration")),
        width=_int_or_none(item.get("width")),
        height=_int_or_none(item.get("height")),
        is_image=bool(item.get("is_image")),
        raw_metadata={
            "id": provider_id,
            "source_url": item.get("source_url") or item.get("page_url") or item.get("url") or "",
            "license": item.get("license", ""),
            "attribution": item.get("attribution", ""),
            "capability": item.get("capability", ""),
        },
    )


def select_best_candidate(
    intent: VisualIntent,
    candidates: list[StockCandidate],
    *,
    used_provider_ids: set[str] | None = None,
    target_duration_sec: float = 5.0,
    output_width: int = 1080,
    output_height: int = 1920,
    minimum_score: float = 1.0,
) -> MediaSelectionResult:
    """Select the highest-scoring candidate from one provider pool."""

    if not candidates:
        return MediaSelectionResult(None, None, 0, warnings=("no candidates",))
    scored = [
        (
            candidate,
            score_candidate(
                intent,
                candidate,
                used_provider_ids=used_provider_ids or set(),
                target_duration_sec=target_duration_sec,
                output_width=output_width,
                output_height=output_height,
            ),
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (item[1].score, item[0].provider_id), reverse=True)
    selected, score = scored[0]
    selected, score, portrait_warning = _prefer_portrait_safe_alternative(
        scored,
        selected,
        score,
        minimum_score=minimum_score,
        output_width=output_width,
        output_height=output_height,
    )
    rejected = tuple(
        (candidate.dedup_key, candidate_score.rejection_reasons)
        for candidate, candidate_score in scored[1:8]
        if candidate_score.rejection_reasons
    )
    warnings: list[str] = []
    if portrait_warning:
        warnings.append(portrait_warning)
    if score.score < minimum_score:
        warnings.append(f"best score {score.score:.2f} below minimum {minimum_score:.2f}")
        return MediaSelectionResult(None, score, len(candidates), tuple(warnings), rejected)
    if score.confidence == "low":
        warnings.append("low confidence media match")
    return MediaSelectionResult(selected, score, len(candidates), tuple(warnings), rejected)


def select_first_available_provider(
    intent: VisualIntent,
    provider_candidates: list[tuple[str, list[StockCandidate]]],
    *,
    used_provider_ids: set[str] | None = None,
    target_duration_sec: float = 5.0,
    output_width: int = 1080,
    output_height: int = 1920,
    minimum_score: float = 1.0,
) -> MediaSelectionResult:
    """Select using provider order, not global cross-provider competition."""

    total_candidates = 0
    warnings: list[str] = []
    rejected: list[tuple[str, tuple[str, ...]]] = []
    for _provider, candidates in provider_candidates:
        total_candidates += len(candidates)
        result = select_best_candidate(
            intent,
            candidates,
            used_provider_ids=used_provider_ids,
            target_duration_sec=target_duration_sec,
            output_width=output_width,
            output_height=output_height,
            minimum_score=minimum_score,
        )
        warnings.extend(result.warnings)
        rejected.extend(result.rejected)
        if result.selected_candidate:
            return MediaSelectionResult(
                result.selected_candidate,
                result.score,
                total_candidates,
                tuple(warnings),
                tuple(rejected),
            )
    return MediaSelectionResult(None, None, total_candidates, tuple(warnings or ["no provider match"]), tuple(rejected))


def score_candidate(
    intent: VisualIntent,
    candidate: StockCandidate,
    *,
    used_provider_ids: set[str] | None = None,
    target_duration_sec: float = 5.0,
    output_width: int = 1080,
    output_height: int = 1920,
) -> CandidateScore:
    """Score a candidate deterministically from provider metadata."""

    used_provider_ids = used_provider_ids or set()
    text = candidate.searchable_text
    content_text = _candidate_content_text(candidate)
    proof_text = content_text if _requires_specific_visual(intent) else text
    breakdown: dict[str, float] = {}
    explanation: list[str] = []
    rejection_reasons: list[str] = []

    subject_score = _subject_score(intent, proof_text)
    breakdown["subject"] = subject_score
    if subject_score > 0:
        explanation.append("subject matched")
    else:
        rejection_reasons.append("subject not found in metadata")

    action_score = sum(1.0 for term in intent.action_terms if term in proof_text)
    breakdown["action"] = action_score
    if action_score:
        explanation.append("action matched")

    environment_score = sum(0.9 for term in intent.environment_terms if term in proof_text)
    breakdown["environment"] = environment_score
    if environment_score:
        explanation.append("environment matched")

    shot_score = 1.2 if intent.shot_type and intent.shot_type in text else 0.0
    breakdown["shot_type"] = shot_score

    duration_score = _duration_score(candidate.duration_sec, target_duration_sec)
    breakdown["duration"] = duration_score

    portrait_score = _portrait_safety_score(candidate, output_width, output_height)
    breakdown["portrait_score"] = portrait_score
    portrait_component = _portrait_selection_component(candidate, portrait_score, output_width, output_height)
    breakdown["portrait_safety"] = portrait_component
    if portrait_score >= 8.0:
        explanation.append("portrait-safe framing")
    elif portrait_score < 4.0:
        rejection_reasons.append("unsafe portrait crop")
    if _is_ultra_wide(candidate):
        rejection_reasons.append("ultra-wide clip")

    resolution_score = _resolution_score(candidate, output_width, output_height)
    breakdown["resolution"] = resolution_score

    duplicate_score = 1.0
    if candidate.dedup_key in used_provider_ids or candidate.provider_id in used_provider_ids:
        duplicate_score = -20.0
        rejection_reasons.append("duplicate provider id")
    breakdown["dedup"] = duplicate_score

    generic_score = -1.5 if _is_generic_query(candidate.query) else 0.0
    if generic_score:
        rejection_reasons.append("generic fallback query")
    breakdown["generic"] = generic_score

    bad_score = 0.0
    for term, penalty in _BAD_TERMS.items():
        if term in text and term not in intent.keywords:
            bad_score += penalty
            rejection_reasons.append(f"penalized term: {term}")
    breakdown["bad_terms"] = bad_score

    conflict_score = _environment_conflict_score(intent, text)
    if conflict_score:
        rejection_reasons.append("unrelated environment")
    breakdown["environment_conflict"] = conflict_score

    relevance_score = _visual_relevance_score(intent, candidate, text, rejection_reasons)
    breakdown["relevance_score"] = relevance_score
    breakdown["relevance"] = (relevance_score - 5.0) * 0.7
    if relevance_score >= 8.0:
        explanation.append("strong visual relevance")

    score = sum(
        value
        for key, value in breakdown.items()
        if isinstance(value, (int, float)) and key not in {"portrait_score", "relevance_score"}
    )
    if _requires_qr_visual(intent) and not _has_qr_evidence(candidate):
        score -= 8.0
        rejection_reasons.append("qr code not proven in provider metadata")
    breakdown["_scene_importance_value"] = intent.scene_importance.value
    breakdown["_selection_reason_value"] = _selection_reason(explanation, rejection_reasons)
    breakdown["_rejection_reason_value"] = "; ".join(dict.fromkeys(rejection_reasons))
    return CandidateScore(
        score=score,
        explanation=tuple(explanation),
        breakdown=breakdown,
        rejection_reasons=tuple(dict.fromkeys(rejection_reasons)),
    )


def _segment_value(segment: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(segment, Mapping):
        return segment.get(key, "")
    return getattr(segment, key, "")


def _scene_importance(value: Any) -> SceneImportance:
    normalized = _normalize_text(str(value or "")).replace(" ", "_")
    for importance in SceneImportance:
        if normalized == importance.value:
            return importance
    return SceneImportance.SUPPORTING


def _score_value(score: CandidateScore | None, key: str) -> float | None:
    if not score:
        return None
    value = score.breakdown.get(key)
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    return None


def _score_text(score: CandidateScore | None, key: str) -> str:
    if not score:
        return ""
    value = score.breakdown.get(f"_{key}_value")
    return str(value or "")


def _fallback_level(warnings: tuple[str, ...]) -> str:
    joined = " ".join(warnings).lower()
    if "broad fallback" in joined:
        return "broad"
    if "fallback" in joined:
        return "provider"
    return "primary"


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _tokens(text: str) -> list[str]:
    return [token for token in _normalize_text(text).split() if token]


def _primary_subject(text: str, topic: str) -> str:
    tokens = [
        token
        for token in _tokens(text)
        if token not in _STOPWORDS
        and token not in _SHOT_TERMS
        and token not in _ACTION_TERMS
        and token not in {"action", "shot"}
    ]
    if len(tokens) >= 2:
        return " ".join(tokens[:2])
    if tokens:
        return tokens[0]
    topic_tokens = [token for token in _tokens(topic) if token not in _STOPWORDS]
    return " ".join(topic_tokens[:2]) if topic_tokens else ""


def _shot_type(text: str) -> str:
    if "close up" in text or "closeup" in text:
        return "close"
    if "wide shot" in text or "landscape" in text:
        return "wide"
    if "detail" in text or "macro" in text:
        return "detail"
    if "aerial" in text:
        return "aerial"
    return ""


def _subject_score(intent: VisualIntent, text: str) -> float:
    subject = _normalize_text(intent.primary_subject)
    if not subject:
        return 0.0
    if subject in text:
        return 5.0
    subject_tokens = [token for token in subject.split() if token not in _STOPWORDS]
    matched = [token for token in subject_tokens if token in text]
    if len(matched) == len(subject_tokens):
        return 3.5
    return len(matched) * 0.75


def _duration_score(duration: float | None, target_duration: float) -> float:
    if not duration or duration <= 0:
        return 0.0
    if target_duration <= duration <= max(target_duration * 4.0, target_duration + 1):
        return 1.5
    if duration >= max(2.5, target_duration * 0.45):
        return 0.75
    return -1.0


def _orientation_score(candidate: StockCandidate, output_width: int, output_height: int) -> float:
    if not candidate.width or not candidate.height:
        return 0.0
    wants_portrait = output_height > output_width
    is_portrait = candidate.height >= candidate.width
    return 1.5 if wants_portrait == is_portrait else -0.75


def _portrait_safety_score(candidate: StockCandidate, output_width: int, output_height: int) -> float:
    if not candidate.width or not candidate.height:
        return 5.0
    if output_height <= output_width:
        return 8.0
    ratio = candidate.width / max(candidate.height, 1)
    target_ratio = output_width / max(output_height, 1)
    if 0.42 <= ratio <= 0.72:
        aspect_score = 10.0
    elif 0.72 < ratio <= 1.05:
        aspect_score = 7.0
    elif 1.05 < ratio <= 1.55:
        aspect_score = 4.5
    elif 1.55 < ratio <= 2.05:
        aspect_score = 2.5
    else:
        aspect_score = 1.0
    visible_fraction = min(target_ratio / ratio, ratio / target_ratio, 1.0)
    crop_score = max(0.0, min(10.0, visible_fraction * 10.0))
    caption_score = 9.5 if ratio <= 1.05 else max(1.5, 7.0 - ((ratio - 1.05) * 4.0))
    overlay_score = 9.0 if ratio <= 1.05 else max(1.0, 6.5 - ((ratio - 1.05) * 3.5))
    subject_score = 9.0 if ratio <= 1.05 else max(1.0, crop_score - 1.5)
    return round((aspect_score + crop_score + caption_score + overlay_score + subject_score) / 5.0, 3)


def _portrait_selection_component(
    candidate: StockCandidate,
    portrait_score: float,
    output_width: int,
    output_height: int,
) -> float:
    if output_height <= output_width:
        return 0.0
    if not candidate.width or not candidate.height:
        return 0.0
    ratio = candidate.width / max(candidate.height, 1)
    official_archive = candidate.provider in {"nasa", "esa", "noaa", "wikimedia"}
    if ratio <= 0.75:
        return 2.75
    if ratio <= 1.05:
        return 1.25
    if official_archive:
        return (portrait_score - 5.0) * 0.45
    return (portrait_score - 5.0) * 1.15


def _is_ultra_wide(candidate: StockCandidate) -> bool:
    if not candidate.width or not candidate.height:
        return False
    return candidate.width / max(candidate.height, 1) >= 2.0


def _is_landscape(candidate: StockCandidate) -> bool:
    return bool(candidate.width and candidate.height and candidate.width > candidate.height)


def _is_portrait_safe(candidate: StockCandidate, output_width: int, output_height: int) -> bool:
    return _portrait_safety_score(candidate, output_width, output_height) >= 6.5


def _prefer_portrait_safe_alternative(
    scored: list[tuple[StockCandidate, CandidateScore]],
    selected: StockCandidate,
    score: CandidateScore,
    *,
    minimum_score: float,
    output_width: int,
    output_height: int,
) -> tuple[StockCandidate, CandidateScore, str]:
    if output_height <= output_width or not _is_landscape(selected):
        return selected, score, ""
    for candidate, candidate_score in scored:
        if candidate is selected:
            continue
        if not _is_portrait_safe(candidate, output_width, output_height):
            continue
        if candidate_score.score < minimum_score:
            continue
        if candidate_score.score >= score.score - 2.5:
            return candidate, candidate_score, "portrait-safe alternative selected over landscape"
    return selected, score, ""


def _resolution_score(candidate: StockCandidate, output_width: int, output_height: int) -> float:
    if not candidate.width or not candidate.height:
        return 0.0
    target_long = max(output_width, output_height)
    candidate_long = max(candidate.width, candidate.height)
    return min(candidate_long / max(target_long, 1), 1.0) * 1.5


def _visual_relevance_score(
    intent: VisualIntent,
    candidate: StockCandidate,
    text: str,
    rejection_reasons: list[str],
) -> float:
    content_text = _candidate_content_text(candidate)
    tokens = set(_tokens(content_text))
    intent_tokens = set(intent.keywords) | set(intent.environment_terms) | set(intent.action_terms)
    score = 6.0
    matched_keywords = tokens & intent_tokens
    if matched_keywords:
        score += min(2.5, len(matched_keywords) * 0.55)
    if _requires_qr_visual(intent) and not _has_qr_evidence(candidate):
        score -= 5.0
        rejection_reasons.append("qr code not proven in provider metadata")
    if _requires_specific_visual(intent):
        if not matched_keywords:
            score -= 2.5
            rejection_reasons.append("specific visual context missing")
        if tokens & _GENERIC_PEOPLE_TERMS and not (intent_tokens & _GENERIC_PEOPLE_TERMS):
            score -= 3.0
            rejection_reasons.append("generic people or lifestyle footage")
        if tokens & _GENERIC_LANDSCAPE_TERMS and len(matched_keywords) < 2:
            score -= 2.0
            rejection_reasons.append("generic landscape footage")
        if "qr" in intent_tokens and tokens & _GENERIC_TECH_TERMS and "qr" not in tokens:
            score -= 3.0
            rejection_reasons.append("abstract tech footage without qr code")
        if {"diagram", "map", "satellite"} & intent_tokens and not (tokens & {"diagram", "map", "satellite", "earth", "data", "animation"}):
            score -= 2.5
            rejection_reasons.append("missing explanatory visual signal")
    if candidate.provider in {"nasa", "esa", "noaa", "wikimedia"} and matched_keywords:
        score += 0.75
    if _is_generic_query(candidate.query):
        score -= 1.5
    return round(max(0.0, min(10.0, score)), 3)


def _candidate_content_text(candidate: StockCandidate) -> str:
    values = [
        candidate.provider,
        candidate.provider_id,
        candidate.title,
        candidate.description,
        candidate.url,
        candidate.download_url,
        str(candidate.local_path or ""),
        " ".join(str(value) for value in candidate.raw_metadata.values() if isinstance(value, (str, int))),
    ]
    return _normalize_text(" ".join(values))


def _requires_qr_visual(intent: VisualIntent) -> bool:
    tokens = set(_tokens(" ".join([
        intent.topic,
        intent.narration,
        intent.primary_subject,
        " ".join(intent.queries),
    ])))
    return "qr" in tokens or {"quick", "response", "code"} <= tokens


def _has_qr_evidence(candidate: StockCandidate) -> bool:
    compact = _candidate_content_text(candidate).replace(" ", "")
    return any(term in compact for term in _QR_VISUAL_TERMS)


def _requires_specific_visual(intent: VisualIntent) -> bool:
    text = _normalize_text(" ".join([
        intent.topic,
        intent.narration,
        intent.primary_subject,
        " ".join(intent.queries),
    ]))
    tokens = set(_tokens(text))
    return bool(tokens & _SPECIFIC_VISUAL_TERMS)


def _selection_reason(explanation: list[str], rejection_reasons: list[str]) -> str:
    if explanation:
        return "; ".join(dict.fromkeys(explanation[:3]))
    if rejection_reasons:
        return f"weak match: {rejection_reasons[0]}"
    return "selected by deterministic score"


def _is_generic_query(query: str) -> bool:
    tokens = set(_tokens(query))
    return bool(tokens & _GENERIC_TERMS) and not any(token not in _GENERIC_TERMS for token in tokens)


def _environment_conflict_score(intent: VisualIntent, text: str) -> float:
    intent_env = set(intent.environment_terms)
    text_tokens = set(_tokens(text))
    for expected, conflicting in _CONFLICTING_ENVIRONMENTS:
        if intent_env & expected and text_tokens & conflicting:
            return -2.5
    return 0.0


def _best_video_file(files: list[Mapping[str, Any]], *, prefer_portrait: bool) -> Mapping[str, Any] | None:
    usable = [file_data for file_data in files if file_data.get("link")]
    if not usable:
        return None
    return sorted(
        usable,
        key=lambda file_data: (
            (_int_or_none(file_data.get("height")) or 0) >= (_int_or_none(file_data.get("width")) or 0)
            if prefer_portrait
            else (_int_or_none(file_data.get("width")) or 0) >= (_int_or_none(file_data.get("height")) or 0),
            _int_or_none(file_data.get("height" if prefer_portrait else "width")) or 0,
            _int_or_none(file_data.get("width" if prefer_portrait else "height")) or 0,
        ),
        reverse=True,
    )[0]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
