"""Editorial canon and primary-subject locking for documentary planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class DocumentaryMode(str, Enum):
    """High-level editorial shape for a documentary short."""

    SINGLE_SUBJECT = "single_subject"
    MULTI_SUBJECT = "multi_subject"
    PLACE = "place"
    EVENT = "event"
    PROCESS = "process"
    TIMELINE = "timeline"
    COMPARISON = "comparison"


@dataclass(frozen=True)
class EditorialCanon:
    """Immutable documentary identity consumed by downstream planning stages."""

    documentary_title: str
    documentary_theme: str
    documentary_type: str
    documentary_mode: DocumentaryMode
    primary_subject: str
    secondary_subjects: tuple[str, ...] = ()
    supporting_entities: tuple[str, ...] = ()
    comparison_entities: tuple[str, ...] = ()
    predator_entities: tuple[str, ...] = ()
    location_entities: tuple[str, ...] = ()
    forbidden_primary_subjects: tuple[str, ...] = ()
    visual_identity: tuple[str, ...] = ()
    expected_scene_roles: tuple[str, ...] = ()
    avoid_terms: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "documentary_title": self.documentary_title,
            "documentary_theme": self.documentary_theme,
            "documentary_type": self.documentary_type,
            "documentary_mode": self.documentary_mode.value,
            "primary_subject": self.primary_subject,
            "secondary_subjects": list(self.secondary_subjects),
            "supporting_entities": list(self.supporting_entities),
            "comparison_entities": list(self.comparison_entities),
            "predator_entities": list(self.predator_entities),
            "location_entities": list(self.location_entities),
            "forbidden_primary_subjects": list(self.forbidden_primary_subjects),
            "visual_identity": list(self.visual_identity),
            "expected_scene_roles": list(self.expected_scene_roles),
            "avoid_terms": list(self.avoid_terms),
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialCanon":
        return cls(
            documentary_title=str(data.get("documentary_title", "")),
            documentary_theme=str(data.get("documentary_theme", "")),
            documentary_type=str(data.get("documentary_type", "")),
            documentary_mode=DocumentaryMode(str(data.get("documentary_mode", DocumentaryMode.SINGLE_SUBJECT.value))),
            primary_subject=str(data.get("primary_subject", "")),
            secondary_subjects=tuple(str(item) for item in data.get("secondary_subjects", [])),
            supporting_entities=tuple(str(item) for item in data.get("supporting_entities", [])),
            comparison_entities=tuple(str(item) for item in data.get("comparison_entities", [])),
            predator_entities=tuple(str(item) for item in data.get("predator_entities", [])),
            location_entities=tuple(str(item) for item in data.get("location_entities", [])),
            forbidden_primary_subjects=tuple(str(item) for item in data.get("forbidden_primary_subjects", [])),
            visual_identity=tuple(str(item) for item in data.get("visual_identity", [])),
            expected_scene_roles=tuple(str(item) for item in data.get("expected_scene_roles", [])),
            avoid_terms=tuple(str(item) for item in data.get("avoid_terms", [])),
            diagnostics=dict(data.get("diagnostics", {})),
        )

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


@dataclass(frozen=True)
class PrimarySubjectLockReport:
    """Diagnostics explaining why the primary subject was locked."""

    primary_subject: str
    evidence: dict[str, Any]
    attempted_overrides: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_subject": self.primary_subject,
            "evidence": self.evidence,
            "attempted_overrides": list(self.attempted_overrides),
        }


class EditorialCanonBuilder:
    """Build deterministic editorial identity before ShotPlan creation."""

    def build(
        self,
        *,
        topic: str,
        segments: Sequence[Mapping[str, Any]],
        knowledge_domains: Sequence[Any] = (),
    ) -> tuple[EditorialCanon, PrimarySubjectLockReport, dict[str, Any], dict[str, Any]]:
        topic_clean = _clean(topic)
        domain_scores = _score_domains(topic_clean, segments, knowledge_domains)
        explicit_subject = _explicit_subject(topic_clean)
        mode = _documentary_mode(topic_clean, explicit_subject)
        primary_subject = explicit_subject or _best_topic_domain_subject(domain_scores) or topic_clean
        if not explicit_subject and mode == DocumentaryMode.PLACE and "largest desert" in _norm(topic_clean):
            primary_subject = "Antarctica"

        domain = _domain_for_subject(primary_subject, knowledge_domains)
        supporting = _supporting_entities(topic_clean, primary_subject, domain)
        predators = _predator_entities(topic_clean, segments)
        forbidden_primary = _dedupe([
            *predators,
            *_forbidden_topic_overrides(topic_clean, primary_subject),
            *[
                getattr(candidate, "primary_subject", "")
                for score, candidate in domain_scores
                if getattr(candidate, "primary_subject", "") and _norm(getattr(candidate, "primary_subject", "")) != _norm(primary_subject)
            ],
        ])
        visual_identity = _visual_identity(topic_clean, primary_subject, domain, mode)
        roles = _scene_roles(topic_clean, mode, len(segments))
        avoid_terms = _avoid_terms(topic_clean, primary_subject, domain)

        attempted_overrides = tuple(
            {
                "candidate_subject": getattr(candidate, "primary_subject", ""),
                "domain_id": getattr(candidate, "id", ""),
                "score": score,
                "decision": "rejected_as_supporting_entity",
                "reason": "knowledge pack may enrich but cannot override editorial primary subject",
            }
            for score, candidate in domain_scores
            if getattr(candidate, "primary_subject", "") and _norm(getattr(candidate, "primary_subject", "")) != _norm(primary_subject)
        )
        canon = EditorialCanon(
            documentary_title=topic_clean,
            documentary_theme=_theme(topic_clean, primary_subject, mode),
            documentary_type=_documentary_type(topic_clean, mode),
            documentary_mode=mode,
            primary_subject=primary_subject,
            secondary_subjects=_secondary_subjects(topic_clean, primary_subject, domain),
            supporting_entities=supporting,
            comparison_entities=_comparison_entities(topic_clean, primary_subject),
            predator_entities=predators,
            location_entities=_location_entities(topic_clean, primary_subject, mode),
            forbidden_primary_subjects=forbidden_primary,
            visual_identity=visual_identity,
            expected_scene_roles=roles,
            avoid_terms=avoid_terms,
            diagnostics={
                "builder": "editorial_canon",
                "explicit_subject": explicit_subject,
                "matched_domain": getattr(domain, "id", "") if domain else "",
            },
        )
        lock_report = PrimarySubjectLockReport(
            primary_subject=primary_subject,
            evidence={
                "title_weight": 100 if explicit_subject else 0,
                "topic": topic_clean,
                "opening_narration": str(segments[0].get("narration", "")) if segments else "",
                "knowledge_domain_scores": [
                    {"domain_id": getattr(candidate, "id", ""), "score": score}
                    for score, candidate in domain_scores
                ],
            },
            attempted_overrides=attempted_overrides,
        )
        scene_role_report = {
            "primary_subject": primary_subject,
            "expected_scene_roles": list(roles),
            "scenes": [
                {
                    "scene_index": index,
                    "role": roles[index] if index < len(roles) else "supporting",
                    "narration": str(segment.get("narration", "")),
                }
                for index, segment in enumerate(segments)
            ],
        }
        domain_report = {
            "selected_domain": getattr(domain, "id", "") if domain else "generic",
            "primary_subject": primary_subject,
            "domain_scores": [
                {
                    "domain_id": getattr(candidate, "id", ""),
                    "primary_subject": getattr(candidate, "primary_subject", ""),
                    "score": score,
                    "accepted": _norm(getattr(candidate, "primary_subject", "")) == _norm(primary_subject),
                }
                for score, candidate in domain_scores
            ],
            "attempted_overrides": list(attempted_overrides),
        }
        return canon, lock_report, scene_role_report, domain_report


def _score_domains(topic: str, segments: Sequence[Mapping[str, Any]], domains: Sequence[Any]) -> list[tuple[int, Any]]:
    topic_text = _norm(topic)
    opening = _norm(str(segments[0].get("narration", "")) if segments else "")
    all_text = _norm(" ".join([
        topic,
        " ".join(str(segment.get("narration", "")) for segment in segments),
        " ".join(str(segment.get("broll", "")) for segment in segments),
    ]))
    scored: list[tuple[int, Any]] = []
    for domain in domains:
        score = 0
        primary = _norm(getattr(domain, "primary_subject", ""))
        if primary and primary in topic_text:
            score += 100
        for term in getattr(domain, "trigger_terms", ()):
            term_norm = _norm(term)
            if not term_norm:
                continue
            if term_norm in topic_text:
                score += 50
            if term_norm in opening:
                score += 12
            if term_norm in all_text:
                score += 2
        if score:
            scored.append((score, domain))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _explicit_subject(topic: str) -> str:
    text = _norm(topic)
    patterns = (
        ("octopus", ("octopus", "octopuses", "mimic octopus")),
        ("Greenland shark", ("greenland shark", "somniosus microcephalus")),
        ("shark", ("shark", "sharks")),
        ("butterfly", ("butterfly", "butterflies", "garden butterfly", "garden butterflies")),
        ("penguin", ("penguin", "penguins")),
        ("Titanic", ("titanic",)),
        ("volcano", ("volcano", "volcanoes", "lava")),
        ("honeybee", ("honeybee", "honeybees", "bee", "bees")),
        ("Roman aqueduct", ("roman aqueduct", "aqueducts")),
        ("aurora borealis", ("northern lights", "aurora")),
        ("lightning", ("lightning",)),
        ("undersea internet cable", ("undersea cable", "internet under the ocean", "submarine cable")),
        ("Seven Wonders of the World", ("seven wonders", "wonders of the world")),
        ("Antarctica", ("largest desert", "antarctica", "frozen desert")),
    )
    for subject, terms in patterns:
        if any(term in text for term in terms):
            return subject
    return ""


def _documentary_mode(topic: str, subject: str) -> DocumentaryMode:
    text = _norm(topic)
    if "seven wonders" in text or "wonders of the world" in text:
        return DocumentaryMode.MULTI_SUBJECT
    if any(term in text for term in ("largest desert", "antarctica", "place", "where is")):
        return DocumentaryMode.PLACE
    if any(term in text for term in ("how ", "created", "work", "built", "forms", "happens")):
        return DocumentaryMode.PROCESS
    if any(term in text for term in ("vs", "versus", "compared", "strongest", "biggest")):
        return DocumentaryMode.COMPARISON
    return DocumentaryMode.SINGLE_SUBJECT if subject else DocumentaryMode.PROCESS


def _best_topic_domain_subject(domain_scores: list[tuple[int, Any]]) -> str:
    return str(getattr(domain_scores[0][1], "primary_subject", "")) if domain_scores else ""


def _domain_for_subject(subject: str, domains: Sequence[Any]) -> Any | None:
    subject_norm = _norm(subject)
    for domain in domains:
        if _norm(getattr(domain, "primary_subject", "")) == subject_norm:
            return domain
    return None


def _supporting_entities(topic: str, primary_subject: str, domain: Any | None) -> tuple[str, ...]:
    base = list(getattr(domain, "named_entities", ()) if domain else ())
    text = _norm(topic)
    if primary_subject == "octopus":
        base.extend(["camouflage", "reef", "predators", "arms", "skin cells", "narrow crevice"])
    if primary_subject == "butterfly":
        base.extend(["wings", "caterpillar", "flower", "garden", "proboscis", "migration"])
    if primary_subject == "Antarctica":
        base.extend(["ice sheet", "glacier", "snowstorm", "research station", "Sahara Desert", "world map"])
    if primary_subject == "Seven Wonders of the World":
        base.extend([
            "Great Wall of China", "Petra", "Colosseum", "Chichen Itza", "Machu Picchu",
            "Taj Mahal", "Christ the Redeemer", "world map", "ancient architecture",
        ])
    if "desert" in text:
        base.extend(["dry climate", "polar desert", "Sahara Desert"])
    return _dedupe(base)


def _secondary_subjects(topic: str, primary_subject: str, domain: Any | None) -> tuple[str, ...]:
    if primary_subject == "Seven Wonders of the World":
        return tuple(entity for entity in _supporting_entities(topic, primary_subject, domain) if entity != "world map")
    if primary_subject == "Antarctica":
        return ("ice sheet", "glacier", "polar desert")
    return tuple(getattr(domain, "required_entities", ()) if domain else ())


def _comparison_entities(topic: str, primary_subject: str) -> tuple[str, ...]:
    if primary_subject == "Antarctica":
        return ("Sahara Desert", "world deserts", "dryness comparison")
    if primary_subject == "Seven Wonders of the World":
        return ("ancient wonder", "modern wonder", "world map")
    return ()


def _predator_entities(topic: str, segments: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    text = _norm(" ".join([topic, " ".join(str(segment.get("narration", "")) for segment in segments)]))
    predators = []
    for term in ("shark", "predator", "orca", "seal", "bird"):
        if term in text:
            predators.append(term)
    return _dedupe(predators)


def _location_entities(topic: str, primary_subject: str, mode: DocumentaryMode) -> tuple[str, ...]:
    if primary_subject == "Antarctica":
        return ("Antarctica", "South Pole", "Southern Ocean")
    if primary_subject == "Seven Wonders of the World":
        return ("China", "Jordan", "Italy", "Mexico", "Peru", "India", "Brazil")
    if mode == DocumentaryMode.PLACE:
        return (primary_subject,)
    return ()


def _forbidden_topic_overrides(topic: str, primary_subject: str) -> tuple[str, ...]:
    if primary_subject == "octopus":
        return ("shark", "fish", "reef", "food", "dish")
    if primary_subject == "Greenland shark":
        return ("great white", "reef shark", "tiger shark", "hammerhead", "aquarium shark", "diver with shark")
    if primary_subject == "butterfly":
        return ("honeybee", "bee", "wasp", "fly", "moth", "generic flower only")
    if primary_subject == "Antarctica":
        return ("penguin", "seal", "wildlife", "Sahara Desert")
    if primary_subject == "Seven Wonders of the World":
        return ("Roman aqueduct", "Pont du Gard", "generic ruins")
    return ()


def _visual_identity(topic: str, primary_subject: str, domain: Any | None, mode: DocumentaryMode) -> tuple[str, ...]:
    if primary_subject == "Antarctica":
        return ("world map", "Antarctica aerial", "ice sheet", "glacier", "snowstorm", "research station")
    if primary_subject == "Seven Wonders of the World":
        return ("world map", "landmark exterior", "ancient architecture", "travel documentary", "wide establishing shot")
    if primary_subject == "octopus":
        return ("real octopus", "octopus underwater", "octopus close up", "octopus camouflage")
    if primary_subject == "Greenland shark":
        return ("Greenland shark", "deep sea shark", "Arctic ocean", "cold water shark")
    if primary_subject == "butterfly":
        return ("real butterfly", "butterfly close up", "butterfly wings", "butterfly on flower", "caterpillar")
    if domain:
        return tuple(getattr(domain, "visual_identity", ()))
    return (primary_subject,)


def _scene_roles(topic: str, mode: DocumentaryMode, count: int) -> tuple[str, ...]:
    if count <= 0:
        return ()
    if mode == DocumentaryMode.PLACE:
        base = ("hook", "map", "landscape", "landscape", "explanation", "comparison", "wildlife", "evidence", "landscape", "ending", "cta")
    elif mode == DocumentaryMode.MULTI_SUBJECT:
        base = ("hook", "map", "overview", "evidence", "landscape", "comparison", "evidence", "timeline", "landscape", "ending", "cta")
    elif mode == DocumentaryMode.PROCESS:
        base = ("hook", "overview", "process", "explanation", "evidence", "close-up", "process", "comparison", "evidence", "ending", "cta")
    else:
        base = ("hook", "overview", "close-up", "wildlife", "explanation", "macro", "evidence", "process", "close-up", "ending", "cta")
    roles = [base[min(index, len(base) - 1)] for index in range(count)]
    if count > 1:
        roles[-1] = "cta"
    return tuple(roles)


def _avoid_terms(topic: str, primary_subject: str, domain: Any | None) -> tuple[str, ...]:
    terms = list(getattr(domain, "avoid_terms", ()) if domain else ())
    if primary_subject == "octopus":
        terms.extend(["grilled", "dish", "food", "cooking", "kitchen", "restaurant", "lemon", "orange", "juice", "plate", "meal"])
    if primary_subject == "Greenland shark":
        terms.extend(["great white", "reef shark", "tiger shark", "hammerhead", "aquarium", "diver", "tropical reef"])
    if primary_subject == "butterfly":
        terms.extend(["honeybee", "bee", "wasp", "fly", "moth", "cartoon", "logo", "generic flower only"])
    if primary_subject == "Antarctica":
        terms.extend(["penguin only", "animal only", "zoo", "cartoon", "generic bird"])
    if primary_subject == "Seven Wonders of the World":
        terms.extend(["Roman aqueduct", "Pont du Gard", "generic stone texture", "modern office"])
    return _dedupe(terms)


def _theme(topic: str, primary_subject: str, mode: DocumentaryMode) -> str:
    if mode == DocumentaryMode.PLACE:
        return f"Geography and scale of {primary_subject}"
    if mode == DocumentaryMode.MULTI_SUBJECT:
        return f"Global overview of {primary_subject}"
    return f"Documentary identity for {primary_subject}"


def _documentary_type(topic: str, mode: DocumentaryMode) -> str:
    if mode == DocumentaryMode.PLACE:
        return "geography"
    if mode == DocumentaryMode.MULTI_SUBJECT:
        return "world_history"
    if mode == DocumentaryMode.PROCESS:
        return "science_explainer"
    return "single_subject_documentary"


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("-", " ")).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean(value)
        key = _norm(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)
