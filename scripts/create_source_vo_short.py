#!/usr/bin/env python3
"""Source-VO Short Generator (ANIMAL WILD / BBC Earth style).

Creates high-engagement 9:16 vertical Shorts using authentic documentary
source footage + master narration audio (Sir David Attenborough style),
with stylized kinetic ASS subtitles, top hook banner, and mobile-optimized
loudness mastering.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def parse_time(val: str | float) -> float:
    """Parse timestamp string (e.g. '01:15' or '75.0') to seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if ":" in val_str:
        parts = val_str.split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return float(val_str)


def generate_ass_subtitles(
    events: list[dict[str, Any]],
    output_path: Path,
    title_banner: Optional[str] = None,
    font_name: str = "Impact",
    font_size: int = 64,
    keyword_color: str = "&H0000E6FF&",  # Vibrant gold/yellow in BGR
    text_color: str = "&H00FFFFFF&",     # Pure white
) -> Path:
    """Generate professional ANIMAL WILD styled ASS subtitle file."""
    header = f"""[Script Info]
Title: OpenMontage Source-VO Master
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Header,{font_name},48,{text_color},{keyword_color},&H00000000,&H80000000,0,0,0,0,100,100,1.5,0,1,3.5,2,8,40,40,160,1
Style: Subtitle,{font_name},{font_size},{text_color},{keyword_color},&H00000000,&H90000000,0,0,0,0,100,100,1.2,0,1,4.5,2.5,2,60,60,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    if title_banner:
        # Top persistent hook
        lines.append(f"Dialogue: 0,0:00:00.00,9:59:59.00,Header,,0,0,0,,{title_banner}\n")

    for ev in events:
        start_str = ev["start"]
        end_str = ev["end"]
        text = ev["text"]
        lines.append(f"Dialogue: 0,{start_str},{end_str},Subtitle,,0,0,0,,{text}\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    return output_path


def render_source_vo_short(
    source_video: str | Path,
    output_video: str | Path,
    start_time: float = 0.0,
    duration: float = 60.0,
    ass_subtitle_path: Optional[str | Path] = None,
    framing: str = "ghost-4-5",  # 'ghost-4-5', 'fullbleed', or 'ghost-16-9'
    title_banner: Optional[str] = None,
    events: Optional[list[dict[str, Any]]] = None,
) -> Path:
    """Renders the 9:16 vertical short from raw documentary source."""
    source_path = Path(source_video).resolve()
    output_path = Path(output_video).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Prepare ASS subtitle file if events provided or custom
    if ass_subtitle_path is None and events:
        temp_ass = output_path.with_suffix(".ass")
        generate_ass_subtitles(events, temp_ass, title_banner=title_banner)
        ass_subtitle_path = temp_ass

    # 2. Build FFmpeg Filtergraph
    if framing in ["ghost-4-5", "4:5", "ghost-blur"]:
        # 4:5 aspect ratio (1080x1350) centered over blurred 9:16 background
        vf_base = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,eq=brightness=-0.06:saturation=1.15[bgblur];"
            "[fg]scale=-1:1350,crop=1080:1350:(iw-1080)/2:0[fg45];"
            "[bgblur][fg45]overlay=0:285"
        )
    elif framing in ["ghost-16-9", "blurred-fill"]:
        # Full 16:9 centered over blurred 9:16 background
        vf_base = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,eq=brightness=-0.06:saturation=1.15[bgblur];"
            "[fg]scale=1080:-1[fgscaled];"
            "[bgblur][fgscaled]overlay=0:(1920-H)/2"
        )
    else:  # fullbleed center-crop (9:16)
        vf_base = "[0:v]scale=-1:1920,crop=1080:1920:(iw-1080)/2:0"

    if ass_subtitle_path and Path(ass_subtitle_path).exists():
        ass_str = str(Path(ass_subtitle_path).resolve()).replace("\\", "/")
        ass_escaped = ass_str.replace(":", "\\:").replace("'", "\\'")
        filter_complex = f"{vf_base},ass='{ass_escaped}'[v]"
    else:
        filter_complex = f"{vf_base}[v]"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", str(source_path),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        str(output_path),
    ]

    print(f"[OpenMontage] Executing render for {output_path.name}...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("[FFmpeg STDOUT]", res.stdout)
        print("[FFmpeg STDERR]", res.stderr)
        raise RuntimeError(f"FFmpeg render failed with exit code {res.returncode}: {res.stderr[-500:]}")

    print(f"[OpenMontage] Render complete: {output_path} ({os.path.getsize(output_path)} bytes)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Create ANIMAL WILD style Source-VO Documentary Short")
    parser.add_argument("--source", type=str, required=True, help="Path to master documentary video")
    parser.add_argument("--output", type=str, default=None, help="Output MP4 file path")
    parser.add_argument("--start", type=str, default="0.0", help="Start time (e.g. 10.0 or 01:15)")
    parser.add_argument("--duration", type=str, default="60.0", help="Duration in seconds")
    parser.add_argument("--framing", choices=["fullbleed", "ghost-4-5", "4:5", "ghost-blur", "ghost-16-9", "blurred-fill"], default="ghost-4-5")
    parser.add_argument("--ass", type=str, default=None, help="Path to ASS subtitle file")
    parser.add_argument("--title", type=str, default=None, help="Top banner hook text")

    args = parser.parse_args()

    start_s = parse_time(args.start)
    duration_s = parse_time(args.duration)

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Error: Source video '{source_path}' does not exist.")
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = source_path.parent / f"{source_path.stem}_short_ghost_4_5.mp4"

    render_source_vo_short(
        source_video=source_path,
        output_video=out_path,
        start_time=start_s,
        duration=duration_s,
        ass_subtitle_path=args.ass,
        framing=args.framing,
        title_banner=args.title,
    )


if __name__ == "__main__":
    main()
