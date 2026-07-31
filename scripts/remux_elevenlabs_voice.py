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

    load_local_env(Path(__file__).resolve().parents[1] / ".env")

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

    raw_voice_ids = os.environ.get("ELEVENLABS_VOICE_IDS", "") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    voice_ids = [item.strip() for item in raw_voice_ids.split(",") if item.strip()]
    if not os.environ.get("ELEVENLABS_API_KEY", "").strip():
        raise SystemExit("ELEVENLABS_API_KEY is not configured")
    if not voice_ids:
        raise SystemExit("ELEVENLABS_VOICE_IDS or ELEVENLABS_VOICE_ID is not configured")

    voice_index = (max(1, args.voice_slot) - 1) % len(voice_ids)
    voice_id = voice_ids[voice_index]
    print(f"[voice-remux] Using ElevenLabs voice slot {voice_index + 1}/{len(voice_ids)}")
    accounts = elevenlabs_accounts(primary_voice_id=voice_id)

    voice_path = artifact_dir / f"remux_voice_slot_{voice_index + 1}.mp3"
    account_index, generated_voice_id = generate_elevenlabs_audio(
        accounts=accounts,
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
        "account_slot": account_index + 1,
        "generated_voice_id": generated_voice_id,
        "video_duration_sec": round(video_duration, 3),
        "generated_voice_duration_sec": round(voice_duration, 3),
        "tempo": round(tempo, 5),
    }
    (artifact_dir / "voice_remux_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[voice-remux] Wrote {output_path} ({video_duration:.1f}s)")


def load_local_env(path: Path) -> None:
    """Load simple KEY=VALUE lines for local remux runs without printing secrets."""

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def elevenlabs_accounts(*, primary_voice_id: str) -> list[tuple[str, str]]:
    accounts: list[tuple[str, str]] = []
    for index in range(1, 11):
        if index == 1:
            api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            voice_id = primary_voice_id.strip()
        else:
            api_key = os.environ.get(f"ELEVENLABS_API_KEY_{index}", "").strip()
            voice_id = os.environ.get(f"ELEVENLABS_VOICE_ID_{index}", "").strip()
        if not api_key and not voice_id:
            break
        if api_key and voice_id:
            accounts.append((api_key, voice_id))
    return accounts


def generate_elevenlabs_audio(
    *,
    accounts: list[tuple[str, str]],
    text: str,
    output_path: Path,
    model: str,
) -> tuple[int, str]:
    if not accounts:
        raise SystemExit("No complete ElevenLabs account + voice pairs are configured")
    errors: list[str] = []
    for account_index, (api_key, voice_id) in enumerate(accounts):
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
        try:
            response.raise_for_status()
        except requests.HTTPError:
            detail = response.text[:300].replace(api_key, "[REDACTED]").replace(voice_id, "[REDACTED]")
            errors.append(f"account {account_index + 1}/{len(accounts)} HTTP {response.status_code}: {detail}")
            if response.status_code in {401, 402, 429}:
                continue
            break
        output_path.write_bytes(response.content)
        return account_index, voice_id
    raise SystemExit("ElevenLabs request failed for all accounts: " + "; ".join(errors))


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
