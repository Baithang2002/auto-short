#!/usr/bin/env python3
"""
auto_short.py  —  Faceless short-video content engine (Step 1 of the pipeline)

Pipeline:  niche  ->  Gemini script  ->  Edge-TTS voiceover  ->  Pexels B-roll
           ->  burned captions  ->  ffmpeg assembly  ->  one finished 1080x1920 MP4

This is the "content half" — fully free and runs locally. Posting to
YouTube/Instagram/Facebook is Step 2 (a separate uploader).

------------------------------------------------------------------------------
SETUP (one time)
------------------------------------------------------------------------------
1. Install ffmpeg (must be on PATH):
     - Windows:  winget install Gyan.FFmpeg     (or download from ffmpeg.org)
     - Mac:      brew install ffmpeg
     - Linux:    sudo apt install ffmpeg

2. Install Python deps:
     pip install edge-tts google-genai requests

3. Get two free API keys:
     - Gemini:  https://aistudio.google.com/apikey   (you already have one)
     - Pexels:  https://www.pexels.com/api/   (free, instant)

4. Set them as environment variables:
     export GEMINI_API_KEY="your_key"        # Windows: setx GEMINI_API_KEY "your_key"
     export PEXELS_API_KEY="your_key"

------------------------------------------------------------------------------
RUN
------------------------------------------------------------------------------
     python auto_short.py "weird facts about the deep ocean"

If you pass no topic, it uses DEFAULT_NICHE below.
Output lands in ./output/final.mp4
"""

import os
import sys
import json
import asyncio
import subprocess
import shutil
import textwrap
import argparse
import random
import re
import time
import uuid
import datetime as dt
from pathlib import Path
from difflib import SequenceMatcher
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------
# CONFIG  — tweak these freely
# ----------------------------------------------------------------------------
DEFAULT_NICHE        = "mind-blowing facts about space"
VOICE                = os.environ.get("EDGE_TTS_VOICE", "en-US-AndrewNeural")   # try en-US-AriaNeural, en-GB-RyanNeural, etc.
GEMINI_MODEL         = "gemini-2.5-flash"
TARGET_DURATION      = 60                     # target video length in seconds (use --duration to override)
AVG_SEGMENT_DURATION = 6.5                    # estimated seconds per narration/story beat
SHORTS_MIN_DURATION  = 50                     # preferred default lower bound for YouTube Shorts
SHORTS_MAX_DURATION  = 58                     # hard ceiling: ≥60s triggers YT Content ID (Adrev). 2s safety margin to absorb encoding drift.
WIDTH, HEIGHT        = 1080, 1920             # vertical (Reels / Shorts / FB)
FPS                  = 30
SCRIPT_DIR           = Path(__file__).parent
OUT_DIR              = SCRIPT_DIR / "output"
PENDING_DIR          = SCRIPT_DIR / "videos" / "pending"   # review queue input
PERSISTENT_USED_PATH = SCRIPT_DIR / "used_videos.json"     # cross-run clip dedup
INPUT_DIR            = SCRIPT_DIR / "input_clips"   # drop your own .mp4/.mov clips here
MUSIC_DIR            = SCRIPT_DIR / "music"          # drop royalty-free .mp3/.wav/.m4a tracks here
VIDEO_EXTENSIONS     = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS     = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTENSIONS     = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MEDIA_EXTENSIONS     = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
DEFAULT_MUSIC_VOLUME = 0.38                    # background bed under narration
_GEMINI_CLIENT = None


def _get_gemini_client():
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None and GEMINI_API_KEY:
        from google import genai
        _GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    return _GEMINI_CLIENT


CAPTION_HIGHLIGHT_WORDS = {
    "amazing", "incredible", "secret", "secrets", "hidden", "deadly", "powerful",
    "unbelievable", "beautiful", "ancient", "vast", "deep", "extreme",
    "giant", "massive", "tiny", "rare", "unique", "strange", "weird",
    "impossible", "mysterious", "dark", "bright", "perfect", "terrifying",
    "breathtaking", "unstoppable", "epic", "greatest", "biggest", "oldest",
    "largest", "smallest", "fastest", "deepest", "tallest", "longest",
    "never", "forever", "always", "inside", "beyond", "beneath", "above",
    "earth", "world", "universe", "ocean", "mountain", "volcano", "storm",
    "hurricane", "earthquake", "ice", "fire", "water", "life", "death",
    "survive", "discover", "explore", "reveal", "transform", "mind",
    "blowing", "shocking", "unexpected", "frozen", "burning", "glowing",
    "dazzling", "prehistoric", "alien", "highest",
    "wonder", "majestic", "untouched", "wild", "fierce", "gentle",
    "surprising", "dangerous", "brilliant", "today",
    "history", "future", "invented", "changed", "built",
}
CAPTION_STYLE        = (
    "Fontname=Arial,Fontsize=64,Bold=1,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,Outline=4,Shadow=2,Alignment=2,MarginV=280"
)

# Load local .env file if present
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")
SPEECHIFY_API_KEY = os.environ.get("SPEECHIFY_API_KEY")
SPEECHIFY_VOICE_ID = os.environ.get("SPEECHIFY_VOICE_ID", "george")
JAMENDO_CLIENT_ID  = os.environ.get("JAMENDO_CLIENT_ID")
PIXABAY_API_KEY    = os.environ.get("PIXABAY_API_KEY")
SAMBANOVA_API_KEY  = os.environ.get("SAMBANOVA_API_KEY")
# Channel name used in end card and SEO metadata
CHANNEL_NAME       = "Wonders of the Nature"  # overridden by auto_short_biasfiles.py
# NASA Image and Video Library has no auth requirement for read access.

# Keywords that route to NASA (in addition to Pexels/Pixabay) - if any of these
# appear in the b-roll query, NASA's library is searched as a third source.
NASA_KEYWORDS = {
    "space", "galaxy", "galaxies", "star", "stars", "starfield", "nebula",
    "cosmos", "universe", "moon", "lunar", "mars", "jupiter", "saturn",
    "neptune", "pluto", "venus", "mercury", "earth", "orbit", "satellite",
    "astronaut", "telescope", "aurora", "supernova", "asteroid", "comet",
    "meteor", "solar", "sun", "iss", "spacex", "nasa", "milky way",
    "exoplanet", "blackhole", "black hole", "eclipse", "rocket", "spacecraft",
}


def needs_nasa(query):
    """Return True if a query mentions space/astronomy terms NASA covers well."""
    q = (query or "").lower()
    return any(kw in q for kw in NASA_KEYWORDS)



# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def die(msg):
    print(f"\n[X] {msg}\n")
    sys.exit(1)


def run_ff(args, cwd=None):
    """Run an ffmpeg/ffprobe command, raising with readable output on failure."""
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{p.stderr[-800:]}")
    return p.stdout


def media_duration(path):
    out = run_ff([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    return float(out.strip())


def count_words(text):
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or ""))


def narration_targets(target_duration, n_segments):
    """Return word-count targets that usually land TTS output near target_duration.

    TTS reads at roughly 2.5-3 words/sec on neural voices. For a 60s target
    that's ~150-180 words total. Multipliers below 2.0 produce 25-35s videos
    (proved: 1.6 gave a 25.7s video). The hard cap trim handles overshoots.
    """
    min_total = max(n_segments * 12, round(target_duration * 2.0))
    max_total = max(min_total + 12, round(target_duration * 2.4))
    min_segment = max(10, min(16, min_total // max(n_segments, 1)))
    max_segment = max(min_segment + 4, round(max_total / max(n_segments, 1)) + 2)
    return min_total, max_total, min_segment, max_segment


def script_quality_notes(data, n_segments, target_duration):
    """Return (fatal_notes, soft_notes).

    Fatal = will produce a broken or way-too-short video. Pipeline must reject.
    Soft  = overshoots and minor issues - video will be slightly longer than
            target but still postable. Accept after one repair attempt fails.

    Splitting these matters because the Groq fallback (used when Gemini 503s)
    consistently writes longer narrations than asked. Hard-failing on overshoot
    means a Gemini outage = no daily video.
    """
    min_total, max_total, min_segment, max_segment = narration_targets(target_duration, n_segments)
    segments = data.get("segments") or []
    fatal_notes = []
    soft_notes  = []

    if len(segments) != n_segments:
        fatal_notes.append(f"expected exactly {n_segments} segments, got {len(segments)}")

    # Tolerance: a segment that's 1-3 words under min is borderline, not broken.
    # Below crit_min the segment is genuinely too short (TTS clip < 3-4s).
    crit_min_segment = max(8, min_segment - 3)
    crit_min_total   = max(n_segments * 8, round(min_total * 0.85))

    total_words = 0
    for idx, seg in enumerate(segments[:n_segments], start=1):
        narration = str(seg.get("narration", "")).strip()
        broll = str(seg.get("broll", "")).strip()
        words = count_words(narration)
        total_words += words
        if words < crit_min_segment:
            fatal_notes.append(f"segment {idx} narration is critically short ({words} words, hard minimum {crit_min_segment})")
        elif words < min_segment:
            soft_notes.append(f"segment {idx} narration is short ({words} words, target minimum {min_segment})")
        if words > max_segment:
            soft_notes.append(f"segment {idx} narration is too long ({words} words, maximum {max_segment})")
        if not broll:
            fatal_notes.append(f"segment {idx} is missing broll")

    if total_words < crit_min_total:
        fatal_notes.append(f"total narration is critically short ({total_words} words, hard minimum {crit_min_total})")
    elif total_words < min_total:
        soft_notes.append(f"total narration is short ({total_words} words, target minimum {min_total})")
    if total_words > max_total:
        soft_notes.append(f"total narration is too long ({total_words} words, maximum {max_total})")

    return fatal_notes, soft_notes


def slugify(s, maxlen=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return (s or "video")[:maxlen]


def check_deps():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            # Fallback for Windows Gyan.FFmpeg winget installations
            winget_dir = Path(os.environ.get("USERPROFILE", "")) / "AppData/Local/Microsoft/WinGet/Packages"
            if winget_dir.exists():
                found = list(winget_dir.glob("**/ffmpeg.exe" if tool == "ffmpeg" else "**/ffprobe.exe"))
                if found:
                    bin_dir = found[0].parent
                    os.environ["PATH"] += os.pathsep + str(bin_dir)
                    if shutil.which(tool):
                        continue
            die(f"'{tool}' not found on PATH. Install ffmpeg first (see header).")
    if not GEMINI_API_KEY or not GEMINI_API_KEY.strip():
        die("GEMINI_API_KEY is not configured. Please add your real key to the .env file.")
    if not PEXELS_API_KEY or not PEXELS_API_KEY.strip():
        die("PEXELS_API_KEY is not configured. Please add your real key to the .env file.")



# ----------------------------------------------------------------------------
# Step 1: script + B-roll keywords from Gemini  (returns list of segments)
# ----------------------------------------------------------------------------
# Provider chain: every entry is tried in order; first success wins.
# Multiple Gemini models so a single throttled model doesn't kill the run.
# Multiple Groq models so a single deprecated model doesn't kill the run.
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",   # smaller/cheaper, often available when 2.5-flash is 503
    "gemini-2.0-flash",
    # gemini-1.5-flash deprecated as of late 2025; removed.
]
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
]
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4o"]
# SambaNova Cloud models (https://cloud.sambanova.ai). OpenAI-compatible API.
# Ordered by JSON-following reliability + capability. First success wins.
SAMBANOVA_MODELS = [
    # Largest first - more obedient to length instructions, less likely to write
    # 9-word fragments when asked for 15-22 word narrations.
    "Meta-Llama-3.1-405B-Instruct",
    "DeepSeek-R1-Distill-Llama-70B",
    "Meta-Llama-3.3-70B-Instruct",
    "Meta-Llama-3.1-70B-Instruct",
    "Qwen2.5-72B-Instruct",
]


def _try_gemini(prompt):
    if not GEMINI_API_KEY:
        return None, None
    client = _get_gemini_client()
    if client is None:
        return None, Exception("Failed to initialize Gemini client")
    last_err = None
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            print(f"    [Gemini] {model} - OK")
            return resp.text.strip(), None
        except Exception as e:
            last_err = e
            print(f"    [Gemini] {model} failed: {str(e)[:120]}")
    return None, last_err


def _try_sambanova(prompt):
    """SambaNova Cloud - OpenAI-compatible API, very fast inference, free tier
    with rate limits. Sits between Gemini and Groq in the chain so a Gemini
    outage routes to SambaNova's multi-provider library first."""
    if not SAMBANOVA_API_KEY or "your_sambanova" in SAMBANOVA_API_KEY:
        return None, None
    try:
        from openai import OpenAI
    except Exception as e:
        return None, e
    client = OpenAI(api_key=SAMBANOVA_API_KEY, base_url="https://api.sambanova.ai/v1")
    last_err = None
    for model in SAMBANOVA_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            print(f"    [SambaNova] {model} - OK")
            return resp.choices[0].message.content.strip(), None
        except Exception as e:
            last_err = e
            print(f"    [SambaNova] {model} failed: {str(e)[:120]}")
    return None, last_err


def _try_groq(prompt):
    if not GROQ_API_KEY or "your_groq" in GROQ_API_KEY:
        return None, None
    import requests
    last_err = None
    for model in GROQ_MODELS:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            print(f"    [Groq] {model} - OK")
            return text, None
        except Exception as e:
            last_err = e
            msg = str(e)[:120]
            print(f"    [Groq] {model} failed: {msg}")
    return None, last_err


def _try_openai(prompt):
    if not OPENAI_API_KEY or "your_openai" in OPENAI_API_KEY:
        return None, None
    try:
        from openai import OpenAI
    except Exception as e:
        return None, e
    client = OpenAI(api_key=OPENAI_API_KEY)
    last_err = None
    for model in OPENAI_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            print(f"    [OpenAI] {model} - OK")
            return resp.choices[0].message.content.strip(), None
        except Exception as e:
            last_err = e
            print(f"    [OpenAI] {model} failed: {str(e)[:120]}")
    return None, last_err


def generate_script_raw(prompt):
    """Try Gemini (multiple models) -> Groq (multiple models) -> OpenAI (multiple models).
    The first non-None response wins."""
    errors = {}

    raw, err = _try_gemini(prompt)
    if raw: return raw
    errors["gemini"] = err

    raw, err = _try_sambanova(prompt)
    if raw: return raw
    errors["sambanova"] = err

    raw, err = _try_groq(prompt)
    if raw: return raw
    errors["groq"] = err

    raw, err = _try_openai(prompt)
    if raw: return raw
    errors["openai"] = err

    summary = "; ".join(f"{k}={str(v)[:80]}" for k, v in errors.items() if v)
    raise RuntimeError(
        "All script providers failed across all model variants. "
        f"Errors: {summary}. "
        "Wait a few minutes for Gemini, or use --reuse-script with last_script.json."
    )


def parse_script_json(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()
    return json.loads(raw)


def generate_script(niche, n_segments, target_duration):
    min_total, max_total, min_segment, max_segment = narration_targets(target_duration, n_segments)
    prompt = f"""
You are scripting a fast-paced vertical short video about: "{niche}".
Target finished length: about {target_duration} seconds.

Return STRICT JSON only (no markdown, no backticks, no preamble) in this shape:
{{
  "title": "punchy 5-8 word title (no #shorts here, it is appended automatically)",
  "description": "2-3 sentence YouTube/Facebook description, hook + value, no hashtags inside",
  "instagram_caption": "1-2 sentence Instagram caption with a soft CTA, no hashtags inside",
  "music_mood": "one of: mysterious, inspiring, dramatic, warm, curious, urgent",
  "hashtags": ["#tag1", "#tag2", "..."],
  "segments": [
    {{"narration": "one or two spoken sentences, {min_segment}-{max_segment} words, concrete and story-driven",
      "broll": "2-3 word stock-footage search term, very visual and literal",
      "broll_queries": ["specific visual search phrase", "backup visual search phrase", "wide establishing search phrase"]}}
  ]
}}

Rules:
- Exactly {n_segments} segments.
- LENGTH IS NON-NEGOTIABLE. Total narration MUST be {min_total}-{max_total} words across all segments. Below {min_total} the video is too short and gets rejected.
- EACH segment narration MUST be at least {min_segment} words and at most {max_segment} words. Count the words before submitting. If any segment is under {min_segment} words, expand it with a concrete detail until it fits.
- Do NOT write short fragments like "Auroras dazzle." That is only 2 words. Write full sentences with subject, verb, and a specific detail.
- A {min_segment}-word target means roughly one full spoken sentence of 12-18 syllables. Aim for the middle of the range, not the floor.
- Make it a connected mini-story with a hook, setup, escalating facts, a twist, and a satisfying closing line.
- First segment must be a scroll-stopping hook with a specific tension, mystery, or shocking fact. Start directly in the middle of the action. NEVER open with greetings, rhetorical questions, or throat-clearing clichés like "Have you ever wondered...", "Did you know...", "Meet the...", or "Imagine a...". Open immediately with a bizarre, counterintuitive, or striking statement.
- The LAST segment must end with a soft CTA that includes the EXACT channel name "Wonders of the Nature" (these literal words, not a paraphrase). Pick one of these patterns:
  "Subscribe to Wonders of the Nature for more."
  "Follow Wonders of the Nature for more like this."
  "Stay curious - Wonders of the Nature posts daily."
  Do NOT invent a different channel name. Do NOT say "Celestial Wonders" or any other variant. Do NOT use "smash the bell", "hit subscribe", or other creator-speak.
- Do not write isolated fact fragments. Every segment must feel like the next beat in the same story.
- Choose "music_mood" to match the emotional feel of the story.
- Narration is plain spoken English, no emojis, no hashtags, no stage directions.
- "broll" must be something Pexels stock video would actually have
  (e.g. "ocean waves", "city night", "galaxy stars") - concrete nouns, not abstractions.
- STRICT ALIGNMENT: The "broll" search term and "broll_queries" list for a segment MUST directly match the subject, animal, object, or action described in that segment's "narration". If you talk about a lioness, the broll and queries must be specifically about a lioness. Never suggest a different animal or unrelated scenery.
- Each "broll_queries" list must have exactly 3 concrete, simple Pexels-friendly searches:
  1 specific close-up/action matching the narration (e.g. "lioness stalking"), 1 environment/establishing shot (e.g. "savannah grass"), and 1 safe 1-2 word generic fallback of the EXACT subject of the narration (e.g. "lion", not "nature documentary" or "nature").
  Avoid abstract words like innovation, wisdom, mystery, or existence as visual searches.
- 10-15 lowercase hashtags, each prefixed with #, no spaces inside a tag.
  Do NOT include "#shorts" in the list - it is appended automatically.
"""

    raw = generate_script_raw(prompt)
    first_draft = parse_script_json(raw)
    fatal, soft = script_quality_notes(first_draft, n_segments, target_duration)

    if not fatal:
        # First draft is acceptable (possibly with soft warnings). DO NOT repair -
        # asking Gemini to rewrite often returns worse output (empty segments,
        # truncated JSON). Soft notes mean "slightly off target but watchable."
        data = first_draft
        if soft:
            print(f"    [Script QA] First draft acceptable with soft notes: {'; '.join(soft[:3])}")
    else:
        # Fatal issues - the script is genuinely broken. Try one repair attempt.
        print(f"    [Script QA] First draft has FATAL issues; asking for a rewrite: {'; '.join(fatal[:2])}")
        repair_prompt = f"""{prompt}

The previous JSON failed these critical checks:
{json.dumps(fatal, indent=2)}

Rewrite the whole JSON from scratch. Keep the same topic, but satisfy every word-count and story rule.
Previous JSON:
{json.dumps(first_draft, ensure_ascii=False)}
"""
        try:
            repaired = parse_script_json(generate_script_raw(repair_prompt))
            fatal2, soft2 = script_quality_notes(repaired, n_segments, target_duration)
        except Exception as e:
            print(f"    [Script QA] Repair attempt errored ({e}); checking if first draft is salvageable...")
            fatal2 = ["repair attempt errored"]
            soft2 = []
            repaired = None

        # Pick whichever draft has fewer fatal issues - sometimes the repair
        # is WORSE than the first draft (empty segments, malformed JSON).
        first_fatal_count = len(fatal)
        repair_fatal_count = len(fatal2) if repaired else 999
        if repair_fatal_count >= first_fatal_count and repaired is not None:
            # Repair is no better. Fall back to first draft if its fatal issues
            # are tolerable (no empty segments, just slight undershoots).
            print(f"    [Script QA] Repair was no better; using first draft.")
            data = first_draft
            fatal, soft = script_quality_notes(data, n_segments, target_duration)
            if fatal:
                # The first draft IS the better option but still has hard issues.
                # Soften the criticism: only re-raise if every fatal issue is
                # "0 words" or "missing broll" (truly unsalvageable).
                unsalvageable = [f for f in fatal if "0 words" in f or "missing broll" in f]
                if unsalvageable:
                    raise RuntimeError("Script is unsalvageable: " + "; ".join(unsalvageable[:4]))
                print(f"    [Script QA] Tolerating soft fatal: {'; '.join(fatal[:2])}")
        else:
            data = repaired
            if fatal2:
                raise RuntimeError("Generated script is still too short or malformed: " + "; ".join(fatal2[:6]))
            if soft2:
                print(f"    [Script QA] Accepting repaired draft with soft notes: {'; '.join(soft2[:3])}")

    segs = data["segments"][:n_segments]
    data["segments"] = segs

    # Normalize SEO metadata. Older runs (or weak fallbacks) may omit these.
    data.setdefault("description", data.get("title", niche))
    data.setdefault("instagram_caption", data.get("title", niche))
    if data.get("music_mood") not in {"mysterious", "inspiring", "dramatic", "warm", "curious", "urgent"}:
        data["music_mood"] = "inspiring"
    for seg in data.get("segments", []):
        queries = seg.get("broll_queries") or []
        if isinstance(queries, str):
            queries = [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()]
        broll = str(seg.get("broll", "")).strip()
        if broll and broll not in queries:
            queries.insert(0, broll)
        while len(queries) < 3:
            queries.append(broll or niche)
        seg["broll_queries"] = queries[:3]
    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[\s,]+", tags) if t.startswith("#")]
    # Evergreen high-volume hashtags for nature/educational Shorts. These are
    # consistently high-search-volume (millions of views weekly), so every video
    # gets a baseline of reach even if Gemini's topic tags are obscure. We add
    # them BETWEEN #shorts and Gemini's tags so the topic-specific ones still
    # appear in the top-15 cap.
    EVERGREEN_TAGS = [
        "#shorts",
        "#nature",
        "#facts",
        "#didyouknow",
        "#viralshorts",
        "#science",
        "#amazingfacts",
        "#mindblown",
        "#earth",
        "#wildlife",
        "#animals",
        "#space",
        "#ocean",
        "#history",
        "#education",
        "#learnontiktok",
        "#dailyfacts",
        "#interesting",
        "#knowledge",
        "#trivia",
    ]
    # Topic-specific extras (Gemini's) come AFTER evergreen so unique angle survives
    tags = EVERGREEN_TAGS + [t for t in tags if t.lower() not in {x.lower() for x in EVERGREEN_TAGS}]
    seen, deduped = set(), []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl); deduped.append(tl)
    data["hashtags"] = deduped[:15]
    data["niche"] = niche

    # Save script to cache for potential reuse (e.g. rate limit bypass)
    try:
        cache_path = OUT_DIR / "last_script.json"
        cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    print(f"[+] Title: {data.get('title','(untitled)')}")
    for i, s in enumerate(segs):
        print(f"    {i+1}. {s['narration']}   [b-roll: {s['broll']}]")
    return data


# ----------------------------------------------------------------------------
# Step 2: voiceover per segment (Speechify with Edge-TTS fallback)
# ----------------------------------------------------------------------------
async def _tts(text, out_path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(out_path))


async def _tts_with_retry_async(text, out_path, tries=3):
    import edge_tts
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            await _tts(text, out_path)
            return
        except edge_tts.exceptions.NoAudioReceived as e:
            last_err = e
            wait = 2 ** (attempt - 1)
            print(f"    [Edge-TTS] attempt {attempt}/{tries} got NoAudioReceived; retrying in {wait}s...")
            await asyncio.sleep(wait)
        except Exception as e:
            last_err = e
            print(f"    [Edge-TTS] attempt {attempt}/{tries} failed: {e}")
            await asyncio.sleep(2 ** (attempt - 1))
    raise RuntimeError(
        f"Edge-TTS failed after {tries} attempts. Last error: {last_err}. "
        "Try: pip install --upgrade edge-tts"
    ) from last_err


def make_voice_speechify(text, voice_id, out_path):
    import base64
    import requests
    url = "https://api.speechify.ai/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {SPEECHIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "input": text,
        "voice_id": voice_id,
        "audio_format": "mp3",
        "model": "simba-english",
    }
    r = requests.post(url, json=data, headers=headers, timeout=60)
    r.raise_for_status()
    body = r.json()
    audio_b64 = body.get("audio_data")
    if not audio_b64:
        raise RuntimeError(f"Speechify response missing audio_data: {r.text[:200]}")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))


# Per-run circuit breaker: if Speechify returns 401/402 once, skip it for the
# rest of this run. Saves 7+ wasted API calls when the key is dead/maxed out.
_SPEECHIFY_DEAD = False


def _edge_tts_with_retry(text, out_path, tries=3):
    asyncio.run(_tts_with_retry_async(text, out_path, tries=tries))


def make_voice(text, idx):
    global _SPEECHIFY_DEAD
    out_path = OUT_DIR / f"voice_{idx}.mp3"

    use_speechify = (
        not _SPEECHIFY_DEAD
        and SPEECHIFY_API_KEY
        and SPEECHIFY_API_KEY.strip()
    )

    if not _SPEECHIFY_DEAD:
        print(f"    [Edge-TTS] Generating voiceover for segment {idx+1}...")
    try:
        _edge_tts_with_retry(text, out_path)
    except Exception as e:
        if use_speechify:
            print(f"    [Edge-TTS] failed ({e}). Falling back to Speechify...")
            try:
                make_voice_speechify(text, SPEECHIFY_VOICE_ID, out_path)
            except Exception as e2:
                err_str = str(e2)
                if "401" in err_str or "402" in err_str or "Unauthorized" in err_str:
                    _SPEECHIFY_DEAD = True
                raise RuntimeError(f"Both Edge-TTS and Speechify failed: {e2}")
        else:
            raise

    return out_path, media_duration(out_path)


def atempo_chain(tempo):
    """Build a valid ffmpeg atempo chain. Values near 1.0 preserve voice quality."""
    parts = []
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    parts.append(f"atempo={tempo:.5f}")
    return ",".join(parts)


def retime_voice(voice_path, idx, tempo):
    out_path = OUT_DIR / f"voice_{idx}_retimed.mp3"
    run_ff([
        "ffmpeg", "-y",
        "-i", str(voice_path),
        "-filter:a", atempo_chain(tempo),
        "-vn",
        str(out_path),
    ])
    return out_path, media_duration(out_path)


def normalize_voice_timing(voice_items, target_duration):
    """Keep default Shorts runs inside 50-60s without making the narration sound odd."""
    if target_duration < SHORTS_MIN_DURATION:
        return voice_items

    total = sum(item["duration"] for item in voice_items)
    desired = min(max(target_duration, SHORTS_MIN_DURATION), SHORTS_MAX_DURATION)

    if SHORTS_MIN_DURATION <= total <= SHORTS_MAX_DURATION:
        return voice_items

    if total < SHORTS_MIN_DURATION:
        raw_tempo = total / desired
        # Avoid making speech unnaturally slow. The richer script prompt should
        # do most of the work; this only corrects normal TTS variance.
        tempo = max(0.82, raw_tempo)
        action = "slowing"
    else:
        raw_tempo = total / desired
        # Anything above 1.30x sounds chipmunky on phone speakers and tanks
        # retention. Better to ship a 62s video and let the hard-cap trim
        # take the last 2 seconds than to rush the whole narration.
        tempo = min(1.30, raw_tempo)
        action = "speeding up"

    print(f"[i] Voiceover is {total:.1f}s; {action} narration slightly for a ~{desired:.0f}s Short.")
    adjusted = []
    for item in voice_items:
        path, duration = retime_voice(item["voice"], item["idx"], tempo)
        adjusted.append({**item, "voice": path, "duration": duration})

    adjusted_total = sum(item["duration"] for item in adjusted)
    if not (SHORTS_MIN_DURATION <= adjusted_total <= SHORTS_MAX_DURATION):
        print(f"    [Warning] Voice timing is still {adjusted_total:.1f}s after safe retiming.")
    return adjusted


def make_all_voices(segments, target_duration):
    voice_items = []
    print("[2/5] Generating voiceovers...")
    for idx, seg in enumerate(segments):
        voice_path, dur = make_voice(seg["narration"], idx)
        voice_items.append({
            "idx": idx,
            "segment": seg,
            "voice": voice_path,
            "duration": dur
        })
    return normalize_voice_timing(voice_items, target_duration)



# ----------------------------------------------------------------------------
# Step 3: fetch B-roll from Pexels, local clips, or images
# ----------------------------------------------------------------------------
def is_valid_video(filepath):
    """Check if a video file has a readable video stream."""
    try:
        out = run_ff(["ffprobe", "-v", "error", "-select_streams", "v:0", 
                      "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(filepath)])
        return "video" in out.lower()
    except (subprocess.CalledProcessError, OSError, ValueError):
        return False


def is_image(filepath):
    """Check if a file is an image based on extension."""
    return Path(filepath).suffix.lower() in IMAGE_EXTENSIONS


def get_local_media():
    """Return list of valid local media files (videos + images) from INPUT_DIR."""
    if not INPUT_DIR.exists():
        return []
    valid = []
    for f in INPUT_DIR.iterdir():
        ext = f.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            valid.append(f)
        elif ext in VIDEO_EXTENSIONS:
            if is_valid_video(f):
                valid.append(f)
            else:
                print(f"[Warning] Skipping corrupt/unreadable clip: {f.name}")
    return valid


def filename_keywords(filepath):
    """Extract keywords from a filename for matching (e.g. 'tripura_gudok.jpg' -> ['tripura', 'gudok'])."""
    stem = Path(filepath).stem.lower()
    # split on underscores, hyphens, spaces, digits
    return [w for w in re.split(r'[_\-\s\d]+', stem) if len(w) > 1]


# Per-run circuit breaker: first Gemini 429 trips this flag. Subsequent calls
# skip Gemini entirely instead of burning more quota and retrying.
_GEMINI_QUOTA_DEAD = False


def smart_match_media(keyword, narration, local_media, idx, used_set, hybrid=False, threshold=0.5):
    """Pick the best local media file matching the keyword/narration using semantic Gemini matching.

    Returns None if no file is a real match. The caller should then fall
    through to Pexels/Pixabay rather than forcing a bad local match.
    """
    global _GEMINI_QUOTA_DEAD
    if not local_media:
        return None

    client = _get_gemini_client()
    file_list = [f.name for f in local_media]

    # Try Gemini semantic match (unless quota is dead for this run)
    if client is not None and not _GEMINI_QUOTA_DEAD:
        prompt = f"""
You are matching a segment's visual needs to a list of local files (images or videos).

Segment Keyword: "{keyword}"
Segment Narration: "{narration}"

Available Files:
{json.dumps(file_list, indent=2)}

Already Used Files:
{json.dumps(list(used_set), indent=2)}

Rules:
1. Select the single best file from the "Available Files" that is a STRONG thematic match.
2. Strongly prefer files that have not been "Already Used" yet for diversity.
3. If NONE of the files are a good match for the topic, return "NONE". A mantis shrimp file is NOT a match for a script about ants.
4. Return ONLY the exact filename from the list, or "NONE". No other text.
"""
        try:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            ans = resp.text.strip().strip('"').strip("'").strip()
            if ans != "NONE" and ans in file_list:
                for f in local_media:
                    if f.name == ans:
                        used_set.add(str(f))
                        return f
            else:
                # Gemini said NONE - respect it, don't fall through to fuzzy match
                return None
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                _GEMINI_QUOTA_DEAD = True
                print(f"    [Gemini quota] 429 hit; disabling Gemini for rest of run.")
            else:
                print(f"    [Warning] Gemini matching failed ({err_str[:120]}).")
    elif _GEMINI_QUOTA_DEAD:
        pass  # silent skip, already announced

    # STRICT filename fallback. Require at least one filename keyword to
    # actually appear as a substring of the narration/keyword. SequenceMatcher
    # fuzzy similarity by itself is too lenient and matched "mantis_shrimp" to
    # "ant" in past runs. Only pick a file if there's a real word overlap.
    search_text = f"{keyword} {narration}".lower()
    scored = []
    for f in local_media:
        fkeys = filename_keywords(f)
        if not fkeys:
            continue
        # Hard requirement: at least one filename keyword must appear in the
        # narration/keyword text as a substring. No substring match = no use.
        overlap = [kw for kw in fkeys if kw in search_text]
        if not overlap:
            continue
        score = len(overlap)
        if str(f) in used_set:
            score -= 0.5
        scored.append((score, f))

    if not scored:
        # No local file has any word-overlap with the topic. Return None so
        # the caller falls through to Pexels/Pixabay.
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    _, best = scored[0]
    used_set.add(str(best))
    return best


def image_to_clip(image_path, duration, idx):
    """Convert a still image into a video clip with a Ken Burns zoom effect."""
    out_path = OUT_DIR / f"img_clip_{idx}.mp4"
    total_frames = max(int(duration * FPS), 1)
    zoom_per_frame = 0.2 / total_frames  # 20% total zoom spread evenly across frames
    vf = (
        f"loop=loop={total_frames}:size=1:start=0,"
        f"scale={WIDTH*2}:{HEIGHT*2}:force_original_aspect_ratio=increase,"
        f"zoompan=z='min(zoom+{zoom_per_frame:.6f},1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"setsar=1"
    )
    run_ff([
        "ffmpeg", "-y",
        "-i", str(image_path),
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(out_path),
    ])
    return out_path


def build_split_screen(image_a, image_b, duration, idx):
    """Create a split-screen (top/bottom) comparison clip from two images or videos."""
    out_path = OUT_DIR / f"split_{idx}.mp4"
    half_h = HEIGHT // 2
    total_frames = int(duration * FPS)

    # Build filter for input A (top half)
    if is_image(image_a):
        input_a = ["-loop", "1", "-i", str(image_a)]
        filter_a = f"[0:v]scale={WIDTH}:{half_h}:force_original_aspect_ratio=increase,crop={WIDTH}:{half_h},setsar=1[top]"
    else:
        input_a = ["-stream_loop", "-1", "-i", str(image_a)]
        filter_a = f"[0:v]scale={WIDTH}:{half_h}:force_original_aspect_ratio=increase,crop={WIDTH}:{half_h},setsar=1,fps={FPS}[top]"

    # Build filter for input B (bottom half)
    if is_image(image_b):
        input_b = ["-loop", "1", "-i", str(image_b)]
        filter_b = f"[1:v]scale={WIDTH}:{half_h}:force_original_aspect_ratio=increase,crop={WIDTH}:{half_h},setsar=1[bot]"
    else:
        input_b = ["-stream_loop", "-1", "-i", str(image_b)]
        filter_b = f"[1:v]scale={WIDTH}:{half_h}:force_original_aspect_ratio=increase,crop={WIDTH}:{half_h},setsar=1,fps={FPS}[bot]"

    filter_complex = f"{filter_a};{filter_b};[top][bot]vstack=inputs=2[out]"

    run_ff([
        "ffmpeg", "-y",
        *input_a, *input_b,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        str(out_path),
    ])
    return out_path


def is_gemini_image_available():
    return bool(GEMINI_API_KEY)


def generate_gemini_image(prompt, idx):
    if not is_gemini_image_available():
        return None

    from google.genai import types

    client = _get_gemini_client()
    if client is None:
        return None
    orientation = "vertical portrait 9:16" if HEIGHT > WIDTH else "horizontal widescreen 16:9"

    print(f"    [Gemini Image] Generating for segment {idx+1} (prompt: '{prompt}')...")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                f"Generate a {orientation} image of {prompt}, documentary cinematography style, vibrant colors, photorealistic, high quality"
            ],
            config=types.GenerateContentConfig(
                response_modalities=["Text", "Image"]
            ),
        )

        out_path = OUT_DIR / f"gemini_img_{idx}.png"
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                with open(out_path, "wb") as f:
                    f.write(part.inline_data.data)
                return out_path

        print(f"    [Gemini Image] No image data in response")
        return None
    except Exception as e:
        print(f"    [Gemini Image] Generation failed ({e}).")
        return None


def broll_query_list(seg, fallback):
    queries = []
    raw_queries = seg.get("broll_queries") or []
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    for q in [seg.get("broll"), *raw_queries, fallback, "cinematic documentary"]:
        q = str(q or "").strip()
        if q and q.lower() not in {seen.lower() for seen in queries}:
            queries.append(q)
    return queries


def interactive_broll_review(segments, niche):
    """Show each segment's proposed b-roll queries.

    If output/broll_overrides.json exists, loads overrides from it.
    Otherwise, prints the review and writes a template file for the user to edit.

    Returns a dict of overrides keyed by segment index:
      - {"queries": [...]}  - use these search terms instead
      - {"clip_path": "..."} - use this local file directly
      - {"skip": True}      - use Gemini image generation
    Empty dict means everything was accepted as-is.
    """
    override_path = OUT_DIR / "broll_overrides.json"

    if override_path.exists():
        raw = override_path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                overrides = json.loads(raw)
                print(f"[i] Loaded {len(overrides)} b-roll override(s) from {override_path}")
                print(f"    Delete that file to re-generate defaults.\n")
                return overrides
            except json.JSONDecodeError as e:
                print(f"    [!] Invalid JSON in {override_path}: {e}")
                print(f"    Delete the file and re-run to generate a fresh template.\n")

    print("\n=== B-ROLL QUERY REVIEW ===\n")
    print("Proposed search queries for each segment's footage:\n")

    template = {}
    for i, seg in enumerate(segments):
        qlist = broll_query_list(seg, niche)
        narr = seg["narration"][:120] + ("..." if len(seg["narration"]) > 120 else "")
        print(f"  Segment {i+1}:")
        print(f"    Voice:   {narr}")
        print(f"    Queries: {', '.join(qlist[:4])}")
        print()
        template[str(i)] = {"queries": qlist[:3]}

    override_path.parent.mkdir(exist_ok=True)
    override_path.write_text(
        json.dumps(template, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[i] Template written to {override_path}")
    print(f"    Edit that file to customize queries, then re-run the same command.")
    print(f"    Options per segment:")
    print(f"      \"queries\": [\"term1\", \"term2\", ...]  - custom search terms")
    print(f"      \"clip_path\": \"C:/path/to/clip.mp4\"   - use a local file")
    print(f"      \"skip\": true                          - use Gemini image generation")
    print(f"    Delete the file to accept defaults.\n")
    sys.exit(0)


def pexels_video_score(video, target_duration):
    duration = float(video.get("duration") or 0)
    files = video.get("video_files") or []
    best_height = max((int(f.get("height") or 0) for f in files), default=0)
    best_width = max((int(f.get("width") or 0) for f in files), default=0)
    score = 0.0
    if HEIGHT > WIDTH:
        score += min(best_height, 1920) / 1920
        score += 0.35 if best_height >= best_width else 0
    else:
        score += min(best_width, 1920) / 1920
        score += 0.35 if best_width >= best_height else 0
    if duration >= target_duration:
        score += 0.35
    elif duration >= max(2.5, target_duration * 0.45):
        score += 0.18
    if duration > 0 and duration <= 25:
        score += 0.08
    return score


def best_pexels_file(video):
    files = sorted(
        video.get("video_files", []),
        key=lambda f: (
            f.get("height" if HEIGHT > WIDTH else "width", 0) >= 1280,
            f.get("height" if HEIGHT > WIDTH else "width", 0),
            f.get("width" if HEIGHT > WIDTH else "height", 0),
        ),
        reverse=True,
    )
    return files[0] if files else None


def _download_to(url, out_path, timeout=120):
    """Stream a URL to a file. Returns True on success."""
    import requests
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"    [download] failed for {url[:80]}...: {e}")
        if out_path.exists():
            try: out_path.unlink()
            except OSError: pass
        return False


def _qualify_query(q, fallback=""):
    """Append domain qualifiers to a search query so Pexels/Pixabay keyword
    matching doesn't return irrelevant results (e.g. human cosplay for
    'vampire squid').  The qualifiers are inferred from the query and fallback."""
    q = q.strip()
    if not q:
        return q
    low = (q + " " + fallback).lower()
    qualifiers = []
    if any(w in low for w in ("deep sea", "ocean", "sea", "marine", "underwater", "aquatic",
                               "coral", "fish", "shark", "whale", "dolphin", "octopus",
                               "squid", "jellyfish", "turtle", "seal", "ray", "eel",
                               "crab", "lobster", "shrimp", "plankton")):
        qualifiers = ["underwater", "ocean", "animal"]
    elif any(w in low for w in ("space", "galaxy", "universe", "astronomy", "planet",
                                 "star", "nebula", "cosmos", "solar", "nasa")):
        qualifiers = ["space", "astronomy"]
    elif any(w in low for w in ("dinosaur", "prehistoric", "fossil", "jurassic",
                                 "cretaceous", "triceratops", "raptor")):
        qualifiers = ["dinosaur", "prehistoric"]
    q_low = q.lower()
    extra = " ".join(w for w in qualifiers if w not in q_low)
    return f"{q} {extra}".strip() if extra else q


def fetch_pexels_video(queries, idx, used_set, target_duration=5.0, fallback="", narration=""):
    """Search Pexels for a portrait video matching any of the queries.
    Returns Path to the downloaded mp4, or None."""
    import requests
    if not PEXELS_API_KEY:
        return None

    headers = {"Authorization": PEXELS_API_KEY}

    def search(q):
        orientation = "landscape" if WIDTH > HEIGHT else "portrait"
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q, "orientation": orientation, "per_page": 10, "size": "medium"},
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("videos", []) or []
        except Exception as e:
            print(f"    [Pexels] search failed for {q!r}: {e}")
            return []

    # Only try the specific queries passed in. Do NOT append generic fallbacks.
    # Qualify each query with context keywords to avoid literal-but-wrong matches
    # (e.g. "vampire squid" matching a vampire cosplay).
    for q in queries:
        enriched = _qualify_query(q, fallback)
        videos = search(enriched)
        candidates = [v for v in videos if f"pexels:{v.get('id')}" not in used_set]
        if not candidates:
            continue
        candidates.sort(key=lambda v: pexels_video_score(v, target_duration), reverse=True)
        video = candidates[0]
        used_set.add(f"pexels:{video.get('id')}")
        pexel_file = best_pexels_file(video)
        if pexel_file is None:
            continue
        link = pexel_file["link"]
        out_path = OUT_DIR / f"broll_{idx}.mp4"
        label = enriched if enriched != q else q
        print(f"    [Pexels] Query: {label!r}  video_id={video.get('id')}")
        if _download_to(link, out_path):
            return out_path
    return None


def fetch_pixabay_video(queries, idx, used_set, target_duration=5.0, fallback=""):
    """Search Pixabay for a vertical video. Returns Path or None.

    Pixabay's video API mirrors their image API. Free key from
    https://pixabay.com/api/docs/  Free tier: ~100 requests/min, plenty.
    """
    import requests
    if not PIXABAY_API_KEY or "your_pixabay" in PIXABAY_API_KEY:
        return None

    def search(q):
        try:
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key":         PIXABAY_API_KEY,
                    "q":           q,
                    "video_type":  "film",
                    "per_page":    20,
                    "safesearch":  "true",
                },
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("hits", []) or []
        except Exception as e:
            print(f"    [Pixabay] search failed for {q!r}: {e}")
            return []

    portrait = HEIGHT > WIDTH

    def score(hit):
        # Pixabay returns multiple renditions per hit under hit["videos"].
        # Pick the best one for our aspect ratio, score on resolution + duration fit.
        renditions = (hit.get("videos") or {}).values()
        if not renditions:
            return -1, None
        best = None
        best_dim = 0
        for rd in renditions:
            w, h = rd.get("width", 0), rd.get("height", 0)
            if portrait:
                if h <= 0 or h < w:    # skip landscape variants
                    continue
                dim = h
            else:
                if w <= 0 or w < h:
                    continue
                dim = w
            if dim > best_dim:
                best, best_dim = rd, dim
        if not best:
            return -1, None
        dur = hit.get("duration", 0) or 0
        # Higher resolution good, longer than target_duration good, 4x target excessive
        dur_score = 1.0 if dur >= target_duration else dur / max(target_duration, 1)
        return (best_dim / 1000.0) + dur_score, best

    for q in queries:
        enriched = _qualify_query(q, fallback)
        hits = search(enriched)
        candidates = [h for h in hits if f"pixabay:{h.get('id')}" not in used_set]
        if not candidates:
            continue
        # Score and pick best
        scored = [(score(h)[0], score(h)[1], h) for h in candidates]
        scored = [(s, rd, h) for s, rd, h in scored if rd is not None]
        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        s, rendition, hit = scored[0]
        used_set.add(f"pixabay:{hit.get('id')}")
        out_path = OUT_DIR / f"broll_{idx}.mp4"
        print(f"    [Pixabay] Query: {q!r}  hit_id={hit.get('id')}  {rendition.get('width')}x{rendition.get('height')}")
        if _download_to(rendition["url"], out_path):
            return out_path
    return None


def fetch_nasa_video(queries, idx, used_set, target_duration=5.0):
    """Search the NASA Image and Video Library for a video clip.
    Returns Path or None. No API key required.

    Docs: https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
    """
    import requests

    def search(q):
        try:
            r = requests.get(
                "https://images-api.nasa.gov/search",
                params={"q": q, "media_type": "video"},
                timeout=30,
            )
            r.raise_for_status()
            return (r.json().get("collection", {}) or {}).get("items", []) or []
        except Exception as e:
            print(f"    [NASA] search failed for {q!r}: {e}")
            return []

    def get_asset_url(nasa_id):
        # Each item exposes an asset manifest at /asset/<id>
        try:
            r = requests.get(f"https://images-api.nasa.gov/asset/{nasa_id}", timeout=30)
            r.raise_for_status()
            items = (r.json().get("collection", {}) or {}).get("items", []) or []
            # NASA returns multiple renditions: ~mobile, ~medium, ~large, ~orig.
            # Prefer "medium" or "small" mp4 (avoid huge "~orig" downloads).
            mp4s = [it["href"] for it in items if it.get("href", "").endswith(".mp4")]
            if not mp4s:
                return None
            preferred = next((u for u in mp4s if "~medium" in u or "~small" in u), None)
            return preferred or mp4s[0]
        except Exception as e:
            print(f"    [NASA] asset lookup failed: {e}")
            return None

    for q in queries:
        items = search(q)
        for item in items[:5]:
            data = (item.get("data") or [{}])[0]
            nasa_id = data.get("nasa_id")
            if not nasa_id or f"nasa:{nasa_id}" in used_set:
                continue
            url = get_asset_url(nasa_id)
            if not url:
                continue
            used_set.add(f"nasa:{nasa_id}")
            out_path = OUT_DIR / f"broll_{idx}.mp4"
            print(f"    [NASA] Query: {q!r}  nasa_id={nasa_id}")
            if _download_to(url, out_path, timeout=180):
                return out_path
    return None


def _load_persistent_used():
    """Load cross-video used_set from disk so clips aren't repeated across runs."""
    try:
        if PERSISTENT_USED_PATH.exists():
            data = json.loads(PERSISTENT_USED_PATH.read_text())
            return set(data) if isinstance(data, list) else set()
    except (OSError, json.JSONDecodeError):
        pass
    return set()

def _save_persistent_used(s):
    try:
        trimmed = sorted(s, key=lambda x: str(x))[-500:]
        PERSISTENT_USED_PATH.write_text(json.dumps(trimmed))
    except (OSError, TypeError):
        pass


def fetch_broll(queries, idx, fallback, local_media=None, narration="", used_set=None,
                hybrid=False, threshold=0.5, dalle=False, target_duration=5.0,
                no_interactive=False):
    """Chain b-roll sources: local -> DALL-E (opt) -> Pexels -> Pixabay -> NASA (space).

    Uses a persistent cross-video used_set so clips aren't repeated across runs.
    """
    used_set = used_set if used_set is not None else _load_persistent_used()
    if isinstance(queries, str):
        queries = [queries]
    queries = [q for q in queries if q]
    keyword = queries[0] if queries else fallback

    # 1. Local media
    if local_media:
        match = smart_match_media(keyword, narration, local_media, idx, used_set,
                                  hybrid=hybrid, threshold=threshold)
        if match:
            print(f"    [Local] Using: {match.name}")
            return match
        elif hybrid or dalle:
            print(f"    [Local] No strong match for '{keyword}' (below threshold).")

    # 2. Gemini image generation (when explicitly requested via --dalle flag)
    if dalle:
        dalle_img = generate_gemini_image(keyword, idx)
        if dalle_img:
            return dalle_img
        print(f"    [Gemini Image] failed; falling through to stock sources.")

    # 3. Stock sources: Pexels (best portrait library) - Pixabay - NASA (space only).
    #    Persistent used_set across runs prevents clip repetition between videos.
    for source, fetcher in [
        ("pexels", lambda: fetch_pexels_video(queries, idx, used_set, target_duration=target_duration, fallback=fallback, narration=narration)),
        ("pixabay", lambda: fetch_pixabay_video(queries, idx, used_set, target_duration=target_duration, fallback=fallback)),
    ] + ([("nasa", lambda: fetch_nasa_video(queries, idx, used_set, target_duration=target_duration))]
         if any(needs_nasa(q) for q in queries) else []):
        out = fetcher()
        if out:
            _save_persistent_used(used_set)
            return out

    # 4. Auto Gemini Image fallback when stock footage fails (free, no billing)
    if is_gemini_image_available():
        print(f"    [Gemini Image] Stock footage exhausted; generating image for '{keyword}'...")
        gemini_img = generate_gemini_image(keyword, idx)
        if gemini_img:
            _save_persistent_used(used_set)
            return gemini_img

    # 5. Last-resort generic Pexels search using the niche/fallback term.
    # This is the "broad nature shot" safety net so scheduled runs don't die.
    print(f"    [!] No specific footage found for segment {idx+1} ('{keyword}'); trying broad niche search.")
    broad_terms = []
    low = (keyword + " " + narration).lower()
    if any(w in low for w in ("deep sea", "ocean", "sea", "marine", "underwater", "aquatic",
                              "coral", "fish", "shark", "whale", "dolphin", "octopus",
                              "squid", "jellyfish", "turtle", "seal", "ray", "eel",
                              "crab", "lobster", "shrimp", "plankton", "water", "river", "lake")):
        broad_terms += ["underwater nature", "sea life close up", "ocean reef"]
    elif any(w in low for w in ("space", "galaxy", "universe", "astronomy", "planet",
                                "star", "nebula", "cosmos", "solar", "nasa", "orbit", "astronaut")):
        broad_terms += ["outer space", "galaxy stars", "nebula space"]
    else:
        broad_terms += ["wildlife close up", "animals in wild", "nature documentary"]
        
    if fallback:
        broad_terms.insert(0, fallback)
    broad_out = fetch_pexels_video(broad_terms, idx, used_set, target_duration=target_duration)
    if broad_out:
        print(f"    [Pexels broad] Using generic '{fallback}' / nature clip as fallback.")
        _save_persistent_used(used_set)
        return broad_out

    # 6. Interactive fallback (only when no_interactive=False, i.e. attended runs).
    # Scheduled runs MUST pass no_interactive=True so they don't hang on stdin.
    if no_interactive:
        die(f"No B-roll found for '{keyword}'. (--no-interactive mode; not prompting.)")

    print(f"    [!] All automatic sources exhausted for segment {idx+1} ('{keyword}').")
    print(f"    You can supply a local file to use as this segment's clip.")
    try:
        ans = input(f"    Path to clip (Enter to quit): ").strip()
        if ans:
            p = Path(ans).expanduser().resolve()
            if p.exists():
                print(f"    [User] Using: {p.name}")
                return p
            else:
                print(f"    [!] Not found: {p}")
    except (EOFError, KeyboardInterrupt):
        pass

    die(f"No B-roll found across Pexels, Pixabay, NASA, Gemini Image for '{keyword}'.")


# ----------------------------------------------------------------------------
# Step 4: build one segment clip (B-roll cropped to vertical + its voiceover)
# ----------------------------------------------------------------------------
def _video_is_landscape(path):
    """Quick ffprobe check — returns True if the video frame is wider than tall."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=,:p=0", str(path),
        ], text=True, timeout=10).strip()
        parts = out.split(",")
        return len(parts) == 2 and int(parts[0]) > int(parts[1])
    except (subprocess.CalledProcessError, OSError, ValueError):
        return False


def build_segment(idx, broll, voice, duration, compare_pair=None):
    out_path = OUT_DIR / f"seg_{idx}.mp4"

    # Comparison mode: split-screen from two media files
    if compare_pair:
        split_clip = build_split_screen(compare_pair[0], compare_pair[1], duration, idx)
        # Mux the split-screen video with the voiceover audio
        run_ff([
            "ffmpeg", "-y",
            "-i", str(split_clip),
            "-i", str(voice),
            "-t", f"{duration:.3f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-shortest",
            str(out_path),
        ])
        return out_path

    # Image mode: convert to clip with Ken Burns effect first
    if is_image(broll):
        img_clip = image_to_clip(broll, duration, idx)
        run_ff([
            "ffmpeg", "-y",
            "-i", str(img_clip),
            "-i", str(voice),
            "-t", f"{duration:.3f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-shortest",
            str(out_path),
        ])
        return out_path

    # Standard video mode — detect landscape content and use blur-background padding
    is_landscape = _video_is_landscape(broll)
    if is_landscape:
        vf = (
            f"[0:v]split[orig][blur];"
            f"[blur]scale={WIDTH}:{HEIGHT},boxblur=20:5[bg];"
            f"[orig]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=fit[fg];"
            f"[bg][fg]overlay=(WIDTH-overlay_w)/2:(HEIGHT-overlay_h)/2,setsar=1,fps={FPS}[vout]"
        )
    else:
        vf = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
              f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}[vout]")
    run_ff([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(broll),
        "-i", str(voice),
        "-t", f"{duration:.3f}",
        "-filter_complex", vf,
        "-map", "[vout]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-shortest",
        str(out_path),
    ])
    return out_path


# ----------------------------------------------------------------------------
# Step 5: captions (word-chunked ASS for clean, readable subtitles)
# ----------------------------------------------------------------------------
WORDS_PER_CHUNK = 2   # smaller chunks for snappier reading


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        s += 1
        cs = 0
        if s == 60:
            m += 1
            cs = 0
            if m == 60:
                h += 1
                m = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", "")
        .replace("}", "")
        .replace("\n", " ")
    )


def _highlight_important_words(words):
    """Highlight up to 2 important words per chunk with a different color."""
    scored = []
    for idx, word in enumerate(words):
        clean = re.sub(r"[^A-Za-z0-9']", "", word).lower()
        score = 0
        if len(clean) >= 5:
            score = len(clean)
        if clean in CAPTION_HIGHLIGHT_WORDS:
            score += 15
        scored.append((idx, score))
    scored.sort(key=lambda x: -x[1])
    highlight_indices = {scored[0][0]} if scored else set()
    if len(scored) > 1 and scored[1][1] >= 6:
        highlight_indices.add(scored[1][0])
    return highlight_indices


def format_caption_line(chunk):
    """Format caption text: bold white with yellow highlights on important words."""
    words = chunk.split()
    highlights = _highlight_important_words(words)
    rendered = []
    for idx, word in enumerate(words):
        safe = ass_escape(word).upper()
        if idx in highlights:
            rendered.append(r"{\c&H00E7FF&\b1}" + safe + r"{\r}")
        else:
            rendered.append(safe)
    return " ".join(rendered)


def build_ass(segments_meta, video_duration=None):
    """Build an ASS file with single-line lower-third captions.

    Single line per chunk, positioned above the Shorts bottom UI.
    Clean bold white text with yellow highlights, no animation.

    If video_duration is provided, caption timings are scaled so they fit
    exactly within the video. This prevents the common "video ends mid-caption"
    issue caused by ffmpeg concat variance (10-50ms per segment over 9
    segments adds up).
    """
    all_chunks = []
    clock = 0.0
    for text, dur in segments_meta:
        if not text.strip():
            continue
        words = text.split()
        chunks = []
        for i in range(0, len(words), WORDS_PER_CHUNK):
            chunks.append(" ".join(words[i:i + WORDS_PER_CHUNK]))
        total_words = len(words)
        for chunk in chunks:
            chunk_words = len(chunk.split())
            chunk_dur = dur * (chunk_words / total_words) if total_words > 0 else dur
            all_chunks.append((chunk, clock, clock + chunk_dur))
            clock += chunk_dur

    # Sync to actual video duration. Leave a 0.3s buffer at the end so the
    # last caption fades out cleanly before the video cuts.
    natural_end = clock
    if video_duration and video_duration > 0 and natural_end > 0:
        target_end = max(0.5, video_duration - 0.3)
        scale = target_end / natural_end
        if abs(scale - 1.0) > 0.005:  # only scale if drift is > 0.5%
            print(f"[i] Syncing captions to video: natural_end={natural_end:.2f}s, "
                  f"target_end={target_end:.2f}s, scale={scale:.3f}")
            all_chunks = [(c, s * scale, e * scale) for c, s, e in all_chunks]

    entries = []
    for chunk, start, end in all_chunks:
        line = format_caption_line(chunk)
        anim = r"{\fad(60,80)}"
        entries.append((ass_time(start), ass_time(end), anim + line))

    margin_v = 600 if HEIGHT > WIDTH else 120
    font_size = 80 if HEIGHT > WIDTH else 52

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00141414,&H00000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    for start, end, text in entries:
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    path = OUT_DIR / "captions.ass"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Step 6: concat segments, then burn captions
# ----------------------------------------------------------------------------
def concat_segments(seg_paths):
    combined = OUT_DIR / "combined.mp4"
    if len(seg_paths) == 1:
        shutil.copyfile(seg_paths[0], combined)
        return combined

    # Crossfade between every segment: xfade (video) + acrossfade (audio).
    # Both produce the same overlapping output length, so they stay in sync.
    fade_dur = 0.35  # 350ms smooth transition
    durations = []
    for p in seg_paths:
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=s=,:p=0", str(p),
            ], text=True, timeout=10).strip()
            durations.append(float(out))
        except (subprocess.CalledProcessError, OSError, ValueError):
            durations.append(6.0)

    n = len(seg_paths)
    xfades, afades = [], []
    for i in range(1, n):
        prev_total = sum(durations[:i])
        offset = prev_total - i * fade_dur
        v_in = f"[v{i-1}]" if i > 1 else "[0:v]"
        a_in = f"[a{i-1}]" if i > 1 else "[0:a]"
        xfades.append(f"{v_in}[{i}:v]xfade=transition=fade:duration={fade_dur}:offset={max(offset, 0)}[v{i}]")
        afades.append(f"{a_in}[{i}:a]acrossfade=d={fade_dur}:c1=tri:c2=tri[a{i}]")

    inputs = []
    for p in seg_paths:
        inputs.extend(["-i", str(p)])

    run_ff([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", "; ".join(xfades + afades),
        "-map", f"[v{n - 1}]", "-map", f"[a{n - 1}]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        str(combined),
    ])
    return combined


def burn_captions():
    # Convert captions.ass path to absolute, use forward slashes and escape colon for Windows ffmpeg
    ass_path = (OUT_DIR / "captions.ass").resolve().as_posix()
    ass_escaped = ass_path.replace(":", "\\:").replace(",", "\\,")
    run_ff([
        "ffmpeg", "-y", "-i", "combined.mp4",
        "-vf", f"subtitles='{ass_escaped}'",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "captioned.mp4",
    ], cwd=OUT_DIR)
    return OUT_DIR / "captioned.mp4"


def get_music_files():
    if not MUSIC_DIR.exists():
        return []
    return sorted(
        [p for p in MUSIC_DIR.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS],
        key=lambda p: p.name.lower(),
    )


def pick_music_track(mood):
    tracks = get_music_files()
    if not tracks:
        return None

    mood = (mood or "").lower()
    mood_matches = [p for p in tracks if mood and mood in p.stem.lower()]
    if mood_matches:
        return random.choice(mood_matches)
    return random.choice(tracks)


# Mood vocabulary the Gemini prompt produces -> Jamendo's tag vocabulary.
# fuzzytags is forgiving; we send 2-3 tags so the search still returns
# something even if one tag is rare.
JAMENDO_MOOD_TAGS = {
    "mysterious": ["ambient", "dark", "cinematic"],
    "inspiring":  ["uplifting", "inspirational", "cinematic"],
    "dramatic":   ["epic", "cinematic", "tension"],
    "warm":       ["acoustic", "soft", "relaxing"],
    "curious":    ["ambient", "soundscape", "dreamy"],
    "urgent":     ["epic", "intense", "drum"],
}
JAMENDO_CACHE_DIR = MUSIC_DIR / "_jamendo_cache"

# Pixabay music: same API key as video B-roll, Content ID-free by policy.
PIXABAY_MUSIC_QUERIES = {
    "mysterious": ["dark ambient", "mysterious cinematic"],
    "inspiring":  ["uplifting", "inspirational background"],
    "dramatic":   ["epic dramatic", "cinematic action"],
    "warm":       ["soft acoustic", "gentle piano"],
    "curious":    ["ambient electronic", "light background"],
    "urgent":     ["intense action", "tense suspense"],
}
PIXABAY_MUSIC_CACHE = MUSIC_DIR / "_pixabay_cache"


def fetch_pixabay_music(mood, min_duration=30):
    """
    Fetch a royalty-free track from Pixabay's audio library.
    Pixabay music is free for commercial use and not enrolled in Content ID —
    unlike Jamendo CC tracks, which can be registered with Adrev/DistroKid
    while simultaneously carrying a Creative Commons license.
    Returns Path to downloaded mp3, or None on any failure.
    """
    import requests
    if not PIXABAY_API_KEY or "your_pixabay" in PIXABAY_API_KEY:
        return None

    queries = PIXABAY_MUSIC_QUERIES.get((mood or "").lower(), ["ambient music"])
    PIXABAY_MUSIC_CACHE.mkdir(parents=True, exist_ok=True)

    for q in queries:
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key":        PIXABAY_API_KEY,
                    "q":          q,
                    "media_type": "music",
                    "per_page":   20,
                    "safesearch": "true",
                },
                timeout=20,
            )
            r.raise_for_status()
            hits = r.json().get("hits", []) or []
            # Hits have varying field names for audio; try the most common ones.
            candidates = []
            for h in hits:
                dur = h.get("duration", 0) or 0
                if dur < min_duration:
                    continue
                dl = (h.get("audio", {}) or {}).get("url") or h.get("audioURL") or h.get("url") or ""
                if dl:
                    candidates.append((dl, h))
            if not candidates:
                continue
            pool = candidates[: max(3, len(candidates) // 2)]
            dl_url, track = random.choice(pool)
            safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(track.get("tags", "music")))[:30] or "music"
            out_path = PIXABAY_MUSIC_CACHE / f"{track.get('id', 'track')}_{safe_title}.mp3"
            if out_path.exists() and out_path.stat().st_size > 50_000:
                print(f"    [Pixabay Music] cached: {track.get('tags','')!r}")
                return out_path
            with requests.get(dl_url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            print(f"    [Pixabay Music] downloaded: {track.get('tags', '')!r}")
            return out_path
        except Exception as e:
            print(f"    [Pixabay Music] {q!r}: {e}")

    return None


def fetch_jamendo_track(mood, min_duration=30):
    """
    Fetch a single instrumental track from Jamendo matching the mood.
    Returns a Path to the downloaded mp3, or None on any failure (caller
    falls back to the synth pad).

    Jamendo API: https://developer.jamendo.com/v3.0/tracks
    Free Client ID at: https://devportal.jamendo.com/signup
    Music is licensed under Creative Commons - the picker only keeps tracks
    whose ccurl is set (i.e. clear license metadata is exposed).
    """
    if not JAMENDO_CLIENT_ID or "your_jamendo" in JAMENDO_CLIENT_ID:
        return None

    import requests, hashlib

    primary_tags  = JAMENDO_MOOD_TAGS.get((mood or "").lower(), ["ambient", "cinematic"])
    # Broader fallback queries if the narrow 3-tag combo returns nothing.
    # Single broad tags have much higher hit rates on Jamendo.
    fallback_tag_sets = [
        primary_tags,
        [primary_tags[0]],            # just the first tag (broadest)
        ["ambient"],                  # universal nature/Shorts fit
        ["cinematic"],                # also broad and popular
    ]

    def _search(tag_set):
        params = {
            "client_id":          JAMENDO_CLIENT_ID,
            "format":              "json",
            "limit":               20,
            "include":             "musicinfo licenses",
            "audioformat":         "mp32",
            "vocalinstrumental":   "instrumental",
            "audiodlallowed":      "true",
            "fuzzytags":           ",".join(tag_set),
            "order":               "popularity_total_desc",
        }
        try:
            r = requests.get("https://api.jamendo.com/v3.0/tracks/",
                             params=params, timeout=20)
            r.raise_for_status()
            return r.json().get("results", []) or []
        except Exception as e:
            print(f"    [Jamendo] search failed for {tag_set} ({e}).")
            return []

    results = []
    used_tag_set = None
    for tag_set in fallback_tag_sets:
        results = _search(tag_set)
        candidates_check = [
            t for t in results
            if t.get("audiodownload") and (t.get("duration") or 0) >= min_duration
        ]
        if candidates_check:
            used_tag_set = tag_set
            if tag_set != primary_tags:
                print(f"    [Jamendo] narrow tags missed; using broader fallback: {tag_set}")
            break

    candidates = [
        t for t in results
        if t.get("audiodownload")
        and (t.get("duration") or 0) >= min_duration
    ]
    if not candidates:
        print(f"    [Jamendo] no instrumental tracks found across {len(fallback_tag_sets)} tag sets. Falling back.")
        return None

    # Pick one randomly from the top half by popularity so videos don't all
    # share the same track on every run.
    pool = candidates[: max(3, len(candidates) // 2)]
    track = random.choice(pool)
    title  = track.get("name") or "track"
    artist = track.get("artist_name") or "unknown"
    track_id = str(track.get("id") or hashlib.md5(track["audiodownload"].encode()).hexdigest())

    JAMENDO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:40] or "track"
    out_path = JAMENDO_CACHE_DIR / f"{track_id}_{safe_title}.mp3"

    if out_path.exists() and out_path.stat().st_size > 50_000:
        print(f"    [Jamendo] cached: {title!r} by {artist}")
        return out_path

    try:
        with requests.get(track["audiodownload"], stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
    except Exception as e:
        print(f"    [Jamendo] download failed ({e}). Falling back.")
        if out_path.exists():
            try: out_path.unlink()
            except OSError: pass
        return None

    print(f"    [Jamendo] downloaded: {title!r} by {artist}  (tags: {','.join(used_tag_set or [])})")
    return out_path


def generate_music_bed(duration, mood):
    """
    Generate a synthetic chord-based ambient pad as a fallback when no real
    music file is found in music/. Uses sine waves at proper musical pitches
    (root + 5th + minor third or third) with detuning and slow filter motion
    so it sounds like a sustained pad rather than filtered noise or beeping.

    This is intentionally a *fallback* - the right move is to drop royalty-free
    instrumental tracks into music/ tagged by mood. See music/README.md.

    Pitches are in Hz. Each mood is built around one minor or major triad in
    a comfortable bass-mid register (200-500 Hz) so it doesn't fight the voice.
    """
    # Pitch sets are root, third, fifth (Hz). All within 200-500 Hz so they sit
    # under the voice without rumbling. Slight detuning is added per-oscillator
    # so the chord slowly beats and breathes instead of sounding static.
    mood_profiles = {
        # Cm chord (C4-Eb4-G4) - common ambient mystery palette
        "mysterious": {"root": 261.6, "third": 311.1, "fifth": 392.0, "loudness": -22},
        # C major (C4-E4-G4) - brighter, hopeful
        "inspiring":  {"root": 261.6, "third": 329.6, "fifth": 392.0, "loudness": -21},
        # Dm (D4-F4-A4) - tense, building
        "dramatic":   {"root": 293.7, "third": 349.2, "fifth": 440.0, "loudness": -21},
        # F major (F3-A3-C4) - warm low triad
        "warm":       {"root": 174.6, "third": 220.0, "fifth": 261.6, "loudness": -22},
        # Em (E4-G4-B4) - reflective
        "curious":    {"root": 329.6, "third": 392.0, "fifth": 493.9, "loudness": -22},
        # Gm (G3-Bb3-D4) - urgent, driving
        "urgent":     {"root": 196.0, "third": 233.1, "fifth": 293.7, "loudness": -21},
    }
    profile = mood_profiles.get((mood or "").lower(), mood_profiles["mysterious"])
    out_path = OUT_DIR / "music_bed.m4a"
    fade_dur = min(3.0, max(0.5, duration / 8))
    fade_start = max(0, duration - fade_dur)

    # Three oscillators per chord tone (slightly detuned for chorus effect),
    # plus a sub-octave root for body. Detune amounts are in cents (about 7 cents
    # which is a barely-audible shimmer, not a clash).
    def detune(hz, cents):
        return hz * (2 ** (cents / 1200))

    osc = []
    for hz in (profile["root"], profile["third"], profile["fifth"]):
        osc.append(("sine",     hz,                0.20))
        osc.append(("sine",     detune(hz,  7),    0.16))
        osc.append(("triangle", detune(hz, -7),    0.10))
    # gentle sub-octave for warmth
    osc.append(("sine", profile["root"] / 2, 0.18))

    inputs = []
    mixes  = []
    for i, (waveform, freq, vol) in enumerate(osc):
        if waveform == "triangle":
            expr = f"(2/PI)*asin(sin(2*PI*{freq:.3f}*t))"
        else:
            expr = f"sin(2*PI*{freq:.3f}*t)"
        inputs.extend([
            "-f", "lavfi", "-i",
            f"aevalsrc='{vol}*{expr}':d={duration:.3f}:s=44100"
        ])
        mixes.append(f"[{i}:a]")
    n = len(osc)

    # Mix everything, then shape it: lowpass to remove harshness, subtle high-pass
    # to keep it from getting muddy, reverb-like delay for ambient tail, then loudnorm + fade.
    filter_complex = (
        f"{''.join(mixes)}amix=inputs={n}:duration=longest:normalize=0[chord];"
        f"[chord]highpass=f=120,lowpass=f=2400,"
        f"aecho=0.8:0.88:60|120|200:0.30|0.22|0.15,"   # short multi-tap reverb
        f"afade=t=in:st=0:d=2.0,"
        f"afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f},"
        f"loudnorm=I={profile['loudness']}:TP=-3:LRA=9,"
        f"pan=stereo|c0=c0|c1=c0[a]"
    )

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", filter_complex,
           "-map", "[a]",
           "-c:a", "aac", "-b:a", "128k",
           str(out_path)]
    run_ff(cmd)
    return out_path


def add_background_music(video_path, duration, mood, music_path=None, music_volume=DEFAULT_MUSIC_VOLUME):
    if music_volume <= 0:
        final_path = OUT_DIR / "final.mp4"
        shutil.copyfile(video_path, final_path)
        return final_path, None

    # Music source priority (each falls through to the next on failure):
    #   1. explicit --music PATH from CLI
    #   2. local music/ folder match by mood keyword in filename  ← best; populate from Pixabay/Mixkit
    #   3. Pixabay API (if PIXABAY_API_KEY set) — Content ID-free
    #   4. synthesised chord pad — 100% safe, listenable but synthetic
    # Jamendo is intentionally disabled: CC licensing does not prevent Content ID
    # registration (e.g. Adrev/DistroKid), which blocks monetised Shorts ≥60s.
    selected_music = Path(music_path).expanduser() if music_path else None
    if selected_music and not selected_music.exists():
        die(f"Music file not found: {selected_music}")

    if not selected_music:
        selected_music = pick_music_track(mood)
        if selected_music:
            print(f"[i] Using local music: {selected_music.name}")

    if not selected_music:
        selected_music = fetch_pixabay_music(mood, min_duration=max(20, int(duration * 0.7)))

    generated = False
    if not selected_music:
        selected_music = generate_music_bed(duration, mood)
        generated = True
        print(f"[i] No real track found; generated a synth {mood or 'mysterious'} ambient bed.")
    else:
        print(f"[i] Mixing background music: {selected_music.name}")

    final_path = OUT_DIR / "final.mp4"
    fade_dur = min(3.0, max(0.5, duration / 8))
    fade_start = max(0, duration - fade_dur)
    run_ff([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(selected_music),
        "-filter_complex",
        f"[0:a]pan=stereo|c0=c0|c1=c0,asplit[voice1][voice2];"
        f"[1:a]volume={music_volume},afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f},"
        f"aformat=channel_layouts=stereo[musicraw];"
        f"[musicraw][voice1]sidechaincompress=threshold=0.035:ratio=3.5:"
        f"attack=35:release=550[ducked];"
        f"[voice2][ducked]amix=inputs=2:duration=first:dropout_transition=2:"
        f"normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95[a]",
        "-map", "0:v:0",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-ac", "2",
        "-shortest",
        str(final_path),
    ])
    return final_path, str(selected_music.resolve()) if not generated else "generated"


# ----------------------------------------------------------------------------
# SEO helpers
# ----------------------------------------------------------------------------
def _enrich_title(title: str, niche: str, hashtags: list[str]) -> str:
    """Append a high-volume search keyword if the title is short enough."""
    if len(title) > 80:
        return title
    keywords = [t.lstrip("#") for t in hashtags if t.lower() != "#shorts"]
    keyword = next((k for k in keywords if k.lower() not in title.lower()), None)
    if keyword:
        candidate = f"{title} | {keyword.title()}"
        if len(candidate) <= 95:
            return candidate
    candidate = f"{title} | Facts"
    if len(candidate) <= 95:
        return candidate
    return title


def _generate_end_card(channel_name: str, output_path: Path, landscape: bool = False) -> Path:
    """Generate a subscribe end card image using Pillow."""
    w, h = (1920, 1080) if landscape else (1080, 1920)
    img = Image.new("RGB", (w, h), "#1A1A1A")
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 100 if not landscape else 70)
        font_small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 50 if not landscape else 36)
    except (OSError, IOError):
        font_big = ImageFont.load_default()
        font_small = font_big

    subscribe_text = "Subscribe"
    sw = font_big.getlength(subscribe_text)
    draw.text(((w - sw) / 2, h * 0.32), subscribe_text, fill="#FFFFFF", font=font_big)

    cw = font_small.getlength(channel_name)
    draw.text(((w - cw) / 2, h * 0.55), channel_name, fill="#D4AF37", font=font_small)

    sub_line = "for daily content"
    slw = font_small.getlength(sub_line)
    draw.text(((w - slw) / 2, h * 0.65), sub_line, fill="#888888", font=font_small)

    img.save(str(output_path), "JPEG", quality=95)
    return output_path


def _build_subscribe_clip(channel_name: str, output_dir: Path, idx: int, duration_s: float = 2.5, landscape: bool = False) -> Path:
    """Generate a subscribe end card with a subtle slow zoom-in."""
    card_path = output_dir / f"end_card_{idx}.jpg"
    clip_path = output_dir / f"end_card_{idx}.mp4"
    _generate_end_card(channel_name, card_path, landscape=landscape)

    w, h = (1920, 1080) if landscape else (1080, 1920)
    total_frames = max(int(duration_s * FPS), 1)
    zoom_per_frame = 0.08 / total_frames  # 8% zoom over the 4s clip
    run_ff([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(card_path),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{duration_s:.1f}",
        "-vf", (
            f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
            f"zoompan=z='min(zoom+{zoom_per_frame:.6f},1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={w}x{h}:fps={FPS},"
            f"setsar=1"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-shortest",
        str(clip_path),
    ], cwd=output_dir)
    return clip_path


# ----------------------------------------------------------------------------
# Orchestrate
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Auto Short Video Generator")
    parser.add_argument("topic", nargs="?", default=DEFAULT_NICHE, help="Video topic/niche")
    parser.add_argument("--duration", type=int, default=TARGET_DURATION,
                        help=f"Target video duration in seconds (default: {TARGET_DURATION})")
    parser.add_argument("--compare", action="store_true",
                        help="Enable split-screen comparison mode (pairs local media files)")
    parser.add_argument("--hybrid", action="store_true",
                        help="Enable hybrid mode: use local files if they match, otherwise fall back to Pexels")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Similarity threshold for local file matching in hybrid mode (default: 0.5)")
    parser.add_argument("--dalle", action="store_true",
                        help="Use OpenAI DALL-E to generate B-roll images when no local match is found (requires OPENAI_API_KEY)")
    parser.add_argument("--landscape", action="store_true",
                        help="Generate landscape video (1920x1080) instead of vertical (1080x1920)")
    parser.add_argument("--reuse-script", action="store_true",
                        help="Reuse the last successfully generated script from output/last_script.json to bypass Gemini API quotas")
    parser.add_argument("--music", type=str, default="",
                        help="Path to background music file (.mp3/.wav/.m4a). If omitted, music/ folder, Jamendo, then a synth pad are tried in order.")
    parser.add_argument("--music-volume", type=float, default=DEFAULT_MUSIC_VOLUME,
                        help=f"Background music volume relative to narration (default: {DEFAULT_MUSIC_VOLUME})")
    parser.add_argument("--no-music", action="store_true",
                        help="Disable background music entirely")
    parser.add_argument("--review-broll", action="store_true",
                        help="Review and customize b-roll queries per segment before fetching")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Disable the interactive 'enter clip path' prompt when stock sources fail. "
                             "Required for scheduled/unattended runs - otherwise the renderer hangs on stdin.")
    args = parser.parse_args()

    check_deps()
    niche = args.topic
    duration = args.duration
    compare_mode = args.compare
    hybrid = args.hybrid
    threshold = args.threshold
    dalle = args.dalle
    landscape = args.landscape
    reuse_script = args.reuse_script
    review_broll = args.review_broll
    no_interactive = args.no_interactive
    music_path = args.music or None
    music_volume = 0.0 if args.no_music else max(0.0, args.music_volume)

    global WIDTH, HEIGHT
    if landscape:
        WIDTH, HEIGHT = 1920, 1080

    n_segments = max(3, round(duration / AVG_SEGMENT_DURATION))

    OUT_DIR.mkdir(exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(exist_ok=True)
    MUSIC_DIR.mkdir(exist_ok=True)

    local_media = get_local_media()
    if local_media:
        img_count = sum(1 for f in local_media if is_image(f))
        vid_count = len(local_media) - img_count
        if dalle:
            mode_str = "hybrid matching with DALL-E fallback"
        else:
            mode_str = "hybrid matching with Pexels fallback" if hybrid else "local-only matching"
        print(f"[i] Found {len(local_media)} local file(s) in input_clips/ ({vid_count} video, {img_count} image) - using {mode_str}.")
    else:
        print(f"[i] No local media in input_clips/ - will download B-roll from Pexels/Pixabay/NASA.")

    if compare_mode:
        if len(local_media) < 2:
            die("Compare mode requires at least 2 files in input_clips/. Add images/videos to compare.")
        print(f"[i] Compare mode ON - will create split-screen segments.")

    print(f"\n=== Building a ~{duration}s {'landscape' if landscape else 'vertical'} video about: {niche} ({n_segments} segments) ===\n")

    if reuse_script:
        cache_path = OUT_DIR / "last_script.json"
        if not cache_path.exists():
            die("No cached script found in output/last_script.json. Run without --reuse-script first.")
        print(f"[i] Reusing cached script from output/last_script.json...")
        try:
            script = json.loads(cache_path.read_text(encoding="utf-8"))
            script["segments"] = script.get("segments", [])[:n_segments]
            fatal, _soft = script_quality_notes(script, n_segments, duration)
            if duration >= SHORTS_MIN_DURATION and fatal:
                die(
                    "Cached script is too short for a 50-60s video. "
                    "Run without --reuse-script so a fuller story can be generated. "
                    f"First issue: {fatal[0]}"
                )
            print(f"[+] Title: {script.get('title', niche)}")
            for i, s in enumerate(script["segments"]):
                print(f"    {i+1}. {s['narration']}   [b-roll: {s['broll']}]")
        except Exception as e:
            die(f"Failed to load cached script: {e}")
    else:
        print("[1/5] Writing script with Gemini...")
        script = generate_script(niche, n_segments, duration)

    title    = script.get("title", niche)
    hashtags_pre = script.get("hashtags") or ["#shorts"]
    title = _enrich_title(title, niche, hashtags_pre)
    segments = script["segments"]

    # Interactive b-roll review: let user customize queries before any API calls
    broll_overrides = {}
    if review_broll:
        raw = interactive_broll_review(segments, niche)
        broll_overrides = {int(k): v for k, v in raw.items()}

    voice_items = make_all_voices(segments, duration)

    used_set = set()
    seg_paths, meta = [], []
    for item in voice_items:
        idx = item["idx"]
        seg = item["segment"]
        voice = item["voice"]
        dur = item["duration"]

        if compare_mode:
            a_idx = (idx * 2) % len(local_media)
            b_idx = (idx * 2 + 1) % len(local_media)
            media_a = local_media[a_idx]
            media_b = local_media[b_idx]
            print(f"[3/5] Segment {idx+1}: split-screen [{media_a.name}] vs [{media_b.name}]...")
            print(f"[4/5] Segment {idx+1}: assembling ({dur:.1f}s)...")
            seg_paths.append(build_segment(idx, None, voice, dur, compare_pair=(media_a, media_b)))
        else:
            # Check for user override from interactive review
            override = broll_overrides.get(idx)
            if override:
                if "clip_path" in override:
                    print(f"[3/5] Segment {idx+1}: using user-supplied clip...")
                    clip = Path(override["clip_path"]).expanduser().resolve()
                    seg_paths.append(build_segment(idx, clip, voice, dur))
                    meta.append((seg["narration"], dur))
                    continue
                elif "skip" in override:
                    print(f"[3/5] Segment {idx+1}: user skipped stock; using Gemini image...")
                    broll = generate_gemini_image(seg.get("broll") or niche, idx)
                    if broll:
                        seg_paths.append(build_segment(idx, broll, voice, dur))
                        meta.append((seg["narration"], dur))
                        continue
                    print(f"    [Gemini Image] failed; falling through to stock sources.")
                    queries = broll_query_list(seg, niche)
                elif "queries" in override:
                    queries = override["queries"]
                else:
                    queries = broll_query_list(seg, niche)
            else:
                queries = broll_query_list(seg, niche)

            print(f"[3/5] Segment {idx+1}: fetching B-roll '{queries[0]}'...")
            broll = fetch_broll(queries, idx, fallback=niche,
                               local_media=local_media, narration=seg["narration"],
                               used_set=used_set, hybrid=hybrid, threshold=threshold, dalle=dalle,
                               target_duration=dur, no_interactive=no_interactive)
            print(f"[4/5] Segment {idx+1}: assembling ({dur:.1f}s)...")
            seg_paths.append(build_segment(idx, broll, voice, dur))

        meta.append((seg["narration"], dur))

    print("[5/5] Stitching + burning captions...")

    # End card removed - verbal CTA in segment 9 carries the call to action,
    # and static fade-to-logo endings hurt Shorts retention.

    # Concatenate segments FIRST so we can measure actual combined duration,
    # then build ASS captions synced to that duration. Fixes the "captions
    # extend past video end" bug caused by ffmpeg concat variance.
    concat_segments(seg_paths)
    combined_path = OUT_DIR / "combined.mp4"
    try:
        combined_dur = media_duration(combined_path)
    except (OSError, subprocess.CalledProcessError):
        combined_dur = sum(d for _, d in meta)

    # Trim combined to max duration BEFORE building captions so the last
    # caption doesn't get cut off by the post-caption hard cap trim.
    if combined_dur > SHORTS_MAX_DURATION:
        print(f"[!] Combined video is {combined_dur:.1f}s - trimming to {SHORTS_MAX_DURATION}s before captions.")
        trimmed = OUT_DIR / "combined_trimmed.mp4"
        run_ff([
            "ffmpeg", "-y", "-i", str(combined_path),
            "-t", f"{SHORTS_MAX_DURATION:.3f}",
            "-c", "copy",
            str(trimmed),
        ])
        trimmed.replace(combined_path)
        combined_dur = SHORTS_MAX_DURATION

    build_ass(meta, video_duration=combined_dur)
    captioned = burn_captions()

    try:
        captioned_duration = media_duration(captioned)
    except (OSError, subprocess.CalledProcessError):
        captioned_duration = sum(d for _, d in meta)

    final, music_used = add_background_music(
        captioned,
        captioned_duration,
        script.get("music_mood"),
        music_path=music_path,
        music_volume=music_volume,
    )

    try:
        total = media_duration(final)
    except (OSError, subprocess.CalledProcessError):
        total = captioned_duration

    # YouTube-safe variant: just the captioned video (voice + visuals, NO
    # background music). This avoids Adrev/Content ID claims on YouTube while
    # IG/FB still get the richer Jamendo mix on final.mp4. The YT uploader
    # will add YT Audio Library music post-upload via the Shorts editor.
    final_yt_safe = OUT_DIR / "final_yt_safe.mp4"
    print(f"[i] Producing YT-safe variant (no music) -> {final_yt_safe.name}")
    run_ff([
        "ffmpeg", "-y", "-i", str(captioned),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(final_yt_safe),
    ])

    # HARD CAP: trim both versions to <SHORTS_MAX_DURATION (58s) so neither
    # gets the >=60s YouTube Content ID treatment.
    def _trim_if_long(path):
        try:
            dur = media_duration(path)
        except (OSError, subprocess.CalledProcessError):
            return None
        if dur <= SHORTS_MAX_DURATION:
            return dur
        print(f"[!] {path.name} is {dur:.1f}s - exceeds {SHORTS_MAX_DURATION}s; trimming.")
        trimmed = path.with_name(path.stem + "_trimmed.mp4")
        run_ff([
            "ffmpeg", "-y", "-i", str(path),
            "-t", f"{SHORTS_MAX_DURATION:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-movflags", "+faststart",
            str(trimmed),
        ])
        shutil.move(str(trimmed), str(path))
        try:
            return media_duration(path)
        except (OSError, subprocess.CalledProcessError):
            return SHORTS_MAX_DURATION

    new_total = _trim_if_long(final)
    if new_total is not None:
        total = new_total
    _trim_if_long(final_yt_safe)
    try:
        yt_total = media_duration(final_yt_safe)
    except (OSError, subprocess.CalledProcessError):
        yt_total = total
    print(f"    Shorts-safe durations: IG/FB={total:.1f}s  YT={yt_total:.1f}s")

    hashtags = script.get("hashtags") or ["#shorts"]
    hashtag_str = " ".join(hashtags)
    youtube_title = title
    if "#shorts" not in youtube_title.lower():
        candidate = f"{title} #shorts"
        if len(candidate) <= 100:
            youtube_title = candidate
    description_base = script.get("description") or title
    # YouTube SEO: ensure primary keyword (title) appears in first ~150 chars
    if title.lower() not in description_base[:150].lower():
        description_base = f"{title}\n\n{description_base}"
    youtube_description = f"{description_base}\n\n{hashtag_str}"
    facebook_description = youtube_description
    instagram_caption = script.get("instagram_caption") or title
    instagram_caption = f"{instagram_caption}\n\n{hashtag_str}"

    # YouTube tags for search (hashtags without the #, comma-separated)
    youtube_tags = ",".join(t.lstrip("#").replace(" ", "") for t in hashtags if t != "#shorts")

    # --- Queue output: copy final video into videos/pending/<id>/ ----
    video_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}-{uuid.uuid4().hex[:6]}"
    pending_folder = PENDING_DIR / video_id
    pending_folder.mkdir(parents=True, exist_ok=True)
    pending_video = pending_folder / "video.mp4"
    pending_video_yt = pending_folder / "video_yt_safe.mp4"
    shutil.copy2(str(final), str(pending_video))
    shutil.copy2(str(final_yt_safe), str(pending_video_yt))

    metadata = {
        "id": video_id,
        "niche": niche,
        "title": title,
        "youtube_title": youtube_title,
        "youtube_description": youtube_description,
        "facebook_description": facebook_description,
        "instagram_caption": instagram_caption,
        "hashtags": hashtags,
        "youtube_tags": youtube_tags,
        "segments": script.get("segments", []),
        "music_mood": script.get("music_mood"),
        "music_path": str(music_used) if music_used else None,
        "music_volume": music_volume,
        "duration_sec": round(total, 2),
        "video_file": "video.mp4",
        "video_file_yt": "video_yt_safe.mp4",
        "video_path": str(pending_video.resolve()),
        "video_path_yt": str(pending_video_yt.resolve()),
        "orientation": "landscape" if landscape else "portrait",
        "status": "pending",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (pending_folder / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta_path = OUT_DIR / "upload_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[OK] Done -> {final}  ({total:.1f}s)")
    print(f"          + {final_yt_safe.name} (YT-safe, no music)")
    print(f"     YouTube title: {youtube_title}")
    print(f"     Hashtags: {hashtag_str}")
    if music_used:
        print(f"     Music: {music_used}  (volume {music_volume})  [IG/FB only]")
    print(f"     Review queue: videos/pending/{video_id}/")
    print(f"     Upload metadata: {meta_path}")
    print(f"     Open the review dashboard:  python review_dashboard.py\n")


if __name__ == "__main__":
    main()
