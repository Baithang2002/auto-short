"""
pipeline_daily.py - the entry point a scheduler should call.

What it does:
  1. Selects a viable, novel topic from configured topic sources.
  2. Falls back to legacy round-robin only when the scheduler is disabled.
  3. Runs pipeline.py with that topic.
  4. Appends a one-line note to state/daily_runs.log with timestamp + topic + exit code.
  5. On Stage 2 failure (any platform), the position still advances - tomorrow gets
     a fresh topic instead of retrying the same one indefinitely. Failures are
     visible in output/upload_log.json and state/daily_runs.log.

Why a wrapper instead of putting this in pipeline.py: scheduling shouldn't
share state with the manual orchestrator. You can still run pipeline.py by
hand with any topic - daily_state.json only tracks the scheduled rotation.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

SRC_DIR = Path(__file__).parent.resolve() / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovideo.intelligence import (
    AutonomousContentScheduler,
    ContentHistoryStore,
    ContentSchedulerConfig,
    SchedulerResult,
    load_topic_sources,
    topic_source_for_path,
)

SCRIPT_DIR = Path(__file__).parent.resolve()
TOPICS     = SCRIPT_DIR / "topics.txt"
STATE_DIR  = SCRIPT_DIR / "state"
OUT_DIR    = SCRIPT_DIR / "output"
STATE      = STATE_DIR / "daily_state.json"
RUN_LOG    = STATE_DIR / "daily_runs.log"
OUTPUT_STATE = OUT_DIR / "daily_state.json"
OUTPUT_RUN_LOG = OUT_DIR / "daily_runs.log"
CONTENT_HISTORY = STATE_DIR / "content_history.json"
SCHEDULER_REPORT = OUT_DIR / "scheduler_report.json"
SOURCE_COVERAGE_REPORT = OUT_DIR / "source_coverage_report.json"
FALLBACK_QUALITY_REPORT = OUT_DIR / "fallback_quality_report.json"
PUBLISH_QUALITY_REPORT = OUT_DIR / "publish_quality_report.json"
VERIFIED_MEDIA_REPORT = OUT_DIR / "verified_media_report.json"
ATTEMPT_REPORTS = (
    SOURCE_COVERAGE_REPORT,
    FALLBACK_QUALITY_REPORT,
    PUBLISH_QUALITY_REPORT,
    VERIFIED_MEDIA_REPORT,
)


def load_topics() -> list:
    if not TOPICS.exists():
        sys.exit(f"[daily] topics file missing: {TOPICS}")
    lines = []
    for raw in TOPICS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    if not lines:
        sys.exit(f"[daily] no usable topics in {TOPICS}")
    return lines


def successful_topics() -> set[str]:
    """Topics with a tracked successful daily run."""
    if not RUN_LOG.exists():
        return set()
    seen: set[str] = set()
    try:
        lines = RUN_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return seen
    for line in lines:
        if "exit=0" not in line or "topic=" not in line:
            continue
        try:
            raw_topic = line.split("topic=", 1)[1].split("  exit=", 1)[0].strip()
            topic = ast.literal_eval(raw_topic)
        except (SyntaxError, ValueError, IndexError):
            continue
        if isinstance(topic, str):
            seen.add(topic)
    return seen


def pick_next(topics: list) -> tuple:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx = 0
    if STATE.exists():
        try:
            idx = int(json.loads(STATE.read_text(encoding="utf-8")).get("next_index", 0))
        except Exception:
            idx = 0
    idx = idx % len(topics)
    original_idx = idx
    already_successful = successful_topics()
    for _ in range(len(topics)):
        topic = topics[idx]
        if topic not in already_successful:
            break
        idx = (idx + 1) % len(topics)
    else:
        idx = original_idx
        topic = topics[idx]
    new_idx = (idx + 1) % len(topics)
    state_text = json.dumps({
        "next_index":   new_idx,
        "last_index":   idx,
        "last_topic":   topic,
        "last_picked":  dt.datetime.now().isoformat(timespec="seconds"),
    }, indent=2)
    STATE.write_text(state_text, encoding="utf-8")
    OUTPUT_STATE.write_text(state_text, encoding="utf-8")
    return idx, topic


def append_log(line: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    with OUTPUT_RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def already_posted_today() -> bool:
    """True if daily_runs.log shows a successful (exit=0) entry for today's date.
    Used so a backup cron doesn't double-post when the primary cron already ran."""
    if not RUN_LOG.exists():
        return False
    today = dt.datetime.now().strftime("%Y-%m-%d")
    try:
        for line in RUN_LOG.read_text(encoding="utf-8").splitlines():
            if line.startswith(today) and "exit=0" in line:
                return True
    except OSError:
        return False
    return False


def schedule_topic(excluded_topics: set[str] | None = None) -> tuple[str | None, str, SchedulerResult | None]:
    """Choose one viable topic and persist scheduling diagnostics/history."""

    config = ContentSchedulerConfig.from_env()
    if not config.enabled:
        _idx, topic = pick_next(load_topics())
        return topic, "", None

    excluded = {topic.casefold() for topic in (excluded_topics or set())}
    sources = [
        topic_source_for_path(SCRIPT_DIR / source_name)
        for source_name in config.topic_sources
    ]
    candidates = [
        candidate for candidate in load_topic_sources(sources)
        if candidate.topic.casefold() not in excluded
    ]
    config = replace(
        config,
        evergreen_topics=tuple(
            topic for topic in config.evergreen_topics if topic.casefold() not in excluded
        ),
    )
    run_id = uuid.uuid4().hex
    history_store = ContentHistoryStore(CONTENT_HISTORY)
    result = AutonomousContentScheduler(config=config).schedule(candidates, history_store.load())
    result.write_json(SCHEDULER_REPORT)
    history_store.record_decisions(result, run_id=run_id)
    return (result.selected.topic if result.selected else None), run_id, result


def source_coverage_deferred(topic: str) -> tuple[bool, str]:
    """Return whether the latest run deferred this exact topic before voice generation."""

    if not SOURCE_COVERAGE_REPORT.exists():
        return False, ""
    try:
        report = json.loads(SOURCE_COVERAGE_REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    if str(report.get("topic", "")).casefold() != topic.casefold():
        return False, ""
    if str(report.get("decision", "")).upper() != "DEFERRED":
        return False, ""
    return True, "; ".join(str(item) for item in report.get("reasons", []) if item)


def _read_json(path: Path) -> dict:
    """Read one JSON diagnostic artifact without treating a missing file as an error."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def clear_attempt_reports() -> None:
    """Remove stale quality reports before a new topic attempt begins."""

    for report_path in ATTEMPT_REPORTS:
        try:
            report_path.unlink()
        except FileNotFoundError:
            continue


def candidate_quality_deferred(topic: str) -> tuple[bool, str]:
    """Return whether a failed attempt should recover with another topic.

    Only content-specific quality outcomes are recoverable here. Provider
    credentials, renderer failures, and upload failures remain terminal so a
    broken deployment is not hidden by repeatedly spending generation quota.
    """

    deferred, reason = source_coverage_deferred(topic)
    if deferred:
        return True, reason or "source coverage preflight deferred topic"

    fallback_report = _read_json(FALLBACK_QUALITY_REPORT)
    if fallback_report and not bool(fallback_report.get("quality_gate_passed", True)):
        return True, "visual fallback quality gate deferred topic"

    verified_report = _read_json(VERIFIED_MEDIA_REPORT)
    rejected_scenes = [
        scene for scene in verified_report.get("scenes", [])
        if isinstance(scene, dict) and str(scene.get("decision", "")).lower() == "rejected"
    ]
    if rejected_scenes:
        return True, "verified media gate rejected planned scene media"

    publish_report = _read_json(PUBLISH_QUALITY_REPORT)
    if str(publish_report.get("verdict", "")).upper() == "DEFERRED":
        return True, "post-render publish quality gate deferred topic"

    return False, ""


def max_topic_attempts() -> int:
    """Load the bounded number of candidate topics permitted in one daily run."""

    try:
        legacy_recoveries = max(
            0,
            int(os.environ.get("AUTO_VIDEO_SOURCE_COVERAGE_MAX_RECOVERIES", "2") or "2"),
        )
    except ValueError:
        legacy_recoveries = 2
    default_attempts = legacy_recoveries + 1
    try:
        return max(
            1,
            int(os.environ.get("AUTO_VIDEO_DAILY_MAX_TOPIC_ATTEMPTS", str(default_attempts))),
        )
    except ValueError:
        return default_attempts


def run_daily() -> int:
    """Publish one quality-approved daily Short, recovering from weak topics."""

    if already_posted_today():
        print(f"[daily] A successful post already happened today. Skipping (backup cron).")
        return 0

    attempt_limit = max_topic_attempts()
    attempted_topics: set[str] = set()
    started = dt.datetime.now()
    last_exit_code = 2

    for attempt_number in range(1, attempt_limit + 1):
        try:
            topic, scheduler_run_id, scheduler_result = schedule_topic(attempted_topics)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"[daily] scheduler failed: {exc}")
            append_log(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  topic=None  exit=2  scheduler_error={exc!r}")
            return 2
        if not topic:
            print("[daily] no eligible topic remains after quality recovery.")
            append_log(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  topic=None  exit=2  scheduler=no_eligible_topic")
            return 2
        if scheduler_result is None:
            print(f"[daily] {dt.datetime.now():%Y-%m-%d %H:%M:%S}  legacy topic rotation: {topic!r}")
        else:
            selected = scheduler_result.selected
            print(
                f"[daily] {dt.datetime.now():%Y-%m-%d %H:%M:%S}  scheduled topic: {topic!r} "
                f"(viability={selected.viability_score:.2f}, rank={selected.ranking_score:.2f})"
            )
        cmd = [sys.executable or "python", str(SCRIPT_DIR / "pipeline.py"),
               topic, "--platforms", "youtube", "--no-interactive"]
        environment = os.environ.copy()
        environment.setdefault("AUTO_VIDEO_SOURCE_COVERAGE_ENFORCE", "true")
        clear_attempt_reports()
        proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR), env=environment)
        last_exit_code = proc.returncode
        if proc.returncode == 0:
            ContentHistoryStore(CONTENT_HISTORY).mark_generated(run_id=scheduler_run_id)
            finished = dt.datetime.now()
            secs = (finished - started).total_seconds()
            append_log(
                f"{started:%Y-%m-%d %H:%M:%S}  topic={topic!r}  exit=0  "
                f"duration={secs:.0f}s  attempts={attempt_number}"
            )
            print(f"[daily] done ({secs:.0f}s, exit 0). Logged to {RUN_LOG}")
            return 0

        deferred, reason = candidate_quality_deferred(topic)
        if not deferred:
            finished = dt.datetime.now()
            secs = (finished - started).total_seconds()
            append_log(
                f"{started:%Y-%m-%d %H:%M:%S}  topic={topic!r}  exit={proc.returncode}  "
                f"duration={secs:.0f}s  critical_failure=true"
            )
            print(f"[daily] stopped after critical failure ({secs:.0f}s, exit {proc.returncode}).")
            return proc.returncode

        ContentHistoryStore(CONTENT_HISTORY).mark_deferred(
            run_id=scheduler_run_id,
            reason=reason,
            status="quality_deferred",
        )
        attempted_topics.add(topic)
        append_log(
            f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  topic={topic!r}  exit={proc.returncode}  "
            f"attempt={attempt_number}/{attempt_limit}  quality_deferred={reason!r}"
        )
        if attempt_number < attempt_limit:
            print(
                f"[daily] quality deferred {topic!r}; selecting recovery topic "
                f"({attempt_number}/{attempt_limit})."
            )

    finished = dt.datetime.now()
    secs = (finished - started).total_seconds()
    print(
        f"[daily] exhausted {attempt_limit} quality-recovery attempts "
        f"({secs:.0f}s, exit {last_exit_code}). Logged to {RUN_LOG}"
    )
    return last_exit_code


def main() -> None:
    """Run the daily scheduler with a process-compatible exit code."""

    sys.exit(run_daily())


if __name__ == "__main__":
    main()
