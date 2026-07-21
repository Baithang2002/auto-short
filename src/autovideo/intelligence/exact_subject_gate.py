"""Deterministically require proof for identity-defining documentaries.

The gate is intentionally a post-retrieval policy.  It never searches,
downloads, scores, or verifies media itself; it only evaluates the metadata
and downloaded-media evidence already produced by earlier pipeline stages.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class ExactSubjectGateDecision(str, Enum):
    """Outcome of an exact-subject availability evaluation."""

    PASSED = "PASSED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class ExactSubjectGateConfig:
    """Runtime policy for strict documentary-identity evidence."""

    enabled: bool = True
    minimum_verified_exact_matches: int = 1
    allow_aliases: bool = True
    allow_scientific_names: bool = True

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "ExactSubjectGateConfig":
        """Load a bounded gate configuration from environment values."""

        env = values if values is not None else os.environ
        return cls(
            enabled=_env_flag(env, "AUTO_VIDEO_EXACT_SUBJECT_GATE_ENABLED", True),
            minimum_verified_exact_matches=max(
                1,
                _env_int(env, "AUTO_VIDEO_EXACT_SUBJECT_GATE_MIN_VERIFIED_MATCHES", 1),
            ),
            allow_aliases=_env_flag(env, "AUTO_VIDEO_EXACT_SUBJECT_GATE_ALLOW_ALIASES", True),
            allow_scientific_names=_env_flag(
                env,
                "AUTO_VIDEO_EXACT_SUBJECT_GATE_ALLOW_SCIENTIFIC_NAMES",
                True,
            ),
        )


@dataclass(frozen=True)
class SubjectDefinition:
    """The canonical identity and approved evidence terms for one documentary."""

    canonical_entity: str
    aliases: tuple[str, ...] = ()
    scientific_names: tuple[str, ...] = ()
    identity_defining: bool = False
    reason: str = ""

    @property
    def accepted_terms(self) -> tuple[str, ...]:
        """Return every acceptable term in deterministic display order."""

        return _dedupe((self.canonical_entity, *self.aliases, *self.scientific_names))


@dataclass(frozen=True)
class ExactSubjectMatch:
    """One selected asset that did or did not prove documentary identity."""

    scene_index: int
    provider: str
    provider_id: str
    media_path: str
    source: str
    selected_entity: str
    entity_fidelity: str
    metadata_confidence: float
    proof: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the match for durable pipeline diagnostics."""

        return asdict(self)


@dataclass(frozen=True)
class ExactSubjectGateReport:
    """Auditable outcome for the strict exact-subject availability gate."""

    topic: str
    canonical_entity: str
    accepted_aliases: tuple[str, ...]
    scientific_names: tuple[str, ...]
    identity_defining: bool
    decision: ExactSubjectGateDecision
    failure_reason: str
    scheduler_action: str
    verified_matches: tuple[ExactSubjectMatch, ...] = ()
    rejected_generic_matches: tuple[ExactSubjectMatch, ...] = ()
    configuration: ExactSubjectGateConfig = field(default_factory=ExactSubjectGateConfig)
    identity_reason: str = ""

    @property
    def passed(self) -> bool:
        """Return whether downstream work may continue."""

        return self.decision is not ExactSubjectGateDecision.DEFERRED

    def to_dict(self) -> dict[str, Any]:
        """Serialize the stable, scheduler-readable report format."""

        return {
            "topic": self.topic,
            "canonical_entity": self.canonical_entity,
            "accepted_aliases": list(self.accepted_aliases),
            "scientific_names": list(self.scientific_names),
            "identity_defining": self.identity_defining,
            "identity_reason": self.identity_reason,
            "decision": self.decision.value,
            "failure_reason": self.failure_reason,
            "scheduler_action": self.scheduler_action,
            "verified_matches": [match.to_dict() for match in self.verified_matches],
            "rejected_generic_matches": [
                match.to_dict() for match in self.rejected_generic_matches
            ],
            "summary": {
                "verified_exact_match_count": len(self.verified_matches),
                "rejected_generic_match_count": len(self.rejected_generic_matches),
                "minimum_verified_exact_matches": self.configuration.minimum_verified_exact_matches,
            },
            "configuration": asdict(self.configuration),
        }

    def write_json(self, path: Path) -> Path:
        """Persist the report before a recoverable pipeline deferral."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class ExactSubjectAvailabilityGate:
    """Require existing exact evidence for a documentary's defining entity."""

    def __init__(self, config: ExactSubjectGateConfig | None = None) -> None:
        self.config = config or ExactSubjectGateConfig()

    def evaluate(
        self,
        *,
        topic: str,
        subject: SubjectDefinition,
        media_assets: Sequence[Any],
        verified_media_report: Mapping[str, Any] | None = None,
    ) -> ExactSubjectGateReport:
        """Evaluate existing selected-media diagnostics without any provider work."""

        if not self.config.enabled:
            return self._report(
                topic,
                subject,
                ExactSubjectGateDecision.SKIPPED,
                "strict exact-subject gate disabled",
                "continue",
            )
        if not subject.identity_defining:
            return self._report(
                topic,
                subject,
                ExactSubjectGateDecision.SKIPPED,
                "documentary subject is a generic category",
                "continue",
            )

        accepted_terms = self._accepted_terms(subject)
        frame_rows = _verified_rows_by_scene(verified_media_report)
        verified: list[ExactSubjectMatch] = []
        rejected_generic: list[ExactSubjectMatch] = []
        for index, asset in enumerate(media_assets):
            metadata = _asset_metadata(asset)
            selection = _selection_metadata(metadata)
            evidence = _mapping(selection.get("evidence_verification"))
            provider = str(selection.get("provider") or metadata.get("provider") or "")
            provider_id = str(selection.get("provider_id") or metadata.get("provider_asset_id") or "")
            source = str(_asset_value(asset, "source") or metadata.get("source") or provider)
            media_path = str(_asset_value(asset, "local_path") or metadata.get("local_path") or "")
            selected_entity = str(
                evidence.get("selected_entity")
                or selection.get("selected_entity")
                or metadata.get("selected_entity")
                or ""
            )
            entity_fidelity = str(
                evidence.get("entity_fidelity")
                or selection.get("entity_fidelity")
                or metadata.get("entity_fidelity")
                or ""
            ).lower()
            metadata_confidence = _number(
                evidence.get("metadata_confidence", selection.get("metadata_confidence", 0.0))
            )
            frame_row = frame_rows.get(index, {})
            if _is_authentic_source(provider, source, media_path):
                proof = self._proof_for_asset(
                    accepted_terms,
                    evidence,
                    selected_entity,
                    entity_fidelity,
                    frame_row,
                )
                match = ExactSubjectMatch(
                    scene_index=index,
                    provider=provider,
                    provider_id=provider_id,
                    media_path=media_path,
                    source=source,
                    selected_entity=selected_entity,
                    entity_fidelity=entity_fidelity,
                    metadata_confidence=metadata_confidence,
                    proof=proof or "",
                    reason=self._match_reason(proof, selected_entity, entity_fidelity, frame_row),
                )
                if proof:
                    verified.append(match)
                    continue
                if _is_generic_substitute(
                    accepted_terms,
                    selected_entity,
                    entity_fidelity,
                    evidence,
                ):
                    rejected_generic.append(match)

        if len(verified) >= self.config.minimum_verified_exact_matches:
            return self._report(
                topic,
                subject,
                ExactSubjectGateDecision.PASSED,
                "",
                "continue",
                verified,
                rejected_generic,
            )

        if rejected_generic:
            reason = (
                f"only generic or related substitutes were selected for {subject.canonical_entity}; "
                f"{len(verified)}/{self.config.minimum_verified_exact_matches} exact matches proven"
            )
        else:
            reason = (
                f"no authentic exact-subject evidence was selected for {subject.canonical_entity}; "
                f"{len(verified)}/{self.config.minimum_verified_exact_matches} exact matches proven"
            )
        return self._report(
            topic,
            subject,
            ExactSubjectGateDecision.DEFERRED,
            reason,
            "defer_topic_and_select_recovery",
            verified,
            rejected_generic,
        )

    def _accepted_terms(self, subject: SubjectDefinition) -> tuple[str, ...]:
        values: list[str] = [subject.canonical_entity]
        if self.config.allow_aliases:
            values.extend(subject.aliases)
        if self.config.allow_scientific_names:
            values.extend(subject.scientific_names)
        return _dedupe(values)

    @staticmethod
    def _proof_for_asset(
        accepted_terms: Sequence[str],
        evidence: Mapping[str, Any],
        selected_entity: str,
        entity_fidelity: str,
        frame_row: Mapping[str, Any],
    ) -> str:
        if entity_fidelity in {"exact_entity", "exact_alias"} and _matches_any(
            selected_entity or str(evidence.get("requested_entity") or ""),
            accepted_terms,
        ):
            return "metadata_exact" if entity_fidelity == "exact_entity" else "metadata_alias"
        if str(frame_row.get("decision", "")).lower() == "verified" and _matches_any(
            str(frame_row.get("verified_entity") or frame_row.get("expected_entity") or ""),
            accepted_terms,
        ):
            return "frame_verified"
        return ""

    @staticmethod
    def _match_reason(
        proof: str,
        selected_entity: str,
        entity_fidelity: str,
        frame_row: Mapping[str, Any],
    ) -> str:
        if proof:
            return f"{proof} evidence matched {selected_entity or frame_row.get('verified_entity', '')}".strip()
        if entity_fidelity:
            return f"metadata entity fidelity was {entity_fidelity}"
        if frame_row:
            return str(frame_row.get("reason") or "downloaded media was not exact-subject verified")
        return "selected media has no exact-subject evidence"

    def _report(
        self,
        topic: str,
        subject: SubjectDefinition,
        decision: ExactSubjectGateDecision,
        failure_reason: str,
        scheduler_action: str,
        verified: Sequence[ExactSubjectMatch] = (),
        rejected: Sequence[ExactSubjectMatch] = (),
    ) -> ExactSubjectGateReport:
        return ExactSubjectGateReport(
            topic=topic,
            canonical_entity=subject.canonical_entity,
            accepted_aliases=subject.aliases if self.config.allow_aliases else (),
            scientific_names=subject.scientific_names if self.config.allow_scientific_names else (),
            identity_defining=subject.identity_defining,
            decision=decision,
            failure_reason=failure_reason,
            scheduler_action=scheduler_action,
            verified_matches=tuple(verified),
            rejected_generic_matches=tuple(rejected),
            configuration=self.config,
            identity_reason=subject.reason,
        )


_GENERIC_SUBJECTS = {
    "animal", "animals", "bee", "bees", "camel", "camels", "desert", "deserts",
    "forest", "forests", "nature", "ocean", "oceans", "octopus", "octopuses",
    "penguin", "penguins", "rainforest", "rainforests", "shark", "sharks", "storm",
    "storms", "volcano", "volcanoes", "weather", "wildlife",
}
_NON_AUTHENTIC_MARKERS = ("broad_fallback", "gemini_image", "hybrid_composer", "local_explainer")


def subject_definition_from_pipeline(
    *,
    editorial_canon: Any | None,
    canonical_report: Any | None,
    shot_plan: Any | None,
) -> SubjectDefinition:
    """Build the gate's identity definition from existing planning artifacts."""

    canonical = str(
        _asset_value(editorial_canon, "primary_subject")
        or _asset_value(canonical_report, "primary_subject")
        or _asset_value(shot_plan, "primary_subject")
        or ""
    ).strip()
    aliases: list[str] = []
    scientific: list[str] = []
    for intent in tuple(_asset_value(shot_plan, "intents") or ()):
        scene_entity = _asset_value(intent, "scene_entity")
        entity = str(_asset_value(scene_entity, "canonical_entity") or "")
        if _norm(entity) != _norm(canonical):
            continue
        for alias in tuple(_asset_value(scene_entity, "aliases") or ()):
            (scientific if _is_scientific_name(str(alias)) else aliases).append(str(alias))
    for scene in tuple(_asset_value(canonical_report, "scenes") or ()):
        if _norm(str(_asset_value(scene, "canonical_entity") or "")) != _norm(canonical):
            continue
        resolved = _asset_value(scene, "resolved_entity")
        for alias in tuple(_asset_value(resolved, "aliases") or ()):
            (scientific if _is_scientific_name(str(alias)) else aliases).append(str(alias))

    identity, reason = _identity_defining(canonical, editorial_canon, shot_plan)
    return SubjectDefinition(
        canonical_entity=canonical,
        aliases=tuple(item for item in _dedupe(aliases) if _norm(item) != _norm(canonical)),
        scientific_names=tuple(_dedupe(scientific)),
        identity_defining=identity,
        reason=reason,
    )


def _identity_defining(canonical: str, editorial_canon: Any | None, shot_plan: Any | None) -> tuple[bool, str]:
    normalized = _norm(canonical)
    if not normalized:
        return False, "no canonical documentary subject was available"
    if normalized in _GENERIC_SUBJECTS:
        return False, "canonical subject is a generic documentary category"
    entity_types = {
        str(_asset_value(_asset_value(intent, "scene_entity"), "entity_type") or "").lower()
        for intent in tuple(_asset_value(shot_plan, "intents") or ())
        if _norm(str(_asset_value(_asset_value(intent, "scene_entity"), "canonical_entity") or "")) == normalized
    }
    if entity_types & {"species", "landmark", "place"}:
        return True, "scene entity type identifies a specific species, landmark, or place"
    if len(normalized.split()) >= 2:
        return True, "multi-word canonical subject is identity-defining"
    if canonical[:1].isupper():
        return True, "capitalized canonical subject is a named documentary entity"
    title = str(_asset_value(editorial_canon, "documentary_title") or "")
    if normalized and normalized in _norm(title) and canonical[:1].isupper():
        return True, "named subject is explicit in documentary title"
    return False, "canonical subject does not require unique-identity proof"


def _asset_metadata(asset: Any) -> Mapping[str, Any]:
    value = _asset_value(asset, "metadata")
    return value if isinstance(value, Mapping) else {}


def _selection_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    selection = metadata.get("selection")
    return selection if isinstance(selection, Mapping) else metadata


def _verified_rows_by_scene(report: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    if not isinstance(report, Mapping):
        return {}
    result: dict[int, Mapping[str, Any]] = {}
    for row in report.get("scenes", ()):
        if isinstance(row, Mapping):
            try:
                result[int(row.get("scene_index"))] = row
            except (TypeError, ValueError):
                continue
    return result


def _is_authentic_source(provider: str, source: str, media_path: str) -> bool:
    text = " ".join((provider, source, media_path)).casefold()
    return not any(marker in text for marker in _NON_AUTHENTIC_MARKERS)


def _is_generic_substitute(
    accepted_terms: Sequence[str],
    selected_entity: str,
    entity_fidelity: str,
    evidence: Mapping[str, Any],
) -> bool:
    if entity_fidelity in {"generic_category", "related_entity", "environment_only", "unknown"}:
        return True
    requested = str(evidence.get("requested_entity") or "")
    selected = _norm(selected_entity)
    accepted = {_norm(term) for term in accepted_terms}
    if selected and selected not in accepted:
        primary_tokens = set(_norm(accepted_terms[0] if accepted_terms else "").split())
        return bool(set(selected.split()) & primary_tokens) or bool(requested)
    return False


def _matches_any(value: str, terms: Sequence[str]) -> bool:
    normalized = _norm(value)
    return bool(normalized) and normalized in {_norm(term) for term in terms}


def _is_scientific_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][a-z]+\s+[a-z]+(?:\s+[a-z]+)?", str(value).strip()))


def _asset_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = _norm(cleaned)
        if key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _env_flag(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    return default if raw is None else str(raw).strip().lower() not in {"", "0", "false", "no", "off"}


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(values.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
