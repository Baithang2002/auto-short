# Roadmap

This document describes the remaining product work. Completed implementation history
lives in `CHANGELOG.md`; architecture boundaries live in `ARCHITECTURE.md`.

---

## Current Status

| | |
|---|---|
| **Current phase** | Era I - Autonomous YouTube Shorts reliability |
| **Operating model** | Single channel, single format, GitHub Actions scheduled publishing |
| **Maturity** | Production-running with strict quality gates; final reliability validation in progress |
| **Immediate focus** | Autonomous topic supply and scheduled reliability burn-in |

The system can select a viable topic, generate a script and narration, retrieve and
verify media, render a Short, apply music/captions/metadata, and upload through the
YouTube Data API. Failed candidate topics are intended to recover to another viable
topic rather than publish poor content.

The current standard is **correctness before publication**: a run may defer when it
cannot demonstrate adequate authentic-media coverage. It must not weaken those gates
merely to create a video.

## Completed Milestones

### Architecture Foundation

| Identifier | Scope | Status |
|---|---|---|
| PR #1 | Foundation layer | Complete |
| PR #2 | Filesystem storage, metadata, and artifact boundaries | Complete |
| PR #3 | Provider and configuration abstraction | Complete |
| PR #4 | Typed domain-model integration | Complete |
| PR #5 | Timeline intermediate representation | Complete |
| PR #6 | Renderer contract and FFmpeg adapter | Complete |
| PR #7 | Deterministic media-selection engine | Complete |
| PR #8 | Content intelligence and capability-driven source planning | Complete |
| PR #9 | Provider expansion and provider capability registry | Complete |
| PR #9.5 / #9.6 | Portrait safety, relevance, and domain-safe media acceptance | Complete |
| PR #10 | Declarative format/render profiles | Complete |
| PR #11 | Resumable pipeline orchestration and stage artifacts | Complete |

### Documentary Quality and Editorial Reliability

| Identifier | Scope | Status |
|---|---|---|
| PR #12 | Visual Director, ShotPlan, and knowledge-pack planning | Complete |
| PR #13 | Archive/image source coverage expansion | Complete |
| PR #14 / #14.1 | Hybrid Visual Composer and visual grammar policy | Complete |
| PR #15 | Authentic-media-first arbitration | Complete |
| PR #16 | Subject Continuity Engine | Complete |
| PR #17 / #17.1 | Editorial Canon, primary-subject lock, scene entities, and query isolation | Complete |
| PR #18 | Evidence Verification with optional selective AI vision | Complete |
| PR #19 | Engagement and immersive audio planning | Complete |
| Reliability extensions | Documentary viability, source coverage, semantic queries, scene constraints, canonical entities, verified-media gate, publish-quality gate, and scheduler recovery | Complete |
| Editorial Identity hotfix | Rejects topic/domain/subject drift before source coverage | Complete |
| Energy/physics capability-routing hotfix | Routes terrestrial solar/renewable-energy scenes to stock providers while retaining NASA-first astronomy routing | Implemented; awaiting real-provider validation |
| Autonomous topic supply | Large nature-safe topic bank, coverage-proven priority, category rotation, and exact-repeat blocking | Complete |

## Current Work

### Era I Reliability Burn-In

- **Objective.** Prove that GitHub Actions can publish fresh, correct Shorts without daily operator intervention.
- **Validation.** Monitor scheduled runs using the topic bank, `scheduler_report.json`, `source_coverage_report.json`, `verified_media_report.json`, and `publish_quality_report.json`.
- **Success criteria.** Runs choose non-repeated, provider-friendly topics; recover from weak candidates; upload only when quality gates pass; and leave complete diagnostics when they defer.
- **Non-goal.** Do not weaken source-coverage thresholds, media verification, rendered QA, or publish-quality gates just to force daily output.

## Next Milestones

Ordered by dependency and impact.

### 1. Video-Level Semantic QA and Retry Policy

- **Objective.** Verify representative rendered frames against each scene's canonical entity and required constraints before upload.
- **Why.** Candidate-level evidence verification cannot prove that the final crop, clip portion, or composition shown to the viewer matches narration.
- **Outcome.** A scene that fails visual fidelity is retried with the next eligible candidate or causes a clean deferral; it is not uploaded silently.
- **Dependencies.** Stable capability routing and existing evidence-verification diagnostics.

### 2. Era I Reliability Burn-In

- **Objective.** Prove unattended operation over scheduled production runs.
- **Success criteria.** Thirty scheduled runs with durable artifacts, topic recovery where appropriate, successful upload behavior, no identity leakage in sampled audits, and no unhandled provider failure preventing later runs.
- **Outcome.** Establish the Shorts v1 production baseline.

### 3. Analytics Feedback Loop

- **Objective.** Use post-publication performance as a ranking signal among already viable, covered, and verified topics.
- **Constraint.** Analytics may not override documentary viability, source coverage, evidence verification, or publish-quality gates.

## Future Eras

### Era II - Multi-Format Production

- Horizontal educational Shorts.
- Long-form documentaries with chapters, structured research, show notes, and long-duration safety controls.
- Podcast-video profiles.

### Era III - Multi-Channel Platform

- Per-channel branding, voice, topic policy, provider preferences, and analytics.
- Shared provider budgets, footage libraries, and music licensing records.

### Era IV - Autonomous Operator

- Strategy-level topic selection informed by analytics, retention, engagement, and revenue.
- Configurable business goals and channel guardrails.

## Deferred Work

- Object storage until multi-machine deployment requires it.
- Instagram and Facebook upload APIs until their account/app requirements justify implementation.
- Browser automation for YouTube Studio actions such as comment pinning; the YouTube upload API remains preferred.
- YouTube Audio Library pre-staging and Content-ID hardening.
- Live streaming, real-time transcription, enterprise multi-tenancy, and a manual video-editing UI.

## Definition of Era I Complete

Era I is complete only when the pipeline reliably creates and publishes correct
Shorts without daily operator intervention. The required evidence is the completed
video-level QA policy plus the reliability burn-in, not simply the existence of more
architecture or providers.
