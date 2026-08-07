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
import hashlib
import datetime as dt
from dataclasses import replace
from pathlib import Path
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from text_cards import create_text_card

SCRIPT_DIR = Path(__file__).parent
SRC_DIR = SCRIPT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _env_flag(*names: str, default: str = "0") -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value.strip().lower() not in {"0", "false", "no", ""}
    return default.strip().lower() not in {"0", "false", "no", ""}

from autovideo.audio import ClipAudioDecision, build_audio_mix_report, clip_audio_filter
from autovideo.config import AppConfig, DEFAULTS, ProviderRegistry, Settings
from autovideo.domain import (
    MediaAsset,
    MediaSource,
    Script,
    TimelineBuildOptions,
    UploadMetadata,
    VoiceTrack,
    build_timeline,
)
from autovideo.intelligence import (
    DocumentaryViabilityConfig,
    DocumentaryViabilityDecision,
    DocumentaryViabilityEngine,
    SceneCoverage,
    SourceCoverageConfig,
    SourceCoverageDecision,
    SourceCoverageEvaluator,
    build_topic_metadata,
    sample_scene_indexes,
    verified_critical_scene_coverage,
    ExactSubjectAvailabilityGate,
    ExactSubjectGateConfig,
    ExactSubjectGateDecision,
    subject_definition_from_pipeline,
)
from autovideo.intelligence.source_coverage import ProviderProbeOutcome, ProviderProbeStatus
from autovideo.engagement import generate_pinned_comment
from autovideo.media import (
    CanonicalEntityReport,
    CanonicalEntityResolverConfig,
    CanonicalSceneEntityResolver,
    EditorialCanon,
    EditorialCanonBuilder,
    EditorialIdentityGate,
    EditorialIdentityReport,
    EntityFidelity,
    EvidenceVerificationConfig,
    EvidenceVerificationEngine,
    KnowledgePackStore,
    MediaSelectionResult,
    QueryPlanner,
    SceneEntity,
    SceneEntityPlanner,
    SceneConstraintConfig,
    SceneConstraintPlanner,
    SceneConstraintReport,
    SceneVisualFocusPlanner,
    SceneVisualFocusReport,
    SemanticQueryConfig,
    SemanticQueryReport,
    SemanticVisualQueryEngine,
    SceneImportance,
    SearchStrategy,
    SourcePlanner,
    DownloadedMediaEvidence,
    VerificationDecision,
    VerificationPriority,
    VerificationRequest,
    VerifiedMediaGate,
    VerifiedMediaGateConfig,
    VerifiedMediaReport,
    VerifiedMediaSceneResult,
    HybridVisualComposer,
    ShotPlan,
    SourceContinuityEngine,
    SourceContinuityState,
    SourceIdentity,
    SubjectContinuityEngine,
    VisionVerificationResult,
    VisualDirector,
    VisualGrammarEngine,
    build_visual_intent,
    candidate_from_local_path,
    candidate_from_nasa_item,
    candidate_from_pexels_video,
    candidate_from_pixabay_hit,
    candidate_from_remote_item,
    default_provider_capability_registry,
    identity_from_candidate,
    score_candidate,
    select_best_candidate,
)
from autovideo.music import MusicPlanner
from autovideo.intelligence.topic_cards import TopicCard, find_topic_card
from autovideo.providers.factory import build_music_registry, build_voice_registry
from autovideo.providers.llm import CallableLLMProvider
from autovideo.providers.voice import VoiceRequest
from autovideo.pipeline import (
    PipelineContext,
    PipelineOrchestrator,
    PipelineStage,
    PipelineStateStore,
    PublishQualityArtifacts,
    PublishQualityConfig,
    PublishQualityGate,
    RenderedSceneRequest,
    RenderedVisualEvidence,
    RenderedVisualQAGate,
    RenderedVisualQAConfig,
    StageRecord,
    StageResult,
)
from autovideo.render import FfmpegRenderServices, FfmpegTimelineRenderer, render_profile_for
from autovideo.format import FormatProfile, get_default_format_profile, resolve_format_profile, story as story_planning
from autovideo.storage import ArtifactStore, FilesystemQueue

# ----------------------------------------------------------------------------
# CONFIG  — tweak these freely
# ----------------------------------------------------------------------------
DEFAULT_NICHE        = DEFAULTS.channel.default_niche
VOICE                = os.environ.get("EDGE_TTS_VOICE", DEFAULTS.providers.edge_tts_voice)   # try en-US-AriaNeural, en-GB-RyanNeural, etc.
GEMINI_MODEL         = DEFAULTS.providers.gemini_models[0]
# Format-shaped configuration is owned by the FormatProfile abstraction.
# The module-level constants below remain for backward compatibility with
# any external code that imports them from this module; their values are
# derived from the active profile so there is a single source of truth.
# The active profile is selected via AUTO_VIDEO_FORMAT (default shorts_vertical).
_FORMAT_PROFILE: FormatProfile = resolve_format_profile()

TARGET_DURATION      = _FORMAT_PROFILE.target_duration_sec                     # story-driven hint; None means the story decides (use --duration to hint)
AVG_SEGMENT_DURATION = DEFAULTS.render.avg_segment_duration_sec                    # estimated seconds per narration/story beat
SHORTS_SCENE_TARGET_DURATION = _FORMAT_PROFILE.scene_target_duration_sec
SHORTS_PREFERRED_NARRATION_TEMPO = _FORMAT_PROFILE.preferred_narration_tempo
SHORTS_TRANSITION_DURATION = _FORMAT_PROFILE.transition_duration_sec
SHORTS_MIN_DURATION  = _FORMAT_PROFILE.min_duration_sec                     # soft quality indicator only - never rejects or pads
SHORTS_MAX_DURATION  = _FORMAT_PROFILE.max_duration_sec                     # the ONLY hard ceiling; every trim/retime/validation reads this
WIDTH, HEIGHT        = DEFAULTS.render.width, DEFAULTS.render.height             # vertical (Reels / Shorts / FB)
FPS                  = DEFAULTS.render.fps
OUT_DIR              = SCRIPT_DIR / "output"
PENDING_DIR          = SCRIPT_DIR / "videos" / "pending"   # review queue input
PERSISTENT_USED_PATH = SCRIPT_DIR / "state" / "used_videos.json"  # cross-run clip dedup
LEGACY_PERSISTENT_USED_PATH = SCRIPT_DIR / "used_videos.json"
INPUT_DIR            = SCRIPT_DIR / "input_clips"   # drop your own .mp4/.mov clips here
MUSIC_DIR            = SCRIPT_DIR / "music"          # drop royalty-free .mp3/.wav/.m4a tracks here
VIDEO_EXTENSIONS     = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS     = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FFMPEG_SAFE_IMAGE_FORMATS = {"JPEG", "PNG", "BMP"}
AUDIO_EXTENSIONS     = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MEDIA_EXTENSIONS     = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
DEFAULT_MUSIC_VOLUME = DEFAULTS.render.default_music_volume                    # background bed under narration
_GEMINI_CLIENT = None
_MEDIA_SELECTION_DIAGNOSTICS = {}
_BROAD_FALLBACK_SCENES = 0
_BROAD_FALLBACK_MAX_SCENES = max(1, int(os.environ.get("AUTO_VIDEO_MAX_BROAD_FALLBACK_SCENES", "1").strip() or "1"))
_YT_CLIP_SCENES_USED = 0


def _yt_clip_max_scenes() -> int:
    """Per-documentary yt_clip scene cap (lazy so the local .env loader has run)."""
    return max(0, int(os.environ.get("AUTO_VIDEO_YT_CLIP_MAX_SCENES", "6").strip() or "6"))


def _stock_provider_order() -> tuple[str, ...]:
    """Return the user-configured provider priority order if any (read lazily so the
    local .env loader has already run)."""
    raw = os.environ.get("AUTO_VIDEO_STOCK_PROVIDER_ORDER", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _yt_clip_budget_available() -> bool:
    """Return whether the per-documentary yt_clip scene budget is not exhausted."""
    return _yt_clip_max_scenes() <= 0 or _YT_CLIP_SCENES_USED < _yt_clip_max_scenes()
_MEDIA_PLANNING_DIAGNOSTICS = {}
_ADAPTIVE_SEARCH_DIAGNOSTICS = {}
_AUDIO_MIX_DECISIONS: list[ClipAudioDecision] = []
# Story-planning metrics (filled by generate_script, enriched at voice/gen
# and report time) -> written to output/story_report.json.
_STORY_REPORT: dict = {}
_WIKIMEDIA_SEARCH_CACHE = {}
_WIKIMEDIA_LAST_REQUEST_AT = 0.0


def _write_story_report() -> Path:
    """Persist the story-planning analytics payload to output/story_report.json."""
    report = {
        "semantic_trim_applied": bool(_STORY_REPORT.get("semantic_trim_applied", False)),
        "narration_overflow": bool(_STORY_REPORT.get("narration_overflow", False)),
        "renderer_tail_trim": bool(_STORY_REPORT.get("renderer_tail_trim", False)),
    }
    report.update({key: value for key, value in _STORY_REPORT.items() if key not in report})
    try:
        path = OUT_DIR / "story_report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError:
        return OUT_DIR / "story_report.json"


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
            key = k.strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key) and key not in os.environ:
                os.environ[key] = v.strip()

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "").strip()
PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")
SPEECHIFY_API_KEY = os.environ.get("SPEECHIFY_API_KEY")
SPEECHIFY_VOICE_ID = os.environ.get("SPEECHIFY_VOICE_ID", "george")
JAMENDO_CLIENT_ID  = os.environ.get("JAMENDO_CLIENT_ID")
PIXABAY_API_KEY    = os.environ.get("PIXABAY_API_KEY")
SAMBANOVA_API_KEY  = os.environ.get("SAMBANOVA_API_KEY")
MIXKIT_API_URL     = os.environ.get("MIXKIT_API_URL", "").strip()
COVERR_API_URL     = os.environ.get("COVERR_API_URL", "").strip()
COVERR_API_KEY     = os.environ.get("COVERR_API_KEY", "").strip()
COVERR_APP_ID      = os.environ.get("COVERR_APP_ID", "").strip()
VIDEVO_API_KEY     = os.environ.get("VIDEVO_API_KEY", "").strip()
VIDEVO_API_URL     = os.environ.get("VIDEVO_API_URL", "").strip()
VECTEEZY_API_URL = os.environ.get("VECTEEZY_API_URL", "https://api.vecteezy.com").strip().rstrip("/")
VECTEEZY_API_KEY = os.environ.get("VECTEEZY_API_KEY", "").strip()
VECTEEZY_ACCOUNT_ID = os.environ.get("VECTEEZY_ACCOUNT_ID", "").strip()
ARCHIVE_PROVIDERS_ENABLED = os.environ.get("ENABLE_ARCHIVE_PROVIDERS", "1").lower() not in {"0", "false", "no"}
NOAA_API_URL       = os.environ.get("NOAA_API_URL", "").strip()
NOAA_USER_AGENT    = os.environ.get("NOAA_USER_AGENT", "auto-short/1.0 educational video generator").strip()
WIKIMEDIA_CONTACT  = os.environ.get("WIKIMEDIA_CONTACT", os.environ.get("CONTACT_EMAIL", "")).strip()
WIKIMEDIA_USER_AGENT = os.environ.get("WIKIMEDIA_USER_AGENT", "").strip()
ESA_API_URL        = os.environ.get("ESA_API_URL", "").strip()
USGS_API_URL       = os.environ.get("USGS_API_URL", "").strip()
SMITHSONIAN_API_URL = os.environ.get("SMITHSONIAN_API_URL", "").strip()
SMITHSONIAN_API_KEY = os.environ.get("SMITHSONIAN_API_KEY", "").strip()
INTERNET_ARCHIVE_ENABLED = os.environ.get("INTERNET_ARCHIVE_ENABLED", "0").lower() not in {"0", "false", "no", ""}
NPS_API_URL        = os.environ.get("NPS_API_URL", "").strip()
USFWS_API_URL      = os.environ.get("USFWS_API_URL", "").strip()
LOC_API_URL        = os.environ.get("LOC_API_URL", "https://www.loc.gov/photos/").strip()
EUROPEANA_API_URL  = os.environ.get("EUROPEANA_API_URL", "").strip()
EUROPEANA_API_KEY  = os.environ.get("EUROPEANA_API_KEY", "").strip()
FLICKR_COMMONS_API_URL = os.environ.get("FLICKR_COMMONS_API_URL", "").strip()
FLICKR_API_KEY     = os.environ.get("FLICKR_API_KEY", "").strip()
POLLINATIONS_ENABLED = _env_flag("POLLINATIONS_ENABLED", default="0")

_PROVIDER_RUN_FAILURES: dict[str, str] = {}
POLLINATIONS_IMAGE_URL = os.environ.get("POLLINATIONS_IMAGE_URL", "https://image.pollinations.ai/prompt").strip().rstrip("/")
POLLINATIONS_MODEL = os.environ.get("POLLINATIONS_MODEL", "").strip()
ENABLE_WIKIMEDIA_COMMONS = os.environ.get("ENABLE_WIKIMEDIA_COMMONS", "1" if ARCHIVE_PROVIDERS_ENABLED else "0").lower() not in {"0", "false", "no"}
AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES = int(os.environ.get("AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES", "5") or "5")
AUTO_VIDEO_ENABLE_LANDSCAPE_EXPANSION = _env_flag("AUTO_VIDEO_ENABLE_LANDSCAPE_EXPANSION", default="true")
AUTO_VIDEO_PROVIDER_EXPANSION_CONFIDENCE = float(os.environ.get("AUTO_VIDEO_PROVIDER_EXPANSION_CONFIDENCE", "0.75") or "0.75")
ENABLE_AI_VISUAL_QA = _env_flag("ENABLE_AI_VISUAL_QA", "AUTO_VIDEO_VISUAL_QA_ENABLED", default="0")
AI_VISUAL_QA_PROVIDER = os.environ.get("AI_VISUAL_QA_PROVIDER", "gemini").strip().lower() or "gemini"
AI_VISUAL_QA_MIN_METADATA_CONFIDENCE = float(os.environ.get("AI_VISUAL_QA_MIN_METADATA_CONFIDENCE", "0.90") or "0.90")
AI_VISUAL_QA_MAX_CANDIDATES = int(os.environ.get("AI_VISUAL_QA_MAX_CANDIDATES", "3") or "3")
AUTO_VIDEO_MAX_EXPLAINER_FALLBACK_RATIO = float(os.environ.get("AUTO_VIDEO_MAX_EXPLAINER_FALLBACK_RATIO", "0.50") or "0.50")
CRITICAL_ASSET_PROVIDERS = ("pexels", "pixabay", "wikimedia", "europeana")
CRITICAL_ASSET_TECHNICAL_FAILURES = {
    ProviderProbeStatus.UNCONFIGURED,
    ProviderProbeStatus.AUTH_ERROR,
    ProviderProbeStatus.RATE_LIMITED,
    ProviderProbeStatus.TIMEOUT,
    ProviderProbeStatus.INVALID_MEDIA,
    ProviderProbeStatus.PROVIDER_ERROR,
}
ACADEMIC_TITLE_SUFFIX_RE = re.compile(
    r"\s*(?:\||-|:)\s*(?:earth science|ocean science|education|science|biology|"
    r"physics|chemistry|astronomy|geography|history|technology|engineering|nature|"
    r"wildlife|animals|ocean|space|weather|climate|environment|psychology|facts)\s*$",
    re.IGNORECASE,
)
DECLARATIVE_TITLE_START_RE = re.compile(
    r"^\s*(?:why|how|what|does|did|can|could|is|are|will|when|where|which)\b",
    re.IGNORECASE,
)
TITLE_QUESTION_RE = re.compile(r"\?")
TITLE_UNSUPPORTED_ABSOLUTE_RE = re.compile(
    r"\b(?:never|always|impossible|no one|nobody|nothing|everyone|"
    r"completely|entirely|perfectly|ultimate|only)\b",
    re.IGNORECASE,
)
# Channel name used in end card and SEO metadata
APP_SETTINGS       = Settings.from_project_root(SCRIPT_DIR)
APP_CONFIG         = AppConfig.from_settings(APP_SETTINGS)
CHANNEL_NAME       = APP_CONFIG.channel_name  # overridden by auto_short_biasfiles.py
# Music tunables come from the validated configuration layer (env-overridable).
DEFAULT_MUSIC_VOLUME = APP_CONFIG.music.volume
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
    q = re.sub(r"[^a-z0-9]+", " ", str(query or "").lower()).strip()
    padded = f" {q} "
    matched = {
        keyword
        for keyword in NASA_KEYWORDS
        if f" {re.sub(r'[^a-z0-9]+', ' ', keyword).strip()} " in padded
    }
    if not matched:
        return False
    if matched == {"space"}:
        return " outer space " in padded or " deep space " in padded
    return True



# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def die(msg):
    print(f"\n[X] {msg}\n")
    sys.exit(1)


def run_ff(args, cwd=None, timeout=120):
    """Run an ffmpeg/ffprobe command, raising with readable output on failure."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(args)}\n"
            "ffmpeg did not finish within the bounded operation timeout; "
            "the input may be corrupt, incompatible, or too slow to process."
        )
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


def automatic_duration_for_topic(topic, card=None):
    """Return an *advisory* story-length hint in seconds.

    The story itself determines the finished length; this value is used
    only for logging/analytics and is never fed to writers as a clamp.
    When a topic card carries a recommendation it is honored; otherwise
    the advisory hint is the platform ceiling from the active profile.
    """

    if isinstance(card, TopicCard) and card.recommended_duration_sec:
        return int(card.recommended_duration_sec), f"topic card {card.id} advisory"
    return int(SHORTS_MAX_DURATION), "platform ceiling advisory"


def resolve_target_duration(topic, explicit_duration=None, card=None):
    """Resolve an explicit CLI --duration hint, or None (story-driven).

    ``--duration`` remains a user override at the call site only: it is a
    soft hint for planning/logging, never a clamp on the finished story.
    """

    if explicit_duration is not None:
        return int(explicit_duration), "explicit --duration override"
    return None, "story-driven length (platform ceiling governs)"


def script_quality_notes(data, critical_asset_plan=None, profile: FormatProfile = _FORMAT_PROFILE):
    """Return (fatal_notes, soft_notes).

    Fatal = genuinely broken output (missing hook/conclusion/beats, empty
    or unspeakable segments, missing b-roll, title violations, critical
    asset misalignment). Duration is NEVER used as a quality proxy and a
    short-but-complete story is never rejected.

    Splitting fatal/soft matters because the Groq fallback (used when
    Gemini 503s) consistently writes longer narrations than asked.
    """
    fatal_notes = []
    soft_notes = []

    title = str(data.get("title") or "").strip()
    if ACADEMIC_TITLE_SUFFIX_RE.search(title):
        fatal_notes.append("title has an academic title suffix")
    fatal_notes.extend(_title_style_notes(title))

    struct_fatal, struct_soft = story_planning.validate_beat_structure(data, profile)
    fatal_notes.extend(struct_fatal)
    soft_notes.extend(struct_soft)

    fatal_notes.extend(_critical_script_alignment_notes(data, critical_asset_plan))

    return fatal_notes, soft_notes


def _title_style_notes(title: str) -> list[str]:
    """Require curious, declarative titles instead of question-led explainers."""

    if not title:
        return []
    notes = []
    if DECLARATIVE_TITLE_START_RE.search(title):
        notes.append("title is question-led; use a curious declarative statement")
    if TITLE_QUESTION_RE.search(title):
        notes.append("title contains a question mark")
    if TITLE_UNSUPPORTED_ABSOLUTE_RE.search(title):
        notes.append("title contains an unsupported absolute")
    return notes


def _critical_script_alignment_notes(data, critical_asset_plan):
    """Check that critical B-roll text still names the entity/action already proved."""

    if not isinstance(critical_asset_plan, dict) or critical_asset_plan.get("status") != "VERIFIED":
        return []
    segments = data.get("segments") or []
    notes = []
    stopwords = {
        "against", "during", "from", "into", "over", "through", "while", "with",
        "its", "the", "and", "that", "this", "their", "them", "then", "a", "an", "in", "on",
    }
    for role in critical_asset_plan.get("roles") or []:
        if not isinstance(role, dict) or role.get("status") != "VERIFIED":
            continue
        scene_index = int(role.get("scene_index", -1))
        if scene_index < 0 or scene_index >= len(segments):
            notes.append(f"critical scene {scene_index + 1} is missing from script")
            continue
        segment = segments[scene_index]
        visual_text = " ".join((
            str(segment.get("broll") or ""),
            " ".join(str(query) for query in segment.get("broll_queries") or ()),
        )).casefold()
        visual_tokens = set(re.findall(r"[a-z0-9]+", visual_text))
        entity_tokens = {
            token for token in re.findall(r"[a-z0-9]+", str(role.get("expected_entity") or "").casefold())
            if token not in stopwords
        }
        if entity_tokens and not entity_tokens <= visual_tokens:
            notes.append(
                f"critical segment {scene_index + 1} broll does not name locked entity "
                f"{role.get('expected_entity')!r}"
            )
        action_tokens = {
            token for token in re.findall(r"[a-z0-9]+", str(role.get("expected_action") or "").casefold())
            if len(token) >= 4 and token not in stopwords
        }
        if action_tokens and not (action_tokens & visual_tokens):
            notes.append(
                f"critical segment {scene_index + 1} broll does not reflect locked action "
                f"{role.get('expected_action')!r}"
            )
    return notes


def _confirmed_critical_visual_prompt(critical_asset_plan):
    if not isinstance(critical_asset_plan, dict) or critical_asset_plan.get("status") != "VERIFIED":
        return "No pre-verified critical visual locks are available."
    lines = []
    for role in critical_asset_plan.get("roles") or []:
        selected = role.get("selected") if isinstance(role, dict) else None
        if not isinstance(selected, dict):
            continue
        verification = selected.get("verification") or {}
        confirmed_entity = verification.get("verified_entity") or role.get("expected_entity")
        confirmed_action = verification.get("verified_action") or role.get("expected_action")
        lines.append(
            f"- Segment {int(role.get('scene_index', 0)) + 1} ({role.get('role')}): "
            f"confirmed {confirmed_entity}; visible action: {confirmed_action}; "
            f"search wording: {selected.get('query')}."
        )
    return "\n".join(lines) or "No pre-verified critical visual locks are available."


def _topic_metadata_classification_text(topic):
    """Include a card's canonical subject when its premise uses only a plural form."""

    card = find_topic_card(topic)
    return f"{topic} {card.subject}" if card is not None else topic


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
    if not any(
        str(key or "").strip()
        for key in (GEMINI_API_KEY, SAMBANOVA_API_KEY, GROQ_API_KEY)
    ):
        die("No script LLM is configured. Set GEMINI_API_KEY, SAMBANOVA_API_KEY, or GROQ_API_KEY.")



# ----------------------------------------------------------------------------
# Step 1: script + B-roll keywords from Gemini  (returns list of segments)
# ----------------------------------------------------------------------------
# Provider chain: every entry is tried in order; first success wins.
# Multiple Gemini models so a single throttled model doesn't kill the run.
# Multiple Groq models so a single deprecated model doesn't kill the run.
GEMINI_MODELS = list(DEFAULTS.providers.gemini_models)
GROQ_MODELS = list(DEFAULTS.providers.groq_models)
GROQ_VISION_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GROQ_VISION_MODELS",
        "qwen/qwen3.6-27b",
    ).split(",")
    if model.strip()
]
# SambaNova Cloud models (https://cloud.sambanova.ai). OpenAI-compatible API.
# Ordered by JSON-following reliability + capability. First success wins.
SAMBANOVA_MODELS = list(DEFAULTS.providers.sambanova_models)


def _try_gemini(prompt):
    global _GEMINI_QUOTA_DEAD

    if _GEMINI_QUOTA_DEAD:
        print("    [LLM Fallback] Gemini skipped because quota is exhausted; trying SambaNova/Groq.")
        return None, None
    if not GEMINI_API_KEY:
        print("    [LLM Fallback] GEMINI_API_KEY missing; trying next LLM provider.")
        return None, None
    client = _get_gemini_client()
    if client is None:
        print("    [LLM Fallback] Gemini client unavailable; trying next LLM provider.")
        return None, Exception("Failed to initialize Gemini client")
    last_err = None
    failures = []
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            print(f"    [Gemini] {model} - OK")
            return resp.text.strip(), None
        except Exception as e:
            last_err = e
            failures.append(str(e))
            print(f"    [Gemini] {model} failed: {str(e)[:120]}")
    if failures and all(
        "429" in failure or "RESOURCE_EXHAUSTED" in failure or "quota" in failure.lower()
        for failure in failures
    ):
        _GEMINI_QUOTA_DEAD = True
        print("    [Gemini quota] all configured Gemini models exhausted; disabling Gemini for rest of run.")
    print("    [LLM Fallback] Gemini could not produce script JSON; trying SambaNova/Groq.")
    return None, last_err


def _try_sambanova(prompt):
    """SambaNova Cloud - OpenAI-compatible API, very fast inference, free tier
    with rate limits. Sits between Gemini and Groq in the chain so a Gemini
    outage routes to SambaNova's multi-provider library first."""
    if not SAMBANOVA_API_KEY or "your_sambanova" in SAMBANOVA_API_KEY:
        print("    [LLM Fallback] SAMBANOVA_API_KEY missing; trying Groq.")
        return None, None
    try:
        from openai import OpenAI
    except Exception as e:
        return None, e
    print("    [LLM Fallback] Trying SambaNova models.")
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
        print("    [LLM Fallback] GROQ_API_KEY missing; no script LLM provider remains.")
        return None, None
    import requests
    print("    [LLM Fallback] Trying Groq models.")
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

def _llm_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    priorities = {name: idx for idx, name in enumerate(APP_CONFIG.provider_priority["llm"])}
    providers = {
        "gemini": CallableLLMProvider("gemini", _try_gemini, models=tuple(GEMINI_MODELS)),
        "sambanova": CallableLLMProvider("sambanova", _try_sambanova, models=tuple(SAMBANOVA_MODELS)),
        "groq": CallableLLMProvider("groq", _try_groq, models=tuple(GROQ_MODELS)),
    }
    for name, provider in providers.items():
        registry.register(
            "llm",
            name,
            provider,
            priority=priorities.get(name, 100),
            enabled=APP_CONFIG.feature_flags["allow_external_api_calls"],
            profiles=(APP_CONFIG.render_profile.name,),
            features=("script_json",),
        )
    return registry


def generate_script_raw(prompt):
    """Try configured LLM providers in fallback order.
    The first non-None response wins."""
    registry = _llm_provider_registry()
    try:
        result = registry.execute(
            "llm",
            lambda provider: provider.generate_text(prompt).value,
            profile=APP_CONFIG.render_profile.name,
            feature="script_json",
        )
        return result
    except Exception as e:
        raise RuntimeError(
            "All script providers failed across all model variants. "
            f"Errors: {str(e)[:240]}. "
            "Wait a few minutes for Gemini, or use --reuse-script with last_script.json."
        ) from e


def parse_script_json(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip()
    return json.loads(raw)


def finalize_story_length(niche, data, critical_asset_plan=None, profile: FormatProfile = _FORMAT_PROFILE):
    """Enforce only the platform ceiling while preserving story-driven length."""
    estimated = story_planning.estimate_story_duration(data, profile, conservative=True)
    segments = data.get("segments") or []
    budget = story_planning.voice_budget_seconds(profile, len(segments))
    if estimated <= budget:
        return estimated, False
    if len(segments) <= profile.min_story_beats or not _try_trim_story(
        niche, data, critical_asset_plan, profile, budget
    ):
        raise RuntimeError(
            f"Story narration is estimated {estimated:.1f}s, exceeding the voice budget "
            f"{budget:.1f}s for a {profile.max_duration_sec}s ceiling. "
            "The story must be regenerated shorter."
        )
    estimated = story_planning.estimate_story_duration(data, profile, conservative=True)
    budget = story_planning.voice_budget_seconds(profile, len(data.get("segments") or []))
    if estimated > budget:
        raise RuntimeError(
            f"Story narration is estimated {estimated:.1f}s after semantic trim, exceeding "
            f"the voice budget {budget:.1f}s for a {profile.max_duration_sec}s ceiling."
        )
    return estimated, True


def generate_script(niche, critical_asset_plan=None, profile: FormatProfile = _FORMAT_PROFILE):
    """Write the complete story for ``niche``.

    Two-pass by default (Story Planner -> Script Writer). With
    ``AUTO_VIDEO_STORY_PLANNER=0`` a single writer pass discovers the beats
    itself. The story decides its own length; the platform ceiling is the only
    limit. Returns the legacy script dict (additive beat metadata included).
    """
    critical_visuals = _confirmed_critical_visual_prompt(critical_asset_plan)
    if isinstance(critical_asset_plan, dict) and critical_asset_plan.get("status") == "VERIFIED":
        critical_lock_rules = (
            '- Segments 1 and 2 are visually locked to the CONFIRMED CRITICAL VISUALS above. '
            'Their narration, "broll", and "broll_queries" MUST name the locked entity and only '
            'describe the confirmed visible action. Do not invent an action, transformation, cause, '
            'or outcome that those frames do not support.'
        )
    else:
        critical_lock_rules = (
            "- No critical visual lock applies to this legacy topic; use the normal strict "
            "narration/B-roll alignment rules."
        )

    planner_plan = None
    if story_planning.planner_enabled():
        planner_plan = _plan_story(niche, critical_visuals, critical_lock_rules, profile)

    if planner_plan and planner_plan.get("beats"):
        prompt = story_planning.build_writer_prompt(
            niche, planner_plan["beats"], critical_visuals, critical_lock_rules, profile
        )
        writer_context = f"story for {niche!r}"
    else:
        prompt = story_planning.build_single_pass_prompt(
            niche, critical_visuals, critical_lock_rules, profile
        )
        writer_context = f"single-pass story for {niche!r}"

    data = _script_draft(prompt, critical_asset_plan, profile, context=writer_context)

    estimated, trim_applied = finalize_story_length(
        niche, data, critical_asset_plan, profile
    )

    # Story quality gate (soft by default; strict via AUTO_VIDEO_STORY_QUALITY_STRICT=1).
    quality_score = None
    if data.get("segments"):
        quality_score, data = _enforce_story_quality(
            niche, data, prompt, critical_asset_plan, profile
        )

    # A quality rewrite must not bypass the story-driven platform ceiling.
    estimated, quality_trim = finalize_story_length(
        niche, data, critical_asset_plan, profile
    )
    trim_applied = trim_applied or quality_trim

    if planner_plan and planner_plan.get("complexity"):
        data["story_complexity"] = planner_plan["complexity"]
    elif len(data.get("segments") or []) >= 10:
        data["story_complexity"] = "complex"
    else:
        data["story_complexity"] = "simple"

    _STORY_REPORT.update({
        "topic": niche,
        "complexity": data.get("story_complexity") or ("complex" if len(data.get("segments") or []) >= 10 else "simple"),
        "beat_count": len(data.get("segments") or []),
        "role_distribution": story_planning.story_roles(data),
        "profile": profile.name,
        "platform_max_duration_sec": profile.max_duration_sec,
        "estimated_narration_sec": round(estimated, 2),
        "final_word_count": sum(count_words(seg.get("narration", "")) for seg in data.get("segments", [])),
        "story_quality_score": quality_score,
        "semantic_trim_applied": bool(trim_applied),
    })

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
        while len(queries) < 4:
            queries.append(broll or niche)
        seg["broll_queries"] = queries[:4]
    data["niche"] = niche
    topic_metadata = build_topic_metadata(
        video_topic=_topic_metadata_classification_text(niche),
        title=data.get("title", niche),
        description=data.get("description", ""),
        instagram_caption=data.get("instagram_caption", ""),
        segments=data.get("segments", []),
        existing_hashtags=data.get("hashtags") or (),
    )
    data["title"] = topic_metadata.title
    data["description"] = topic_metadata.description
    data["instagram_caption"] = topic_metadata.instagram_caption
    data["hashtags"] = list(topic_metadata.hashtags)
    data["category_id"] = topic_metadata.category_id
    data = Script.from_legacy_dict(data, niche=niche).to_legacy_dict()
    segs = data["segments"]

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


def _plan_story(niche, critical_visuals, critical_lock_rules, profile):
    """Story Planner pass: produce a structured beat outline (or None)."""
    try:
        raw = generate_script_raw(story_planning.build_planner_prompt(niche, critical_visuals))
        plan = parse_script_json(raw)
    except Exception as e:
        print(f"    [Story Planner] failed ({e}); writing single-pass script.")
        return None
    if not isinstance(plan, dict):
        print("    [Story Planner] returned malformed output; writing single-pass script.")
        return None
    beats = [
        beat for beat in plan.get("beats") or []
        if isinstance(beat, dict) and str(beat.get("role") or "").strip()
    ]
    if not beats:
        print("    [Story Planner] produced no usable beats; writing single-pass script.")
        return None
    print(f"    [Story Planner] planned {len(beats)} beats (complexity {plan.get('complexity', '?')}).")
    plan["beats"] = beats
    return plan


def _script_draft(prompt, critical_asset_plan, profile, context="script"):
    """Run the writer prompt, QA it, and repair only when fatally broken.

    Preserves the legacy wisdom: a first draft with only soft notes is
    accepted untouched (a rewrite usually makes it worse), a repair is
    attempted only on fatal issues, and the better of the two drafts wins.
    """
    raw = generate_script_raw(prompt)
    first_draft = parse_script_json(raw)
    fatal, soft = script_quality_notes(first_draft, critical_asset_plan, profile)
    if not fatal:
        data = first_draft
        if soft:
            print(f"    [Script QA] {context}: first draft acceptable with soft notes: {'; '.join(soft[:3])}")
    else:
        print(f"    [Script QA] {context}: first draft has FATAL issues; asking for a rewrite: {'; '.join(fatal[:2])}")
        repair_prompt = f"""{prompt}

The previous JSON failed these critical checks:
{json.dumps(fatal, indent=2)}

Rewrite the whole JSON from scratch. Keep the same topic, but satisfy every rule.
Previous JSON:
{json.dumps(first_draft, ensure_ascii=False)}
"""
        try:
            repaired = parse_script_json(generate_script_raw(repair_prompt))
            fatal2, soft2 = script_quality_notes(repaired, critical_asset_plan, profile)
        except Exception as e:
            print(f"    [Script QA] {context}: repair attempt errored ({e}); checking if first draft is salvageable...")
            fatal2 = ["repair attempt errored"]
            soft2 = []
            repaired = None

        first_fatal_count = len(fatal)
        repair_fatal_count = len(fatal2) if repaired else 999
        if repair_fatal_count >= first_fatal_count and repaired is not None:
            # Repair is no better. Fall back to the first draft if its fatal
            # issues are tolerable (no empty segments, just slight issues).
            print(f"    [Script QA] {context}: repair was no better; using first draft.")
            data = first_draft
            fatal, _soft = script_quality_notes(data, critical_asset_plan, profile)
            if fatal:
                unsalvageable = [note for note in fatal if _is_unsalvageable_note(note)]
                if unsalvageable:
                    raise RuntimeError("Script is unsalvageable: " + "; ".join(unsalvageable[:4]))
                print(f"    [Script QA] {context}: tolerating soft fatal: {'; '.join(fatal[:2])}")
        else:
            data = repaired
            if fatal2:
                raise RuntimeError("Generated script is still malformed: " + "; ".join(fatal2[:6]))
            if soft2:
                print(f"    [Script QA] {context}: accepting repaired draft with soft notes: {'; '.join(soft2[:3])}")
    return data


def _is_unsalvageable_note(note):
    """A fatal QA note that can never be repaired by another LLM pass."""
    markers = (
        "no narration", "no segments", "has no narration", "missing broll",
        "academic title suffix", "title is question-led", "title contains a question mark",
        "title contains an unsupported absolute", "critical segment", "critical scene",
        "missing a hook", "missing a conclusion", "below the",
    )
    return any(marker in note for marker in markers)


def _try_trim_story(niche, data, critical_asset_plan, profile, budget):
    """Ceiling trim: merge/cut low-priority beats, never cut equally.

    Preserves hook, climax, resolution, conclusion CTA, and any beat
    flagged critical_asset_dependency or can_remove=False. Retries the
    semantic merge up to three times, then enforces the budget with a
    deterministic drop of the least important supporting beats so the
    platform ceiling always holds even when the LLM trim is too shallow.
    """
    segments = data.get("segments") or []
    before = len(segments)
    feedback = ""
    llm_trimmed = False
    for attempt in range(1, 4):
        prompt = story_planning.build_trim_prompt(niche, data, profile, budget) + feedback
        try:
            trimmed = _script_draft(prompt, critical_asset_plan, profile, context=f"ceiling trim (attempt {attempt})")
        except RuntimeError as e:
            print(f"    [Ceiling trim] attempt {attempt} failed ({e}); retrying.")
            feedback = (
                "\n\nYour previous attempt was REJECTED. Fix ALL of these issues in the next "
                f"draft: {e}\n"
            )
            continue
        trimmed_segs = trimmed.get("segments") or []
        if not trimmed_segs or len(trimmed_segs) >= before:
            if attempt >= 3:
                print("    [Ceiling trim] produced no shorter story; trying deterministic trim.")
                break
            print(f"    [Ceiling trim] attempt {attempt} did not shorten the story; retrying.")
            feedback = (
                "\n\nYour previous attempt did NOT reduce the number of segments. Return "
                "FEWER segments (merge or remove supporting beats) while keeping "
                "hook/climax/resolution/conclusion_cta.\n"
            )
            continue
        for key in ("title", "description", "instagram_caption", "music_mood", "hashtags"):
            if not trimmed.get(key):
                trimmed[key] = data.get(key)
        data.clear()
        data.update(trimmed)
        llm_trimmed = True
        print(f"    [Ceiling trim] LLM trimmed {before} -> {len(trimmed_segs)} segments.")
        break

    # Deterministic enforcement: keep dropping the least important removable
    # beat until the conservative estimate fits the budget or we hit the floor.
    segments = data.get("segments") or []
    floor = profile.min_story_beats
    dropped = 0
    while len(segments) > floor:
        estimated = story_planning.estimate_story_duration(data, profile, conservative=True)
        if estimated <= budget:
            break
        candidates = story_planning.merge_suggestions(segments)
        removable = [
            c for c in candidates
            if c["can_remove"]
            and c["role"] not in story_planning.PROTECTED_ROLES
            and not str(segments[c["index"]].get("critical_asset_dependency") or "").strip().casefold() in {"true", "1"}
        ]
        if not removable:
            print("    [Ceiling trim] no removable supporting beat left; keeping the full story.")
            break
        drop_index = removable[0]["index"]
        dropped_seg = segments[drop_index]
        segments.pop(drop_index)
        dropped += 1
        print(
            f"    [Ceiling trim] deterministic drop: {dropped_seg.get('beat_role')} "
            f"(importance {dropped_seg.get('beat_importance', '?')}/10)."
        )
        data["segments"] = segments
    if dropped:
        print(f"    [Ceiling trim] deterministic trim removed {dropped} beat(s) to fit the ceiling.")
    return bool(llm_trimmed or dropped)


def _score_story(niche, data):
    """LLM story-quality review; returns None when the provider fails."""
    try:
        raw = generate_script_raw(story_planning.build_quality_prompt(niche, data))
    except Exception as e:
        print(f"    [Story quality] evaluation failed ({e}); skipping gate.")
        return None
    scores = story_planning.parse_quality_scores(raw)
    if not scores:
        return None
    return {
        "scores": scores,
        "aggregate": story_planning.aggregate_quality_score(scores),
        "broken": story_planning.is_structurally_broken(scores),
    }


def _enforce_story_quality(niche, data, writer_prompt, critical_asset_plan, profile):
    """Story quality gate. Soft by default; strict via env.

    Always hard-fails on objectively broken stories (missing hook or ending
    CTA). Below the threshold it requests ONE revision and, if still below,
    proceeds with a warning (soft) or raises (AUTO_VIDEO_STORY_QUALITY_STRICT=1).
    """
    score = _score_story(niche, data)
    if score is None or score["aggregate"] is None:
        return None, data
    aggregate = score["aggregate"]
    if score["broken"]:
        raise RuntimeError(
            "Story is structurally broken (missing hook or ending CTA). "
            + str(score["scores"].get("summary") or "")[:160]
        )
    print(f"    [Story quality] score {aggregate}/10.")
    if aggregate >= story_planning.min_story_score():
        return aggregate, data

    critique = str(score["scores"].get("summary") or "raise the overall story quality")
    revision_prompt = f"""{writer_prompt}

A reviewer scored the previous draft {aggregate}/10 and requested a rewrite.
Their note: {critique}

Rewrite the complete story satisfying every rule. Keep the same beats where
possible but fix the flagged weaknesses.
Previous JSON:
{json.dumps(data, ensure_ascii=False)}
"""
    try:
        revised = _script_draft(revision_prompt, critical_asset_plan, profile, context="story quality revision")
    except RuntimeError as e:
        print(f"    [Story quality] revision failed ({e}); keeping original draft.")
        revised = data
    revised_score = _score_story(niche, revised)
    revised_aggregate = revised_score["aggregate"] if revised_score else None
    if revised_score and revised_score["broken"]:
        raise RuntimeError("Story is structurally broken after revision.")
    if revised_aggregate is not None and revised_aggregate >= story_planning.min_story_score():
        return revised_aggregate, revised
    if story_planning.quality_gate_soft():
        print(
            f"    [Story quality] still {max(aggregate, revised_aggregate or 0)}/10 "
            f"below {story_planning.min_story_score()}; soft gate proceeding."
        )
        return max(aggregate, revised_aggregate or 0), revised
    raise RuntimeError(
        f"Story quality {max(aggregate, revised_aggregate or 0)}/10 below "
        f"threshold {story_planning.min_story_score()} (AUTO_VIDEO_STORY_QUALITY_STRICT=1)."
    )

# ----------------------------------------------------------------------------
# Step 2: voiceover per segment (Speechify with Edge-TTS fallback)
# ----------------------------------------------------------------------------
async def _tts(text, out_path, voice_id=None):
    import edge_tts
    await edge_tts.Communicate(
        text,
        voice_id or VOICE,
        rate=APP_CONFIG.edge_tts_rate,
    ).save(str(out_path))


async def _tts_with_retry_async(text, out_path, tries=3, voice_id=None):
    import edge_tts
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            await _tts(text, out_path, voice_id=voice_id)
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


def _edge_tts_with_retry(text, out_path, tries=3, voice_id=None):
    asyncio.run(_tts_with_retry_async(text, out_path, tries=tries, voice_id=voice_id))


def _synthesize_edge_tts(text, out_path, voice_id):
    _edge_tts_with_retry(text, out_path, tries=APP_CONFIG.retry_attempts, voice_id=voice_id or APP_CONFIG.edge_tts_voice)


def _synthesize_speechify(text, out_path, voice_id):
    global _SPEECHIFY_DEAD
    try:
        make_voice_speechify(text, voice_id or SPEECHIFY_VOICE_ID, out_path)
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "402" in err_str or "Unauthorized" in err_str:
            _SPEECHIFY_DEAD = True
        raise


def _voice_provider_registry() -> ProviderRegistry:
    return build_voice_registry(APP_CONFIG)


def _make_voice_track(text, idx, registry=None, preferred_provider=""):
    out_path = OUT_DIR / f"voice_{idx}.mp3"
    registry = registry or _voice_provider_registry()
    provider_names = registry.provider_names("voice", profile=APP_CONFIG.render_profile.name)
    display_name = provider_names[0] if provider_names else "voice"
    label = "Edge-TTS" if display_name == "edge_tts" else display_name
    print(f"    [{label}] Generating voiceover for segment {idx+1}...")
    try:
        result = registry.execute(
            "voice",
            lambda provider: provider.synthesize(
                VoiceRequest(text=text, output_path=out_path, scene_id=str(idx))
            ),
            profile=APP_CONFIG.render_profile.name,
            feature="scene_narration",
            preferred_name=preferred_provider or None,
        )
    except Exception as e:
        raise RuntimeError(f"All voice providers failed: {e}") from e

    result_path = result.value
    result_metadata = dict(result.metadata or {})
    return VoiceTrack(
        audio_path=result_path,
        duration_sec=media_duration(result_path),
        provider=result.provider,
        voice_id=str(result_metadata.get("voice_id", "")),
        scene_id=str(idx),
        metadata=result_metadata,
    )


def make_voice(text, idx):
    track = _make_voice_track(text, idx)
    return track.audio_path, track.duration_sec


def atempo_chain(tempo):
    """Build a valid ffmpeg atempo chain. Values near 1.0 preserve voice quality.

    Tempo is clamped to a safe [0.5, 2.0] overall range to prevent extreme
    speed changes if called directly with an out-of-range value.
    """
    tempo = max(0.5, min(2.0, float(tempo)))
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


_ALLOWED_RETIME_RANGE = (0.5, 2.0)


def _clamp_tempo(tempo: float) -> float:
    """Clamp tempo to a safe overall range regardless of caller intent."""
    return max(_ALLOWED_RETIME_RANGE[0], min(_ALLOWED_RETIME_RANGE[1], float(tempo)))


def pad_voice(voice_path, idx, duration, padding):
    """Add a short silent tail so scene pacing can meet the format minimum."""

    out_path = OUT_DIR / f"voice_{idx}_padded.mp3"
    run_ff([
        "ffmpeg", "-y",
        "-i", str(voice_path),
        "-filter:a", f"apad=pad_dur={padding:.3f}",
        "-t", f"{duration + padding:.3f}",
        "-vn",
        str(out_path),
    ])
    return out_path, media_duration(out_path)


def fit_narration_to_ceiling(voice_items, profile: FormatProfile = _FORMAT_PROFILE):
    """Keep narration natural; only enforce the platform ceiling.

    There is no duration padding and no duration-window targeting. The
    finished length follows the actual narration. If the actual narration
    exceeds the ceiling, it is sped up slightly with a BOUNDED retime
    (optional, ``AUTO_VIDEO_ALLOW_NARRATION_RETIME``). If the retime cannot
    bring the narration within the ceiling plus a small encoding tolerance,
    the function raises so the run fails before rendering instead of
    producing a hard-tail-trimmed video.
    """
    if not voice_items:
        return voice_items
    total = sum(item["duration"] for item in voice_items)
    transition_allowance = max(0, len(voice_items) - 1) * profile.transition_duration_sec
    ceiling = float(profile.max_duration_sec)
    tolerance = float(os.environ.get("AUTO_VIDEO_DURATION_TOLERANCE_SEC", "1.0").strip() or "1.0")
    combined = total + transition_allowance
    if combined <= ceiling + tolerance:
        return voice_items

    allow_retime = os.environ.get("AUTO_VIDEO_ALLOW_NARRATION_RETIME", "1").strip() not in {"0", "false", "no"}
    tempo = min(profile.narration_max_retime_tempo, combined / ceiling)
    if allow_retime and tempo > 1.0:
        print(
            f"[i] Voiceover is {combined:.1f}s, over the {ceiling:.0f}s ceiling; "
            f"speeding the narration up slightly ({tempo:.2f}x) to fit."
        )
        adjusted = []
        for item in voice_items:
            path, duration = retime_voice(item["voice"], item["idx"], tempo)
            adjusted_item = {**item, "voice": path, "duration": duration}
            voice_track = item.get("voice_track")
            if isinstance(voice_track, VoiceTrack):
                adjusted_item["voice_track"] = voice_track.with_retimed_audio(path, duration)
            adjusted.append(adjusted_item)
        adjusted_total = sum(item["duration"] for item in adjusted) + transition_allowance
        if adjusted_total <= ceiling + tolerance:
            return adjusted
        raise RuntimeError(
            f"Narration is {adjusted_total:.1f}s after {tempo:.2f}x retime, exceeding the "
            f"{ceiling:.0f}s ceiling (tolerance {tolerance:.1f}s). The story must be trimmed "
            "before rendering, not hard-cut at the tail."
        )
    raise RuntimeError(
        f"Narration is {combined:.1f}s, exceeding the {ceiling:.0f}s ceiling "
        f"(tolerance {tolerance:.1f}s) and retiming is disabled or insufficient. "
        "The story must be trimmed before rendering, not hard-cut at the tail."
    )


def normalize_voice_timing(voice_items, target_duration=None, profile: FormatProfile = _FORMAT_PROFILE):
    """Backward-compatible alias for :func:`fit_narration_to_ceiling`.

    ``target_duration`` and ``profile.min_duration_sec`` are ignored: the
    story decides the length and only the ceiling is enforced.
    """
    return fit_narration_to_ceiling(voice_items, profile)


def make_all_voices(segments, profile: FormatProfile = _FORMAT_PROFILE):
    voice_items = []
    registry = _voice_provider_registry()
    preferred_provider = ""
    print("[2/5] Generating voiceovers...")
    if APP_CONFIG.elevenlabs_voice_ids:
        print(
            f"    [elevenlabs] Voice rotation slot "
            f"{APP_CONFIG.elevenlabs_voice_index + 1}/{len(APP_CONFIG.elevenlabs_voice_ids)} selected."
        )
    for idx, seg in enumerate(segments):
        voice_track = _make_voice_track(
            seg["narration"],
            idx,
            registry=registry,
            preferred_provider=preferred_provider,
        )
        preferred_provider = voice_track.provider
        voice_items.append(voice_track.to_legacy_item(index=idx, segment=seg))
    return fit_narration_to_ceiling(voice_items, profile)



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


def _valid_raster_image(filepath):
    """Return True when Pillow can decode the image as a raster asset."""

    try:
        with Image.open(filepath) as img:
            img.verify()
        with Image.open(filepath) as img:
            img.load()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def _safe_raster_image_info(filepath):
    """Return raster metadata when Pillow can fully decode the file."""

    try:
        with Image.open(filepath) as img:
            img.verify()
        with Image.open(filepath) as img:
            img.load()
            return {
                "format": str(img.format or "").upper(),
                "mode": str(img.mode or ""),
                "size": tuple(img.size),
            }
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _prepare_raster_image_for_ffmpeg(filepath, idx):
    """Validate and normalize a raster image to a format FFmpeg handles reliably."""

    info = _safe_raster_image_info(filepath)
    if not info:
        print(f"[!] Segment {idx}: '{Path(filepath).name}' is not a valid image.")
        return None
    suffix = Path(filepath).suffix.lower()
    image_format = str(info.get("format") or "").upper()
    expected_formats = {
        ".jpg": {"JPEG"},
        ".jpeg": {"JPEG"},
        ".png": {"PNG"},
        ".bmp": {"BMP"},
        ".webp": {"WEBP"},
    }.get(suffix, set())
    if image_format in FFMPEG_SAFE_IMAGE_FORMATS and (not expected_formats or image_format in expected_formats):
        return Path(filepath)

    normalized = OUT_DIR / f"normalized_img_{idx}.png"
    try:
        with Image.open(filepath) as img:
            img.load()
            if img.mode not in {"RGB", "RGBA"}:
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            if img.mode == "RGBA":
                background = Image.new("RGB", img.size, (0, 0, 0))
                background.paste(img, mask=img.getchannel("A"))
                img = background
            else:
                img = img.convert("RGB")
            normalized.parent.mkdir(parents=True, exist_ok=True)
            img.save(normalized, format="PNG", optimize=True)
        print(
            f"    [Image normalize] {Path(filepath).name} "
            f"({image_format or 'unknown'} as {suffix or 'no extension'}) -> {normalized.name}"
        )
        return normalized
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        print(f"[!] Segment {idx}: failed to normalize image '{Path(filepath).name}': {exc}")
        return None


def _fallback_color_clip(idx, duration, reason):
    """Create a simple dark clip when a media asset cannot be rendered safely."""

    print(f"[!] Segment {idx}: {reason} — using fallback color clip.")
    out_path = OUT_DIR / f"img_clip_{idx}.mp4"
    run_ff([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#1a1a2e:s={WIDTH}x{HEIGHT}:d={duration:.3f}:r={FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(out_path),
    ])
    return out_path


def get_local_media():
    """Return list of valid local media files (videos + images) from INPUT_DIR."""
    if not INPUT_DIR.exists():
        return []
    valid = []
    for f in INPUT_DIR.iterdir():
        ext = f.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            if _valid_raster_image(f):
                valid.append(f)
            else:
                print(f"[Warning] Skipping corrupt/unreadable image: {f.name}")
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


def _override_stem_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _find_manual_input_clip(idx, local_media, intent=None):
    """Return an operator-provided scene/entity clip from input_clips/ when present."""

    if not local_media:
        return None
    scene_keys = {
        _override_stem_key(f"scene_{idx+1:02d}"),
        _override_stem_key(f"scene_{idx+1}"),
        _override_stem_key(f"scene_{idx:02d}"),
        _override_stem_key(f"scene_{idx}"),
    }
    entity = ""
    if intent is not None:
        entity = (
            getattr(intent, "requested_entity", "")
            or getattr(intent, "primary_subject", "")
            or ""
        )
    entity_key = _override_stem_key(slugify(entity, 80)) if entity else ""
    for path in local_media:
        stem_key = _override_stem_key(Path(path).stem)
        if stem_key in scene_keys or (entity_key and stem_key == entity_key):
            return Path(path)
    return None


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
    return bool(GEMINI_API_KEY and GEMINI_IMAGE_MODEL)


def is_pollinations_image_available():
    return bool(POLLINATIONS_ENABLED and POLLINATIONS_IMAGE_URL)


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
            model=GEMINI_IMAGE_MODEL,
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


def generate_pollinations_image(prompt, idx):
    if not is_pollinations_image_available():
        return None

    from urllib.parse import quote, urlencode

    clean_prompt = " ".join(str(prompt or "").split())
    if not clean_prompt:
        return None
    full_prompt = (
        f"{clean_prompt}, vertical 9:16 educational documentary explainer image, "
        "clear subject, cinematic lighting, no text, no watermark"
    )
    params = {
        "width": str(WIDTH),
        "height": str(HEIGHT),
        "nologo": "true",
        "enhance": "true",
    }
    if POLLINATIONS_MODEL:
        params["model"] = POLLINATIONS_MODEL
    url = f"{POLLINATIONS_IMAGE_URL}/{quote(full_prompt)}?{urlencode(params)}"
    out_path = OUT_DIR / f"pollinations_img_{idx}.jpg"
    print(f"    [Pollinations Image] Generating for segment {idx+1} (prompt: '{clean_prompt}')...")
    if _download_to(url, out_path, timeout=90, max_bytes=16 * 1024 * 1024):
        return out_path
    return None


def _append_unique_query(queries, query):
    q = str(query or "").strip()
    if q and q.lower() not in {seen.lower() for seen in queries}:
        queries.append(q)


def _visual_environment_hint(text):
    low = text.lower()
    if any(w in low for w in ("arctic", "snow", "ice", "tundra", "winter", "polar")):
        return "snowy arctic"
    if any(w in low for w in ("ocean", "sea", "underwater", "reef", "marine")):
        return "underwater"
    if any(w in low for w in ("forest", "jungle", "tree", "rainforest")):
        return "forest"
    if any(w in low for w in ("desert", "sand", "dune")):
        return "desert"
    if any(w in low for w in ("mountain", "cliff", "rocky")):
        return "mountain"
    if any(w in low for w in ("space", "galaxy", "planet", "star", "nebula")):
        return "space"
    return ""


def broll_query_list(seg, fallback):
    queries = []
    raw_queries = seg.get("broll_queries") or []
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]

    base = str(seg.get("broll") or fallback or "").strip()
    narration = str(seg.get("narration") or "")
    environment = _visual_environment_hint(f"{base} {narration} {fallback}")
    visual_context = f"{base} {' '.join(str(q) for q in raw_queries)} {narration} {fallback}".lower()

    if "arctic landscape" in visual_context or "arctic wilderness" in visual_context:
        _append_unique_query(queries, "arctic tundra snow landscape")
        _append_unique_query(queries, "snowy arctic wilderness")

    if "arctic fox" in visual_context:
        if base and "arctic fox" in base.lower():
            _append_unique_query(queries, f"wild {base}")
        if any(w in visual_context for w in ("close", "fur", "hair")):
            _append_unique_query(queries, "wild arctic fox close up")
        if any(w in visual_context for w in ("hunt", "pounc", "prey", "eat", "leap")):
            _append_unique_query(queries, "arctic fox hunting in snow")
        if "den" in visual_context:
            _append_unique_query(queries, "arctic fox den snow")
        if any(w in visual_context for w in ("paw", "footpad", "feet")):
            _append_unique_query(queries, "arctic fox paws snow")
        _append_unique_query(queries, "wild arctic fox in snow")

    for query in [base, *raw_queries]:
        _append_unique_query(queries, query)

    if base:
        _append_unique_query(queries, f"{base} close up")
        if environment and environment not in base.lower():
            _append_unique_query(queries, f"{base} in {environment}")
        _append_unique_query(queries, f"{base} wide shot")

    if fallback:
        _append_unique_query(queries, fallback)
    return queries


def interactive_broll_review(segments, niche):
    """Show each segment's proposed b-roll queries.

    If output/broll_overrides.json exists, loads overrides from it.
    Otherwise, prints the review and writes a template file for the user to edit.

    Returns a dict of overrides keyed by segment index:
      - {"queries": [...]}  - use these search terms instead
      - {"clip_path": "..."} - use this local file directly
      - {"source_path": "...", "start_sec": 12.0} - slice this local source
        at a specific timestamp; add preserve_audio=true to retain source audio
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


def pexels_relevance_score(video, query, narration=""):
    source_text = f"{video.get('url', '')} {video.get('user', {}).get('name', '')}".lower().replace("-", " ")
    context = f"{query} {narration}".lower()
    score = 0.0
    if "arctic fox" in context:
        if "fox" in source_text:
            score += 2.0
        else:
            score -= 0.75
        if any(w in source_text for w in ("arctic", "snow", "winter", "polar")):
            score += 1.0
        if any(bad in source_text for bad in ("dog", "husky", "bird", "zoo", "cage", "enclosure", "human", "person")):
            score -= 5.0
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


def _validate_downloaded_media(path, expected_path=None):
    """Verify that the downloaded file's magic bytes are consistent with its extension.

    Raises RuntimeError if the file is clearly not a valid image/video (e.g. a DjVu
    document, HTML error page, or other non-media content saved with a media extension).
    """
    expected_path = Path(expected_path or path)
    ext = expected_path.suffix.lower().lstrip(".")
    try:
        with open(path, "rb") as f:
            header = f.read(512)
    except OSError:
        return  # Can't read — let downstream handle it

    if len(header) < 8:
        raise RuntimeError(f"downloaded file too small ({len(header)} bytes)")

    # Known non-media signatures that providers sometimes serve
    # DjVu files start with AT&TFORM
    if header[:8] == b"AT&TFORM":
        raise RuntimeError("downloaded file is DjVu, not a valid image/video")
    # HTML error pages
    leading = header.lstrip().lower()
    if leading.startswith((b"<!doctype", b"<html", b"<?xml", b"<svg", b"{", b"[")):
        raise RuntimeError("downloaded file is an HTML/XML/JSON error payload, not media")
    if leading.startswith((b"error", b"access denied", b"rate limit", b"too many requests")):
        raise RuntimeError("downloaded file is a text error payload, not media")

    # For image extensions, verify magic bytes
    if ext in ("jpg", "jpeg"):
        if header[:2] != b"\xff\xd8":
            raise RuntimeError(f"file has .{ext} extension but is not a JPEG (header: {header[:8].hex()})")
    elif ext == "png":
        if header[:4] != b"\x89PNG":
            raise RuntimeError(f"file has .png extension but is not a PNG (header: {header[:8].hex()})")
    elif ext == "webp":
        if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            raise RuntimeError(f"file has .webp extension but is not a WebP (header: {header[:8].hex()})")


def _retry_delay_seconds(headers, attempt, *, maximum=30.0):
    """Return bounded exponential backoff while respecting a valid Retry-After."""

    delay = min(float(2 ** max(0, attempt - 1)), maximum)
    retry_after = str((headers or {}).get("Retry-After", "") or "").strip()
    if not retry_after:
        return delay
    try:
        server_delay = max(0.0, float(retry_after))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
            server_delay = max(0.0, (retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            server_delay = 0.0
    return min(maximum, max(delay, server_delay))


def _normalize_downloaded_image(path, destination):
    """Validate a raster payload and transcode it to the destination's format."""

    requested = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".webp": "WEBP",
        ".bmp": "BMP",
    }.get(Path(destination).suffix.lower())
    if not requested:
        return

    normalized = Path(f"{path}.normalized")
    try:
        with Image.open(path) as image:
            detected = str(image.format or "").upper()
            image.load()
            if detected not in {"JPEG", "PNG", "WEBP", "BMP"}:
                raise RuntimeError(f"unsupported downloaded image format: {detected or 'unknown'}")
            if image.width <= 0 or image.height <= 0:
                raise RuntimeError("downloaded image has invalid dimensions")
            if detected == requested:
                return

            if requested in {"JPEG", "BMP"}:
                if image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    flattened = Image.new("RGB", rgba.size, (255, 255, 255))
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    image = flattened
                else:
                    image = image.convert("RGB")
            elif requested == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            elif requested == "PNG" and image.mode == "CMYK":
                image = image.convert("RGB")

            save_options = {"quality": 92} if requested in {"JPEG", "WEBP"} else {}
            image.save(normalized, format=requested, **save_options)
        with Image.open(normalized) as image:
            image.verify()
        normalized.replace(path)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise RuntimeError(f"invalid downloaded image: {exc}") from exc
    finally:
        try:
            normalized.unlink()
        except FileNotFoundError:
            pass


def _download_to(url, out_path, timeout=120, max_bytes=250 * 1024 * 1024):
    """Download, validate, and atomically publish media. Returns True on success."""

    import requests

    out_path = Path(out_path)
    temp_path = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.part")
    retryable_statuses = {429, 500, 502, 503, 504}
    max_attempts = 3
    started = time.monotonic()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, max_attempts + 1):
            elapsed = time.monotonic() - started
            remaining = float(timeout) - elapsed
            if remaining <= 0:
                raise RuntimeError(f"download exceeded {timeout}s time limit")
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

            with requests.get(
                url,
                stream=True,
                timeout=(min(10.0, remaining), min(30.0, remaining)),
                headers={"User-Agent": "auto-short/1.0 educational video generator"},
            ) as response:
                status = int(getattr(response, "status_code", 0) or 0)
                if status in retryable_statuses:
                    if attempt >= max_attempts:
                        raise RuntimeError(f"HTTP {status} after {max_attempts} attempts")
                    delay = _retry_delay_seconds(
                        getattr(response, "headers", {}),
                        attempt,
                        maximum=min(30.0, max(1.0, float(timeout) / 2.0)),
                    )
                    if time.monotonic() - started + delay >= timeout:
                        raise RuntimeError(f"HTTP {status}; retry would exceed {timeout}s time limit")
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                headers = getattr(response, "headers", {}) or {}
                content_type = str(headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
                if (
                    content_type.startswith("text/")
                    or content_type in {
                        "application/json",
                        "application/problem+json",
                        "application/xml",
                        "application/xhtml+xml",
                    }
                ):
                    raise RuntimeError(f"server returned non-media content type {content_type}")
                try:
                    content_length = int(headers.get("Content-Length", 0) or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > max_bytes:
                    raise RuntimeError(f"download exceeded {max_bytes // (1024 * 1024)} MB limit")

                total = 0
                with open(temp_path, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise RuntimeError(f"download exceeded {max_bytes // (1024 * 1024)} MB limit")
                        if time.monotonic() - started > timeout:
                            raise RuntimeError(f"download exceeded {timeout}s time limit")
                        output.write(chunk)

            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise RuntimeError("download produced an empty file")
            _normalize_downloaded_image(temp_path, out_path)
            _validate_downloaded_media(temp_path, out_path)
            temp_path.replace(out_path)
            return True
    except Exception as e:
        print(f"    [download] failed for {_safe_diagnostic(url)[:80]}...: {_safe_diagnostic(e)}")
        for path in (temp_path, Path(f"{temp_path}.normalized")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
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
        if any(w in low for w in ("fish", "shark", "whale", "dolphin", "octopus",
                                  "squid", "jellyfish", "turtle", "seal", "ray", "eel",
                                  "crab", "lobster", "shrimp", "plankton", "marine life")):
            qualifiers = ["underwater", "ocean", "animal"]
        else:
            qualifiers = ["ocean", "water", "motion"]
    elif any(w in low for w in ("space", "galaxy", "universe", "astronomy", "planet",
                                 "star", "nebula", "cosmos", "cosmic", "solar", "nasa",
                                 "aurora", "magnetic", "atmosphere")):
        qualifiers = ["space", "astronomy"]
    elif any(w in low for w in ("lightning", "thunder", "thunderstorm", "storm", "weather",
                                 "cloud", "rain", "monsoon", "cyclone", "hurricane")):
        qualifiers = ["storm", "sky"]
    elif any(w in low for w in ("dinosaur", "prehistoric", "fossil", "jurassic",
                                 "cretaceous", "triceratops", "raptor")):
        qualifiers = ["dinosaur", "prehistoric"]
    elif (
        any(w in low for w in ("arctic", "polar", "snow", "ice", "tundra", "winter"))
        and any(w in low for w in ("landscape", "wilderness", "mountain", "glacier", "scenery"))
    ):
        qualifiers = ["snow"]
    elif any(w in low for w in ("arctic", "polar", "snow", "ice", "tundra", "winter")):
        qualifiers = ["wildlife", "snow"]
    elif any(w in low for w in ("fox", "wolf", "bear", "lion", "tiger", "bird", "eagle",
                                 "animal", "wildlife", "predator", "prey")):
        qualifiers = ["wildlife"]
    q_low = q.lower()
    extra = " ".join(w for w in qualifiers if w not in q_low)
    return f"{q} {extra}".strip() if extra else q


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).lower() not in {"0", "false", "no"}


def _candidate_metadata_text(candidate):
    values = [
        candidate.provider,
        candidate.provider_id,
        candidate.title,
        candidate.description,
        candidate.url,
        candidate.download_url,
        str(candidate.local_path or ""),
    ]
    raw = candidate.raw_metadata if isinstance(candidate.raw_metadata, dict) else {}
    values.extend(str(value) for value in raw.values() if isinstance(value, (str, int)))
    return " ".join(" ".join(str(value or "").lower().split()) for value in values)


def _exact_subject_terms(intent):
    terms = []
    for value in (intent.primary_subject, *getattr(intent, "supporting_subjects", ())):
        normalized = " ".join(str(value or "").lower().split())
        if not normalized:
            continue
        if normalized in {"underwater", "ocean", "sea", "water", "reef", "coral", "animal", "wildlife"}:
            continue
        terms.append(normalized)
    return tuple(dict.fromkeys(terms))


def _exact_subject_candidate_count(candidates, intent):
    terms = _exact_subject_terms(intent)
    if not terms:
        return 0
    seen = set()
    for candidate in candidates:
        text = _candidate_metadata_text(candidate)
        if any(term in text for term in terms):
            seen.add(candidate.dedup_key)
    return len(seen)


def _candidate_has_exact_subject(candidate, intent):
    score = score_candidate(intent, candidate, evidence_engine=EvidenceVerificationEngine())
    fidelity = score.breakdown.get("_entity_fidelity_value")
    return fidelity in {EntityFidelity.EXACT_ENTITY.value, EntityFidelity.EXACT_ALIAS.value}


def _confidence_value(confidence):
    return {
        "high": 0.9,
        "medium": 0.6,
        "low": 0.3,
        "fallback": 0.2,
        "rejected": 0.0,
    }.get(str(confidence or "").lower(), 0.0)


def _evidence_engine():
    return EvidenceVerificationEngine(
        EvidenceVerificationConfig(
            enable_ai_visual_qa=ENABLE_AI_VISUAL_QA,
            ai_visual_qa_provider=AI_VISUAL_QA_PROVIDER,
            ai_visual_qa_min_metadata_confidence=AI_VISUAL_QA_MIN_METADATA_CONFIDENCE,
            ai_visual_qa_max_candidates=AI_VISUAL_QA_MAX_CANDIDATES,
        ),
        vision_verifier=_gemini_visual_qa_verifier if ENABLE_AI_VISUAL_QA else None,
    )


def _gemini_visual_qa_verifier(requested_entity, candidate):
    """Best-effort Gemini visual QA for already-local media assets.

    Remote candidates are verified by metadata before download. Vision QA is
    intentionally best-effort and never required for pipeline success.
    """

    local_path = Path(getattr(candidate, "local_path", "") or "")
    if not local_path.exists():
        return None
    try:
        samples = _representative_visual_samples(local_path, requested_entity)
        if not samples:
            return None
        prompt = (
            "Requested Entity:\n"
            f"{requested_entity}\n\n"
            "Question:\n"
            "Does this image/frame primarily depict the requested entity?\n\n"
            "Return compact JSON with: match, matched_entity, confidence, brief_reasoning."
        )
        raw, provider = _vision_completion(prompt, samples)
        data = _require_vision_json(raw, "match", "entity_match")
        confidence = float(data.get("confidence", 0.0) or 0.0)
        return VisionVerificationResult(
            match=bool(data.get("match")),
            matched_entity=str(data.get("matched_entity") or ""),
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=str(data.get("brief_reasoning") or data.get("reasoning") or "")[:240],
            provider=provider,
        )
    except Exception as exc:
        return VisionVerificationResult(match=False, provider="vision_fallback", error=str(exc)[:240])


def _extract_json_object(raw):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return {}


def _require_vision_json(raw, *result_keys):
    data = _extract_json_object(raw)
    if not isinstance(data, dict) or not data or not any(key in data for key in result_keys):
        raise RuntimeError("vision provider returned malformed or incomplete JSON")
    return data


def _generate_gemini_vision_content(client, contents):
    """Try configured Gemini vision-capable models in deterministic order."""

    models = []
    for model in (GEMINI_IMAGE_MODEL, *GEMINI_MODELS):
        cleaned = str(model or "").strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)
    if not models:
        raise RuntimeError("no Gemini vision model is configured")

    failures = []
    for model in models:
        try:
            return client.models.generate_content(model=model, contents=contents), model
        except Exception as exc:
            failures.append(f"{model}: {_safe_diagnostic(exc)}")
    raise RuntimeError("all Gemini vision models failed: " + " | ".join(failures))


def _looks_like_quota_error(error_text):
    text = str(error_text or "")
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


def _groq_vision_content(prompt, samples):
    """Return raw JSON-ish vision output from Groq's free-tier vision models."""

    if not GROQ_API_KEY or "your_groq" in GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured for vision fallback")
    if not GROQ_VISION_MODELS:
        raise RuntimeError("no Groq vision model is configured")

    import base64
    import requests

    content = [{"type": "text", "text": prompt}]
    for sample in samples:
        encoded = base64.b64encode(Path(sample).read_bytes()).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
        })

    failures = []
    for model in GROQ_VISION_MODELS:
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0,
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                },
                timeout=45,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            print(f"    [Groq Vision] {model} - OK")
            return str(raw or "").strip(), "groq"
        except Exception as exc:
            failures.append(f"{model}: {_safe_diagnostic(exc)}")
            print(f"    [Groq Vision] {model} failed: {str(exc)[:120]}")
    raise RuntimeError("all Groq vision models failed: " + " | ".join(failures))


def _vision_completion(prompt, samples):
    """Try Gemini vision first, then Groq vision as the free fallback."""

    global _GEMINI_QUOTA_DEAD

    if not _GEMINI_QUOTA_DEAD:
        client = _get_gemini_client()
        if client is not None:
            try:
                from google.genai import types

                parts = [types.Part.from_text(text=prompt)]
                for sample in samples:
                    parts.append(types.Part.from_bytes(
                        data=Path(sample).read_bytes(), mime_type="image/jpeg"
                    ))
                response, model = _generate_gemini_vision_content(
                    client,
                    [types.Content(role="user", parts=parts)],
                )
                raw = str(getattr(response, "text", "") or "").strip()
                _require_vision_json(raw, "match", "entity_match")
                print(f"    [Gemini Vision] {model} - OK")
                return raw, "gemini"
            except Exception as exc:
                diagnostic = _safe_diagnostic(exc)
                if _looks_like_quota_error(diagnostic):
                    _GEMINI_QUOTA_DEAD = True
                    print("    [Vision Fallback] Gemini vision quota exhausted; using Groq vision for rest of run.")
                else:
                    print(f"    [Vision Fallback] Gemini vision failed ({diagnostic}); trying Groq vision.")
        else:
            print("    [Vision Fallback] Gemini client unavailable; trying Groq vision.")
    else:
        print("    [Vision Fallback] Gemini skipped because quota is already exhausted; trying Groq vision.")

    return _groq_vision_content(prompt, samples)


def _representative_visual_samples(path, requested_entity, max_frames=3):
    """Return one image or up to three evenly spaced video frames for vision QA."""

    path = Path(path)
    if is_image(path):
        prepared = _prepare_raster_image_for_ffmpeg(path, f"vision_{slugify(requested_entity)}")
        return [prepared] if prepared else []
    try:
        duration = max(media_duration(path), 0.1)
    except Exception:
        duration = 1.0
    times = [duration / 2.0] if max_frames <= 1 else [duration * 0.25, duration * 0.5, duration * 0.75]
    samples = []
    for sample_idx, timestamp in enumerate(times[:max_frames]):
        out = OUT_DIR / f"vision_sample_{slugify(requested_entity)}_{sample_idx}.jpg"
        try:
            run_ff([
                "ffmpeg", "-y",
                "-ss", f"{timestamp:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-q:v", "3",
                str(out),
            ], timeout=30)
            if out.exists():
                samples.append(out)
        except Exception:
            continue
    return samples


def _record_post_download_visual_qa(idx, intent, media_path):
    """Attach best-effort visual QA diagnostics after a selected asset is local."""

    if not ENABLE_AI_VISUAL_QA:
        return
    requested_entity = (
        getattr(intent, "requested_entity", "")
        or getattr(intent, "primary_subject", "")
        or ""
    )
    if not requested_entity or not media_path:
        return
    selection_payload = _MEDIA_SELECTION_DIAGNOSTICS.setdefault(idx, {"selection": {}})
    selection = selection_payload.setdefault("selection", {})
    evidence = selection.setdefault("evidence_verification", {})
    if evidence.get("post_download_vision_checked"):
        return
    evidence.setdefault("requested_entity", requested_entity)
    evidence["post_download_vision_checked"] = True
    evidence["vision_requested"] = True
    evidence["vision_provider"] = AI_VISUAL_QA_PROVIDER
    evidence["local_media_path"] = str(media_path)
    result = _gemini_visual_qa_verifier(
        requested_entity,
        SimpleNamespace(local_path=Path(media_path)),
    )
    if result is None:
        evidence["vision_invoked"] = False
        evidence["vision_result"] = "unavailable"
        evidence.setdefault("fallback_reason", "vision verifier unavailable or no samples")
        return
    evidence["vision_invoked"] = True
    evidence["vision_result"] = "match" if result.match else "no_match"
    evidence["vision_confidence"] = result.confidence
    evidence["vision_reasoning"] = result.reasoning
    evidence["vision_error"] = result.error
    if result.matched_entity:
        evidence["selected_entity"] = result.matched_entity


def _verification_priority_for_intent(intent) -> VerificationPriority:
    importance = getattr(getattr(intent, "scene_importance", None), "value", None)
    importance = str(importance or getattr(intent, "scene_importance", "")).upper()
    if importance in {"HOOK", "MAIN_REVEAL"}:
        return VerificationPriority.CRITICAL
    if importance == "SUPPORTING":
        return VerificationPriority.HIGH
    if importance == "TRANSITION":
        return VerificationPriority.LOW
    return VerificationPriority.MEDIUM


def _replacement_queries(ctx: PipelineContext, idx: int, intent) -> list[str]:
    """Replacement search queries for a rejected scene, with segment fallback.

    Prefers the semantic query report, then the shot intent's own search
    queries, and finally the writer-authored ``broll_queries``/``broll`` of
    the segment so a rejected critical asset always has a concrete query to
    search again instead of silently aborting.
    """
    semantic_report = ctx.values.get("semantic_query_report")
    semantic_scene = (
        semantic_report.scene_for_index(idx)
        if isinstance(semantic_report, SemanticQueryReport) else None
    )
    if semantic_scene and semantic_scene.provider_queries:
        return list(semantic_scene.provider_queries)
    intent_queries = list(getattr(intent, "search_queries", ()) or ())
    if intent_queries:
        return intent_queries
    voice_by_index = {item["idx"]: item for item in ctx.values.get("voice_items", [])}
    item = voice_by_index.get(idx)
    segment = (item or {}).get("segment") or {}
    broll_queries = list(segment.get("broll_queries") or ())
    if broll_queries:
        return [q for q in broll_queries if str(q).strip()]
    broll = str(segment.get("broll") or "").strip()
    return [broll] if broll else []


def _verification_request_for_asset(idx, asset, intent) -> VerificationRequest:
    entity = (
        getattr(intent, "requested_entity", "")
        or getattr(getattr(intent, "scene_entity", None), "canonical_entity", "")
        or getattr(intent, "primary_subject", "")
        or ""
    )
    action_terms = tuple(
        str(term).strip()
        for term in (getattr(intent, "action_terms", ()) or ())
        if str(term).strip()
    )
    expected_action = action_terms[0] if action_terms else str(getattr(intent, "action", "") or "")
    visual_goal = getattr(getattr(intent, "visual_goal", None), "value", None)
    return VerificationRequest(
        scene_index=idx,
        media_path=Path(asset.local_path),
        expected_entity=str(entity),
        expected_action=expected_action,
        visual_goal=str(visual_goal or ""),
        priority=_verification_priority_for_intent(intent),
    )


def _gemini_verified_media_verifier(request, max_frames):
    """Verify downloaded frames only; metadata and search text are excluded."""

    try:
        samples = _representative_visual_samples(
            request.media_path,
            request.expected_entity or f"scene_{request.scene_index}",
            max_frames=max_frames,
        )
        if not samples:
            return DownloadedMediaEvidence(
                entity_match=False,
                sampled_frames=(),
                provider="vision_fallback",
                error="no representative frames could be sampled",
            )
        prompt = (
            "Evaluate only the supplied frames, not their filenames or any search query.\n\n"
            f"Expected entity: {request.expected_entity or 'not specified'}\n"
            f"Expected action: {request.expected_action or 'not required'}\n"
            f"Visual goal: {request.visual_goal or 'show'}\n\n"
            "Does the media primarily depict the expected entity, and when an action is "
            "required, does it visibly depict that action? Return compact JSON only: "
            "entity_match, entity_confidence, verified_entity, action_match, "
            "action_confidence, verified_action, reasoning. Confidence values must be 0 to 1."
        )
        raw, provider = _vision_completion(prompt, samples)
        data = _require_vision_json(raw, "entity_match", "match")
        def as_optional_bool(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "yes", "match"}:
                    return True
                if normalized in {"false", "no", "no_match"}:
                    return False
            return None

        entity_match = as_optional_bool(data.get("entity_match", data.get("match", False)))
        action_match = as_optional_bool(data.get("action_match"))
        return DownloadedMediaEvidence(
            entity_match=entity_match is True,
            entity_confidence=max(0.0, min(1.0, float(
                data.get("entity_confidence", data.get("confidence", 0.0)) or 0.0
            ))),
            action_match=action_match,
            action_confidence=max(0.0, min(1.0, float(
                data.get("action_confidence", 0.0) or 0.0
            ))),
            verified_entity=str(data.get("verified_entity") or data.get("matched_entity") or ""),
            verified_action=str(data.get("verified_action") or ""),
            reasoning=str(data.get("reasoning") or data.get("brief_reasoning") or "")[:300],
            sampled_frames=tuple(str(sample) for sample in samples),
            provider=provider,
        )
    except Exception as exc:
        return DownloadedMediaEvidence(
            entity_match=False,
            provider="vision_fallback",
            error=str(exc)[:300],
        )


def _gemini_rendered_visual_verifier(request: RenderedSceneRequest) -> RenderedVisualEvidence:
    """Verify one final-render frame without relying on file names or queries."""

    try:
        prompt = (
            "Evaluate only this rendered video frame. Do not infer anything from file names or search terms.\n\n"
            f"Expected entity: {request.expected_entity}\n"
            f"Visual goal: {request.visual_goal or 'show'}\n\n"
            "Does the visible frame clearly depict the expected entity? Return compact JSON only: "
            "match, confidence, matched_entity, reasoning. Confidence must be 0 to 1."
        )
        raw, provider = _vision_completion(prompt, [request.frame_path])
        data = _require_vision_json(raw, "match", "entity_match")
        match = data.get("match", data.get("entity_match", False))
        if isinstance(match, str):
            match = match.strip().lower() in {"true", "yes", "match"}
        return RenderedVisualEvidence(
            match=bool(match),
            confidence=max(0.0, min(1.0, float(data.get("confidence", data.get("entity_confidence", 0.0)) or 0.0))),
            matched_entity=str(data.get("matched_entity") or data.get("verified_entity") or ""),
            reasoning=str(data.get("reasoning") or data.get("brief_reasoning") or "")[:300],
            provider=provider,
        )
    except Exception as exc:
        return RenderedVisualEvidence(False, provider="vision_fallback", error=str(exc)[:300])


def _extract_rendered_scene_frame(video_path: Path, timestamp_sec: float, scene_index: int) -> Path:
    """Extract one final-output frame for the bounded rendered visual check."""

    output = OUT_DIR / "rendered_visual_qa" / f"scene_{scene_index:02d}.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ff([
            "ffmpeg", "-y", "-ss", f"{max(0.0, timestamp_sec):.3f}",
            "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(output),
        ], timeout=30)
    except Exception:
        return output
    return output


def _candidate_extension(candidate):
    if candidate.is_image:
        return ".jpg"
    return ".mp4"


def _broad_fallback_terms(keyword, narration="", fallback=""):
    """Return last-resort search terms without crossing into the wrong domain."""

    low = (keyword + " " + narration).lower()
    broad_terms = []
    if any(w in low for w in ("lightning", "thunder", "thunderstorm", "storm", "weather",
                              "cloud", "rain", "monsoon", "cyclone", "hurricane")):
        broad_terms += ["lightning storm sky", "thunderstorm clouds", "storm clouds lightning"]
    elif any(w in low for w in ("deep sea", "ocean", "sea", "marine", "underwater", "aquatic",
                                "coral", "fish", "shark", "whale", "dolphin", "octopus",
                                "squid", "jellyfish", "turtle", "seal", "ray", "eel",
                                "crab", "lobster", "shrimp", "plankton", "water", "river", "lake")):
        broad_terms += ["underwater nature", "sea life close up", "ocean reef"]
    elif any(w in low for w in ("space", "galaxy", "universe", "astronomy", "planet",
                                "star", "nebula", "cosmos", "cosmic", "solar", "nasa",
                                "orbit", "astronaut", "aurora", "northern lights",
                                "magnetic", "atmosphere", "particles")):
        broad_terms += ["outer space", "galaxy stars", "nebula space"]
    elif any(w in low for w in ("roman", "ancient", "history", "aqueduct", "empire",
                                "archaeology", "ruins", "civilization")):
        broad_terms += ["ancient history ruins", "historical architecture", "archaeology site"]
    elif any(w in low for w in ("qr", "technology", "computer", "phone", "robot", "chip",
                                "screen", "digital", "code")):
        broad_terms += ["technology close up", "digital device detail", "computer technology"]
    elif any(w in low for w in ("fox", "wolf", "bear", "lion", "tiger", "bird", "eagle",
                                "animal", "wildlife", "predator", "prey")):
        broad_terms += ["wildlife close up", "animals in wild", "nature documentary"]
    else:
        subject = " ".join(str(keyword or fallback).replace("-", " ").split())
        broad_terms += [subject, f"{subject} documentary", f"{subject} close up"]

    if fallback:
        broad_terms.insert(0, fallback)
    return broad_terms


def _scene_importance_for_index(idx, narration=""):
    low = str(narration or "").lower()
    if any(word in low for word in ("subscribe", "follow", "like for", "comment")):
        return SceneImportance.CTA.value
    if idx == 0:
        return SceneImportance.HOOK.value
    if idx == 1:
        return SceneImportance.MAIN_REVEAL.value
    return SceneImportance.SUPPORTING.value


def _minimum_score_for_intent(intent):
    return {
        SceneImportance.HOOK: 6.0,
        SceneImportance.MAIN_REVEAL: 5.0,
        SceneImportance.SUPPORTING: 1.5,
        SceneImportance.TRANSITION: 1.0,
        SceneImportance.CTA: 1.0,
    }.get(getattr(intent, "scene_importance", SceneImportance.SUPPORTING), 1.5)


def _selection_intent(queries, fallback="", narration="", idx=None, shot_intent=None):
    return build_visual_intent(
        {
            "narration": narration,
            "broll": queries[0] if queries else fallback,
            "broll_queries": queries,
            "scene_importance": _scene_importance_for_index(idx, narration) if idx is not None else "",
            "media_mode": getattr(getattr(shot_intent, "media_mode", None), "value", getattr(shot_intent, "media_mode", "")) or "",
            "primary_subject": getattr(shot_intent, "primary_subject", "") or "",
            "scene_entity": (
                getattr(shot_intent, "scene_entity", None).to_dict()
                if getattr(shot_intent, "scene_entity", None)
                else None
            ),
            "supporting_subjects": list(getattr(shot_intent, "required_entities", ()) or ()),
            "subject_persistence_target": (
                getattr(shot_intent, "diagnostics", {})
                .get("subject_continuity", {})
                .get("subject_persistence_target", 0.85)
            ) if shot_intent else 0.85,
            "allowed_substitutions": (
                getattr(shot_intent, "diagnostics", {})
                .get("subject_continuity", {})
                .get("allowed_substitutions", [])
            ) if shot_intent else [],
            "forbidden_substitutions": list(getattr(shot_intent, "negative_terms", ()) or ()),
        },
        fallback,
    )


def _remember_media_selection(idx, result, provider_name=""):
    if isinstance(result, MediaSelectionResult):
        previous = _MEDIA_SELECTION_DIAGNOSTICS.get(idx, {})
        attempts = list(previous.get("selection_attempts", []))
        rejected_provider = ""
        if result.rejected:
            rejected_provider = str(result.rejected[0][0]).split(":", 1)[0]
        attempts.append({
            "provider": result.provider or rejected_provider or str(provider_name).lower(),
            "accepted": bool(result.selected_candidate),
            "confidence": result.confidence,
            "candidate_count": result.candidate_count,
            "warnings": list(result.warnings),
            "rejected": [
                {"candidate": candidate_id, "reasons": list(reasons)}
                for candidate_id, reasons in result.rejected
            ],
        })
        metadata = result.to_metadata()
        metadata["selection_attempts"] = attempts
        if idx in _MEDIA_PLANNING_DIAGNOSTICS:
            planning = _MEDIA_PLANNING_DIAGNOSTICS[idx]
            query_plan = planning.get("query_plan", {})
            provider_plan = next(
                (
                    plan
                    for plan in planning.get("search_strategy", [])
                    if plan.get("provider") == result.provider
                ),
                {},
            )
            metadata["scene_type"] = query_plan.get("scene_type", metadata.get("scene_type", ""))
            metadata["capability"] = next(
                iter(provider_plan.get("matched_capabilities", [])),
                metadata.get("capability", ""),
            )
            if "selection" in metadata:
                metadata["selection"]["scene_type"] = metadata["scene_type"]
                metadata["selection"]["capability"] = metadata["capability"]
            metadata.update(planning)
        _MEDIA_SELECTION_DIAGNOSTICS[idx] = metadata


def _build_search_strategy(queries, fallback, narration, local_media=None, idx=None, intent=None):
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    registry = default_provider_capability_registry(
        local_enabled=bool(local_media),
        pexels_enabled=bool(PEXELS_API_KEY),
        pixabay_enabled=bool(PIXABAY_API_KEY and "your_pixabay" not in PIXABAY_API_KEY),
        nasa_enabled=True,
        gemini_image_enabled=is_gemini_image_available(),
        pollinations_image_enabled=is_pollinations_image_available(),
        mixkit_enabled=bool(MIXKIT_API_URL),
        coverr_enabled=False,
        yt_clip_enabled=False,
        vecteezy_enabled=bool(VECTEEZY_API_URL and VECTEEZY_API_KEY and VECTEEZY_ACCOUNT_ID),
        videvo_enabled=bool(VIDEVO_API_URL and VIDEVO_API_KEY),
        wikimedia_enabled=ENABLE_WIKIMEDIA_COMMONS,
        noaa_enabled=bool(NOAA_API_URL),
        esa_enabled=bool(ESA_API_URL),
        usgs_enabled=bool(USGS_API_URL),
        smithsonian_enabled=bool(SMITHSONIAN_API_URL and SMITHSONIAN_API_KEY),
        nps_enabled=bool(NPS_API_URL),
        usfws_enabled=bool(USFWS_API_URL),
        loc_enabled=ARCHIVE_PROVIDERS_ENABLED and bool(LOC_API_URL),
        europeana_enabled=bool(EUROPEANA_API_URL and EUROPEANA_API_KEY),
        flickr_commons_enabled=bool(FLICKR_COMMONS_API_URL and FLICKR_API_KEY),
        internet_archive_enabled=INTERNET_ARCHIVE_ENABLED,
    )
    query_plan = QueryPlanner().plan(intent)
    return SourcePlanner(registry).plan(query_plan, provider_order=_stock_provider_order())


def _remember_media_planning(idx, strategy):
    if isinstance(strategy, SearchStrategy):
        _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
            **strategy.diagnostics,
        }


def _select_candidate_for_provider(
    provider_name,
    idx,
    candidates,
    *,
    intent,
    used_set,
    target_duration,
    minimum_score=1.0,
):
    effective_minimum = max(float(minimum_score), _minimum_score_for_intent(intent))
    result = select_best_candidate(
        intent,
        candidates,
        used_provider_ids=set(used_set or []),
        target_duration_sec=target_duration,
        output_width=WIDTH,
        output_height=HEIGHT,
        minimum_score=effective_minimum,
        evidence_engine=_evidence_engine(),
    )
    _remember_media_selection(idx, result, provider_name)
    if result.selected_candidate:
        cand = result.selected_candidate
        print(
            f"    [{provider_name}] Selected {cand.provider_id} "
            f"score={result.score.score:.2f} confidence={result.confidence} "
            f"portrait={result.to_metadata().get('portrait_score')} "
            f"relevance={result.to_metadata().get('relevance_score')}"
        )
        return cand
    if result.candidate_count:
        print(f"    [{provider_name}] No candidate passed scoring ({result.candidate_count} checked).")
    return None


def _provider_is_configured(provider):
    return {
        "pexels": bool(PEXELS_API_KEY),
        "pixabay": bool(PIXABAY_API_KEY and "your_pixabay" not in PIXABAY_API_KEY),
        "wikimedia": bool(ENABLE_WIKIMEDIA_COMMONS),
        "mixkit": bool(MIXKIT_API_URL),
        "coverr": bool(COVERR_API_URL and COVERR_API_KEY),
        "vecteezy": bool(VECTEEZY_API_URL and VECTEEZY_API_KEY and VECTEEZY_ACCOUNT_ID),
        "videvo": bool(VIDEVO_API_URL and VIDEVO_API_KEY),
        "noaa": bool(NOAA_API_URL),
        "esa": bool(ESA_API_URL),
        "usgs": bool(USGS_API_URL),
        "europeana": bool(EUROPEANA_API_URL and EUROPEANA_API_KEY),
    }.get(str(provider or "").lower(), True)


def _provider_failure_detail(provider):
    return _PROVIDER_RUN_FAILURES.get(str(provider or "").lower(), "")


def _provider_is_available(provider):
    provider_id = str(provider or "").lower()
    return _provider_is_configured(provider_id) and not _provider_failure_detail(provider_id)


def _mark_provider_run_failure(provider, detail):
    provider_id = str(provider or "").lower()
    if not provider_id:
        return
    message = str(detail or "hard provider failure")[:240]
    if provider_id not in _PROVIDER_RUN_FAILURES:
        print(f"    [{provider_id}] hard failure; disabling provider for this run: {message}")
    _PROVIDER_RUN_FAILURES[provider_id] = message


def _classify_provider_probe_exception(exc):
    """Map provider exceptions to stable source-coverage diagnostics."""

    import requests

    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return ProviderProbeStatus.TIMEOUT
    response = getattr(exc, "response", None)
    status_code = int(getattr(response, "status_code", 0) or 0)
    text = str(exc).lower()
    if status_code in {401, 403} or any(token in text for token in ("unauthorized", "forbidden", "invalid api key")):
        return ProviderProbeStatus.AUTH_ERROR
    if status_code == 429 or any(token in text for token in ("rate limit", "too many requests", "quota")):
        return ProviderProbeStatus.RATE_LIMITED
    if "timed out" in text or "timeout" in text:
        return ProviderProbeStatus.TIMEOUT
    if any(token in text for token in ("invalid media", "html/xml/json error payload", "non-media content")):
        return ProviderProbeStatus.INVALID_MEDIA
    return ProviderProbeStatus.PROVIDER_ERROR


def _pexels_video_candidates(
    queries,
    fallback="",
    *,
    orientation="portrait",
    per_page=10,
    timeout_sec=30,
    raise_errors=False,
):
    """Return normalized Pexels candidates without downloading them."""

    import requests

    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    candidate_pool = []
    for q in queries:
        enriched = _qualify_query(q, fallback)
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={
                    "query": enriched,
                    "orientation": orientation,
                    "per_page": per_page,
                    "size": "medium",
                },
                timeout=timeout_sec,
            )
            response.raise_for_status()
            videos = response.json().get("videos", []) or []
        except Exception as e:
            print(f"    [Pexels] search failed for {enriched!r}: {_safe_diagnostic(e)}")
            if raise_errors:
                raise
            continue
        for video in videos:
            candidate = candidate_from_pexels_video(video, enriched)
            if candidate:
                candidate_pool.append(candidate)
    return candidate_pool


def _best_pixabay_rendition(hit, *, allow_landscape=False):
    renditions = (hit.get("videos") or {}).values()
    best = None
    best_dim = 0
    for rd in renditions:
        w, h = rd.get("width", 0), rd.get("height", 0)
        if h <= 0 or w <= 0:
            continue
        if not allow_landscape and h < w:
            continue
        dim = h if h >= w else w
        if dim > best_dim:
            best, best_dim = rd, dim
    return best


def _pixabay_video_candidates(
    queries,
    fallback="",
    *,
    allow_landscape=False,
    per_page=20,
    timeout_sec=30,
    raise_errors=False,
):
    """Return normalized Pixabay candidates without downloading them."""

    import requests

    if not PIXABAY_API_KEY or "your_pixabay" in PIXABAY_API_KEY:
        return []
    candidate_pool = []
    for q in queries:
        enriched = _qualify_query(q, fallback)
        try:
            response = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": PIXABAY_API_KEY,
                    "q": enriched,
                    "video_type": "film",
                    "per_page": per_page,
                    "safesearch": "true",
                },
                timeout=timeout_sec,
            )
            response.raise_for_status()
            hits = response.json().get("hits", []) or []
        except Exception as e:
            print(f"    [Pixabay] search failed for {enriched!r}: {_safe_diagnostic(e)}")
            if raise_errors:
                raise
            continue
        for hit in hits:
            rendition = _best_pixabay_rendition(hit, allow_landscape=allow_landscape)
            if rendition:
                candidate_pool.append(candidate_from_pixabay_hit(hit, rendition, enriched))
    return candidate_pool


def _json_stock_candidates(
    provider,
    endpoint,
    queries,
    *,
    fallback="",
    headers=None,
    params_extra=None,
    license_name="",
    timeout_sec=30,
    raise_errors=False,
):
    """Return normalized configured JSON-provider candidates without downloading."""

    import requests

    if not endpoint:
        return []
    candidate_pool = []
    for q in queries:
        enriched = _qualify_query(q, fallback)
        try:
            response = requests.get(
                endpoint,
                headers=headers or {},
                params={"q": enriched, "query": enriched, **(params_extra or {})},
                timeout=timeout_sec,
            )
            response.raise_for_status()
            items = _json_items(response.json())
        except Exception as e:
            print(f"    [{provider.title()}] search failed for {enriched!r}: {_safe_diagnostic(e)}")
            if raise_errors:
                raise
            continue
        for item in items:
            normalized = _remote_item_from_provider(provider, item, enriched)
            if normalized:
                normalized["license"] = normalized.get("license") or license_name
                candidate = candidate_from_remote_item(provider, normalized, enriched)
                if candidate:
                    candidate_pool.append(candidate)
    return candidate_pool


def _coverr_base_url():
    base = COVERR_API_URL.rstrip("/")
    if base.endswith("/search/videos"):
        base = base[: -len("/search/videos")]
    if base.endswith("/videos"):
        base = base[: -len("/videos")]
    return base


def _coverr_headers():
    headers = {"User-Agent": "auto-short/1.0 educational video generator"}
    if COVERR_API_KEY:
        headers["Authorization"] = f"Bearer {COVERR_API_KEY}"
        headers["X-API-Key"] = COVERR_API_KEY
    if COVERR_APP_ID:
        headers["X-App-ID"] = COVERR_APP_ID
        headers["X-API-ID"] = COVERR_APP_ID
    return headers


def _coverr_download_url(item):
    urls = item.get("urls") if isinstance(item.get("urls"), dict) else {}
    for key in ("mp4_download", "mp4", "mp4_preview"):
        if urls.get(key):
            return str(urls[key])
    variant = item.get("default_variant") if isinstance(item.get("default_variant"), dict) else {}
    renditions = variant.get("renditions") if isinstance(variant.get("renditions"), list) else []
    free_renditions = [rendition for rendition in renditions if isinstance(rendition, dict) and not rendition.get("is_plus")]
    if free_renditions:
        picked = max(free_renditions, key=lambda rendition: int(rendition.get("height") or 0))
        if picked.get("url"):
            return str(picked["url"])
    return str(item.get("download_url") or item.get("video_url") or "")


def _coverr_candidates(queries, *, fallback="", timeout_sec=30, raise_errors=False):
    import requests

    base_url = _coverr_base_url()
    if not base_url or _provider_failure_detail("coverr"):
        return []
    session = requests.Session()
    candidate_pool = []
    for q in queries:
        enriched = _qualify_query(q, fallback)
        try:
            response = session.get(
                f"{base_url}/videos",
                headers=_coverr_headers(),
                params={"query": enriched, "urls": "true", "page_size": 8},
                timeout=timeout_sec,
            )
            response.raise_for_status()
            items = _json_items(response.json())
        except Exception as e:
            print(f"    [Coverr] search failed for {enriched!r}: {_safe_diagnostic(e)}")
            response = getattr(e, "response", None)
            status_code = int(getattr(response, "status_code", 0) or 0)
            hard_failure = status_code in {401, 403, 404, 429}
            if hard_failure:
                _mark_provider_run_failure("coverr", f"HTTP {status_code}: {_safe_diagnostic(e)}")
            if raise_errors and not candidate_pool:
                raise
            if hard_failure:
                return candidate_pool
            continue
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            base_filename = item.get("base_filename") or item.get("baseFilename")
            download_url = _coverr_download_url(item)
            if not download_url and (item.get("id") or item.get("objectID") or item.get("video_id")):
                try:
                    detail_response = session.get(
                        f"{base_url}/videos/{item.get('id') or item.get('objectID') or item.get('video_id')}",
                        headers=_coverr_headers(),
                        timeout=timeout_sec,
                    )
                    detail_response.raise_for_status()
                    item = {**item, **detail_response.json()}
                    download_url = _coverr_download_url(item)
                except Exception as e:
                    print(f"    [Coverr] video detail failed for {base_filename!r}: {_safe_diagnostic(e)}")
                    detail_response = getattr(e, "response", None)
                    status_code = int(getattr(detail_response, "status_code", 0) or 0)
                    if status_code in {401, 403, 429}:
                        _mark_provider_run_failure("coverr", f"HTTP {status_code}: {_safe_diagnostic(e)}")
                    if raise_errors and not candidate_pool:
                        raise
                    if status_code in {401, 403, 429}:
                        return candidate_pool
                    continue
            if not download_url:
                continue
            is_vertical = bool(item.get("is_vertical"))
            candidate = candidate_from_remote_item(
                "coverr",
                {
                    "provider_asset_id": item.get("id") or base_filename or download_url,
                    "title": item.get("title") or item.get("name") or "",
                    "description": " ".join([
                        str(item.get("description") or ""),
                        " ".join(str(tag) for tag in (item.get("tags") or [])),
                    ]).strip(),
                    "source_url": (
                        item.get("contributor_url")
                        or item.get("url")
                        or (f"https://coverr.co/videos/{item.get('slug')}" if item.get("slug") else "")
                        or "https://coverr.co/"
                    ),
                    "download_url": download_url,
                    "duration_sec": item.get("duration"),
                    "width": 1080 if is_vertical else 1920,
                    "height": 1920 if is_vertical else 1080,
                    "license": "Coverr License",
                    "attribution": item.get("contributor_name") or "Coverr",
                    "capability": "generic_stock_video",
                },
                enriched,
            )
            if candidate:
                candidate_pool.append(candidate)
    return candidate_pool


def _vecteezy_headers():
    headers = {"User-Agent": "auto-short/1.0 educational video generator"}
    if VECTEEZY_API_KEY:
        headers["Authorization"] = f"Bearer {VECTEEZY_API_KEY}"
    return headers


def _vecteezy_resource_extensions(item):
    file_metadata = item.get("file_metadata") if isinstance(item.get("file_metadata"), dict) else {}
    file_types = file_metadata.get("available_file_types")
    if not isinstance(file_types, list):
        return []
    extensions = []
    for entry in file_types:
        if isinstance(entry, dict) and entry.get("extension"):
            extensions.append(str(entry["extension"]))
    return extensions


def _vecteezy_resource_dimensions(item):
    file_metadata = item.get("file_metadata") if isinstance(item.get("file_metadata"), dict) else {}
    sizes = file_metadata.get("available_download_sizes")
    if not isinstance(sizes, list):
        return None, None
    sizes = [entry for entry in sizes if isinstance(entry, dict)]
    preferred = next((entry for entry in sizes if entry.get("id") == "original"), None)
    picked = preferred or (sizes[0] if sizes else None)
    if not picked:
        return None, None
    width = picked.get("width")
    height = picked.get("height")
    if not width or not height:
        return None, None
    return int(width), int(height)


def _vecteezy_download_resolution(session, resource_id, extensions, *, timeout_sec=30, raise_errors=False):
    """Resolve the signed download URL for one Vecteezy resource.

    Returns a dict with download_url and attribution fields, or None when the
    item cannot be downloaded. Auth, quota, and rate-limit failures disable
    the provider for the run; item-level failures do not.
    """

    file_type = "mp4" if "mp4" in extensions else (extensions[0] if extensions else "")
    if not file_type:
        return None
    try:
        response = session.get(
            f"{VECTEEZY_API_URL}/v2/{VECTEEZY_ACCOUNT_ID}/resources/{resource_id}/download",
            headers=_vecteezy_headers(),
            params={"file_type": file_type},
            timeout=timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        response = getattr(e, "response", None)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403, 429, 402}:
            _mark_provider_run_failure("vecteezy", f"HTTP {status_code}: {_safe_diagnostic(e)}")
            if raise_errors:
                raise
        else:
            print(f"    [Vecteezy] download resolution failed for {resource_id}: {_safe_diagnostic(e)}")
        return None
    if not isinstance(payload, dict):
        return None
    url = str(payload.get("url") or payload.get("inline_url") or "").strip()
    if not url:
        return None
    return {
        "download_url": url,
        "requires_attribution": bool(payload.get("requires_attribution")),
        "required_attribution_url": str(payload.get("required_attribution_url") or "").strip(),
    }


def _vecteezy_candidates(queries, *, fallback="", timeout_sec=30, raise_errors=False):
    import requests

    if not (VECTEEZY_API_URL and VECTEEZY_API_KEY and VECTEEZY_ACCOUNT_ID):
        return []
    if _provider_failure_detail("vecteezy"):
        return []
    session = requests.Session()
    candidate_pool = []
    for q in queries:
        enriched = _qualify_query(q, fallback)
        try:
            response = session.get(
                f"{VECTEEZY_API_URL}/v2/{VECTEEZY_ACCOUNT_ID}/resources",
                headers=_vecteezy_headers(),
                params={
                    "term": enriched,
                    "content_type": "video",
                    "per_page": 8,
                    "sort_by": "relevance",
                    "license_type": "commercial",
                    "family_friendly": "true",
                },
                timeout=timeout_sec,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("resources") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                items = _json_items(payload)
        except Exception as e:
            print(f"    [Vecteezy] search failed for {enriched!r}: {_safe_diagnostic(e)}")
            response = getattr(e, "response", None)
            status_code = int(getattr(response, "status_code", 0) or 0)
            hard_failure = status_code in {401, 403, 404, 429, 402}
            if hard_failure:
                _mark_provider_run_failure("vecteezy", f"HTTP {status_code}: {_safe_diagnostic(e)}")
            if raise_errors and not candidate_pool:
                raise
            if hard_failure:
                return candidate_pool
            continue
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            resource_id = item.get("id")
            if not resource_id:
                continue
            resolution = _vecteezy_download_resolution(
                session,
                resource_id,
                _vecteezy_resource_extensions(item),
                timeout_sec=timeout_sec,
                raise_errors=raise_errors,
            )
            if resolution is None:
                if _provider_failure_detail("vecteezy"):
                    return candidate_pool
                continue
            width, height = _vecteezy_resource_dimensions(item)
            attribution_url = resolution["required_attribution_url"]
            candidate = candidate_from_remote_item(
                "vecteezy",
                {
                    "provider_asset_id": resource_id,
                    "title": item.get("title") or "",
                    "description": " ".join(
                        str(tag) for tag in (item.get("tags") or []) if str(tag).strip()
                    ),
                    "source_url": attribution_url or "https://www.vecteezy.com/",
                    "download_url": resolution["download_url"],
                    "width": width,
                    "height": height,
                    "license": "Vecteezy License",
                    "attribution": "Vecteezy",
                    "capability": "generic_stock_video",
                },
                enriched,
            )
            if candidate:
                candidate_pool.append(candidate)
    return candidate_pool


def _wikimedia_user_agent():
    if WIKIMEDIA_USER_AGENT:
        return WIKIMEDIA_USER_AGENT
    contact = WIKIMEDIA_CONTACT
    if not contact:
        match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", NOAA_USER_AGENT)
        contact = match.group(0) if match else ""
    suffix = f"; contact: {contact}" if contact else ""
    return f"auto-short/1.0 (educational documentary media generator{suffix})"


def _search_wikimedia_pages(query, *, timeout_sec=30, raise_errors=False):
    """Run one cached, paced Wikimedia imageinfo search with bounded retries."""

    import requests

    global _WIKIMEDIA_LAST_REQUEST_AT
    cache_key = " ".join(str(query).lower().split())
    if cache_key in _WIKIMEDIA_SEARCH_CACHE:
        return _WIKIMEDIA_SEARCH_CACHE[cache_key]

    session = requests.Session()
    session.headers.update({"User-Agent": _wikimedia_user_agent()})
    started = time.monotonic()
    try:
        for attempt in range(1, 4):
            remaining = float(timeout_sec) - (time.monotonic() - started)
            if remaining <= 0:
                raise requests.exceptions.Timeout(f"Wikimedia search exceeded {timeout_sec}s")
            pacing = 0.1 - (time.monotonic() - _WIKIMEDIA_LAST_REQUEST_AT)
            if pacing > 0:
                time.sleep(min(pacing, remaining))
            response = None
            try:
                response = session.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "format": "json",
                        "generator": "search",
                        "gsrnamespace": 6,
                        "gsrsearch": query,
                        "gsrlimit": 8,
                        "prop": "imageinfo",
                        "iiprop": "url|mime|size|extmetadata",
                        "iiurlwidth": 1280,
                    },
                    timeout=min(float(timeout_sec), remaining),
                )
                _WIKIMEDIA_LAST_REQUEST_AT = time.monotonic()
                status = int(getattr(response, "status_code", 0) or 0)
                if status in {429, 500, 502, 503, 504}:
                    if attempt >= 3:
                        response.raise_for_status()
                        raise RuntimeError(f"Wikimedia HTTP {status} after 3 attempts")
                    delay = _retry_delay_seconds(getattr(response, "headers", {}), attempt, maximum=10.0)
                    if time.monotonic() - started + delay >= timeout_sec:
                        raise requests.HTTPError(
                            f"Wikimedia HTTP {status}; retry would exceed {timeout_sec}s",
                            response=response,
                        )
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                pages = (response.json().get("query", {}) or {}).get("pages", {}) or {}
                _WIKIMEDIA_SEARCH_CACHE[cache_key] = pages
                return pages
            finally:
                if response is not None:
                    response.close()
    except Exception as exc:
        print(f"    [Wikimedia] search failed for {query!r}: {_safe_diagnostic(exc)}")
        if raise_errors:
            raise
        return {}
    finally:
        session.close()


def _wikimedia_candidates(queries, *, timeout_sec=30, raise_errors=False):
    """Return normalized Wikimedia candidates without downloading them."""

    if not ENABLE_WIKIMEDIA_COMMONS:
        return []
    candidate_pool = []
    for query in queries:
        pages = _search_wikimedia_pages(
            query,
            timeout_sec=timeout_sec,
            raise_errors=raise_errors,
        )
        for page in pages.values():
            candidate = _wikimedia_candidate_from_page(page, query)
            if candidate:
                candidate_pool.append(candidate)
    return candidate_pool


def _yt_clip_probe_candidates(queries, fallback="", *, timeout_sec=15, raise_errors=False):
    """Lightweight yt_clip availability probe for coverage preflight.

    Runs a single yt-dlp metadata-only search (no download) and emits one
    candidate per scene when YouTube returns results, so rare/microscopic
    topics that stock APIs cannot answer are not deferred before yt_clip
    has a chance to retrieve authentic footage.
    """

    try:
        from autovideo.providers.stock.yt_clip import is_yt_dlp_available
    except ImportError:
        return []
    if not is_yt_dlp_available():
        return []
    yt_dlp_cmd = shutil.which("yt-dlp") or "yt-dlp"
    candidate_queries = [
        str(query).strip()
        for query in (*queries, fallback or "wildlife nature footage")
        if str(query or "").strip()
    ]
    if not candidate_queries:
        return []
    for raw_query in dict.fromkeys(candidate_queries):
        search_term = f"ytsearch2:{raw_query} footage"
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
            res = subprocess.run(
                info_cmd, capture_output=True, text=True, timeout=min(int(timeout_sec), 12),
            )
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            continue
        if not res.stdout.strip():
            continue
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            continue
        entries = data.get("entries") or [data] if "entries" in data or "id" in data else []
        if not entries:
            continue
        entry = entries[0]
        vid_id = str(entry.get("id") or f"probe_{hash(raw_query) & 0xffff:04x}")
        candidate = candidate_from_remote_item(
            "yt_clip",
            {
                "provider_asset_id": vid_id,
                "title": str(entry.get("title") or raw_query)[:200],
                "description": str(entry.get("description") or "")[:400],
                "source_url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}",
                "download_url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}",
                "width": None,
                "height": None,
                "license": "YouTube Standard License",
                "attribution": "",
                "is_image": False,
                "capability": "generic_stock_video",
            },
            raw_query,
        )
        return [candidate] if candidate else []
    return []


def _adaptive_provider_candidates(
    provider,
    queries,
    fallback,
    *,
    landscape=False,
    timeout_sec=30,
    probe=False,
):
    if provider == "pexels":
        orientation = "landscape" if landscape else "portrait"
        return _pexels_video_candidates(
            queries,
            fallback,
            orientation=orientation,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "pixabay":
        return _pixabay_video_candidates(
            queries,
            fallback,
            allow_landscape=landscape,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "wikimedia":
        return _wikimedia_candidates(queries, timeout_sec=timeout_sec, raise_errors=probe)
    if provider == "mixkit":
        return _json_stock_candidates(
            "mixkit",
            MIXKIT_API_URL,
            queries,
            fallback=fallback,
            license_name="Mixkit License",
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "coverr":
        return _coverr_candidates(
            queries,
            fallback=fallback,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "vecteezy":
        return _vecteezy_candidates(
            queries,
            fallback=fallback,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "videvo" and VIDEVO_API_URL and VIDEVO_API_KEY:
        return _json_stock_candidates(
            "videvo",
            VIDEVO_API_URL,
            queries,
            fallback=fallback,
            headers={"Authorization": f"Bearer {VIDEVO_API_KEY}"},
            license_name="Videvo license varies by clip",
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "noaa":
        return _json_stock_candidates(
            "noaa",
            NOAA_API_URL,
            queries,
            fallback=fallback,
            headers={"User-Agent": NOAA_USER_AGENT},
            license_name="NOAA public domain / usage varies",
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "esa":
        return _json_stock_candidates(
            "esa",
            ESA_API_URL,
            queries,
            fallback=fallback,
            license_name="ESA media usage guidelines",
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "usgs":
        return _json_stock_candidates(
            "usgs",
            USGS_API_URL,
            queries,
            fallback=fallback,
            license_name="USGS public domain / usage varies",
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "europeana":
        return _europeana_candidates(
            queries,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    if provider == "yt_clip":
        return _yt_clip_probe_candidates(
            queries,
            fallback,
            timeout_sec=timeout_sec,
            raise_errors=probe,
        )
    return []


def _dedupe_candidates(candidates):
    seen = set()
    deduped = []
    for candidate in candidates:
        key = (candidate.provider, candidate.provider_id, candidate.download_url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _stable_source_url(url):
    """Remove expiring signature material while retaining a stable source page."""

    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        signed_keys = {
            "expires", "signature", "sig", "token", "x-amz-algorithm",
            "x-amz-credential", "x-amz-date", "x-amz-expires", "x-amz-signature",
            "x-amz-signedheaders",
        }
        if any(key.casefold() in signed_keys for key, _value in pairs):
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))
    except ValueError:
        return value.split("?", 1)[0]


def _safe_diagnostic(exc):
    """Keep provider diagnostics useful without persisting keys or signed URLs."""

    text = str(exc or "")[:500]
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: _stable_source_url(match.group(0)),
        text,
    )
    for secret in (
        PEXELS_API_KEY,
        PIXABAY_API_KEY,
        COVERR_API_KEY,
        EUROPEANA_API_KEY,
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:300]


def _stable_provider_id(provider_id):
    value = str(provider_id or "").strip()
    if value.casefold().startswith(("http://", "https://")):
        return _stable_source_url(value)
    return value


def _critical_candidate_is_authentic(candidate):
    provider = str(getattr(candidate, "provider", "") or "").casefold()
    if provider not in CRITICAL_ASSET_PROVIDERS:
        return False
    raw = getattr(candidate, "raw_metadata", {}) or {}
    provenance = " ".join((
        str(raw.get("capability") or ""),
        str(raw.get("source_type") or ""),
        str(raw.get("provenance") or ""),
    )).casefold()
    return not any(marker in provenance for marker in ("generated", "composed", "synthetic"))


def _critical_visual_intent(card: TopicCard, role, queries):
    scene_index = 0 if role == "hook" else 1
    intent = build_visual_intent(
        {
            "narration": f"{card.required_entity} {card.required_action}",
            "broll": queries[0],
            "broll_queries": list(queries),
            "scene_importance": "hook" if scene_index == 0 else "main_reveal",
            "media_mode": "prove",
            "primary_subject": card.required_entity,
            "scene_entity": {
                "canonical_entity": card.required_entity,
                "entity_type": "topic_card_required_entity",
                "aliases": [card.subject],
                "required_terms": [card.required_entity],
                "optional_terms": [],
                "forbidden_terms": [],
            },
        },
        card.premise,
    )
    action_terms = tuple(dict.fromkeys(
        token
        for token in re.findall(r"[a-z0-9]+", card.required_action.casefold())
        if len(token) >= 4 and token not in {"through", "while", "with", "into", "from", "over", "against"}
    ))
    return replace(intent, action_terms=action_terms)


def _critical_candidate_record(candidate, score=None):
    raw = candidate.raw_metadata if isinstance(candidate.raw_metadata, dict) else {}
    return {
        "provider": candidate.provider,
        "provider_id": _stable_provider_id(candidate.provider_id),
        "source_url": _stable_source_url(candidate.url or raw.get("source_url")),
        "license": str(raw.get("license") or ""),
        "attribution": str(raw.get("attribution") or ""),
        "query": candidate.query,
        "score": round(float(score.score), 3) if score is not None else None,
        "confidence": score.confidence if score is not None else "",
    }


def _rank_critical_candidates(intent, candidates, used_provider_ids, maximum):
    ranked = []
    for candidate in _dedupe_candidates(candidates):
        if not _critical_candidate_is_authentic(candidate):
            continue
        score = score_candidate(
            intent,
            candidate,
            used_provider_ids=set(used_provider_ids),
            target_duration_sec=SHORTS_SCENE_TARGET_DURATION,
            output_width=WIDTH,
            output_height=HEIGHT,
            evidence_engine=EvidenceVerificationEngine(),
        )
        if score.quality_gate_passed and score.score >= _minimum_score_for_intent(intent):
            ranked.append((score, candidate))
    ranked.sort(
        key=lambda item: (
            item[0].score,
            item[0].relevance_score,
            int(not item[1].is_image),
        ),
        reverse=True,
    )
    return ranked[:maximum]


def discover_critical_assets(
    topic,
    *,
    output_dir=None,
    card=None,
    providers=None,
    candidate_loader=None,
    downloader=None,
    verifier=None,
    gate_config=None,
):
    """Discover and frame-verify TopicCard hook/reveal assets before scripting."""

    matched_card = card or find_topic_card(topic)
    if matched_card is None:
        return {
            "version": 1,
            "topic": topic,
            "topic_card": None,
            "status": "SKIPPED",
            "failure_classification": "SKIPPED",
            "failure_reason": "topic does not match a structured TopicCard premise",
            "providers": [],
            "roles": [],
        }

    selected_providers = tuple(providers) if providers is not None else tuple(
        provider for provider in CRITICAL_ASSET_PROVIDERS if _provider_is_configured(provider)
    )
    plan = {
        "version": 1,
        "topic": topic,
        "topic_card": {
            "id": matched_card.id,
            "pillar": matched_card.pillar,
            "subject": matched_card.subject,
            "premise": matched_card.premise,
        },
        "status": "FAILED",
        "failure_classification": "",
        "failure_reason": "",
        "providers": list(selected_providers),
        "roles": [],
    }
    if not selected_providers:
        plan["failure_classification"] = "TECHNICAL_PROVIDER_FAILURE"
        plan["failure_reason"] = "no authentic critical-asset provider is configured"
        return plan

    output_root = Path(output_dir or OUT_DIR) / "critical_assets"
    maximum = max(1, _env_int("AUTO_VIDEO_CRITICAL_ASSET_MAX_ALTERNATIVES", 3))
    provider_timeout = max(5, _env_int("AUTO_VIDEO_CRITICAL_ASSET_PROVIDER_TIMEOUT_SEC", 30))
    download_timeout = max(10, _env_int("AUTO_VIDEO_CRITICAL_ASSET_DOWNLOAD_TIMEOUT_SEC", 90))
    max_bytes = max(1, _env_int("AUTO_VIDEO_CRITICAL_ASSET_MAX_DOWNLOAD_MB", 120)) * 1024 * 1024
    load_candidates = candidate_loader or (
        lambda provider, queries, fallback: _adaptive_provider_candidates(
            provider,
            queries,
            fallback,
            landscape=WIDTH >= HEIGHT,
            timeout_sec=provider_timeout,
            probe=True,
        )
    )
    download = downloader or _download_to
    config = gate_config or VerifiedMediaGateConfig.from_env(os.environ)
    config = replace(
        config,
        enabled=True,
        allow_unverified_when_vision_unavailable=False,
    )
    gate = VerifiedMediaGate(config, verifier=verifier or _gemini_verified_media_verifier)
    used_provider_ids = _load_persistent_used()
    failed_roles = []

    for scene_index, (role_name, queries) in enumerate((
        ("hook", matched_card.hook_queries),
        ("main_reveal", matched_card.reveal_queries),
    )):
        intent = _critical_visual_intent(matched_card, role_name, queries)
        role_plan = {
            "role": role_name,
            "scene_index": scene_index,
            "expected_entity": matched_card.required_entity,
            "expected_action": matched_card.required_action,
            "queries": list(queries),
            "status": "FAILED",
            "selected": None,
            "backups": [],
            "attempts": [],
            "provider_outcomes": [],
            "failure_classification": "",
            "failure_reason": "",
        }
        candidates = []
        provider_failed = False
        for provider in selected_providers:
            try:
                found = load_candidates(provider, tuple(queries), matched_card.subject) or []
            except Exception as exc:
                status = _classify_provider_probe_exception(exc)
                provider_failed = provider_failed or status in CRITICAL_ASSET_TECHNICAL_FAILURES
                role_plan["provider_outcomes"].append({
                    "provider": provider,
                    "status": status.value,
                    "candidates_found": 0,
                    "detail": _safe_diagnostic(exc),
                })
                continue
            authentic = [candidate for candidate in found if _critical_candidate_is_authentic(candidate)]
            candidates.extend(authentic)
            role_plan["provider_outcomes"].append({
                "provider": provider,
                "status": ("SUCCESS" if authentic else "NO_RESULTS"),
                "candidates_found": len(authentic),
                "excluded_generated_or_composed": len(found) - len(authentic),
                "detail": "authentic candidates returned" if authentic else "provider returned no authentic candidates",
            })

        ranked = _rank_critical_candidates(intent, candidates, used_provider_ids, maximum)
        verifier_failed = False
        download_failed = False
        downloaded_candidate = False
        for rank, (score, candidate) in enumerate(ranked, start=1):
            attempt = {
                **_critical_candidate_record(candidate, score),
                "rank": rank,
                "expected_entity": matched_card.required_entity,
                "expected_action": matched_card.required_action,
                "local_path": "",
                "verification": None,
                "status": "DOWNLOAD_FAILED",
            }
            out_path = output_root / (
                f"{scene_index}_{role_name}_{rank}_{slugify(candidate.provider_id, 24)}"
                f"{_candidate_extension(candidate)}"
            )
            try:
                downloaded = download(
                    candidate.download_url,
                    out_path,
                    timeout=download_timeout,
                    max_bytes=max_bytes,
                )
            except Exception as exc:
                downloaded = False
                attempt["download_error"] = f"{type(exc).__name__}: download failed"
            if not downloaded or not out_path.exists():
                download_failed = True
                role_plan["attempts"].append(attempt)
                continue
            downloaded_candidate = True
            attempt["local_path"] = str(out_path)
            request = VerificationRequest(
                scene_index=scene_index,
                media_path=out_path,
                expected_entity=matched_card.required_entity,
                expected_action=matched_card.required_action,
                visual_goal="show" if scene_index == 0 else "reveal",
                priority=VerificationPriority.CRITICAL,
            )
            try:
                result = gate.evaluate(request, replacement_attempt=rank - 1)
                verification = result.to_dict()
            except Exception as exc:
                verification = {
                    "scene_index": scene_index,
                    "media_path": str(out_path),
                    "expected_entity": matched_card.required_entity,
                    "expected_action": matched_card.required_action,
                    "priority": VerificationPriority.CRITICAL.value,
                    "decision": VerificationDecision.UNVERIFIED.value,
                    "reason": "frame verifier raised an exception",
                    "error": _safe_diagnostic(exc),
                }
            attempt["verification"] = verification
            attempt["status"] = str(verification.get("decision") or "").upper()
            role_plan["attempts"].append(attempt)
            error_text = " ".join((
                str(verification.get("error") or ""),
                str(verification.get("reason") or ""),
            )).casefold()
            is_verifier_failure = bool(verification.get("error")) or any(
                marker in error_text
                for marker in ("unavailable", "quota", "rate limit", "resource_exhausted", "429", "no representative frames")
            )
            if is_verifier_failure:
                verifier_failed = True
                if role_plan["selected"] is None:
                    break
                continue
            if (
                verification.get("decision") == VerificationDecision.VERIFIED.value
                and role_plan["selected"] is None
            ):
                role_plan["selected"] = attempt
                role_plan["status"] = "VERIFIED"
                used_provider_ids.add(candidate.dedup_key)

        selected = role_plan["selected"]
        role_plan["backups"] = [
            attempt for attempt in role_plan["attempts"]
            if selected is None
            or (
                attempt.get("provider") != selected.get("provider")
                or attempt.get("provider_id") != selected.get("provider_id")
            )
        ]
        if selected is None:
            if verifier_failed:
                classification = "TECHNICAL_VERIFIER_FAILURE"
                reason = "critical frame verifier was unavailable or failed"
            elif ranked and not downloaded_candidate and download_failed:
                classification = "TECHNICAL_PROVIDER_FAILURE"
                reason = "all ranked critical candidates failed to download"
            elif candidates and not ranked:
                classification = "CONTENT_ASSET_GAP"
                reason = "authentic candidates were found but none passed intent/evidence scoring"
            elif ranked:
                classification = "CONTENT_ASSET_GAP"
                reason = "downloaded candidates did not verify the required entity and action"
            elif provider_failed and not any(
                outcome.get("status") in {"SUCCESS", "NO_RESULTS"}
                for outcome in role_plan["provider_outcomes"]
            ):
                classification = "TECHNICAL_PROVIDER_FAILURE"
                reason = "all configured critical providers failed"
            else:
                classification = "CONTENT_ASSET_GAP"
                reason = "configured providers returned no authentic critical candidates"
            role_plan["failure_classification"] = classification
            role_plan["failure_reason"] = reason
            failed_roles.append(role_plan)
        plan["roles"].append(role_plan)

    if not failed_roles:
        plan["status"] = "VERIFIED"
        plan["failure_classification"] = "NONE"
        return plan

    technical = next(
        (
            role for role in failed_roles
            if str(role.get("failure_classification") or "").startswith("TECHNICAL")
        ),
        None,
    )
    representative = technical or failed_roles[0]
    plan["failure_classification"] = representative["failure_classification"]
    plan["failure_reason"] = "; ".join(
        f"{role['role']}: {role['failure_reason']}" for role in failed_roles
    )
    return plan


def critical_asset_overrides(plan):
    """Return verified scene-index locks from a persisted critical plan."""

    if not isinstance(plan, dict) or plan.get("status") != "VERIFIED":
        return {}
    return {
        int(role["scene_index"]): role["selected"]
        for role in plan.get("roles") or []
        if isinstance(role, dict)
        and role.get("status") == "VERIFIED"
        and isinstance(role.get("selected"), dict)
    }


def apply_topic_card_identity(shot_plan, card):
    """Overlay an authoritative TopicCard entity and critical action on a ShotPlan."""

    if not isinstance(card, TopicCard):
        return shot_plan
    canonical = card.required_entity.strip()
    aliases = tuple(
        value for value in (card.subject.strip(),)
        if value and value.casefold() != canonical.casefold()
    )
    intents = []
    for intent in shot_plan.intents:
        source_entity = intent.scene_entity
        scene_entity = SceneEntity(
            canonical_entity=canonical,
            entity_type="topic_card_required_entity",
            aliases=aliases,
            required_terms=(canonical,),
            optional_terms=tuple(getattr(source_entity, "optional_terms", ())),
            forbidden_terms=tuple(getattr(source_entity, "forbidden_terms", ())),
            confidence=1.0,
        )
        critical = intent.scene_index in {0, 1}
        intents.append(replace(
            intent,
            primary_subject=canonical,
            scene_entity=scene_entity,
            required_entities=(canonical, *aliases),
            action=card.required_action if critical else intent.action,
            diagnostics={
                **intent.diagnostics,
                "topic_card_id": card.id,
                "topic_card_required_entity": canonical,
                **({"topic_card_required_action": card.required_action} if critical else {}),
            },
        ))
    return replace(
        shot_plan,
        primary_subject=canonical,
        required_subjects=(canonical,),
        visual_identity=tuple(dict.fromkeys((canonical, *shot_plan.visual_identity))),
        intents=tuple(intents),
        diagnostics={
            **shot_plan.diagnostics,
            "topic_card_id": card.id,
            "topic_card_required_entity": canonical,
        },
    )


def _critical_plan_outputs_valid(path, topic, *, require_reverification=False):
    if require_reverification:
        return False
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if str(plan.get("topic") or "").casefold() != str(topic or "").casefold():
        return False
    if plan.get("status") == "SKIPPED":
        return True
    return plan.get("status") == "VERIFIED" and all(
        Path(selected.get("local_path") or "").exists()
        for selected in critical_asset_overrides(plan).values()
    ) and len(critical_asset_overrides(plan)) == 2


def _select_adaptive_result(intent, candidates, used_set, target_duration):
    candidates = _dedupe_candidates(candidates)
    exact_candidates = [
        candidate for candidate in candidates if _candidate_has_exact_subject(candidate, intent)
    ]
    if exact_candidates:
        exact_result = select_best_candidate(
            intent,
            exact_candidates,
            used_provider_ids=set(used_set or []),
            target_duration_sec=target_duration,
            output_width=WIDTH,
            output_height=HEIGHT,
            minimum_score=max(1.0, _minimum_score_for_intent(intent)),
            evidence_engine=_evidence_engine(),
        )
        if exact_result.selected_candidate:
            return exact_result
    return select_best_candidate(
        intent,
        candidates,
        used_provider_ids=set(used_set or []),
        target_duration_sec=target_duration,
        output_width=WIDTH,
        output_height=HEIGHT,
        minimum_score=max(1.0, _minimum_score_for_intent(intent)),
        evidence_engine=_evidence_engine(),
    )


def _adaptive_needs_expansion(result, exact_count):
    if exact_count < AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES:
        return True
    return _confidence_value(result.confidence) < AUTO_VIDEO_PROVIDER_EXPANSION_CONFIDENCE


def _fetch_adaptive_broll(
    strategy,
    *,
    idx,
    fallback,
    narration,
    used_set,
    target_duration,
    intent,
    provider_query_variants=None,
    continuity_engine=None,
    continuity_state=None,
):
    """Build an expanded candidate pool before downloading a stock asset."""
    global _YT_CLIP_SCENES_USED

    plans = [
        plan
        for plan in strategy.provider_plans
        if getattr(plan, "score", 0) > 0
        and getattr(plan, "provider_id", "") not in {"local", "gemini_image"}
    ]
    if not plans:
        return None

    candidates = []
    searched = []
    report = {
        "scene_index": idx,
        "minimum_exact_candidates": AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES,
        "landscape_expansion_enabled": AUTO_VIDEO_ENABLE_LANDSCAPE_EXPANSION,
        "provider_expansion_threshold": AUTO_VIDEO_PROVIDER_EXPANSION_CONFIDENCE,
        "portrait_candidates_found": 0,
        "landscape_candidates_found": 0,
        "exact_subject_candidates": 0,
        "provider_expansion_triggered": False,
        "providers_searched": searched,
        "final_provider_selected": "",
        "confidence_before_expansion": "rejected",
        "confidence_after_expansion": "rejected",
    }

    before_result = None
    for order, plan in enumerate(plans):
        provider = plan.provider_id
        plan_queries = list(
            (provider_query_variants or {}).get(provider)
            or plan.queries
            or []
        )
        if provider not in {
            "pexels",
            "pixabay",
            "wikimedia",
            "mixkit",
            "coverr",
            "vecteezy",
            "videvo",
            "noaa",
            "esa",
            "usgs",
            "yt_clip",
        }:
            continue
        portrait_candidates = _adaptive_provider_candidates(
            provider,
            plan_queries,
            fallback,
            landscape=False,
        )
        if provider in {"pexels", "pixabay"}:
            report["portrait_candidates_found"] += len(portrait_candidates)
        candidates.extend(portrait_candidates)
        searched.append(
            {
                "provider": provider,
                "queries": plan_queries,
                "portrait_candidates": len(portrait_candidates),
                "landscape_candidates": 0,
            }
        )

        current_result = _select_adaptive_result(intent, candidates, used_set, target_duration)
        current_exact = _exact_subject_candidate_count(candidates, intent)
        if before_result is None:
            before_result = current_result
            report["confidence_before_expansion"] = current_result.confidence

        if (
            provider in {"pexels", "pixabay"}
            and AUTO_VIDEO_ENABLE_LANDSCAPE_EXPANSION
            and current_exact < AUTO_VIDEO_MIN_EXACT_SUBJECT_CANDIDATES
        ):
            landscape_candidates = _adaptive_provider_candidates(
                provider,
                plan_queries,
                fallback,
                landscape=True,
            )
            report["landscape_candidates_found"] += len(landscape_candidates)
            searched[-1]["landscape_candidates"] = len(landscape_candidates)
            candidates.extend(landscape_candidates)
            current_result = _select_adaptive_result(intent, candidates, used_set, target_duration)
            current_exact = _exact_subject_candidate_count(candidates, intent)
            report["provider_expansion_triggered"] = True

        report["exact_subject_candidates"] = current_exact
        report["confidence_after_expansion"] = current_result.confidence
        if not _adaptive_needs_expansion(current_result, current_exact):
            break
        if order < len(plans) - 1:
            report["provider_expansion_triggered"] = True

    if not candidates:
        _ADAPTIVE_SEARCH_DIAGNOSTICS[idx] = report
        return None

    result = _select_adaptive_result(intent, candidates, used_set, target_duration)
    report["exact_subject_candidates"] = _exact_subject_candidate_count(candidates, intent)
    report["confidence_after_expansion"] = result.confidence
    if not result.selected_candidate:
        _ADAPTIVE_SEARCH_DIAGNOSTICS[idx] = report
        return None

    continuity_reason = ""
    if continuity_engine is not None and continuity_state is not None:
        result, continuity_reason = continuity_engine.prefer_continuity(
            intent,
            candidates,
            result,
            continuity_state,
            used_provider_ids=set(used_set or []),
            target_duration_sec=target_duration,
            output_width=WIDTH,
            output_height=HEIGHT,
            scene_index=idx,
        )
        if result.selected_candidate:
            continuity_state.record(
                idx,
                identity_from_candidate(result.selected_candidate),
                reason=continuity_reason,
            )

    candidate = result.selected_candidate
    report["final_provider_selected"] = candidate.provider
    report["selected_provider_id"] = candidate.provider_id
    report["selected_query"] = candidate.query
    report["continuity_reason"] = continuity_reason
    _ADAPTIVE_SEARCH_DIAGNOSTICS[idx] = report
    _remember_media_selection(idx, result, "adaptive")
    if continuity_reason:
        _MEDIA_SELECTION_DIAGNOSTICS.setdefault(idx, {}).setdefault("selection", {})[
            "continuity_reason"
        ] = continuity_reason

    out_path = OUT_DIR / f"broll_{idx}{_candidate_extension(candidate)}"
    print(
        f"    [Adaptive] Selected {candidate.provider}:{candidate.provider_id} "
        f"confidence={result.confidence} exact={report['exact_subject_candidates']}"
    )
    if candidate.provider == "yt_clip":
        if not _yt_clip_budget_available():
            print(
                f"    [YTClip] scene budget exhausted "
                f"({_YT_CLIP_SCENES_USED}/{_yt_clip_max_scenes()}); skipping yt_clip for this scene."
            )
            return None
        segment_offset = None
        if continuity_state is not None:
            candidate_identity = identity_from_candidate(candidate)
            prior_uses = [
                scene_idx
                for scene_idx, scene_identity in continuity_state.scene_sources.items()
                if scene_idx != idx and scene_identity.matches(candidate_identity)
            ]
            if prior_uses:
                segment_offset = 5.0 + (len(prior_uses) * (target_duration + 3.0))
        out = fetch_yt_clip_video(
            [candidate.query] if candidate.query else (plan_queries or []),
            idx,
            used_set,
            target_duration=target_duration,
            fallback=fallback,
            narration=narration,
            intent=intent,
            clip_source=candidate.download_url or candidate.url,
            segment_offset=segment_offset,
        )
        if _valid_media_path(out):
            _YT_CLIP_SCENES_USED += 1
            used_set.add(candidate.dedup_key)
            return out
        return None
    if _download_to(candidate.download_url, out_path):
        used_set.add(candidate.dedup_key)
        return out_path
    return None


def _select_local_media(
    queries, fallback, narration, local_media, idx, used_set, target_duration,
    threshold=0.5, intent=None,
):
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidates = [
        candidate_from_local_path(path, queries[0] if queries else fallback)
        for path in local_media
    ]
    cand = _select_candidate_for_provider(
        "Local",
        idx,
        candidates,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=max(0.5, min(9.0, float(threshold) * 8.0)),
    )
    if not cand or not cand.local_path:
        return None
    used_set.add(str(cand.local_path))
    used_set.add(cand.dedup_key)
    return cand.local_path


def fetch_pexels_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Pexels for a portrait video matching any of the queries.
    Returns Path to the downloaded mp4, or None."""
    if not PEXELS_API_KEY:
        return None

    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    orientation = "landscape" if WIDTH > HEIGHT else "portrait"
    candidate_pool = _pexels_video_candidates(queries, fallback, orientation=orientation)
    candidate = _select_candidate_for_provider(
        "Pexels",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.mp4"
    print(f"    [Pexels] Query: {candidate.query!r}  video_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def fetch_pixabay_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Pixabay for a vertical video. Returns Path or None.

    Pixabay's video API mirrors their image API. Free key from
    https://pixabay.com/api/docs/  Free tier: ~100 requests/min, plenty.
    """
    if not PIXABAY_API_KEY or "your_pixabay" in PIXABAY_API_KEY:
        return None

    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = _pixabay_video_candidates(
        queries,
        fallback,
        allow_landscape=WIDTH >= HEIGHT,
    )
    candidate = _select_candidate_for_provider(
        "Pixabay",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.mp4"
    print(f"    [Pixabay] Query: {candidate.query!r}  hit_id={candidate.provider_id}  {candidate.width}x{candidate.height}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def fetch_nasa_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search the NASA Image and Video Library for a video clip or still image.
    Returns Path or None. No API key required.

    Docs: https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf
    """
    import requests

    def search(q):
        try:
            r = requests.get(
                "https://images-api.nasa.gov/search",
                params={"q": q, "media_type": "video,image"},
                timeout=30,
            )
            r.raise_for_status()
            return (r.json().get("collection", {}) or {}).get("items", []) or []
        except Exception as e:
            print(f"    [NASA] search failed for {q!r}: {_safe_diagnostic(e)}")
            return []

    def get_asset_url(nasa_id, media_type="video"):
        # Each item exposes an asset manifest at /asset/<id>
        try:
            r = requests.get(f"https://images-api.nasa.gov/asset/{nasa_id}", timeout=30)
            r.raise_for_status()
            items = (r.json().get("collection", {}) or {}).get("items", []) or []
            # NASA returns multiple renditions: ~mobile, ~medium, ~large, ~orig.
            # Prefer "medium" or "small" mp4 (avoid huge "~orig" downloads).
            if media_type == "video":
                mp4s = [it["href"] for it in items if it.get("href", "").lower().endswith(".mp4")]
                if mp4s:
                    preferred = next((u for u in mp4s if "~medium" in u or "~small" in u), None)
                    return preferred or mp4s[0], False
            image_urls = [
                it["href"]
                for it in items
                if it.get("href", "").lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png"))
            ]
            if not image_urls:
                return None, False
            preferred = next((u for u in image_urls if "~large" in u or "~medium" in u), None)
            return preferred or image_urls[0], True
        except Exception as e:
            print(f"    [NASA] asset lookup failed: {_safe_diagnostic(e)}")
            return None, False

    intent = intent or _selection_intent(
        queries,
        fallback=fallback or (queries[0] if queries else ""),
        narration=narration,
        idx=idx,
    )
    candidate_pool = []
    for q in queries:
        items = search(q)
        for item in items[:5]:
            data = (item.get("data") or [{}])[0]
            nasa_id = data.get("nasa_id")
            if not nasa_id:
                continue
            url, is_image = get_asset_url(nasa_id, data.get("media_type") or "video")
            if not url:
                continue
            candidate_pool.append(candidate_from_nasa_item(item, url, q, is_image=is_image))
    candidate = _select_candidate_for_provider(
        "NASA",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.0,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}{'.jpg' if candidate.is_image else '.mp4'}"
    print(f"    [NASA] Query: {candidate.query!r}  nasa_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path, timeout=60, max_bytes=120 * 1024 * 1024):
        return out_path
    return None


def _json_items(payload):
    """Return the first list-like item collection from common provider JSON shapes."""

    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "videos", "data", "media", "hits"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    collection = payload.get("collection")
    if isinstance(collection, dict) and isinstance(collection.get("items"), list):
        return collection["items"]
    return []


def _remote_item_from_provider(provider, item, query):
    """Map common stock-provider JSON fields into the normalized candidate shape."""

    if not isinstance(item, dict):
        return None
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    video_files = item.get("video_files") if isinstance(item.get("video_files"), list) else []
    best_file = None
    if video_files:
        best_file = max(
            video_files,
            key=lambda file_data: int(file_data.get("height") or file_data.get("width") or 0),
        )
    download_url = (
        item.get("download_url")
        or item.get("downloadUrl")
        or item.get("video_url")
        or item.get("videoUrl")
        or item.get("url")
        or item.get("src")
        or source.get("url")
        or (best_file or {}).get("link")
    )
    if not download_url:
        return None
    media_type = str(item.get("media_type") or item.get("type") or item.get("mime") or "").lower()
    url_path = str(download_url).lower().split("?", 1)[0]
    is_image = bool(item.get("is_image")) or media_type.startswith("image") or url_path.endswith((".jpg", ".jpeg", ".png", ".webp"))
    return {
        "provider_asset_id": item.get("id") or item.get("uuid") or item.get("slug") or download_url,
        "title": item.get("title") or item.get("name") or item.get("slug") or "",
        "description": item.get("description") or item.get("tags") or "",
        "source_url": item.get("source_url") or item.get("page_url") or item.get("html_url") or item.get("url") or "",
        "download_url": download_url,
        "duration_sec": item.get("duration") or item.get("duration_sec"),
        "width": item.get("width") or (best_file or {}).get("width"),
        "height": item.get("height") or (best_file or {}).get("height"),
        "license": item.get("license") or item.get("license_name") or provider,
        "attribution": item.get("attribution") or item.get("author") or item.get("user") or provider,
        "capability": item.get("capability", ""),
        "is_image": is_image,
    }


def _fetch_json_stock_provider(
    provider,
    endpoint,
    queries,
    idx,
    used_set,
    *,
    target_duration=5.0,
    fallback="",
    narration="",
    headers=None,
    params_extra=None,
    license_name="",
    intent=None,
):
    """Fetch a provider whose configured endpoint returns JSON media candidates."""

    import requests

    if not endpoint:
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = _json_stock_candidates(
        provider,
        endpoint,
        queries,
        fallback=fallback,
        headers=headers,
        params_extra=params_extra,
        license_name=license_name,
    )
    candidate = _select_candidate_for_provider(
        provider.title(),
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}{'.jpg' if candidate.is_image else '.mp4'}"
    print(f"    [{provider.title()}] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def fetch_mixkit_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch Mixkit results from a configured JSON endpoint.

    Mixkit does not expose a stable public API here, so production use is
    intentionally opt-in via MIXKIT_API_URL.
    """

    return _fetch_json_stock_provider(
        "mixkit",
        MIXKIT_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        license_name="Mixkit License",
        intent=intent,
    )


def fetch_coverr_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch Coverr videos via search metadata and signed MP4 URLs."""

    if not (COVERR_API_URL and COVERR_API_KEY):
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate = _select_candidate_for_provider(
        "Coverr",
        idx,
        _coverr_candidates(queries, fallback=fallback),
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.mp4"
    print(f"    [Coverr] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path, timeout=90, max_bytes=120 * 1024 * 1024):
        return out_path
    return None


def fetch_vecteezy_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch Vecteezy videos via the account-scoped V2 content API."""

    if not (VECTEEZY_API_URL and VECTEEZY_API_KEY and VECTEEZY_ACCOUNT_ID):
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate = _select_candidate_for_provider(
        "Vecteezy",
        idx,
        _vecteezy_candidates(queries, fallback=fallback),
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.mp4"
    print(f"    [Vecteezy] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path, timeout=90, max_bytes=120 * 1024 * 1024):
        return out_path
    return None


def fetch_videvo_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch Videvo results from a configured JSON endpoint and API key."""

    if not (VIDEVO_API_URL and VIDEVO_API_KEY):
        return None
    return _fetch_json_stock_provider(
        "videvo",
        VIDEVO_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        headers={"Authorization": f"Bearer {VIDEVO_API_KEY}"},
        license_name="Videvo license varies by clip",
        intent=intent,
    )


def fetch_yt_clip_video(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
    clip_source=None,
    segment_offset=None,
):
    """Fetch short YouTube clip via yt-dlp fallback."""
    from autovideo.providers.stock import fetch_yt_clip
    entity = ""
    constraints: list[str] = []
    if intent is not None:
        entity = (
            getattr(intent, "requested_entity", "")
            or getattr(intent, "primary_subject", "")
            or ""
        )
        for attr in ("environment_terms", "action_terms", "entity_required_terms"):
            values = tuple(getattr(intent, attr, ()) or ())
            constraints.extend(str(v).strip() for v in values if str(v or "").strip())
    out = fetch_yt_clip(
        queries,
        idx,
        OUT_DIR,
        target_duration=target_duration,
        used_set=used_set,
        expected_entity=str(entity).strip() or None,
        constraints=constraints,
        source_url=str(clip_source or "").strip() or None,
        segment_offset_sec=segment_offset,
    )
    if out and out.exists() and out.stat().st_size > 0:
        _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
            **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
            "selection": {
                "query": queries[0] if queries else fallback,
                "provider": "yt_clip",
                "provider_id": out.stem,
                "score": 0.6,
                "confidence": "fallback",
                "confidence_level": "MEDIUM",
                "required_constraints": list(constraints),
                "canonical_entity": str(entity).strip() or "",
                "fallback_level": "provider",
                "yt_scene_usage": _YT_CLIP_SCENES_USED + 1,
                "warnings": [],
                "rejection_reasons": [],
                "candidate_count": 1,
                "score_breakdown": {},
            }
        }
        return out
    return None


def fetch_noaa_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch NOAA scientific media from a configured JSON endpoint."""

    return _fetch_json_stock_provider(
        "noaa",
        NOAA_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        headers={"User-Agent": NOAA_USER_AGENT},
        license_name="NOAA public domain / usage varies",
        intent=intent,
    )


def fetch_esa_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch ESA media from a configured JSON endpoint."""

    return _fetch_json_stock_provider(
        "esa",
        ESA_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        license_name="ESA media usage guidelines",
        intent=intent,
    )


def fetch_usgs_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch USGS scientific/geology media from a configured JSON endpoint."""

    return _fetch_json_stock_provider(
        "usgs",
        USGS_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        license_name="USGS public domain / usage varies",
        intent=intent,
    )


def _smithsonian_candidate_from_row(row, query):
    """Normalize one Smithsonian Open Access row into an image candidate.

    Real response shape (verified 2026-07-11):
      row.content.descriptiveNonRepeating.online_media.media[].resources[]
    Only rows with usable online media are returned; text-only records are skipped.
    """
    if not isinstance(row, dict):
        return None
    dnr = row.get("content", {}).get("descriptiveNonRepeating", {}) or {}
    online = dnr.get("online_media")
    if not isinstance(online, dict):
        return None
    media_items = online.get("media") or []
    if not media_items:
        return None
    for media in media_items:
        if not isinstance(media, dict):
            continue
        access = ((media.get("usage") or {}).get("access") or "").upper()
        if access and access not in {"CC0", "PUBLIC DOMAIN"}:
            continue
        # Prefer the "High-resolution JPEG" resource; fall back to first .jpg.
        resources = media.get("resources") or []
        picked = None
        for res in resources:
            if not isinstance(res, dict):
                continue
            label = (res.get("label") or "").lower()
            url = res.get("url") or ""
            if not url:
                continue
            if "high-resolution" in label and url.lower().endswith((".jpg", ".jpeg", ".png")):
                picked = res
                break
        if not picked:
            for res in resources:
                if isinstance(res, dict) and str(res.get("url", "")).lower().endswith((".jpg", ".jpeg", ".png")):
                    picked = res
                    break
        if not picked:
            # Content URL is the delivery service; may resolve to an image.
            content_url = media.get("content")
            if content_url:
                picked = {"url": content_url}
        if not picked:
            continue
        width = picked.get("width")
        height = picked.get("height")
        title = (dnr.get("title") or {}).get("content") or row.get("title") or ""
        return candidate_from_remote_item(
            "smithsonian",
            {
                "provider_asset_id": row.get("id") or row.get("url") or picked.get("url"),
                "title": title,
                "description": media.get("extDescrAccessibility") or media.get("altTextAccessibility") or "",
                "source_url": dnr.get("record_link") or row.get("url", ""),
                "download_url": picked["url"],
                "license": "CC0" if access == "CC0" else "Smithsonian Open Access / rights vary",
                "attribution": dnr.get("data_source") or "Smithsonian",
                "is_image": True,
                "capability": "commons_media",
                "width": int(width) if isinstance(width, (int, str)) and str(width).isdigit() else None,
                "height": int(height) if isinstance(height, (int, str)) and str(height).isdigit() else None,
            },
            query,
        )
    return None


def fetch_smithsonian_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search the Smithsonian Open Access API for CC0 images.

    Docs: https://edan.si.edu/openaccess/apidocs/
    Endpoint: https://api.si.edu/openaccess/api/v1.0/search
    Auth: api_key query parameter (api.data.gov key).
    """
    import requests

    if not (SMITHSONIAN_API_URL and SMITHSONIAN_API_KEY):
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = []
    session = requests.Session()
    for q in queries:
        try:
            response = session.get(
                SMITHSONIAN_API_URL,
                params={"q": q, "rows": 20, "api_key": SMITHSONIAN_API_KEY},
                timeout=30,
            )
            response.raise_for_status()
            rows = (response.json().get("response") or {}).get("rows") or []
        except Exception as e:
            print(f"    [Smithsonian] search failed for {q!r}: {_safe_diagnostic(e)}")
            continue
        for row in rows[:12]:
            candidate = _smithsonian_candidate_from_row(row, q)
            if candidate:
                candidate_pool.append(candidate)
    candidate = _select_candidate_for_provider(
        "Smithsonian",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.5,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.jpg"
    print(f"    [Smithsonian] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def fetch_nps_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch National Park Service media from a configured JSON endpoint."""

    return _fetch_json_stock_provider(
        "nps",
        NPS_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        license_name="National Park Service public domain / usage varies",
        intent=intent,
    )


def fetch_usfws_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch US Fish & Wildlife Service media from a configured JSON endpoint."""

    return _fetch_json_stock_provider(
        "usfws",
        USFWS_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        license_name="USFWS public domain / usage varies",
        intent=intent,
    )


def _europeana_candidate_from_item(item, query):
    """Normalize one Europeana item into an image candidate.

    Real response shape (verified 2026-07-11):
      item.edmIsShownBy[0]  - direct URL to the primary image
      item.edmPreview[0]    - thumbnail through Europeana thumbnail service
      item.rights[0]        - license URL (CC-BY, CC0, PDM, etc.)
      item.title[0]         - display title
      item.dataProvider[0]  - originating institution
      item.type             - "IMAGE" / "SOUND" / "TEXT" / "VIDEO"
    """
    if not isinstance(item, dict):
        return None
    if (item.get("type") or "").upper() != "IMAGE":
        return None
    shown_by = item.get("edmIsShownBy") or []
    preview = item.get("edmPreview") or []
    download_url = ""
    if isinstance(shown_by, list) and shown_by:
        download_url = str(shown_by[0])
    if not download_url and isinstance(preview, list) and preview:
        download_url = str(preview[0])
    if not download_url:
        return None
    if not download_url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
        # Some providers return a service URL that resolves to an image
        # (Wellcome IIIF, Rijksmuseum). We accept it and let the downloader
        # follow redirects; the file extension will be added at save time.
        pass
    title_list = item.get("title") or []
    rights_list = item.get("rights") or []
    provider_list = item.get("dataProvider") or []
    return candidate_from_remote_item(
        "europeana",
        {
            "provider_asset_id": item.get("id") or item.get("guid") or download_url,
            "title": (title_list[0] if isinstance(title_list, list) and title_list else "") or "",
            "description": " ".join(item.get("dcDescription", []) if isinstance(item.get("dcDescription"), list) else []),
            "source_url": item.get("guid") or (item.get("edmIsShownAt") or [""])[0] or "",
            "download_url": download_url,
            "license": (rights_list[0] if isinstance(rights_list, list) and rights_list else "Europeana rights vary by item") or "",
            "attribution": (provider_list[0] if isinstance(provider_list, list) and provider_list else "Europeana") or "",
            "is_image": True,
            "capability": "commons_media",
        },
        query,
    )


def _europeana_candidates(queries, *, timeout_sec=30, raise_errors=False):
    """Return reusable Europeana image candidates without downloading them."""

    import requests

    if not (EUROPEANA_API_URL and EUROPEANA_API_KEY):
        return []
    candidate_pool = []
    session = requests.Session()
    try:
        for query in queries:
            try:
                response = session.get(
                    EUROPEANA_API_URL,
                    params={
                        "wskey": EUROPEANA_API_KEY,
                        "query": query,
                        "rows": 20,
                        "media": "true",
                        "reusability": "open",
                        "type": "IMAGE",
                    },
                    timeout=timeout_sec,
                )
                response.raise_for_status()
                items = response.json().get("items") or []
            except Exception as exc:
                print(f"    [Europeana] search failed for {query!r}: {_safe_diagnostic(exc)}")
                if raise_errors:
                    raise
                continue
            for item in items[:12]:
                candidate = _europeana_candidate_from_item(item, query)
                if candidate:
                    candidate_pool.append(candidate)
    finally:
        session.close()
    return candidate_pool


def fetch_europeana_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Europeana for open-licensed images.

    Docs: https://pro.europeana.eu/page/apis
    Endpoint: https://api.europeana.eu/record/v2/search.json
    Auth: wskey query parameter.
    Filters: media=true, reusability=open (CC0 / CC-BY / PDM only).
    """
    if not (EUROPEANA_API_URL and EUROPEANA_API_KEY):
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = _europeana_candidates(queries)
    candidate = _select_candidate_for_provider(
        "Europeana",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.5,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.jpg"
    print(f"    [Europeana] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def _internet_archive_download_url(identifier, file_entry):
    """Build a direct download URL from an Internet Archive file entry."""
    name = file_entry.get("name") or ""
    if not name:
        return ""
    return f"https://archive.org/download/{identifier}/{name}"


def _internet_archive_pick_video_file(files):
    """Pick the best downloadable video file from an IA metadata files list.

    Priority: h.264 derivative mp4 (small, fast) > original HD mov > any mp4.
    """
    if not isinstance(files, list):
        return None
    # h.264 derivative mp4 (usually 854x480, ~5-10 MB per minute)
    for f in files:
        if not isinstance(f, dict):
            continue
        if f.get("format") == "h.264" and f.get("name", "").endswith(".mp4"):
            return f
    # Any other mp4
    for f in files:
        if isinstance(f, dict) and f.get("name", "").endswith(".mp4"):
            return f
    # Fall back to original .mov if present (larger)
    for f in files:
        if isinstance(f, dict) and f.get("name", "").endswith(".mov") and f.get("source") == "original":
            return f
    return None


def _internet_archive_pick_image_file(files):
    """Pick the best downloadable image file from an IA metadata files list."""
    if not isinstance(files, list):
        return None
    # Prefer original jpg/png over derivative thumbnails.
    for f in files:
        if not isinstance(f, dict):
            continue
        name = f.get("name", "")
        if name.endswith((".jpg", ".jpeg", ".png")) and f.get("source") == "original":
            return f
    for f in files:
        if isinstance(f, dict) and f.get("name", "").endswith((".jpg", ".jpeg", ".png")):
            return f
    return None


def fetch_internet_archive_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Internet Archive advanced-search then fetch downloadable media.

    Docs: https://archive.org/developers/index-apis.html
    Search: https://archive.org/advancedsearch.php (no auth)
    Metadata: https://archive.org/metadata/{identifier}
    Download: https://archive.org/download/{identifier}/{filename}
    """
    import requests

    if not INTERNET_ARCHIVE_ENABLED:
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    session = requests.Session()
    candidate_pool = []
    for q in queries:
        # Search movies first (video is preferred over images for B-roll).
        search_query = f'title:({q}) AND mediatype:(movies)'
        try:
            search_resp = session.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": search_query,
                    "fl[]": ["identifier", "title", "mediatype", "licenseurl", "creator", "description"],
                    "rows": 8,
                    "output": "json",
                },
                timeout=30,
            )
            search_resp.raise_for_status()
            docs = (search_resp.json().get("response") or {}).get("docs") or []
        except Exception as e:
            print(f"    [Internet Archive] search failed for {q!r}: {_safe_diagnostic(e)}")
            continue
        for doc in docs[:5]:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            # Skip YouTube mirror uploads — those files are frequently unavailable.
            if identifier.startswith("youtube-") or identifier.startswith("youtube--"):
                continue
            try:
                meta_resp = session.get(
                    f"https://archive.org/metadata/{identifier}",
                    timeout=30,
                )
                meta_resp.raise_for_status()
                meta = meta_resp.json()
            except Exception as e:
                print(f"    [Internet Archive] metadata failed for {identifier!r}: {_safe_diagnostic(e)}")
                continue
            files = meta.get("files") or []
            video_file = _internet_archive_pick_video_file(files)
            picked, is_image = (video_file, False) if video_file else (_internet_archive_pick_image_file(files), True)
            if not picked:
                continue
            download_url = _internet_archive_download_url(identifier, picked)
            if not download_url:
                continue
            duration = None
            if not is_image:
                length = picked.get("length")
                if isinstance(length, str):
                    try:
                        duration = float(length)
                    except (TypeError, ValueError):
                        duration = None
            candidate = candidate_from_remote_item(
                "internet_archive",
                {
                    "provider_asset_id": identifier,
                    "title": doc.get("title") or (meta.get("metadata") or {}).get("title") or identifier,
                    "description": doc.get("description") or "",
                    "source_url": f"https://archive.org/details/{identifier}",
                    "download_url": download_url,
                    "license": doc.get("licenseurl") or (meta.get("metadata") or {}).get("licenseurl") or "Internet Archive rights vary by item",
                    "attribution": doc.get("creator") or (meta.get("metadata") or {}).get("creator") or "Internet Archive",
                    "is_image": is_image,
                    "capability": "archive_footage" if not is_image else "history_images",
                    "duration": duration,
                    "width": int(picked.get("width")) if str(picked.get("width", "")).isdigit() else None,
                    "height": int(picked.get("height")) if str(picked.get("height", "")).isdigit() else None,
                },
                q,
            )
            if candidate:
                candidate_pool.append(candidate)
    candidate = _select_candidate_for_provider(
        "Internet Archive",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.5,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    ext = ".jpg" if candidate.is_image else ".mp4"
    out_path = OUT_DIR / f"broll_{idx}{ext}"
    print(f"    [Internet Archive] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path):
        return out_path
    return None


def fetch_flickr_commons_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Fetch Flickr Commons media from a configured endpoint and API key."""

    if not (FLICKR_COMMONS_API_URL and FLICKR_API_KEY):
        return None
    return _fetch_json_stock_provider(
        "flickr_commons",
        FLICKR_COMMONS_API_URL,
        queries,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        params_extra={"api_key": FLICKR_API_KEY, "license": "4,5,6,7,8,9,10"},
        license_name="Flickr Commons license varies by item",
        intent=intent,
    )


def _loc_candidate_from_result(result, query):
    """Normalize one Library of Congress search result into an image candidate."""

    if not isinstance(result, dict):
        return None
    image_urls = result.get("image_url") or result.get("image_urls") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    image_urls = [
        str(url)
        for url in image_urls
        if str(url).lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    if not image_urls:
        return None
    # The LOC API commonly orders thumbnails before larger derivatives.
    download_url = image_urls[-1]
    return candidate_from_remote_item(
        "loc",
        {
            "provider_asset_id": result.get("id") or result.get("number") or result.get("url") or download_url,
            "title": result.get("title") or "",
            "description": result.get("description") or result.get("subject") or "",
            "source_url": result.get("url") or result.get("item", {}).get("url") or "",
            "download_url": download_url,
            "license": result.get("rights") or "Library of Congress rights vary by item",
            "attribution": "Library of Congress",
            "is_image": True,
            "capability": "history_images",
        },
        query,
    )


def fetch_library_of_congress_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Library of Congress JSON search for archive images."""

    import requests

    if not (ARCHIVE_PROVIDERS_ENABLED and LOC_API_URL):
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = []
    session = requests.Session()
    for q in queries:
        try:
            response = session.get(
                LOC_API_URL,
                params={"fo": "json", "q": q, "c": 20},
                timeout=30,
            )
            response.raise_for_status()
            results = response.json().get("results", []) or []
        except Exception as e:
            print(f"    [LOC] search failed for {q!r}: {_safe_diagnostic(e)}")
            continue
        for result in results[:12]:
            candidate = _loc_candidate_from_result(result, q)
            if candidate:
                candidate_pool.append(candidate)
    candidate = _select_candidate_for_provider(
        "LOC",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.5,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    out_path = OUT_DIR / f"broll_{idx}.jpg"
    print(f"    [LOC] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path, timeout=60, max_bytes=80 * 1024 * 1024):
        return out_path
    return None


def _wikimedia_candidate_from_page(page, query):
    """Normalize a Wikimedia Commons page result into a media candidate."""

    title = str(page.get("title") or "")
    imageinfo = (page.get("imageinfo") or [{}])[0]
    mime = str(imageinfo.get("mime") or "")
    original_url = str(imageinfo.get("url") or "")
    is_image_result = mime.startswith("image/")
    if not title or not original_url or not (mime.startswith("video/") or is_image_result):
        return None
    media_signature = " ".join([title, mime, original_url]).lower()
    if any(token in media_signature for token in ("svg", "tiff", ".tif", "djvu", ".djv", ".pdf")):
        return None
    download_url = str(imageinfo.get("thumburl") or original_url) if is_image_result else original_url
    width = imageinfo.get("thumbwidth") or imageinfo.get("width")
    height = imageinfo.get("thumbheight") or imageinfo.get("height")
    ext = imageinfo.get("extmetadata") or {}

    def ext_value(name):
        value = ext.get(name) or {}
        return str(value.get("value") or "")

    return candidate_from_remote_item(
        "wikimedia",
        {
            "provider_asset_id": title.replace("File:", ""),
            "title": title.replace("File:", ""),
            "description": ext_value("ImageDescription"),
            "source_url": imageinfo.get("descriptionurl") or original_url,
            "download_url": download_url,
            "width": width,
            "height": height,
            "license": ext_value("LicenseShortName") or ext_value("UsageTerms"),
            "attribution": ext_value("Artist") or ext_value("Credit"),
            "is_image": is_image_result,
            "capability": "history_images" if is_image_result else "history_video",
        },
        query,
    )


def fetch_wikimedia_media(
    queries, idx, used_set, target_duration=5.0, fallback="", narration="", intent=None,
):
    """Search Wikimedia Commons for historical, educational, or diagram media."""

    if not ENABLE_WIKIMEDIA_COMMONS:
        return None
    intent = intent or _selection_intent(queries, fallback=fallback, narration=narration, idx=idx)
    candidate_pool = _wikimedia_candidates(queries, timeout_sec=30)
    candidate = _select_candidate_for_provider(
        "Wikimedia",
        idx,
        candidate_pool,
        intent=intent,
        used_set=used_set,
        target_duration=target_duration,
        minimum_score=0.5,
    )
    if not candidate:
        return None
    used_set.add(candidate.dedup_key)
    suffix = ".jpg" if candidate.is_image else ".mp4"
    out_path = OUT_DIR / f"broll_{idx}{suffix}"
    print(f"    [Wikimedia] Query: {candidate.query!r}  asset_id={candidate.provider_id}")
    if _download_to(candidate.download_url, out_path, timeout=60, max_bytes=120 * 1024 * 1024):
        return out_path
    return None


def _load_persistent_used():
    """Load cross-video used_set from disk so clips aren't repeated across runs."""
    try:
        source_path = (
            PERSISTENT_USED_PATH
            if PERSISTENT_USED_PATH.exists()
            else LEGACY_PERSISTENT_USED_PATH
        )
        if source_path.exists():
            data = json.loads(source_path.read_text())
            if not isinstance(data, list):
                return set()
            return {
                str(item)
                for item in data
                if _stable_used_key(item)
            }
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _stable_used_key(value):
    text = str(value or "").strip()
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return False
    if not re.match(r"^[a-z][a-z0-9_]*:.+", text, flags=re.IGNORECASE):
        return False
    return not text.lower().startswith((
        "local:",
        "hybrid_composer:",
        "gemini_image:",
        "pollinations:",
    ))


def _save_persistent_used(s):
    try:
        trimmed = sorted(
            {
                str(value)
                for value in (*_load_persistent_used(), *s)
                if _stable_used_key(value)
            },
            key=str,
        )[-500:]
        PERSISTENT_USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        PERSISTENT_USED_PATH.write_text(json.dumps(trimmed))
    except (OSError, TypeError):
        pass


def _valid_media_path(path):
    """Return True only for paths that can safely enter the Timeline."""

    if not path:
        return False
    try:
        media_path = Path(path)
    except TypeError:
        return False
    try:
        if not media_path.exists() or not media_path.is_file() or media_path.stat().st_size <= 0:
            return False
        if is_image(media_path):
            return _valid_raster_image(media_path)
        return True
    except OSError:
        return False


def _dedupe_runtime_queries(queries):
    seen = set()
    ordered = []
    for query in queries:
        cleaned = " ".join(str(query or "").split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)
    return ordered


def fetch_broll(queries, idx, fallback, local_media=None, narration="", used_set=None,
                hybrid=False, threshold=0.5, dalle=False, target_duration=5.0,
                no_interactive=False, shot_intent=None,
                visual_grammar_engine=None, visual_grammar_decision=None,
                provider_query_variants=None, scene_constraints=None,
                continuity_engine=None, continuity_state=None):
    """Chain b-roll sources: local -> generated image (opt) -> Pexels -> Pixabay -> NASA (space).

    Uses a persistent cross-video used_set so clips aren't repeated across runs.
    """
    used_set = used_set if used_set is not None else _load_persistent_used()
    global _BROAD_FALLBACK_SCENES, _YT_CLIP_SCENES_USED
    if idx == 0:
        _BROAD_FALLBACK_SCENES = 0
        _YT_CLIP_SCENES_USED = 0
    if isinstance(queries, str):
        queries = [queries]
    queries = [q for q in queries if q]
    keyword = queries[0] if queries else fallback
    if visual_grammar_engine is None:
        visual_grammar_engine = VisualGrammarEngine(topic=fallback, total_scenes=max(1, idx + 1))
    if visual_grammar_decision is None:
        visual_grammar_decision = visual_grammar_engine.decide(
            scene_index=idx,
            narration=narration,
            queries=tuple(queries),
            shot_intent=shot_intent,
        )
        queries = _dedupe_runtime_queries([*visual_grammar_decision.repaired_queries, *queries])
    canonical_intent = _selection_intent(
        queries,
        fallback=fallback,
        narration=narration,
        idx=idx,
        shot_intent=shot_intent,
    )
    strategy = _build_search_strategy(
        queries,
        fallback,
        narration,
        local_media=local_media,
        idx=idx,
        intent=canonical_intent,
    )
    _remember_media_planning(idx, strategy)

    # 1. Local media
    if local_media:
        match = _select_local_media(
            queries,
            fallback,
            narration,
            local_media,
            idx,
            used_set,
            target_duration,
            threshold=threshold,
            intent=canonical_intent,
        )
        if match:
            print(f"    [Local] Using: {match.name}")
            visual_grammar_engine.register_real_asset(provider="local")
            return match
        elif hybrid or dalle:
            print(f"    [Local] No strong match for '{keyword}' (below threshold).")

    # 2. Gemini image generation (when explicitly requested via the legacy --dalle flag)
    if dalle:
        dalle_img = generate_gemini_image(keyword, idx)
        if dalle_img:
            _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                "selection": {
                    "query": keyword,
                    "provider": "gemini_image",
                    "provider_id": Path(dalle_img).name,
                    "score": None,
                    "confidence": "fallback",
                    "warnings": ["explicit image fallback"],
                    "rejection_reasons": [],
                    "candidate_count": 0,
                    "score_breakdown": {},
                }
            }
            return dalle_img
        print(f"    [Gemini Image] failed; falling through to stock sources.")

    # 3. Remote sources are ordered by required visual capabilities, not by
    # hard-coded topic branches. Existing wrappers still own provider I/O.
    adaptive_out = _fetch_adaptive_broll(
        strategy,
        idx=idx,
        fallback=fallback,
        narration=narration,
        used_set=used_set,
        target_duration=target_duration,
        intent=canonical_intent,
        provider_query_variants=provider_query_variants,
        continuity_engine=continuity_engine,
        continuity_state=continuity_state,
    )
    if _valid_media_path(adaptive_out):
        _save_persistent_used(used_set)
        selection = (_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}) or {}).get("selection", {})
        _record_post_download_visual_qa(idx, canonical_intent, adaptive_out)
        visual_grammar_engine.register_real_asset(provider=selection.get("provider", "adaptive"))
        return adaptive_out
    if adaptive_out:
        print("    [Adaptive] returned an unrenderable media file; trying legacy provider order.")

    for plan in strategy.provider_plans:
        source = plan.provider_id
        plan_queries = list(
            (provider_query_variants or {}).get(source)
            or plan.queries
            or queries
        )
        if plan.score <= 0:
            continue
        if source == "local":
            continue
        if source == "pexels":
            out = fetch_pexels_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "pixabay":
            out = fetch_pixabay_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "mixkit":
            out = fetch_mixkit_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "coverr":
            out = fetch_coverr_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "vecteezy":
            out = fetch_vecteezy_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "videvo":
            out = fetch_videvo_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "yt_clip":
            if not _yt_clip_budget_available():
                print(
                    f"    [YTClip] scene budget exhausted "
                    f"({_YT_CLIP_SCENES_USED}/{_yt_clip_max_scenes()}); skipping yt_clip for this scene."
                )
                continue
            out = fetch_yt_clip_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
            if _valid_media_path(out):
                _YT_CLIP_SCENES_USED += 1
        elif source == "wikimedia":
            out = fetch_wikimedia_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "noaa":
            out = fetch_noaa_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "usgs":
            out = fetch_usgs_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "usfws":
            out = fetch_usfws_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "loc":
            out = fetch_library_of_congress_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "smithsonian":
            out = fetch_smithsonian_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "nps":
            out = fetch_nps_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "nasa":
            out = fetch_nasa_video(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "europeana":
            out = fetch_europeana_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "flickr_commons":
            out = fetch_flickr_commons_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "internet_archive":
            out = fetch_internet_archive_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "esa":
            out = fetch_esa_media(
                plan_queries,
                idx,
                used_set,
                target_duration=target_duration,
                fallback=fallback,
                narration=narration,
                intent=canonical_intent,
            )
        elif source == "gemini_image":
            if not is_gemini_image_available():
                continue
            print(f"    [Gemini Image] Strategy fallback for '{keyword}'...")
            out = generate_gemini_image(plan_queries[0] if plan_queries else keyword, idx)
            if out:
                _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                    **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                    **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                    "selection": {
                        "query": plan_queries[0] if plan_queries else keyword,
                        "provider": "gemini_image",
                        "provider_id": Path(out).name,
                        "score": None,
                        "confidence": "fallback",
                        "warnings": ["strategy image fallback"],
                        "rejection_reasons": [],
                        "candidate_count": 0,
                        "score_breakdown": {},
                    },
                }
        elif source == "pollinations_image":
            if not is_pollinations_image_available():
                continue
            print(f"    [Pollinations Image] Strategy fallback for '{keyword}'...")
            out = generate_pollinations_image(plan_queries[0] if plan_queries else keyword, idx)
            if out:
                _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                    **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                    **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                    "selection": {
                        "query": plan_queries[0] if plan_queries else keyword,
                        "provider": "pollinations_image",
                        "provider_id": Path(out).name,
                        "score": None,
                        "confidence": "fallback",
                        "warnings": ["strategy generated-image fallback"],
                        "rejection_reasons": [],
                        "candidate_count": 0,
                        "score_breakdown": {},
                    },
                }
        else:
            continue
        if _valid_media_path(out):
            _save_persistent_used(used_set)
            _record_post_download_visual_qa(idx, canonical_intent, out)
            if source in {"gemini_image", "pollinations_image"}:
                visual_grammar_engine.register_explainer()
            else:
                visual_grammar_engine.register_real_asset(provider=source)
            return out
        if out:
            print(f"    [{source}] returned an unrenderable media file; trying next source.")

    # 4. Hybrid visual composition before plain cards. This keeps the renderer
    # input unchanged: it still receives one local image path for the segment.
    try:
        composition = HybridVisualComposer(width=WIDTH, height=HEIGHT).compose(
            topic=fallback,
            narration=narration,
            queries=queries,
            output_dir=OUT_DIR,
            idx=idx,
            shot_intent=shot_intent,
            grammar_decision=visual_grammar_decision,
        )
    except (OSError, ValueError) as exc:
        print(f"    [Hybrid composer] failed for '{keyword}': {exc}")
    else:
        if _valid_media_path(composition.local_path):
            allowed, grammar_reason = visual_grammar_engine.allow_composition(
                visual_grammar_decision,
                scene_type=composition.plan.scene_type,
            )
            if not allowed:
                print(f"    [Visual grammar] rejected composition: {grammar_reason}")
                _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
                    **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                    "visual_grammar": visual_grammar_decision.to_dict(),
                    "visual_grammar_composition_rejected": grammar_reason,
                }
            else:
                visual_grammar_engine.register_composed_asset(scene_type=composition.plan.scene_type)
                print(
                    "    [Hybrid composer] "
                    f"{composition.plan.scene_type} visual for '{keyword}'."
                )
                _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                    **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                    **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                    "selection": composition.metadata,
                }
                _save_persistent_used(used_set)
                return composition.local_path

    # 5. Auto Gemini Image fallback when stock footage fails (free, no billing)
    if is_gemini_image_available():
        print(f"    [Gemini Image] Stock footage exhausted; generating image for '{keyword}'...")
        gemini_img = generate_gemini_image(keyword, idx)
        if gemini_img:
            _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                "selection": {
                    "query": keyword,
                    "provider": "gemini_image",
                    "provider_id": Path(gemini_img).name,
                    "score": None,
                    "confidence": "fallback",
                    "warnings": ["stock footage exhausted"],
                    "rejection_reasons": [],
                    "candidate_count": 0,
                    "score_breakdown": {},
                }
            }
            visual_grammar_engine.register_explainer()
            _save_persistent_used(used_set)
            return gemini_img

    if is_pollinations_image_available():
        print(f"    [Pollinations Image] Stock footage exhausted; generating image for '{keyword}'...")
        pollinations_img = generate_pollinations_image(keyword, idx)
        if pollinations_img:
            _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                "selection": {
                    "query": keyword,
                    "provider": "pollinations_image",
                    "provider_id": Path(pollinations_img).name,
                    "score": None,
                    "confidence": "fallback",
                    "warnings": ["stock footage exhausted", "generated-image fallback"],
                    "rejection_reasons": [],
                    "candidate_count": 0,
                    "score_breakdown": {},
                }
            }
            visual_grammar_engine.register_explainer()
            _save_persistent_used(used_set)
            return pollinations_img

    if _needs_qr_explainer_fallback(keyword, narration, fallback):
        print(f"    [QR fallback] Creating local explainer image for '{keyword}'...")
        qr_img = _generate_qr_explainer_image(keyword, idx)
        _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
            **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
            "selection": {
                "query": keyword,
                "provider": "gemini_image",
                "provider_id": Path(qr_img).name,
                "score": None,
                "confidence": "fallback",
                "confidence_level": "LOW",
                "portrait_score": 10.0,
                "relevance_score": 7.0,
                "scene_importance": _scene_importance_for_index(idx, narration),
                "selection_reason": "local QR explainer fallback after stock footage failed",
                "rejection_reason": "",
                "fallback_level": "local_explainer",
                "warnings": ["stock footage exhausted", "local QR explainer fallback"],
                "rejection_reasons": [],
                "candidate_count": 0,
                "score_breakdown": {},
            },
        }
        visual_grammar_engine.register_explainer()
        _save_persistent_used(used_set)
        return qr_img

    # A deterministic explainer card is safer than unrelated stock when every
    # source fails the relevance gate. Image scenes receive the existing slow
    # zoom treatment during segment rendering, so unattended runs still finish.
    try:
        explainer_img = _generate_local_explainer_image(keyword, idx)
    except (OSError, ValueError) as exc:
        print(f"    [Local explainer] failed for '{keyword}': {exc}")
    else:
        planning = _MEDIA_PLANNING_DIAGNOSTICS.get(idx, {})
        scene_type = (planning.get("query_plan") or {}).get("scene_type", "")
        _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
            **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
            **planning,
            "selection": {
                "query": keyword,
                "provider": "local",
                "provider_id": Path(explainer_img).name,
                "score": None,
                "confidence": "fallback",
                "confidence_level": "MEDIUM",
                "portrait_score": 10.0,
                "relevance_score": 7.0,
                "evidence_score": 10.0,
                "visual_domain": scene_type,
                "quality_gate_passed": True,
                "scene_importance": _scene_importance_for_index(idx, narration),
                "selection_reason": "domain-safe local explainer after provider rejection",
                "rejection_reason": "",
                "fallback_level": "local_explainer",
                "warnings": ["stock providers failed relevance gate", "local explainer fallback"],
                "rejection_reasons": [],
                "candidate_count": 0,
                "score_breakdown": {},
            },
        }
        visual_grammar_engine.register_explainer()
        _save_persistent_used(used_set)
        return explainer_img

    # 5. Last-resort generic Pexels search using the niche/fallback term.
    # This is the "broad nature shot" safety net so scheduled runs don't die.
    scene_importance = _scene_importance_for_index(idx, narration)
    forbidden_role = scene_importance in {
        SceneImportance.HOOK.value,
        SceneImportance.MAIN_REVEAL.value,
        SceneImportance.CTA.value,
    }
    if forbidden_role:
        print(
            f"    [Broad fallback] blocked for {scene_importance} scene {idx+1}: "
            "generic fallback is not allowed on hook/main-reveal/CTA scenes."
        )
        _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
            "broad_fallback_blocked_reason": (
                f"scene importance {scene_importance} forbids generic broad fallback"
            ),
        }
        return None
    if _BROAD_FALLBACK_SCENES >= _BROAD_FALLBACK_MAX_SCENES:
        print(
            f"    [Constraint fallback] broad fallback budget exhausted "
            f"({_BROAD_FALLBACK_SCENES}/{_BROAD_FALLBACK_MAX_SCENES}); skipping generic fallback."
        )
        _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
            "broad_fallback_blocked_reason": (
                f"per-documentary budget exhausted "
                f"({_BROAD_FALLBACK_SCENES}/{_BROAD_FALLBACK_MAX_SCENES})"
            ),
        }
        return None
    print(f"    [!] No specific footage found for segment {idx+1} ('{keyword}'); trying broad niche search.")
    broad_terms = _broad_fallback_terms(keyword, narration, fallback)
    if scene_constraints and getattr(scene_constraints, "constraints", ()):
        constrained_terms, rejected_terms = scene_constraints.filter_queries(broad_terms)
        if not constrained_terms:
            print("    [Constraint guard] skipped broad fallback that lost mandatory visuals.")
            _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
                **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                "rejected_unconstrained_broad_queries": list(rejected_terms),
            }
            return None
        broad_terms = list(constrained_terms)
    broad_out = fetch_pexels_video(
        broad_terms,
        idx,
        used_set,
        target_duration=target_duration,
        fallback=fallback,
        narration=narration,
        intent=canonical_intent,
    )
    if broad_out:
        print(f"    [Pexels broad] Using domain-safe broad clip for '{fallback}'.")
        _BROAD_FALLBACK_SCENES += 1
        _MEDIA_SELECTION_DIAGNOSTICS.setdefault(idx, {"selection": {}})
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"].setdefault("warnings", [])
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"]["warnings"].append("broad fallback used")
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"]["confidence"] = "low"
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"]["confidence_level"] = "LOW"
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"]["fallback_level"] = "broad"
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"]["broad_fallback_scene_importance"] = scene_importance
        _MEDIA_SELECTION_DIAGNOSTICS[idx]["selection"].setdefault(
            "selection_reason",
            "last-resort broad fallback after specific providers failed",
        )
        if _valid_media_path(broad_out):
            _save_persistent_used(used_set)
            _record_post_download_visual_qa(idx, canonical_intent, broad_out)
            visual_grammar_engine.register_real_asset(provider="pexels")
            return broad_out
        if broad_out:
            print(f"    [Pexels broad] returned a missing or empty media file; continuing fallback.")

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
def _needs_qr_explainer_fallback(keyword, narration="", fallback=""):
    text = " ".join(str(part or "").lower() for part in (keyword, narration, fallback))
    return "qr" in text or "quick response" in text


def _generate_local_explainer_image(keyword, idx):
    """Create a portrait-safe text visual when no stock candidate is trustworthy."""

    cleaned = " ".join(str(keyword or "Visual explanation").replace("-", " ").split())
    display_text = cleaned[:90].title()
    return create_text_card(
        display_text,
        OUT_DIR / f"local_explainer_{idx}.png",
        width=WIDTH,
        height=HEIGHT,
        font_size=78,
        text_color="#FFFFFF",
        bg_color="#101820",
    )


def _generate_qr_explainer_image(keyword, idx):
    """Create a portrait-safe QR explainer card when stock/image providers fail."""
    out_path = OUT_DIR / f"qr_explainer_{idx}.png"
    OUT_DIR.mkdir(exist_ok=True)
    img = Image.new("RGB", (WIDTH, HEIGHT), "#101418")
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 72)
        label_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 42)
        small_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34)
    except (OSError, IOError):
        title_font = ImageFont.load_default()
        label_font = title_font
        small_font = title_font

    title = "QR CODE"
    title_width = draw.textlength(title, font=title_font)
    draw.text(((WIDTH - title_width) / 2, 150), title, fill="#FFFFFF", font=title_font)
    subtitle = str(keyword or "how qr codes work").replace("-", " ").title()[:42]
    subtitle_width = draw.textlength(subtitle, font=small_font)
    draw.text(((WIDTH - subtitle_width) / 2, 245), subtitle, fill="#A7E8FF", font=small_font)

    grid_size = min(int(WIDTH * 0.72), 780)
    cell = grid_size // 21
    grid_size = cell * 21
    left = (WIDTH - grid_size) // 2
    top = int(HEIGHT * 0.28)
    draw.rounded_rectangle(
        [left - 28, top - 28, left + grid_size + 28, top + grid_size + 28],
        radius=28,
        fill="#F8FBFF",
    )

    def finder(cx, cy):
        draw.rectangle([cx, cy, cx + cell * 6, cy + cell * 6], fill="#111111")
        draw.rectangle([cx + cell, cy + cell, cx + cell * 5, cy + cell * 5], fill="#F8FBFF")
        draw.rectangle([cx + cell * 2, cy + cell * 2, cx + cell * 4, cy + cell * 4], fill="#111111")

    finder(left + cell, top + cell)
    finder(left + grid_size - cell * 7, top + cell)
    finder(left + cell, top + grid_size - cell * 7)

    seed = hashlib.sha1(str(keyword or idx).encode("utf-8")).digest()
    for y in range(21):
        for x in range(21):
            in_finder = (
                (x <= 7 and y <= 7)
                or (x >= 14 and y <= 7)
                or (x <= 7 and y >= 14)
            )
            if in_finder:
                continue
            value = seed[(x + y * 21) % len(seed)]
            if (value + x * 3 + y * 5) % 4 in {0, 1}:
                x0 = left + x * cell
                y0 = top + y * cell
                draw.rectangle([x0, y0, x0 + cell - 2, y0 + cell - 2], fill="#111111")

    labels = [
        ("Finder patterns", left + 35, top + grid_size + 70),
        ("Timing grid", left + 35, top + grid_size + 125),
        ("Data + error correction", left + 35, top + grid_size + 180),
    ]
    for text, x, y in labels:
        draw.rounded_rectangle([x, y + 8, x + 22, y + 30], radius=4, fill="#A7E8FF")
        draw.text((x + 42, y), text, fill="#FFFFFF", font=label_font)

    img.save(out_path)
    return out_path


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


def _clip_start_offset(path, idx, segment_duration):
    try:
        source_duration = media_duration(path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0.0
    room = source_duration - segment_duration - 0.5
    if room <= 1.0:
        return 0.0
    return min(room, (idx * 2.3) % room)


def _media_asset_from_path(path, *, source=MediaSource.UNKNOWN, idx=0, metadata=None):
    local_path = Path(path)
    duration = None
    if not is_image(local_path):
        try:
            duration = media_duration(local_path)
        except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
            duration = None
    return MediaAsset(
        local_path=local_path,
        source=source,
        source_id=f"{source.value}:{local_path.name}:{idx}",
        duration_sec=duration,
        is_image=is_image(local_path),
        metadata=dict(metadata or {}),
    )


def _media_asset_from_critical_lock(locked, idx):
    """Build a render asset without losing the locked provider provenance."""

    locked = dict(locked)
    provider = str(locked.get("provider") or "")
    provider_id = str(locked.get("provider_id") or "")
    provenance = {
        "source_url": locked.get("source_url", ""),
        "license": locked.get("license", ""),
        "attribution": locked.get("attribution", ""),
    }
    metadata = {
        "critical_asset_lock": True,
        "critical_asset": locked,
        "provider": provider,
        "provider_asset_id": provider_id,
        **provenance,
        "verified_media": locked.get("verification") or {},
        "selection": {
            "query": locked.get("query", ""),
            "provider": provider,
            "provider_id": provider_id,
            **provenance,
            "score": locked.get("score"),
            "confidence": "verified",
            "confidence_level": "VERIFIED",
            "quality_gate_passed": True,
            "selection_reason": "pre-script frame-verified critical asset lock",
            "fallback_level": "critical_asset_lock",
            "warnings": [],
            "rejection_reasons": [],
            "candidate_count": 1,
            "score_breakdown": {},
        },
    }
    asset = _media_asset_from_path(
        Path(locked.get("local_path") or ""),
        source=_media_source_from_selection(metadata),
        idx=idx,
        metadata=metadata,
    )
    return replace(
        asset,
        source_id=f"{provider}:{provider_id}",
        attribution=provenance,
    )


def _media_source_from_selection(metadata):
    provider = ((metadata or {}).get("selection") or {}).get("provider")
    return {
        "local": MediaSource.LOCAL,
        "pexels": MediaSource.PEXELS,
        "pixabay": MediaSource.PIXABAY,
        "nasa": MediaSource.NASA,
        "gemini_image": MediaSource.GEMINI_IMAGE,
        "pollinations_image": MediaSource.POLLINATIONS_IMAGE,
    }.get(provider, MediaSource.UNKNOWN)


def _source_has_audio_stream(path) -> bool:
    if is_image(path):
        return False
    try:
        output = subprocess.check_output([
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ], text=True, timeout=10).strip()
        return "audio" in output.lower()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False


def _clip_audio_quality_gate(path) -> tuple[bool, str]:
    """Return whether source ambience is safe enough to mix under narration."""
    if not APP_CONFIG.clip_audio.use_clip_audio:
        return False, "disabled"
    if is_image(path):
        return False, "still image has no clip audio"
    if not _source_has_audio_stream(path):
        return False, "no audio stream"
    if not APP_CONFIG.clip_audio.noise_gate:
        return True, "audio stream present; noise gate disabled"
    try:
        proc = subprocess.run([
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-t",
            "3",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"audio probe failed: {type(exc).__name__}"
    probe_text = f"{proc.stdout}\n{proc.stderr}"
    mean_match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", probe_text)
    max_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", probe_text)
    if proc.returncode != 0:
        return False, "audio probe failed"
    mean_volume = float(mean_match.group(1)) if mean_match else None
    max_volume = float(max_match.group(1)) if max_match else None
    if mean_volume is not None and mean_volume <= -55.0:
        return False, "near-silent clip audio"
    if max_volume is not None and max_volume >= -0.2:
        return False, "clip audio is clipping"
    if mean_volume is not None and mean_volume >= -6.0:
        return False, "clip audio too loud for ambience bed"
    return True, "audio stream passed quality gate"


def _record_clip_audio_decision(*, idx: int, broll, extracted: bool, used: bool, reason: str) -> None:
    _AUDIO_MIX_DECISIONS.append(ClipAudioDecision(
        segment_index=idx,
        source_path=str(Path(broll)),
        clip_audio_extracted=extracted,
        clip_audio_used=used,
        clip_audio_muted=not used,
        reason=reason,
        volume=APP_CONFIG.clip_audio.volume if used else 0.0,
        ducking_applied=bool(used and APP_CONFIG.clip_audio.ducking),
        fade_ms=APP_CONFIG.clip_audio.fade_ms,
    ))


def _segment_filter_graph(video_filter: str, *, duration: float, use_clip_audio: bool) -> tuple[str, str]:
    if not use_clip_audio:
        return video_filter, "1:a:0"
    audio_graph, audio_label = clip_audio_filter(
        source_audio_label="[0:a]",
        voice_audio_label="[1:a]",
        duration_sec=float(duration),
        config=APP_CONFIG.clip_audio,
    )
    return f"{video_filter};{audio_graph}", audio_label


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
        _record_clip_audio_decision(
            idx=idx,
            broll=split_clip,
            extracted=False,
            used=False,
            reason="comparison clip audio skipped",
        )
        return out_path

    # Image mode: convert to clip with Ken Burns effect first
    if is_image(broll):
        safe_image = _prepare_raster_image_for_ffmpeg(broll, idx)
        if safe_image is None:
            img_clip = _fallback_color_clip(
                idx,
                duration,
                f"'{Path(broll).name}' is not a renderable image",
            )
        else:
            img_clip = image_to_clip(safe_image, duration, idx)
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
        _record_clip_audio_decision(
            idx=idx,
            broll=broll,
            extracted=False,
            used=False,
            reason="still image has no clip audio",
        )
        return out_path

        if not _valid_raster_image(broll):
            print(f"[!] Segment {idx}: '{Path(broll).name}' is not a valid image — using fallback color clip.")
            img_clip = OUT_DIR / f"img_clip_{idx}.mp4"
            run_ff([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c=#1a1a2e:s={WIDTH}x{HEIGHT}:d={duration:.3f}:r={FPS}",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                str(img_clip),
            ])
        else:
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
        _record_clip_audio_decision(
            idx=idx,
            broll=broll,
            extracted=False,
            used=False,
            reason="still image has no clip audio",
        )
        return out_path

    # Standard video mode — detect landscape content and use blur-background padding
    use_clip_audio, clip_audio_reason = _clip_audio_quality_gate(broll)
    _record_clip_audio_decision(
        idx=idx,
        broll=broll,
        extracted=use_clip_audio,
        used=use_clip_audio,
        reason=clip_audio_reason,
    )
    is_landscape = _video_is_landscape(broll)
    if is_landscape:
        vf = (
            f"[0:v]split[orig][blur];"
            f"[blur]scale={WIDTH}:{HEIGHT},boxblur=20:5[bg];"
            f"[orig]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(WIDTH-overlay_w)/2:(HEIGHT-overlay_h)/2,setsar=1,fps={FPS}[vout]"
        )
    else:
        vf = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
              f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}[vout]")
    input_args = ["-stream_loop", "-1"]
    start_offset = _clip_start_offset(broll, idx, duration)
    if start_offset > 0:
        input_args.extend(["-ss", f"{start_offset:.3f}"])
    input_args.extend(["-i", str(broll)])
    filter_graph, audio_map = _segment_filter_graph(vf, duration=duration, use_clip_audio=use_clip_audio)
    command = [
        "ffmpeg", "-y",
        *input_args,
        "-i", str(voice),
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-shortest",
        str(out_path),
    ]
    try:
        run_ff(command)
    except RuntimeError:
        if not is_landscape:
            raise
        print(f"    [Render] Landscape blur fit failed for {Path(broll).name}; retrying crop fill.")
        fallback_vf = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}[vout]"
        )
        fallback_command = list(command)
        fallback_graph, _ = _segment_filter_graph(
            fallback_vf,
            duration=duration,
            use_clip_audio=use_clip_audio,
        )
        fallback_command[fallback_command.index("-filter_complex") + 1] = fallback_graph
        run_ff(fallback_command)
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
    fade_dur = SHORTS_TRANSITION_DURATION  # short transition keeps Shorts pacing snappy
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

    # A single 1080p xfade graph has to decode and re-encode every segment.
    # Keep corruption protection, but scale the bounded timeout with graph
    # size so a healthy 12-scene Short is not mistaken for a hung process.
    stitch_timeout = min(600, max(180, 45 * n))
    run_ff([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", "; ".join(xfades + afades),
        "-map", f"[v{n - 1}]", "-map", f"[a{n - 1}]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        str(combined),
    ], timeout=stitch_timeout)
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


_MUSIC_PLANNER = None


def _get_music_planner():
    """Build the music planner lazily from the validated configuration layer.

    Provider order, credentials, retries, timeouts, and the license policy all
    come from APP_CONFIG.music — no provider names are hard-coded here.
    """
    global _MUSIC_PLANNER
    if _MUSIC_PLANNER is None:
        registry = build_music_registry(APP_CONFIG, generated_synthesizer=generate_music_bed)
        _MUSIC_PLANNER = MusicPlanner(registry, APP_CONFIG.music)
    return _MUSIC_PLANNER


def write_audio_mix_report(music_volume: float, voice_volume: float = 1.0) -> Path:
    report_path = OUT_DIR / "audio_mix_report.json"
    report = build_audio_mix_report(
        list(_AUDIO_MIX_DECISIONS),
        music_volume=float(music_volume or 0.0),
        voice_volume=float(voice_volume),
    )
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


def add_background_music(
    video_path,
    duration,
    mood,
    music_path=None,
    music_volume=DEFAULT_MUSIC_VOLUME,
    selection_key="",
):
    if music_volume <= 0:
        final_path = OUT_DIR / "final.mp4"
        shutil.copyfile(video_path, final_path)
        write_audio_mix_report(0.0)
        return final_path, None

    # Music selection:
    #   1. explicit --music PATH from CLI always wins (operator override)
    #   2. MusicPlanner walks the configured provider chain
    #      (default: jamendo -> pixabay -> mixkit -> generated -> silence),
    #      validating every candidate's license before it may be mixed.
    selected_music = Path(music_path).expanduser() if music_path else None
    if selected_music and not selected_music.exists():
        die(f"Music file not found: {selected_music}")

    generated = False
    if not selected_music:
        selection = _get_music_planner().select(
            mood,
            float(duration),
            selection_key=str(selection_key or ""),
        )
        try:
            (OUT_DIR / "music_selection.json").write_text(
                json.dumps(selection.to_dict(), indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        for attempt in selection.attempts:
            if attempt.outcome != "selected":
                print(f"    [Music] {attempt.provider}: {attempt.outcome} ({attempt.detail})")
        track = selection.track
        if track.is_silence:
            print("[i] No usable music from any provider; rendering without background music.")
            final_path = OUT_DIR / "final.mp4"
            shutil.copyfile(video_path, final_path)
            write_audio_mix_report(0.0)
            return final_path, None
        selected_music = Path(track.local_path)
        generated = track.provider == "generated"
        if generated:
            print(f"[i] No real track found; generated a synth {mood or 'mysterious'} ambient bed.")
        else:
            print(
                f"[i] Music: {track.title!r} via {track.provider} "
                f"(license: {track.license.license or 'unknown'}, verified: {track.license.verified})"
            )

    if not generated:
        print(f"[i] Mixing background music: {selected_music.name}")

    final_path = OUT_DIR / "final.mp4"
    fade_in_sec = max(0.0, APP_CONFIG.music.fade_in_ms / 1000.0)
    if APP_CONFIG.music.fade_out_ms > 0:
        fade_dur = APP_CONFIG.music.fade_out_ms / 1000.0
    else:
        fade_dur = min(3.0, max(0.5, duration / 8))
    fade_start = max(0, duration - fade_dur)
    run_ff([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(selected_music),
        "-filter_complex",
        f"[0:a]pan=stereo|c0=c0|c1=c0,asplit[voice1][voice2];"
        f"[1:a]volume={music_volume},afade=t=in:st=0:d={fade_in_sec:.3f},"
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
    write_audio_mix_report(music_volume)
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
    parser.add_argument("--duration", type=int, default=None,
                        help="Target video duration in seconds (default: automatic topic policy)")
    parser.add_argument("--compare", action="store_true",
                        help="Enable split-screen comparison mode (pairs local media files)")
    parser.add_argument("--hybrid", action="store_true",
                        help="Enable hybrid mode: use local files if they match, otherwise fall back to Pexels")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Similarity threshold for local file matching in hybrid mode (default: 0.5)")
    parser.add_argument("--dalle", action="store_true",
                        help="Use Gemini image generation when no local/stock match is found")
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
    parser.add_argument(
        "--coverage-preflight-only",
        action="store_true",
        help="Stop after source-coverage validation without voice, rendering, queue creation, or upload.",
    )
    args = parser.parse_args()

    check_deps()
    _AUDIO_MIX_DECISIONS.clear()
    _STORY_REPORT.clear()
    niche = args.topic
    topic_card = find_topic_card(niche)
    duration, duration_reason = resolve_target_duration(niche, args.duration, topic_card)
    if args.duration is None:
        print(f"[i] Story-driven duration: no target set; the story decides its length ({duration_reason}).")
    else:
        print(f"[i] Explicit --duration hint: {duration}s ({duration_reason}).")
    compare_mode = args.compare
    hybrid = args.hybrid
    threshold = args.threshold
    dalle = args.dalle
    landscape = args.landscape
    reuse_script = args.reuse_script
    review_broll = args.review_broll
    no_interactive = args.no_interactive
    coverage_preflight_only = args.coverage_preflight_only
    music_path = args.music or None
    music_volume = 0.0 if args.no_music else max(0.0, args.music_volume)

    global WIDTH, HEIGHT
    if landscape:
        WIDTH, HEIGHT = 1920, 1080

    OUT_DIR.mkdir(exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(exist_ok=True)
    MUSIC_DIR.mkdir(exist_ok=True)

    local_media = get_local_media()
    if local_media:
        img_count = sum(1 for f in local_media if is_image(f))
        vid_count = len(local_media) - img_count
        if dalle:
            mode_str = "hybrid matching with generated-image fallback"
        else:
            mode_str = "hybrid matching with Pexels fallback" if hybrid else "local-only matching"
        print(f"[i] Found {len(local_media)} local file(s) in input_clips/ ({vid_count} video, {img_count} image) - using {mode_str}.")
    else:
        print(f"[i] No local media in input_clips/ - will download B-roll from Pexels/Pixabay/NASA.")

    if compare_mode:
        if len(local_media) < 2:
            die("Compare mode requires at least 2 files in input_clips/. Add images/videos to compare.")
        print(f"[i] Compare mode ON - will create split-screen segments.")

    print(f"\n=== Building a {'landscape' if landscape else 'vertical'} documentary short about: {niche} "
          f"(platform ceiling {_FORMAT_PROFILE.max_duration_sec}s, format {_FORMAT_PROFILE.name}) ===\n")

    def write_manifest(path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def read_manifest(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def set_script_context(ctx: PipelineContext, script_payload: dict) -> None:
        topic_meta = build_topic_metadata(
            video_topic=_topic_metadata_classification_text(niche),
            title=script_payload.get("title", niche),
            description=script_payload.get("description", ""),
            instagram_caption=script_payload.get("instagram_caption", ""),
            segments=script_payload.get("segments", []),
            existing_hashtags=script_payload.get("hashtags") or (),
        )
        script_payload["category_id"] = topic_meta.category_id
        ctx.values["script"] = script_payload
        ctx.values["topic_metadata"] = topic_meta
        ctx.values["title"] = topic_meta.title
        ctx.values["segments"] = script_payload["segments"]

    def load_cached_script(*, announce: bool, critical_asset_plan=None) -> dict:
        cache_path = OUT_DIR / "last_script.json"
        if not cache_path.exists():
            die("No cached script found in output/last_script.json. Run without --reuse-script first.")
        if announce:
            print(f"[i] Reusing cached script from output/last_script.json...")
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["segments"] = cached.get("segments", [])
            cached = Script.from_legacy_dict(cached, niche=niche).to_legacy_dict()
            fatal, soft = script_quality_notes(cached, critical_asset_plan, _FORMAT_PROFILE)
            if fatal:
                die(
                    "Cached script is structurally broken. "
                    "Run without --reuse-script so a complete story can be generated. "
                    f"First issue: {fatal[0]}"
                )
            if soft:
                print(f"[i] Cached script loaded with soft notes: {'; '.join(soft[:3])}")
            if announce:
                print(f"[+] Title: {cached.get('title', niche)}")
                for i, s in enumerate(cached["segments"]):
                    print(f"    {i+1}. {s['narration']}   [b-roll: {s['broll']}]")
            return cached
        except Exception as e:
            die(f"Failed to load cached script: {e}")

    def voice_manifest_from_items(items: list[dict]) -> dict:
        return {
            "items": [
                {
                    "idx": int(item["idx"]),
                    "segment": item["segment"],
                    "voice": str(item["voice"]),
                    "duration": float(item["duration"]),
                    "voice_track": (
                        item["voice_track"].to_dict()
                        if item.get("voice_track")
                        else VoiceTrack(
                            audio_path=Path(item["voice"]),
                            duration_sec=float(item["duration"]),
                            scene_id=str(item["idx"]),
                        ).to_dict()
                    ),
                }
                for item in items
            ]
        }

    def voice_items_from_manifest(payload: dict) -> list[dict]:
        items = []
        for raw in payload.get("items", []):
            voice_track = VoiceTrack.from_dict(dict(raw["voice_track"]))
            items.append({
                "idx": int(raw["idx"]),
                "segment": raw["segment"],
                "voice": Path(raw["voice"]),
                "duration": float(raw["duration"]),
                "voice_track": voice_track,
            })
        return items

    def validate_paths(paths: list[str]) -> bool:
        return all(Path(path).exists() for path in paths)

    def stage_topic_selection(ctx: PipelineContext) -> StageResult:
        payload = {
            "topic": niche,
            "duration": duration,
            "duration_reason": duration_reason,
            "format": _FORMAT_PROFILE.name,
            "platform_max_duration_sec": _FORMAT_PROFILE.max_duration_sec,
            "orientation": "landscape" if landscape else "portrait",
            "compare_mode": compare_mode,
            "hybrid": hybrid,
            "threshold": threshold,
            "dalle": dalle,
            "review_broll": review_broll,
            "no_interactive": no_interactive,
        }
        path = write_manifest(OUT_DIR / "pipeline_topic.json", payload)
        ctx.values["topic_stage"] = payload
        return StageResult(outputs={"topic_manifest": str(path)})

    def load_topic_selection(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["topic_stage"] = read_manifest(Path(record.outputs["topic_manifest"]))

    def stage_documentary_viability(ctx: PipelineContext) -> StageResult:
        config = DocumentaryViabilityConfig.from_env(os.environ)
        report = DocumentaryViabilityEngine(config).evaluate(niche)
        report_path = write_manifest(OUT_DIR / "documentary_viability_report.json", report.to_dict())
        ctx.values["documentary_viability"] = report.to_dict()
        print(
            "[Viability] "
            f"{report.decision.value} score={report.overall_score:.2f} "
            f"enabled={config.enabled}"
        )
        if config.enabled:
            if report.decision == DocumentaryViabilityDecision.SKIP:
                raise RuntimeError(
                    "Documentary viability gate skipped this topic before script generation: "
                    + "; ".join(report.reasons)
                )
            if (
                report.decision == DocumentaryViabilityDecision.REVIEW
                and not config.allow_review_topics
            ):
                raise RuntimeError(
                    "Documentary viability gate requires review for this topic: "
                    + "; ".join(report.reasons)
                )
        warnings = []
        if report.decision != DocumentaryViabilityDecision.APPROVED:
            warnings.append(f"documentary viability decision: {report.decision.value}")
        return StageResult(
            outputs={
                "documentary_viability_report": str(report_path),
                "decision": report.decision.value,
                "overall_score": round(report.overall_score, 4),
            },
            warnings=warnings,
        )

    def load_documentary_viability(ctx: PipelineContext, record: StageRecord) -> None:
        report_path = Path(record.outputs.get("documentary_viability_report", ""))
        if report_path.exists():
            ctx.values["documentary_viability"] = read_manifest(report_path)

    def validate_documentary_viability(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("documentary_viability_report", "")).exists()

    def stage_critical_asset_discovery(ctx: PipelineContext) -> StageResult:
        if not _env_flag("AUTO_VIDEO_CRITICAL_ASSET_ENFORCE", default="true"):
            plan = {
                "version": 1,
                "topic": niche,
                "topic_card": None,
                "status": "SKIPPED",
                "failure_classification": "DISABLED_FOR_TEST_RENDER",
                "failure_reason": "critical asset discovery disabled by AUTO_VIDEO_CRITICAL_ASSET_ENFORCE",
                "providers": [],
                "roles": [],
            }
            plan_path = write_manifest(OUT_DIR / "critical_asset_plan.json", plan)
            ctx.values["critical_asset_plan"] = plan
            print("[Critical assets] SKIPPED classification=DISABLED_FOR_TEST_RENDER")
            return StageResult(outputs={
                "critical_asset_plan": str(plan_path),
                "status": plan["status"],
                "failure_classification": plan["failure_classification"],
                "critical_media_files": [],
            })
        try:
            plan = discover_critical_assets(niche, output_dir=OUT_DIR)
        except Exception as exc:
            plan = {
                "version": 1,
                "topic": niche,
                "topic_card": None,
                "status": "FAILED",
                "failure_classification": "TECHNICAL_PROVIDER_FAILURE",
                "failure_reason": (
                    f"critical discovery raised {type(exc).__name__}: {_safe_diagnostic(exc)}"
                ),
                "providers": [],
                "roles": [],
            }
        plan_path = write_manifest(OUT_DIR / "critical_asset_plan.json", plan)
        ctx.values["critical_asset_plan"] = plan
        print(
            f"[Critical assets] {plan['status']} "
            f"classification={plan['failure_classification']}"
        )
        if plan["status"] == "FAILED":
            if plan["failure_classification"] == "CONTENT_ASSET_GAP":
                # Daily recovery already recognizes rejected verified-media rows
                # as a content deferral. This pre-script compatibility report
                # avoids misclassifying a healthy visual mismatch as technical.
                scenes = []
                for role in plan.get("roles") or []:
                    attempts = role.get("attempts") or []
                    verification = dict(next(
                        (
                            attempt.get("verification")
                            for attempt in reversed(attempts)
                            if isinstance(attempt.get("verification"), dict)
                        ),
                        None,
                    ) or {
                        "scene_index": role.get("scene_index"),
                        "expected_entity": role.get("expected_entity"),
                        "expected_action": role.get("expected_action"),
                        "decision": VerificationDecision.REJECTED.value,
                        "reason": role.get("failure_reason"),
                    })
                    verification["decision"] = VerificationDecision.REJECTED.value
                    scenes.append(verification)
                write_manifest(OUT_DIR / "verified_media_report.json", {
                    "source": "critical_asset_discovery",
                    "failure_classification": plan["failure_classification"],
                    "scenes": scenes,
                    "summary": {
                        "scene_count": len(scenes),
                        "verified_count": 0,
                        "unverified_count": 0,
                        "rejected_count": len(scenes),
                        "verified_coverage": 0.0,
                    },
                })
            raise RuntimeError(
                "Critical asset discovery failed before script generation "
                f"({plan['failure_classification']}): {plan['failure_reason']}. "
                f"See {plan_path.name}."
            )
        return StageResult(outputs={
            "critical_asset_plan": str(plan_path),
            "status": plan["status"],
            "failure_classification": plan["failure_classification"],
            "critical_media_files": [
                selected["local_path"]
                for selected in critical_asset_overrides(plan).values()
            ],
        })

    def load_critical_asset_discovery(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["critical_asset_plan"] = read_manifest(
            Path(record.outputs["critical_asset_plan"])
        )

    def validate_critical_asset_discovery(_ctx: PipelineContext, record: StageRecord) -> bool:
        return _critical_plan_outputs_valid(
            record.outputs.get("critical_asset_plan", ""),
            niche,
            require_reverification=reuse_script and find_topic_card(niche) is not None,
        )

    def stage_script_generation(ctx: PipelineContext) -> StageResult:
        critical_plan = ctx.values.get("critical_asset_plan")
        if reuse_script:
            script_payload = load_cached_script(
                announce=True,
                critical_asset_plan=critical_plan,
            )
        else:
            print("[1/5] Writing script with Gemini...")
            script_payload = generate_script(
                niche,
                critical_asset_plan=critical_plan,
                profile=_FORMAT_PROFILE,
            )
        set_script_context(ctx, script_payload)
        write_manifest(OUT_DIR / "last_script.json", script_payload)
        return StageResult(outputs={"script_path": str(OUT_DIR / "last_script.json")})

    def load_script_generation(ctx: PipelineContext, _record: StageRecord) -> None:
        script_payload = load_cached_script(
            announce=False,
            critical_asset_plan=ctx.values.get("critical_asset_plan"),
        )
        set_script_context(ctx, script_payload)

    def _query_generation_report(shot_plan: ShotPlan) -> dict:
        scenes = []
        for intent in shot_plan.intents:
            scene_entity = intent.scene_entity.to_dict() if intent.scene_entity else None
            scenes.append({
                "scene_index": intent.scene_index,
                "documentary_role": intent.documentary_role,
                "visual_goal": intent.visual_goal.value,
                "canonical_entity": scene_entity.get("canonical_entity") if scene_entity else intent.primary_subject,
                "scene_entity": scene_entity,
                "generated_queries": list(intent.search_queries),
                "rejected_mixed_queries": intent.diagnostics.get("query_isolation_rejections", []),
                "fallback_chain": intent.diagnostics.get("fallback_chain", []),
            })
        return {
            "planner": "scene_entity_query_isolation",
            "primary_subject": shot_plan.primary_subject,
            "scenes": scenes,
        }

    def stage_media_planning(ctx: PipelineContext) -> StageResult:
        overrides = {}
        if review_broll:
            raw = interactive_broll_review(ctx.values["segments"], niche)
            overrides = {int(k): v for k, v in raw.items()}
        knowledge_store = KnowledgePackStore()
        editorial_canon, lock_report, scene_role_report, domain_report = EditorialCanonBuilder().build(
            topic=niche,
            segments=ctx.values["segments"],
            knowledge_domains=knowledge_store.load(),
            primary_subject_override=topic_card.required_entity if topic_card else "",
        )
        scene_entity_plan = SceneEntityPlanner().plan(
            editorial_canon=editorial_canon,
            segments=ctx.values["segments"],
        )
        editorial_canon_path = editorial_canon.write_json(OUT_DIR / "editorial_canon.json")
        primary_subject_lock_path = write_manifest(
            OUT_DIR / "primary_subject_lock_report.json",
            lock_report.to_dict(),
        )
        scene_role_path = write_manifest(OUT_DIR / "scene_role_report.json", scene_role_report)
        domain_report_path = write_manifest(OUT_DIR / "domain_classification_report.json", domain_report)
        shot_plan = VisualDirector(knowledge_store).plan(
            topic=niche,
            segments=ctx.values["segments"],
            editorial_canon=editorial_canon,
            scene_entity_plan=scene_entity_plan,
        )
        shot_plan = SubjectContinuityEngine().apply(
            shot_plan,
            segments=ctx.values["segments"],
            editorial_canon=editorial_canon,
        )
        shot_plan = apply_topic_card_identity(shot_plan, topic_card)
        shot_plan_path = shot_plan.write_json(OUT_DIR / "shot_plan.json")
        scene_entity_report_path = write_manifest(
            OUT_DIR / "scene_entity_report.json",
            scene_entity_plan.to_report(),
        )
        query_generation_report_path = write_manifest(
            OUT_DIR / "query_generation_report.json",
            _query_generation_report(shot_plan),
        )
        ctx.values["broll_overrides"] = overrides
        ctx.values["editorial_canon"] = editorial_canon
        ctx.values["shot_plan"] = shot_plan
        path = write_manifest(
            OUT_DIR / "media_planning_manifest.json",
            {
                "broll_overrides": {str(k): v for k, v in overrides.items()},
                "editorial_canon": str(editorial_canon_path),
                "primary_subject_lock_report": str(primary_subject_lock_path),
                "scene_role_report": str(scene_role_path),
                "domain_classification_report": str(domain_report_path),
                "scene_entity_report": str(scene_entity_report_path),
                "query_generation_report": str(query_generation_report_path),
                "shot_plan": str(shot_plan_path),
            },
        )
        return StageResult(outputs={
            "media_planning_manifest": str(path),
            "editorial_canon": str(editorial_canon_path),
            "primary_subject_lock_report": str(primary_subject_lock_path),
            "scene_role_report": str(scene_role_path),
            "domain_classification_report": str(domain_report_path),
            "scene_entity_report": str(scene_entity_report_path),
            "query_generation_report": str(query_generation_report_path),
            "shot_plan": str(shot_plan_path),
        })

    def load_media_planning(ctx: PipelineContext, record: StageRecord) -> None:
        payload = read_manifest(Path(record.outputs["media_planning_manifest"]))
        ctx.values["broll_overrides"] = {
            int(k): v for k, v in payload.get("broll_overrides", {}).items()
        }
        canon_path = Path(payload.get("editorial_canon") or record.outputs.get("editorial_canon", ""))
        if canon_path.exists():
            ctx.values["editorial_canon"] = EditorialCanon.from_dict(read_manifest(canon_path))
        else:
            knowledge_store = KnowledgePackStore()
            editorial_canon, lock_report, scene_role_report, domain_report = EditorialCanonBuilder().build(
                topic=niche,
                segments=ctx.values["segments"],
                knowledge_domains=knowledge_store.load(),
                primary_subject_override=topic_card.required_entity if topic_card else "",
            )
            ctx.values["editorial_canon"] = editorial_canon
            editorial_canon.write_json(OUT_DIR / "editorial_canon.json")
            write_manifest(OUT_DIR / "primary_subject_lock_report.json", lock_report.to_dict())
            write_manifest(OUT_DIR / "scene_role_report.json", scene_role_report)
            write_manifest(OUT_DIR / "domain_classification_report.json", domain_report)
            scene_entity_plan = SceneEntityPlanner().plan(
                editorial_canon=editorial_canon,
                segments=ctx.values["segments"],
            )
            write_manifest(OUT_DIR / "scene_entity_report.json", scene_entity_plan.to_report())
        shot_plan_path = Path(payload.get("shot_plan") or record.outputs.get("shot_plan", ""))
        if shot_plan_path.exists():
            ctx.values["shot_plan"] = apply_topic_card_identity(
                ShotPlan.from_dict(read_manifest(shot_plan_path)),
                topic_card,
            )
            ctx.values["shot_plan"].write_json(shot_plan_path)
            write_manifest(OUT_DIR / "scene_entity_report.json", {
                "scenes": [
                    {
                        "scene_index": intent.scene_index,
                        "scene_entity": intent.scene_entity.to_dict() if intent.scene_entity else None,
                    }
                    for intent in ctx.values["shot_plan"].intents
                ],
                "rejected_mixed_queries": [],
                "fallback_chains": [
                    {
                        "scene_index": intent.scene_index,
                        "fallback_chain": intent.diagnostics.get("fallback_chain", []),
                    }
                    for intent in ctx.values["shot_plan"].intents
                ],
            })
            write_manifest(OUT_DIR / "query_generation_report.json", _query_generation_report(ctx.values["shot_plan"]))

        else:
            knowledge_store = KnowledgePackStore()
            shot_plan = VisualDirector(knowledge_store).plan(
                topic=niche,
                segments=ctx.values["segments"],
                editorial_canon=ctx.values.get("editorial_canon"),
                scene_entity_plan=SceneEntityPlanner().plan(
                    editorial_canon=ctx.values["editorial_canon"],
                    segments=ctx.values["segments"],
                ) if ctx.values.get("editorial_canon") else None,
            )
            ctx.values["shot_plan"] = SubjectContinuityEngine().apply(
                shot_plan,
                segments=ctx.values["segments"],
                editorial_canon=ctx.values.get("editorial_canon"),
            )
            ctx.values["shot_plan"] = apply_topic_card_identity(
                ctx.values["shot_plan"],
                topic_card,
            )
            write_manifest(OUT_DIR / "query_generation_report.json", _query_generation_report(ctx.values["shot_plan"]))

    def validate_media_planning(_ctx: PipelineContext, record: StageRecord) -> bool:
        required_paths = (
            record.outputs.get("media_planning_manifest", ""),
            record.outputs.get("editorial_canon", ""),
            record.outputs.get("shot_plan", ""),
        )
        if not all(path and Path(path).exists() for path in required_paths):
            return False
        if topic_card is None:
            return True
        try:
            canon_payload = read_manifest(Path(record.outputs["editorial_canon"]))
            shot_payload = read_manifest(Path(record.outputs["shot_plan"]))
        except (OSError, ValueError, KeyError):
            return False
        expected = topic_card.required_entity.casefold()
        if str(canon_payload.get("primary_subject") or "").casefold() != expected:
            return False
        if str(shot_payload.get("primary_subject") or "").casefold() != expected:
            return False
        critical_intents = [
            item for item in shot_payload.get("intents", ())
            if isinstance(item, dict) and item.get("scene_index") in {0, 1}
        ]
        return len(critical_intents) == 2 and all(
            str((item.get("scene_entity") or {}).get("canonical_entity") or "").casefold() == expected
            and str(item.get("action") or "").casefold() == topic_card.required_action.casefold()
            for item in critical_intents
        )

    def stage_editorial_identity(ctx: PipelineContext) -> StageResult:
        """Reject a plan whose subject no longer represents the requested topic."""

        report = EditorialIdentityGate().evaluate(
            topic=niche,
            editorial_canon=ctx.values["editorial_canon"],
            shot_plan=ctx.values["shot_plan"],
        )
        report_path = report.write_json(OUT_DIR / "editorial_identity_report.json")
        ctx.values["editorial_identity_report"] = report
        if not report.approved:
            raise RuntimeError(
                "Editorial identity gate rejected this topic before source coverage: "
                + "; ".join(report.reasons)
            )
        return StageResult(outputs={"editorial_identity_report": str(report_path)})

    def load_editorial_identity(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["editorial_identity_report"] = EditorialIdentityReport.from_dict(
            read_manifest(Path(record.outputs["editorial_identity_report"]))
        )

    def validate_editorial_identity(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("editorial_identity_report", "")).exists()

    def _resolved_provider_intent(intent, report: CanonicalEntityReport | None):
        """Create a retrieval-only ShotIntent without mutating the ShotPlan."""

        if report is None:
            return intent
        resolution = report.scene_for_index(intent.scene_index)
        if resolution is None:
            return intent
        return replace(
            intent,
            primary_subject=resolution.canonical_entity,
            scene_entity=resolution.resolved_entity,
            required_entities=(
                resolution.canonical_entity,
                *resolution.supporting_entities,
            ),
        )

    def _focused_provider_intent(
        intent,
        canonical_report: CanonicalEntityReport | None,
        focus_report: SceneVisualFocusReport | None,
    ):
        """Apply a retrieval-only scene focus without changing the ShotPlan."""

        provider_intent = _resolved_provider_intent(intent, canonical_report)
        if provider_intent is None or not isinstance(focus_report, SceneVisualFocusReport):
            return provider_intent
        focus = focus_report.scene_for_index(provider_intent.scene_index)
        if focus is None:
            return provider_intent
        focused_entity = focus.to_scene_entity(provider_intent.scene_entity)
        return replace(
            provider_intent,
            primary_subject=focus.required_visual_entity,
            scene_entity=focused_entity,
            required_entities=(
                focus.required_visual_entity,
                *focus.query_terms,
            ),
            diagnostics={
                **provider_intent.diagnostics,
                "scene_visual_focus": focus.to_dict(),
            },
        )

    def stage_canonical_entity_resolution(ctx: PipelineContext) -> StageResult:
        """Resolve provider-facing entities while retaining the original ShotPlan."""

        report = CanonicalSceneEntityResolver(
            CanonicalEntityResolverConfig.from_env(os.environ)
        ).resolve(
            documentary_topic=niche,
            shot_plan=ctx.values["shot_plan"],
        )
        report_path = report.write_json(OUT_DIR / "canonical_entity_report.json")
        ctx.values["canonical_entity_report"] = report
        return StageResult(outputs={"canonical_entity_report": str(report_path)})

    def load_canonical_entity_resolution(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["canonical_entity_report"] = CanonicalEntityReport.from_dict(
            read_manifest(Path(record.outputs["canonical_entity_report"]))
        )

    def stage_scene_visual_focus(ctx: PipelineContext) -> StageResult:
        """Resolve scene-visible entities while preserving the documentary anchor."""

        canonical_report = ctx.values.get("canonical_entity_report")
        resolved_intents = tuple(
            _resolved_provider_intent(intent, canonical_report)
            for intent in ctx.values["shot_plan"].intents
        )
        normalized_shot_plan = SimpleNamespace(
            intents=resolved_intents,
            primary_subject=(
                canonical_report.canonical_documentary_entity
                if isinstance(canonical_report, CanonicalEntityReport)
                else ctx.values["shot_plan"].primary_subject
            ),
            domain_id=getattr(ctx.values["shot_plan"], "domain_id", ""),
        )
        report = SceneVisualFocusPlanner().plan(
            documentary_topic=niche,
            shot_plan=normalized_shot_plan,
            knowledge_domains=KnowledgePackStore().load(),
        )
        path = report.write_json(OUT_DIR / "scene_visual_focus_report.json")
        ctx.values["scene_visual_focus_report"] = report
        return StageResult(outputs={"scene_visual_focus_report": str(path)})

    def load_scene_visual_focus(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["scene_visual_focus_report"] = SceneVisualFocusReport.from_dict(
            read_manifest(Path(record.outputs["scene_visual_focus_report"]))
        )

    def validate_scene_visual_focus(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("scene_visual_focus_report", "")).exists()

    def validate_canonical_entity_resolution(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("canonical_entity_report", "")).exists()

    def stage_semantic_query_planning(ctx: PipelineContext) -> StageResult:
        """Translate canonical entities into provider-only query language."""

        shot_plan = ctx.values["shot_plan"]
        canonical_report = ctx.values.get("canonical_entity_report")
        resolved_intents = tuple(
            _focused_provider_intent(
                intent,
                canonical_report,
                ctx.values.get("scene_visual_focus_report"),
            )
            for intent in shot_plan.intents
        )
        retrieval_shot_plan = SimpleNamespace(
            intents=resolved_intents,
            primary_subject=shot_plan.primary_subject,
        )
        report = SemanticVisualQueryEngine(
            SemanticQueryConfig.from_env(os.environ)
        ).plan(
            # The canonical resolver is authoritative for retrieval identity.
            # Supplying the editorial title here would let the legacy semantic
            # normalizer reintroduce title-wide entities such as "rainforest"
            # into a scene explicitly resolved as "Amazon River".
            documentary_topic="",
            shot_plan=retrieval_shot_plan,
            constraint_report=ctx.values.get("scene_constraint_report"),
        )
        report = replace(report, documentary_topic=niche)
        report_path = report.write_json(OUT_DIR / "semantic_query_report.json")
        ctx.values["semantic_query_report"] = report
        return StageResult(outputs={"semantic_query_report": str(report_path)})

    def load_semantic_query_planning(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["semantic_query_report"] = SemanticQueryReport.from_dict(
            read_manifest(Path(record.outputs["semantic_query_report"]))
        )

    def stage_scene_constraint_planning(ctx: PipelineContext) -> StageResult:
        """Freeze mandatory visual requirements before provider query translation."""

        focused_intents = tuple(
            _focused_provider_intent(
                intent,
                ctx.values.get("canonical_entity_report"),
                ctx.values.get("scene_visual_focus_report"),
            )
            for intent in ctx.values["shot_plan"].intents
        )
        report = SceneConstraintPlanner(
            SceneConstraintConfig.from_env(os.environ)
        ).plan(
            documentary_topic=niche,
            shot_plan=SimpleNamespace(
                intents=focused_intents,
                primary_subject=ctx.values["shot_plan"].primary_subject,
            ),
        )
        path = report.write_json(OUT_DIR / "scene_constraint_report.json")
        ctx.values["scene_constraint_report"] = report
        return StageResult(outputs={"scene_constraint_report": str(path)})

    def load_scene_constraint_planning(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["scene_constraint_report"] = SceneConstraintReport.from_dict(
            read_manifest(Path(record.outputs["scene_constraint_report"]))
        )

    def validate_scene_constraint_planning(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("scene_constraint_report", "")).exists()

    def validate_semantic_query_planning(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("semantic_query_report", "")).exists()

    def _source_coverage_intents(shot_plan: ShotPlan, maximum: int) -> list:
        intents = list(shot_plan.intents)
        critical = [
            intent
            for intent in intents
            if str(intent.scene_importance).upper() in {"HOOK", "MAIN_REVEAL"}
        ]
        selected_indexes = set(sample_scene_indexes(len(intents), maximum))
        selected_indexes.update(intent.scene_index for intent in critical)
        selected = [intent for intent in intents if intent.scene_index in selected_indexes]
        if len(selected) > maximum:
            selected = critical[:maximum]
            selected_indexes = {intent.scene_index for intent in selected}
            for intent in intents:
                if len(selected) >= maximum:
                    break
                if intent.scene_index not in selected_indexes:
                    selected.append(intent)
                    selected_indexes.add(intent.scene_index)
        return selected

    def _probe_source_coverage_scene(
        intent,
        config: SourceCoverageConfig,
        narrations: dict[int, str],
        canonical_report: CanonicalEntityReport | None = None,
        semantic_report: SemanticQueryReport | None = None,
        constraint_report: SceneConstraintReport | None = None,
        focus_report: SceneVisualFocusReport | None = None,
    ) -> SceneCoverage:
        provider_intent = _focused_provider_intent(
            intent,
            canonical_report,
            focus_report,
        )
        semantic_scene = (
            semantic_report.scene_for_index(intent.scene_index)
            if semantic_report else None
        )
        constraint_scene = (
            constraint_report.scene_for_index(intent.scene_index)
            if isinstance(constraint_report, SceneConstraintReport) else None
        )
        queries = list(
            semantic_scene.provider_queries if semantic_scene else provider_intent.search_queries
        )[:config.max_queries_per_scene]
        if constraint_scene:
            queries = list(constraint_scene.filter_queries(queries)[0] or constraint_scene.query_seeds)
        fallback = provider_intent.primary_subject or niche
        visual_intent = _selection_intent(
            queries,
            fallback=fallback,
            narration=narrations.get(intent.scene_index, ""),
            idx=intent.scene_index,
            shot_intent=provider_intent,
        )
        strategy = _build_search_strategy(
            queries,
            fallback,
            narrations.get(intent.scene_index, ""),
            idx=intent.scene_index,
            intent=visual_intent,
        )
        supported = {
            "pexels",
            "pixabay",
            "wikimedia",
            "mixkit",
            "coverr",
            "videvo",
            "noaa",
            "esa",
            "usgs",
            "yt_clip",
        }
        ranked_plans = [
            plan
            for plan in strategy.provider_plans
            if plan.provider_id in supported
            and plan.score > 0
            and _provider_is_configured(plan.provider_id)
        ]
        disabled_plans = [
            plan for plan in ranked_plans if _provider_failure_detail(plan.provider_id)
        ]
        plans = [
            plan for plan in ranked_plans if _provider_is_available(plan.provider_id)
        ][:config.max_providers_per_scene]
        # Coverage preflight is an availability probe, not the final routing
        # decision.  A narrow capability ranking must not make a concrete
        # stock query appear impossible when configured stock sources
        # can still answer it.  Final provider selection remains unchanged.
        if not plans and queries:
            fallback_providers = [
                provider_id
                for provider_id in ("pexels", "pixabay")
                if _provider_is_available(provider_id)
            ][:config.max_providers_per_scene]
            plans = [
                SimpleNamespace(provider_id=provider_id, queries=tuple(queries), score=0.01)
                for provider_id in fallback_providers
            ]
            reasons = [
                "coverage probe used stock fallback after no provider plan was ranked"
                if plans else "no configured probe-supported provider was ranked for this scene"
            ]
        else:
            reasons: list[str] = []
        attempted: list[str] = []
        provider_outcomes: list[ProviderProbeOutcome] = []
        candidates = []
        for plan in (*disabled_plans, *plans):
            attempted.append(plan.provider_id)
            provider_failure = _provider_failure_detail(plan.provider_id)
            if provider_failure:
                provider_outcomes.append(ProviderProbeOutcome(
                    provider=plan.provider_id,
                    status=ProviderProbeStatus.PROVIDER_ERROR,
                    detail=f"provider disabled for this run: {provider_failure}",
                ))
                reasons.append(f"{plan.provider_id} disabled for this run: {provider_failure}")
                continue
            if not _provider_is_configured(plan.provider_id):
                provider_outcomes.append(ProviderProbeOutcome(
                    provider=plan.provider_id,
                    status=ProviderProbeStatus.UNCONFIGURED,
                    detail="provider endpoint or credentials are not configured",
                ))
                reasons.append(f"{plan.provider_id} is not configured")
                continue
            plan_queries = list(
                semantic_scene.queries_for(plan.provider_id)
                if semantic_scene else plan.queries or queries
            )[:config.max_queries_per_scene]
            try:
                provider_candidates = _adaptive_provider_candidates(
                    plan.provider_id,
                    plan_queries,
                    fallback,
                    timeout_sec=config.provider_timeout_sec,
                    probe=True,
                )
            except Exception as exc:
                status = _classify_provider_probe_exception(exc)
                detail = str(exc)[:300]
                provider_outcomes.append(ProviderProbeOutcome(
                    provider=plan.provider_id,
                    status=status,
                    detail=detail,
                ))
                reasons.append(f"{plan.provider_id} probe {status.value.lower()}: {detail}")
                continue
            provider_outcomes.append(ProviderProbeOutcome(
                provider=plan.provider_id,
                status=(
                    ProviderProbeStatus.SUCCESS
                    if provider_candidates else ProviderProbeStatus.NO_RESULTS
                ),
                candidates_found=len(provider_candidates),
                detail=("candidates returned" if provider_candidates else "provider returned no candidates"),
            ))
            candidates.extend(provider_candidates)
            if not provider_candidates:
                reasons.append(f"{plan.provider_id} returned no candidates")
        candidates = _dedupe_candidates(candidates)
        scored = [
            score_candidate(
                visual_intent,
                candidate,
                target_duration_sec=SHORTS_SCENE_TARGET_DURATION,
                output_width=WIDTH,
                output_height=HEIGHT,
                evidence_engine=EvidenceVerificationEngine(),
            )
            for candidate in candidates
        ]
        minimum_score = max(1.0, _minimum_score_for_intent(visual_intent))
        critical = str(getattr(intent, "scene_importance", "")).upper() in {
            "HOOK", "MAIN_REVEAL",
        }
        required_score = minimum_score if critical else max(
            1.0,
            minimum_score * config.supporting_scene_score_ratio,
        )
        accepted = [
            score
            for score in scored
            if score.quality_gate_passed and score.score >= required_score
        ]
        yt_clip_hits = [
            candidate
            for candidate in candidates
            if str(candidate.provider).lower() == "yt_clip"
        ]
        if not accepted and yt_clip_hits:
            reasons.append(
                "yt_clip availability probe succeeded; authentic footage will be "
                "fetched and vision-verified during media selection"
            )
        if candidates and not accepted and not yt_clip_hits:
            reasons.append("candidates found but none passed production scoring")
        if not plans:
            reasons.append("no probe-supported provider was ranked for this scene")
        entity = (
            provider_intent.scene_entity.canonical_entity
            if provider_intent.scene_entity else provider_intent.primary_subject
        )
        return SceneCoverage(
            scene_index=intent.scene_index,
            canonical_entity=entity,
            documentary_role=intent.documentary_role,
            scene_importance=intent.scene_importance,
            query=queries[0] if queries else fallback,
            providers_attempted=tuple(attempted),
            candidates_found=len(candidates),
            accepted_candidates=len(accepted) + len(yt_clip_hits),
            best_score=max((score.score for score in scored), default=None),
            covered=bool(accepted) or bool(yt_clip_hits),
            provider_outcomes=tuple(provider_outcomes),
            reasons=tuple(reasons),
            coverage_basis="production_score" if critical else "availability_score",
            required_score=round(required_score, 4),
        )

    def stage_source_coverage(ctx: PipelineContext) -> StageResult:
        config = SourceCoverageConfig.from_env(os.environ)
        if not config.enabled:
            report = SourceCoverageEvaluator(config).evaluate(niche, ())
            report_path = report.write_json(OUT_DIR / "source_coverage_report.json")
            ctx.values["source_coverage"] = report.to_dict()
            return StageResult(outputs={
                "source_coverage_report": str(report_path),
                "decision": report.decision.value,
                "coverage_ratio": 0.0,
            })
        narrations = {
            index: str(segment.get("narration") or "")
            for index, segment in enumerate(ctx.values["segments"])
        }
        sampled_intents = _source_coverage_intents(ctx.values["shot_plan"], config.max_scenes)
        scenes = []
        for intent in sampled_intents:
            locked_coverage = verified_critical_scene_coverage(
                intent,
                ctx.values.get("critical_asset_plan"),
            )
            scenes.append(locked_coverage or _probe_source_coverage_scene(
                intent,
                config,
                narrations,
                ctx.values.get("canonical_entity_report"),
                ctx.values.get("semantic_query_report"),
                ctx.values.get("scene_constraint_report"),
                ctx.values.get("scene_visual_focus_report"),
            ))
        report = SourceCoverageEvaluator(config).evaluate(niche, scenes)
        report_path = report.write_json(OUT_DIR / "source_coverage_report.json")
        ctx.values["source_coverage"] = report.to_dict()
        print(
            f"[Coverage] {report.decision.value} "
            f"coverage={report.coverage_ratio:.0%} sampled={len(report.scenes)}"
        )
        # A direct CLI run must use the same safety standard as scheduled
        # publishing. Set this explicitly to false only for development
        # experiments that intentionally inspect weak fallback behavior.
        enforce = _env_flag("AUTO_VIDEO_SOURCE_COVERAGE_ENFORCE", default="true")
        if enforce and report.decision == SourceCoverageDecision.DEFERRED:
            raise RuntimeError(
                "Source coverage preflight deferred this topic before voice generation: "
                + "; ".join(report.reasons)
            )
        warnings = list(report.reasons) if report.decision == SourceCoverageDecision.DEFERRED else []
        return StageResult(
            outputs={
                "source_coverage_report": str(report_path),
                "decision": report.decision.value,
                "coverage_ratio": round(report.coverage_ratio, 4),
            },
            warnings=warnings,
        )

    def load_source_coverage(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["source_coverage"] = read_manifest(
            Path(record.outputs["source_coverage_report"])
        )

    def validate_source_coverage(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("source_coverage_report", "")).exists()

    def stage_voice_generation(ctx: PipelineContext) -> StageResult:
        voice_items = make_all_voices(ctx.values["segments"])
        ctx.values["voice_items"] = voice_items
        actual_seconds = sum(item["duration"] for item in voice_items)
        transition_allowance = max(0, len(voice_items) - 1) * _FORMAT_PROFILE.transition_duration_sec
        _STORY_REPORT["actual_narration_sec"] = round(actual_seconds, 2)
        if actual_seconds + transition_allowance > _FORMAT_PROFILE.max_duration_sec:
            _STORY_REPORT["narration_overflow"] = True
        _write_story_report()
        manifest = voice_manifest_from_items(voice_items)
        path = write_manifest(OUT_DIR / "voice_manifest.json", manifest)
        return StageResult(outputs={
            "voice_manifest": str(path),
            "voice_files": [str(item["voice"]) for item in voice_items],
        })

    def load_voice_generation(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["voice_items"] = voice_items_from_manifest(
            read_manifest(Path(record.outputs["voice_manifest"]))
        )

    def validate_voice_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        return (
            Path(record.outputs.get("voice_manifest", "")).exists()
            and validate_paths(record.outputs.get("voice_files", []))
        )

    def write_fallback_quality_report(media_assets: list[MediaAsset]) -> Path:
        scenes = []
        local_explainer_count = 0
        hybrid_count = 0
        for idx, asset in enumerate(media_assets):
            metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
            selection = metadata.get("selection") if isinstance(metadata.get("selection"), dict) else {}
            provider = str(selection.get("provider") or metadata.get("provider") or asset.source.value)
            fallback_level = str(selection.get("fallback_level") or metadata.get("fallback_level") or "")
            reason = str(selection.get("selection_reason") or metadata.get("selection_reason") or "")
            provider_id = str(selection.get("provider_id") or metadata.get("provider_asset_id") or "")
            is_local_explainer = (
                fallback_level == "local_explainer"
                or "local explainer" in reason.lower()
                or ("explainer" in provider_id.lower() and provider in {"local", MediaSource.LOCAL.value})
            )
            is_hybrid = provider in {"hybrid", "hybrid_composer"} or fallback_level == "hybrid_composer"
            if is_local_explainer:
                local_explainer_count += 1
            if is_hybrid:
                hybrid_count += 1
            scenes.append({
                "scene_index": idx,
                "provider": provider,
                "provider_id": provider_id,
                "fallback_level": fallback_level,
                "is_local_explainer": is_local_explainer,
                "is_hybrid_composer": is_hybrid,
                "confidence": selection.get("confidence_level") or selection.get("confidence"),
                "reason": reason,
            })
        total = max(1, len(media_assets))
        local_explainer_ratio = local_explainer_count / total
        report = {
            "total_scenes": len(media_assets),
            "local_explainer_count": local_explainer_count,
            "hybrid_composer_count": hybrid_count,
            "local_explainer_ratio": round(local_explainer_ratio, 4),
            "max_allowed_local_explainer_ratio": AUTO_VIDEO_MAX_EXPLAINER_FALLBACK_RATIO,
            "quality_gate_passed": local_explainer_ratio <= AUTO_VIDEO_MAX_EXPLAINER_FALLBACK_RATIO,
            "scenes": scenes,
        }
        path = write_manifest(OUT_DIR / "fallback_quality_report.json", report)
        if not report["quality_gate_passed"]:
            raise RuntimeError(
                "Visual fallback quality gate failed: "
                f"{local_explainer_count}/{len(media_assets)} scenes used local explainer fallbacks "
                f"({local_explainer_ratio:.0%}; max {AUTO_VIDEO_MAX_EXPLAINER_FALLBACK_RATIO:.0%}). "
                "Stopping before timeline/render so automation does not publish a weak video."
            )
        return path

    def stage_media_selection(ctx: PipelineContext) -> StageResult:
        used_set = _load_persistent_used()
        media_assets = []
        broll_overrides = ctx.values.get("broll_overrides", {})
        shot_plan = ctx.values.get("shot_plan")
        canonical_entity_report = ctx.values.get("canonical_entity_report")
        scene_visual_focus_report = ctx.values.get("scene_visual_focus_report")
        semantic_query_report = ctx.values.get("semantic_query_report")
        scene_constraint_report = ctx.values.get("scene_constraint_report")
        critical_locks = critical_asset_overrides(ctx.values.get("critical_asset_plan"))
        visual_grammar_engine = VisualGrammarEngine(
            topic=niche,
            total_scenes=len(ctx.values["voice_items"]),
        )
        source_continuity_engine = SourceContinuityEngine.from_env()
        source_continuity_state = SourceContinuityState()
        for item in ctx.values["voice_items"]:
            idx = item["idx"]
            seg = item["segment"]
            dur = item["duration"]
            shot_intent = shot_plan.intent_for_index(idx) if isinstance(shot_plan, ShotPlan) else None
            provider_shot_intent = _focused_provider_intent(
                shot_intent,
                canonical_entity_report,
                scene_visual_focus_report,
            ) if shot_intent else None
            semantic_scene = (
                semantic_query_report.scene_for_index(idx)
                if isinstance(semantic_query_report, SemanticQueryReport) else None
            )
            constraint_scene = (
                scene_constraint_report.scene_for_index(idx)
                if isinstance(scene_constraint_report, SceneConstraintReport) else None
            )
            visual_grammar_decision = None

            if idx in critical_locks:
                locked = dict(critical_locks[idx])
                locked_path = Path(locked.get("local_path") or "")
                if not _valid_media_path(locked_path):
                    raise RuntimeError(
                        f"verified critical asset for segment {idx + 1} is missing or invalid"
                    )
                provider = str(locked.get("provider") or "")
                provider_id = str(locked.get("provider_id") or "")
                if provider and provider_id:
                    used_set.add(
                        provider_id
                        if provider_id.startswith(f"{provider}:")
                        else f"{provider}:{provider_id}"
                    )
                    _save_persistent_used(used_set)
                locked_asset = _media_asset_from_critical_lock(locked, idx)
                _MEDIA_SELECTION_DIAGNOSTICS[idx] = locked_asset.metadata
                media_assets.append(locked_asset)
                visual_grammar_engine.register_real_asset(provider=provider)
                print(
                    f"[3/5] Segment {idx + 1}: using verified critical asset "
                    f"{provider}:{provider_id}..."
                )
                continue

            if compare_mode:
                a_idx = (idx * 2) % len(local_media)
                b_idx = (idx * 2 + 1) % len(local_media)
                media_a = local_media[a_idx]
                media_b = local_media[b_idx]
                print(f"[3/5] Segment {idx+1}: split-screen [{media_a.name}] vs [{media_b.name}]...")
                media_assets.append(_media_asset_from_path(
                    media_a,
                    source=MediaSource.LOCAL,
                    idx=idx,
                    metadata={
                        "compare_pair": [str(media_a), str(media_b)],
                        "selection": {
                            "query": "compare_mode",
                            "provider": "local",
                            "provider_id": str(media_a.resolve()),
                            "score": None,
                            "confidence": "manual",
                            "warnings": ["compare mode"],
                            "rejection_reasons": [],
                            "candidate_count": len(local_media),
                            "score_breakdown": {},
                        },
                    },
                ))
            else:
                override = broll_overrides.get(idx)
                if override:
                    source_path = str(override.get("source_path") or "").strip()
                    if source_path:
                        source_path = str(Path(source_path).expanduser().resolve())
                        if not Path(source_path).is_file():
                            raise RuntimeError(f"Source segment override does not exist: {source_path}")
                        start_sec = override.get("start_sec", override.get("source_start_sec"))
                        try:
                            start_sec = float(start_sec) if start_sec is not None else None
                        except (TypeError, ValueError):
                            start_sec = None
                        print(
                            f"[3/5] Segment {idx+1}: slicing source override "
                            f"'{Path(source_path).name}' at "
                            f"{start_sec if start_sec is not None else 2.0:.1f}s..."
                        )
                        manual_queries = list(
                            semantic_scene.provider_queries if semantic_scene
                            else provider_shot_intent.search_queries if provider_shot_intent
                            else broll_query_list(seg, niche)
                        )
                        clip = fetch_yt_clip_video(
                            manual_queries,
                            idx,
                            used_set,
                            target_duration=dur,
                            fallback=(provider_shot_intent.primary_subject if provider_shot_intent else niche),
                            narration=seg["narration"],
                            intent=provider_shot_intent,
                            clip_source=source_path,
                            segment_offset=start_sec,
                            preserve_audio=bool(override.get("preserve_audio", False)),
                        )
                        if clip:
                            media_assets.append(_media_asset_from_path(
                                clip,
                                source=MediaSource.LOCAL,
                                idx=idx,
                                metadata={
                                    "selection": {
                                        "query": "source_override",
                                        "provider": "yt_clip",
                                        "provider_id": Path(clip).stem,
                                        "source_url": source_path,
                                        "source_start_sec": start_sec,
                                        "score": None,
                                        "confidence": "manual",
                                        "manual_override": True,
                                        "warnings": ["local source segment override"],
                                        "rejection_reasons": [],
                                        "candidate_count": 1,
                                        "score_breakdown": {},
                                    }
                                },
                            ))
                            continue
                        raise RuntimeError(
                            f"Could not slice source segment override: {source_path}"
                        )
                    if "clip_path" in override:
                        print(f"[3/5] Segment {idx+1}: using user-supplied clip...")
                        clip = Path(override["clip_path"]).expanduser().resolve()
                        media_assets.append(_media_asset_from_path(
                            clip,
                            source=MediaSource.LOCAL,
                            idx=idx,
                            metadata={
                                "selection": {
                                    "query": "manual_override",
                                    "provider": "local",
                                    "provider_id": str(clip),
                                    "score": None,
                                    "confidence": "manual",
                                    "manual_override": True,
                                    "warnings": ["manual clip override"],
                                    "rejection_reasons": [],
                                    "candidate_count": 1,
                                    "score_breakdown": {},
                                }
                            },
                        ))
                        continue
                    elif "youtube_id" in override or "clip_url" in override:
                        clip_source = str(override.get("clip_url") or override.get("youtube_id") or "").strip()
                        if clip_source:
                            print(f"[3/5] Segment {idx+1}: using user-supplied YouTube override...")
                            manual_queries = list(
                                semantic_scene.provider_queries if semantic_scene
                                else provider_shot_intent.search_queries if provider_shot_intent
                                else broll_query_list(seg, niche)
                            )
                            clip = fetch_yt_clip_video(
                                manual_queries,
                                idx,
                                used_set,
                                target_duration=dur,
                                fallback=(provider_shot_intent.primary_subject if provider_shot_intent else niche),
                                narration=seg["narration"],
                                intent=provider_shot_intent,
                                clip_source=clip_source,
                            )
                            if clip:
                                media_assets.append(_media_asset_from_path(
                                    clip,
                                    source=MediaSource.LOCAL,
                                    idx=idx,
                                    metadata={
                                        "selection": {
                                            "query": "manual_override",
                                            "provider": "yt_clip",
                                            "provider_id": Path(clip).stem,
                                            "score": None,
                                            "confidence": "manual",
                                            "manual_override": True,
                                            "warnings": ["manual YouTube override"],
                                            "rejection_reasons": [],
                                            "candidate_count": 1,
                                            "score_breakdown": {},
                                        }
                                    },
                                ))
                                continue
                    elif "skip" in override:
                        print(f"[3/5] Segment {idx+1}: user skipped stock; using Gemini image...")
                        broll = generate_gemini_image(seg.get("broll") or niche, idx)
                        if broll:
                            media_assets.append(_media_asset_from_path(
                                broll,
                                source=MediaSource.GEMINI_IMAGE,
                                idx=idx,
                                metadata={
                                    "selection": {
                                        "query": seg.get("broll") or niche,
                                        "provider": "gemini_image",
                                        "provider_id": Path(broll).name,
                                        "score": None,
                                        "confidence": "fallback",
                                        "warnings": ["user skipped stock"],
                                        "rejection_reasons": [],
                                        "candidate_count": 0,
                                        "score_breakdown": {},
                                    }
                                },
                            ))
                            continue
                        print(f"    [Gemini Image] failed; falling through to stock sources.")
                        queries = list(
                            semantic_scene.provider_queries if semantic_scene
                            else provider_shot_intent.search_queries if provider_shot_intent
                            else broll_query_list(seg, niche)
                        )
                    elif "queries" in override:
                        queries = override["queries"]
                    else:
                        queries = list(
                            semantic_scene.provider_queries if semantic_scene
                            else provider_shot_intent.search_queries if provider_shot_intent
                            else broll_query_list(seg, niche)
                        )
                else:
                    queries = list(
                        semantic_scene.provider_queries if semantic_scene
                        else provider_shot_intent.search_queries if provider_shot_intent
                        else broll_query_list(seg, niche)
                    )
                manual_clip = _find_manual_input_clip(idx, local_media, provider_shot_intent)
                if manual_clip:
                    print(f"[3/5] Segment {idx+1}: using input_clips override '{manual_clip.name}'...")
                    media_assets.append(_media_asset_from_path(
                        manual_clip,
                        source=MediaSource.LOCAL,
                        idx=idx,
                        metadata={
                            "selection": {
                                "query": "manual_override",
                                "provider": "local",
                                "provider_id": str(manual_clip.resolve()),
                                "score": None,
                                "confidence": "manual",
                                "manual_override": True,
                                "warnings": ["input_clips scene/entity override"],
                                "rejection_reasons": [],
                                "candidate_count": 1,
                                "score_breakdown": {},
                            }
                        },
                    ))
                    continue
                if not queries:
                    queries = broll_query_list(seg, niche)
                visual_grammar_decision = visual_grammar_engine.decide(
                    scene_index=idx,
                    narration=seg["narration"],
                    queries=tuple(queries),
                    shot_intent=provider_shot_intent,
                )
                queries = _dedupe_runtime_queries([
                    *visual_grammar_decision.repaired_queries,
                    *queries,
                ])
                if constraint_scene:
                    constrained_queries, rejected_queries = constraint_scene.filter_queries(queries)
                    queries = list(constrained_queries or constraint_scene.query_seeds)
                    if rejected_queries:
                        _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
                            **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                            "rejected_unconstrained_runtime_queries": list(rejected_queries),
                        }
                if shot_intent:
                    _MEDIA_PLANNING_DIAGNOSTICS[idx] = {
                        **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                        "shot_plan": {
                            "domain_id": shot_plan.domain_id,
                            "primary_subject": shot_plan.primary_subject,
                            "supporting_subjects": list(shot_plan.supporting_subjects),
                            "subject_persistence_target": shot_plan.subject_persistence_target,
                            "allowed_substitutions": list(shot_plan.allowed_substitutions),
                            "forbidden_substitutions": list(shot_plan.forbidden_substitutions),
                            "visual_identity": list(shot_plan.visual_identity),
                            "style_rules": shot_plan.style_rules.to_dict(),
                            "query_budget": shot_plan.query_budget,
                        },
                        "shot_intent": shot_intent.to_dict(),
                        "canonical_scene_entity": (
                            canonical_entity_report.scene_for_index(idx).to_dict()
                            if canonical_entity_report and canonical_entity_report.scene_for_index(idx)
                            else None
                        ),
                        "scene_visual_focus": (
                            scene_visual_focus_report.scene_for_index(idx).to_dict()
                            if scene_visual_focus_report
                            and scene_visual_focus_report.scene_for_index(idx)
                            else None
                        ),
                        "semantic_query_plan": semantic_scene.to_dict() if semantic_scene else None,
                        "scene_constraints": constraint_scene.to_dict() if constraint_scene else None,
                        "visual_grammar": visual_grammar_decision.to_dict(),
                    }

                print(f"[3/5] Segment {idx+1}: fetching B-roll '{queries[0]}'...")
                broll = fetch_broll(
                    queries,
                    idx,
                    fallback=(provider_shot_intent.primary_subject if provider_shot_intent else niche),
                    local_media=local_media,
                    narration=seg["narration"],
                    used_set=used_set,
                    hybrid=hybrid,
                    threshold=threshold,
                    dalle=dalle,
                    target_duration=dur,
                    no_interactive=no_interactive,
                    shot_intent=provider_shot_intent,
                    visual_grammar_engine=visual_grammar_engine,
                    visual_grammar_decision=visual_grammar_decision,
                    provider_query_variants=(
                        semantic_scene.provider_variants if semantic_scene else None
                    ),
                    scene_constraints=constraint_scene,
                    continuity_engine=source_continuity_engine,
                    continuity_state=source_continuity_state,
                )
                if not _valid_media_path(broll):
                    print(f"    [Local explainer] replacing missing media for segment {idx+1}.")
                    broll = _generate_local_explainer_image(queries[0] if queries else niche, idx)
                    visual_grammar_engine.register_explainer()
                    _MEDIA_SELECTION_DIAGNOSTICS[idx] = {
                        **_MEDIA_SELECTION_DIAGNOSTICS.get(idx, {}),
                        **_MEDIA_PLANNING_DIAGNOSTICS.get(idx, {}),
                        "selection": {
                            "query": queries[0] if queries else niche,
                            "provider": "local",
                            "provider_id": Path(broll).name,
                            "score": None,
                            "confidence": "fallback",
                            "confidence_level": "MEDIUM",
                            "portrait_score": 10.0,
                            "relevance_score": 7.0,
                            "quality_gate_passed": True,
                            "scene_importance": _scene_importance_for_index(idx, seg["narration"]),
                            "selection_reason": "local explainer fallback replaced missing provider media",
                            "rejection_reason": "provider returned missing or empty media file",
                            "fallback_level": "local_explainer",
                            "warnings": ["provider returned missing or empty media file", "local explainer fallback"],
                            "rejection_reasons": ["provider returned missing or empty media file"],
                            "candidate_count": 0,
                            "score_breakdown": {},
                        },
                    }
                selection_metadata = _MEDIA_SELECTION_DIAGNOSTICS.get(idx, {})
                media_assets.append(_media_asset_from_path(
                    broll,
                    source=_media_source_from_selection(selection_metadata),
                    idx=idx,
                    metadata=selection_metadata,
                ))
        ctx.values["media_assets"] = media_assets
        fallback_quality_report_path = write_fallback_quality_report(media_assets)
        path = write_manifest(
            OUT_DIR / "media_manifest.json",
            {"assets": [asset.to_dict() for asset in media_assets]},
        )
        continuity_report_path = OUT_DIR / "subject_continuity_report.json"
        if isinstance(shot_plan, ShotPlan):
            continuity_report = SubjectContinuityEngine().report_from_assets(
                shot_plan,
                media_assets,
                scene_visual_focus_report=scene_visual_focus_report,
            )
            write_manifest(continuity_report_path, continuity_report.to_dict())
        source_continuity_report_path = OUT_DIR / "source_continuity_report.json"
        source_report = source_continuity_engine.build_report(
            source_continuity_state,
            total_scenes=len(ctx.values["voice_items"]),
        )
        write_manifest(source_continuity_report_path, source_report.to_dict())
        grammar_report_path = write_manifest(
            OUT_DIR / "visual_grammar_report.json",
            visual_grammar_engine.report(),
        )
        return StageResult(outputs={
            "media_manifest": str(path),
            "subject_continuity_report": str(continuity_report_path),
            "source_continuity_report": str(source_continuity_report_path),
            "visual_grammar_report": str(grammar_report_path),
            "fallback_quality_report": str(fallback_quality_report_path),
            "media_files": [str(asset.local_path) for asset in media_assets],
        })

    def load_media_selection(ctx: PipelineContext, record: StageRecord) -> None:
        payload = read_manifest(Path(record.outputs["media_manifest"]))
        ctx.values["media_assets"] = [
            MediaAsset.from_dict(asset) for asset in payload.get("assets", [])
        ]

    def stage_verified_media(ctx: PipelineContext) -> StageResult:
        """Reject bad downloaded assets before Timeline construction.

        Retrieval remains the owner of provider calls and deterministic scoring.
        This stage only asks it for a bounded replacement after frame evidence
        rejects an already-downloaded selection.
        """

        config = VerifiedMediaGateConfig.from_env(os.environ)
        gate = VerifiedMediaGate(
            config,
            verifier=_gemini_verified_media_verifier if config.enabled else None,
        )
        shot_plan = ctx.values.get("shot_plan")
        canonical_report = ctx.values.get("canonical_entity_report")
        scene_visual_focus_report = ctx.values.get("scene_visual_focus_report")
        scene_constraint_report = ctx.values.get("scene_constraint_report")
        voice_by_index = {item["idx"]: item for item in ctx.values.get("voice_items", [])}
        media_assets = list(ctx.values.get("media_assets", []))
        used_set = _load_persistent_used()
        for asset in media_assets:
            selection = (asset.metadata or {}).get("selection", {})
            provider = str(selection.get("provider") or "")
            provider_id = str(selection.get("provider_id") or "")
            if provider and provider_id:
                used_set.add(
                    provider_id
                    if provider_id.startswith(f"{provider}:")
                    else f"{provider}:{provider_id}"
                )
            used_set.add(str(asset.local_path))

        final_results = []
        attempts = []

        def write_verified_report() -> Path:
            payload = VerifiedMediaReport(
                tuple(final_results), tuple(attempts)
            ).to_dict()
            for row in payload["scenes"]:
                scene_index = int(row["scene_index"])
                if scene_index >= len(media_assets):
                    continue
                selection = (media_assets[scene_index].metadata or {}).get("selection", {})
                evidence = selection.get("evidence_verification", {}) if isinstance(selection, dict) else {}
                row["selection"] = {
                    "provider": selection.get("provider"),
                    "provider_id": selection.get("provider_id"),
                    "score": selection.get("score"),
                    "confidence": selection.get("confidence_level") or selection.get("confidence"),
                    "candidate_count": selection.get("candidate_count"),
                    "candidate_ranking": evidence.get("candidate_ranking"),
                }
            return write_manifest(OUT_DIR / "verified_media_report.json", payload)

        for idx, asset in enumerate(media_assets):
            item = voice_by_index.get(idx)
            intent = shot_plan.intent_for_index(idx) if isinstance(shot_plan, ShotPlan) else None
            provider_intent = _focused_provider_intent(
                intent,
                canonical_report,
                scene_visual_focus_report,
            ) if intent else None
            asset_metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
            locked = asset_metadata.get("critical_asset")
            if asset_metadata.get("critical_asset_lock") and isinstance(locked, dict):
                verification = locked.get("verification") or {}
                request = VerificationRequest(
                    scene_index=idx,
                    media_path=Path(asset.local_path),
                    expected_entity=str(locked.get("expected_entity") or niche),
                    expected_action=str(locked.get("expected_action") or ""),
                    visual_goal="show" if idx == 0 else "reveal",
                    priority=VerificationPriority.CRITICAL,
                )
                evidence = DownloadedMediaEvidence(
                    entity_match=True,
                    entity_confidence=float(verification.get("entity_confidence") or 1.0),
                    action_match=(True if request.expected_action else None),
                    action_confidence=float(verification.get("action_confidence") or 1.0),
                    verified_entity=str(verification.get("verified_entity") or request.expected_entity),
                    verified_action=str(verification.get("verified_action") or request.expected_action),
                    reasoning=str(verification.get("reasoning") or "pre-script critical asset verification"),
                    sampled_frames=tuple(verification.get("sampled_frames") or ()),
                    provider=str(verification.get("provider") or "gemini"),
                )
                result = VerifiedMediaSceneResult(
                    request=request,
                    decision=VerificationDecision.VERIFIED,
                    evidence=evidence,
                    reason="reused pre-script frame-verified critical asset",
                )
                asset.metadata["verified_media"] = result.to_dict()
                attempts.append(result)
                final_results.append(result)
                continue
            request = _verification_request_for_asset(idx, asset, provider_intent)
            if not request.expected_entity:
                request = replace(request, expected_entity=niche)
            result = gate.evaluate(request)
            initial_result = result
            attempts.append(result)

            # Disabled and externally unavailable verification are intentional
            # soft-pass modes. Keep the selected media and preserve an audit
            # record; do not burn replacement attempts on a provider outage.
            if result.decision in {
                VerificationDecision.VERIFIED,
                VerificationDecision.UNVERIFIED,
            }:
                asset.metadata.setdefault("verified_media", result.to_dict())
                final_results.append(result)
                continue

            original_asset = asset
            original_path = Path(original_asset.local_path)
            original_backup = None
            if (
                original_path.exists()
                and original_path.parent.resolve() == OUT_DIR.resolve()
                and original_path.stem == f"broll_{idx}"
            ):
                backup_dir = OUT_DIR / "verified_media_backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                original_backup = backup_dir / f"broll_{idx}{original_path.suffix}"
                shutil.copy2(original_path, original_backup)
            replacement = None
            queries = _replacement_queries(ctx, idx, provider_intent)
            fallback = getattr(provider_intent, "primary_subject", "") or niche
            for replacement_attempt in range(1, config.max_replacement_attempts + 1):
                if not item or not queries:
                    break
                try:
                    candidate_path = fetch_broll(
                        queries,
                        idx,
                        fallback=fallback,
                        local_media=local_media,
                        narration=item["segment"].get("narration", ""),
                        used_set=used_set,
                        # A rejected authentic asset must not be replaced by a
                        # composer/card before the gate has exhausted real media.
                        hybrid=False,
                        threshold=threshold,
                        dalle=False,
                        target_duration=item["duration"],
                        no_interactive=True,
                        shot_intent=provider_intent,
                        provider_query_variants=None,
                        scene_constraints=(
                            scene_constraint_report.scene_for_index(idx)
                            if isinstance(scene_constraint_report, SceneConstraintReport) else None
                        ),
                    )
                except (SystemExit, Exception) as exc:
                    print(f"    [Verified media] replacement retrieval failed for segment {idx + 1}: {exc}")
                    break
                if not _valid_media_path(candidate_path):
                    break
                replacement_metadata = _MEDIA_SELECTION_DIAGNOSTICS.get(idx, {})
                candidate_asset = _media_asset_from_path(
                    candidate_path,
                    source=_media_source_from_selection(replacement_metadata),
                    idx=idx,
                    metadata=replacement_metadata,
                )
                candidate_request = _verification_request_for_asset(idx, candidate_asset, provider_intent)
                if not candidate_request.expected_entity:
                    candidate_request = replace(candidate_request, expected_entity=niche)
                result = gate.evaluate(candidate_request, replacement_attempt=replacement_attempt)
                attempts.append(result)
                if result.decision is VerificationDecision.VERIFIED:
                    replacement = candidate_asset
                    media_assets[idx] = candidate_asset
                    break

            if replacement is None:
                # Never allow a rejected replacement to displace the original.
                if original_backup is not None and original_backup.exists():
                    original_backup.replace(original_path)
                media_assets[idx] = original_asset
                result = initial_result
                if request.priority is not VerificationPriority.CRITICAL:
                    result = replace(
                        initial_result,
                        decision=VerificationDecision.UNVERIFIED,
                        reason=f"{initial_result.reason}; no verified replacement available",
                    )
            elif original_backup is not None:
                try:
                    original_backup.unlink()
                except FileNotFoundError:
                    pass
            media_assets[idx].metadata.setdefault("verified_media", result.to_dict())
            final_results.append(result)

            if gate.must_abort(result):
                report_path = write_verified_report()
                raise RuntimeError(
                    "Verified Media Gate rejected critical scene "
                    f"{idx + 1} ({request.expected_entity or niche}): {result.reason}. "
                    f"See {report_path.name}."
                )

        ctx.values["media_assets"] = media_assets
        media_manifest = write_manifest(
            OUT_DIR / "media_manifest.json",
            {"assets": [asset.to_dict() for asset in media_assets]},
        )
        report_path = write_verified_report()
        fallback_report = write_fallback_quality_report(media_assets)
        return StageResult(outputs={
            "verified_media_report": str(report_path),
            "media_manifest": str(media_manifest),
            "fallback_quality_report": str(fallback_report),
            "media_files": [str(asset.local_path) for asset in media_assets],
        })

    def load_verified_media(ctx: PipelineContext, record: StageRecord) -> None:
        payload = read_manifest(Path(record.outputs["media_manifest"]))
        ctx.values["media_assets"] = [
            MediaAsset.from_dict(asset) for asset in payload.get("assets", [])
        ]

    def validate_verified_media_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        return (
            Path(record.outputs.get("verified_media_report", "")).exists()
            and Path(record.outputs.get("media_manifest", "")).exists()
            and validate_paths(record.outputs.get("media_files", []))
        )

    def stage_exact_subject_availability(ctx: PipelineContext) -> StageResult:
        """Defer identity-specific documentaries without exact selected evidence."""

        report = ExactSubjectAvailabilityGate(
            ExactSubjectGateConfig.from_env(os.environ)
        ).evaluate(
            topic=niche,
            subject=subject_definition_from_pipeline(
                editorial_canon=ctx.values.get("editorial_canon"),
                canonical_report=ctx.values.get("canonical_entity_report"),
                shot_plan=ctx.values.get("shot_plan"),
            ),
            media_assets=ctx.values.get("media_assets", ()),
            verified_media_report=read_manifest(OUT_DIR / "verified_media_report.json"),
        )
        report_path = report.write_json(OUT_DIR / "exact_subject_gate_report.json")
        ctx.values["exact_subject_gate_report"] = report
        if report.decision is ExactSubjectGateDecision.DEFERRED:
            raise RuntimeError(
                "Strict Exact Subject Availability Gate deferred this topic before rendering: "
                f"{report.failure_reason}. See {report_path.name}."
            )
        return StageResult(outputs={"exact_subject_gate_report": str(report_path)})

    def load_exact_subject_availability(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["exact_subject_gate_report"] = read_manifest(
            Path(record.outputs["exact_subject_gate_report"])
        )

    def validate_exact_subject_availability(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("exact_subject_gate_report", "")).exists()

    def validate_media_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        subject_report = record.outputs.get("subject_continuity_report")
        return (
            Path(record.outputs.get("media_manifest", "")).exists()
            and (not subject_report or Path(subject_report).exists())
            and validate_paths(record.outputs.get("media_files", []))
        )

    def stage_music(ctx: PipelineContext) -> StageResult:
        topic_meta = ctx.values["topic_metadata"]
        payload = {
            "requested_music_path": str(music_path) if music_path else "",
            "requested_music_volume": music_volume,
            "music_selection_key": f"{topic_meta.title}|{niche}",
            "music_mood": ctx.values["script"].get("music_mood"),
        }
        ctx.values["music_plan"] = payload
        path = write_manifest(OUT_DIR / "music_manifest.json", payload)
        return StageResult(outputs={"music_manifest": str(path)})

    def load_music(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["music_plan"] = read_manifest(Path(record.outputs["music_manifest"]))

    def stage_timeline(ctx: PipelineContext) -> StageResult:
        script_payload = ctx.values["script"]
        title = ctx.values["title"]
        script_model = Script.from_legacy_dict(script_payload, niche=niche)
        voice_tracks = [
            item.get("voice_track") or VoiceTrack(
                audio_path=Path(item["voice"]),
                duration_sec=float(item["duration"]),
                scene_id=str(item["idx"]),
            )
            for item in ctx.values["voice_items"]
        ]
        timeline_metadata = UploadMetadata.from_legacy_dict({
            "id": "draft",
            "title": title,
            "video_path": str(OUT_DIR / "final.mp4"),
            "niche": niche,
            "segments": script_payload.get("segments", []),
            "category_id": ctx.values["topic_metadata"].category_id,
            "orientation": "landscape" if landscape else "portrait",
            "status": "draft",
        })
        timeline = build_timeline(
            script=script_model,
            voice_tracks=voice_tracks,
            media_assets=ctx.values["media_assets"],
            upload_metadata=timeline_metadata,
            options=TimelineBuildOptions(
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                format_profile="shorts_landscape" if landscape else "shorts_vertical",
                transition_duration_sec=SHORTS_TRANSITION_DURATION,
                check_asset_files=True,
            ),
        )
        timeline.metadata.update(ctx.values["music_plan"])
        timeline.write_json(OUT_DIR / "timeline.json")
        ctx.values["timeline"] = timeline
        return StageResult(outputs={"timeline": str(OUT_DIR / "timeline.json")})

    def load_timeline(ctx: PipelineContext, record: StageRecord) -> None:
        from autovideo.domain import Timeline
        ctx.values["timeline"] = Timeline.from_json(
            Path(record.outputs["timeline"]).read_text(encoding="utf-8")
        )

    def stage_rendering(ctx: PipelineContext) -> StageResult:
        render_profile = render_profile_for(
            APP_CONFIG.render_profile.name,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            shorts_max_duration_sec=SHORTS_MAX_DURATION,
            transition_duration_sec=SHORTS_TRANSITION_DURATION,
            music_volume=music_volume,
        )
        renderer = FfmpegTimelineRenderer(
            out_dir=OUT_DIR,
            profile=render_profile,
            services=FfmpegRenderServices(
                build_segment=build_segment,
                concat_segments=concat_segments,
                media_duration=media_duration,
                build_ass=build_ass,
                burn_captions=burn_captions,
                add_background_music=add_background_music,
                run_ff=run_ff,
                move_file=shutil.move,
            ),
        )
        render_result = renderer.render(ctx.values["timeline"])
        payload = {
            "final_path": str(render_result.final_path),
            "youtube_safe_path": str(render_result.youtube_safe_path),
            "captioned_path": str(render_result.captioned_path),
            "combined_path": str(render_result.combined_path),
            "final_duration_sec": render_result.final_duration_sec,
            "youtube_safe_duration_sec": render_result.youtube_safe_duration_sec,
            "music_path": str(render_result.music_path) if render_result.music_path else None,
            "segment_paths": [str(path) for path in render_result.segment_paths],
            "captions_path": str(OUT_DIR / "captions.ass"),
        }
        write_manifest(OUT_DIR / "render_manifest.json", payload)
        ctx.values.update({
            "final": render_result.final_path,
            "final_yt_safe": render_result.youtube_safe_path,
            "total": render_result.final_duration_sec,
            "music_used": render_result.music_path,
        })
        return StageResult(outputs={
            "render_manifest": str(OUT_DIR / "render_manifest.json"),
            "final": str(render_result.final_path),
            "final_yt_safe": str(render_result.youtube_safe_path),
            "captions": str(OUT_DIR / "captions.ass"),
        })

    def load_rendering(ctx: PipelineContext, record: StageRecord) -> None:
        payload = read_manifest(Path(record.outputs["render_manifest"]))
        ctx.values.update({
            "final": Path(payload["final_path"]),
            "final_yt_safe": Path(payload["youtube_safe_path"]),
            "total": float(payload["final_duration_sec"]),
            "music_used": payload.get("music_path"),
        })

    def validate_render_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        return (
            Path(record.outputs.get("render_manifest", "")).exists()
            and Path(record.outputs.get("final", "")).exists()
            and Path(record.outputs.get("final_yt_safe", "")).exists()
            and Path(record.outputs.get("captions", "")).exists()
        )

    def stage_metadata(ctx: PipelineContext) -> StageResult:
        script_payload = ctx.values["script"]
        topic_metadata = ctx.values["topic_metadata"]
        title = ctx.values["title"]
        hashtags = list(topic_metadata.hashtags)
        hashtag_str = " ".join(hashtags)
        youtube_title = title
        if "#shorts" not in youtube_title.lower():
            candidate = f"{title} #shorts"
            if len(candidate) <= 100:
                youtube_title = candidate
        description_base = topic_metadata.description or title
        if title.lower() not in description_base[:150].lower():
            description_base = f"{title}\n\n{description_base}"
        visual_attributions = []
        for asset in ctx.values.get("media_assets", []):
            asset_metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
            selection = asset_metadata.get("selection")
            selection = selection if isinstance(selection, dict) else asset_metadata
            if str(selection.get("provider") or "").lower() not in {"coverr", "vecteezy"}:
                continue
            if str(selection.get("provider") or "").lower() == "vecteezy":
                attribution = str(selection.get("attribution") or "Vecteezy").strip()
                source_url = str(selection.get("source_url") or "https://www.vecteezy.com/").strip()
            else:
                attribution = str(selection.get("attribution") or "Coverr").strip()
                source_url = str(selection.get("source_url") or "https://coverr.co/").strip()
            credit = f"{attribution}: {source_url}"
            if credit not in visual_attributions:
                visual_attributions.append(credit)
        visual_credit_text = "; ".join(visual_attributions)
        if visual_credit_text:
            description_base = f"{description_base}\n\nVisual footage: {visual_credit_text}"
        music_selection = {}
        try:
            music_selection = read_manifest(OUT_DIR / "music_selection.json")
        except (OSError, ValueError):
            pass
        selected_track = (
            music_selection.get("track", {})
            if isinstance(music_selection, dict) else {}
        )
        license_metadata = (
            selected_track.get("license_metadata", {})
            if isinstance(selected_track, dict) else {}
        )
        music_attribution = ""
        if (
            isinstance(license_metadata, dict)
            and bool(license_metadata.get("attribution_required"))
        ):
            music_attribution = str(
                license_metadata.get("attribution_text")
                or selected_track.get("attribution_text")
                or ""
            ).strip()
            if not music_attribution:
                raise RuntimeError(
                    "Selected music requires attribution, but no attribution text is available."
                )
            description_base = f"{description_base}\n\nMusic: {music_attribution}"
        publish_slot = os.environ.get("AUTO_VIDEO_DAILY_SLOT", "").strip().lower()
        publish_key = (
            f"{dt.datetime.now():%Y-%m-%d}:{publish_slot}"
            if publish_slot else ""
        )
        if publish_key:
            description_base = f"{description_base}\n\nAutoShort-Publish-Key: {publish_key}"
        youtube_description = f"{description_base}\n\n{hashtag_str}"
        facebook_description = youtube_description
        instagram_caption = topic_metadata.instagram_caption or title
        if visual_credit_text:
            instagram_caption = f"{instagram_caption}\n\nVisual footage: {visual_credit_text}"
        instagram_caption = f"{instagram_caption}\n\n{hashtag_str}"
        youtube_tags = topic_metadata.youtube_tags
        pinned_comment = generate_pinned_comment(
            topic=niche,
            title=title,
            segments=script_payload.get("segments", []),
        )

        video_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}-{uuid.uuid4().hex[:6]}"
        artifact_store = ArtifactStore(SCRIPT_DIR)
        pending_folder = artifact_store.queue_item_path("pending", video_id)
        pending_video = pending_folder / "video.mp4"
        pending_video_yt = pending_folder / "video_yt_safe.mp4"
        metadata = {
            "id": video_id,
            "niche": niche,
            "title": title,
            "youtube_title": youtube_title,
            "youtube_description": youtube_description,
            "pinned_comment": pinned_comment,
            "facebook_description": facebook_description,
            "instagram_caption": instagram_caption,
            "hashtags": hashtags,
            "youtube_tags": youtube_tags,
            "category_id": topic_metadata.category_id,
            "segments": script_payload.get("segments", []),
            "music_mood": script_payload.get("music_mood"),
            "music_path": str(ctx.values["music_used"]) if ctx.values.get("music_used") else None,
            "music_volume": music_volume,
            "music_provider": selected_track.get("provider") if isinstance(selected_track, dict) else None,
            "music_license": license_metadata if isinstance(license_metadata, dict) else {},
            "music_attribution": music_attribution,
            "visual_attributions": visual_attributions,
            "publish_key": publish_key,
            "duration_sec": round(float(ctx.values["total"]), 2),
            "video_file": "video.mp4",
            "video_file_yt": "video_yt_safe.mp4",
            "video_path": str(pending_video.resolve()),
            "video_path_yt": str(pending_video_yt.resolve()),
            "orientation": "landscape" if landscape else "portrait",
            "status": "pending",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        UploadMetadata.from_legacy_dict(metadata)
        meta_path = OUT_DIR / "upload_metadata.json"
        write_manifest(meta_path, metadata)
        ctx.values.update({
            "metadata": metadata,
            "video_id": video_id,
            "youtube_title": youtube_title,
            "hashtag_str": hashtag_str,
            "meta_path": meta_path,
        })
        return StageResult(outputs={"metadata": str(meta_path), "video_id": video_id})

    def load_metadata(ctx: PipelineContext, record: StageRecord) -> None:
        meta_path = Path(record.outputs["metadata"])
        metadata = read_manifest(meta_path)
        ctx.values.update({
            "metadata": metadata,
            "video_id": metadata["id"],
            "youtube_title": metadata.get("youtube_title", metadata.get("title", "")),
            "hashtag_str": " ".join(metadata.get("hashtags", [])),
            "meta_path": meta_path,
        })

    def write_ffprobe_report(video_path: Path) -> Path:
        report_path = OUT_DIR / "ffprobe.json"
        try:
            raw = run_ff([
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,duration",
                "-of",
                "json",
                str(video_path),
            ])
            report_path.write_text(raw, encoding="utf-8")
        except Exception as exc:
            write_manifest(report_path, {"error": str(exc), "video_path": str(video_path)})
        return report_path

    def write_contact_sheet(video_path: Path) -> Path:
        sheet_path = OUT_DIR / "contact_sheet.jpg"
        if sheet_path.exists():
            try:
                sheet_path.unlink()
            except OSError:
                pass
        try:
            run_ff([
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(video_path),
                "-vf",
                "fps=1/5,scale=270:480,tile=4x3",
                "-frames:v",
                "1",
                str(sheet_path),
            ])
        except Exception:
            pass
        return sheet_path

    def verify_render_decode(video_path: Path) -> bool:
        null_output = "NUL" if os.name == "nt" else "/dev/null"
        try:
            run_ff([
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video_path),
                "-f",
                "null",
                null_output,
            ], timeout=120)
            return True
        except Exception:
            return False

    def write_provider_report() -> Path:
        selection_diagnostics = load_selection_diagnostics()
        provider_stats: dict[str, dict[str, Any]] = {}
        for metadata in selection_diagnostics.values():
            selection = metadata.get("selection", {}) if isinstance(metadata, dict) else {}
            selected_provider = str(selection.get("provider") or "")
            for attempt in metadata.get("selection_attempts", []) if isinstance(metadata, dict) else []:
                provider = str(attempt.get("provider") or "")
                if not provider:
                    continue
                stats = provider_stats.setdefault(provider, {
                    "searched": 0,
                    "assets_returned": 0,
                    "assets_selected": 0,
                    "assets_rejected": 0,
                    "warnings": {},
                })
                stats["searched"] += 1
                stats["assets_returned"] += int(attempt.get("candidate_count") or 0)
                stats["assets_rejected"] += len(attempt.get("rejected") or [])
                if attempt.get("accepted") or provider == selected_provider:
                    stats["assets_selected"] += 1
                for warning in attempt.get("warnings") or []:
                    warning = str(warning)
                    stats["warnings"][warning] = stats["warnings"].get(warning, 0) + 1
            if selected_provider and not metadata.get("selection_attempts"):
                stats = provider_stats.setdefault(selected_provider, {
                    "searched": 0,
                    "assets_returned": 0,
                    "assets_selected": 0,
                    "assets_rejected": 0,
                    "warnings": {},
                })
                stats["assets_returned"] += int(selection.get("candidate_count") or 1)
                stats["assets_selected"] += 1
        return write_manifest(OUT_DIR / "provider_report.json", provider_stats)

    def write_adaptive_search_report() -> Path:
        report = {
            str(idx + 1): payload
            for idx, payload in sorted(_ADAPTIVE_SEARCH_DIAGNOSTICS.items())
        }
        return write_manifest(OUT_DIR / "adaptive_search_report.json", report)

    def write_evidence_verification_report() -> Path:
        selection_diagnostics = load_selection_diagnostics()
        report = {}
        for idx in sorted(selection_diagnostics):
            item = selection_diagnostics[idx]
            selection = item.get("selection", {}) if isinstance(item, dict) else {}
            evidence = selection.get("evidence_verification") or item.get("evidence_verification") or {}
            report[str(idx + 1)] = {
                "requested_entity": evidence.get("requested_entity") or selection.get("primary_subject", ""),
                "selected_entity": evidence.get("selected_entity") or selection.get("selected_entity", ""),
                "entity_fidelity": evidence.get("entity_fidelity") or selection.get("entity_fidelity", ""),
                "metadata_confidence": evidence.get("metadata_confidence") or selection.get("metadata_confidence"),
                "vision_requested": bool(evidence.get("vision_requested")),
                "vision_invoked": bool(evidence.get("vision_invoked")),
                "post_download_vision_checked": bool(evidence.get("post_download_vision_checked")),
                "vision_result": evidence.get("vision_result"),
                "vision_confidence": evidence.get("vision_confidence"),
                "fallback_reason": evidence.get("fallback_reason") or selection.get("rejection_reason", ""),
                "candidate_ranking": evidence.get("candidate_ranking"),
                "candidate_id": evidence.get("candidate_id") or f"{selection.get('provider', '')}:{selection.get('provider_id', '')}",
                "candidate_ranking_summary": selection.get("score_breakdown", {}).get("_evidence_verification_value", evidence),
            }
        return write_manifest(OUT_DIR / "evidence_verification_report.json", report)

    def load_selection_diagnostics() -> dict[int, dict[str, Any]]:
        if _MEDIA_SELECTION_DIAGNOSTICS:
            return dict(_MEDIA_SELECTION_DIAGNOSTICS)
        manifest_path = OUT_DIR / "media_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        diagnostics: dict[int, dict[str, Any]] = {}
        for idx, asset in enumerate(manifest.get("assets") or []):
            if not isinstance(asset, dict):
                continue
            metadata = asset.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            selection = metadata.get("selection")
            if not isinstance(selection, dict):
                selection = {
                    "provider": metadata.get("provider"),
                    "provider_id": metadata.get("provider_asset_id"),
                    "query": metadata.get("selected_query"),
                    "media_mode": metadata.get("media_mode"),
                    "confidence": metadata.get("confidence"),
                    "confidence_level": metadata.get("confidence_level"),
                    "selection_reason": metadata.get("selection_reason"),
                    "rejection_reason": metadata.get("rejection_reason"),
                    "candidate_count": metadata.get("candidate_count"),
                }
            diagnostics[idx] = {
                "selection": selection,
                "shot_intent": {
                    "media_mode": metadata.get("media_mode"),
                    "scene_importance": metadata.get("scene_importance"),
                    "scene_type": metadata.get("scene_type"),
                },
            }
        return diagnostics

    def stage_rendered_visual_qa(ctx: PipelineContext) -> StageResult:
        """Check a bounded set of final rendered frames before publishing.

        Only scenes that should visibly show or prove a concrete entity are
        sampled. Explain/transition scenes are intentionally excluded because
        their diagrams and motion graphics are valid non-literal visuals.
        """

        config = RenderedVisualQAConfig.from_env(os.environ)
        timeline = ctx.values.get("timeline")
        shot_plan = ctx.values.get("shot_plan")
        focus_report = ctx.values.get("scene_visual_focus_report")
        intent_by_index = {
            intent.scene_index: intent
            for intent in getattr(shot_plan, "intents", ())
        }
        candidates = []
        for scene in getattr(timeline, "scenes", ()):
            intent = intent_by_index.get(scene.index)
            if intent is None:
                continue
            visual_goal = str(getattr(getattr(intent, "visual_goal", None), "value", getattr(intent, "visual_goal", "show"))).lower()
            if visual_goal not in {"show", "reveal", "prove"}:
                continue
            priority = _verification_priority_for_intent(intent).value
            focus = (
                focus_report.scene_for_index(scene.index)
                if isinstance(focus_report, SceneVisualFocusReport) else None
            )
            expected_entity = str(
                getattr(focus, "required_visual_entity", "")
                or getattr(intent, "requested_entity", "")
                or getattr(getattr(intent, "scene_entity", None), "canonical_entity", "")
                or getattr(intent, "primary_subject", "")
                or niche
            )
            timestamp = scene.start_sec + (scene.duration_sec / 2.0)
            candidates.append((
                0 if priority == VerificationPriority.CRITICAL.value else 1,
                scene.index,
                RenderedSceneRequest(
                    scene_index=scene.index,
                    expected_entity=expected_entity,
                    visual_goal=visual_goal,
                    media_mode=str(getattr(getattr(intent, "media_mode", None), "value", getattr(intent, "media_mode", ""))),
                    timestamp_sec=timestamp,
                    frame_path=(
                        _extract_rendered_scene_frame(Path(ctx.values["final"]), timestamp, scene.index)
                        if config.enabled else OUT_DIR / "rendered_visual_qa" / f"scene_{scene.index:02d}.jpg"
                    ),
                    priority=priority,
                ),
            ))
        requests = [item[2] for item in sorted(candidates)[:config.max_scenes]]
        report = RenderedVisualQAGate(
            config,
            verifier=_gemini_rendered_visual_verifier if config.enabled else None,
        ).evaluate(requests)
        report_path = report.write_json(OUT_DIR / "rendered_visual_qa_report.json")
        ctx.values["rendered_visual_qa_report"] = report.to_dict()
        warnings = [
            f"scene {scene.request.scene_index + 1}: {scene.reason}"
            for scene in report.scenes
            if scene.decision.value in {"mismatch", "unavailable"}
        ]
        print(
            "[rendered QA] "
            f"checked={len(report.scenes)} mismatches={sum(scene.decision.value == 'mismatch' for scene in report.scenes)}"
        )
        return StageResult(
            outputs={"rendered_visual_qa_report": str(report_path)},
            warnings=warnings,
        )

    def load_rendered_visual_qa(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["rendered_visual_qa_report"] = read_manifest(
            Path(record.outputs["rendered_visual_qa_report"])
        )

    def validate_rendered_visual_qa_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("rendered_visual_qa_report", "")).exists()

    def stage_publish_quality_gate(ctx: PipelineContext) -> StageResult:
        video_path = Path(ctx.values["final"])
        ffprobe_path = write_ffprobe_report(video_path)
        contact_sheet_path = write_contact_sheet(video_path)
        evidence_report_path = write_evidence_verification_report()
        artifacts = PublishQualityArtifacts(
            video_path=video_path,
            captions_path=OUT_DIR / "captions.ass",
            timeline_path=OUT_DIR / "timeline.json",
            media_manifest_path=OUT_DIR / "media_manifest.json",
            ffprobe_path=ffprobe_path,
            fallback_quality_path=OUT_DIR / "fallback_quality_report.json",
            audio_mix_path=OUT_DIR / "audio_mix_report.json",
            evidence_verification_path=evidence_report_path,
            contact_sheet_path=contact_sheet_path,
            decode_verified=verify_render_decode(video_path),
            verified_media_path=OUT_DIR / "verified_media_report.json",
            rendered_visual_qa_path=OUT_DIR / "rendered_visual_qa_report.json",
        )
        report = PublishQualityGate(PublishQualityConfig.from_format_profile(_FORMAT_PROFILE)).evaluate(artifacts)
        report_path = report.write_json(OUT_DIR / "publish_quality_report.json")
        ctx.values["publish_quality_report"] = report.to_dict()
        final_seconds = float(ctx.values.get("total") or 0.0)
        actual_seconds = _STORY_REPORT.get("actual_narration_sec")
        if actual_seconds:
            transitions = max(0, int(_STORY_REPORT.get("beat_count", 0)) - 1) * _FORMAT_PROFILE.transition_duration_sec
            if final_seconds and (actual_seconds + transitions) > _FORMAT_PROFILE.max_duration_sec and final_seconds <= _FORMAT_PROFILE.max_duration_sec:
                _STORY_REPORT["renderer_tail_trim"] = True
        _STORY_REPORT["final_video_sec"] = round(final_seconds, 2) if final_seconds else None
        _STORY_REPORT["average_scene_sec"] = round(final_seconds / max(1, int(_STORY_REPORT.get("beat_count", 0))), 2) if final_seconds else None
        estimated = _STORY_REPORT.get("estimated_narration_sec")
        if estimated and final_seconds:
            _STORY_REPORT["trim_percentage"] = round(max(0.0, 1.0 - (final_seconds / estimated)) * 100.0, 1)
        _write_story_report()
        warnings = [
            f"{check.name}: {check.message}"
            for check in report.checks
            if check.severity.value in {"WARNING", "DEFER", "BLOCK"}
        ]
        print(f"[quality] Publish decision: {report.verdict.value}")
        for warning in warnings:
            print(f"    [quality] {warning}")
        return StageResult(
            outputs={
                "publish_quality_report": str(report_path),
                "verdict": report.verdict.value,
                "ffprobe": str(ffprobe_path),
                "contact_sheet": str(contact_sheet_path),
            },
            warnings=warnings,
        )

    def load_publish_quality_gate(ctx: PipelineContext, record: StageRecord) -> None:
        ctx.values["publish_quality_report"] = read_manifest(
            Path(record.outputs["publish_quality_report"])
        )

    def validate_publish_quality_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        return Path(record.outputs.get("publish_quality_report", "")).exists()

    def write_selection_report(metadata: dict) -> Path:
        selection_diagnostics = load_selection_diagnostics()
        lines = [
            "=" * 50,
            f"Selection Report: {metadata.get('title') or niche}",
            "=" * 50,
            "",
        ]
        for idx in sorted(selection_diagnostics):
            item = selection_diagnostics[idx]
            selection = item.get("selection", {}) if isinstance(item, dict) else {}
            shot_intent = item.get("shot_intent", {}) if isinstance(item, dict) else {}
            lines.extend([
                f"Scene {idx + 1}",
                f"Narration: {(metadata.get('segments') or [{}])[idx].get('narration', '') if idx < len(metadata.get('segments') or []) else ''}",
                f"Provider: {selection.get('provider', '')}",
                f"Provider ID: {selection.get('provider_id', '')}",
                f"Media Mode: {selection.get('media_mode') or shot_intent.get('media_mode', '')}",
                f"Primary Subject: {selection.get('primary_subject', '')}",
                f"Subject Visible: {selection.get('subject_visible', '')}",
                f"Continuity: {selection.get('continuity_reason', '')}",
                f"Confidence: {selection.get('confidence_level') or selection.get('confidence', '')}",
                f"Query: {selection.get('query') or selection.get('selected_query', '')}",
                f"Reason: {selection.get('selection_reason', '')}",
                f"Rejected: {selection.get('rejection_reason', '')}",
                "",
            ])
        report_path = OUT_DIR / "selection_report.txt"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    def copy_queue_snapshot(pending_folder: Path, files: dict[str, Path]) -> dict[str, str]:
        pending_folder.mkdir(parents=True, exist_ok=True)
        copied: dict[str, str] = {}
        for name, source in files.items():
            source = Path(source)
            if not source.exists() or not source.is_file():
                continue
            target = pending_folder / name
            try:
                shutil.copy2(str(source), str(target))
                copied[name] = str(target)
            except OSError:
                continue
        return copied

    def stage_queue_creation(ctx: PipelineContext) -> StageResult:
        metadata = ctx.values["metadata"]
        video_id = ctx.values["video_id"]
        artifact_store = ArtifactStore(SCRIPT_DIR)
        queue = FilesystemQueue(artifact_store.queue_root())
        ffprobe_path = OUT_DIR / "ffprobe.json"
        if not ffprobe_path.exists():
            ffprobe_path = write_ffprobe_report(Path(ctx.values["final"]))
        contact_sheet_path = OUT_DIR / "contact_sheet.jpg"
        if not contact_sheet_path.exists():
            contact_sheet_path = write_contact_sheet(Path(ctx.values["final"]))
        provider_report_path = write_provider_report()
        adaptive_search_report_path = write_adaptive_search_report()
        evidence_verification_report_path = OUT_DIR / "evidence_verification_report.json"
        if not evidence_verification_report_path.exists():
            evidence_verification_report_path = write_evidence_verification_report()
        selection_report_path = write_selection_report(metadata)
        existing = queue.find(video_id)
        if existing is None:
            queue.create_pending(
                video_id,
                metadata,
                artifacts={
                    "video.mp4": ctx.values["final"],
                    "video_yt_safe.mp4": ctx.values["final_yt_safe"],
                },
            )
        pending_folder = artifact_store.queue_item_path("pending", video_id)
        snapshot_files = {
            "scheduler_report.json": OUT_DIR / "scheduler_report.json",
            "documentary_viability_report.json": OUT_DIR / "documentary_viability_report.json",
            "editorial_identity_report.json": OUT_DIR / "editorial_identity_report.json",
            "source_coverage_report.json": OUT_DIR / "source_coverage_report.json",
            "critical_asset_plan.json": OUT_DIR / "critical_asset_plan.json",
            "timeline.json": OUT_DIR / "timeline.json",
            "editorial_canon.json": OUT_DIR / "editorial_canon.json",
            "primary_subject_lock_report.json": OUT_DIR / "primary_subject_lock_report.json",
            "scene_role_report.json": OUT_DIR / "scene_role_report.json",
            "domain_classification_report.json": OUT_DIR / "domain_classification_report.json",
            "scene_entity_report.json": OUT_DIR / "scene_entity_report.json",
            "scene_visual_focus_report.json": OUT_DIR / "scene_visual_focus_report.json",
            "query_generation_report.json": OUT_DIR / "query_generation_report.json",
            "canonical_entity_report.json": OUT_DIR / "canonical_entity_report.json",
            "semantic_query_report.json": OUT_DIR / "semantic_query_report.json",
            "scene_constraint_report.json": OUT_DIR / "scene_constraint_report.json",
            "shot_plan.json": OUT_DIR / "shot_plan.json",
            "visual_grammar_report.json": OUT_DIR / "visual_grammar_report.json",
            "subject_continuity_report.json": OUT_DIR / "subject_continuity_report.json",
            "fallback_quality_report.json": OUT_DIR / "fallback_quality_report.json",
            "publish_quality_report.json": OUT_DIR / "publish_quality_report.json",
            "rendered_visual_qa_report.json": OUT_DIR / "rendered_visual_qa_report.json",
            "provider_report.json": provider_report_path,
            "adaptive_search_report.json": adaptive_search_report_path,
            "evidence_verification_report.json": evidence_verification_report_path,
            "verified_media_report.json": OUT_DIR / "verified_media_report.json",
            "exact_subject_gate_report.json": OUT_DIR / "exact_subject_gate_report.json",
            "selection_report.txt": selection_report_path,
            "contact_sheet.jpg": contact_sheet_path,
            "media_manifest.json": OUT_DIR / "media_manifest.json",
            "ffprobe.json": ffprobe_path,
            "audio_mix_report.json": OUT_DIR / "audio_mix_report.json",
            "upload_metadata.json": ctx.values["meta_path"],
        }
        copied_snapshot = copy_queue_snapshot(pending_folder, snapshot_files)
        payload = {
            "video_id": video_id,
            "queue_folder": str(pending_folder),
            "pending_video": str(pending_folder / "video.mp4"),
            "pending_video_yt": str(pending_folder / "video_yt_safe.mp4"),
            "snapshot_artifacts": copied_snapshot,
        }
        path = write_manifest(OUT_DIR / "queue_manifest.json", payload)
        ctx.values["queue_folder"] = pending_folder
        return StageResult(outputs={**payload, "queue_manifest": str(path)})

    def load_queue_creation(ctx: PipelineContext, record: StageRecord) -> None:
        artifact_store = ArtifactStore(SCRIPT_DIR)
        queue = FilesystemQueue(artifact_store.queue_root())
        item = queue.find(record.outputs["video_id"])
        ctx.values["queue_folder"] = item.folder if item else Path(record.outputs["queue_folder"])

    def validate_queue_outputs(_ctx: PipelineContext, record: StageRecord) -> bool:
        artifact_store = ArtifactStore(SCRIPT_DIR)
        queue = FilesystemQueue(artifact_store.queue_root())
        return queue.find(record.outputs.get("video_id", "")) is not None

    fingerprint_payload = {
        "topic": niche,
        "duration": duration,
        "duration_reason": duration_reason,
        "compare": compare_mode,
        "hybrid": hybrid,
        "threshold": threshold,
        "dalle": dalle,
        "landscape": landscape,
        "reuse_script": reuse_script,
        "music": str(music_path or ""),
        "music_volume": music_volume,
        "review_broll": review_broll,
        "no_interactive": no_interactive,
        "width": WIDTH,
        "height": HEIGHT,
        "format": _FORMAT_PROFILE.name,
        "platform_max_duration_sec": _FORMAT_PROFILE.max_duration_sec,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    context = PipelineContext(
        root_dir=SCRIPT_DIR,
        output_dir=OUT_DIR,
        run_id=uuid.uuid4().hex,
        fingerprint=fingerprint,
        topic=niche,
    )
    stages = [
        PipelineStage("topic_selection", stage_topic_selection, load=load_topic_selection),
        PipelineStage(
            "documentary_viability",
            stage_documentary_viability,
            load=load_documentary_viability,
            validate_outputs=validate_documentary_viability,
        ),
        PipelineStage(
            "critical_asset_discovery",
            stage_critical_asset_discovery,
            load=load_critical_asset_discovery,
            validate_outputs=validate_critical_asset_discovery,
        ),
        PipelineStage("script_generation", stage_script_generation, load=load_script_generation),
        PipelineStage(
            "media_planning",
            stage_media_planning,
            load=load_media_planning,
            validate_outputs=validate_media_planning,
        ),
        PipelineStage(
            "editorial_identity",
            stage_editorial_identity,
            load=load_editorial_identity,
            validate_outputs=validate_editorial_identity,
        ),
        PipelineStage(
            "canonical_entity_resolution",
            stage_canonical_entity_resolution,
            load=load_canonical_entity_resolution,
            validate_outputs=validate_canonical_entity_resolution,
        ),
        PipelineStage(
            "scene_visual_focus",
            stage_scene_visual_focus,
            load=load_scene_visual_focus,
            validate_outputs=validate_scene_visual_focus,
        ),
        PipelineStage(
            "scene_constraint_planning",
            stage_scene_constraint_planning,
            load=load_scene_constraint_planning,
            validate_outputs=validate_scene_constraint_planning,
        ),
        PipelineStage(
            "semantic_query_planning",
            stage_semantic_query_planning,
            load=load_semantic_query_planning,
            validate_outputs=validate_semantic_query_planning,
        ),
        PipelineStage(
            "source_coverage",
            stage_source_coverage,
            load=load_source_coverage,
            validate_outputs=validate_source_coverage,
        ),
        PipelineStage("voice_generation", stage_voice_generation, load=load_voice_generation, validate_outputs=validate_voice_outputs),
        PipelineStage("media_selection", stage_media_selection, load=load_media_selection, validate_outputs=validate_media_outputs),
        PipelineStage(
            "verified_media",
            stage_verified_media,
            load=load_verified_media,
            validate_outputs=validate_verified_media_outputs,
        ),
        PipelineStage(
            "exact_subject_availability",
            stage_exact_subject_availability,
            load=load_exact_subject_availability,
            validate_outputs=validate_exact_subject_availability,
        ),
        PipelineStage("music", stage_music, load=load_music),
        PipelineStage("timeline_construction", stage_timeline, load=load_timeline),
        PipelineStage("rendering", stage_rendering, load=load_rendering, validate_outputs=validate_render_outputs),
        PipelineStage(
            "rendered_visual_qa",
            stage_rendered_visual_qa,
            load=load_rendered_visual_qa,
            validate_outputs=validate_rendered_visual_qa_outputs,
        ),
        PipelineStage("metadata", stage_metadata, load=load_metadata),
        PipelineStage(
            "publish_quality_gate",
            stage_publish_quality_gate,
            load=load_publish_quality_gate,
            validate_outputs=validate_publish_quality_outputs,
        ),
        PipelineStage("queue_creation", stage_queue_creation, load=load_queue_creation, validate_outputs=validate_queue_outputs),
    ]
    if coverage_preflight_only:
        coverage_stage_index = next(
            index for index, stage in enumerate(stages) if stage.name == "source_coverage"
        )
        stages = stages[:coverage_stage_index + 1]
    orchestrator = PipelineOrchestrator(
        stages,
        PipelineStateStore(OUT_DIR / "pipeline_state.json"),
    )
    orchestrator.run(
        context,
        resume=True,
        force=os.environ.get("AUTO_VIDEO_FORCE_RERUN", "").strip() == "1",
    )

    if coverage_preflight_only:
        report = context.values.get("source_coverage", {})
        ratio = float(report.get("coverage_ratio", 0.0)) if isinstance(report, dict) else 0.0
        print(f"[Qualification] Source coverage approved ({ratio:.0%}); stopping before voice generation.")
        return

    final = context.values["final"]
    final_yt_safe = context.values["final_yt_safe"]
    total = float(context.values["total"])
    music_used = context.values.get("music_used")
    video_id = context.values["video_id"]
    youtube_title = context.values["youtube_title"]
    hashtag_str = context.values["hashtag_str"]
    meta_path = context.values["meta_path"]

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
