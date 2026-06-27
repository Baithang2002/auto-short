#!/usr/bin/env python3
"""
auto_short_biasfiles.py — Bias Files variant of auto_short.py.

Reuses 100% of auto_short.py's rendering pipeline (Gemini -> Speechify/Edge-TTS
-> Pexels/Pixabay/NASA b-roll -> ffmpeg assembly). The ONLY differences from
auto_short.py are:

  1. The Gemini script prompt (forensic / money-psychology voice, demands real
     bias names, real researchers, real dollar amounts).
  2. The default niche (a Bias Files topic).
  3. The evergreen hashtags (money / finance / psychology, not nature).
  4. The caption highlight words (money + bias terminology, not nature wonder).

Nothing in auto_short.py is modified. This file simply imports it, then
monkey-patches the four pieces above before invoking auto_short.main().

USAGE — identical to auto_short.py:
    python auto_short_biasfiles.py "how Jack Whittaker lost 315 million from a lottery ticket"
    python auto_short_biasfiles.py --duration 55
    python auto_short_biasfiles.py --landscape

The nature pipeline still runs untouched via:
    python auto_short.py "weird facts about the deep ocean"
"""
from __future__ import annotations

import json
import re

import auto_short


# ============================================================================
# OVERRIDE 1 — default niche
# ============================================================================
auto_short.DEFAULT_NICHE = "why losing 100 dollars feels worse than winning 200"
auto_short.CHANNEL_NAME = "The Bias Files"


# ============================================================================
# OVERRIDE 2 — caption highlight words
# Words that get the "highlight" treatment in burned-in captions. Swapping
# the nature-themed set for a money/bias-themed set.
# ============================================================================
auto_short.CAPTION_HIGHLIGHT_WORDS = {
    # Money words
    "loss", "lost", "broke", "bankrupt", "million", "billion", "trillion",
    "dollar", "dollars", "money", "rich", "poor", "fortune", "wealth",
    # Bias / psychology words
    "wrong", "bias", "brain", "study", "research", "proven", "actually",
    "real", "smart", "dumb", "secretly", "twice", "doubled", "kahneman",
    "nobel", "experiment", "scientists", "psychologists",
    # Emphasis
    "never", "always", "every", "single", "exactly",
}


# ============================================================================
# OVERRIDE 3 — Bias Files evergreen hashtags
# Used inside the patched generate_script below.
# ============================================================================
BIAS_FILES_EVERGREEN_TAGS = [
    "#shorts",
    "#moneypsychology",
    "#behavioralfinance",
    "#cognitivebias",
    "#money",
    "#finance",
    "#investing",
    "#wealth",
    "#kahneman",
    "#thebiasfiles",
    "#psychology",
    "#behavioraleconomics",
    "#personalfinance",
    "#moneytips",
    "#financialliteracy",
    "#stockmarket",
    "#budgeting",
    "#savingmoney",
    "#debtfree",
    "#financialfreedom",
]


# ============================================================================
# OVERRIDE 4 — Gemini script prompt
# A complete replacement for auto_short.generate_script. Reuses all of
# auto_short's helpers (narration_targets, generate_script_raw,
# parse_script_json, script_quality_notes). Only the prompt text and the
# evergreen-tags list change.
# ============================================================================
def generate_script_biasfiles(niche, n_segments, target_duration):
    min_total, max_total, min_segment, max_segment = auto_short.narration_targets(
        target_duration, n_segments
    )

    prompt = f"""
You are scripting a fast-paced vertical short for THE BIAS FILES — a faceless
YouTube channel about money psychology, cognitive biases, and real-person
money disasters.

TOPIC FOR THIS VIDEO: "{niche}"
Target finished length: about {target_duration} seconds.

CHANNEL VOICE (non-negotiable):
- Forensic, unsparing, quietly authoritative. Documentary tone.
- Treat the viewer as a smart adult. No hype, no "guys", no "wow".
- Every claim must feel grounded in real research or real events.
- If you reference a study, name the actual researcher (Kahneman, Tversky,
  Thaler, Ariely, Odean, Loewenstein, Santos, Poldrack, etc.) and an
  approximate year. Do not invent studies.
- If you reference a person, use a real verifiable person (Mike Tyson,
  Jack Whittaker, Isaac Newton, Adam Neumann, Elizabeth Holmes, Nicolas
  Cage, MC Hammer, 50 Cent, Johnny Depp, etc.).
- Specific dollar amounts beat vague claims. "$315 million" beats "a fortune".

Return STRICT JSON only (no markdown, no backticks, no preamble) in this shape:
{{
  "title": "punchy 5-8 word title (no #shorts here, appended automatically)",
  "description": "2-3 sentence YouTube/Facebook description, hook + payoff, no hashtags inside",
  "instagram_caption": "1-2 sentence Instagram caption ending with a soft CTA, no hashtags inside",
  "music_mood": "one of: mysterious, dramatic, urgent, curious",
  "hashtags": ["#tag1", "#tag2", "..."],
  "segments": [
    {{"narration": "one or two spoken sentences, {min_segment}-{max_segment} words, concrete and specific",
      "broll": "2-3 word stock-footage search term, very visual and literal",
      "broll_queries": ["specific visual search phrase", "backup visual search phrase", "wide establishing search phrase"]}}
  ]
}}

RULES:
- Exactly {n_segments} segments.
- LENGTH IS NON-NEGOTIABLE. Total narration MUST be {min_total}-{max_total} words.
- Each segment MUST be {min_segment}-{max_segment} words. Count before submitting.
- Segment 1 is the HOOK: open directly with a counterintuitive claim, a specific dollar amount, or a named person who lost a lot of money. Start immediately in the middle of the action. NEVER open with greetings, rhetorical questions, or throat-clearing clichés like "Have you ever wondered...", "Did you know...", "Imagine...", or "Meet...". Open immediately with a bizarre, counterintuitive, or striking statement.
- Segments 2-3 set up the bias or story. Name the bias OR the person involved.
- Middle segments deliver the EVIDENCE: the study finding, the brain-science
  detail, the specific dollar amount lost, the exact mistake.
- Penultimate segment delivers the TWIST: the counterintuitive insight, or the
  "compared to what" interrupt mechanism the wealthiest investors use.
- The LAST segment closes with a soft CTA woven into the narration. Use this
  exact style (vary the wording slightly):
    "Follow The Bias Files for the cognitive bias hidden in every money decision."
    "Subscribe to The Bias Files for one new money trap your brain falls for every week."
  Never "smash subscribe", "hit the bell", or any cringe creator-speak.
- "broll" must be a concrete noun phrase Pexels stock video would actually have.
  Bias Files visual vocabulary: cash close-up, stock chart falling, brain mri
  scan, vintage psychology lab, dice rolling, scales balance, broken piggy bank,
  crumpled dollar bills, slot machines, balance sheets, lottery tickets,
  empty wallets, abandoned gym, magnifying glass on numbers.
  AVOID abstract searches like "decision", "psychology", "mindset", "wisdom" —
  Pexels won't return what you want. Use literal visuals.
- STRICT ALIGNMENT: The "broll" search term and "broll_queries" list for a segment MUST directly match the subject, person, object, or action described in that segment's "narration". If you talk about a wallet or a specific person, the broll and queries must search specifically for that object or context. Never suggest unrelated scenery or generic placeholders.
- Each "broll_queries" list = exactly 3 concrete, simple Pexels-friendly searches: one specific close-up/action matching the narration, one environment/establishing shot, and one safe generic fallback matching the EXACT subject of the narration (e.g. "wallet" or "cash" or "stock chart", not a generic "finance").
- Choose "music_mood" to match the tone — usually "mysterious", "dramatic",
  or "urgent" for this channel. Avoid "warm" or "inspiring".
- 10-15 lowercase hashtags. Do NOT include #shorts (appended automatically).
- BANNED WORDS in narration: "amazing", "incredible", "wow", "guys",
  "literally", "smash", "crazy", "wild", "insane", "mind-blown".
"""

    raw = auto_short.generate_script_raw(prompt)
    data = auto_short.parse_script_json(raw)
    fatal, soft = auto_short.script_quality_notes(data, n_segments, target_duration)

    if fatal or soft:
        all_notes = fatal + soft
        print("    [Script QA] First draft needs adjustment; requesting rewrite...")
        repair_prompt = f"""{prompt}

The previous JSON failed these checks:
{json.dumps(all_notes, indent=2)}

Rewrite from scratch satisfying every word-count and tone rule.
Previous JSON:
{json.dumps(data, ensure_ascii=False)}
"""
        data = auto_short.parse_script_json(auto_short.generate_script_raw(repair_prompt))
        fatal, soft = auto_short.script_quality_notes(data, n_segments, target_duration)
        if fatal:
            raise RuntimeError(
                "Generated script is still too short or malformed: "
                + "; ".join(fatal[:6])
            )
        if soft:
            print(
                f"    [Script QA] Accepting with minor overshoots: {'; '.join(soft[:3])}"
            )

    segs = data["segments"][:n_segments]
    data["segments"] = segs

    # Normalize SEO metadata
    data.setdefault("description", data.get("title", niche))
    data.setdefault("instagram_caption", data.get("title", niche))
    if data.get("music_mood") not in {"mysterious", "dramatic", "urgent", "curious"}:
        data["music_mood"] = "dramatic"

    # Normalize broll_queries
    for seg in data.get("segments", []):
        queries = seg.get("broll_queries") or []
        if isinstance(queries, str):
            queries = [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()]
        broll = str(seg.get("broll", "")).strip()
        if broll and broll not in queries:
            queries.insert(0, broll)
        while len(queries) < 3:
            queries.append(broll or "money cash")
        seg["broll_queries"] = queries[:3]

    # Hashtags: Bias Files evergreen list first, then Gemini's topic-specific
    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[\s,]+", tags) if t.startswith("#")]
    tags = BIAS_FILES_EVERGREEN_TAGS + [
        t for t in tags
        if t.lower() not in {x.lower() for x in BIAS_FILES_EVERGREEN_TAGS}
    ]
    seen, deduped = set(), []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            deduped.append(tl)
    data["hashtags"] = deduped[:15]
    data["niche"] = niche

    # Cache for --reuse-script
    try:
        cache_path = auto_short.OUT_DIR / "last_script.json"
        cache_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass

    print(f"[+] Title: {data.get('title','(untitled)')}")
    for i, s in enumerate(segs):
        print(f"    {i+1}. {s['narration']}   [b-roll: {s['broll']}]")
    return data


# Install the override
auto_short.generate_script = generate_script_biasfiles


if __name__ == "__main__":
    auto_short.main()
