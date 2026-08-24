"""Story-driven duration and quality planning.

The story/script determines the natural length of a video. The active
``FormatProfile`` provides only the platform ceiling. This module holds
the pure, testable pieces of that policy:

* Story-beat metadata (roles, importance, merge/remove flags).
* Word-count estimation (used for *planning* only; actual narration
  timestamps are the source of truth once TTS runs).
* Ceiling budgeting with a safety margin so the renderer's hard tail
  trim almost never fires.
* Structural beat validation (hook + conclusion present, sane order,
  enough beats).
* Story quality scoring (0-10 criteria) and analytics for
  ``output/story_report.json``.

No LLM calls happen here -- ``auto_short`` owns provider failover and
invokes the prompt builders in this module.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .profiles import FormatProfile

STORY_BEAT_ROLES: tuple[str, ...] = (
    "hook",
    "context",
    "setup",
    "discovery",
    "conflict",
    "escalation",
    "turning_point",
    "climax",
    "resolution",
    "interesting_fact",
    "conclusion_cta",
)

# Roles that must survive any ceiling trim.
PROTECTED_ROLES: frozenset[str] = frozenset(
    {"hook", "climax", "resolution", "conclusion_cta"}
)

# Roles that form the core narrative; only "supporting" roles are
# candidates for merging when the story exceeds the platform ceiling.
CORE_ROLES: frozenset[str] = frozenset(STORY_BEAT_ROLES) - frozenset(
    {"context", "interesting_fact"}
)

ROLE_PRIORITY: dict[str, int] = {
    "hook": 10,
    "climax": 9,
    "conclusion_cta": 9,
    "resolution": 8,
    "turning_point": 7,
    "discovery": 6,
    "conflict": 6,
    "escalation": 6,
    "setup": 5,
    "context": 4,
    "interesting_fact": 3,
}

DEFAULT_STORY_SCAFFOLD: tuple[str, ...] = (
    "hook",
    "context",
    "setup",
    "discovery",
    "conflict",
    "escalation",
    "turning_point",
    "climax",
    "resolution",
    "interesting_fact",
    "conclusion_cta",
)

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")


def count_words(text: str) -> int:
    """Count spoken narration words (same rule as the legacy pipeline).

    Contractions like "it's" and "fox's" count as a single word.
    """
    if not isinstance(text, str):
        return 0
    return len(_WORD_RE.findall(text))


def segment_words(segments: list[dict[str, Any]]) -> list[int]:
    return [count_words(str(seg.get("narration") or "")) for seg in segments]


def story_roles(script: dict[str, Any]) -> list[str]:
    """Return the beat role of each segment (normalized)."""
    roles = []
    for seg in script.get("segments") or []:
        role = str(seg.get("beat_role") or "").strip().casefold().replace(" ", "_")
        roles.append(role if role in ROLE_PRIORITY else "discovery")
    return roles


def estimate_story_duration(
    script: dict[str, Any],
    profile: FormatProfile,
    conservative: bool = True,
) -> float:
    """Estimate finished narration length from words (planning only).

    Uses the *lower* words-per-second bound for a conservative
    (longer) estimate so ceiling trimming never under-trims.
    """
    wps = profile.narration_words_per_sec_min if conservative else profile.narration_words_per_sec_max
    total = sum(segment_words(script.get("segments") or []))
    if wps <= 0:
        return 0.0
    return total / wps


def voice_budget_seconds(
    profile: FormatProfile,
    segment_count: int,
    safety_margin: float = 0.5,
    renderer_tolerance_sec: float | None = None,
) -> float:
    """Narration ceiling after transitions and a small safety margin.

    The renderer tolerates combined (narration + transitions) length up to
    ``max_duration_sec`` plus ``AUTO_VIDEO_DURATION_TOLERANCE_SEC`` (default
    1.0s) and may retime narration up to ``narration_max_retime_tempo``.
    The budget mirrors that acceptance so a natural ~60s story is not
    rejected for a 60s platform; the small margin still keeps the renderer's
    hard tail-trim from firing under normal TTS variance.
    """
    if renderer_tolerance_sec is None:
        try:
            renderer_tolerance_sec = float(
                os.environ.get("AUTO_VIDEO_DURATION_TOLERANCE_SEC", "1.0").strip() or "1.0"
            )
        except ValueError:
            renderer_tolerance_sec = 1.0
    transitions = max(0, segment_count - 1) * profile.transition_duration_sec
    return max(
        1.0,
        float(profile.max_duration_sec) + float(renderer_tolerance_sec)
        - transitions - safety_margin,
    )


def validate_beat_structure(
    script: dict[str, Any],
    profile: FormatProfile,
) -> tuple[list[str], list[str]]:
    """Content-based story validation. Never judges duration.

    Returns (fatal, soft) note lists. A script is structurally broken
    when it lacks a hook, lacks a conclusion CTA, has fewer beats than
    the profile floor, or contains empty/unspeakable segments.
    """
    fatal: list[str] = []
    soft: list[str] = []
    segments = script.get("segments") or []
    if not segments:
        fatal.append("script has no segments")
        return fatal, soft

    roles = story_roles(script)
    if "hook" not in roles:
        fatal.append("story is missing a hook beat (first segment must open on tension)")
    if "conclusion_cta" not in roles:
        fatal.append("story is missing a conclusion/CTA beat (last segment must end with the channel CTA)")

    if len(segments) < profile.min_story_beats:
        fatal.append(
            f"story has {len(segments)} beats, below the {profile.min_story_beats}-beat floor"
        )

    for idx, seg in enumerate(segments, start=1):
        narration = str(seg.get("narration") or "").strip()
        broll = str(seg.get("broll") or "").strip()
        words = count_words(narration)
        if not narration:
            fatal.append(f"segment {idx} has no narration")
        elif words < profile.narration_words_per_segment_min:
            fatal.append(
                f"segment {idx} narration is too short for TTS ({words} words, "
                f"hard minimum {profile.narration_words_per_segment_min})"
            )
        if not broll:
            fatal.append(f"segment {idx} is missing broll")

    hook_present = roles and roles[0] == "hook"
    cta_present = roles and roles[-1] == "conclusion_cta"
    if hook_present and cta_present and len(roles) > 2:
        if "hook" in roles[1:] or "conclusion_cta" in roles[:-1]:
            soft.append("beat roles are not in narrative order")
    return fatal, soft


def merge_suggestions(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank segments as trim candidates (supporting beats first).

    The trimming LLM pass uses this only as guidance; it always keeps
    protected roles and beats flagged ``can_remove=False``.
    """
    candidates = []
    for idx, seg in enumerate(segments):
        role = str(seg.get("beat_role") or "").strip().casefold().replace(" ", "_")
        can_remove = str(seg.get("beat_can_remove") or "true").strip().casefold() != "false"
        can_merge = str(seg.get("beat_can_merge") or "true").strip().casefold() != "false"
        candidates.append({
            "index": idx,
            "role": role if role in ROLE_PRIORITY else "discovery",
            "priority": ROLE_PRIORITY.get(role if role in ROLE_PRIORITY else "discovery", 5),
            "can_merge": can_merge,
            "can_remove": can_remove,
        })
    protected = {c["index"] for c in candidates if c["role"] in PROTECTED_ROLES}
    candidates.sort(
        key=lambda c: (
            # Hard-protected beats (can't be removed or merged) always last.
            not (c["can_merge"] and c["can_remove"]),
            c["index"] in protected,
            # Least important supporting beats first (ascending priority).
            c["priority"],
            c["index"],
        )
    )
    return candidates


def build_planner_prompt(niche: str, critical_visuals: str) -> str:
    return f"""You are a documentary story planner. Topic: "{niche}".

Plan the complete natural story as a connected short film. The finished
video will be published as a vertical short, but you are NOT limited to a
fixed number of beats or a fixed duration -- the story's own narrative
structure decides how many beats it needs. A simple curiosity explainer
might need 6-8 beats; a complex documentary might need 12-18+.

CONFIRMED CRITICAL VISUALS (already downloaded and frame-verified):
{critical_visuals}

Return STRICT JSON only (no markdown, no backticks, no preamble):
{{
  "title": "one punchy 5-8 word curious declarative statement (no question, no #shorts, no academic label)",
  "complexity": "simple" or "complex",
  "beats": [
    {{
      "role": "one of: hook, context, setup, discovery, conflict, escalation, turning_point, climax, resolution, interesting_fact, conclusion_cta",
      "purpose": "one sentence: what this beat does for the story",
      "importance": 1-10,
      "can_merge": true,
      "can_remove": true,
      "critical_asset_dependency": true,
      "expected_words": 8-45
    }}
  ]
}}

Rules:
- Beat roles must follow a coherent story order: hook -> context/setup ->
  discovery/conflict/escalation -> turning_point/climax -> resolution ->
  conclusion_cta.
- The FIRST beat is always the hook: a concrete problem, danger, mystery,
  or surprising mechanism. Never a greeting, lesson title, or "did you know".
- The LAST beat is always the conclusion_cta soft CTA.
- Locked critical visuals MUST appear in the beat(s) whose narration and
  b-roll will name the confirmed entity and visible action. Mark those beats
  "critical_asset_dependency": true and "can_remove": false.
- Simple curiosities get fewer, denser beats; deep documentaries spread the
  evidence over more beats. Never compute the beat count from duration.
"""


def build_writer_prompt(
    niche: str,
    beats: list[dict[str, Any]],
    critical_visuals: str,
    critical_lock_rules: str,
    profile: FormatProfile,
) -> str:
    beat_lines = []
    for index, beat in enumerate(beats, start=1):
        beat_lines.append(
            f"- Beat {index}: role={beat.get('role')}; purpose: {beat.get('purpose', '')} "
            f"(importance {beat.get('importance', 5)}/10, "
            f"can_merge={str(beat.get('can_merge', True)).lower()}, "
            f"can_remove={str(beat.get('can_remove', True)).lower()}, "
            f"critical_asset_dependency={str(beat.get('critical_asset_dependency', False)).lower()})"
        )
    beat_plan = "\n".join(beat_lines)
    return f"""You are scripting a high-retention vertical documentary about: "{niche}".

Write the complete story, one spoken segment per beat. The story decides the
length -- do not compress or pad to hit any duration target. The only limit is
the platform ceiling of about {profile.max_duration_sec} seconds.

STORY PLAN (write every beat in this exact order; you MAY merge two thin
beats into one segment, and if you do, set "beat_role" to the more important
role and list the others in "merged_from"):
{beat_plan}

CONFIRMED CRITICAL VISUALS (already downloaded and frame-verified):
{critical_visuals}

Style target:
- Sound like a cinematic nature documentary, not a textbook or a list of facts.
- Write for one human listener. Use simple, spoken words and natural pauses.
- Build one connected short film: opening image -> subject's need -> obstacle
  or tension -> action and mechanism -> cost or stakes -> reveal -> quiet payoff.
- Keep scientific details inside the action: explain only what the viewer
  needs to understand what is happening on screen.
- Never invent material properties, precise measurements, motives, or claims
  not established by the topic.
- First segment is the strongest visual line, creates tension immediately, and
  opens on a concrete problem/danger/mystery/surprising mechanism. Its primary
  b-roll must promise motion or impact, never a static map or abstract graphic.

Return STRICT JSON only (no markdown, no backticks, no preamble) in this shape:
{{
  "title": "one punchy 5-8 word curious declarative statement (no question, #shorts, or academic label)",
  "description": "2-3 sentence YouTube/Facebook description, hook + value, no hashtags inside",
  "instagram_caption": "1-2 sentence Instagram caption with a soft CTA, no hashtags inside",
  "music_mood": "one of: mysterious, inspiring, dramatic, warm, curious, urgent",
  "hashtags": ["#tag1", "#tag2", "..."],
  "segments": [
    {{"narration": "one or two spoken sentences, conversational and visual",
      "broll": "3-5 word stock-footage search term with subject + action",
      "broll_queries": ["subject action close up", "subject in environment wide shot", "subject detail shot", "safe exact-subject fallback"],
      "beat_role": "one beat role from the story plan",
      "beat_importance": 1-10,
      "beat_can_merge": true,
      "beat_can_remove": true,
      "critical_asset_dependency": false}}
  ]
}}

Rules:
- One segment per beat (or per merged pair). Never invent beats.
- Each segment must be at least {profile.narration_words_per_segment_min} spoken words and must read as complete sentences.
- The LAST segment must end with a soft CTA that includes the EXACT channel name "The Wild Mechanics" (these literal words). Pick one pattern: "Subscribe to The Wild Mechanics for more.", "Follow The Wild Mechanics for more like this.", "Stay curious - The Wild Mechanics posts daily."
- STRICT ALIGNMENT: each segment's "broll" and "broll_queries" MUST match the subject and action of that segment's narration.
- Each "broll_queries" list has exactly 4 concrete searches: close-up/action, environment/wide, detail, and safe exact-subject fallback. For segment 1 the first query is the strongest motion/action query.
- For space/ocean topics include real-object NASA-friendly terms.
- Vary shot type from the previous segment when possible.
- 10-15 lowercase hashtags, each prefixed with #, no spaces. Do NOT include #shorts.
- Narration is plain spoken English: no emojis, no hashtags, no stage directions.
- "broll" must be concrete nouns a stock service would actually have.
{critical_lock_rules}
"""


def build_single_pass_prompt(
    niche: str,
    critical_visuals: str,
    critical_lock_rules: str,
    profile: FormatProfile,
) -> str:
    """One-pass writer prompt (used when the Story Planner is disabled).

    The writer discovers the beat structure itself -- the scene count is
    never derived from duration or a fixed default.
    """
    return f"""You are scripting a high-retention vertical documentary about: "{niche}".

Write the complete natural story. The story decides how many beats it needs --
a simple curiosity explainer might need 6-8 segments, a complex documentary
12-18+ (or fewer if the story is tight). The only limit is the platform ceiling
of about {profile.max_duration_sec} seconds; do NOT compress or pad the story to
hit any specific duration.

CONFIRMED CRITICAL VISUALS (already downloaded and frame-verified):
{critical_visuals}

Style target:
- Sound like a cinematic nature documentary, not a textbook or a list of facts.
- Write for one human listener. Use simple, spoken words and natural pauses.
- Build one connected short film: opening image -> subject's need -> obstacle
  or tension -> action and mechanism -> cost or stakes -> reveal -> quiet payoff.
- Keep scientific details inside the action. Never invent claims not established
  by the topic.
- First segment is the strongest visual line, opens on a concrete problem,
  danger, mystery, or surprising mechanism. Its primary b-roll must promise
  motion or impact, never a static map or abstract graphic.

Return STRICT JSON only (no markdown, no backticks, no preamble) in this shape:
{{
  "title": "one punchy 5-8 word curious declarative statement (no question, #shorts, or academic label)",
  "description": "2-3 sentence YouTube/Facebook description, hook + value, no hashtags inside",
  "instagram_caption": "1-2 sentence Instagram caption with a soft CTA, no hashtags inside",
  "music_mood": "one of: mysterious, inspiring, dramatic, warm, curious, urgent",
  "hashtags": ["#tag1", "#tag2", "..."],
  "segments": [
    {{"narration": "one or two spoken sentences, conversational and visual",
      "broll": "3-5 word stock-footage search term with subject + action",
      "broll_queries": ["subject action close up", "subject in environment wide shot", "subject detail shot", "safe exact-subject fallback"],
      "beat_role": "one of: hook, context, setup, discovery, conflict, escalation, turning_point, climax, resolution, interesting_fact, conclusion_cta",
      "beat_importance": 1-10,
      "beat_can_merge": true,
      "beat_can_remove": true,
      "critical_asset_dependency": false}}
  ]
}}

Rules:
- Every segment must carry a beat_role following a coherent story order; the
  FIRST segment is always the hook and the LAST is always the conclusion_cta.
- Each segment must be at least {profile.narration_words_per_segment_min} spoken words and must read as complete sentences.
- The LAST segment must end with a soft CTA that includes the EXACT channel name "The Wild Mechanics" (these literal words). Pick one pattern: "Subscribe to The Wild Mechanics for more.", "Follow The Wild Mechanics for more like this.", "Stay curious - The Wild Mechanics posts daily."
- STRICT ALIGNMENT: each segment's "broll" and "broll_queries" MUST match the subject and action of that segment's narration.
- Each "broll_queries" list has exactly 4 concrete searches: close-up/action, environment/wide, detail, and safe exact-subject fallback. For segment 1 the first query is the strongest motion/action query.
- For space/ocean topics include real-object NASA-friendly terms.
- Vary shot type from the previous segment when possible.
- 10-15 lowercase hashtags, each prefixed with #, no spaces. Do NOT include #shorts.
- Narration is plain spoken English: no emojis, no hashtags, no stage directions.
- "broll" must be concrete nouns a stock service would actually have.
{critical_lock_rules}
"""


def build_writer_prompt_sambanova(
    niche: str,
    beats: list[dict[str, Any]],
    critical_visuals: str,
    critical_lock_rules: str,
    profile: FormatProfile,
) -> str:
    """Writer prompt optimised for SambaNova / open-weight models.

    These models respond better to explicit step-by-step instructions,
    front-loaded mandatory rules, and concrete examples.
    """
    beat_lines = []
    for index, beat in enumerate(beats, start=1):
        beat_lines.append(
            f"- Beat {index}: role={beat.get('role')}; purpose: {beat.get('purpose', '')} "
            f"(importance {beat.get('importance', 5)}/10, "
            f"can_merge={str(beat.get('can_merge', True)).lower()}, "
            f"can_remove={str(beat.get('can_remove', True)).lower()}, "
            f"critical_asset_dependency={str(beat.get('critical_asset_dependency', False)).lower()})"
        )
    beat_plan = "\n".join(beat_lines)
    return f"""You are scripting a high-retention vertical documentary about: "{niche}".

===== CRITICAL RULES (违反任何一条 = REJECTED) =====
1. LAST segment MUST have beat_role = "conclusion_cta".
2. LAST segment narration MUST end with the EXACT sentence: "Subscribe to The Wild Mechanics for more."
3. title MUST be a declarative statement (NOT a question). 5-8 words. No "?" character. No "#shorts". No academic label.
   GOOD: "The Hidden Cost of Coral Bleaching"  BAD: "What Happens When Coral Dies?"
4. Return ONLY the raw JSON object. No markdown fences, no preamble, no explanation.
5. Each segment narration >= {profile.narration_words_per_segment_min} words. Complete sentences only.
6. broll_queries MUST have exactly 4 items per segment.

===== STORY PLAN (follow this beat order exactly) =====
{beat_plan}

===== CONFIRMED CRITICAL VISUALS =====
{critical_visuals}

===== STYLE =====
- Cinematic nature documentary tone. Simple spoken words. Natural pauses.
- One connected short film: opening -> need -> tension -> action -> stakes -> reveal -> payoff.
- Keep science inside the action. Never invent claims.
- First segment opens on a concrete problem/danger/mystery. broll must promise motion.

===== JSON SCHEMA (return exactly this shape) =====
{{
  "title": "5-8 word declarative statement about the topic",
  "description": "2-3 sentence hook + value",
  "instagram_caption": "1-2 sentence caption",
  "music_mood": "mysterious | inspiring | dramatic | warm | curious | urgent",
  "hashtags": ["#tag1", "#tag2"],
  "segments": [
    {{
      "narration": "one or two spoken sentences",
      "broll": "3-5 word stock search term",
      "broll_queries": ["query1", "query2", "query3", "query4"],
      "beat_role": "hook | context | setup | discovery | conflict | escalation | turning_point | climax | resolution | interesting_fact | conclusion_cta",
      "beat_importance": 7,
      "beat_can_merge": true,
      "beat_can_remove": true,
      "critical_asset_dependency": false
    }}
  ]
}}

===== RULES =====
- One segment per beat (or merged pair). Never invent beats.
- STRICT broll ALIGNMENT: broll and broll_queries MUST match the narration subject and action.
- 10-15 lowercase hashtags. No #shorts.
- Narration: plain spoken English, no emojis, no stage directions.
- broll: concrete nouns a stock service would have.
{critical_lock_rules}
"""


def build_single_pass_prompt_sambanova(
    niche: str,
    critical_visuals: str,
    critical_lock_rules: str,
    profile: FormatProfile,
) -> str:
    """Single-pass writer prompt optimised for SambaNova / open-weight models.
    """
    return f"""You are scripting a high-retention vertical documentary about: "{niche}".

===== CRITICAL RULES (违反任何一条 = REJECTED) =====
1. LAST segment MUST have beat_role = "conclusion_cta".
2. LAST segment narration MUST end with the EXACT sentence: "Subscribe to The Wild Mechanics for more."
3. title MUST be a declarative statement (NOT a question). 5-8 words. No "?" character. No "#shorts". No academic label.
   GOOD: "The Hidden Cost of Coral Bleaching"  BAD: "What Happens When Coral Dies?"
4. Return ONLY the raw JSON object. No markdown fences, no preamble, no explanation.
5. Each segment narration >= {profile.narration_words_per_segment_min} words. Complete sentences only.
6. broll_queries MUST have exactly 4 items per segment.
7. FIRST segment beat_role = "hook". LAST segment beat_role = "conclusion_cta".

===== STORY LENGTH =====
Write 6-15 segments as the story needs. Platform ceiling is about {profile.max_duration_sec} seconds.
Do NOT compress or pad to hit any duration.

===== CONFIRMED CRITICAL VISUALS =====
{critical_visuals}

===== STYLE =====
- Cinematic nature documentary tone. Simple spoken words. Natural pauses.
- One connected short film: opening -> need -> tension -> action -> stakes -> reveal -> payoff.
- Keep science inside the action. Never invent claims.
- First segment opens on a concrete problem/danger/mystery. broll must promise motion.

===== JSON SCHEMA (return exactly this shape) =====
{{
  "title": "5-8 word declarative statement about the topic",
  "description": "2-3 sentence hook + value",
  "instagram_caption": "1-2 sentence caption",
  "music_mood": "mysterious | inspiring | dramatic | warm | curious | urgent",
  "hashtags": ["#tag1", "#tag2"],
  "segments": [
    {{
      "narration": "one or two spoken sentences",
      "broll": "3-5 word stock search term",
      "broll_queries": ["query1", "query2", "query3", "query4"],
      "beat_role": "hook | context | setup | discovery | conflict | escalation | turning_point | climax | resolution | interesting_fact | conclusion_cta",
      "beat_importance": 7,
      "beat_can_merge": true,
      "beat_can_remove": true,
      "critical_asset_dependency": false
    }}
  ]
}}

===== RULES =====
- One segment per beat. Never invent beats.
- STRICT broll ALIGNMENT: broll and broll_queries MUST match the narration subject and action.
- 10-15 lowercase hashtags. No #shorts.
- Narration: plain spoken English, no emojis, no stage directions.
- broll: concrete nouns a stock service would have.
{critical_lock_rules}
"""


def build_trim_prompt(
    niche: str,
    script: dict[str, Any],
    profile: FormatProfile,
    budget_seconds: float,
) -> str:
    segments = script.get("segments") or []
    seg_lines = []
    for idx, seg in enumerate(segments, start=1):
        seg_lines.append(
            f"- Segment {idx} [{seg.get('beat_role', 'discovery')}] "
            f"(importance {seg.get('beat_importance', 5)}/10, "
            f"can_merge={str(seg.get('beat_can_merge', True)).lower()}, "
            f"can_remove={str(seg.get('beat_can_remove', True)).lower()}, "
            f"critical={str(seg.get('critical_asset_dependency', False)).lower()}): "
            f"{seg.get('narration', '')}"
        )
    segment_list = "\n".join(seg_lines)
    return f"""The completed story about "{niche}" is too long for the platform ceiling
(about {profile.max_duration_sec} seconds total; narration must stay near
{budget_seconds:.1f}s). Shorten the story WITHOUT damaging it:

- MERGE low-priority, mergeable beats into their neighbors (summarize both into
  one tighter segment). Do NOT trim every scene equally.
- REMOVE only beats flagged can_remove=true that are not critical-asset
  dependent and not protected.
- ALWAYS preserve: hook (segment 1), climax, resolution, conclusion_cta, and
  every segment flagged critical_asset_dependency or can_remove=false.
- Keep the remaining narration natural and complete. Do not pad. Do not change
  the topic or the confirmed critical visuals.
- Return the SAME JSON shape (title/description/instagram_caption/music_mood/
  hashtags/segments), with FEWER segments than before. Every surviving segment
  MUST keep its full schema: "narration" (spoken text), "broll" (3-5 word
  concrete stock search term), "broll_queries" (exactly 4 concrete searches:
  close-up/action, environment/wide, detail, safe exact-subject fallback),
  "beat_role", "beat_importance", "beat_can_merge", "beat_can_remove",
  "critical_asset_dependency". A segment without "broll" or "broll_queries" is
  invalid.

Current segments:
{segment_list}
"""


def build_quality_prompt(niche: str, script: dict[str, Any]) -> str:
    segments = script.get("segments") or []
    seg_lines = []
    for idx, seg in enumerate(segments, start=1):
        seg_lines.append(f"{idx}. {seg.get('narration', '')}")
    narration = "\n".join(seg_lines)
    return f"""Score this completed documentary story about "{niche}" as a reviewer.

Title: {script.get('title', '')}
Narration:
{narration}

Rate each criterion 0-10 (10 = perfect), and answer the yes/no questions:
1. hook_strength: how strong is the opening (tension, curiosity, concrete image)?
2. narrative_coherence: does it feel like one connected short film, not a fact list?
3. logical_flow: does each segment follow from the last?
4. repetition: are there redundant segments (10 = no repetition at all)?
5. educational_value: does it teach something real without inventing claims?
6. emotional_progression: is there rising stakes/tension toward a payoff?
7. ending_quality: is the ending a satisfying payoff + clear soft CTA?
8. hook_present: yes/no -- does segment 1 open on tension (not a greeting/lesson)?
9. ending_present: yes/no -- does the last segment carry the exact channel CTA?
10. beets_coherent: yes/no -- do the beat roles follow a sane narrative order?

Return STRICT JSON only:
{{
  "hook_strength": 0-10,
  "narrative_coherence": 0-10,
  "logical_flow": 0-10,
  "repetition": 0-10,
  "educational_value": 0-10,
  "emotional_progression": 0-10,
  "ending_quality": 0-10,
  "hook_present": "yes" or "no",
  "ending_present": "yes" or "no",
  "beats_coherent": "yes" or "no",
  "summary": "one sentence"
}}
"""


def parse_quality_scores(raw: str) -> dict[str, Any]:
    import json

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    numeric_keys = (
        "hook_strength",
        "narrative_coherence",
        "logical_flow",
        "repetition",
        "educational_value",
        "emotional_progression",
        "ending_quality",
    )
    scores = {}
    for key in numeric_keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            scores[key] = max(0.0, min(10.0, float(value)))
    for key in ("hook_present", "ending_present", "beats_coherent"):
        if str(payload.get(key, "")).strip().lower() in {"yes", "true", "1"}:
            scores[key] = True
        elif str(payload.get(key, "")).strip().lower() in {"no", "false", "0"}:
            scores[key] = False
    scores["summary"] = str(payload.get("summary") or "")
    return scores


def aggregate_quality_score(scores: dict[str, Any]) -> float | None:
    """Aggregate the 0-10 criteria into one score (None when unscorable).

    Yes/no answers (booleans) are excluded -- ``bool`` subclasses ``int``
    in Python, so without this guard the binary criteria would each count
    as 1.0 and drag a strong story far below its real score.
    """
    values = [
        v
        for k, v in scores.items()
        if k.startswith("_") is False
        and isinstance(v, (int, float))
        and not isinstance(v, bool)
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def is_structurally_broken(scores: dict[str, Any]) -> bool:
    """Objective breakage that must hard-fail regardless of threshold."""
    if scores.get("hook_present") is False:
        return True
    if scores.get("ending_present") is False:
        return True
    return False


def story_analytics(
    script: dict[str, Any],
    profile: FormatProfile,
    *,
    estimated_seconds: float | None = None,
    actual_narration_seconds: float | None = None,
    final_video_seconds: float | None = None,
    quality_score: float | None = None,
    trim_applied: bool = False,
    narration_overflow: bool = False,
    renderer_tail_trim: bool = False,
) -> dict[str, Any]:
    """Build the durable analytics payload for ``story_report.json``."""
    segments = script.get("segments") or []
    roles = story_roles(script)
    words = segment_words(segments)
    scene_lengths: list[float] = []
    if actual_narration_seconds and words:
        per_word = actual_narration_seconds / max(1, sum(words))
        scene_lengths = [round(w * per_word, 2) for w in words]
    avg_scene = round(sum(scene_lengths) / len(scene_lengths), 2) if scene_lengths else None
    trim_pct = None
    if estimated_seconds and final_video_seconds:
        trim_pct = round(max(0.0, 1.0 - (final_video_seconds / estimated_seconds)) * 100.0, 1)
    return {
        "complexity": script.get("story_complexity") or (
            "complex" if len(segments) >= 10 else "simple"
        ),
        "beat_count": len(segments),
        "role_distribution": {role: roles.count(role) for role in sorted(set(roles))},
        "profile": profile.name,
        "platform_max_duration_sec": profile.max_duration_sec,
        "estimated_narration_sec": round(estimated_seconds, 2) if estimated_seconds else None,
        "actual_narration_sec": round(actual_narration_seconds, 2) if actual_narration_seconds else None,
        "final_video_sec": round(final_video_seconds, 2) if final_video_seconds else None,
        "average_scene_sec": avg_scene,
        "trim_percentage": trim_pct,
        "story_quality_score": quality_score,
        "semantic_trim_applied": bool(trim_applied),
        "narration_overflow": bool(narration_overflow),
        "renderer_tail_trim": bool(renderer_tail_trim),
    }


def planner_enabled() -> bool:
    """Whether the two-pass Story Planner -> Script Writer flow is active.

    Set ``AUTO_VIDEO_STORY_PLANNER=0`` to fall back to single-pass script
    writing (fewer LLM calls for free-tier rate limits).
    """
    return os.environ.get("AUTO_VIDEO_STORY_PLANNER", "1").strip() not in {"0", "false", "no"}


def quality_gate_soft() -> bool:
    """Story-quality gate is a soft warning by default.

    Set ``AUTO_VIDEO_STORY_QUALITY_STRICT=1`` to hard-fail any story that
    scores below ``AUTO_VIDEO_MIN_STORY_SCORE`` (default 8.0).
    """
    return os.environ.get("AUTO_VIDEO_STORY_QUALITY_STRICT", "0").strip() not in {"1", "true", "yes"}


def min_story_score() -> float:
    try:
        value = float(os.environ.get("AUTO_VIDEO_MIN_STORY_SCORE", "8.0").strip())
    except ValueError:
        value = 8.0
    return max(0.0, min(10.0, value))
