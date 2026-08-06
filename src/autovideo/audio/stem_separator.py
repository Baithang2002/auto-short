"""Automated AI Stem Separation module using Meta's Demucs model.

Isolates vocals, background music, and natural ambient sounds from any input video/audio.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class StemSeparator:
    """Manages Demucs AI stem separation for audio/video media."""

    def __init__(self, model_name: str = "htdemucs", device: Optional[str] = None) -> None:
        self.model_name = model_name
        self.device = device

    @staticmethod
    def is_available() -> bool:
        """Check if Demucs executable or python package is available in the environment."""
        if shutil.which("demucs"):
            return True
        try:
            import demucs  # type: ignore
            return True
        except ImportError:
            return False

    def separate_stems(
        self,
        input_path: Path | str,
        output_dir: Path | str,
        *,
        model_name: Optional[str] = None,
        two_stems: Optional[str] = None,
    ) -> Dict[str, Path]:
        """Separate an input media file (MP4/MP3/WAV/etc.) into individual audio stems.

        Returns a dictionary mapping stem names ('vocals', 'ambient', 'drums', 'bass')
        to their separated file paths on disk.
        """
        input_file = Path(input_path).resolve()
        if not input_file.exists():
            raise FileNotFoundError(f"Input media file not found: {input_file}")

        out_dir = Path(output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        target_model = model_name or self.model_name
        cmd = [
            shutil.which("demucs") or "demucs",
            "-n", target_model,
            "--out", str(out_dir),
        ]

        if two_stems:
            cmd.extend(["--two-stems", two_stems])

        if self.device:
            cmd.extend(["-d", self.device])

        cmd.append(str(input_file))

        logger.info(f"[StemSeparator] Running Demucs separation on {input_file.name} (model={target_model})")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error(f"[StemSeparator] Demucs failed: {res.stderr}")
            raise RuntimeError(f"Demucs stem separation failed: {res.stderr[:300]}")

        track_name = input_file.stem
        stems_folder = out_dir / target_model / track_name

        result_stems: Dict[str, Path] = {}
        stem_mapping = {
            "vocals": "vocals.wav",
            "ambient": "other.wav",
            "music": "no_vocals.wav" if two_stems == "vocals" else "music.wav",
            "drums": "drums.wav",
            "bass": "bass.wav",
        }

        for key, filename in stem_mapping.items():
            candidate = stems_folder / filename
            if candidate.exists():
                result_stems[key] = candidate

        return result_stems

    def extract_ambient_audio(
        self,
        input_path: Path | str,
        output_path: Path | str,
        temp_dir: Optional[Path | str] = None,
    ) -> Path:
        """Extract natural ambient sounds (removing music and narration vocals) to output_path."""
        inp = Path(input_path)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        work_dir = Path(temp_dir) if temp_dir else out.parent / "_demucs_tmp"
        stems = self.separate_stems(inp, work_dir)

        ambient_stem = stems.get("ambient")
        if not ambient_stem or not ambient_stem.exists():
            raise RuntimeError(f"Demucs did not produce an ambient ('other.wav') stem for {inp.name}")

        shutil.copy2(str(ambient_stem), str(out))
        return out

    def extract_vocal_audio(
        self,
        input_path: Path | str,
        output_path: Path | str,
        temp_dir: Optional[Path | str] = None,
    ) -> Path:
        """Extract vocal narration (removing background music and ambient sounds) to output_path."""
        inp = Path(input_path)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        work_dir = Path(temp_dir) if temp_dir else out.parent / "_demucs_tmp"
        stems = self.separate_stems(inp, work_dir, two_stems="vocals")

        vocal_stem = stems.get("vocals")
        if not vocal_stem or not vocal_stem.exists():
            raise RuntimeError(f"Demucs did not produce a vocal stem for {inp.name}")

        shutil.copy2(str(vocal_stem), str(out))
        return out
