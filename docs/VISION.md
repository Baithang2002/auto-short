# Vision

## Mission

Build a modular, provider-agnostic AI Video Production Platform that lets a single operator publish high-quality, correctly-formatted video content across multiple formats and platforms — reliably, automatically, and with transparent, cost-efficient operation.

The platform's role is to remove every step of video production that a machine can do at parity with (or better than) a human: research, scripting, voice generation, footage selection, editing, mastering, publishing, and distribution follow-up. Human involvement is reserved for the parts machines are still weak at — creative direction, taste, moderation, and strategy.

## What We Are Building

The platform is a content pipeline that turns an intent — a topic, a niche, a target format — into a finished, published, discoverable video. Its long-term scope covers:

| Format | Length | Distribution primary |
|---|---|---|
| Vertical Shorts | 30–60 s | YouTube Shorts, Instagram Reels, TikTok, Facebook Reels |
| Educational shorts | 60–120 s | YouTube, TikTok, LinkedIn |
| Long-form documentaries | 8–20 min | YouTube |
| Podcast videos | 20–90 min | YouTube, Spotify Video |
| Storytelling narratives | 3–10 min | YouTube, Instagram |
| Explainer / tutorial | 3–15 min | YouTube, embedded on external sites |

Each format shares the same underlying platform. The differences are captured as configuration and render profiles rather than as separate codebases.

The platform is deliberately **provider-agnostic**. No component is bound to a single vendor:

- **Script generation**: multiple LLM providers behind a common interface, ranked by capability and cost per request.
- **Voice synthesis**: multiple TTS providers behind a common interface, ranked by quality per second of audio.
- **Stock media**: multiple footage/image libraries queried in parallel, results merged and ranked.
- **Music**: multiple music libraries plus a fallback synthesizer.
- **Distribution**: multiple platform uploaders behind a common interface — official APIs preferred over browser automation.
- **Storage**: local filesystem, object storage, and content-addressable stores are interchangeable.

Provider selection is a configuration decision, not a code change. Adding a new provider means implementing an interface, not modifying business logic.

## Design Philosophy

The following values are load-bearing. They guide every technical decision.

**Correctness before speed.** A pipeline that publishes the wrong content, the wrong length, or on the wrong platform is worse than a pipeline that publishes nothing. Every stage validates its own output before passing it downstream.

**Reversibility over cleverness.** Every artifact — script, voiceover, footage selection, cut, master, upload — is a discrete, inspectable, replayable step. Any output can be regenerated from earlier stages without re-running the whole pipeline. State moves forward; nothing is irretrievably clobbered.

**Provider substitution without code change.** Providers are treated as commodity resources. When a provider raises prices, hits quotas, degrades in quality, or disappears entirely, the platform routes around it via configuration. No provider is a critical dependency.

**Automation with a review escape hatch.** The default mode is fully automated. But every stage supports pause, inspection, override, and resume. The platform never forces the operator into "all or nothing."

**Backward compatibility is a hard constraint.** Existing pipelines must continue to work through every migration. New capabilities are added alongside, not on top of, existing ones. An old configuration file must still produce the same result years later.

**Cost transparency.** Every operation's cost — API calls, storage, compute — is measurable and reportable. The operator always knows the marginal cost of the next video.

**Failure isolation.** A failure in one video, one platform, or one provider must not cascade to other videos, platforms, or providers. Each production run is independent.

**Deterministic where possible, probabilistic where necessary.** Rendering, editing, mastering, and publishing are deterministic. Only content generation (scripts, image selection) is probabilistic, and even those steps are seedable for reproducibility.

## Long-term Roadmap

The platform matures across four eras. Each era builds on the last without invalidating it.

### Era I — Vertical Shorts Platform (current)

The platform reliably produces vertical short-form video for a single channel, publishing daily on schedule, without human involvement between topics and uploads. The operator's role is limited to defining topics and reviewing analytics.

Success at this era is measured by:

- Videos published on schedule without operator intervention.
- Zero-touch continuity when any single provider fails.
- Content quality sufficient for a niche channel to grow to first monetization thresholds.

### Era II — Multi-Format Platform

The same underlying pipeline supports vertical Shorts, horizontal educational shorts, and long-form documentary content. Format selection is configuration. Rendering profiles, caption styles, pacing rules, and platform metadata are all format-scoped.

Success at this era is measured by:

- One operator can run channels in multiple formats without maintaining separate codebases.
- New formats can be added without changes to the core pipeline.
- Configuration files, not code, distinguish one channel from another.

### Era III — Multi-Channel Platform

The platform manages multiple channels concurrently under a shared operator, with per-channel identity, branding, niches, and analytics. Cross-channel resource sharing (music libraries, footage, brand assets) is automatic. Channel-level performance data feeds back into topic selection and pacing.

Success at this era is measured by:

- N channels operate independently under one deployment.
- Per-channel branding and voice are consistent without manual maintenance.
- Analytics from any channel can inform decisions on any other.

### Era IV — Autonomous Operator

The platform selects its own topics, monitors its own performance, and adjusts its own strategy based on measured outcomes. The operator's role narrows to setting business goals, defining brand guardrails, and approving strategic pivots. Day-to-day content decisions are automated based on retention, engagement, growth, and revenue signals.

Success at this era is measured by:

- Topics are chosen by the platform based on measured audience demand.
- Format, length, and cadence adapt to what the audience rewards.
- Revenue attribution feeds back into content strategy without operator intervention.

The gap between eras is intentional and long. The platform does not attempt an era until the previous one is unambiguously reliable.

## Project Principles

These are non-negotiable rules that survive across eras.

**One-way doors get discussed. Two-way doors get shipped.** Reversible decisions are made quickly and cheaply. Irreversible decisions — data schemas, provider API contracts, published channel identities — require deliberation.

**Boring technology by default.** New dependencies are added only when the existing stack cannot solve the problem. Preference is for standard, widely-maintained, mature libraries. Fashionable frameworks are declined.

**Every failure is a bug or a lesson.** Postmortems are lightweight but consistent. Every production incident results in either a code change (bug fix), a documentation change (missing knowledge), a process change (organizational fix), or a deliberate accepted risk (documented in this repository).

**No secrets in code.** Credentials, API keys, tokens, and identities live only in secret managers or environment variables. Any file committed to the repository is treated as public.

**Documentation is code.** This file, and every document in `docs/`, is maintained with the same discipline as the code. Documentation drift is a bug. Contributors update documentation as part of the same change that alters behavior.

**Cost consciousness.** The platform must be operable by a single individual on a hobbyist budget. Features that only work with expensive APIs are optional add-ons, not core dependencies. The free-tier path must always exist.

**Human override is always available.** Automation should reduce repetitive work, not remove operator control. Every automated decision — provider selection, topic, script, footage, cut, upload target — must be overridable without modifying the codebase. Configuration files, review queues, and per-run flags are the standard override mechanisms.

**Testing where it matters.** Not everything needs test coverage. Public interfaces, critical failure modes, and business-rule enforcement do. Rendering aesthetics and content quality are validated by human review, not by machines.

## What Success Looks Like

At each level of maturity, success is measurable and specific.

**As a codebase**, success means:

- A new contributor — human or AI agent — can locate any component in under five minutes.
- A new provider (LLM, TTS, stock, platform) can be added in under one working day.
- A new video format can be added without modifying existing formats.
- The pipeline runs end-to-end in an unattended environment with only credentials as input.
- Every failed run leaves enough state to diagnose the failure without re-running.
- A failed job can be resumed from the last successful stage rather than restarting the entire pipeline.

**As a product**, success means:

- One operator produces content that a full-time creator would spend 20+ hours a week on.
- Cost per published video approaches the cost of the API calls, with no fixed overhead.
- Publishing continues through provider outages, API changes, and platform UI updates.
- The operator's time is spent on strategy, not on production.

**As a platform**, success means:

- Multiple channels of different formats operate from one deployment without contention.
- Onboarding a new channel is a configuration exercise, not a development exercise.
- The platform's monthly cost scales with usage, not with channel count.
- Contributor knowledge transfers cleanly between channels.

Success at every level is characterized by **absence of drama**. A mature platform is quiet: renders complete, uploads succeed, videos publish, and the operator's inbox stays empty of incident reports. The system's most valuable state is being unnoticeable.

## Out of Scope

To keep the vision honest, the following are explicitly not goals of this platform:

- Live streaming, real-time transcription, or interactive video.
- Video editing UI for humans. The platform is a pipeline, not an NLE.
- User-generated content moderation at scale. The platform serves a small number of operators.
- Enterprise multi-tenancy. Deployments are single-operator or small-team.
- Full replacement of creative human judgment. The platform amplifies operator taste; it does not substitute for it.

Any feature or provider whose value is not clearly aligned with the mission above is deferred. The scope stays small so that the mission stays clear.
