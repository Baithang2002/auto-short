#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _pipeline_base import main as base_main, OUT_DIR, META_PATH, LOG_PATH


def _build_long_parser():
    ap = argparse.ArgumentParser(description="The Bias Files Long-Form landscape orchestrator")
    ap.add_argument("topic", nargs="?", default="Why Losing $100 Hurts More Than Winning $200",
                    help="Bias / story topic (passed to bias_long.py)")
    ap.add_argument("--platforms", nargs="+", default=["youtube"])
    ap.add_argument("--skip-generate", action="store_true",
                    help="Skip Stage 1; reuse the existing output/upload_metadata.json")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Run Stage 1 only; don't upload")
    ap.add_argument("--upload", type=str,
                    help="Explicit video path (otherwise taken from upload_metadata.json)")
    ap.add_argument("--headless", action="store_true",
                    help="Pass-through to uploader.py")
    ap.add_argument("--generate", action="store_true",
                    help="Force Gemini generation for topic instead of pre-verified Episode 1 script")
    return ap


def _long_extra_builder(args: argparse.Namespace) -> list[str]:
    extra = []
    if args.generate:
        extra += ["--generate"]
    return extra


def _long_meta_resolver(topic: str, out_dir: Path, meta_path: Path) -> dict | None:
    slug = topic.lower().replace(" ", "-").replace("$", "").replace(",", "")
    slug = "".join([c for c in slug if c.isalnum() or c == "-"])[:40]
    slug_meta = out_dir / "long" / f"ep_{slug}" / "upload_metadata.json"
    if slug_meta.exists():
        meta_candidates = [slug_meta]
    else:
        meta_candidates = list((out_dir / "long").glob("**/upload_metadata.json"))
    if not meta_candidates:
        return None
    meta_candidates.sort(key=lambda p: p.stat().st_mtime)
    meta_file = meta_candidates[-1]
    content = meta_file.read_text(encoding="utf-8")
    meta_path.write_text(content, encoding="utf-8")
    return json.loads(content)


def main():
    base_main(
        "bias_long.py",
        prefix="pipeline_biasfiles_long",
        parser_builder=_build_long_parser,
        meta_resolver=_long_meta_resolver,
        extra_builder=_long_extra_builder,
        channel_label="Bias Files Long Pipeline",
    )


if __name__ == "__main__":
    main()
