# SolidWorks Macro / Extraction Error Ledger (E-numbers)

Each entry is a failure mode the pipeline hit once and now guards against, so a
known-bad pattern can never silently ship again. `pipeline/macro_audit.py`
statically enforces the ones expressible as a banned/required API pattern; the
rest are enforced by generation-time invariants in `pipeline/macro_generator.py`
and `pipeline/solidworks_builder.py`.

> Note (2026-07-24): this file is referenced from code
> (`macro_audit.py`: "Every error logged in docs/solidworks-macro-error-log.md")
> but was absent in this clone, so it was reconstructed. Only entries that can be
> sourced verbatim from the code in THIS repository are listed with their rule
> text — historical entries whose text is not recorded here are intentionally
> left out rather than fabricated. Add new entries in the `EXXX` format below.

## Auditor-enforced rules (from `macro_audit.py::BANNED_APIS`)

### E004 — invented bounding-box API
- **Pattern:** `GetModelBoundingBox`
- **Why:** `IModelDoc2.GetModelBoundingBox` does not exist (it was an invented
  API); calling it fails at run time.
- **Fix:** read the box from the solid body — `IBody2.GetBodyBox`.

### E006 — re-selecting a closed sketch by name
- **Pattern:** `SelectByID2(<...>Name, "SKETCH", …)`
- **Why:** re-finding a closed sketch by name is unreliable; the name may have
  drifted (auto-numbering) and the selection silently misses.
- **Fix:** consume the ACTIVE sketch directly (the recorder pattern), or re-find
  it by `ProfileFeature` type/object — never by a name lookup after closing.

## Extraction / region-pass reliability (2026-07-24)

### Unconditional fixed-region high-resolution extraction pass
Not a build-failure E-number — a NEW reliability layer that removes a silent-skip
failure class at its source. The rejected prior design gated a high-resolution
re-read on Claude's self-reported confidence, so a field the model read *wrong
but confidently* never got a second look. The pass now runs over fixed
overlapping regions of every drawing **unconditionally** (`pipeline/region_extraction.py`),
and confidence is used only afterward to decide whether a merged field needs a
human — never to decide whether the field got a high-res read at all.

- Every overview field is accounted for at merge time (`merge_fields` raises
  `RegionMergeError` and halts the drawing if any overview field lacks a
  merge-log entry).
- Two HIGH-confidence disagreements are never auto-tie-broken — they escalate to
  human review with both readings and both region crops.
- `coverage_gap` fires only if the region pass did not run over a field's area at
  all (the literal silent-skip), which full-coverage tiling makes impossible
  unless the pass was skipped.

**When a resolved region-pass correction traces back to a documented build
failure class above, add its `EXXX` cross-reference here** (e.g. a mis-read hole
count that produced a wrong pattern build). None have traced to a build failure
yet; this note marks where such entries go.
