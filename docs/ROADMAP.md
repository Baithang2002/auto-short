# Roadmap

This document answers one question: **What are we building next?**

It does not repeat vision, architecture, or engineering rules — those live in `VISION.md`, `ARCHITECTURE.md`, and `ENGINEERING_GUIDE.md`. It does not record history — that lives in `CHANGELOG.md`. This document is forward-looking and short by design.

---

## Current Status

| | |
|---|---|
| **Current version** | v0.1.0 — Initial Production Baseline |
| **Current phase** | Phase 1 — Shorts Platform (Era I, per `VISION.md`) |
| **Overall maturity** | Production-running, single-format, single-channel, single-operator |

The daily Shorts pipeline is operational. Videos publish on schedule via GitHub Actions using the YouTube Data API. No operator intervention is required between topic definition and upload confirmation.

The layered architecture is defined and its foundational milestones are complete. Physical migration of legacy monolithic modules into the target modular architecture is the current focus.

---

## Completed Milestones

| Identifier | Scope | Ship version |
|---|---|---|
| PR #1 | Foundation Layer — cross-cutting utilities | v0.1.0 |
| PR #2 | Storage Abstraction — artifact persistence + metadata | v0.1.0 |
| PR #3 | Provider & Configuration Layer — fallback chains + `.env` loader | v0.1.0 |
| PR #4 | Domain Integration — typed models wired into legacy stages | v0.2.0 |
| PR #5 | Timeline Builder — Timeline IR emitted before rendering | v0.3.0 |
| PR #6 | Renderer Contract — Timeline-based renderer interface + FFmpeg implementation | v0.3.0 |
| PR #7 | Intelligent Media Selection — deterministic B-roll candidate scoring before Timeline construction | v0.4.0 |
| PR #8 | Content Intelligence & Source Planning — capability-driven query/source planning before media selection | v0.5.0 |
| PR #9 | Provider Expansion / Registry Formalization — scene-type routing and optional provider registration | v0.6.0 |
| Documentation baseline | VISION · ENGINEERING_GUIDE · ARCHITECTURE · CHANGELOG · ROADMAP | v0.1.0 |
| Serverless YouTube uploads | YouTube Data API path + OAuth refresh flow | v0.1.0 |
| Daily automation | GitHub Actions schedule with off-peak cron + dedup guard | v0.1.0 |

**Implementation Status** (mirrors `docs/ARCHITECTURE.md § Implementation Status`):

- ✅ Foundation
- ✅ Storage
- ✅ Providers
- ✅ Domain Integration
- ✅ Timeline Builder
- ✅ Renderer (new contract)
- ✅ Media Selection
- ✅ Source Planning
- ✅ Provider Expansion
- 🚧 Pipeline (orchestrator + stages)
- 🚧 Interface (CLIs, workflows)

---

## Current Focus

**PR #10 — Render Profiles.** Provider expansion is now behind the capability registry. The next boundary is moving Shorts format behavior into declarative render profiles without changing output behavior.

This depends on PR #6 and should not change provider, storage, upload, queue, or metadata behavior.

---

## Next Milestones

Ordered by dependency, not by date. Each milestone is a coherent boundary, not a single feature.

### PR #10 — Render Profiles

- **Objective.** Expand the initial renderer profiles into declarative format profiles (ADR-006).
- **Expected outcome.** Adding a new format (educational horizontal, long-form documentary, podcast video) requires only a new profile — no code changes to pipeline, renderer, or domain layers.
- **Dependencies.** PR #6 (renderer consumes profile settings).
- **Success criteria.** All current Shorts behavior is expressed as `shorts_vertical.yaml`. A second profile (`educational_horizontal.yaml`) is added as proof-of-concept and can render an example video without changes elsewhere.

### PR #11 — Pipeline Orchestrator with Resume Points

- **Objective.** Introduce the resume-from-stage capability promised in `VISION.md` and `ARCHITECTURE.md § Resume Points`.
- **Expected outcome.** A failed pipeline run can be resumed from the last successful stage using persisted artifact IDs.
- **Dependencies.** PR #4 (typed models with content addresses), PR #5 (timeline as an inspectable artifact), PR #10 (profiles as data).
- **Success criteria.** `--from-stage <name>` and `--only-stage <name>` flags work as documented. A failed publish stage does not require re-rendering.

---

## Future Phases

Work beyond the next milestones is grouped into phases. Each phase is a coherent product step, not a sprint.

**Phase 1 — Shorts Platform** *(current, PRs #1 – #8 shipped)*
The vertical Shorts pipeline works reliably end-to-end, unattended, on a daily schedule. Layered architecture is defined and its foundations are in place.

**Phase 2 — Timeline Engine** *(PRs #4 – #7 complete)*
Typed domain models, explicit timeline representation, formal renderer contract, and deterministic pre-timeline media selection.

**Phase 3 — Production Scaling** *(PRs #9 – #11 range)*
Provider expansion, declarative render profiles, resumable orchestration, quality-driven provider ranking based on measured output, feedback-informed prompt tuning, content quality scoring before publish.

**Phase 4 — Long-form Production**
Long-form documentary and podcast formats folded into the render-profile mechanism (retires the parallel `bias_long.py` variant per ADR-012). Longer-duration content-safety enforcement, chapter markers, structured show-notes.

**Phase 5 — Multi-channel Platform**
Multiple channels under a single deployment. Per-channel branding, per-channel provider preferences, per-channel analytics. Cross-channel resource sharing for footage and music libraries.

---

## Version Roadmap

Approximate semver progression. Milestone descriptions replace dates.

| Version | Milestone description |
|---|---|
| v0.1.0 | Initial Production Baseline. Daily Shorts publishing works. Foundation, Storage, and Providers established. Documentation baseline shipped. |
| v0.2.0 | Domain Integration complete (PR #4). Typed models used across all stage boundaries. |
| v0.3.0 | Timeline Builder + new Renderer contract (PRs #5 – #6). Renderer is a pure function. |
| v0.4.0 | Intelligent Media Selection complete (PR #7). B-roll candidates are scored deterministically before Timeline construction. |
| v0.5.0 | Content Intelligence & Source Planning complete (PR #8). Scenes route by visual capability before media selection. |
| v0.6.0 | Provider Expansion / Registry Formalization (PR #9). Providers register capabilities; preferences are configuration. |
| v0.7.0 | Render Profiles introduced (PR #10). Multiple formats supported declaratively. |
| v0.8.0 | Resumable Pipeline Orchestrator (PR #11). Any stage can restart from persisted upstream artifacts. |
| v0.9.0 | Multi-channel deployment. Per-channel configuration and analytics. |
| v0.10.0 | Analytics feedback loop. Topic selection informed by measured audience data. |
| v1.0.0 | Production-ready platform. Full backward compatibility guarantee begins. All Era II success criteria met. |

Versions between v0.1.0 and v1.0.0 may release non-consecutively as scope shifts. Skipped versions are recorded in `CHANGELOG.md` with the reason.

---

## Deferred Work

The following are intentionally postponed. Contributors should not preemptively implement them; wait for a scheduled migration PR that lifts the deferral.

- **Object storage backend.** Content-addressed persistence in S3-compatible storage. Deferred per ADR-001 until multi-machine deployment requires it.
- **Instagram Graph API upload.** Requires Business account and Meta app review. Deferred until channel monetization justifies the setup effort.
- **Facebook Graph API upload.** Same rationale as above.
- **YouTube Audio Library pre-staging** ("Phase E" internally). Optional Content-ID hardening. Deferred while `YT_MUSIC=true` remains an acceptable trade-off.
- **Full replacement of browser-based uploaders.** Playwright fallbacks continue to exist for platforms without usable APIs. Retired only when API paths are proven for those platforms.
- **Live streaming, interactive video, real-time transcription.** Out of scope per `VISION.md § Out of Scope`.
- **Multi-tenant enterprise deployment.** Out of scope per `VISION.md § Out of Scope`.

Deferred items are not the same as **cancelled** items. A deferred item may become active in a later phase; a cancelled item is removed from the roadmap and does not return.

---

## Roadmap Maintenance

This document is a **living** document — the only one in `docs/` that is expected to change frequently.

**When a milestone completes:**

1. Move it from *Next Milestones* into the appropriate row of *Completed Milestones*.
2. Update *Current Focus* to point at the next in-flight milestone.
3. Update *Implementation Status* if a component changed status.
4. Update `ARCHITECTURE.md § Implementation Status` in the same pull request.

**When a new milestone is scoped:**

1. Append it to *Next Milestones* with objective, outcome, dependencies, and success criteria.
2. If it introduces a new architectural decision, add or supersede the corresponding ADR in `ARCHITECTURE.md`.
3. Reserve its version slot in the *Version Roadmap*.

**When priorities shift:**

1. Reorder *Next Milestones* by dependency, not by preference.
2. Move de-prioritized work into *Deferred Work* with a one-line explanation.
3. Never delete an entry — deferrals and cancellations are part of the record.

**When a phase completes:**

1. Confirm every milestone in the phase has shipped.
2. Update *Current Status* to point at the next phase.
3. Refresh the *Version Roadmap* if versions moved.

Roadmap entries should stay short. If a milestone description grows beyond a few lines, its detail belongs in the pull request description, in `ARCHITECTURE.md`, or in a new ADR — not here.
