"""
documentary_source_downloader.py
Automated 1080p/4K Documentary Video Downloader for OpenMontage.
Supports GitHub Actions / Datacenter runners with iOS player client fallback.
"""

import json
import subprocess
import urllib.request
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

        # Strategy 1: iOS client extractor (bypasses datacenter bot check)
        cmd_ios = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=ios",
            "-o", str(target_file),
            url,
        ]

        print(f"[DOWNLOADER] Attempting iOS client download...")
        res = subprocess.run(cmd_ios, capture_output=True, text=True)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            print(f"[DOWNLOADER] Success via iOS client: {target_file} ({target_file.stat().st_size} bytes)")
            return target_file

        # Strategy 2: MWeb / Android Embedded client
        print(f"[DOWNLOADER] Attempting MWeb client fallback...")
        cmd_mweb = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=mweb,android_embedded",
            "-o", str(target_file),
            url,
        ]
        res_mweb = subprocess.run(cmd_mweb, capture_output=True, text=True)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            print(f"[DOWNLOADER] Success via MWeb: {target_file} ({target_file.stat().st_size} bytes)")
            return target_file

        # Strategy 3: Generic fallback
        print(f"[DOWNLOADER] Attempting generic fallback...")
        cmd_generic = [
            "yt-dlp",
            "-f", "best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(target_file),
            url,
        ]
        res_generic = subprocess.run(cmd_generic, capture_output=True, text=True)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            print(f"[DOWNLOADER] Success via generic: {target_file} ({target_file.stat().st_size} bytes)")
            return target_file

        raise RuntimeError(f"All download strategies failed for {url}. Last error: {res_generic.stderr or res.stderr}")
