from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
SRC_DIR    = SCRIPT_DIR / "src"
OUT_DIR    = SCRIPT_DIR / "output"
META_PATH  = OUT_DIR / "upload_metadata.json"
LOG_PATH   = OUT_DIR / "upload_log.json"
QUALITY_REPORT_PATH = OUT_DIR / "publish_quality_report.json"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovideo.pipeline import upload_allowed_from_report


def _python() -> str:
    return sys.executable or "python"


def run_stage1(engine_script: str, topic: str, extra: list[str],
               prefix: str = "pipeline") -> dict:
    engine = SCRIPT_DIR / engine_script
    cmd = [_python(), str(engine), topic, *extra]
    print(f"[{prefix}] generating: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(SCRIPT_DIR), stdin=sys.stdin)
    if r.returncode != 0:
        sys.exit(f"[{prefix}] Stage 1 ({engine_script}) failed with exit {r.returncode}")
    if not META_PATH.exists():
        sys.exit(f"[{prefix}] Stage 1 finished but output/upload_metadata.json was not written.")
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def run_stage2(video_path: Path, platforms: list[str], headless: bool,
               prefix: str = "pipeline") -> None:
    cmd = [
        _python(), str(SCRIPT_DIR / "uploader.py"),
        "--upload", str(video_path),
        "--platforms", *platforms,
    ]
    if headless:
        cmd.append("--headless")
    print(f"[{prefix}] uploading:  {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if r.returncode != 0:
        sys.exit(f"[{prefix}] Stage 2 (uploader.py) failed with exit {r.returncode}")


def last_log_entry(log_path: Path = LOG_PATH) -> Optional[dict]:
    if not log_path.exists():
        return None
    try:
        history = json.loads(log_path.read_text(encoding="utf-8"))
        runs = history.get("runs") or []
        return runs[-1] if runs else None
    except Exception:
        return None


MetaResolver = Callable[[str, Path, Path], Optional[dict]]


def build_standard_parser(description: str = "auto_short + uploader orchestrator",
                          extra_args: Optional[Callable] = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("topic", nargs="?", default="",
                    help="Niche/topic for the video (passed to the engine)")
    ap.add_argument("--platforms", nargs="+", default=["youtube", "instagram", "facebook"])
    ap.add_argument("--skip-generate", action="store_true",
                    help="Skip Stage 1; reuse the existing output/upload_metadata.json")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Run Stage 1 only; don't upload")
    ap.add_argument("--upload", type=str,
                    help="Explicit video path (otherwise taken from upload_metadata.json)")
    ap.add_argument("--headless", action="store_true",
                    help="Pass-through to uploader.py (not recommended for IG/FB)")
    # Stage-1 pass-through flags
    ap.add_argument("--duration",  type=int)
    ap.add_argument("--landscape", action="store_true")
    ap.add_argument("--hybrid",    action="store_true")
    ap.add_argument("--dalle",     action="store_true")
    ap.add_argument("--compare",   action="store_true")
    ap.add_argument("--music", type=str, default="",
                    help="Optional background music file passed to engine")
    ap.add_argument("--music-volume", type=float,
                    help="Background music volume passed to engine")
    ap.add_argument("--no-music", action="store_true",
                    help="Disable background music")
    ap.add_argument("--review-broll", action="store_true",
                    help="Review and customize b-roll queries per segment before fetching")
    ap.add_argument("--no-interactive", action="store_true",
                    help="Disable the interactive 'enter clip path' fallback. Required for "
                         "scheduled runs - otherwise the renderer hangs on stdin if b-roll fails.")
    ap.add_argument("--review", action="store_true",
                    help="Generate only; skip upload and leave video in the review queue. "
                         "Open the dashboard with: python review_dashboard.py")
    return ap


def build_stage1_extra(args: argparse.Namespace) -> list[str]:
    extra = []
    if args.duration:  extra += ["--duration", str(args.duration)]
    if args.landscape: extra += ["--landscape"]
    if args.hybrid:    extra += ["--hybrid"]
    if args.dalle:     extra += ["--dalle"]
    if args.compare:   extra += ["--compare"]
    if args.music:     extra += ["--music", args.music]
    if args.music_volume is not None: extra += ["--music-volume", str(args.music_volume)]
    if args.no_music:  extra += ["--no-music"]
    if args.review_broll: extra += ["--review-broll"]
    if args.no_interactive: extra += ["--no-interactive"]
    return extra


def default_meta_resolver(topic: str, out_dir: Path, meta_path: Path) -> Optional[dict]:
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return None


def main(engine_script: str, *,
         prefix: str = "pipeline",
         parser_builder: Callable = build_standard_parser,
         meta_resolver: MetaResolver = default_meta_resolver,
         extra_builder: Callable = build_stage1_extra,
         channel_label: str = "Pipeline") -> None:
    ap = parser_builder()
    args = ap.parse_args()

    if not args.skip_generate and not args.topic:
        ap.error("A topic is required unless --skip-generate is set.")

    # ---- Stage 1 ----
    if args.skip_generate:
        if not META_PATH.exists():
            sys.exit(f"[{prefix}] --skip-generate set, but output/upload_metadata.json is missing.")
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        print(f"[{prefix}] reusing metadata: {meta.get('title')!r}")
    else:
        extra = extra_builder(args)
        meta = run_stage1(engine_script, args.topic, extra, prefix=prefix)

    video_path = Path(args.upload).resolve() if args.upload else Path(meta["video_path"]).resolve()
    if not video_path.exists():
        sys.exit(f"[{prefix}] video file missing: {video_path}")

    print(f"[{prefix}] Stage 1 OK -> {video_path}")
    print(f"           title:     {meta.get('youtube_title') or meta.get('title')}")
    print(f"           hashtags:  {' '.join(meta.get('hashtags') or [])}")

    if args.skip_upload or getattr(args, 'review', False):
        if getattr(args, 'review', False):
            print(f"[{prefix}] Video queued for review. Open the dashboard:")
            print("           python review_dashboard.py")
            print("           Then run the upload worker when ready:")
            print("           python upload_worker.py")
        else:
            print(f"[{prefix}] --skip-upload set; stopping after generation.")
        return

    enforce_quality_gate = os.environ.get(
        "AUTO_VIDEO_ENFORCE_PUBLISH_QUALITY_GATE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    may_upload, quality_reason = upload_allowed_from_report(
        QUALITY_REPORT_PATH,
        enforce=enforce_quality_gate,
    )
    if not may_upload:
        print(f"[{prefix}] Upload deferred: {quality_reason}.")
        print(f"[{prefix}] Render remains in the pending queue for review.")
        return

    # ---- Stage 2 ----
    run_stage2(video_path, args.platforms, args.headless, prefix=prefix)

    # ---- Summary ----
    entry = last_log_entry(LOG_PATH) or {}
    results = entry.get("results", {}) or {}
    print(f"\n=== {channel_label} summary ===")
    print(f"  topic:       {args.topic or meta.get('niche') or '(reused)'}")
    print(f"  video:       {video_path}")
    print(f"  finished at: {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    for platform in args.platforms:
        res = results.get(platform, {})
        status = res.get("status", "?")
        if status == "ok":
            print(f"    [OK]  {platform:9s}  {res.get('url','')}")
        else:
            print(f"    [X]   {platform:9s}  {res.get('error','')}")
    print(f"  log:  {LOG_PATH}")
