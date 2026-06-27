"""
pipeline_biasfiles_daily.py - Daily scheduler entry point for THE BIAS FILES.

Identical to pipeline_daily.py except:
  - Reads topics_biasfiles.txt (instead of topics.txt)
  - Keeps its own state file (output/daily_state_biasfiles.json)
  - Writes to its own log (output/daily_runs_biasfiles.log)
  - Calls pipeline_biasfiles.py (instead of pipeline.py)

This means both daily schedulers can run side-by-side without overwriting each
other's rotation state. You can schedule them at different times:
  - 6:00 AM  -> pipeline_daily.py            (nature shorts)
  - 6:30 AM  -> pipeline_biasfiles_daily.py  (bias files shorts)

The nature daily script (pipeline_daily.py) is untouched and still works.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
TOPICS     = SCRIPT_DIR / "topics_biasfiles.txt"
OUT_DIR    = SCRIPT_DIR / "output"
STATE      = OUT_DIR / "daily_state_biasfiles.json"
RUN_LOG    = OUT_DIR / "daily_runs_biasfiles.log"
PIPELINE   = SCRIPT_DIR / "pipeline_biasfiles.py"


def load_topics() -> list:
    if not TOPICS.exists():
        sys.exit(f"[biasfiles-daily] topics file missing: {TOPICS}")
    lines = []
    for raw in TOPICS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    if not lines:
        sys.exit(f"[biasfiles-daily] no usable topics in {TOPICS}")
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


def main():
    topics = load_topics()
    idx, topic = pick_next(topics)
    started = dt.datetime.now()
    print(f"[biasfiles-daily] {started:%Y-%m-%d %H:%M:%S}  topic #{idx+1}/{len(topics)}: {topic!r}")

    # YouTube-only by default. Add other platforms when their uploader sessions
    # are connected: ["youtube", "instagram", "facebook"]
    cmd = [sys.executable or "python", str(PIPELINE), topic, "--platforms", "youtube"]
    proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR))

    finished = dt.datetime.now()
    secs = (finished - started).total_seconds()
    line = (f"{started:%Y-%m-%d %H:%M:%S}  topic={topic!r}  "
            f"exit={proc.returncode}  duration={secs:.0f}s")
    append_log(line)
    print(f"[biasfiles-daily] done ({secs:.0f}s, exit {proc.returncode}). Logged to {RUN_LOG}")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
