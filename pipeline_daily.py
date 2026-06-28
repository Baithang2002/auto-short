"""
pipeline_daily.py - the entry point a scheduler should call.

What it does:
  1. Reads topics.txt (one topic per line, # for comments).
  2. Round-robin picks the next topic via a tiny state file (output/daily_state.json).
  3. Runs pipeline.py with that topic.
  4. Appends a one-line note to output/daily_runs.log with timestamp + topic + exit code.
  5. On Stage 2 failure (any platform), the position still advances - tomorrow gets
     a fresh topic instead of retrying the same one indefinitely. Failures are
     visible in output/upload_log.json and output/daily_runs.log.

Why a wrapper instead of putting this in pipeline.py: scheduling shouldn't
share state with the manual orchestrator. You can still run pipeline.py by
hand with any topic - daily_state.json only tracks the scheduled rotation.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
TOPICS     = SCRIPT_DIR / "topics.txt"
OUT_DIR    = SCRIPT_DIR / "output"
STATE      = OUT_DIR / "daily_state.json"
RUN_LOG    = OUT_DIR / "daily_runs.log"


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


def pick_next(topics: list) -> tuple:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx = 0
    if STATE.exists():
        try:
            idx = int(json.loads(STATE.read_text(encoding="utf-8")).get("next_index", 0))
        except Exception:
            idx = 0
    idx = idx % len(topics)
    topic = topics[idx]
    new_idx = (idx + 1) % len(topics)
    STATE.write_text(json.dumps({
        "next_index":   new_idx,
        "last_index":   idx,
        "last_topic":   topic,
        "last_picked":  dt.datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    return idx, topic


def append_log(line: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
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


def main():
    if already_posted_today():
        print(f"[daily] A successful post already happened today. Skipping (backup cron).")
        sys.exit(0)

    topics = load_topics()
    idx, topic = pick_next(topics)
    started = dt.datetime.now()
    print(f"[daily] {started:%Y-%m-%d %H:%M:%S}  topic #{idx+1}/{len(topics)}: {topic!r}")

    # YouTube-only for now (Instagram/Facebook sessions not connected).
    # To re-enable, change to: [..., topic, "--platforms", "youtube", "instagram", "facebook"]
    # --no-interactive: scheduled runs cannot answer stdin prompts. Without
    # this, a missing-broll segment hangs the renderer forever, the daily run
    # never finishes, and tomorrow's run can't start.
    cmd = [sys.executable or "python", str(SCRIPT_DIR / "pipeline.py"),
           topic, "--platforms", "youtube", "--no-interactive"]
    proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR))

    finished = dt.datetime.now()
    secs = (finished - started).total_seconds()
    line = (f"{started:%Y-%m-%d %H:%M:%S}  topic={topic!r}  "
            f"exit={proc.returncode}  duration={secs:.0f}s")
    append_log(line)
    print(f"[daily] done ({secs:.0f}s, exit {proc.returncode}). Logged to {RUN_LOG}")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
