"""
documentary_source_downloader.py
Automated 1080p/4K Documentary Video Downloader for OpenMontage.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = ROOT_DIR / "assets" / "documentaries"


class DocumentarySourceDownloader:
    def __init__(self, output_base_dir: Optional[Path] = None):
        self.output_base_dir = output_base_dir or DOCUMENTS_DIR
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def download_from_url(
        self,
        url: str,
        animal: str,
        resolution: str = "1080p",
        force: bool = False,
    ) -> Path:
        animal_clean = animal.lower().strip().replace(" ", "_")
        animal_dir = self.output_base_dir / animal_clean
        animal_dir.mkdir(parents=True, exist_ok=True)
        target_file = animal_dir / f"{animal_clean}_doc_source_01.mp4"

        if not force and target_file.exists() and target_file.stat().st_size > 5_000_000:
            return target_file

        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=android,web",
            "-o", str(target_file),
            url,
        ]

        print(f"[DOWNLOADER] Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not target_file.exists():
            print(f"[DOWNLOADER ERROR STDOUT]: {res.stdout}")
            print(f"[DOWNLOADER ERROR STDERR]: {res.stderr}")
            # Fallback attempt with generic best
            fallback_cmd = [
                "yt-dlp",
                "-f", "best",
                "--merge-output-format", "mp4",
                "-o", str(target_file),
                url,
            ]
            res_fb = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if res_fb.returncode != 0 or not target_file.exists():
                raise RuntimeError(f"Failed to download source video from {url}: {res_fb.stderr}")

        print(f"[DOWNLOADER] Successfully downloaded {target_file} ({target_file.stat().st_size} bytes)")
        return target_file
