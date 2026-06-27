#!/usr/bin/env python3
from _pipeline_base import (
    run_stage1, run_stage2, last_log_entry, main,
    SCRIPT_DIR, OUT_DIR, META_PATH, LOG_PATH,
)

if __name__ == "__main__":
    main("auto_short.py", prefix="pipeline", channel_label="Pipeline")
