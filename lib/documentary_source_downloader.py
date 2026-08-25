"""
documentary_source_downloader.py
Automated 1080p/4K Documentary Video Downloader for OpenMontage.
Supports GitHub Actions / Datacenter runners with JS runtime (Deno/Node) and Cobalt API fallback.
"""

import json
import subprocess
import urllib.request
import urllib.parse
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = ROOT_DIR / "assets" / "documentaries"


class DocumentarySourceDownloader:
    def __init__(self, output_base_dir: Optional[Path] = None):
        self.output_base_dir = output_base_dir or DOCUMENTS_DIR
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def _download_via_cobalt(self, youtube_url: str, target_file: Path) -> bool:
        """Download directly via Cobalt stream API (no bot check)."""
        cobalt_instances = [
            "https://cobalt-api.kwiatekm.com",
            "https://api.cobalt.tools",
        ]
        for instance in cobalt_instances:
            try:
                print(f"[DOWNLOADER] Trying Cobalt stream mirror: {instance}...")
                req = urllib.request.Request(
                    instance,
                    data=json.dumps({"url": youtube_url, "videoQuality": "1080"}).encode("utf-8"),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "OpenMontage/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    stream_url = data.get("url")
                    if stream_url:
                        print(f"[DOWNLOADER] Streaming video from Cobalt...")
                        urllib.request.urlretrieve(stream_url, str(target_file))
                        if target_file.exists() and target_file.stat().st_size > 1_000_000:
                            print(f"[DOWNLOADER] Cobalt download successful: {target_file.stat().st_size} bytes")
                            return True
            except Exception as e:
                print(f"[DOWNLOADER] Cobalt mirror {instance} failed: {e}")
        return False

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

        # Strategy 1: yt-dlp with Node / Deno JS runtime
        cmd_ytdlp = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", "youtube:player_client=ios,android,web",
            "-o", str(target_file),
            url,
        ]

        print(f"[DOWNLOADER] Running yt-dlp download...")
        res = subprocess.run(cmd_ytdlp, capture_output=True, text=True)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            print(f"[DOWNLOADER] Success via yt-dlp: {target_file} ({target_file.stat().st_size} bytes)")
            return target_file

        # Strategy 2: Cobalt Stream API Fallback
        if self._download_via_cobalt(url, target_file):
            return target_file

        # Strategy 3: Generic yt-dlp
        cmd_generic = [
            "yt-dlp",
            "-f", "best",
            "--merge-output-format", "mp4",
            "-o", str(target_file),
            url,
        ]
        res_gen = subprocess.run(cmd_generic, capture_output=True, text=True)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            return target_file

        raise RuntimeError(f"All download strategies failed for {url}. Error: {res.stderr or res_gen.stderr}")
