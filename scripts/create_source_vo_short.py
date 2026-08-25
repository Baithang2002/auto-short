"""
create_source_vo_short.py
========================
High-Retention Source-VO Wildlife Documentary Shorts Production Engine.

Transforms BBC / National Geographic 16:9 documentary footage into viral 9:16 vertical shorts:
- Ghost-Blur 4:5 / 9:16 aspect ratio with cinematic blurred background
- Anti-Content-ID color grading (eq=saturation=1.12:contrast=1.04:brightness=-0.02)
- Anti-Content-ID horizontal flip (hflip)
- Anti-Content-ID audio frequency & acoustic fingerprint scrambler
- Elevated kinetic ASS subtitles with safe-zone positioning (MarginV=520)
- EBU R128 integrated loudness normalization (-14 LUFS)
"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent


def parse_time_to_seconds(val: str | float) -> float:
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
Style: Subtitle,{font_name},{font_size},{text_color},{keyword_color},&H00000000,&H90000000,0,0,0,0,100,100,1.2,0,1,4.5,2.5,2,60,60,520,1

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
    duration: float = 58.0,
    ass_subtitle_path: Optional[str | Path] = None,
    framing: str = "ghost-4-5",
    title_banner: Optional[str] = None,
    events: Optional[list[dict[str, Any]]] = None,
    hflip: bool = True,
) -> Path:
    """Renders the 9:16 vertical short from raw documentary source with anti-Content-ID protections."""
    source_path = Path(source_video).resolve()
    output_path = Path(output_video).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Prepare ASS subtitle file if events provided or custom
    if ass_subtitle_path is None and events:
        temp_ass = output_path.with_suffix(".ass")
        generate_ass_subtitles(events, temp_ass, title_banner=title_banner)
        ass_subtitle_path = temp_ass

    # 2. Visual Filtergraph with Anti-Content-ID color grade & framing
    flip_prefix = "hflip," if hflip else ""

    if framing in ["ghost-4-5", "4:5", "ghost-blur"]:
        # 4:5 aspect ratio (1080x1350) centered over blurred 9:16 background
        vf_base = (
            f"[0:v]{flip_prefix}split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,eq=brightness=-0.08:saturation=1.15[bgblur];"
            "[fg]scale=-1:1350,crop=1080:1350:(iw-1080)/2:0,eq=saturation=1.12:contrast=1.04:brightness=-0.02[fg45];"
            "[bgblur][fg45]overlay=0:285"
        )
    elif framing in ["ghost-16-9", "blurred-fill"]:
        # Full 16:9 centered over blurred 9:16 background
        vf_base = (
            f"[0:v]{flip_prefix}split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,eq=brightness=-0.08:saturation=1.15[bgblur];"
            "[fg]scale=1080:-1,eq=saturation=1.12:contrast=1.04:brightness=-0.02[fgscaled];"
            "[bgblur][fgscaled]overlay=0:(1920-H)/2"
        )
    else:  # fullbleed center-crop (9:16)
        vf_base = f"[0:v]{flip_prefix}scale=-1:1920,crop=1080:1920:(iw-1080)/2:0,eq=saturation=1.12:contrast=1.04:brightness=-0.02"

    if ass_subtitle_path and Path(ass_subtitle_path).exists():
        ass_str = str(Path(ass_subtitle_path).resolve()).replace("\\", "/")
        ass_escaped = ass_str.replace(":", "\\:").replace("'", "\\'")
        filter_complex = f"{vf_base},ass='{ass_escaped}'[v]"
    else:
        filter_complex = f"{vf_base}[v]"

    # Anti-Content-ID acoustic fingerprint scrambler + EBU R128 loudness
    audio_filter = "asetrate=44100*1.02,atempo=0.98,highpass=f=60,lowpass=f=16000,loudnorm=I=-14:TP=-1.5:LRA=11"

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
        "-af", audio_filter,
        str(output_path),
    ]

    print(f"[OpenMontage] Executing render for {output_path.name} (hflip={hflip}, duration={duration}s)...")
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
    parser.add_argument("--duration", type=str, default="58.0", help="Duration in seconds (<=58s recommended for Shorts)")
    parser.add_argument("--framing", choices=["fullbleed", "ghost-4-5", "4:5", "ghost-blur", "ghost-16-9", "blurred-fill"], default="ghost-4-5")
    parser.add_argument("--ass", type=str, default=None, help="Path to ASS subtitle file")
    parser.add_argument("--title", type=str, default=None, help="Top hook title banner")
    parser.add_argument("--hflip", action="store_true", default=True, help="Apply horizontal flip")
    parser.add_argument("--no-hflip", dest="hflip", action="store_false", help="Disable horizontal flip")

    args = parser.parse_args()
    source_p = Path(args.source)
    if not source_p.exists():
        print(f"Error: Source video '{source_p}' does not exist.")
        exit(1)

    start_sec = parse_time_to_seconds(args.start)
    dur_sec = parse_time_to_seconds(args.duration)

    if args.output:
        out_p = Path(args.output)
    else:
        out_p = ROOT_DIR / "renders" / f"{source_p.stem}_ghost_4_5.mp4"

    render_source_vo_short(
        source_video=source_p,
        output_video=out_p,
        start_time=start_sec,
        duration=dur_sec,
        ass_subtitle_path=args.ass,
        framing=args.framing,
        title_banner=args.title,
        hflip=args.hflip,
    )


if __name__ == "__main__":
    main()
