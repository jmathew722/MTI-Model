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

---

## Environment, COM and geometry failure modes (merged from reference doc 10)

Merged 2026-08-16 from `docs/reference/10_common_failure_modes.md` so this file
stays the ONE canonical troubleshooting reference (the alternative — a second
competing failure doc — is exactly the problem `REFACTOR_ANALYSIS.md` §1.8 just
fixed). Rows marked **[repo]** are where this project's live experience differs
from the generic guidance; trust those over the generic advice here.

### Environment / setup

| Symptom | Cause | Fix |
|---|---|---|
| `CreateInstance` hangs, SW splash never finishes | first-run dialogs, license prompt, or a crashed instance | kill stray `SLDWORKS.exe`; launch SW manually once per machine; then attach |
| `InvalidCastException` on the app object | interop version ≠ installed SW | **[repo]** N/A — this pipeline is late-bound `win32com`, so there are no interop DLLs to mismatch. The equivalent failure here is a method that silently does not resolve; see E-series below. |
| Calls fail with `RPC_E_SERVERCALL_RETRYLATER` | SW busy (rebuilding, dialog open) | **[repo]** `solidworks_builder.com_retry()` retries the busy HRESULT family with bounded backoff (added 2026-08-16, reference doc 02). |
| Works on run 1, flaky on run N | RCW/COM object leaks | **[repo]** `solidworks_builder.release_com()` releases objects created in enumeration loops (added 2026-08-16). |
| Part template not found after a SW upgrade | `SOLIDWORKS_TEMPLATE_PATH` pinned to the old version | **[repo]** `resolve_part_template()` falls back: configured path → SW's own preference → newest installed template. A stale setting is reported, not fatal. |
| Automation blocked | SW security settings / add-in interference | Tools → Options; disable nonessential add-ins for the automation session |

### Dialog suppression
Dialogs hang unattended runs. Never call an API that opens UI; use silent save
options; prefer `ForceRebuild3(False)` + programmatic error reading over any
UI-triggering path. **[repo]** Generated VBA deliberately DOES use `MsgBox` for
assumption flags — those macros are run by a human in SolidWorks, not
unattended; the COM path (`solidworks_builder.py`) opens no UI.

### Geometry failures

| Symptom | Cause | Fix |
|---|---|---|
| "Zero-thickness geometry" | a cut/boss leaves surfaces touching along a line | overshoot the boundary — **[repo]** `slot_cut.EDGE_OVERSHOOT_EPS` does exactly this for open-edge notches |
| Fillet fails on some edges | radius ≥ the local wall/edge size | reduce, or fillet edge-by-edge and report the skips — **[repo]** the deferred-retry queue does this (`deferred_retry.py`) |
| Boss creates a second body | it does not touch the base, or merge=false | assert body count after every additive feature — **[repo]** `validation.py` layer `solid_body` |
| Cut splits the part in two | depth/position wrong | re-check that step's evidence dims — **[repo]** each step now records them (`BuildStep.evidence`) |
| Sketch "cannot be used" | open contour, self-intersection, duplicate entities | compute every vertex from the plan, never by chaining additions around a profile |
| Feature builds in the wrong direction | direction flag | **[repo]** `FeatureCut4` is already called with a flip-retry (both directions) before failing |

### Floating-point discipline
Compute every sketch vertex closed-form from plan dimensions, never by chaining
additions around a profile (accumulated error leaves micro-gaps that break
contours); compare coordinates with a tolerance, never `==`; keep angles as
exact expressions.

### Version sensitivity
Feature methods get superseded (`FeatureExtrusion2`→`3`, `FeatureCut3`→`4`).
**[repo]** Hole Wizard is called out in doc 10 as "the most version-volatile" API
in the product — this project live-verified that on SolidWorks 2026 and REMOVED
its Hole Wizard path entirely; see `pipeline/experimental/README.md`. The
plain-cut fallback doc 10 recommends is this pipeline's only hole method.

---

## Empirically discovered failure modes (hands-on lab, 2026-08-16)

Found by running code against live SolidWorks 2026 rev 34.3.2, not by reading
documentation. Lab notebook: `experiments/solidworks_practice/`; lessons:
`docs/solidworks-lessons/`. **All four are SILENT** — the call returns an object
and the model reports clean.

### E012 — a guessed enum value deleted the entire solid
**Symptom:** `FeatureCut4` returned a valid feature, `check_rebuild_errors`
reported clean, and the part's volume went 12.0 in³ → 0. Body count stayed 1.
**Cause:** `swEndCondThroughAllBoth` was assumed to be `7` from enum ordering. On
this install it is **9**; `7` is `swEndCondUpToBody`, which with no body
reference removes everything. (`swEndCondMidPlane` is likewise **6**, not 4.)
**Fix:** resolve every enum through `_const("swEndCond…")` after connecting —
never a literal, never an inferred ordinal. This is doc 03's "magic numbers for
enums" anti-pattern with a measured consequence.

### E013 — `None` for a VT_DISPATCH argument fails the whole call
**Symptom:** every `SelectByID2` raised `Type mismatch (-2147352571)`; eight
consecutive experiments failed before a single feature was created.
**Cause:** the 8th argument is `VT_DISPATCH`. Python `None` marshals as
`VT_EMPTY`, which SolidWorks rejects — even though the parameter is optional.
**Fix:** pass `VARIANT(pythoncom.VT_DISPATCH, None)` —
`solidworks_builder._null_dispatch()`.

### E014 — clearing the selection between closing a sketch and cutting
**Symptom:** `FeatureCut4` returned a feature; zero material was removed; the
hole audit found no cylindrical faces. No error anywhere.
**Cause:** closing a sketch with the second `InsertSketch(True)` leaves it
SELECTED, and that selection is the profile the feature consumes. A
`ClearSelection2(True)` before the feature call deselects it.
**Fix:** close the sketch, then call the feature immediately. Do not clear the
selection in between. (Distinct from E006: that one re-selects a closed sketch by
name; this one clears a correct selection.)

### E015 — a hole sketched on a non-Front plane silently misses the body
**Symptom:** the same (x, y) that drills correctly on the Front Plane produced no
cylindrical face and removed no material when sketched on the Top or Right plane.
**Cause:** each sketch plane has its own 2-D frame; Front-Plane model coordinates
do not transfer. The profile lands off the solid, the through-all cut finds
nothing, and both direction flips return `None`.
**Fix:** resolve the sketch-plane → model-axis mapping per plane before placing
geometry; verify by measuring the resulting cylindrical face, not by the call's
return value.
