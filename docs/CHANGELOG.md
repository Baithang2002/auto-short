# Changelog

All notable changes to this project are recorded here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Sections in each entry follow the Keep a Changelog vocabulary: **Added** (new features), **Changed** (updates to existing behavior), **Deprecated** (soon-to-be-removed features), **Removed** (removed features), **Fixed** (bug fixes), **Security** (vulnerability fixes).

Entries below the `[Unreleased]` section describe released versions. When work merges to the main branch under a version bump, it is moved from `[Unreleased]` to a versioned section with the release date. Each release entry is chronologically-ordered and immutable — corrections happen in later releases with explicit notes.

---

## [Unreleased]

Work in progress. Once released, entries here move to a versioned section.

---

## [0.6.3] — Media Acceptance & Domain Safety (backfilled 2026-07-10)

**Milestone: PR #9.6 — Media Acceptance & Domain Safety.** Media selection now requires independent evidence gates before accepting a candidate. Wrong-domain matches are rejected explicitly rather than selected on other merits.

### Added

- Independent evidence gates for candidate acceptance. A candidate must pass subject-match, environment-match, and shot-type-match gates independently — passing one strongly does not compensate for failing another.
- Confidence tier derived from visual-relevance evidence rather than provider-quality score alone.
- Canonical `VisualIntent` shared across providers so per-provider adapters agree on what the scene requires.
- Wrong-domain rejection. Candidates from a mismatched domain (aquarium footage for a desert-wildlife scene, indoor stock for a weather scene) are rejected explicitly.
- Domain-safe local-explainer fallback when no candidate satisfies the gates.
- Per-candidate rejection reasons in provider diagnostics.

### Changed

- Universal wildlife fallback removed. A scene requesting technical or human-subject visuals no longer receives a wildlife filler as a last resort. The local-explainer fallback runs instead.

### Compatibility

- Scenes that previously matched high-confidence candidates continue to match. Scenes that previously accepted low-confidence or wrong-domain candidates may now fall through to the local-explainer.

---

## [0.6.2] — Metadata Quality Improvement (backfilled 2026-07-10)

**Metadata Quality Improvement.** Deterministic topic classification replaces the channel-niche default in upload metadata. Title, description, and hashtag generation reflect the actual topic of each video.

### Added

- `TopicCategory` enum covering 17 high-level categories under `src/autovideo/intelligence/topic_metadata.py`, with a `TopicClassification` producing primary and optional secondary categories from a video topic string.
- `TopicMetadata` container carrying classification, generated title, description, Instagram caption, hashtags, and keyword list.
- Deterministic classification — the same topic string always yields the same classification.

### Changed

- Upload metadata (title, description, hashtags, keywords) now uses the detected topic category rather than the channel-niche default.

### Compatibility

- `upload_metadata.json` schema is unchanged. Only the values inside change for a given topic.

---

## [0.6.1] — Portrait Safety & Visual Relevance Guardrails (backfilled 2026-07-10)

**Milestone: PR #9.5 — Portrait Safety & Visual Relevance Guardrails.** Selection scoring gains explicit portrait-safety and visual-relevance criteria. Confidence thresholds tighten; generic stock footage is penalized.

### Added

- Portrait-first media selection. Candidates are scored by orientation fit for the target output aspect ratio before other criteria apply. Landscape and ultra-wide sources are eligible only when portrait-safe alternatives are absent.
- Portrait-safety scoring ensuring the visual subject remains centered when a landscape source is cropped to portrait.
- Visual-relevance scoring comparing candidate content against declared scene intent (subject, environment, shot type). Domain-mismatched candidates are penalized regardless of other quality metrics.
- Scene-importance weighting. Hook and turn scenes require higher confidence than filler scenes before selection is accepted.
- Portrait-safety verdicts and relevance-gate rejection reasons added to selection diagnostics.

### Changed

- Confidence thresholds tightened. A scene falls through to the next provider or the local-explainer fallback rather than accepting a low-confidence match.
- Generic stock footage penalized in scoring. Queries matching generic patterns score below queries with specific subjects.

### Compatibility

- Scenes that previously selected high-confidence portrait footage continue to select the same footage. Scenes that previously accepted landscape or low-confidence footage may now select a portrait-safe alternative or fall through.

---

## [0.6.0] — Provider Expansion & Registry Formalization (backfilled 2026-07-10)

**Milestone: PR #9 — Provider Expansion & Visual Source Intelligence.** Formalizes the capability registry and expands the stock-media provider set. Scene routing becomes capability-declared rather than order-based.

### Added

- `SceneType` classification driving per-scene provider selection based on visual character (wildlife, weather, technical, urban, and so on).
- Capability registry entries for Mixkit, Coverr, Videvo, Wikimedia Commons, NOAA, and ESA stock providers, joining Pexels, Pixabay, and NASA. Each provider declares which scene types it can serve.
- Per-scene provider diagnostics: which providers were consulted, which returned results, which was selected, and why.
- Capability-aware routing at scene-selection time. A `SceneType.WEATHER` scene prefers NOAA and ESA; a `SceneType.WILDLIFE` scene prefers Pexels and Pixabay.

### Changed

- Per-scene provider order is derived from capability declarations rather than a global preference list. The historical order (`pexels → pixabay → nasa`) is preserved as the default for scenes with no specific capability requirement.

### Compatibility

- Default routing for previously-supported scene types reproduces the historical provider order. Newly added providers activate only for scene types they declare.

---

## [0.5.1] — Music Subsystem Refactor (backfilled 2026-07-10)

**Music Subsystem Refactor.** Provider-agnostic music architecture behind a license-aware provider contract. Fallback is guaranteed to reach silence — rendering never stops because a music provider failed.

### Added

- Typed `MusicTrackProvider` contract with structured track and license metadata, and per-provider capability declarations.
- Concrete music providers: Jamendo, Pixabay Music, Mixkit, Generated (synthesizer), and Silence (terminal fallback). All deterministic; transports injectable for tests.
- `MusicPlanner` walking a configured registry chain with per-provider retries and license validation before rendering. Selection diagnostics persist to `output/music_selection.json`.
- License validation (`AUTO_VIDEO_REQUIRE_COMMERCIAL_LICENSE`, `AUTO_VIDEO_ALLOW_ATTRIBUTION`) enforced before a track can be selected.
- Validated `MusicConfig` on `AppConfig.music` covering provider order, retries, timeout, volume, fades, duration bounds, and license policy. Invalid values fail at load time with errors naming the variable.

### Changed

- Default music fallback chain is now `jamendo → pixabay → mixkit → generated → silence` (was `local folder → pixabay → generated`), configurable via `AUTO_VIDEO_MUSIC_PROVIDER_ORDER`. Explicit `--music PATH` still overrides everything.
- Default voice provider priority is now `elevenlabs → edge_tts → speechify` in development and production profiles. ElevenLabs is skipped automatically when credentials are absent.
- Music fade-in / fade-out are configurable (`AUTO_VIDEO_MUSIC_FADE_IN_MS`, `AUTO_VIDEO_MUSIC_FADE_OUT_MS`); defaults preserve previous behavior.

### Deprecated

- Automatic music selection from the local `music/` folder. The `local` name in a music provider order is accepted but skipped with a `DeprecationWarning`. Use `--music PATH` for operator-supplied tracks.

### Compatibility

- Explicit `--music PATH` remains the primary operator override. Legacy helpers (`pick_music_track`, `fetch_pixabay_music`, `fetch_jamendo_track`) remain in `auto_short.py` for compatibility but are no longer called by the pipeline.

---

## [0.5.0] — Content Intelligence & Source Planning (backfilled 2026-07-10)

**Milestone: PR #8 — Content Intelligence & Source Planning.** Adds the planning stage between script generation and media selection. Scenes route to providers by declared capability rather than by fixed provider order.

### Added

- Planning stage translating `Script → VisualIntent → QueryPlan → CapabilityRequirements → ProviderCapabilityRanking → SearchStrategy`. Each stage is a pure function of the previous.
- Provider capability declarations. Each provider states which visual capabilities it can serve; scene requirements are matched against declarations at planning time.
- Capability-aware routing replacing the previous fixed provider order for stock media. Providers that cannot serve a scene are skipped for that scene, not for the run.

### Changed

- Media selection consumes a `SearchStrategy` rather than a plain query string. The strategy carries the ordered provider list, per-provider query variants, and per-provider capability tags.

### Compatibility

- No user-visible behavior change. Default capability declarations reproduce the previous provider order for scenes with no declared special requirement.

---

## [0.4.0] — Intelligent Media Selection (backfilled 2026-07-10)

**Milestone: PR #7 — Intelligent Media Selection.** Introduces deterministic per-scene candidate scoring. Only the winning candidate is downloaded; scoring diagnostics are persisted for review.

### Added

- Typed intent / candidate / score / result model for per-scene media selection under `src/autovideo/media/selection.py`. Same inputs produce the same selection.
- Deterministic scoring across subject match, duration fit, orientation, resolution, and quality-gate criteria.
- Download-winner-only fetch behavior. Candidates are compared from lightweight metadata; only the winning candidate's video file is downloaded.
- Structured selection diagnostics attached to `MediaAsset.metadata`: score breakdown, warnings, rejection reasons, candidate count, confidence tier.

### Changed

- Per-scene stock media flow now runs candidate scoring before download. Legacy download-then-score-then-discard behavior is retired.

### Compatibility

- No user-visible behavior change. Scoring defaults reproduce the historical selection heuristics.

---

## [0.3.0] — Timeline Builder + Renderer Contract (backfilled 2026-07-10)

**Milestones: PR #5 — Timeline Builder** and **PR #6 — Renderer Contract.** Introduces the Timeline intermediate representation and the Renderer interface. The FFmpeg renderer consumes a `Timeline`; existing rendering behavior is preserved via a legacy adapter that translates timelines into the historical call shape.

### Added

- Timeline as the canonical intermediate representation between planning and rendering. Builder produces timelines from typed inputs; a validator enforces ordering, duration, and caption-alignment invariants. Timeline architecture documented in `docs/ARCHITECTURE.md`.
- `Renderer` protocol and `FfmpegTimelineRenderer` implementation in `src/autovideo/render/`. The renderer accepts a `Timeline` and returns a `RenderResult`.
- Legacy renderer adapter translating timelines into the argument shape the historical rendering functions expect.
- Environment-keyed `RenderProfile` carrying width, height, fps, duration ceiling, codec, and mastering parameters. Selectable via `AUTO_VIDEO_RENDER_PROFILE`, `RENDER_PROFILE`, or `ENVIRONMENT` environment variables.

### Changed

- The `auto_short.py` main flow instantiates `FfmpegTimelineRenderer` and passes a `Timeline`. Historical rendering functions remain in place as injected services.

### Compatibility

- No user-visible behavior change. Renderer produces functionally identical output for equivalent inputs.

---

## [0.2.0] — Domain Integration (backfilled 2026-07-10)

**Milestone: PR #4 — Domain Integration.** Introduces the typed domain layer under `src/autovideo/domain/`. Pipeline stages consume typed models where they cross layer boundaries; legacy dictionary passing continues to work behind conversion helpers.

### Added

- Typed domain models spanning script, voice, media, timeline, mastering, publishing, and metadata. Frozen dataclasses with enum types and construction-time invariant checks. Model catalog documented in `docs/ARCHITECTURE.md § Domain Layer`.
- Legacy conversion helpers (`from_legacy_dict()` / `to_legacy_dict()`) on the exchange types, preserving the historical JSON shape.

### Changed

- Layer boundaries in `auto_short.py` are crossed with typed models rather than dictionaries. Conversion happens at the edges of the touched stages.

### Compatibility

- No user-visible behavior change. Legacy dict inputs and outputs remain accepted at every touched call site. Output files are unchanged.

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
