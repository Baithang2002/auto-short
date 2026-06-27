# Changes - 2026-06-27

## `auto_short.py`

### 1. Query enrichment to prevent wrong Pexels matches (new function)
**Lines 1108-1130** — Added `_qualify_query(q, fallback)` that appends domain qualifiers
(e.g. `"underwater ocean animal"`) to search queries when the topic is marine,
preventing literal-but-wrong matches like "vampire squid" returning a human
vampire cosplay.

Also enriches for space and dinosaur topics.

### 2. `fetch_pexels_video` — removed generic fallback queries
**Lines 1157-1178** — Removed `+ ([fallback] if fallback else []) + ["documentary footage"]`
from the query loop.  Generic terms like "documentary footage" always match
something irrelevant on Pexels and block Pixabay / Gemini Image / user prompt
from ever being reached.  Each query is now enriched via `_qualify_query()`
before being sent.

### 3. `fetch_pixabay_video` — added `fallback` parameter + enrichment
**Line 1207** — Added `fallback=""` param.  Queries are now enriched via
`_qualify_query(q, fallback)` before searching, same as Pexels.

### 4. `fetch_broll` — removed last-ditch generic Pexels search
**Lines 1378-1380** — Removed step 5 that called
`fetch_pexels_video([fallback, "documentary footage", "nature"], ...)`.  This
always matched garbage on Pexels and blocked the user-interactive fallback.

### 5. Interactive b-roll review (`--review-broll` flag)
**Lines 1002-1053** — Added `interactive_broll_review()` function.  When
`--review-broll` is passed:

  - If `output/broll_overrides.json` exists, loads overrides from it.
  - Otherwise prints all segment queries and writes a template JSON file,
    then exits.  User edits the file and re-runs.

  Override options per segment:
  ```json
  {"queries": ["term1", "term2"]}   -- custom search terms
  {"clip_path": "C:/path/to.mp4"}   -- use a local file
  {"skip": true}                    -- use Gemini image generation
  ```

**Lines 2200-2215** — Main loop applies overrides (custom clip, skip, or
edited queries) when present.

### 6. Interactive fallback when all sources fail (`fetch_broll`)
**Lines 1380-1397** — When Pexels, Pixabay, NASA, and Gemini Image all fail,
the script now asks the user for a local clip path before calling `die()`.

### 7. Trim BEFORE captions (fixes premature video end)
**Lines 2281-2293** — `combined.mp4` is now trimmed to `SHORTS_MAX_DURATION`
(60s) BEFORE measuring duration and building ASS captions.  Previously the
trim happened after captions were burned in, cutting off the last ~3-6s of
captions.

### 8. Tighter word-count targets
**Lines 180-188** — `narration_targets()` multiplier reduced from 2.25 to 1.6
so Gemini generates scripts that actually fit the target duration without
needing aggressive trimming.

### 9. Higher tempo cap for voice retiming
**Line 741** — Speedup cap raised from 1.30x to 1.50x in
`normalize_voice_timing()`, so overshoot can actually be corrected.

### 10. `SHORTS_MAX_DURATION` raised to 60
**Line 65** — Changed from 58 to 60 (the real Content ID threshold).

### 11. Gemini image model updated
**Line 967** — `gemini-2.0-flash-exp` changed to `gemini-2.5-flash` (old model
returned 404).

### 12. `--review-broll` argument added
**Lines 2093-2094** — New CLI flag on `auto_short.py`.

## `_pipeline_base.py`

### 13. `--review-broll` passthrough
**Lines 89-90** — Added argument to pipeline parser.
**Line 107** — Passed through to `auto_short.py` in `build_stage1_extra()`.
**Line 26** — Added `stdin=sys.stdin` to `subprocess.run()` so interactive
prompts can work (though the review now uses a file-based flow instead).
