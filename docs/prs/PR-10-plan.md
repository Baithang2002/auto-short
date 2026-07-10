# PR #10 Plan — Declarative Render & Duration Profiles

**Status:** approved 2026-07-10. Implementation may begin.
**Ship target:** v0.7.0
**Scope discipline:** additive refactor only. Preserve Shorts behavior byte-for-byte.

---

## 1. Scope Statement

**What this PR does.** Introduce an additive `FormatProfile` abstraction that owns the duration- and scene-shaped behavior currently baked as module-level constants in `auto_short.py`. Register a single `shorts_vertical` profile whose numeric values exactly match today's constants.

**What this PR does NOT do** (locked non-goals):

- No long-form profile registered. `shorts_vertical` is the only profile.
- No CLI flag or environment variable for format selection. Format is fixed to `shorts_vertical`.
- No modification to `src/autovideo/render/profiles.py` (environment `RenderProfile`).
- No modification to `src/autovideo/config/channels.py` (environment `RenderProfile` sibling).
- No modification to `src/autovideo/config/defaults.py` (`RenderDefaults` stays).
- No modification to renderer, media selection, timeline, providers, storage, upload, queue, or metadata.
- No Visual QA. No computer vision. No pipeline redesign.

The environment `RenderProfile` (dev/prod/test) and the format `FormatProfile` (shorts_vertical) become two independent concepts that compose at runtime.

---

## 2. `FormatProfile` Type

New file: `src/autovideo/format/profiles.py`

```python
"""Format-shaped configuration for content-length and scene behavior."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

FormatProfileName = Literal["shorts_vertical"]  # additional formats added in later PRs


@dataclass(frozen=True)
class FormatProfile:
    """Format-shaped configuration owning duration, scene, and narration constants.

    A FormatProfile answers: 'What is the target shape of the finished video?'
    Environment concerns (dev/prod/test, mock providers, codec) remain on
    autovideo.render.profiles.RenderProfile and are composed at runtime.
    """

    name: FormatProfileName
    target_duration_sec: float
    min_duration_sec: float
    max_duration_sec: float
    scene_target_duration_sec: float
    transition_duration_sec: float
    preferred_narration_tempo: float
    narration_max_retime_tempo: float
    narration_min_retime_tempo: float
    narration_words_per_sec_min: float
    narration_words_per_sec_max: float
    narration_words_per_segment_min: int
```

**Registered `shorts_vertical` values** — verbatim from current constants:

| Field | Value | Sourced from (current location) |
|---|---|---|
| `name` | `"shorts_vertical"` | new |
| `target_duration_sec` | `60.0` | `RenderDefaults.target_duration_sec` |
| `min_duration_sec` | `50.0` | `RenderDefaults.shorts_min_duration_sec` |
| `max_duration_sec` | `58.0` | `RenderDefaults.shorts_max_duration_sec` |
| `scene_target_duration_sec` | `5.0` | `auto_short.py:105 SHORTS_SCENE_TARGET_DURATION` |
| `transition_duration_sec` | `0.22` | `auto_short.py:107 SHORTS_TRANSITION_DURATION` |
| `preferred_narration_tempo` | `1.06` | `auto_short.py:106 SHORTS_PREFERRED_NARRATION_TEMPO` |
| `narration_max_retime_tempo` | `1.30` | inline in `normalize_voice_timing:852` |
| `narration_min_retime_tempo` | `0.90` | inline in `normalize_voice_timing:845` |
| `narration_words_per_sec_min` | `2.25` | inline in `narration_targets:243` |
| `narration_words_per_sec_max` | `2.55` | inline in `narration_targets:244` |
| `narration_words_per_segment_min` | `10` | inline in `narration_targets:243` |

Because every value on this profile matches the currently-live constant, byte-for-byte Shorts output is guaranteed by-construction.

**Ownership decision:** `transition_duration_sec` is canonical on `FormatProfile`. Environment `RenderProfile` keeps its `transition_duration_sec` field for interface compatibility; the value at instantiation comes from the FormatProfile.

---

## 3. Registry & Lookup

New file: `src/autovideo/format/registry.py`

```python
"""Format-profile registry."""

from __future__ import annotations
from .profiles import FormatProfile, FormatProfileName

_SHORTS_VERTICAL = FormatProfile(
    name="shorts_vertical",
    target_duration_sec=60.0,
    min_duration_sec=50.0,
    max_duration_sec=58.0,
    scene_target_duration_sec=5.0,
    transition_duration_sec=0.22,
    preferred_narration_tempo=1.06,
    narration_max_retime_tempo=1.30,
    narration_min_retime_tempo=0.90,
    narration_words_per_sec_min=2.25,
    narration_words_per_sec_max=2.55,
    narration_words_per_segment_min=10,
)

_REGISTRY: dict[str, FormatProfile] = {
    "shorts_vertical": _SHORTS_VERTICAL,
}


def get_format_profile(name: str) -> FormatProfile:
    """Return a registered format profile by name. Raises KeyError if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(f"Unknown format profile: {name!r}. Available: {available}") from exc


def get_default_format_profile() -> FormatProfile:
    """Return the default format profile (shorts_vertical)."""
    return _SHORTS_VERTICAL
```

No format is selectable from env vars or CLI in this PR. `get_default_format_profile()` is the only entry point used by consumers.

New file: `src/autovideo/format/__init__.py`

```python
"""Format-shaped configuration for the video production pipeline."""

from .profiles import FormatProfile, FormatProfileName
from .registry import (
    get_default_format_profile,
    get_format_profile,
)

__all__ = [
    "FormatProfile",
    "FormatProfileName",
    "get_default_format_profile",
    "get_format_profile",
]
```

---

## 4. File-by-File Change Plan

### 4.1 New files (3)

| Path | Purpose | Est. LOC |
|---|---|---|
| `src/autovideo/format/__init__.py` | Public API exports | 15 |
| `src/autovideo/format/profiles.py` | `FormatProfile` type | 30 |
| `src/autovideo/format/registry.py` | `shorts_vertical` registered + lookup | 45 |

### 4.2 Modified files (1)

`auto_short.py` — six surgical edits.

**Edit 1.** Import at top of file:

```python
from autovideo.format import FormatProfile, get_default_format_profile
```

**Edit 2.** Replace module constants at lines 105-109 with profile-derived bindings:

```python
_FORMAT_PROFILE: FormatProfile = get_default_format_profile()

SHORTS_SCENE_TARGET_DURATION = _FORMAT_PROFILE.scene_target_duration_sec
SHORTS_PREFERRED_NARRATION_TEMPO = _FORMAT_PROFILE.preferred_narration_tempo
SHORTS_TRANSITION_DURATION = _FORMAT_PROFILE.transition_duration_sec
SHORTS_MIN_DURATION = _FORMAT_PROFILE.min_duration_sec
SHORTS_MAX_DURATION = _FORMAT_PROFILE.max_duration_sec
TARGET_DURATION = _FORMAT_PROFILE.target_duration_sec  # was DEFAULTS.render.target_duration_sec
```

Every existing consumer of these module constants continues to work — same names, same numeric values, same types. No downstream call site changes.

**Edit 3.** `narration_targets()` at lines 237-244: read word-rate bounds from profile:

```python
def narration_targets(target_duration, n_segments, profile: FormatProfile = _FORMAT_PROFILE):
    min_total = max(n_segments * profile.narration_words_per_segment_min,
                    round(target_duration * profile.narration_words_per_sec_min))
    max_total = max(min_total + 14,
                    round(target_duration * profile.narration_words_per_sec_max))
    # ... rest unchanged
```

Backward compatible — the `profile` parameter defaults to the module-level `_FORMAT_PROFILE`, so existing call sites (`script_quality_notes`, `generate_script`) work without changes.

**Edit 4.** `normalize_voice_timing()` at lines 833-878: read tempo bounds from profile:

```python
def normalize_voice_timing(voice_items, target_duration, profile: FormatProfile = _FORMAT_PROFILE):
    if target_duration < profile.min_duration_sec:
        return voice_items

    total = sum(item["duration"] for item in voice_items)
    desired = min(max(target_duration, profile.min_duration_sec), profile.max_duration_sec)

    if total < profile.min_duration_sec:
        raw_tempo = total / desired
        tempo = max(profile.narration_min_retime_tempo, raw_tempo)
        action = "slowing"
    else:
        raw_tempo = total / desired
        tempo = min(profile.narration_max_retime_tempo, raw_tempo)
        action = "speeding up"

    if profile.min_duration_sec <= total <= profile.max_duration_sec:
        transition_allowance = max(0, len(voice_items) - 1) * profile.transition_duration_sec
        minimum_voice_total = profile.min_duration_sec + transition_allowance + 0.5
        tempo = min(profile.preferred_narration_tempo, total / minimum_voice_total)
        # ... rest unchanged
```

Same numeric behavior. All bounds now sourced from the profile.

**Edit 5.** `make_all_voices()` at line 881: pass profile through:

```python
def make_all_voices(segments, target_duration, profile: FormatProfile = _FORMAT_PROFILE):
    # ... unchanged body ...
    return normalize_voice_timing(voice_items, target_duration, profile)
```

**Edit 6.** `main()` at line 3386: use profile for `n_segments` calculation:

```python
n_segments = max(3, round(duration / _FORMAT_PROFILE.scene_target_duration_sec))
```

Since `_FORMAT_PROFILE.scene_target_duration_sec == SHORTS_SCENE_TARGET_DURATION` numerically, output is identical.

### 4.3 Files NOT touched (locked)

- `src/autovideo/render/profiles.py` — environment `RenderProfile` stays.
- `src/autovideo/config/channels.py` — environment `RenderProfile` sibling stays.
- `src/autovideo/config/defaults.py` — `RenderDefaults` stays as-is.
- `src/autovideo/render/ffmpeg_renderer.py` — renderer already reads `shorts_max_duration_sec` from the environment `RenderProfile`. That value continues to flow from `auto_short.py:3600` where `SHORTS_MAX_DURATION` is passed in — and `SHORTS_MAX_DURATION` is now bound from the FormatProfile. The renderer sees the same number it did before.
- All other layers (media, timeline, providers, music, storage, upload, queue, metadata) untouched.

---

## 5. Backward-Compatibility Guarantee

Two independent guarantees make Shorts byte-identical:

1. **Numeric equivalence.** Every field on the `shorts_vertical` FormatProfile matches the currently-live module constant. `SHORTS_MAX_DURATION` is still `58`, `SHORTS_SCENE_TARGET_DURATION` is still `5.0`, and so on.
2. **Aliasing preservation.** The module constants at `auto_short.py:105-109` continue to exist and continue to be exported. External code that imports `SHORTS_MAX_DURATION` from `auto_short` still gets the same value.

If a Shorts output differs from pre-PR output byte-for-byte, that is a regression bug and blocks merge.

---

## 6. Test Plan

New file: `tests/unit/test_format_profiles.py`

Assertions:

1. `shorts_vertical` field values match current constants — one assertion per field.
2. `get_format_profile("shorts_vertical")` returns a `FormatProfile`.
3. `get_format_profile("unknown_format")` raises `KeyError` with the available-names message.
4. `get_default_format_profile()` returns the `shorts_vertical` profile.
5. `FormatProfile` is frozen — mutation attempt raises `FrozenInstanceError`.

Existing tests: no modifications required. Since profile values equal the former constants, any test that indirectly exercised the constants passes without change.

No integration test is added in this PR. Full-pipeline regression coverage is a separate concern.

---

## 7. Verification Checklist for Merge Review

- [ ] `python -c "from autovideo.format import get_default_format_profile; p = get_default_format_profile(); print(p.max_duration_sec, p.scene_target_duration_sec, p.min_duration_sec)"` prints `58.0 5.0 50.0`.
- [ ] `python -c "import auto_short; print(auto_short.SHORTS_MAX_DURATION, auto_short.SHORTS_SCENE_TARGET_DURATION, auto_short.SHORTS_MIN_DURATION)"` prints `58.0 5.0 50.0`.
- [ ] `python -c "from auto_short import narration_targets; print(narration_targets(60, 12))"` prints identical output to the pre-PR value.
- [ ] `pytest tests/unit/test_format_profiles.py` passes.
- [ ] `pytest tests/unit/` full suite passes.
- [ ] Full Shorts pipeline dry-run (`python auto_short.py "Ocean currents" --no-interactive`) produces identical output to the pre-PR run for the same topic.

---

## 8. Explicit Non-Goals

This PR does not:

- Introduce additional `FormatProfile` values (long-form, educational, podcast). Those wait for their own PRs.
- Add a `--format` CLI flag or `AUTO_VIDEO_FORMAT` env var.
- Modify the environment `RenderProfile` on either `src/autovideo/render/profiles.py` or `src/autovideo/config/channels.py`.
- Change the renderer's behavior. `FfmpegTimelineRenderer` and `LegacyRendererAdapter` are untouched.
- Change media-selection scoring, source planning, or provider routing.
- Modify `bias_long.py` or any long-form scaffolding.
- Change the CI workflow, daily pipeline entry point, or state files.

---

## 9. CHANGELOG Draft (for when PR #10 lands)

Goes into `[Unreleased]` on the implementation commit; moves to `[0.7.0]` on release.

```markdown
## [0.7.0] — Declarative Render & Duration Profiles

**Milestone: PR #10 — Declarative Render & Duration Profiles.** Introduces the `FormatProfile` abstraction under `src/autovideo/format/`, owning duration- and scene-shaped configuration that was previously module-level constants in `auto_short.py`. Environment `RenderProfile` (dev/prod/test) and format `FormatProfile` (shorts_vertical) are independent concepts composed at runtime.

### Added

- `FormatProfile` type under `src/autovideo/format/profiles.py` with duration bounds, scene-target duration, transition duration, and narration tempo/word-rate parameters.
- `shorts_vertical` format profile registered in `src/autovideo/format/registry.py`, values matching the historical constants exactly.
- `get_format_profile()` and `get_default_format_profile()` lookup functions.

### Changed

- `auto_short.py` module constants (`SHORTS_MAX_DURATION`, `SHORTS_MIN_DURATION`, `SHORTS_SCENE_TARGET_DURATION`, `SHORTS_TRANSITION_DURATION`, `SHORTS_PREFERRED_NARRATION_TEMPO`) are now derived from the `shorts_vertical` FormatProfile rather than defined inline. Numeric values unchanged.
- `narration_targets()`, `normalize_voice_timing()`, and `make_all_voices()` accept an optional `profile: FormatProfile` parameter defaulting to the module-level profile.

### Compatibility

- Shorts output is byte-identical. Every numeric value on the `shorts_vertical` profile matches its former inline constant.
- Module-level constant names remain exported for external consumers.
- No CLI or environment-variable changes. No new format is selectable.
```

---

## 10. Follow-up PRs unblocked by this one

This plan intentionally leaves work for later. When PR #10 ships:

- A subsequent PR can register a `long_form_documentary` FormatProfile in `registry.py` without touching consumers.
- A subsequent PR can add a `--format` CLI flag that calls `get_format_profile(args.format)` instead of `get_default_format_profile()`.
- A subsequent PR can wire the renderer to receive the FormatProfile alongside the environment RenderProfile, decoupling `shorts_max_duration_sec` on the render side.

None of these follow-ups are part of PR #10.

---

*This plan is the durable reference for PR #10. Any deviation from the file-by-file change list requires a plan update in the same PR.*
