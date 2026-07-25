"""Cross-artifact consistency checks for documentary editorial identity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .editorial import EditorialCanon
from .visual_director import ShotPlan


class EditorialIdentityDecision(str, Enum):
    """Outcome of validating a documentary plan against its requested topic."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class EditorialIdentityReport:
    """Auditable decision explaining whether planning preserved topic identity."""

    topic: str
    canonical_primary_subject: str
    shot_plan_primary_subject: str
    decision: EditorialIdentityDecision
    topic_terms: tuple[str, ...]
    subject_terms: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        """Return whether this plan can proceed to source coverage."""

        return self.decision == EditorialIdentityDecision.APPROVED

    def to_dict(self) -> dict[str, Any]:
        """Serialize the validation decision for resume and audit."""

        return {
            "topic": self.topic,
            "canonical_primary_subject": self.canonical_primary_subject,
            "shot_plan_primary_subject": self.shot_plan_primary_subject,
            "decision": self.decision.value,
            "topic_terms": list(self.topic_terms),
            "subject_terms": list(self.subject_terms),
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditorialIdentityReport":
        """Restore a persisted identity-validation report."""

        return cls(
            topic=str(data.get("topic", "")),
            canonical_primary_subject=str(data.get("canonical_primary_subject", "")),
            shot_plan_primary_subject=str(data.get("shot_plan_primary_subject", "")),
            decision=EditorialIdentityDecision(
                str(data.get("decision", EditorialIdentityDecision.REJECTED.value))
            ),
            topic_terms=tuple(str(item) for item in data.get("topic_terms", ())),
            subject_terms=tuple(str(item) for item in data.get("subject_terms", ())),
            reasons=tuple(str(item) for item in data.get("reasons", ())),
        )

    def write_json(self, path: Path) -> Path:
        """Write the report to the run artifact directory."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path


class EditorialIdentityGate:
    """Reject plans whose locked subject is unrelated to the requested topic."""

    def evaluate(
        self,
        *,
        topic: str,
        editorial_canon: EditorialCanon,
        shot_plan: ShotPlan,
    ) -> EditorialIdentityReport:
        """Validate topic, canon, and ShotPlan identity before media retrieval."""

        reasons: list[str] = []
        topic_terms = _identity_terms(topic)
        canonical_terms = _identity_terms(editorial_canon.primary_subject)
        explicit_subject = str(editorial_canon.diagnostics.get("explicit_subject", ""))

        if _normalized(editorial_canon.documentary_title) != _normalized(topic):
            reasons.append("Editorial Canon title does not match the requested documentary topic")
        if _normalized(shot_plan.topic) != _normalized(topic):
            reasons.append("ShotPlan topic does not match the requested documentary topic")
        if _normalized(editorial_canon.primary_subject) != _normalized(shot_plan.primary_subject):
            reasons.append("Editorial Canon and ShotPlan primary subjects differ")
        if not canonical_terms:
            reasons.append("Editorial Canon did not produce a primary subject")
        elif (
            not explicit_subject
            and not set(topic_terms).intersection(canonical_terms)
        ):
            reasons.append(
                "Editorial Canon primary subject has no topic-level lexical anchor"
            )

        return EditorialIdentityReport(
            topic=topic,
            canonical_primary_subject=editorial_canon.primary_subject,
            shot_plan_primary_subject=shot_plan.primary_subject,
            decision=(
                EditorialIdentityDecision.REJECTED
                if reasons
                else EditorialIdentityDecision.APPROVED
            ),
            topic_terms=topic_terms,
            subject_terms=canonical_terms,
            reasons=tuple(reasons),
        )


_STOP_TERMS = {
    "a", "an", "and", "are", "can", "do", "does", "how", "in", "into", "is",
    "of", "the", "their", "this", "to", "turn", "turns", "why", "with",
}


def _identity_terms(value: str) -> tuple[str, ...]:
    """Return normalized identity words with simple plural normalization."""

    terms: list[str] = []
    for word in re.findall(r"[a-z0-9]+", value.lower()):
        if word in _STOP_TERMS:
            continue
        normalized = word[:-1] if len(word) > 3 and word.endswith("s") else word
        if normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


def _normalized(value: str) -> str:
    """Normalize free text for deterministic equality checks."""

    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))
