"""Subject continuity planning and diagnostics for media selection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .visual_director import MediaMode, ShotIntent, ShotPlan


@dataclass(frozen=True)
class SubjectContinuityProfile:
    """Document-level subject continuity rules."""

    primary_subject: str
    supporting_subjects: tuple[str, ...] = ()
    subject_persistence_target: float = 0.85
    allowed_substitutions: tuple[str, ...] = ()
    forbidden_substitutions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_subject": self.primary_subject,
            "supporting_subjects": list(self.supporting_subjects),
            "subject_persistence_target": self.subject_persistence_target,
            "allowed_substitutions": list(self.allowed_substitutions),
            "forbidden_substitutions": list(self.forbidden_substitutions),
        }


@dataclass(frozen=True)
class SubjectContinuityReport:
    """Run-level subject continuity diagnostics."""

    primary_subject: str
    persistence_target: float
    subject_visible_percentage: float
    substitutions_used: tuple[str, ...] = ()
    forbidden_substitutions_accepted: tuple[str, ...] = ()
    continuity_score: float = 0.0
    reasons_for_continuity_breaks: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_subject": self.primary_subject,
            "persistence_target": self.persistence_target,
            "subject_visible_percentage": self.subject_visible_percentage,
            "substitutions_used": list(self.substitutions_used),
            "forbidden_substitutions_accepted": list(self.forbidden_substitutions_accepted),
            "continuity_score": self.continuity_score,
            "reasons_for_continuity_breaks": list(self.reasons_for_continuity_breaks),
        }


class SubjectContinuityEngine:
    """Annotate shot plans and media results with documentary subject continuity."""

    def build_profile(
        self,
        plan: ShotPlan,
        *,
        segments: list[Mapping[str, Any]] | None = None,
    ) -> SubjectContinuityProfile:
        """Build deterministic continuity rules from the current shot plan."""

        primary_subject = _clean_subject(plan.primary_subject or "")
        if not primary_subject:
            primary_subject = _clean_subject(_first_nonempty(plan.required_subjects))
        if not primary_subject:
            primary_subject = _clean_subject(plan.topic)

        supporting = _dedupe_terms([
            *plan.supporting_subjects,
            *plan.required_subjects,
            *plan.visual_identity,
            *_topic_supporting_subjects(plan.topic, primary_subject),
        ])
        allowed = _dedupe_terms([
            *plan.allowed_substitutions,
            *supporting,
            *_topic_allowed_substitutions(plan.topic, primary_subject),
        ])
        forbidden = _dedupe_terms([
            *plan.forbidden_substitutions,
            *plan.avoid_terms,
            *_topic_forbidden_substitutions(plan.topic, primary_subject),
        ])
        target = plan.subject_persistence_target or 0.85
        return SubjectContinuityProfile(
            primary_subject=primary_subject,
            supporting_subjects=tuple(term for term in supporting if term != primary_subject),
            subject_persistence_target=max(0.0, min(1.0, float(target))),
            allowed_substitutions=tuple(term for term in allowed if term != primary_subject),
            forbidden_substitutions=tuple(forbidden),
        )

    def apply(
        self,
        plan: ShotPlan,
        *,
        segments: list[Mapping[str, Any]] | None = None,
    ) -> ShotPlan:
        """Return a shot plan enriched with continuity metadata."""

        profile = self.build_profile(plan, segments=segments)
        intents = tuple(_apply_profile_to_intent(intent, profile) for intent in plan.intents)
        diagnostics = {
            **plan.diagnostics,
            "subject_continuity": profile.to_dict(),
        }
        return replace(
            plan,
            primary_subject=profile.primary_subject,
            supporting_subjects=profile.supporting_subjects,
            subject_persistence_target=profile.subject_persistence_target,
            allowed_substitutions=profile.allowed_substitutions,
            forbidden_substitutions=profile.forbidden_substitutions,
            intents=intents,
            diagnostics=diagnostics,
        )

    def report_from_assets(
        self,
        plan: ShotPlan,
        media_assets: list[Any],
    ) -> SubjectContinuityReport:
        """Build continuity diagnostics from selected MediaAsset metadata."""

        show_total = 0
        visible = 0
        substitutions: list[str] = []
        forbidden: list[str] = []
        breaks: list[dict[str, Any]] = []

        for index, asset in enumerate(media_assets):
            metadata = getattr(asset, "metadata", {}) or {}
            selection = metadata.get("selection", {}) if isinstance(metadata, dict) else {}
            mode = str(
                selection.get("media_mode")
                or metadata.get("media_mode")
                or ""
            )
            if mode not in {MediaMode.SHOW.value, MediaMode.PROVE.value, MediaMode.REVEAL.value}:
                continue
            show_total += 1
            subject_visible = bool(
                selection.get("subject_visible")
                or metadata.get("subject_visible")
            )
            substitution = str(selection.get("substitution_used") or metadata.get("substitution_used") or "")
            accepted_forbidden = selection.get("forbidden_substitutions") or metadata.get("forbidden_substitutions") or []
            if subject_visible:
                visible += 1
            elif substitution:
                substitutions.append(substitution)
            if accepted_forbidden:
                forbidden.extend(str(item) for item in accepted_forbidden)
            if not subject_visible:
                breaks.append({
                    "scene_index": index,
                    "media_mode": mode,
                    "provider": selection.get("provider") or metadata.get("provider"),
                    "provider_id": selection.get("provider_id") or metadata.get("provider_asset_id"),
                    "query": selection.get("query") or metadata.get("selected_query"),
                    "reason": selection.get("continuity_reason")
                    or metadata.get("continuity_reason")
                    or "primary subject not proven in selected media metadata",
                })

        percentage = visible / show_total if show_total else 1.0
        score = min(1.0, percentage / max(plan.subject_persistence_target or 0.85, 0.01))
        return SubjectContinuityReport(
            primary_subject=plan.primary_subject,
            persistence_target=plan.subject_persistence_target,
            subject_visible_percentage=round(percentage, 3),
            substitutions_used=tuple(dict.fromkeys(substitutions)),
            forbidden_substitutions_accepted=tuple(dict.fromkeys(forbidden)),
            continuity_score=round(score, 3),
            reasons_for_continuity_breaks=tuple(breaks),
        )


def _apply_profile_to_intent(
    intent: ShotIntent,
    profile: SubjectContinuityProfile,
) -> ShotIntent:
    diagnostics = {
        **intent.diagnostics,
        "subject_continuity": profile.to_dict(),
    }
    return replace(
        intent,
        primary_subject=profile.primary_subject,
        required_entities=_dedupe_terms([
            profile.primary_subject,
            *intent.required_entities,
            *profile.supporting_subjects,
        ]),
        negative_terms=_dedupe_terms([
            *intent.negative_terms,
            *profile.forbidden_substitutions,
        ]),
        diagnostics=diagnostics,
    )


def _topic_supporting_subjects(topic: str, primary_subject: str) -> tuple[str, ...]:
    text = _normalize(topic)
    if "shark" in text:
        return ("shark fossil", "shark skeleton", "shark tooth", "shark fin")
    if "penguin" in text:
        return ("emperor penguin", "penguin colony", "penguin huddle", "penguin chick")
    if "titanic" in text:
        return ("Titanic shipwreck", "Titanic bow", "Titanic artifact", "iceberg", "deep sea wreck")
    if "volcano" in text or "lava" in text:
        return ("lava", "eruption", "volcanic rock", "volcanic island", "magma")
    if "bee" in text or "honeybee" in text:
        return ("honeybee", "beehive", "honeycomb", "waggle dance", "worker bee")
    return (primary_subject,)


def _topic_allowed_substitutions(topic: str, primary_subject: str) -> tuple[str, ...]:
    text = _normalize(topic)
    if "titanic" in text:
        return ("archive photo", "shipwreck", "ocean liner", "iceberg", "submersible")
    if "volcano" in text or "lava" in text:
        return ("lava", "magma", "eruption", "volcanic island", "geologic map")
    return _topic_supporting_subjects(topic, primary_subject)


def _topic_forbidden_substitutions(topic: str, primary_subject: str) -> tuple[str, ...]:
    text = _normalize(topic)
    if "shark" in text:
        return ("diver", "scuba", "jellyfish", "generic reef", "random fish", "coral only")
    if "penguin" in text:
        return ("archive document", "newspaper", "desert", "hard drive", "wind only", "person")
    if "titanic" in text:
        return ("random boat", "generic ocean", "modern cruise", "beach", "people lifestyle")
    if "volcano" in text or "lava" in text:
        return ("generic ocean", "coastline only", "smoke only", "unrelated mountain", "city")
    if "bee" in text or "honeybee" in text:
        return ("flower only", "garden only", "person talking", "empty forest")
    return ("generic people", "unrelated landscape")


def _clean_subject(value: str) -> str:
    return " ".join(str(value or "").replace("-", " ").split())


def _normalize(value: str) -> str:
    return _clean_subject(value).lower()


def _first_nonempty(values: tuple[str, ...]) -> str:
    for value in values:
        if str(value).strip():
            return str(value)
    return ""


def _dedupe_terms(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean_subject(value)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return tuple(ordered)
