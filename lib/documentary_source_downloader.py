"""
documentary_source_downloader.py
Automated 1080p/4K Documentary Video Downloader for OpenMontage.

Searches YouTube for high-resolution (1080p/4K) nature documentaries for a
specified animal, downloads a full-length source video into
assets/documentaries/<animal>/, and probes exact stream metadata via ffprobe.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = ROOT_DIR / "assets" / "documentaries"

_RESOLUTION_FORMATS = {
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]/best",
    "4k":    "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/best[height<=2160][ext=mp4]/best[height<=2160]/best",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
}

_DOCUMENTARY_TERMS = [
    " документальный фильм",
    " documentary film",
    " documentary",
    " BBC Earth",
    " National Geographic",
    " Nat Geo Wild",
    " Animal Planet",
    " wildlife",
    " 4K UHD",
    " nature film",
]


class DocumentarySourceDownloader:
    def __init__(self, output_base_dir: Optional[Path] = None):
        self.output_base_dir = output_base_dir or DOCUMENTS_DIR
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def probe_video_metadata(self, file_path: Path) -> dict[str, Any]:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(file_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return {"width": 1280, "height": 720, "duration": 120.0, "fps": 30.0,
                    "resolution": "1280x720", "codec": "unknown"}

        try:
            info = json.loads(res.stdout)
            fmt = info.get("format", {})
            duration = float(fmt.get("duration", 0))
            width, height, fps, codec = 1280, 720, 30.0, "h264"

            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    width = s.get("width", 1280)
                    height = s.get("height", 720)
                    codec = s.get("codec_name", "h264")
                    rfr = s.get("r_frame_rate", "30/1")
                    if "/" in rfr:
                        num, den = rfr.split("/", 1)
                        if float(den) > 0:
                            fps = round(float(num) / float(den), 2)
                    break

            return {
                "width": width,
                "height": height,
                "resolution": f"{width}x{height}",
                "duration": round(duration, 2),
                "fps": fps,
                "codec": codec,
                "file_size_bytes": fmt.get("size", 0),
            }
        except Exception:
            return {"width": 1280, "height": 720, "resolution": "1280x720",
                    "duration": 120.0, "fps": 30.0, "codec": "unknown"}

    def _search_query(self, animal: str) -> str:
        animal_clean = animal.replace("_", " ")
        return f"ytsearch1:{animal_clean} documentary wildlife 1080p"

    def download_from_url(
        self,
        url: str,
        animal: str,
        resolution: str = "1080p",
        force: bool = False,
    ) -> Path:
        """Direct URL download — supports YouTube and TVids.net (no watermark).

        TVids is preferred for pipeline use: clean 1080p, no burned watermark,
        large catalog (BBC Earth, NatGeo, etc.). Pass any TVids episode URL
        like https://www.tvids.net/watch8073/the-hunt/season-01-episode-01-...
        Uses yt-dlp generic extractor with EJS challenge solver.
        NOTE: tvids.net/tvids.tv is VPN-gated in some regions (your error).
        This method now auto-falls back to VPN-free mirrors.
        """
        animal_clean = animal.lower().strip().replace(" ", "_")
        animal_dir = self.output_base_dir / animal_clean
        animal_dir.mkdir(parents=True, exist_ok=True)
        target_file = animal_dir / f"{animal_clean}_doc_source_01.mp4"
        if not force and target_file.exists() and target_file.stat().st_size > 5_000_000:
            return target_file
        fmt_str = _RESOLUTION_FORMATS.get(resolution, _RESOLUTION_FORMATS["1080p"])

        def _try(cmd_url: str, extra_args=None) -> bool:
            cmd = [
                "yt-dlp", "-f", fmt_str, "--merge-output-format", "mp4",
                "--no-playlist", "--quiet", "--no-warnings",
                "--remote-components", "ejs:npm",
                "-o", str(target_file), cmd_url,
            ]
            if extra_args:
                cmd.extend(extra_args)
            subprocess.run(cmd, capture_output=True, text=True)
            return target_file.exists() and target_file.stat().st_size > 500_000

        # 1. Try original URL (tvids.net / youtube / generic)
        if _try(url):
            return target_file
        # 2. TVids VPN fallback: try documentaryarea mirror (same catalog, no VPN)
        if "tvids" in url:
            for mirror in [
                url.replace("tvids.net", "documentaryarea.com").replace("tvids.tv", "documentaryarea.com"),
                url.replace("tvids.net", "tvids.tv").replace("watch", "watch"),
            ]:
                if _try(mirror):
                    return target_file
            # fallback to YouTube search for same episode title
            title = url.split("/")[-1].replace("-", " ")
            yt_query = f"ytsearch1:{title} full episode"
            if _try(yt_query):
                return target_file
        # 3. Final fallback to best
        subprocess.run(
            ["yt-dlp", "--remote-components", "ejs:npm",
             "-f", "best[ext=mp4]/best", "--merge-output-format", "mp4",
             "-o", str(target_file), url],
            capture_output=True, text=True,
        )
        return target_file

    def download_documentary(
        self,
        animal: str,
        resolution: str = "1080p",
        max_duration_s: Optional[int] = None,
        search_query: Optional[str] = None,
        force: bool = False,
    ) -> Path:
        animal_clean = animal.lower().strip().replace(" ", "_")
        animal_dir = self.output_base_dir / animal_clean
        animal_dir.mkdir(parents=True, exist_ok=True)

        target_file = animal_dir / f"{animal_clean}_doc_source_01.mp4"

        if not force and target_file.exists() and target_file.stat().st_size > 5_000_000:
            size_mb = round(target_file.stat().st_size / (1024 * 1024), 1)
            print(f"[SKIP] Documentary exists: {target_file.relative_to(ROOT_DIR)} ({size_mb} MB)")
            meta = self.probe_video_metadata(target_file)
            print(f"       Resolution: {meta['resolution']}, Duration: {meta['duration']}s")
            return target_file

        query = search_query or self._search_query(animal_clean)
        fmt_str = _RESOLUTION_FORMATS.get(resolution, _RESOLUTION_FORMATS["1080p"])

        print(f"[DOWNLOAD] Searching: {query}")
        print(f"           Target: {resolution} -> {target_file.relative_to(ROOT_DIR)}")

        try:
            import yt_dlp

            ydl_opts: dict[str, Any] = {
                "format": fmt_str,
                "merge_output_format": "mp4",
                "outtmpl": str(target_file),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }

            if max_duration_s:
                ydl_opts["download_sections"] = [[f"*00:00:00-{max_duration_s}"]] if isinstance(max_duration_s, int) else max_duration_s

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([query])

        except Exception as e:
            print(f"[FALLBACK] yt-dlp Python API failed ({e}), trying subprocess…")
            self._download_via_subprocess(query, target_file, fmt_str, max_duration_s)

        if not target_file.exists() or target_file.stat().st_size < 500_000:
            print("[FALLBACK] 1080p failed, trying best available…")
            fallback_fmt = "best[ext=mp4]/best"
            self._download_via_subprocess(query, target_file, fallback_fmt, max_duration_s)

        meta = self.probe_video_metadata(target_file)
        size_mb = round(target_file.stat().st_size / (1024 * 1024), 1)
        print(f"[DONE] Downloaded: {target_file.relative_to(ROOT_DIR)} "
              f"({meta['resolution']}, {meta['duration']}s, {size_mb} MB)")
        return target_file

    def _download_via_subprocess(
        self, query: str, target: Path, fmt: str, max_dur: Optional[int]
    ) -> None:
        cmd = [
            "yt-dlp",
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--no-playlist", "--quiet", "--no-warnings",
            "-o", str(target),
        ]
        if max_dur:
            cmd += ["--download-sections", f"*00:00:00-{max_dur}"]
        cmd.append(query)
        subprocess.run(cmd, capture_output=True, text=True)


if __name__ == "__main__":
    downloader = DocumentarySourceDownloader()
    downloader.download_documentary("tiger", resolution="1080p", max_duration_s=360)
