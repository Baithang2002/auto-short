#!/usr/bin/env python3
"""
upload_worker.py - Queue-aware upload worker using real Playwright uploaders.

Scans videos/approved/ for items whose scheduled time has passed and dispatches
to real browser-based uploaders (YouTube, Instagram, Facebook) via uploader.py.

Usage:
    python upload_worker.py            # one pass
    python upload_worker.py --loop 60  # poll every 60s
    python upload_worker.py --headless # use headless browser (not recommended)
"""
from __future__ import annotations

import sys
import time
import argparse
import datetime as dt
from pathlib import Path

import video_queue as q
from uploader import run_uploads, SESSION_DIR

SCRIPT_DIR = Path(__file__).parent.resolve()


def _build_uploader_metadata(meta: dict) -> dict:
    """Bridge queue metadata fields to the format uploader.py expects."""
    title = meta.get("title", "New Short")
    return {
        "title":                title,
        "youtube_title":        meta.get("youtube_title") or title,
        "youtube_description":  meta.get("youtube_description") or meta.get("description") or title,
        "facebook_description": meta.get("facebook_description") or meta.get("description") or title,
        "instagram_caption":    meta.get("instagram_caption") or title,
    }


def _resolve_video_path(meta: dict) -> Path:
    """Resolve the video file path from queue metadata."""
    # Try absolute path first
    vp = meta.get("video_path")
    if vp:
        p = Path(vp)
        if p.exists():
            return p
    # Fall back to looking inside the video's queue folder
    folder = meta.get("_folder")
    if folder:
        p = Path(folder) / (meta.get("video_file") or "video.mp4")
        if p.exists():
            return p
    raise FileNotFoundError(f"Cannot find video for {meta.get('id', '?')}")


def run_once(*, headless: bool = False) -> int:
    """Process all due uploads in one pass. Returns number processed."""
    q.ensure_dirs()

    if not SESSION_DIR.exists():
        print(f"[!] No browser session found at {SESSION_DIR}")
        print(f"    Run: python uploader.py --login")
        return 0

    due = q.list_due_for_upload()
    if not due:
        print(f"[{dt.datetime.now():%H:%M:%S}] nothing due.")
        return 0

    # Group by video id so we open the browser once per video
    by_video: dict[str, list[str]] = {}
    video_metas: dict[str, dict] = {}
    for meta, platform in due:
        vid = meta["id"]
        by_video.setdefault(vid, []).append(platform)
        video_metas[vid] = meta

    n = 0
    for vid, platforms in by_video.items():
        meta = video_metas[vid]
        try:
            video_path = _resolve_video_path(meta)
        except FileNotFoundError as e:
            print(f"[{dt.datetime.now():%H:%M:%S}] {vid}: {e}")
            continue

        uploader_meta = _build_uploader_metadata(meta)
        print(f"[{dt.datetime.now():%H:%M:%S}] {vid} -> {', '.join(platforms)}")

        try:
            results = run_uploads(video_path, platforms, uploader_meta, headless=headless)
        except SystemExit:
            print(f"    [!] Browser launch failed for {vid}")
            for platform in platforms:
                q.record_upload_result(vid, platform, {
                    "status": "error", "error": "browser launch failed"
                })
            continue
        except Exception as e:
            print(f"    [!] Unexpected error for {vid}: {e}")
            for platform in platforms:
                q.record_upload_result(vid, platform, {
                    "status": "error", "error": repr(e)
                })
            continue

        for platform, res in results.items():
            q.record_upload_result(vid, platform, res)
            status = res.get("status", "?")
            if status == "ok":
                print(f"    [OK]  {platform:9s}  {res.get('url', '')}")
            else:
                print(f"    [X]   {platform:9s}  {res.get('error', '')}")
            n += 1

    return n


def main():
    ap = argparse.ArgumentParser(
        description="Queue-aware upload worker (uses real browser uploaders)"
    )
    ap.add_argument("--loop", type=int, default=0,
                    help="Poll interval in seconds. 0 = single pass.")
    ap.add_argument("--headless", action="store_true",
                    help="Run browser headless (not recommended for IG/FB).")
    args = ap.parse_args()

    if args.loop <= 0:
        run_once(headless=args.headless)
        return

    print(f"upload_worker: polling every {args.loop}s. Ctrl-C to stop.")
    while True:
        try:
            run_once(headless=args.headless)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[worker error] {e}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
