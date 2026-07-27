"""Maintain a buffer of fresh topics that already passed source coverage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SRC_DIR = Path(__file__).parent.resolve() / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovideo.intelligence import (  # noqa: E402
    ContentHistoryStore,
    ContentSchedulerConfig,
    TopicBankStateStore,
    TopicBankStatus,
    classify_topic,
    load_topic_sources,
    topic_source_for_path,
)


SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_DIR = SCRIPT_DIR / "state"
OUT_DIR = SCRIPT_DIR / "output"
CONTENT_HISTORY = STATE_DIR / "content_history.json"
TOPIC_BANK_STATE = STATE_DIR / "topic_bank_state.json"
TOPIC_BANK_REPORT = OUT_DIR / "topic_bank_status_report.json"
QUALIFICATION_REPORT = OUT_DIR / "topic_qualification_report.json"
SOURCE_COVERAGE_REPORT = OUT_DIR / "source_coverage_report.json"
EDITORIAL_IDENTITY_REPORT = OUT_DIR / "editorial_identity_report.json"
PIPELINE_STATE = OUT_DIR / "pipeline_state.json"
LAST_SCRIPT = OUT_DIR / "last_script.json"
QUALIFIED_SCRIPT_DIR = STATE_DIR / "qualified_scripts"


@dataclass(frozen=True)
class TopicQualificationConfig:
    """Bounded policy for background source-coverage qualification."""

    target_buffer: int = 3
    max_attempts: int = 6
    timeout_sec: int = 300
    quarantine_days: int = 14

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "TopicQualificationConfig":
        """Load qualification limits from environment variables."""

        values = env if env is not None else os.environ
        return cls(
            target_buffer=max(
                1,
                _env_int(values, "AUTO_VIDEO_TOPIC_QUALIFICATION_TARGET_BUFFER", 3),
            ),
            max_attempts=max(
                1,
                _env_int(values, "AUTO_VIDEO_TOPIC_QUALIFICATION_MAX_ATTEMPTS", 6),
            ),
            timeout_sec=max(
                30,
                _env_int(values, "AUTO_VIDEO_TOPIC_QUALIFICATION_TIMEOUT_SEC", 300),
            ),
            quarantine_days=max(
                0,
                _env_int(values, "AUTO_VIDEO_TOPIC_QUALIFICATION_QUARANTINE_DAYS", 14),
            ),
        )


@dataclass(frozen=True)
class QualificationAttempt:
    """One preflight result retained in the aggregate qualification report."""

    topic: str
    outcome: str
    reason: str
    coverage_ratio: float | None
    return_code: int | None
    started_at: str
    completed_at: str


def load_candidates(config: ContentSchedulerConfig) -> tuple[str, ...]:
    """Load and deduplicate the checked-in bank plus configured topic sources."""

    sources = [
        topic_source_for_path(SCRIPT_DIR / source_name)
        for source_name in config.topic_sources
    ]
    source_topics = [candidate.topic for candidate in load_topic_sources(sources)]
    return _deduplicate(
        (
            *config.coverage_proven_topics,
            *source_topics,
            *config.evergreen_topics,
        )
    )


def select_qualification_candidates(
    topics: Sequence[str],
    statuses: Mapping[str, str],
    *,
    limit: int,
) -> tuple[str, ...]:
    """Select fresh candidates with broad category interleaving."""

    eligible = [
        topic
        for topic in topics
        if statuses.get(_normalise(topic), TopicBankStatus.CANDIDATE.value)
        == TopicBankStatus.CANDIDATE.value
    ]
    buckets: dict[str, list[str]] = {}
    for topic in eligible:
        category = classify_topic(topic).primary.value
        buckets.setdefault(category, []).append(topic)

    selected: list[str] = []
    categories = sorted(buckets)
    while categories and len(selected) < max(0, limit):
        remaining: list[str] = []
        for category in categories:
            bucket = buckets[category]
            if bucket and len(selected) < limit:
                selected.append(bucket.pop(0))
            if bucket:
                remaining.append(category)
        categories = remaining
    return tuple(selected)


def classify_attempt(topic: str, return_code: int) -> tuple[str, str, float | None]:
    """Classify a completed subprocess using fresh diagnostic artifacts."""

    coverage = _read_json(SOURCE_COVERAGE_REPORT)
    if str(coverage.get("topic", "")).casefold() == topic.casefold():
        decision = str(coverage.get("decision", "")).upper()
        ratio = _optional_float(coverage.get("coverage_ratio"))
        if return_code == 0 and decision == "APPROVED":
            return "qualified", "source coverage approved", ratio
        if decision == "DEFERRED":
            reasons = "; ".join(str(item) for item in coverage.get("reasons", []) if item)
            return "deferred", reasons or "source coverage deferred topic", ratio

    editorial = _read_json(EDITORIAL_IDENTITY_REPORT)
    if (
        str(editorial.get("topic", "")).casefold() == topic.casefold()
        and str(editorial.get("decision", "")).upper() == "REJECTED"
    ):
        reasons = "; ".join(str(item) for item in editorial.get("reasons", []) if item)
        return "deferred", reasons or "editorial identity rejected topic", None

    return "technical_failure", f"preflight exited {return_code} without a quality decision", None


def run_preflight(topic: str, *, timeout_sec: int) -> tuple[int | None, str]:
    """Run the existing planning and coverage stages without expensive production."""

    _clear_attempt_state()
    environment = os.environ.copy()
    environment["AUTO_VIDEO_FORCE_RERUN"] = "1"
    environment["AUTO_VIDEO_SOURCE_COVERAGE_ENFORCE"] = "true"
    command = [
        sys.executable or "python",
        str(SCRIPT_DIR / "auto_short.py"),
        topic,
        "--no-interactive",
        "--coverage-preflight-only",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(SCRIPT_DIR),
            env=environment,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return None, f"preflight timed out after {timeout_sec}s"
    return result.returncode, ""


def run_qualification() -> int:
    """Fill the ready-topic buffer within a bounded request budget."""

    scheduler_config = ContentSchedulerConfig.from_env()
    qualification_config = TopicQualificationConfig.from_env()
    topics = load_candidates(scheduler_config)
    store = TopicBankStateStore(TOPIC_BANK_STATE)
    _bootstrap_store(store, topics, scheduler_config.topic_bank_quarantine_days)

    statuses = store.status_map(topics)
    qualified_before = sum(
        status == TopicBankStatus.QUALIFIED.value
        for status in statuses.values()
    )
    required = max(0, qualification_config.target_buffer - qualified_before)
    candidates = select_qualification_candidates(
        topics,
        statuses,
        limit=qualification_config.max_attempts if required else 0,
    )
    attempts: list[QualificationAttempt] = []
    qualified_now = 0

    for topic in candidates:
        started = _utc_now()
        print(f"[qualification] probing {topic!r}")
        return_code, execution_error = run_preflight(
            topic,
            timeout_sec=qualification_config.timeout_sec,
        )
        if return_code is None:
            outcome, reason, coverage_ratio = "technical_failure", execution_error, None
        else:
            outcome, reason, coverage_ratio = classify_attempt(topic, return_code)

        if outcome == "qualified":
            try:
                script_path = _persist_qualified_script(topic)
            except OSError as exc:
                outcome = "technical_failure"
                reason = f"could not persist qualified script: {exc}"
                print(f"[qualification] technical failure for {topic!r}: {reason}")
            else:
                measured_ratio = coverage_ratio or 0.0
                store.mark_qualified(
                    topic,
                    coverage_ratio=measured_ratio,
                    script_path=script_path,
                )
                qualified_now += 1
                print(f"[qualification] qualified {topic!r} coverage={measured_ratio:.0%}")
        elif outcome == "deferred":
            store.mark_failure(
                topic,
                reason=reason,
                quarantine_days=qualification_config.quarantine_days,
            )
            print(f"[qualification] quarantined {topic!r}: {reason}")
        else:
            print(f"[qualification] technical failure for {topic!r}: {reason}")

        attempts.append(
            QualificationAttempt(
                topic=topic,
                outcome=outcome,
                reason=reason,
                coverage_ratio=coverage_ratio,
                return_code=return_code,
                started_at=started,
                completed_at=_utc_now(),
            )
        )
        if qualified_before + qualified_now >= qualification_config.target_buffer:
            break

    final_statuses = store.status_map(topics)
    qualified_after = sum(
        status == TopicBankStatus.QUALIFIED.value
        for status in final_statuses.values()
    )
    store.write_report(TOPIC_BANK_REPORT, topics)
    _write_qualification_report(
        qualification_config,
        attempts,
        candidate_count=len(topics),
        qualified_before=qualified_before,
        qualified_after=qualified_after,
    )
    print(
        f"[qualification] ready buffer={qualified_after}/"
        f"{qualification_config.target_buffer}; attempts={len(attempts)}"
    )
    return 0 if qualified_after >= qualification_config.target_buffer else 1


def _bootstrap_store(
    store: TopicBankStateStore,
    topics: Sequence[str],
    quarantine_days: int,
) -> None:
    history = ContentHistoryStore(CONTENT_HISTORY).load()
    keys = {topic.casefold() for topic in topics}
    store.bootstrap(
        generated=tuple(
            (record.topic, record.generated_at or record.recorded_at)
            for record in history
            if record.status == "generated" and record.topic.casefold() in keys
        ),
        deferred=tuple(
            (record.topic, record.reason, record.recorded_at)
            for record in history
            if record.status in {"coverage_deferred", "quality_deferred"}
            and record.topic.casefold() in keys
        ),
        quarantine_days=quarantine_days,
    )


def _write_qualification_report(
    config: TopicQualificationConfig,
    attempts: Sequence[QualificationAttempt],
    *,
    candidate_count: int,
    qualified_before: int,
    qualified_after: int,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    QUALIFICATION_REPORT.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "candidate_count": candidate_count,
                "qualified_before": qualified_before,
                "qualified_after": qualified_after,
                "target_buffer": config.target_buffer,
                "buffer_ready": qualified_after >= config.target_buffer,
                "configuration": asdict(config),
                "attempts": [asdict(attempt) for attempt in attempts],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _clear_attempt_state() -> None:
    for path in (SOURCE_COVERAGE_REPORT, EDITORIAL_IDENTITY_REPORT, PIPELINE_STATE):
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _persist_qualified_script(topic: str) -> str:
    """Copy the approved script into tracked state for deterministic reuse."""

    if not LAST_SCRIPT.exists():
        raise OSError(f"approved script artifact is missing: {LAST_SCRIPT}")
    digest = hashlib.sha256(_normalise(topic).encode("utf-8")).hexdigest()[:20]
    QUALIFIED_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    destination = QUALIFIED_SCRIPT_DIR / f"{digest}.json"
    shutil.copy2(LAST_SCRIPT, destination)
    return destination.relative_to(SCRIPT_DIR).as_posix()


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _deduplicate(topics: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        cleaned = str(topic).strip()
        key = _normalise(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main() -> None:
    """Run the qualification sweep with a process-compatible exit code."""

    raise SystemExit(run_qualification())


if __name__ == "__main__":
    main()
