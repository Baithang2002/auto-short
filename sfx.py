"""
sfx.py — Royalty-Free Sound Effects Generator
==============================================

Generates short sound effects via FFmpeg audio synthesis filters.
Each effect is generated once and cached to disk in `output/sfx/`.

Usage:
    from sfx import get_sfx, whoosh, cash

    path = get_sfx("whoosh")           # uses default output/sfx/
    path = whoosh(sfx_dir="my_sfx/")   # custom directory
"""

from pathlib import Path
import subprocess
from typing import Callable

DEFAULT_SFX_DIR = Path("output/sfx")


def _ensure_dir(sfx_dir: Path) -> Path:
    """Create the sfx directory if it doesn't exist and return it."""
    sfx_dir = Path(sfx_dir)
    sfx_dir.mkdir(parents=True, exist_ok=True)
    return sfx_dir


def _run_ffmpeg(args: list[str]) -> None:
    """Run an ffmpeg command, raising on failure."""
    cmd = ["ffmpeg", "-y"] + args
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Individual SFX generators
# ---------------------------------------------------------------------------

def whoosh(sfx_dir: str | Path | None = None) -> Path:
    """
    Filtered noise sweep (0.4 s) for scene transitions.

    Uses white noise fed through a band-pass filter whose center frequency
    sweeps from 200 Hz → 4000 Hz, with a fast fade-in/out envelope.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "whoosh.wav"
    if out.exists():
        return out

    # Generate white noise, apply a sweeping bandpass via aeval + lowpass,
    # then shape with a fade envelope.
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d=0.4:c=white:r=44100:a=0.6,"
            "highpass=f=200:p=2,"
            "lowpass=f=4000:p=2,"
            "afade=t=in:st=0:d=0.08,"
            "afade=t=out:st=0.25:d=0.15"
        ),
        "-ac", "2",
        str(out),
    ])
    return out


def pop(sfx_dir: str | Path | None = None) -> Path:
    """
    Short sine burst (0.15 s) for text appearance.

    A 900 Hz sine tone with a very fast attack and exponential decay.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "pop.wav"
    if out.exists():
        return out

    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "sine=frequency=900:duration=0.15:sample_rate=44100,"
            "afade=t=in:st=0:d=0.005,"
            "afade=t=out:st=0.02:d=0.13,"
            "volume=0.7"
        ),
        "-ac", "2",
        str(out),
    ])
    return out


def cash(sfx_dir: str | Path | None = None) -> Path:
    """
    Metallic coin ping (0.3 s).

    Layered high-frequency sine tones (3200 Hz + 5400 Hz) with fast decay
    to simulate a metallic "ching" sound.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "cash.wav"
    if out.exists():
        return out

    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "sine=frequency=3200:duration=0.3:sample_rate=44100,"
            "afade=t=out:st=0.02:d=0.28,"
            "volume=0.5"
        ),
        "-f", "lavfi",
        "-i", (
            "sine=frequency=5400:duration=0.3:sample_rate=44100,"
            "afade=t=out:st=0.01:d=0.29,"
            "volume=0.3"
        ),
        "-filter_complex", "[0][1]amix=inputs=2:duration=shortest",
        "-ac", "2",
        str(out),
    ])
    return out


def click(sfx_dir: str | Path | None = None) -> Path:
    """
    Sharp transient click (0.1 s).

    A very short noise burst with near-instant attack and fast decay,
    filtered to emphasize upper-mid frequencies.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "click.wav"
    if out.exists():
        return out

    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d=0.1:c=white:r=44100:a=0.8,"
            "highpass=f=1500,"
            "afade=t=in:st=0:d=0.002,"
            "afade=t=out:st=0.005:d=0.095,"
            "volume=0.6"
        ),
        "-ac", "2",
        str(out),
    ])
    return out


def heartbeat(sfx_dir: str | Path | None = None) -> Path:
    """
    Low double-thump (0.8 s).

    Two short low-frequency sine pulses (55 Hz) separated by a brief gap,
    mimicking a heartbeat's lub-dub pattern.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "heartbeat.wav"
    if out.exists():
        return out

    # First thump: 55 Hz, 0.15 s — louder
    # Second thump: 55 Hz, 0.12 s — slightly softer, starts at 0.25 s
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "sine=frequency=55:duration=0.15:sample_rate=44100,"
            "afade=t=in:st=0:d=0.01,"
            "afade=t=out:st=0.03:d=0.12,"
            "volume=0.9"
        ),
        "-f", "lavfi",
        "-i", (
            "sine=frequency=55:duration=0.12:sample_rate=44100,"
            "afade=t=in:st=0:d=0.01,"
            "afade=t=out:st=0.02:d=0.10,"
            "volume=0.7"
        ),
        "-filter_complex", (
            "[1]adelay=250|250[b];"
            "[0][b]amix=inputs=2:duration=longest,"
            "lowpass=f=120"
        ),
        "-t", "0.8",
        "-ac", "2",
        str(out),
    ])
    return out


def coin_drop(sfx_dir: str | Path | None = None) -> Path:
    """
    Descending metallic tones (0.5 s).

    Three quick descending sine pings at different frequencies to simulate
    a coin bouncing/dropping.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "coin_drop.wav"
    if out.exists():
        return out

    # Three descending pings: 4800 Hz, 3600 Hz, 2400 Hz
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "sine=frequency=4800:duration=0.12:sample_rate=44100,"
            "afade=t=out:st=0.01:d=0.11,volume=0.5"
        ),
        "-f", "lavfi",
        "-i", (
            "sine=frequency=3600:duration=0.12:sample_rate=44100,"
            "afade=t=out:st=0.01:d=0.11,volume=0.5"
        ),
        "-f", "lavfi",
        "-i", (
            "sine=frequency=2400:duration=0.15:sample_rate=44100,"
            "afade=t=out:st=0.01:d=0.14,volume=0.5"
        ),
        "-filter_complex", (
            "[1]adelay=140|140[b];"
            "[2]adelay=300|300[c];"
            "[0][b][c]amix=inputs=3:duration=longest"
        ),
        "-t", "0.5",
        "-ac", "2",
        str(out),
    ])
    return out


def paper_flip(sfx_dir: str | Path | None = None) -> Path:
    """
    Short noise burst (0.2 s) for page/card flip transitions.

    Band-limited white noise with a fast attack and medium decay.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "paper_flip.wav"
    if out.exists():
        return out

    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d=0.2:c=white:r=44100:a=0.5,"
            "highpass=f=800,"
            "lowpass=f=6000,"
            "afade=t=in:st=0:d=0.005,"
            "afade=t=out:st=0.04:d=0.16,"
            "volume=0.5"
        ),
        "-ac", "2",
        str(out),
    ])
    return out


def typing(sfx_dir: str | Path | None = None) -> Path:
    """
    Rapid clicks (0.6 s) simulating keyboard typing.

    Multiple short noise bursts at irregular intervals layered together.
    """
    sfx_dir = _ensure_dir(sfx_dir or DEFAULT_SFX_DIR)
    out = sfx_dir / "typing.wav"
    if out.exists():
        return out

    # Generate a single short click, then repeat it with varying delays
    # using aevalsrc for a rhythmic clicking pattern.
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d=0.03:c=white:r=44100:a=0.7,"
            "highpass=f=2000,"
            "afade=t=out:st=0.005:d=0.025,"
            "volume=0.4"
        ),
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d=0.025:c=white:r=44100:a=0.6,"
            "highpass=f=2500,"
            "afade=t=out:st=0.003:d=0.022,"
            "volume=0.35"
        ),
        "-filter_complex", (
            # Create multiple delayed copies to simulate key presses
            "[0]acopy[k1];"
            "[0]adelay=80|80[k2];"
            "[0]adelay=170|170[k3];"
            "[1]adelay=230|230[k4];"
            "[0]adelay=310|310[k5];"
            "[1]adelay=370|370[k6];"
            "[0]adelay=440|440[k7];"
            "[1]adelay=500|500[k8];"
            "[0]adelay=550|550[k9];"
            "[k1][k2][k3][k4][k5][k6][k7][k8][k9]"
            "amix=inputs=9:duration=longest,"
            "volume=2.0"
        ),
        "-t", "0.6",
        "-ac", "2",
        str(out),
    ])
    return out


# ---------------------------------------------------------------------------
# Convenience dispatcher
# ---------------------------------------------------------------------------

_SFX_MAP: dict[str, Callable[..., Path]] = {
    "whoosh":     whoosh,
    "pop":        pop,
    "cash":       cash,
    "click":      click,
    "heartbeat":  heartbeat,
    "coin_drop":  coin_drop,
    "paper_flip": paper_flip,
    "typing":     typing,
}


def get_sfx(name: str, sfx_dir: str | Path | None = None) -> Path:
    """
    Look up a sound effect by name and return the path to its WAV file.

    Parameters
    ----------
    name : str
        One of: whoosh, pop, cash, click, heartbeat, coin_drop,
        paper_flip, typing.
    sfx_dir : str or Path, optional
        Directory to cache generated files. Defaults to ``output/sfx/``.

    Returns
    -------
    Path
        Absolute or relative path to the generated WAV file.

    Raises
    ------
    ValueError
        If *name* is not a recognised sound effect.
    """
    fn = _SFX_MAP.get(name.lower().strip())
    if fn is None:
        available = ", ".join(sorted(_SFX_MAP.keys()))
        raise ValueError(
            f"Unknown SFX '{name}'. Available effects: {available}"
        )
    kwargs = {}
    if sfx_dir is not None:
        kwargs["sfx_dir"] = sfx_dir
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Quick smoke-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating all sound effects …")
    for name in _SFX_MAP:
        path = get_sfx(name)
        print(f"  ✓ {name:12s} → {path}")
    print("Done.")
