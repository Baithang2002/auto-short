"""Persistent burn-in state for autonomous topic candidates."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class TopicBankStatus(str, Enum):
    """Current production-readiness state of one topic-bank entry."""

    CANDIDATE = "candidate"
    PROVEN = "proven"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class TopicBankRecord:
    """Durable burn-in result for one topic."""

    topic: str
    status: TopicBankStatus = TopicBankStatus.CANDIDATE
    success_count: int = 0
    failure_count: int = 0
    last_attempt_at: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    last_failure_reason: str = ""
    quarantine_until: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the record to stable JSON."""

        payload = asdict(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "TopicBankRecord":
        """Deserialize one record while preserving forward compatibility."""

        raw_status = str(raw.get("status", TopicBankStatus.CANDIDATE.value)).lower()
        try:
            status = TopicBankStatus(raw_status)
        except ValueError:
            status = TopicBankStatus.CANDIDATE
        return cls(
            topic=str(raw.get("topic", "")).strip(),
            status=status,
            success_count=max(0, int(raw.get("success_count", 0) or 0)),
            failure_count=max(0, int(raw.get("failure_count", 0) or 0)),
            last_attempt_at=str(raw.get("last_attempt_at", "")),
            last_success_at=str(raw.get("last_success_at", "")),
            last_failure_at=str(raw.get("last_failure_at", "")),
            last_failure_reason=str(raw.get("last_failure_reason", "")),
            quarantine_until=str(raw.get("quarantine_until", "")),
        )


class TopicBankStateStore:
    """Atomic filesystem store for candidate, proven, and quarantined topics."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[TopicBankRecord]:
        """Load valid records, returning an empty state when the file is absent."""

        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        records = payload.get("records", ()) if isinstance(payload, dict) else ()
        if not isinstance(records, list):
            raise ValueError(f"{self.path} must contain a records list")
        return [
            TopicBankRecord.from_dict(record)
            for record in records
            if isinstance(record, dict) and str(record.get("topic", "")).strip()
        ]

    def save(self, records: Sequence[TopicBankRecord]) -> None:
        """Atomically save the current topic-bank projection."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": _utc_now(),
                    "records": [record.to_dict() for record in records],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def bootstrap(
        self,
        *,
        generated: Sequence[tuple[str, str]] = (),
        deferred: Sequence[tuple[str, str, str]] = (),
        quarantine_days: int,
    ) -> None:
        """Seed missing burn-in records from the existing content history."""

        records = self.load()
        known = {_normalise(record.topic) for record in records}
        changed = False
        for topic, generated_at in generated:
            key = _normalise(topic)
            if not key or key in known:
                continue
            timestamp = generated_at or _utc_now()
            records.append(
                TopicBankRecord(
                    topic=topic,
                    status=TopicBankStatus.PROVEN,
                    success_count=1,
                    last_attempt_at=timestamp,
                    last_success_at=timestamp,
                )
            )
            known.add(key)
            changed = True
        for topic, reason, deferred_at in deferred:
            key = _normalise(topic)
            if not key or key in known:
                continue
            timestamp = deferred_at or _utc_now()
            attempted = _parse_timestamp(timestamp) or datetime.now(UTC)
            records.append(
                TopicBankRecord(
                    topic=topic,
                    status=TopicBankStatus.QUARANTINED,
                    failure_count=1,
                    last_attempt_at=timestamp,
                    last_failure_at=timestamp,
                    last_failure_reason=reason,
                    quarantine_until=_format_timestamp(
                        attempted + timedelta(days=max(0, quarantine_days))
                    ),
                )
            )
            known.add(key)
            changed = True
        if changed:
            self.save(sorted(records, key=lambda record: record.topic.casefold()))

    def status_map(
        self,
        topics: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> dict[str, str]:
        """Return effective statuses, treating expired quarantines as candidates."""

        current = (now or datetime.now(UTC)).astimezone(UTC)
        records = {_normalise(record.topic): record for record in self.load()}
        statuses: dict[str, str] = {}
        for topic in topics:
            key = _normalise(topic)
            if not key:
                continue
            record = records.get(key)
            statuses[key] = _effective_status(record, current).value
        return statuses

    def mark_success(self, topic: str, *, attempted_at: str | None = None) -> None:
        """Promote a topic after a complete successful pipeline run."""

        timestamp = attempted_at or _utc_now()
        records = self.load()
        current = _find_record(records, topic)
        updated = TopicBankRecord(
            topic=topic,
            status=TopicBankStatus.PROVEN,
            success_count=(current.success_count if current else 0) + 1,
            failure_count=current.failure_count if current else 0,
            last_attempt_at=timestamp,
            last_success_at=timestamp,
            last_failure_at=current.last_failure_at if current else "",
            last_failure_reason="",
            quarantine_until="",
        )
        self.save(_upsert(records, updated))

    def mark_failure(
        self,
        topic: str,
        *,
        reason: str,
        quarantine_days: int,
        attempted_at: str | None = None,
    ) -> None:
        """Quarantine a topic after a content-specific quality deferral."""

        timestamp = attempted_at or _utc_now()
        attempted = _parse_timestamp(timestamp) or datetime.now(UTC)
        quarantine_until = attempted + timedelta(days=max(0, quarantine_days))
        records = self.load()
        current = _find_record(records, topic)
        updated = TopicBankRecord(
            topic=topic,
            status=TopicBankStatus.QUARANTINED,
            success_count=current.success_count if current else 0,
            failure_count=(current.failure_count if current else 0) + 1,
            last_attempt_at=timestamp,
            last_success_at=current.last_success_at if current else "",
            last_failure_at=timestamp,
            last_failure_reason=reason,
            quarantine_until=_format_timestamp(quarantine_until),
        )
        self.save(_upsert(records, updated))

    def write_report(
        self,
        path: Path,
        topics: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> Path:
        """Write an inspectable status snapshot for scheduled-run diagnostics."""

        current = (now or datetime.now(UTC)).astimezone(UTC)
        records = {_normalise(record.topic): record for record in self.load()}
        entries: list[dict[str, object]] = []
        counts = {status.value: 0 for status in TopicBankStatus}
        seen: set[str] = set()
        for topic in topics:
            key = _normalise(topic)
            if not key or key in seen:
                continue
            seen.add(key)
            record = records.get(key)
            status = _effective_status(record, current)
            counts[status.value] += 1
            entries.append(
                {
                    "topic": topic,
                    "effective_status": status.value,
                    "record": record.to_dict() if record else None,
                }
            )
        for key, record in records.items():
            if key in seen:
                continue
            status = _effective_status(record, current)
            counts[status.value] += 1
            entries.append(
                {
                    "topic": record.topic,
                    "effective_status": status.value,
                    "record": record.to_dict(),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "generated_at": _format_timestamp(current),
                    "summary": counts,
                    "topics": entries,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path


def _effective_status(
    record: TopicBankRecord | None,
    now: datetime,
) -> TopicBankStatus:
    if record is None:
        return TopicBankStatus.CANDIDATE
    if record.status != TopicBankStatus.QUARANTINED:
        return record.status
    quarantine_until = _parse_timestamp(record.quarantine_until)
    if quarantine_until is None or quarantine_until <= now:
        return TopicBankStatus.CANDIDATE
    return TopicBankStatus.QUARANTINED


def _find_record(
    records: Sequence[TopicBankRecord],
    topic: str,
) -> TopicBankRecord | None:
    key = _normalise(topic)
    return next((record for record in records if _normalise(record.topic) == key), None)


def _upsert(
    records: Sequence[TopicBankRecord],
    updated: TopicBankRecord,
) -> list[TopicBankRecord]:
    key = _normalise(updated.topic)
    result = [record for record in records if _normalise(record.topic) != key]
    result.append(updated)
    return sorted(result, key=lambda record: record.topic.casefold())


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return _format_timestamp(datetime.now(UTC))
