# 0017 — Graph page edge visual encoding: concrete colors/styles

## Status
Accepted

## Context
Spec §9 sketches the Graph page's edge encoding loosely ("edge style differs by origin (e.g. solid = explicit, dashed = extracted, colored = curated); orphaned: true edges rendered in warning color") but doesn't pin exact values. Issue #13's acceptance criteria require this to actually be implemented and visually distinguishable, so it needs to be resolved before #13 starts rather than left to whichever agent implements it. Resolved via `/grill-with-docs`.

## Decision
- `origin: explicit` — solid, neutral gray.
- `origin: extracted` — dashed, neutral gray (same hue family as explicit, since both are pipeline-derived; the dash is what signals "inferred").
- `origin: curated` — solid, blue (the one accent color — human-touched edges should visually pop against the two pipeline-derived origins).
- `orphaned: true` — solid, red. This overrides the origin color entirely regardless of what origin the edge has, since the orphan warning is the more urgent signal.

Rejected alternative: giving each origin its own distinct hue (gray/amber/blue + red for orphaned). Rejected because a 4-color palette leaves less contrast for orphaned-red against extracted-amber at a glance — restricting origin to one hue (gray) + one accent (blue) keeps red unambiguous as the only warning color on the canvas.

## Consequences
- NVL edge styling in #13 must apply the orphaned-red override in code as a check that short-circuits before origin-based styling, not as a fourth parallel case.
- Any future addition to the `origin` enum (spec §5 lists exactly `extracted`/`explicit`/`curated`, closed set) should revisit this ADR rather than silently reusing gray/blue.
