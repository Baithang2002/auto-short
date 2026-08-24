"""YouTube Clip Fallback Provider using yt-dlp.

Searches YouTube for target queries, downloads a short segment,
crops it to 9:16 vertical video, and strips audio for copyright safety.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from autovideo.config import defaults
from autovideo.domain.asset import Asset, AssetStatus, AssetType
from autovideo.providers.base import ProviderExecutionError, ProviderResult, ProviderUnavailableError
from autovideo.providers.stock.base import StockQuery

logger = logging.getLogger(__name__)

_VISION_ACCEPT_MIN_CONFIDENCE = 0.70
_VISION_PROMPT = (
    "Requested Entity:\n"
    "{entity}\n"
    "Required Constraints:\n"
    "{constraints}\n\n"
    "Question:\n"
    "Does this image/frame primarily depict the requested entity while satisfying the required constraints?\n\n"
    "If a required constraint (such as an environment or action) is not visible, match must be false.\n\n"
    "Return compact JSON with: match, matched_entity, confidence, brief_reasoning."
)


def is_yt_dlp_available() -> bool:
    """Check if yt-dlp CLI tool or python package is available."""
    if shutil.which("yt-dlp"):
        return True
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _yt_clip_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    if not any(key in data for key in ("match", "entity_match")):
        return None
    return data


def _yt_clip_vision_verifier_for(entity: str | None, constraints: Sequence[str] = ()):
    """Build a Gemini vision verifier, or None when entity/vision is unavailable.

    ``constraints`` are mandatory scene attributes (e.g. "underwater",
    "waggle dance") that must remain visible for a clip to be accepted.
    """

    if not entity:
        return None
    verify_flag = os.environ.get("AUTO_VIDEO_YT_CLIP_VERIFY", "1").strip().lower()
    if verify_flag in {"0", "false", "no", "off"}:
        return None
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    models = tuple(dict.fromkeys(
        str(model).strip()
        for model in (defaults.DEFAULTS.providers.gemini_image_model, *defaults.DEFAULTS.providers.gemini_models)
        if str(model or "").strip()
    ))

    def verify(entity: str, frame_paths: Sequence[Path]) -> tuple[bool, float] | None:
        constraints_text = ", ".join(
            dict.fromkeys(str(term).strip() for term in constraints if str(term or "").strip())
        ) or "none"
        parts = [types.Part.from_text(text=_VISION_PROMPT.format(
            entity=entity, constraints=constraints_text,
        ))]
        for frame_path in frame_paths:
            parts.append(types.Part.from_bytes(
                data=Path(frame_path).read_bytes(), mime_type="image/jpeg"
            ))
        client = genai.Client(api_key=api_key)
        # Respect the global Gemini rate limit so yt_clip verification
        # calls don't exhaust the free-tier RPM budget.
        import time as _time
        _min_interval = float(os.environ.get("GEMINI_MIN_INTERVAL_SEC", "5"))
        failures = []
        for model in models:
            _time.sleep(_min_interval)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                )
                data = _yt_clip_json_object(str(getattr(response, "text", "") or ""))
                if not data:
                    raise RuntimeError("vision provider returned malformed JSON")
                match = bool(data.get("match") or data.get("entity_match"))
                confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0)))
                return match, confidence
            except Exception as exc:
                failures.append(f"{model}: {exc}")
        logger.warning(f"[YTClip] Vision verification failed: {' | '.join(failures)}")
        return None

    return verify


def _yt_clip_extract_frames(video_path: Path, out_dir: Path, count: int = 3) -> list[Path]:
    """Extract up to ``count`` evenly spaced JPEG frames for vision verification."""

    duration = 0.0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip() or 0.0)
    except Exception:
        duration = 0.0
    if duration <= 0.1:
        duration = 1.0
    times = [duration * 0.25, duration * 0.5, duration * 0.75][:count]
    frames: list[Path] = []
    for sample_idx, timestamp in enumerate(times):
        out = out_dir / f"frame_{sample_idx}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "3", str(out)],
                capture_output=True, timeout=30,
            )
            if out.exists() and out.stat().st_size > 0:
                frames.append(out)
        except Exception:
            continue
    return frames


def fetch_yt_clip(
    queries: Sequence[str],
    idx: int,
    output_dir: Path,
    target_duration: float = 4.0,
    used_set: set[str] | None = None,
    expected_entity: str | None = None,
    constraints: Sequence[str] = (),
    source_url: str | None = None,
    segment_offset_sec: float | None = None,
    preserve_audio: bool = False,
) -> Path | None:
    """Download a short segment from YouTube via yt-dlp, crop to 9:16, and strip audio.

    ``segment_offset_sec`` enables source-continuity reuse: when set, the same
    pinned ``source_url`` video is sliced from a later timestamp instead of the
    default 5-second mark, and the ``used_set`` guard is lifted so one verified
    video can contribute several distinct scenes. ``preserve_audio`` keeps the
    local source audio when the caller has enabled clip-audio mixing.
    """

    if not is_yt_dlp_available():
        logger.warning("[YTClip] yt-dlp is not available.")
        return None

    yt_dlp_cmd = shutil.which("yt-dlp") or "yt-dlp"

    # Clean search query: remove redundant fluff words but preserve mandatory constraints
    raw_query = queries[0] if queries else "wildlife nature footage"
    fluff_phrases = (
        "in the wild", "wild animal", "wild animals", "nature documentary", "wildlife footage",
        "slow motion", "4k video", "hd video", "full hd", "dark ocean", "deep ocean", "close up",
        "wildlife", "nature", "animal", "animals", "creature", "creatures", "beast", "beasts",
        "predator", "wide", "close", "motion", "scene", "wallpaper", "stock", "video", "clip",
        "footage", "hd", "4k", "background", "documentary",
    )
    cleaned_query = raw_query
    for fw in fluff_phrases:
        cleaned_query = re.sub(rf"\b{re.escape(fw)}\b", "", cleaned_query, flags=re.IGNORECASE)
    cleaned_query = " ".join(cleaned_query.split())
    if not cleaned_query:
        cleaned_query = raw_query

    entity = expected_entity or (cleaned_query.split()[0] if cleaned_query else None)
    max_candidates = max(1, int(
        os.environ.get("AUTO_VIDEO_YT_CLIP_MAX_CANDIDATES", "5").strip() or "5"
    ))
    info_timeout = max(10, int(os.environ.get("AUTO_VIDEO_YT_CLIP_INFO_TIMEOUT", "30").strip() or "30"))
    dl_timeout = max(15, int(os.environ.get("AUTO_VIDEO_YT_CLIP_DL_TIMEOUT", "240").strip() or "240"))
    ffmpeg_timeout = max(10, int(os.environ.get("AUTO_VIDEO_YT_CLIP_FFMPEG_TIMEOUT", "60").strip() or "60"))
    search_term = f"ytsearch{max_candidates}:{cleaned_query} footage"
    verifier = _yt_clip_vision_verifier_for(entity, constraints)

    tmp_dir = output_dir / f"_yt_tmp_{idx}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    final_out = output_dir / f"broll_{idx}.mp4"

    # Local-source mode: when ``source_url`` points at an already-downloaded video
    # file, skip yt-dlp entirely and just slice a 9:16 segment with ffmpeg. This is
    # the "download once, cut into scenes" workflow. Continuity reuse via
    # ``segment_offset_sec`` advances the cut position into the local video.
    local_source: Path | None = None
    if source_url:
        local_url = str(source_url).strip()
        if local_url and local_url != "manual_0":
            candidate_path = Path(local_url).expanduser()
            if candidate_path.exists() and candidate_path.is_file():
                local_source = candidate_path
    if local_source is not None:
        try:
            segment_start = 2.0 if segment_offset_sec is None else max(1.0, float(segment_offset_sec))
            slice_cmd = [
                "ffmpeg", "-y",
                "-ss", f"{segment_start:.2f}",
                "-i", str(local_source),
                "-t", str(max(0.5, float(target_duration))),
                "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                "-map", "0:v:0",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
            ]
            if preserve_audio:
                slice_cmd.extend([
                    "-map", "0:a:0?",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-ar", "44100",
                    "-ac", "2",
                ])
            else:
                slice_cmd.append("-an")
            slice_cmd.extend(["-shortest", str(final_out)])
            subprocess.run(slice_cmd, capture_output=True, timeout=ffmpeg_timeout)
            if final_out.exists() and final_out.stat().st_size > 0:
                print(
                    f"    [YTClip] Sliced local segment @ {segment_start:.1f}s "
                    f"(+{target_duration:.1f}s) from {local_source.name} -> {final_out.name}"
                )
                return final_out
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.warning("[YTClip] Local source slicing failed.")
        return None

    try:
        if source_url:
            direct = str(source_url).strip()
            if direct and re.fullmatch(r"[A-Za-z0-9_-]{11}", direct):
                direct = f"https://www.youtube.com/watch?v={direct}"
            vid_match = re.search(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})", direct)
            vid_id = vid_match.group(1) if vid_match else f"manual_{idx}"
            entries = [{"id": vid_id, "webpage_url": direct, "title": cleaned_query}]
        else:
            # Step 1: Search & Get Video Metadata
            info_cmd = [
                yt_dlp_cmd,
                search_term,
                "--dump-single-json",
                "--default-search", "ytsearch",
                "--no-playlist",
                "--match-filter", "duration <= 600 & duration >= 3",
                "--quiet",
            ]
            try:
                res = subprocess.run(info_cmd, capture_output=True, text=True, timeout=info_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"[YTClip] Search info request timed out for {search_term!r}")
                return None

            if res.returncode != 0 or not res.stdout.strip():
                logger.info(f"[YTClip] No search results for {cleaned_query!r}")
                return None

            data = json.loads(res.stdout)
            entries = data.get("entries") or [data] if "entries" in data or "id" in data else []
            if not entries:
                return None

        # Step 2: Try candidates in rank order; accept the first one that vision-verifies
        for rank, entry in enumerate(entries):
            vid_id = entry.get("id")
            if not vid_id:
                continue
            if used_set is not None and vid_id in used_set and segment_offset_sec is None:
                continue

            vid_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}"
            raw_out = tmp_dir / f"raw_{rank}.mp4"

            section_start = 5.0 if segment_offset_sec is None else max(1.0, float(segment_offset_sec))
            section_end = section_start + target_duration + 2.0
            dl_cmd = [
                yt_dlp_cmd,
                vid_url,
                "-f", ("bestvideo[height<=720][vcodec^=avc1][ext=mp4]/"
                       "bestvideo[height<=720][ext=mp4]/"
                       "best[height<=720][vcodec^=avc1][ext=mp4]/"
                       "best[height<=720]"),
                "-o", str(raw_out),
                "--download-sections",
                (f"*00:00:{int(section_start):02d}-00:00:{int(section_end):02d}"
                 if segment_offset_sec is None
                 else f"*{section_start:.1f}-{section_end:.1f}"),
                "--concurrent-fragments", "4",
                "--no-playlist",
                "--quiet",
                "--force-overwrites",
            ]
            try:
                subprocess.run(dl_cmd, capture_output=True, timeout=dl_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"[YTClip] Section download timed out for {vid_id}")

            if not raw_out.exists() or raw_out.stat().st_size == 0:
                # Quick fallback without section slicing if slice fails (using duration/resolution, NO fixed file-size limit)
                dl_cmd_fallback = [
                    yt_dlp_cmd,
                    vid_url,
                    "-f", ("bestvideo[height<=480][vcodec^=avc1][ext=mp4]/"
                           "best[height<=480][vcodec^=avc1][ext=mp4]/"
                           "best[height<=480]"),
                    "-o", str(raw_out),
                    "--no-playlist",
                    "--quiet",
                    "--force-overwrites",
                ]
                try:
                    subprocess.run(dl_cmd_fallback, capture_output=True, timeout=dl_timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(f"[YTClip] Fallback download timed out for {vid_id}")

            if not raw_out.exists() or raw_out.stat().st_size == 0:
                continue

            # Step 3: Process video using FFmpeg (crop to 9:16, strip audio -an, clamp duration)
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", "2.0",
                "-i", str(raw_out),
                "-t", str(target_duration),
                "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                "-an",  # Strip original audio completely for Content ID safety
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(final_out),
            ]
            subprocess.run(ffmpeg_cmd, capture_output=True, timeout=ffmpeg_timeout)

            if not final_out.exists() or final_out.stat().st_size == 0:
                continue

            if verifier is not None and entity:
                frames = _yt_clip_extract_frames(final_out, tmp_dir)
                if frames:
                    result = verifier(entity, frames)
                    if result is None:
                        logger.warning(
                            f"[YTClip] Vision unavailable for {vid_id}; skipping unverified candidate."
                        )
                        final_out.unlink(missing_ok=True)
                        continue
                    match, confidence = result
                    verdict = "match" if match and confidence >= _VISION_ACCEPT_MIN_CONFIDENCE else "reject"
                    print(
                        f"    [YTClip] Candidate {vid_id} vision {verdict} "
                        f"entity={entity!r} confidence={confidence:.2f}"
                    )
                    if not match or confidence < _VISION_ACCEPT_MIN_CONFIDENCE:
                        final_out.unlink(missing_ok=True)
                        continue
                else:
                    logger.warning(f"[YTClip] No frames extracted for {vid_id}; skipping.")
                    final_out.unlink(missing_ok=True)
                    continue

            if used_set is not None:
                used_set.add(vid_id)
            print(f"    [YTClip] Successfully fetched clip from YouTube: {vid_id} ('{cleaned_query}')")
            return final_out

        logger.warning(
            f"[YTClip] No verified candidate accepted for {cleaned_query!r} "
            f"(checked {len(entries)} result(s))"
        )
        return None

    except Exception as e:
        logger.warning(f"[YTClip] Error downloading YouTube clip for {cleaned_query!r}: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return None


class YouTubeClipProvider:
    """StockProvider adapter for YouTubeClipProvider."""

    name = "yt_clip"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def fetch(self, query: StockQuery, output_dir: Path) -> ProviderResult[Asset]:
        if not self.enabled:
            raise ProviderUnavailableError(self.name, "YouTube clip provider disabled")
        out_path = fetch_yt_clip(
            query.queries,
            idx=0,
            output_dir=output_dir,
            target_duration=query.target_duration_sec,
        )
        if not out_path:
            raise ProviderExecutionError(self.name, "Failed to download YouTube clip")

        asset = Asset(
            id=out_path.stem,
            asset_type=AssetType.VIDEO,
            provider=self.name,
            local_path=out_path,
            status=AssetStatus.AVAILABLE,
        )
        return ProviderResult(provider=self.name, value=asset)
