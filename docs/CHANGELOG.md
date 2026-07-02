# Changelog

All notable changes to this project are recorded here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Sections in each entry follow the Keep a Changelog vocabulary: **Added** (new features), **Changed** (updates to existing behavior), **Deprecated** (soon-to-be-removed features), **Removed** (removed features), **Fixed** (bug fixes), **Security** (vulnerability fixes).

Entries below the `[Unreleased]` section describe released versions. When work merges to the main branch under a version bump, it is moved from `[Unreleased]` to a versioned section with the release date. Each release entry is chronologically-ordered and immutable — corrections happen in later releases with explicit notes.

---

## [Unreleased]

Work in progress. Once released, entries here move to a versioned section.

### Documentation

- `docs/VISION.md` — mission, roadmap eras, design philosophy, project principles.
- `docs/ENGINEERING_GUIDE.md` — coding standards, architecture principles, provider/storage/configuration rules, testing strategy, performance philosophy, AI contributor guidelines, definition of done.
- `docs/ARCHITECTURE.md` — package structure, domain models, provider layer, storage layer, timeline architecture, rendering architecture, migration strategy, extension points, and 12 Architectural Decision Records.
- `docs/CHANGELOG.md` — this file.
- `docs/ROADMAP.md` — pending.

---

## [0.1.0] — Initial Production Baseline (2026-06-28)

**Initial Production Baseline.** This release documents the state of the platform at the moment the layered-architecture migration begins. Every capability listed below is functional in the current codebase. Migration from the legacy monolithic modules (`auto_short.py`, `pipeline.py`, `uploader.py`, and siblings) to the target `platform/` package structure is the subject of subsequent releases.

Three architectural milestones — *Foundation Layer*, *Storage Abstraction*, and *Provider & Configuration Layer* — are recorded as **completed internal architecture milestones**. They were implemented incrementally in the working branch during development rather than as discrete GitHub pull requests, and predate the formal PR discipline established with the introduction of `docs/ENGINEERING_GUIDE.md`. Their scope is captured under the categories below and reflected in the Implementation Status table of `docs/ARCHITECTURE.md`.

### Added — content generation

- **Script generation** with a multi-provider fallback chain: Gemini (multiple model IDs), SambaNova (multiple model IDs), Groq (multiple model IDs), OpenAI (multiple model IDs). The chain is tried in order and skips providers whose credentials are absent.
- **Per-run quota circuit breaker** for Gemini. On the first `429 RESOURCE_EXHAUSTED` response, Gemini is disabled for the remainder of the run and later stages that would call Gemini (e.g., semantic clip matching) skip it silently.
- **Script quality assurance** distinguishing fatal issues (too-short segments, missing b-roll, incorrect segment count) from soft issues (slight word-count overshoots). Soft issues are accepted with a warning; fatal issues trigger one repair attempt. If the repair returns worse output, the first draft is used instead.
- **Structured metadata** produced alongside the script: title, description, YouTube-specific description with `#shorts` handling, per-platform captions, hashtags (evergreen + topic-specific merged and deduplicated).

### Added — voice synthesis

- **Voice synthesis** with an Edge-TTS primary path and a Speechify fallback. A per-run circuit breaker disables Speechify after the first authentication failure.
- **Voice retry wrapper** for Edge-TTS `NoAudioReceived` errors with exponential backoff.
- **Voice timing normalization** that adjusts playback speed post-generation to fit target duration bounds. Speed-up cap of 1.30x preserves natural narration cadence.

### Added — media selection

- **Stock media** from Pexels (primary), Pixabay (secondary), and NASA (space topics only, routed by keyword detection).
- **Cross-source deduplication** across all providers and across successive video renders, persisted in `used_videos.json`.
- **Query enrichment** that appends domain qualifiers (`underwater ocean animal`, `outer space`, `wildlife close up`) to ambiguous searches, preventing category-mismatched matches.
- **Segment-aware last-resort fallbacks** that inspect the query's semantic domain (marine / space / land) and route to appropriate broad Pexels searches when specific queries fail.
- **Local media** support via an `input_clips/` folder. Files are matched to segments by strict keyword overlap between filename and narration.
- **Interactive review flag** (`--review-broll`) that writes a JSON template of all segment queries, exits, and re-reads operator edits on the next invocation.
- **Interactive fallback** that prompts for a local clip path when all automated sources fail. Suppressed by `--no-interactive` for unattended runs.
- **Gemini image generation** as a final fallback for segments that no video source can satisfy.

### Added — music

- **Music sourcing** with a chain: local `music/` folder (matched by mood keyword in filename) → Jamendo (with broader-tag fallback for narrow mood misses) → synthesized ambient bed.
- **Synthesized fallback** uses filtered noise sources routed through a mood-appropriate filter graph. Deterministic output.
- **Sidechain-compressor mixing** that ducks music under narration.

### Added — rendering

- **Vertical 1080×1920 render pipeline** targeting YouTube Shorts, Instagram Reels, and Facebook Reels.
- **Word-chunked captions** with per-word highlighting for emphasized terms. Rendered as `.ass` subtitles with the `subtitles` ffmpeg filter.
- **Ken Burns treatment** for image sources.
- **Dual output**: the primary `final.mp4` includes music for IG/FB, and a `final_yt_safe.mp4` variant is produced without background music for YouTube Content-ID safety.
- **Hard 58-second duration cap** with automatic tail-trim. Uploader refuses to publish `>= 60 s` videos to YouTube as a second defense layer.
- **Caption timing sync** that clamps caption end times to the actual rendered video duration, preventing captions from extending past the final frame.
- **Loudness mastering** to −16 LUFS with peak limiting at −1 dBFS.

### Added — publishing

- **YouTube Data API v3 uploader** using OAuth refresh tokens. This path runs headless, requires no browser session, and is the default when `YT_REFRESH_TOKEN` is set.
- **Browser-based fallback** via Playwright for platforms without an API path — Instagram Reels, Facebook Reels, and YouTube (as a fallback when Data API credentials are absent).
- **Per-step screenshot-on-failure** in the browser flow, saved under `output/screenshots/`.
- **Retry with backoff** wrapping every browser interaction step.
- **Per-run JSON history** at `output/upload_log.json` recording status, URL, duration, error, and screenshot path per platform per attempt.
- **YT Studio Shorts audio-swap automation** (Playwright) as an optional post-upload step for adding YouTube Audio Library music to a silent-uploaded video.
- **Configurable `YT_MUSIC` toggle** that selects whether the YouTube path uploads the with-music main render or the silent Content-ID-safe variant.
- **Content-ID uploader guard** that probes video duration and refuses to send `>= 60 s` videos to YouTube.
- **Cross-platform inter-upload jitter** between 20–60 seconds to avoid bot-detection footprints.

### Added — automation

- **GitHub Actions workflow** (`.github/workflows/daily.yml`) running Python 3.11 with ffmpeg, invoking the daily pipeline on schedule.
- **Off-peak cron scheduling** at `:17` UTC with a `:47` backup cron for load-shedding resilience on GitHub's free tier.
- **Same-day deduplication guard** in `pipeline_daily.py` that skips a second run when a successful entry for today already exists in `daily_runs.log`.
- **Round-robin topic rotation** from `topics.txt`, with state persisted to `state/daily_state.json` and committed back to the repository after each run.
- **State restoration step** at the start of each workflow run, copying `state/*` into `output/` to seed the rotation index.
- **Run artifact upload** preserving `final.mp4`, `final_yt_safe.mp4`, `upload_metadata.json`, `daily_runs.log`, and any failure screenshots for seven days.
- **Manual workflow dispatch** with optional topic override, supporting one-off runs from the Actions tab.

### Added — review tooling

- **Local Flask review dashboard** (`review_dashboard.py`) that lists pending videos, plays them inline, and offers approve/reject controls.
- **Upload worker** (`upload_worker.py`) that reads approved videos from the local queue and dispatches them via the uploader.
- **Review queue folder structure** under `videos/pending/`, `videos/approved/`, `videos/rejected/`, `videos/uploaded/`, with metadata sidecars per video.

### Added — configuration and secrets

- **Environment-based configuration** via `.env` for local runs, with corresponding GitHub Secrets for CI runs. Recognized variables include: `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`, `SAMBANOVA_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `JAMENDO_CLIENT_ID`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `YT_MUSIC`, `SPEECHIFY_API_KEY`.
- **`.gitignore` covering** credentials (`.env`, `oauth_client.json`, `.youtube_credentials.json`), runtime state (`output/`, `videos/`), operator libraries (`input_clips/`, `music/`), and browser session data (`browser_session/`, `browser_session_test/`).

### Added — long-form variant

- **Parallel long-form pipeline** (`bias_long.py`, `pipeline_biasfiles*.py`, `topics_biasfiles*.txt`) supporting 1920×1080 horizontal videos. Shares no code path with the vertical pipeline in the current state; will be folded into a format-profile mechanism as part of a future migration.

### Completed internal architecture milestones

The following architecture milestones were completed incrementally in the working branch during development. They are recorded here for continuity — they are not GitHub pull requests but the scope they represent is functionally in place. Later releases will migrate the physical layout to `platform/` while preserving the boundaries these milestones established.

- **Foundation** — cross-cutting utilities in production use: path helpers, ID generation, retry decorators, structured logging, time helpers. Currently distributed across the legacy modules; subsequent releases will consolidate them under the target modular architecture.
- **Storage abstraction** — artifact persistence and sidecar metadata in production use. Filesystem-backed writes with JSON metadata companions. Subsequent releases will introduce a formal store interface and route existing calls through it under the target modular architecture without behavior change.
- **Providers and configuration** — provider fallback chains and `.env`-driven configuration in production use. Script, voice, media, music, image, and upload providers all follow the same fallback pattern. Subsequent releases will formalize the fallback mechanism into a provider registry with typed result types under the target modular architecture.

See `docs/ARCHITECTURE.md § Architectural Decision Records` (ADRs 001, 002, 004, 005, 011) for the rationale behind these boundaries.

### Known limitations at 0.1.0

- No unified content-addressed store; artifacts are named by convention rather than by hash. See ADR-010.
- Provider fallback is inline branching, not registry-based. See ADR-002.
- Timeline representation is implicit; the renderer walks segments directly. See ADR-003.
- Configuration schema is not centrally validated at load time. Malformed keys surface later as `KeyError` or attribute lookup errors during pipeline execution.
- Provider-specific error handling still uses exceptions rather than typed result types. See ADR-009.
- Instagram and Facebook publishing require browser sessions on the operator's machine and cannot run headless on cloud CI.

---

## Architecture Evolution

The project evolves through documented architecture milestones. Each milestone represents a coherent boundary — an interface introduced, a layer consolidated, a legacy path retired — rather than a single feature.

Milestone identifiers of the form **PR #1**, **PR #2**, **PR #3**, and so on refer to **internal architecture milestones**, not GitHub pull requests. They were adopted before the formal PR discipline was in place and continue as the durable identifier for architecture-level scope. GitHub PRs are numbered separately and can be referenced when relevant.

Future documentation may reference both **semantic versions** and **architecture milestone numbers** for continuity. When both are cited (for example, *"introduced in PR #4 and released in v0.2.0"*), the milestone identifier names the scope and the version names the ship date.

---

## Guiding Principles for Future Entries

Contributors — human or AI — follow these rules when adding a new changelog entry.

**Every user-visible change gets an entry.** Bug fixes, new features, behavior changes, deprecations, removals, and security fixes are all recorded. Purely internal refactors that do not alter behavior may be omitted, but only if their rationale is captured in the corresponding PR description.

**Entries describe the change, not the diff.** "Fixed a bug in the uploader" is not useful. "Fixed YouTube upload failing when the video description contained backslashes" is useful.

**Group by Keep a Changelog category** (Added / Changed / Deprecated / Removed / Fixed / Security). Within each category, entries are one-line bullets.

**Breaking changes are flagged**. A breaking change carries a leading `⚠️ BREAKING:` marker and is accompanied by a migration note in the release entry.

**Reference ADRs by number** when a change implements or supersedes an architectural decision. Example: *"Added typed `Script` model in `platform/domain/models/`. Implements ADR-007."*

**Reference the release date, not the merge date.** The release date is the day the version bump merges to `main`. Individual entry dates are not tracked; the release entry's date is the collective date for all entries under it.

**Do not delete or edit past entries.** Corrections happen in a later release with an explicit "note" line. This preserves an accurate history.

---

*This file is maintained with the same discipline as the code. A pull request that changes behavior without adding a changelog entry is incomplete.*
