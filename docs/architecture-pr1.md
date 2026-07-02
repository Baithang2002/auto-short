# Architecture PR #1: Foundation Layer

This document records the first incremental architecture step for the AI video
production platform.

## Scope

PR #1 is intentionally additive. Existing runtime behavior remains unchanged.

Included:

- `src/autovideo` package skeleton
- typed domain models
- provider interfaces
- filesystem-backed storage abstractions
- configuration modules
- unit tests for the foundation

Not included:

- no feature changes
- no SQLite
- no provider implementation migration
- no renderer migration
- no changes to existing CLI behavior

## Compatibility Rule

Legacy scripts remain authoritative during this phase:

- `auto_short.py`
- `bias_long.py`
- `pipeline.py`
- `pipeline_daily.py`
- `uploader.py`
- `video_queue.py`

New modules are not wired into runtime yet. They define the contracts that
future pull requests will gradually adopt.

## Package Responsibilities

```text
src/autovideo/
  config/       settings, channel profiles, provider registry
  domain/       typed business objects
  providers/    provider interfaces only
  storage/      filesystem queue and metadata abstractions
  intelligence/ future creative reasoning layer
  planning/     future outline/script/storyboard modules
  media/        future media helpers
  render/       future timeline renderer
  workflows/    future workflow orchestration
  legacy/       future adapters around existing scripts
  cli/          future package CLI entrypoints
```

## Storage Decision

The project continues using the current filesystem queue:

```text
videos/pending/
videos/approved/
videos/rejected/
videos/uploaded/
```

Each video keeps `metadata.json`.

The new storage abstraction wraps that layout so future business logic does not
depend directly on paths. A database can be introduced later only if concurrent
jobs, remote review, or multi-user workflows require it.

## Provider Decision

Providers are protocols, not implementations, in this PR.

Current external services remain in legacy scripts until they are migrated
behind interfaces:

- Gemini / SambaNova / Groq / OpenAI
- Edge-TTS / Speechify
- Pexels / Pixabay / NASA / local media
- Pixabay Music / Jamendo / local music
- YouTube Data API / browser upload

## Domain Models

Initial models:

- `VideoProject`
- `Episode`
- `Scene`
- `VisualPlan`
- `AudioPlan`
- `CaptionPlan`
- `RetentionPlan`
- `Asset`
- `Timeline`
- `TimelineItem`
- `UploadMetadata`

The models include dictionary conversion only where needed to preserve current
JSON metadata compatibility.

## Testing

PR #1 tests:

- domain model round-trips
- upload metadata legacy-field preservation
- provider registry priority behavior
- filesystem queue list/move behavior
- metadata validation

Run:

```bash
python -m unittest discover -s tests
```

## Migration Notes

Future PRs should move one boundary at a time:

1. Extract one provider implementation behind a protocol.
2. Add tests.
3. Keep the old script calling path working.
4. Avoid moving renderer and provider logic in the same PR.

Large refactors remain prohibited.