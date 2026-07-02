# Engineering Guide

This document is the engineering constitution of the platform. It defines the rules every contributor — human or AI agent — must follow when changing this codebase. It does not describe *what* to build; that belongs in `VISION.md` and `ARCHITECTURE.md`. It describes *how* to build.

When in doubt, this document is the tiebreaker. When this document is silent, defer to the most restrictive rule in the same neighborhood.

## Table of Contents

1. [Coding Standards](#coding-standards)
2. [Architecture Principles](#architecture-principles)
3. [Dependency Rules](#dependency-rules)
4. [SOLID Principles in this Codebase](#solid-principles-in-this-codebase)
5. [Migration Strategy](#migration-strategy)
6. [Backward Compatibility](#backward-compatibility)
7. [Provider Abstraction Rules](#provider-abstraction-rules)
8. [Storage Rules](#storage-rules)
9. [Configuration Rules](#configuration-rules)
10. [Render Profile Rules](#render-profile-rules)
11. [Logging Philosophy](#logging-philosophy)
12. [Error Handling Philosophy](#error-handling-philosophy)
13. [Testing Strategy](#testing-strategy)
14. [Performance Philosophy](#performance-philosophy)
15. [Pull Request Workflow](#pull-request-workflow)
16. [AI Contributor Guidelines](#ai-contributor-guidelines)
17. [Definition of Done](#definition-of-done)

---

## Coding Standards

**Language.** Python 3.11 is the reference interpreter. Code must run on 3.11 through 3.14. Features unavailable in 3.11 are not used unless polyfilled and documented.

**Formatting.** Standard PEP 8 with these overrides:

- Line length: 100 characters, not 79.
- Two blank lines between top-level definitions; one between nested definitions.
- String literals use double quotes by default; single quotes only inside f-strings or when double quotes would require escaping.
- Trailing commas in multi-line collection literals and call signatures.
- No aligned assignments (`x    = 1; longer = 2`). Vertical alignment ages badly.

**Type hints.** All new public functions and methods carry type hints. Private helpers may omit them if the types are obvious from the parameter names. Return types are always present. `Any` is a code smell — use it only at genuine any-serializable boundaries (JSON in/out).

**Naming.**

- Modules: `lowercase_snake_case.py`
- Classes: `PascalCase`
- Functions and variables: `lowercase_snake_case`
- Constants: `UPPER_SNAKE_CASE` at module level
- Private helpers: leading underscore. Two leading underscores are reserved for name-mangling and should almost never be used.

**Docstrings.** Every public module, class, and function has a docstring. Format: one-line summary, blank line, extended description (optional), then `Args:`, `Returns:`, `Raises:` sections as needed. One-liners are fine for obvious helpers.

**Imports.** Standard library first, then third-party, then local, with a blank line between groups. `from x import *` is forbidden. Aliased imports (`import numpy as np`) are permitted for well-established conventions only.

**No commented-out code.** Delete it. Git remembers.

## Architecture Principles

**Layered composition.** Every module belongs to exactly one architectural layer, and layers depend only on layers below them. From bottom to top:

1. **Foundation.** Cross-cutting utilities: paths, IDs, retries, logging, small pure functions. No business logic. Depends on nothing internal.
2. **Storage.** Content-addressable persistence, artifact lifecycle, blob I/O. Depends only on Foundation.
3. **Providers.** External-service adapters (LLMs, TTS, stock, music, upload targets) behind common interfaces. Depends on Foundation and Storage.
4. **Domain.** The video-production business rules: timeline, scenes, cuts, captions, mastering. Depends on Foundation, Storage, and Providers.
5. **Pipeline.** Orchestration: stages, DAGs, resume, scheduling. Depends on all lower layers.
6. **Interface.** Entry points — CLIs, HTTP handlers, workflow files. Depends on all lower layers.

An import from a lower layer to a higher layer is a bug.

**No circular dependencies, even between siblings in the same layer.** If two providers need to share code, that code moves down to a lower layer.

**Explicit over implicit.** Behavior triggered by side effects, monkey-patches, or import-time execution is prohibited. Modules import cheaply and do nothing until their functions are called.

**Composition over inheritance.** Inheritance is used only for interfaces (ABCs) and to model true is-a relationships. Extend behavior via composition and delegation.

**Small surfaces.** A module's public surface should be as small as its consumers need. `__all__` is set where the module is imported broadly. Private helpers start with `_`.

**No god objects.** A class that owns most of the state of a subsystem is a smell. If a class has more than ten public methods or ten instance attributes, it should be decomposed.

## Dependency Rules

**Standard library first.** If the standard library can do the job at acceptable cost, use it. Every added dependency is a security burden, a version compatibility burden, and a supply-chain surface.

**A new third-party dependency requires justification.** Additions to `requirements.txt` must be accompanied by a note (in the PR description) explaining what problem it solves and why no existing dependency covers it.

**Pin major versions only.** Use `>=X.Y` rather than `==X.Y.Z`. Exact pins are reserved for known-brittle libraries and are documented inline.

**Optional dependencies are optional.** If a feature depends on a heavy or paid package, that package must be optional. The module imports it lazily inside the function that uses it, and raises a clear error if it's missing.

**No native binary dependencies without a documented rationale.** Pure-Python packages preferred. Compiled dependencies (like `ffmpeg-python`) may be justified when they wrap a system tool the platform already requires.

**Vendor tools stay isolated.** Each external provider (a specific LLM, a specific TTS, a specific upload platform) is wrapped in its own module. Business logic never imports a provider SDK directly.

## SOLID Principles in this Codebase

**Single Responsibility.** Each module has one reason to change. A module that both fetches footage and encodes video violates SRP; split it.

**Open/Closed.** Adding a new provider must not require editing the code that selects between providers. Provider selection is registry-based; new providers register themselves.

**Liskov Substitution.** Every implementation of a provider interface behaves interchangeably. If two "script providers" have different failure modes, those differences are surfaced through common return types (an error result), not raised exceptions that only some callers handle.

**Interface Segregation.** Interfaces are narrow and role-specific. A footage provider that supports images and video does not force clients to import both if they only need one. Prefer several small ABCs over a large one.

**Dependency Inversion.** Domain code depends on provider interfaces, not on provider implementations. Wiring — which implementation is loaded — happens in configuration and at composition points, not inside business logic.

## Migration Strategy

The platform is under active migration from a monolithic single-format Shorts tool to the layered, provider-agnostic architecture described in `ARCHITECTURE.md`. The migration follows these non-negotiable rules:

**Incremental, not big-bang.** No pull request rewrites more than one subsystem at a time. A PR that touches Foundation, Storage, and Providers simultaneously is rejected regardless of correctness.

**Additive first, subtractive later.** New abstractions are introduced alongside existing code. The existing code path continues to work. Only after the new path is proven in production do we deprecate the old path. Only after a deprecation window do we delete it.

**Feature parity before switch.** A new layer or provider is not considered "ready" until it can pass the same production tests as the code it replaces. Missing edge-case handling is a blocker.

**Reversible switches.** Every migration step is guarded by a configuration flag or environment variable. Rolling back does not require a code deploy.

**Migration commits are labeled.** PR titles for migration work carry a `[migration]` tag. This lets future contributors and AI agents distinguish load-bearing migration work from ordinary feature work.

## Backward Compatibility

Backward compatibility is a **hard requirement**. Every existing pipeline configuration must continue to produce the same output, in the same format, at the same location, after every change. Contributors must assume that configurations, output paths, environment variable names, and CLI flags in current use are load-bearing for someone.

Rules:

- **Never rename a public identifier without a deprecation shim.** The old name aliases to the new one for at least one minor version. The shim carries a `DeprecationWarning` with a pointer to the replacement.
- **Never change a default without discussion.** A change to a default flag, a default provider, or a default output path is treated as a breaking change even if the API signature is unchanged.
- **Never change environment variable names.** If a rename is truly necessary, both names are accepted, and the old one is deprecated with a warning.
- **Never remove a CLI flag.** Silent removals break shell scripts, cron jobs, and CI workflows. Flags may become no-ops that print a deprecation notice.
- **Output artifacts stay at their historical paths.** If a new location is preferred, the artifact is written to both paths during the deprecation window.

Breaking changes are permitted only at explicit major version boundaries, and only when documented in `CHANGELOG.md` with a migration guide.

## Provider Abstraction Rules

Providers wrap external services (LLMs, TTS, stock libraries, music libraries, upload platforms, image generators, translation, etc.). All providers follow the same rules.

**One provider per module.** No module contains implementations for two different vendors.

**Interface-first.** A provider implements an abstract interface defined in the Domain layer. The interface exists before the first implementation. Interfaces are not "designed by the first implementation."

**No hidden state.** A provider's constructor takes explicit configuration (credentials, options, endpoints). It never reads environment variables directly; configuration handles that.

**Result types, not exceptions, for expected failures.** A provider returns a discriminated result — `Ok`, `NotAvailable`, `RateLimited`, `AuthError`, `TransientError` — for outcomes the caller must handle. Unexpected failures (bugs, malformed responses) may raise.

**Idempotence where possible.** If a provider's operation is repeatable (e.g., "generate a script for this prompt"), retries produce equivalent results. Where operations are inherently non-idempotent (e.g., "publish this video"), providers expose a `dry_run` mode.

**Rate-limit awareness.** Providers respect their own rate limits and report `RateLimited` cleanly with a suggested retry-after. Callers do not implement per-provider backoff logic.

**No cross-provider fallback in the provider layer.** Fallback is orchestration, not a provider concern. A "primary + backup" strategy lives in the pipeline layer, not inside any single provider.

**Health checks are cheap.** Every provider offers a `check()` method that verifies credentials and reachability without consuming meaningful quota.

## Storage Rules

Storage is content-addressable. The same input always maps to the same output location, regardless of when it was generated.

**Artifacts are named by content, not by time.** Filenames or object keys derive from a hash of the input parameters or the content itself. Timestamps and UUIDs live in metadata, not in identifiers.

**Writes are atomic.** A file is either fully written or absent. Partial writes are prevented by writing to a temporary path and renaming on completion. Cloud storage uses the equivalent (single-part uploads for small files, completed multipart uploads for large).

**Reads are cheap and repeatable.** Reading the same artifact twice returns the same bytes. No provider is allowed to mutate previously-written artifacts.

**Deletes are explicit and rare.** No component deletes artifacts as a side effect of another operation. Cleanup is a separate, opt-in operation. Retention policies are configuration.

**Local, cloud, and in-memory backends are interchangeable.** The storage interface hides the backend. Business logic never calls `pathlib.Path` or `boto3.client` directly.

**Metadata rides alongside content.** For each artifact, a companion metadata document records origin, parameters, checksums, and lineage. Metadata is authoritative for querying.

## Configuration Rules

Configuration is the primary customization mechanism. It exists in layers, in this order of precedence (highest wins):

1. Command-line flags.
2. Environment variables.
3. Per-run configuration files (`--config <path>`).
4. Per-channel configuration files (part of the repository or deployment).
5. Platform defaults (bundled with the code).

Rules:

- **Every runtime tunable is configurable.** Hard-coded magic numbers are prohibited outside of Foundation utility functions with obvious bounds (a hash length, a buffer size). Business rules — durations, prompts, provider preferences, thresholds — live in configuration.
- **Configuration schemas are validated at load time.** Malformed configuration is caught before the pipeline consumes budget. Validation errors are actionable ("field X must be a positive integer, got Y") not cryptic.
- **Defaults are safe.** The default behavior is the conservative behavior. Aggressive settings (larger context windows, higher rate limits, riskier providers) are opt-in.
- **Secrets never appear in configuration files that could be committed.** Configuration files may reference secrets by name; the actual value comes from environment variables or a secret manager.
- **Deprecated configuration keys keep working with a warning.** See [Backward Compatibility](#backward-compatibility).

## Render Profile Rules

A render profile is a bundle of settings that fully describes how a specific format is produced: aspect ratio, target duration, caption style, pacing constraints, safe zones, mastering targets. Render profiles are configuration, not code.

- **One profile per format.** Vertical Shorts, horizontal educational, long-form documentary, podcast video — each has its own profile.
- **Profiles are declarative.** A profile does not embed procedural logic. It declares values that domain code consumes.
- **Profiles are versioned.** Changes to a profile carry a version bump and a deprecation notice. Old profile versions continue to render identically.
- **Profiles inherit.** A "long-form documentary" profile may inherit from a "long-form base" profile. Inheritance is explicit and shallow (single-parent only).
- **Profile selection is explicit at pipeline entry.** No auto-detection based on filename or heuristics. The operator, configuration, or scheduler chooses the profile.

## Logging Philosophy

Logs exist to reconstruct what happened after the fact. They are not a UI.

**Levels.** Use standard library `logging`. Level meanings are strict:

- `DEBUG` — verbose internal state, useful only during active debugging.
- `INFO` — normal progress: a stage started, a stage completed, a provider was chosen.
- `WARNING` — something unexpected but recoverable: a fallback fired, a retry occurred, a quota approached its limit.
- `ERROR` — a failure that a human should read: a stage failed, an artifact was rejected, an upload was blocked.
- `CRITICAL` — the process cannot proceed.

**Every log line is structured.** Logs are consumed by machines. Prefer key-value pairs (`user=abc, action=fetch, result=ok`) over free-form prose. A structured logging library is used where practical.

**No logging of secrets, tokens, or full API payloads.** Log identifiers, sizes, and outcomes. Never log the value of a credential or the body of a document that may contain PII.

**Log at the boundary, not throughout.** A single function does not log every internal step. It logs its entry, its outcome, and any decisions the caller needs to see.

**Idempotent messages.** Log messages are unique enough that grepping for a phrase locates the code that emitted it.

## Error Handling Philosophy

Errors are first-class outcomes, not exceptional interruptions.

**Distinguish expected from unexpected.** An expected failure (network timeout, rate limit, quota exhaustion, provider outage) returns a result value. An unexpected failure (a bug, a malformed input, an assertion violation) raises. Callers handle expected failures with normal control flow.

**Never swallow errors.** A bare `except:` or an `except Exception: pass` is prohibited. If an error is truly ignorable, it is logged at `DEBUG` with a comment explaining why.

**Retry only what benefits from retry.** Network calls and transient failures may retry with exponential backoff. Bugs, misconfiguration, and authorization failures do not benefit from retry and must not be retried.

**Fail fast at boundaries.** Validate inputs at the entry point of a subsystem, not at every internal call site. Once validated, internal code trusts its inputs.

**Errors carry context.** An exception message includes the parameters that were in scope: the file being processed, the provider being called, the stage that was running. Stack traces are not enough.

**Timeouts everywhere.** Every network call has an explicit timeout. There are no unbounded waits.

**No re-raising as generic Exception.** Preserve exception types. `raise NewError("wrapping the old one") from err` is preferred to bare re-raise when adding context.

## Testing Strategy

Not everything is tested. What matters is tested well.

**Test the observable behavior, not the implementation.** Tests that break every time you rename a private method are testing the wrong thing.

**Public interfaces have tests.** Every public function on every layer's public API has at least one test. Tests verify normal use, at least one error path, and any behavior that the docstring specifically promises.

**Provider interfaces have contract tests.** A contract test suite validates that any implementation of a provider interface conforms to the interface. New providers run this suite and must pass.

**Critical failure modes have tests.** The specific behaviors that have caused production incidents get a permanent regression test. If a bug is fixed without adding a test, it will regress.

**Rendering aesthetics are not unit-tested.** Whether a caption is readable, whether music sits nicely under narration, whether a hook is punchy — these are judged by human review, not by automated tests.

**Slow tests are marked and excluded from the default run.** A default `pytest` invocation completes in under a minute. Integration tests that exercise real providers are opt-in.

**Fixtures are shared and small.** A test that constructs 200 lines of setup is testing the setup, not the code.

**Test names describe the scenario, not the function.** `test_upload_retries_on_transient_network_failure` is preferred to `test_upload_2`.

## Performance Philosophy

Performance improvements are driven by measurement, not by intuition.

**No optimization without a demonstrated bottleneck.** Speculative rewrites in the name of speed are prohibited. Before any optimization work begins, a profile identifies where time or memory is actually spent. Optimizing a function that does not appear in the profile is wasted work — and worse, it usually adds complexity that outlives the imagined problem.

**Correctness and readability take priority over micro-optimizations.** A slow-but-clear implementation is preferred to a fast-but-cryptic one until profiling proves the tradeoff is worth it. The platform's hot path is I/O and external APIs, not CPU — Python-level micro-optimizations rarely move the needle.

**When optimization is justified, follow this order:**

1. Reproduce the slow case with a benchmark that fails today's target.
2. Profile with `cProfile`, `pyinstrument`, or a comparable tool. Attach the profile output to the pull request.
3. Change one thing. Measure again.
4. Only accept the change if the benchmark shows a real, meaningful improvement.
5. Keep the benchmark in the test suite as a regression guard.

**Memory is a resource too.** Streaming large artifacts (videos, audio tracks) is preferred to loading them into memory. Every hard-coded byte-array size is a decision that deserves comment.

**Cost is a performance dimension.** Time and money are both measured. A change that halves latency but triples per-video API cost is not a win. Optimize the composite.

## Pull Request Workflow

Every non-trivial change goes through a pull request. "Non-trivial" means: any change to production code, any change to configuration schemas, any change to a public interface. Documentation-only fixes may be committed directly to `main` at the maintainer's discretion.

**Branch names.** `type/short-slug` — `fix/pexels-query-quotas`, `feat/pixabay-provider`, `docs/engineering-guide`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `migration`.

**PR titles.** Imperative mood, present tense, under 72 characters. Same type prefix as the branch: `feat: Add Pixabay video provider`.

**PR descriptions.** Every PR includes:

- What changed and why.
- What was tested and how.
- Any behavior that is intentionally out of scope for the PR.
- A link to the issue or discussion, if one exists.

**Small PRs.** A PR that changes more than ~400 lines is reviewed with skepticism. Large refactors are split. A "clean-up" PR that touches 30 files is fine; a "add new feature" PR that touches 30 files is not.

**PRs are self-contained.** Each PR passes tests, compiles, and can be reverted independently. A "part 1 of 3" PR that leaves the tree broken is rejected.

**Review criteria.** Reviewers verify: correctness, adherence to this guide, test coverage where applicable, documentation updates where applicable, and backward compatibility. Style bikeshedding is deferred to the linter.

**Merging.** Squash-merge with a well-written commit message. Never merge with tests failing. Never merge if a warning was introduced without a fix or a documented rationale.

## AI Contributor Guidelines

Most changes to this repository will be authored by AI agents (Codex, Claude, and their successors) working from human prompts. AI agents are held to the same standards as human contributors — every rule in this document applies equally. The guidelines below reinforce the norms AI agents most often violate.

**Prefer modifying existing code over creating duplicate implementations.** Before writing a new function, search for one that already does what you need. Two functions that do the same thing under different names are a maintenance burden with no benefit.

**Read surrounding modules before introducing new abstractions.** A new abstraction is only justified if the existing code cannot express the concept naturally. When in doubt, ask (or state the tradeoff explicitly in the PR description) rather than assume.

**Avoid architectural redesign unless explicitly requested.** If a task can be completed within the current architecture, complete it that way. Volunteering a redesign — even a technically better one — is out of scope until the operator asks for it. Redesigns arrive as separate, labeled pull requests.

**Keep pull requests focused on a single responsibility.** One PR does one thing. Combining a bug fix with an unrelated refactor makes the change harder to review and impossible to revert cleanly.

**Preserve backward compatibility unless explicitly instructed otherwise.** Renaming, removing, or reordering public identifiers, CLI flags, environment variables, output paths, or configuration keys is not "cleanup" — it is a breaking change and must be treated as one.

**Explain reasoning before major refactors.** For any change touching more than one module, or any change to a public interface, the PR description states the problem, the alternatives considered, and the reason for the chosen approach. A reviewer should be able to disagree with the choice without reading the diff first.

**Verify assumptions against the code, not against training data.** Before asserting that a file, function, flag, or dependency exists, confirm it. Training-data confidence is not a substitute for reading the current tree.

**When a rule in this guide seems wrong for the task at hand, flag it explicitly.** Do not silently work around a rule. Either follow it, propose an exception in the PR description, or ask the operator to update this guide. The guide is authoritative; silent workarounds erode it.

**When the task is ambiguous, prefer a smaller change plus a question over a larger change plus assumptions.** A stub implementation with a clarifying question is more useful than a complete implementation that solves the wrong problem.

## Definition of Done

A change is not done until every applicable box is checked. Contributors — human or AI — confirm each box themselves before requesting review.

- [ ] Code compiles and imports cleanly.
- [ ] Type hints are present on new public functions.
- [ ] Docstrings are present on new public functions.
- [ ] Tests exist for new public interfaces.
- [ ] Existing tests still pass.
- [ ] Backward compatibility is preserved (existing configurations, CLI flags, environment variables, output paths continue to work identically).
- [ ] Configuration schemas are validated at load time if any new configuration was added.
- [ ] Documentation is updated: `ARCHITECTURE.md`, `CHANGELOG.md`, `ROADMAP.md`, and any relevant guide reflect the change.
- [ ] No secrets in code, no secrets in configuration files that are committed.
- [ ] Logging is structured, respects levels, and does not leak sensitive data.
- [ ] Error handling distinguishes expected from unexpected failures.
- [ ] Any new external dependency is justified in the PR description.
- [ ] Any breaking change is called out in the PR description with a migration path.
- [ ] The commit message accurately summarizes the change.

A PR that skips any applicable box without a documented reason is returned for revision.

---

*This document is maintained with the same discipline as the code. When a rule changes, update this file in the same pull request that introduces the change.*
