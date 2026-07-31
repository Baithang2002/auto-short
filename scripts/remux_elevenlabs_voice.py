#!/usr/bin/env python3
"""Regenerate narration with one ElevenLabs voice and remux it onto a rendered video."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--voice-slot", type=int, default=1, help="1-based voice slot from ELEVENLABS_VOICE_IDS")
    parser.add_argument("--output", default="final_remuxed_voice.mp4")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    metadata_path = artifact_dir / "upload_metadata.json"
    video_path = artifact_dir / "final_yt_safe.mp4"
    if not video_path.exists():
        video_path = artifact_dir / "final.mp4"
    if not metadata_path.exists():
        raise SystemExit(f"upload_metadata.json missing in {artifact_dir}")
    if not video_path.exists():
        raise SystemExit(f"rendered video missing in {artifact_dir}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    segments = metadata.get("segments") or []
    narration = "\n\n".join(
        str(segment.get("narration", "")).strip()
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("narration", "")).strip()
    )
    if not narration:
        raise SystemExit("No segment narration found in upload_metadata.json")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    raw_voice_ids = os.environ.get("ELEVENLABS_VOICE_IDS", "") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    voice_ids = [item.strip() for item in raw_voice_ids.split(",") if item.strip()]
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is not configured")
    if not voice_ids:
        raise SystemExit("ELEVENLABS_VOICE_IDS or ELEVENLABS_VOICE_ID is not configured")

    voice_index = (max(1, args.voice_slot) - 1) % len(voice_ids)
    voice_id = voice_ids[voice_index]
    print(f"[voice-remux] Using ElevenLabs voice slot {voice_index + 1}/{len(voice_ids)}")

    voice_path = artifact_dir / f"remux_voice_slot_{voice_index + 1}.mp3"
    generate_elevenlabs_audio(
        api_key=api_key,
        voice_id=voice_id,
        text=narration,
        output_path=voice_path,
        model=os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip() or "eleven_multilingual_v2",
    )

    video_duration = media_duration(video_path)
    voice_duration = media_duration(voice_path)
    if video_duration <= 0 or voice_duration <= 0:
        raise SystemExit(f"Invalid durations video={video_duration:.2f}s voice={voice_duration:.2f}s")

    output_path = artifact_dir / args.output
    tempo = voice_duration / video_duration
    filter_graph = f"[1:a]{atempo_chain(tempo)},apad,atrim=0:{video_duration:.3f},asetpts=N/SR/TB[a]"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(voice_path),
        "-filter_complex", filter_graph,
        "-map", "0:v:0",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ], check=True)

    report = {
        "source_video": str(video_path.name),
        "output_video": str(output_path.name),
        "voice_slot": voice_index + 1,
        "voice_count": len(voice_ids),
        "video_duration_sec": round(video_duration, 3),
        "generated_voice_duration_sec": round(voice_duration, 3),
        "tempo": round(tempo, 5),
    }
    (artifact_dir / "voice_remux_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[voice-remux] Wrote {output_path} ({video_duration:.1f}s)")


def generate_elevenlabs_audio(*, api_key: str, voice_id: str, text: str, output_path: Path, model: str) -> None:
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)


def media_duration(path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], check=True, capture_output=True, text=True)
    return float(result.stdout.strip() or 0.0)


def atempo_chain(tempo: float) -> str:
    tempo = max(0.1, min(tempo, 10.0))
    parts: list[str] = []
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    parts.append(f"atempo={tempo:.5f}")
    return ",".join(parts)


if __name__ == "__main__":
    main()
