# PR #10 — Quality Review Notes

**Reviewer:** Vincent · **Date:** 2026-07-10 · **Sample:** Northern Lights validation video (55.23 s)
**Overall score:** 7.8–8.2 / 10 · **PR #10 verdict:** approved

These notes are the durable record of quality observations made while validating PR #10. They are not blockers for PR #10 (which the abstraction-refactor scope does not address). They are the input to future PR scoping.

---

## What worked

- Visual relevance significantly better than earlier iterations. No "animal in a lightning video" mismatches. Confirms PRs #7 – #9.6 (media selection, source planning, provider expansion, portrait safety, media acceptance & domain safety) are functioning.
- Pacing appropriate for ~55 s Short.
- Captions readable and consistent.
- Topic coherence preserved throughout.
- Portrait format preserved. No landscape spill.

The pipeline now prefers a correct placeholder over an obviously wrong clip. This is the architectural payoff of the last several PRs.

## Quality gaps to address in future work

### 1. Local-explainer fallback fires too often

Scenes with dark backgrounds + centered text (e.g., "Person Listening In Snow", "Purple Aurora Borealis Sky", "Frozen Pine Trees Night") appear frequently enough to make parts of the video feel like a slideshow rather than a dynamic Short. The domain-safe fallback introduced in PR #9.6 is working correctly — it is just firing more readily than the desired aesthetic tolerates.

**Candidate approaches:**

- Cap fallback density per run (e.g., ≤ 20 % of scenes may fall through).
- Require a minimum candidate-score gap before falling through — a near-miss real clip is better than a text card.
- Enrich the fallback itself (add motion, add relevant background footage instead of a solid dark card).

### 2. Motion absent from static NASA imagery

NASA solar/space stills are topically correct but read as documentary-slow. The pipeline already has a Ken Burns treatment for images (per `auto_short.py` render-segment path) — it may not be applied uniformly to all NASA sources.

**Candidate approaches:**

- Verify Ken Burns treatment applies to NASA `providers/stock` items, not only local-folder images.
- Add subtle zoom/pan/parallax primitives as scene-level properties on the timeline.

### 3. Caption timing not synced to visual beats

The highlighted-word system works, but caption emphasis sometimes drifts from narration emphasis and scene transitions.

**Candidate approaches:**

- Anchor caption highlights to TTS word-timing metadata rather than uniform slicing.
- Emphasize the caption word that lands on a scene-change frame.

### 4. Visual variety limited

Repeated styling — dark explainer cards, NASA imagery, starry skies — creates a narrower visual palette than a polished Short warrants.

**Candidate approaches:**

- Broaden the SceneType-to-provider capability declarations for astronomy topics so more diverse providers are consulted.
- Score candidates on visual-diversity vs previously-picked-in-this-run, not only per-scene fit.

---

## Recommendation for the next PR

The architecture is now preventing bad visual mismatches. The next quality leap comes from making correct visuals more engaging, not from continuing to refactor the core pipeline. **Consider proposing a visual-polish PR before proceeding to ROADMAP's PR #11 (Pipeline Orchestrator with Resume Points)**, if quality-perception matters more than reliability at this point in the project's evolution.

Candidate PR #12 scope options, ordered by leverage:

1. **Fallback rate limiting** + local-explainer enrichment (addresses gap #1). Small surface area, high visual impact.
2. **Ken Burns treatment audit** for all image sources including NASA (addresses gap #2). Investigate first; may already work and need only a config nudge.
3. **Caption-to-word-timing alignment** using TTS metadata (addresses gap #3). Requires exposing word-timing from the voice provider layer.
4. **Diversity-aware scoring** in media selection (addresses gap #4). Adds a per-run "recently used" penalty to `score_candidate`.

Any subset of these could ship as one PR or several. None require touching Timeline, Renderer, or Domain models.

---

## Explicit non-conclusions

- These observations are drawn from **one** validation video. They may or may not generalize to Ocean Currents / QR Codes / other topics.
- The reviewer explicitly did not ask for Visual QA (computer-vision-based semantic verification) or a pipeline redesign. Both remain out of scope.
- The reviewer approved PR #10 without conditions. This document does not gate PR #10 merge.
