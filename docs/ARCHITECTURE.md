# Architecture

This document is the technical reference for the platform. It describes how the codebase is organized, what each layer is responsible for, how components communicate, and why the architecture is shaped this way. When a pull request touches a boundary between layers, `ARCHITECTURE.md` is the source of truth for the boundary.

`VISION.md` explains *what* we are building. `ENGINEERING_GUIDE.md` explains *how* to build it. `ARCHITECTURE.md` explains *where* everything goes.

The platform is under active migration from a monolithic single-format Shorts tool to the layered structure documented below. Where the codebase currently diverges from the target, the divergence is called out explicitly. Contributors implementing new features should build against the target architecture, not against the legacy shape.

## Implementation Status

The table below tracks the maturity of each architectural component. Contributors update this table in the same pull request that changes a component's status.

| Component | Status | Notes |
|---|---|---|
| Foundation | ✅ Implemented | Cross-cutting utilities in place |
| Storage | ✅ Implemented | Artifact persistence + metadata |
| Providers | ✅ Implemented | Provider abstraction + configuration |
| Domain Integration | ✅ Implemented | Typed models and business rules wired into legacy stages |
| Timeline Builder | ✅ Implemented | Timeline IR emitted and bridged to the legacy renderer |
| Renderer (new contract) | ✅ Implemented | Timeline-based renderer interface with FFmpeg implementation |
| Media Selection | ✅ Implemented | Deterministic B-roll candidate scoring before Timeline construction |
| Source Planning | ✅ Implemented | Capability-driven query and provider strategy before media selection |
| Provider Expansion | ✅ Implemented | Scene-type routing and optional stock/archive/science provider registration |
| Content Scheduling | ✅ Implemented | Viability-aware topic ranking, uniqueness, category rotation, and durable history |
| Source Coverage Preflight | ✅ Implemented | Bounded metadata-only media probe before voice generation; scheduled runs recover to another topic when coverage is weak |
| Canonical Scene Entity Resolver | ✅ Implemented | Converts documentary phrasing into auditable, retrieval-safe scene entities without altering editorial identity |
| Semantic Visual Query Engine | ✅ Implemented | Translates canonical scene entities into provider-specific search language at the retrieval boundary |
| Publish Quality Gate | ✅ Implemented | Post-render artifact policy emits an auditable approve/defer/block verdict before unattended upload |
| Pipeline (orchestrator + stages) | 🚧 In Progress | Legacy monolithic `main()` still authoritative |
| Interface (CLIs, workflows) | 🚧 In Progress | Legacy entry points at repo root, new location planned |
| Long-form profile | 📋 Deferred | `bias_long.py` continues as a parallel variant |

Legend: ✅ Implemented · 🚧 In Progress · 📋 Planned · 🗓 Deferred

## Table of Contents

1. [Overview](#overview)
2. [Implementation Status](#implementation-status)
3. [Layered Architecture](#layered-architecture)
4. [Package Structure](#package-structure)
5. [Folder Layout](#folder-layout)
6. [Domain Models](#domain-models)
7. [Provider Layer](#provider-layer)
8. [Storage Layer](#storage-layer)
9. [Timeline Architecture](#timeline-architecture)
10. [Rendering Architecture](#rendering-architecture)
11. [Configuration Layer](#configuration-layer)
12. [Pipeline and Orchestration](#pipeline-and-orchestration)
13. [Data Flow](#data-flow)
14. [Dependency Graph](#dependency-graph)
15. [Migration Strategy](#migration-strategy)
16. [Extension Points](#extension-points)
17. [Architectural Decision Records](#architectural-decision-records)

---

## Overview

The platform turns a **topic intent** — a niche, a format, a set of constraints — into a **published video** by walking a set of composable stages in order. Each stage consumes artifacts produced by the previous one and emits new artifacts that the next stage consumes. The stages are:

```
  Intent  →  Script  →  Voice  →  Media  →  Timeline  →  Render  →  Master  →  Publish
```

Each transition is a boundary. Each boundary is inspectable, replayable, and interchangeable. A failed publish does not force re-rendering; a failed render does not force re-scripting; a failed voice generation does not force re-scripting. The pipeline's job is to walk the boundaries; the layers' job is to make each boundary well-defined.

The rest of this document defines how the layers implement those boundaries.

## Layered Architecture

The platform is decomposed into six layers. `ENGINEERING_GUIDE.md` names them and states the strict rule: **imports flow only from higher layers to lower layers**. This document explains what each layer contains and how it presents itself to the layer above.

| # | Layer | Purpose | Depends on |
|---|---|---|---|
| 1 | **Foundation** | Cross-cutting utilities. Small, pure, well-tested. | Nothing internal. |
| 2 | **Storage** | Content-addressable persistence and artifact lifecycle. | Foundation |
| 3 | **Providers** | Adapters for external services (LLM, TTS, stock, music, uploaders) behind common interfaces. | Foundation, Storage |
| 4 | **Domain** | The video-production business rules. Timeline, scenes, cuts, mastering, format profiles. | Foundation, Storage, Providers |
| 5 | **Pipeline** | Orchestration — stages, DAGs, resume points, scheduling. | Foundation, Storage, Providers, Domain |
| 6 | **Interface** | Entry points — CLIs, HTTP handlers, workflow YAMLs. | All lower layers |

An import from a higher-numbered layer into a lower-numbered layer is required. An import in the opposite direction is a bug and must be caught in review.

### Layer responsibilities in detail

**Foundation.** Path handling, ID generation, hashing, retries with backoff, structured logging, small pure helpers. Zero business knowledge. If a helper is useful to two unrelated layers, it belongs in Foundation. Foundation has the strictest test discipline: every function has unit tests.

**Storage.** All persistence. Reading and writing artifacts, computing content addresses, moving artifacts between local disk and object storage, retention, cleanup. Storage has no opinion about what an artifact *is* — it only cares about bytes and metadata.

**Providers.** Every external service is wrapped in a provider module: one for each LLM vendor, one for each TTS vendor, one per stock library, one per music library, one per upload platform. Providers implement narrow interfaces defined in the Domain layer. A provider never contains business logic; it is a transport wrapper with typed inputs and typed outputs.

**Domain.** The heart of the platform. Types for scripts, segments, voice tracks, media assets, timelines, cuts, captions, and mastered videos. Business rules that hold true regardless of which provider or which format is in use. Format profiles (Shorts, long-form, podcast) live here as data.

**Pipeline.** Stages that consume artifacts and produce artifacts. Orchestration logic that knows the order, the resume points, the retry policy per stage. The pipeline does not know how to fetch a video from Pexels; it knows how to ask the Providers layer for stock media and how to hand what it gets back to the Domain layer for timeline construction.

**Interface.** Command-line entry points, HTTP endpoints (if any), and the GitHub Actions workflows that fire the pipeline on a schedule. Interfaces should be thin — they parse inputs, invoke Pipeline, and format outputs.

## Package Structure

The **target** package structure looks like this. It is the destination of the current migration; individual modules may still live in legacy locations while incremental PRs move them into place.

```
platform/
├── foundation/                    # Layer 1
│   ├── ids.py                     # Deterministic ID generation
│   ├── hashing.py                 # Content-address hashing
│   ├── paths.py                   # Path helpers, artifact routing
│   ├── retries.py                 # Retry decorators with backoff
│   ├── logging.py                 # Structured logging setup
│   └── time.py                    # Timezone-aware time helpers
│
├── storage/                       # Layer 2
│   ├── base.py                    # ArtifactStore interface
│   ├── local.py                   # Local filesystem backend
│   ├── s3.py                      # Object storage backend (future)
│   ├── metadata.py                # Metadata sidecar handling
│   └── retention.py               # Cleanup policies
│
├── providers/                     # Layer 3
│   ├── base/                      # Provider interfaces (also referenced from domain/)
│   │   ├── script.py              # ScriptProvider ABC
│   │   ├── voice.py               # VoiceProvider ABC
│   │   ├── stock.py               # StockMediaProvider ABC
│   │   ├── music.py               # MusicProvider ABC
│   │   ├── image.py               # ImageProvider ABC
│   │   └── uploader.py            # UploadProvider ABC
│   ├── script/
│   │   ├── gemini.py
│   │   ├── sambanova.py
│   │   ├── groq.py
│   │   └── openai.py
│   ├── voice/
│   │   ├── edge_tts.py
│   │   ├── elevenlabs.py
│   │   └── speechify.py
│   ├── stock/
│   │   ├── pexels.py
│   │   ├── pixabay.py
│   │   └── nasa.py
│   ├── music/
│   │   ├── jamendo.py
│   │   └── local_library.py
│   ├── image/
│   │   ├── gemini_image.py
│   │   └── openai_image.py
│   └── uploader/
│       ├── youtube_api.py         # YouTube Data API v3
│       ├── youtube_browser.py     # Playwright fallback
│       ├── instagram_browser.py
│       └── facebook_browser.py
│
├── domain/                        # Layer 4
│   ├── models/                    # Typed data models
│   │   ├── script.py              # Script, Segment
│   │   ├── voice.py               # VoiceTrack
│   │   ├── media.py               # MediaAsset, ClipSelection
│   │   ├── timeline.py            # Timeline, Cut
│   │   ├── caption.py             # CaptionTrack
│   │   ├── master.py              # MasteredVideo
│   │   └── publish.py             # PublishTarget, PublishResult
│   ├── profiles/                  # Render profiles
│   │   ├── base.py                # RenderProfile ABC
│   │   ├── shorts_vertical.py
│   │   ├── educational_horizontal.py
│   │   └── longform_documentary.py
│   ├── scripting/                 # Business rules for scripts
│   │   ├── quality.py             # Script quality checks
│   │   └── prompts.py             # Prompt templates
│   ├── selection/                 # Business rules for media selection
│   │   ├── query.py               # Query enrichment (qualifiers, niche routing)
│   │   ├── scoring.py             # Clip scoring across providers
│   │   └── dedup.py               # Cross-clip deduplication
│   ├── editing/                   # Business rules for timeline construction
│   │   ├── pacing.py
│   │   └── captions.py
│   └── mastering/                 # Audio mixing, loudness, ffmpeg graphs
│       ├── audio.py
│       └── ffmpeg_graph.py
│
├── pipeline/                      # Layer 5
│   ├── stages/                    # Individual pipeline stages
│   │   ├── script.py
│   │   ├── voice.py
│   │   ├── media.py
│   │   ├── timeline.py
│   │   ├── render.py
│   │   ├── master.py
│   │   └── publish.py
│   ├── orchestrator.py            # Stage sequencing, DAG, resume points
│   ├── scheduler.py               # Cron / date-based topic rotation
│   └── review_queue.py            # Human-review escape hatch
│
├── interface/                     # Layer 6
│   ├── cli/
│   │   ├── pipeline.py            # `python -m platform.interface.cli.pipeline`
│   │   ├── uploader.py
│   │   ├── dashboard.py
│   │   └── generate_assets.py
│   └── workflows/                 # .github/workflows/ referenced from here
│
└── config/                        # Configuration schemas (not a "layer" - loaded by all)
    ├── schema.py                  # Pydantic-style validation
    ├── loader.py                  # Precedence: CLI > env > file > default
    └── defaults/
        ├── shorts.yaml
        ├── educational.yaml
        └── longform.yaml
```

The current codebase does not yet mirror this layout. Legacy modules — `auto_short.py`, `pipeline.py`, `uploader.py`, `yt_data_api.py`, `_pipeline_base.py`, and others — currently live at the repository root. See [Migration Strategy](#migration-strategy) for how they map onto the target and the sequence for moving them.

## Folder Layout

Beyond the Python package, the repository contains several first-class directories.

```
auto-short/
├── platform/           # Target package (see above)
├── docs/               # VISION, ENGINEERING_GUIDE, ARCHITECTURE, CHANGELOG, ROADMAP
├── config/             # Per-channel configuration files (checked in)
├── state/              # Persisted rotation and dedup state (committed by CI)
├── input_clips/        # Optional operator-provided source footage (gitignored)
├── music/              # Optional operator-provided music library (gitignored)
├── output/             # Runtime artifacts (gitignored, ephemeral)
├── videos/             # Review queue for pending / approved / rejected videos (gitignored)
├── assets/             # Static branding assets (avatar, banner, watermark)
├── browser_session/    # Persisted Playwright session cookies (gitignored)
├── .github/
│   └── workflows/      # CI schedule + one-off automation
└── requirements.txt    # Pinned Python dependencies
```

**Directories that are checked in** contain source of truth: code, docs, config schemas, CI, and rotation state.

**Directories that are gitignored** contain runtime artifacts, operator secrets, or content that cannot be committed for licensing reasons (stock footage from the operator's local library).

## Domain Models

Domain models are the shape of the artifacts that flow between stages. They are declarative — no methods with business logic, only structure and validation.

### Script

A `Script` is the output of the script stage and the input to voice and media stages. It contains:

| Field | Type | Meaning |
|---|---|---|
| `title` | str | Short punchy title, format-appropriate length |
| `description` | str | Longer copy for the publish platform |
| `hashtags` | list[str] | Ordered list, lowercase, `#`-prefixed |
| `music_mood` | enum | One of the supported moods (`mysterious`, `inspiring`, ...) |
| `segments` | list[Segment] | Ordered list, one per timeline segment |
| `metadata` | dict | Provider used, model, tokens consumed, cost |

A `Segment` contains:

| Field | Type | Meaning |
|---|---|---|
| `narration` | str | Spoken text for this segment |
| `broll` | str | Primary visual keyword |
| `broll_queries` | list[str] | Alternate search terms, ordered specific → generic |
| `estimated_duration` | float | Predicted playback seconds |

### VoiceTrack

The output of the voice stage. One `VoiceTrack` per segment:

| Field | Type | Meaning |
|---|---|---|
| `audio_path` | Path | Content-addressed audio file |
| `duration` | float | Actual measured duration |
| `provider` | str | Which voice provider produced it |
| `voice_id` | str | Provider-specific voice identifier |
| `retimed` | bool | True if the pipeline sped/slowed the audio to fit |

### MediaAsset

The output of the media stage. One `MediaAsset` per segment:

| Field | Type | Meaning |
|---|---|---|
| `local_path` | Path | Path to the file on disk |
| `source` | enum | `pexels`, `pixabay`, `nasa`, `gemini_image`, `local`, `dalle` |
| `source_id` | str | Provider-specific identifier for dedup |
| `duration` | float | Original playback duration |
| `dimensions` | tuple[int, int] | Width × height |
| `is_image` | bool | True if a still (needs Ken-Burns treatment) |
| `attribution` | dict | Rights-holder metadata for CC licensed content |

Media selection is implemented as a deterministic pre-timeline boundary in `src/autovideo/media/`. Source planning first converts `VisualIntent` into `SceneType`, `QueryPlan`, `CapabilityRequirement`, and `SearchStrategy` objects so provider routing is based on required visual capability instead of a fixed provider order. The selector then normalizes provider results into `StockCandidate`, assigns deterministic `CandidateScore` values, and returns a `MediaSelectionResult`. Only the winning candidate is downloaded. Planning and selection diagnostics are stored inside `MediaAsset.metadata` and are intentionally not copied into upload metadata or queue metadata.

### Timeline

A `Timeline` is a declarative description of a video. It is deterministic — the same `Timeline` always renders to the same bytes.

| Field | Type | Meaning |
|---|---|---|
| `format_profile` | str | Which render profile applies (`shorts_vertical`, etc.) |
| `dimensions` | tuple[int, int] | Target output size |
| `fps` | int | Frames per second |
| `cuts` | list[Cut] | Ordered list of what plays when |
| `caption_track` | CaptionTrack | Overlay text with timings |
| `voice_track` | Path | Combined voiceover audio |
| `music_track` | Optional[MusicTrack] | Background music, if any |
| `total_duration` | float | Sum of cut durations |

A `Cut` describes one segment on the timeline:

| Field | Type | Meaning |
|---|---|---|
| `start` | float | Timeline seconds |
| `end` | float | Timeline seconds |
| `media` | MediaAsset | What to show |
| `crop` | Crop | How to crop / pan / zoom the media |
| `voice_segment_index` | int | Which voice segment plays underneath |

### CaptionTrack

The caption overlay. Word-chunked, optionally with per-word highlights.

| Field | Type | Meaning |
|---|---|---|
| `entries` | list[CaptionEntry] | Ordered timed captions |
| `style` | CaptionStyle | Font, size, colors, position |

Each `CaptionEntry` has `start`, `end`, `text`, and optional `highlight_words`.

### MasteredVideo

The output of the master stage. This is what gets uploaded.

| Field | Type | Meaning |
|---|---|---|
| `video_path` | Path | Rendered + mastered video file |
| `duration` | float | Final measured duration (post-trim) |
| `format_profile` | str | Which profile was applied |
| `music_included` | bool | True if music mixed in |
| `platform_variants` | dict[str, Path] | Optional per-platform variants (e.g., YT-safe silent version) |

### PublishTarget and PublishResult

Publishing is per-platform. Each platform is its own `PublishTarget` with its own `PublishResult`:

| PublishTarget Field | Type | Meaning |
|---|---|---|
| `platform` | str | `youtube`, `instagram`, `facebook`, ... |
| `credentials_ref` | str | Named credential reference, not the credential itself |
| `metadata` | dict | Per-platform title / description / tags |

| PublishResult Field | Type | Meaning |
|---|---|---|
| `status` | enum | `ok`, `error`, `rate_limited`, `blocked` |
| `url` | Optional[str] | Public URL if published |
| `platform_id` | Optional[str] | Platform-specific video ID |
| `error` | Optional[str] | Error message if not ok |
| `attempted_at` | datetime | When the attempt started |

## Provider Layer

Providers are the platform's interchange point with external services. They are the only place vendor SDKs are imported. Every provider follows the same architectural pattern.

### Provider interfaces

Six provider interfaces are defined in `providers/base/`. Each is a minimal ABC (abstract base class) with a small number of methods.

| Interface | Methods | Purpose |
|---|---|---|
| `ScriptProvider` | `generate(prompt) -> ScriptResult` | Turn a prompt into a structured script |
| `VoiceProvider` | `synthesize(text, voice_id) -> VoiceResult` | Turn text into audio |
| `StockMediaProvider` | `search(query) -> list[MediaCandidate]`, `download(candidate) -> Path` | Find and fetch stock footage/images |
| `MusicProvider` | `search(mood) -> list[TrackCandidate]`, `download(track) -> Path` | Find and fetch background music |
| `ImageProvider` | `generate(prompt) -> Path` | Generate an image from a prompt |
| `UploadProvider` | `upload(video, metadata) -> PublishResult` | Publish a video to a platform |

All methods return typed result objects — not raw strings, not tuples, not raw exceptions for expected failure modes.

### Result types

Every provider method returns a discriminated result. Expected failure modes are values; only bugs raise exceptions.

```python
class ProviderResult(Enum):
    OK = "ok"                   # Success; result carries the payload
    NOT_AVAILABLE = "not_available"  # Provider unusable (misconfigured, unreachable)
    RATE_LIMITED = "rate_limited"    # Provider hit a quota; result carries retry-after
    AUTH_ERROR = "auth_error"        # Credentials invalid
    NOT_FOUND = "not_found"          # Query returned nothing usable
    TRANSIENT_ERROR = "transient_error"  # Retry may succeed
```

Callers pattern-match on the result kind. Orchestration decides whether to retry, fall back to a different provider, or surface the failure to the operator.

### Provider registry

Providers register themselves with a registry keyed by interface and name:

```
registry.register("script", "gemini", GeminiScriptProvider)
registry.register("script", "sambanova", SambaNovaScriptProvider)
```

The registry is populated at import time. Orchestration asks the registry for "all script providers, ordered by preference" and iterates until one returns `OK`. Adding a new provider requires:

1. Implementing the interface in `providers/<kind>/<name>.py`.
2. Calling `registry.register(...)` at module import time.
3. Nothing else.

No code in the pipeline layer, domain layer, or interface layer changes when a provider is added.

### Provider ranking

Providers are ordered by a **preference list**, which is configuration, not code. The default preference for each provider kind is documented in the format profile. Operators override per-run via configuration flags.

Preference lists are ordered by (in this order):

1. **Cost** — free tier before paid, cheap before expensive
2. **Quality** — better-perceived output before worse
3. **Reliability** — historically stable providers before flaky ones

Providers may be **disabled** in configuration without being removed. A disabled provider is skipped without being tried.

### Provider health checks

Every provider implements `check() -> HealthResult`. This is a cheap operation — a lightweight API call or a credential validation, not a real content generation. Health checks run:

- On operator command (`python -m platform.interface.cli.doctor`)
- Optionally before each pipeline run
- Never on the schedule's critical path unless configured

## Storage Layer

The storage layer treats every produced artifact as a **content-addressed blob** with sidecar metadata. The primary interface is `ArtifactStore`, implemented by concrete backends (local filesystem, S3-compatible object store, in-memory for tests).

### ArtifactStore interface

```python
class ArtifactStore(ABC):
    def put(self, artifact: bytes | Path, metadata: dict) -> ArtifactRef: ...
    def get(self, ref: ArtifactRef) -> Path: ...
    def exists(self, ref: ArtifactRef) -> bool: ...
    def metadata(self, ref: ArtifactRef) -> dict: ...
    def delete(self, ref: ArtifactRef) -> None: ...  # Explicit only, no side effects
```

An `ArtifactRef` is a compact identifier — logically a hash plus a namespace — that resolves to bytes plus metadata regardless of backend.

### Content addressing

Artifacts are named by a hash of their inputs, not by their timestamps. The same script prompt with the same seed produces the same script artifact identifier. This gives us:

- **Deduplication** — an identical input produces the same output location, so repeated runs don't waste storage.
- **Cache reuse** — a stage can check if its output already exists before doing the work.
- **Verifiability** — a hash mismatch means the input changed.

Timestamps are recorded in metadata, not in filenames.

### Atomic writes

Writes go to a temporary path, then rename to the final path once the write completes. Concurrent writers producing the same content produce the same output because they compute the same hash.

### Metadata sidecars

Each artifact has an accompanying metadata document (JSON) recording:

- Origin (which stage produced it, which provider, which config)
- Input hashes (what upstream artifacts fed into it)
- Parameters (prompt, seed, options)
- Timestamps (created, last accessed)
- Size, format, checksum
- Lineage (chain back to the intent)

Metadata is queryable independently of the artifact bytes. A cleanup routine can identify orphaned artifacts by examining metadata.

## Timeline Architecture

A `Timeline` is a **declarative recipe for a video**. It is the intermediate representation between "we have voice, media, and mood" and "we have a rendered file."

### Why an explicit timeline

Older versions of the pipeline built videos by walking the segments and stitching audio + video pairs directly. That made cross-cutting concerns — variable pacing, non-linear transitions, per-word caption highlights, cross-fades between b-roll clips — either impossible or bolted-on. The timeline is the escape from that bind.

### Timeline construction

The timeline is built by the domain layer's `editing` module from:

- The `Script` (segments, timings, hooks)
- The `VoiceTrack` list (one per segment, actual measured durations)
- The `MediaAsset` list (one per segment, plus overflow for cutaways)
- The `RenderProfile` (dimensions, pacing rules, caption style)
- The `CaptionTrack` (word-chunked overlays)

The output is a `Timeline` where every second of output is accounted for and every crop, cut, and overlay is explicit.

### Timeline invariants

Every timeline satisfies these invariants:

- Cut start times are strictly monotonically increasing.
- Cut ranges are non-overlapping.
- Cut ranges fully cover `[0, total_duration]` — no gaps.
- Caption entries fit within `total_duration`.
- Every cut's `voice_segment_index` refers to a real voice segment.
- `total_duration <= profile.max_duration`.

Timeline construction produces a timeline that satisfies all invariants or raises a domain error. Rendering trusts the invariants.

## Rendering Architecture

Rendering turns a `Timeline` into a `MasteredVideo`. It is a pure function: same timeline in, same bytes out.

### Renderer contract

```python
class Renderer(ABC):
    def render(self, timeline: Timeline, out_dir: Path) -> MasteredVideo: ...
```

The current implementation uses `ffmpeg` invoked via subprocess. Alternative implementations — a Python-native renderer, a GPU-accelerated one, a cloud-based one — could implement the same contract.

### Render stages inside the renderer

Even the renderer itself is layered:

1. **Segment build.** Each `Cut` is rendered as a self-contained short mp4 with its own audio.
2. **Concat.** Segments are concatenated. Re-encoded on concat to normalize parameter drift.
3. **Caption burn.** Captions are burned onto the concatenated video via `subtitles` filter with `.ass` file.
4. **Music mix.** Optional. Background music is mixed under the voice with sidechain compression.
5. **Master.** Loudness normalization to `-16 LUFS`, peak limiting to `-1 dBFS`, `+faststart` MP4 flag.
6. **Trim.** If output exceeds `profile.max_duration`, trim from the tail.
7. **Variant.** For platforms with special constraints (YouTube-safe silent version), a variant is produced.

Each stage produces an artifact that can be inspected. A rendering bug at stage 4 does not require redoing stages 1–3.

### Determinism

The renderer aims to be deterministic to the byte where practical. In practice, encoder non-determinism (H.264 encoder threading) makes exact byte-equality unreliable across runs. What is guaranteed:

- Duration is identical
- Dimensions are identical
- Frame count is identical
- Audio waveform is identical (uncompressed)

Byte-equality is aspirational, not required.

## Configuration Layer

Configuration is not itself a layer in the strict sense — it does not participate in the imports hierarchy. It is a cross-cutting concern loaded by every layer.

### Precedence

Higher wins:

1. Command-line flags
2. Environment variables
3. Per-run config files (`--config path.yaml`)
4. Per-channel config files (checked in under `config/<channel>.yaml`)
5. Format-profile defaults (bundled under `platform/config/defaults/`)

### Schema validation

Every configuration is validated at load time against a Pydantic-style schema. Malformed configuration is caught before pipeline execution consumes provider budget. Validation errors name the offending field and state the expected type or range.

### Secrets

Configuration files never contain secret values. They reference secrets by name (`gemini_api_key: env:GEMINI_API_KEY`), and the loader resolves the reference against environment variables or a secret manager. This lets configuration files be committed without leaking credentials.

## Pipeline and Orchestration

The pipeline layer is responsible for **stage sequencing** and **cross-stage state management**.

### Stages

Each pipeline stage is a function that consumes typed input artifacts and produces typed output artifacts. Stage signatures make dependencies explicit:

```python
def script_stage(intent: Intent, config: Config) -> Script: ...
def voice_stage(script: Script, config: Config) -> list[VoiceTrack]: ...
def media_stage(script: Script, voices: list[VoiceTrack], config: Config) -> list[MediaAsset]: ...
def timeline_stage(script: Script, voices, media, config) -> Timeline: ...
def render_stage(timeline: Timeline, config: Config) -> MasteredVideo: ...
def publish_stage(video: MasteredVideo, targets: list[PublishTarget]) -> list[PublishResult]: ...
```

### Orchestrator

The orchestrator walks stages in order, persists intermediate artifacts via the Storage layer, and manages resume points. If a stage fails, the orchestrator can restart from the last successful stage — this is the concrete implementation of the resume-from-stage principle in `VISION.md`.

### Resume points

Every stage's output is persisted with a deterministic identifier before the next stage begins. On restart, the orchestrator checks for existing outputs and skips stages whose outputs are present and valid.

Configuration flags control resume behavior:

- `--from-stage <name>` — force restart from a specific stage, discarding downstream artifacts
- `--only-stage <name>` — run one stage and stop
- `--dry-run` — walk the plan without executing external calls

### Scheduling

The scheduler is a thin layer ahead of the orchestrator that:

- Loads candidates from independent topic sources (`topics.txt`, optional `topics.json`)
- Scores candidates with the Documentary Viability Engine before any provider or script work
- Ranks approved candidates by viability, canonical-subject uniqueness, and category diversity
- Uses REVIEW candidates, then a configured evergreen pool, to guarantee forward progress
- Persists selections, deferrals, rejections, and generated topics in `state/content_history.json`
- Invokes the orchestrator with that topic
- Handles same-day deduplication (a backup cron should not double-post)
- Writes `output/scheduler_report.json` for every autonomous selection

The current implementation uses GitHub Actions cron. A future self-hosted or serverless invoker would implement the same interface.

## Data Flow

The end-to-end data flow for one video looks like this:

```
     [Intent]                 topic, format, profile
        │
        ▼
     Script stage             ─→ Providers.script (Gemini → SambaNova → Groq → OpenAI)
        │                     Persists: Script + metadata
        ▼
     Voice stage              ─→ Providers.voice (Edge-TTS → Speechify → ElevenLabs)
        │                     Persists: list[VoiceTrack]
        ▼
     Media stage              ─→ Providers.stock (Pexels → Pixabay → NASA)
        │                     ─→ Providers.image (Gemini Image as fallback)
        │                     Persists: list[MediaAsset]
        ▼
     Timeline stage           Domain-only. No provider calls.
        │                     Persists: Timeline
        ▼
     Music stage              ─→ Providers.music (Jamendo → Pixabay → Mixkit → Generated → Silence)
        │                     License-validated; order is configuration
        │                     Persists: MusicTrack (optional) + music_selection.json
        ▼
     Render stage             ─→ Renderer (ffmpeg)
        │                     Persists: MasteredVideo
        ▼
     Publish stage            ─→ Providers.uploader (YouTube API → browser fallback)
        │                     Persists: list[PublishResult]
        ▼
     Analytics feedback       (Future — feeds back into topic selection)
```

Each arrow is a well-defined artifact handoff. Each stage's output is content-addressed so cache reuse works across runs.

## Dependency Graph

```
                                Interface (CLI, workflows)
                                        │
                                        ▼
                              Pipeline (stages, orchestrator)
                                        │
                                ┌───────┴───────┐
                                ▼               ▼
                             Domain          Providers
                                │               │
                                └───┬───────────┘
                                    ▼
                                 Storage
                                    │
                                    ▼
                                Foundation
```

Every arrow points down. There are no arrows pointing up. Sibling layers do not import each other — Domain and Providers both depend on Storage, but never on each other directly. Where Domain needs to instruct a provider, it does so through an interface that lives in `providers/base/`, and Domain depends on the *interface*, not on any implementation.

## Migration Strategy

The current codebase is monolithic. The target codebase is layered. The migration follows the strict rules from `ENGINEERING_GUIDE.md § Migration Strategy` — additive first, one subsystem at a time, feature parity before switching, reversible flags.

**Migration prioritizes architectural correctness and production stability over migration speed.** A daily Shorts pipeline is running unattended; preserving that production is more important than reaching the target architecture on any specific timeline.

### Current → target mapping

| Current location | Target location | Migration state |
|---|---|---|
| `auto_short.py` (monolithic) | Split across `pipeline/`, `domain/`, `providers/`, `foundation/` | Not started |
| `pipeline.py`, `_pipeline_base.py` | `platform/interface/cli/pipeline.py` + `platform/pipeline/orchestrator.py` | Not started |
| `pipeline_daily.py` | `platform/pipeline/scheduler.py` + `platform/interface/cli/daily.py` | Not started |
| `uploader.py` | `platform/providers/uploader/*.py` | Not started |
| `yt_data_api.py` | `platform/providers/uploader/youtube_api.py` | Not started |
| `video_queue.py` | `platform/pipeline/review_queue.py` | Not started |
| `review_dashboard.py` | `platform/interface/cli/dashboard.py` + templates | Not started |
| `upload_worker.py` | `platform/pipeline/scheduler.py` (merged) | Not started |
| `bias_long.py`, `pipeline_biasfiles*.py` | Long-form profile + separate scheduler | Deferred |
| `sfx.py`, `charts.py`, `text_cards.py`, `thumbnail_gen.py` | `domain/mastering/` + `domain/branding/` | Deferred |

"Deferred" means: not planned in the current migration window. These modules continue to work at their current location. When the migration reaches them, they move; until then, they stay.

### Migration sequence

The migration is ordered by dependency direction. Lower layers move first because higher layers depend on them:

1. **Foundation.** Extract cross-cutting utilities from `auto_short.py` and other modules into `platform/foundation/`. This is pure refactor — no behavior change.
2. **Storage.** Introduce `ArtifactStore` and route existing artifact-writing calls through it. Legacy paths continue to work.
3. **Provider interfaces.** Define the six provider ABCs in `platform/providers/base/`. Implement one provider (Pexels) as the reference. Legacy code continues.
4. **Provider migrations.** One provider per PR: Pexels first, then Pixabay, NASA, Gemini, SambaNova, Groq, OpenAI, Edge-TTS, Speechify, Jamendo, YouTube API, browser uploaders. Each migration is a two-step: add the new implementation, then switch the caller.
5. **Domain models.** Introduce typed models (`Script`, `Timeline`, etc.). Adapt legacy code to consume the new types.
6. **Pipeline stages.** Split `auto_short.py`'s `main()` into typed stages. This is the biggest single change and will span multiple PRs.
7. **Interface.** Move CLI entry points into `platform/interface/cli/`. Legacy entry points continue to work as thin shims.
8. **Cleanup.** Once all callers have migrated, remove deprecation shims. This is the last step.

Each numbered step is potentially many PRs. The rule is: after any single PR, the pipeline still works end-to-end. No PR leaves the tree half-migrated.

## Extension Points

The architecture is designed to grow along specific, well-defined seams. Additions along these seams do not require touching core components. The list below describes where contributors are expected to extend the platform and what a "well-behaved" extension looks like.

### New script (LLM) provider

Add `platform/providers/script/<name>.py` implementing `ScriptProvider`. Register with the provider registry at module import time. Update the default preference list in the appropriate render profile if the new provider should be tried automatically. No changes to pipeline, domain, or interface layers are needed.

### New voice (TTS) provider

Add `platform/providers/voice/<name>.py` implementing `VoiceProvider`. Register. Update profile preferences if applicable. Contract tests must pass.

### New stock media provider

Add `platform/providers/stock/<name>.py` implementing `StockMediaProvider`. The provider handles its own query enrichment (via the shared query-qualifier helper) and returns typed candidates with source attribution. Register with the registry.

### New music provider

Add `src/autovideo/providers/music/<name>.py` implementing the `MusicTrackProvider` contract (`fetch_track(query) -> MusicTrack` with structured `MusicLicense` metadata and declared `MusicCapability` values), register it in `build_music_registry`, and add its name to the supported set in `autovideo.config.music`. Provider order is configuration (`AUTO_VIDEO_MUSIC_PROVIDER_ORDER`); the `MusicPlanner` validates every candidate's license before rendering and degrades to silence when all providers fail. No planner or business-logic changes are needed for a new source (Epidemic Sound, Soundstripe, etc.).

### New image provider

Add `platform/providers/image/<name>.py` implementing `ImageProvider`. Used only when stock/video fallback cannot supply footage for a segment.

### New upload provider (platform)

Add `platform/providers/uploader/<platform>_<transport>.py` — the naming convention encodes both the target platform (`youtube`, `instagram`, etc.) and the transport (`api`, `browser`). Implement `UploadProvider`. The API path is preferred; browser fallbacks exist for platforms without usable APIs. Publishing metadata (title, description, tags, category) is passed as a `PublishTarget`, not as constructor arguments.

### New video format

Add a new `RenderProfile` in `platform/domain/profiles/<format>.py`. Declare dimensions, target duration, caption style, pacing rules, safe zones, mastering targets, and per-format provider preferences. Profiles inherit from a base declaratively (single-parent only). No new pipeline stages or renderer code should be needed — if a format requires new rendering behavior, either the profile is incomplete or the renderer's abstraction is incorrect.

### New render profile variant (same format, different tuning)

Add a new profile that inherits from an existing base profile and overrides the specific fields that differ. This is the mechanism for A/B testing pacing, caption styles, or duration targets within a format.

### New workflow (scheduled cadence, one-off automation)

Add `.github/workflows/<name>.yml`. Workflows are interface-layer entities — they invoke the CLI, they do not embed business logic. A workflow that reaches into domain code is doing too much.

### New CLI command

Add `platform/interface/cli/<name>.py`. CLI commands are thin: parse inputs, call into the pipeline or a single domain function, format the result. Business logic in a CLI module is a code smell.

### New storage backend

Implement `ArtifactStore` in `platform/storage/<backend>.py`. The interface is unchanged; only the backend swaps. Configuration selects the backend at deployment time. Existing pipelines must continue to work without modification when a new backend is added.

### New review or approval workflow

Add a stage or hook that pauses the orchestrator between the render and publish stages. Review queues are the standard mechanism (`platform/pipeline/review_queue.py`). A new review flow is a configuration decision — which stage pauses, what data is presented to the reviewer, how the approval is recorded — not a business-logic change.

### Rule of thumb

If an extension requires modifying files in a layer other than its own home layer, either the extension is misplaced or the architecture has a missing seam. In the second case, propose an architectural change via a new ADR **before** implementing the extension.

## Architectural Decision Records

Architectural Decision Records document *why* the architecture is what it is. Each ADR states the decision, the reasoning, alternatives that were considered, and the current status. Contributors — human or AI — should read the relevant ADR before proposing a change that touches an established boundary.

The status field means:

- **Active** — the decision is in force. Contributors should design new code to conform.
- **Future** — the decision is planned but not yet implemented. Legacy code diverges; new code should conform.
- **Deferred** — the decision is planned but not scheduled. Contributors should not preemptively conform; wait for a scheduled migration PR.
- **Superseded** — the decision has been replaced by a later ADR. See the referenced replacement.

### ADR-001: Filesystem storage before object storage

**Decision.** The initial storage backend is the local filesystem. Object storage (S3-compatible) is a future backend behind the same interface.

**Rationale.** Local filesystem is zero-cost, zero-configuration, and adequate for a single-operator platform. Object storage adds cost, latency, and credentials without a corresponding immediate benefit. The `ArtifactStore` interface is designed to be backend-agnostic from day one, so switching backends later requires only wiring, not business-logic changes.

**Alternatives considered.** SQLite for artifact metadata (rejected because artifacts themselves are blobs; SQLite adds indirection without solving the storage question). Direct S3 from day one (rejected because it imposes cost and complexity on hobbyist deployments and doesn't materially improve reliability for a single-operator workflow).

**Status.** Active.

---

### ADR-002: Provider abstraction with typed result types

**Decision.** Every external service (LLM, TTS, stock, music, image, uploader) is wrapped in a provider module that implements a narrow interface. Provider methods return discriminated result types (`Ok`, `NotAvailable`, `RateLimited`, `AuthError`, `NotFound`, `TransientError`) rather than raising exceptions for expected failures.

**Rationale.** The platform is explicitly provider-agnostic (`VISION.md`). Any provider may become unavailable, expensive, rate-limited, or deprecated without notice. The pipeline needs to route around such conditions programmatically, which requires structured — not exceptional — failure representation. Exceptions couple failure to control flow in ways that make fallback orchestration brittle.

**Alternatives considered.** Exception-based error handling (rejected — leads to fragile `try/except/except/except` chains in orchestration and hides which failures are expected). Provider-native return types with per-provider handling (rejected — forces orchestration to know each provider's error shape, defeating the abstraction).

**Status.** Active for new code. Legacy `auto_short.py` provider calls still raise; migration will convert them.

---

### ADR-003: Timeline as an explicit intermediate representation

**Decision.** Videos are constructed from a `Timeline` — a declarative representation of what plays when — rather than being built imperatively by iterating segments.

**Rationale.** Direct segment-by-segment rendering makes cross-cutting features (variable pacing, non-linear transitions, per-word caption highlights, cross-fades) either impossible or bolted-on with special-case logic. An explicit timeline makes these features straightforward: they are transformations on the timeline before rendering. It also makes rendering a pure function — the same timeline always renders to the same bytes, which is a testability and cache-reuse win.

**Alternatives considered.** Direct imperative construction (current state, rejected for future work because of the extensibility problems above). MoviePy-style scene graphs (rejected because they couple the timeline representation to a specific renderer; the timeline should outlast any single renderer).

**Status.** Active for new code. Timeline representation is emitted by the legacy Shorts pipeline and passed into the renderer contract. The FFmpeg implementation still uses legacy render helpers internally for backward compatibility.

---

### ADR-004: Incremental (Strangler) migration from monolith to layers

**Decision.** The monolithic `auto_short.py` is not rewritten in place. Instead, the target layered architecture is introduced alongside, and functionality is migrated piece by piece behind reversible flags.

**Rationale.** A big-bang rewrite of a 2,400-line production module carries unacceptable risk to daily publishing. Strangler migration keeps the pipeline shipping videos while the architecture matures. Every migration PR is reversible; if a new implementation regresses, its flag is disabled and the legacy path resumes.

**Alternatives considered.** Big-bang rewrite (rejected — halts production during the rewrite, and the rewrite is unlikely to reach parity on the first attempt). Freeze legacy and build entirely new pipeline in parallel (rejected — doubles maintenance burden until parity is reached).

**Status.** Active. This is the governing migration strategy.

---

### ADR-005: Provider-agnostic design

**Decision.** No architectural component depends on a specific vendor. Every external service is behind an interface. The orchestrator selects providers from a configured preference list at runtime.

**Rationale.** Vendors change. APIs deprecate. Prices rise. Free tiers vanish. A platform that depends on any specific vendor inherits every risk that vendor carries. Making providers interchangeable at the configuration level insulates the platform from vendor risk and lets the operator pick the best current option without code changes.

**Alternatives considered.** Best-of-breed lock-in (rejected — trades short-term simplicity for long-term dependency risk). Provider-specific pipelines per vendor (rejected — combinatorial explosion as the platform supports more formats and more channels).

**Status.** Active for the design. Legacy `auto_short.py` currently has vendor branch logic inline; migration will replace it with the registry-based selection described above.

---

### ADR-006: Render profiles as data, not code

**Decision.** Each supported format (vertical Shorts, horizontal educational, long-form documentary, podcast) is described by a **render profile** — a data structure of settings that fully describes how the format is produced. Profiles are declarative; the domain code that consumes them is format-agnostic.

**Rationale.** The platform's long-term scope covers many formats. Encoding each format as code paths would explode the codebase and force every format to be tested against every code change. Profiles as data make format additions cheap and reduce the blast radius of code changes.

**Alternatives considered.** Per-format subclasses of a `Renderer` base class (rejected — inheritance couples format to renderer implementation; profiles are more flexible). Global constants toggled by flags (rejected — this is what the current codebase does, and it does not scale).

**Status.** Active in initial form. Renderer profiles exist for development, production, and testing while preserving current Shorts defaults. Fully declarative format profiles remain scheduled for the next migration step.

---

### ADR-007: Typed domain models

**Decision.** Artifacts that flow between stages are typed — `Script`, `VoiceTrack`, `MediaAsset`, `Timeline`, `MasteredVideo`, `PublishResult`, etc. Dictionaries are permitted at layer boundaries (e.g., serialized JSON in and out of storage) but not inside domain or pipeline code.

**Rationale.** Dictionary-of-strings passing between stages hides shape drift, produces cryptic key-error tracebacks, and makes refactoring expensive. Typed models catch shape mismatches at construction time and give AI agents a machine-readable spec of what each stage expects.

**Alternatives considered.** Untyped dictionaries throughout (current state, rejected because of the drift problems). Runtime schema validation (Pydantic) at every stage boundary (accepted — models will use Pydantic or dataclasses with validators).

**Status.** Future. Current codebase passes dictionaries. Typed models are the target.

---

### ADR-008: Backward compatibility as a hard constraint

**Decision.** No change to a public identifier, CLI flag, environment variable, output path, or configuration key breaks existing deployments. Renames get shims. Deprecations get a warning window. Removals happen only at major version boundaries.

**Rationale.** The platform runs unattended on a schedule. Operator deployments include Windows Task Scheduler entries, cron jobs, GitHub Actions workflows, and shell scripts — many of which reference identifiers the codebase might otherwise be tempted to rename. Silent breakage of these external artifacts costs the operator far more than the codebase saves by "cleaning up."

**Alternatives considered.** Semantic versioning with cheap breaking changes at every minor bump (rejected — imposes upgrade friction on a single-operator platform). Deprecation-free evolution (rejected — locks in mistakes forever).

**Status.** Active.

---

### ADR-009: Result types over exceptions for expected failures

**Decision.** Provider methods and stage boundaries return discriminated result types for expected failures. Exceptions are reserved for unexpected failures (bugs, corrupted state, unsatisfied invariants).

**Rationale.** Fallback orchestration requires cheap pattern-matching on failure kinds. Exceptions couple "what went wrong" to "how the caller unwinds," which is the wrong coupling for multi-provider fallback. Returning values also makes tests easier to write.

**Alternatives considered.** Exception hierarchy with `RateLimitedError`, `AuthError`, etc. (rejected — still uses control-flow exceptions, still fragile in fallback loops). Tuple returns `(ok: bool, payload_or_error: Any)` (rejected — unstructured and untyped).

**Status.** Active for new code.

---

### ADR-010: Content-addressed artifacts

**Decision.** Every artifact is identified by a hash of its inputs. The same script prompt with the same seed produces the same script artifact ID. Timestamps and run IDs are metadata, not identifiers.

**Rationale.** Content addressing gives us cache reuse, deduplication, resumability, and lineage tracking for free. A pipeline that re-runs the script stage with the same inputs finds the existing artifact and skips the work.

**Alternatives considered.** Timestamp-based names (rejected — no dedup, no cache reuse). UUID-based names (rejected — every run produces new artifacts even for identical inputs).

**Status.** Future for the storage layer as a whole. Currently, some cache reuse happens ad-hoc (`last_script.json`, `used_videos.json`), but not via a unified content-addressed store.

---

### ADR-011: Configuration precedence with safe defaults

**Decision.** Configuration is layered with five levels of precedence: CLI flags > environment variables > per-run files > per-channel files > platform defaults. Defaults are conservative; opting into risk is explicit.

**Rationale.** Operators want to override individual values without maintaining full configuration copies. Layered configuration with clear precedence gives them that. Safe defaults prevent a fresh install from doing something surprising.

**Alternatives considered.** Single configuration file (rejected — no per-run overrides). Flat environment variable soup (rejected — no structured composition).

**Status.** Active for the design. Current codebase reads a mix of `.env` and hard-coded constants; migration will introduce the layered loader.

---

### ADR-012: Long-form pipeline as a separate variant of the same platform

**Decision.** The long-form pipeline (`bias_long.py` and friends) is treated as a separate format variant — same underlying platform, different render profile, different scheduler, different prompt templates. It does not fork the codebase.

**Rationale.** Long-form and Shorts share ~80% of the pipeline: script generation, voice synthesis, media selection, mastering, publishing. Only rendering, pacing, and format metadata differ meaningfully. Duplicating the pipeline for long-form would double the maintenance burden without corresponding benefit.

**Alternatives considered.** Separate codebase (rejected — code duplication for shared concerns). Single format only (rejected — long-form is a legitimate target of the platform's vision).

**Status.** Deferred. Current codebase has `bias_long.py` as a parallel module; the migration will fold it into the format-profile mechanism once profiles are implemented.

---

*This document is maintained with the same discipline as the code. When a boundary changes, an ADR is added or updated in the same pull request. When a new architectural decision is made, an ADR is added below the last one — old ADRs are marked `Superseded` rather than edited in place.*
