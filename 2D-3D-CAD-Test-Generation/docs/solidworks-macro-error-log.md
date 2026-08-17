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

### E016 — two centerlines do NOT fail a revolve (doc contradiction)
**Symptom:** a revolve sketch containing TWO centerlines built successfully and
returned a feature. Reference doc 06 states this makes the axis ambiguous and
the revolve fail.
**Cause:** on SolidWorks 2026 the revolve picks one centerline rather than
rejecting the sketch. Which one it picks was not determined.
**Fix:** do not rely on SolidWorks to reject an ambiguous axis. Emit exactly one
centerline per revolve sketch and assert that count before calling
`FeatureRevolve2` — the API will not tell you.

### E017 — patterns and mirrors fail SILENTLY (None / wrong location, no error)
**Symptom:** three separate cases in one session — `FeatureLinearPattern4` and
`FeatureCircularPattern5` returned `None` with no exception and no new geometry;
`InsertMirrorFeature2` returned a valid feature while producing no new
cylindrical face because the mirrored copy landed off the part.
**Cause:** pattern/mirror features depend entirely on the selection state
(direction or axis at Mark 1, seed feature at Mark 4, mirror plane at Mark 2).
A wrong or missing selection is not an error condition to SolidWorks.
**Fix:** never accept a pattern/mirror on its return value. Count the resulting
geometry — cylindrical faces for hole patterns, volume delta otherwise — and
compare it to the expected instance count. The repo's
`macro_semantics`/`feature_verify` instance checks exist for exactly this.

**Narrowed (iteration 9):** CIRCULAR patterns DO build once the axis is derived
from a concentric cylindrical face and the seed is at Mark 4 (measured: faces
3 -> 6, four instances; Mark 1 instead gives `None` and nothing). LINEAR patterns
remain unexplained: `GetIDsOfNames` finds dispids for `FeatureLinearPattern`
through `FeatureLinearPattern5`, so the API is present, yet
`FeatureLinearPattern4` with an edge at Mark 1 and the seed at Mark 4 returns
`None` and creates nothing. Prefer sketching every hole at its computed centre in
ONE sketch and cutting once — measured exact, one feature, no prerequisites, and
no silent-failure mode (see lesson 06).

### E018 — `callable()` is not a valid method-vs-property test under win32com
**Symptom:** `IModelDoc2.FirstFeature` raised `Member not found (-2147352573)`
when read through a helper of the form `v() if callable(v) else v`. Downstream,
a feature-tree walk found nothing, and a reference axis that HAD been created
successfully (`Axis1 | RefAxis`, confirmed by dumping the tree) was reported as
missing — which in turn made a working circular pattern look impossible.
**Cause:** win32com dynamic objects define `__call__`. A COM OBJECT returned by
a property is therefore "callable", so the helper calls it.
**Fix:** try the call and fall back to the attribute value:
```python
attr = getattr(owner, name)
if not callable(attr):
    return attr
try:
    return attr(*args)
except Exception as e:
    if not args and ("Member not found" in str(e)
                     or "Parameter not optional" in str(e)):
        return attr        # a property whose value is a COM object
    raise
```
Note `solidworks_builder._prop` uses the naive form. It is safe where it is used
today (its values are tuples and bools) but is the same latent trap.

### E019 — an extrude with the sketch left OPEN succeeds (doc contradiction)
**Symptom:** deliberately skipping the closing `InsertSketch(True)` before
`FeatureExtrusion3` produced a valid feature with no exception.
**Cause:** SolidWorks consumes the still-open active sketch. Reference doc 08
lists "sketch still open" as a cause of a null return; that is not the behaviour
here.
**Fix:** none needed for a boss — but do NOT generalise it: the COM **cut** path
does require the sketch closed and selected (E014). Boss and cut are not
symmetric. Close the sketch in both cases so one rule covers both.


### E020 — RETRACTED (iteration 14). Was: "an all-edges fillet is a silent no-op"
**This entry was wrong, and the mistake was mine, not SolidWorks'.** It is kept
rather than deleted because how it was produced is the lesson.

**What it claimed:** `FeatureFillet3` with 44 edges selected on a finished
bracket returned without raising and changed the volume by exactly zero
(5.83808 -> 5.83808 in^3), so a single incompatible edge must void the whole
feature.

**What was actually happening:** the test measured the volume BETWEEN selecting
the edges and calling the fillet (`t4_bracket_and_validation.py` selects at line
155, measures at 159, calls at 162). The measurement forces a rebuild, the
rebuild clears the selection, and `FeatureFillet3` was therefore called with
NOTHING selected. See E022 for the real mechanism.

**Re-tested with the measurement moved before the selection** (iteration 14,
`t10_retract_e020.py`), on a 5.0 x 3.0 x 0.375 plate carrying four through
holes, all 20 edges selected INCLUDING the circular hole edges:

    R.0625 all-edges fillet -> BUILT, volume 5.45933 -> 5.42309 in^3

An all-edges fillet works. A single-edge fillet on that same plate also works
(5.45933 -> 5.45514), which had been recorded as a second unexplained gap in
iteration 9 for the identical reason. **MANUAL rule 11 was rewritten**; the
edge-by-edge advice is still reasonable for *reporting* which edges were skipped,
but it is a preference, not a workaround for a defect that does not exist.

### E022 — a rebuild between "select" and "call" voids the call, silently
**Symptom:** an identical, correct feature call builds in one script and returns
`None` in another. No exception either way.
**Cause:** anything that rebuilds the model — `ForceRebuild3`, `EditRebuild3`,
or any helper that calls one (in this lab, `swlab.Lab.measure` ends with
`check_rebuild_errors`, which forces a rebuild) — **clears the current
selection**. The feature call then runs against an empty selection and returns
`None`. Measured directly: 12 edges selected -> rebuild -> `GetSelectedObjectCount2`
returns 0. Enumerating bodies, reading `doc.Extension`, and
`GetFeatureCount` do NOT clear it; only the rebuild does.
**A rebuild also DISCONNECTS topology pointers you already hold.** Edge and face
objects collected before the rebuild raise
`com_error: The object invoked has disconnected from its clients` when you try
to select them afterwards.
**Fix:** treat *collect topology -> select -> call* as one atomic sequence with
nothing in between. Do every measurement, sanity check and log line BEFORE you
start selecting. This is the natural "select, verify, then build" pattern, and
it is exactly the pattern that breaks — which is why it produced two false
findings here (E020 and iteration 9's single-edge gap).
**The pipeline is NOT affected** (audited iteration 13): every
`check_rebuild_errors` call site in `pipeline/solidworks_builder.py` runs AFTER
a feature completes, never between a selection and a call.

### E023 — a sweep that stops at its first success proves nothing about the rest
**Symptom:** iteration 15 swept `InsertFeatureChamfer(Options, ChamferType, ...)`,
found `Options=0` built a chamfer, stopped there, and concluded that the
pipeline's `Options=4` was a defect worth fixing.
**Cause:** the sweep never tried `Options=4`. An A/B on one part, alternating
`4, 0, 4, 0`, showed **both** values build on the first attempt and **both**
return `None` on the later ones — the failures track attempt order (the edge
already carries a chamfer), not the flag.
**Fix:** no pipeline change; `build_chamfer`'s `InsertFeatureChamfer(4, 1, ...)`
is correct. Before editing working code on the strength of an experiment, run
the comparison the experiment skipped. Iteration 14's single-edge chamfer
failure is explained the same way: that edge had just been filleted.

### E021 — IFeatureManager.InsertFeatureShell is not exposed under late binding
**Symptom:** `AttributeError: <unknown>.InsertFeatureShell`; `hasattr` finds
neither `InsertFeatureShell` nor `InsertFeatureShell2` on the FeatureManager.
**Cause:** unknown — the method is documented for this interface but does not
resolve through dynamic dispatch on this install (compare E018, where a member
existed but needed a different access form; here it is absent entirely).
**Fix:** none — CONFIRMED absent (iteration 9). The suggested raw-dispid Invoke
route was tested and does NOT rescue it: `GetIDsOfNames` finds no dispid for
`InsertFeatureShell`, `InsertFeatureShell2`, `InsertShell`, `FeatureShell` or
`InsertFeatureShellByFace`. The method is not on this `IFeatureManager` in any
access form. Treat shell as an unsupported feature kind and escalate rather than
emitting a call that will raise.

### E024 — an edge treatment at or past half the material thickness does nothing
**Symptom:** `FeatureFillet3` returns `None`, raises nothing, and the volume is
unchanged — for a radius that looks perfectly reasonable on the drawing.
**Cause:** the two opposite edges of a through edge each consume the radius, so a
fillet needs `R < thickness / 2`. Measured on a 4.0 x 3.0 x 0.5 in plate, all 12
edges, each radius from an independent clean state (iteration 16,
`t12_return_value_trust.py`):

    R0.01 -> built     R0.20 -> built     R0.26 -> None     R1.00 -> None
    R0.0625 -> built   R0.24 -> built     R0.30 -> None     R5.00 -> None
    R0.125 -> built                       R0.50 -> None

The last radius that builds is 0.24 and the first that fails is 0.26 on a 0.5
plate. The boundary value itself (0.25) is not established either way.
**Fix:** `pipeline/validator.py::_check_edge_treatment_radius` now warns at PLAN
time when a fillet radius or chamfer distance is not strictly less than half the
material thickness, naming both numbers, so it reaches the engineering report
before anyone opens SolidWorks. Advisory, never blocking — the drawing is the
authority and the callout may be intended for a thicker edge. The COM builder's
existing post-failure message stays as the backstop. Pinned by
`tests/test_edge_treatment_radius.py`.

**Corollary worth its own note:** across those same 10 radii, "returned a
Feature" and "the volume changed" agreed **every time**. The return value is a
trustworthy signal — which is what makes the interactive fillet macro's bare
`If swFeat Is Nothing` check sufficient in the one place where a human owns the
selection and rule 14 forbids measuring first.

### E017 (UPDATE, iteration 19-21) — linear patterns SOLVED; it was the selection form
Previously recorded as "patterns fail silently, cause unknown". Circular patterns
were already understood (axis at Mark 1, seed at Mark 4). Linear patterns are now
closed too, and the cause was never a missing or broken API.

**The working combination**, measured on an 8 x 3 x 0.375 plate, holes 1 -> 3:

    direction -> Extension.SelectByID2("", "EDGE", x, y, z, True, 1, Nothing, 0)
    seed      -> Extension.SelectByID2(name, "BODYFEATURE", 0,0,0, True, 4, ...)
    call      -> FeatureLinearPattern4 with TWENTY arguments

**What fails:** `IEdge::Select4` for the direction (returns True, unusable);
`FeatureLinearPattern5` (Type mismatch under both selection forms); the 18-argument
call (`Parameter not optional`, -2147352561).

**Unexplained:** `IEntity::Select2(True, 1)` sets the correct Mark on the same
edge object and still does not work, so "Mark 1" is necessary but not sufficient.
The prediction that the Mark was the whole mechanism was tested and disproved;
SelectByID2 is the only measured route. Recorded as a gap, not rationalised.

**Two defects were fixed in `solidworks_builder.build_pattern`**, the second
hidden behind the first: it selected the seed at Mark 0 with no direction
reference at all (never able to succeed), and its call carried 18 arguments. Both
corrected and verified through the SHIPPING code path, not just the lab recipe
(`t16_verify_pipeline_fix.py`).

### E025 — hardcoded enum fallbacks are the residual E012 risk
**Symptom:** none observed — this entry records an audit that came back clean.
**Cause:** `_const(name, fallback)` applies its hardcoded number exactly when the
type library cannot be read, so a wrong fallback cannot be caught downstream.
**Fix:** all five fallbacks in `pipeline/` (`swCM` 1, `swDefaultTemplatePart` 8,
`swINCHES` 3, `swMM` 0, `swSolidBody` 0) were read from the live library and
agree, as do the five E012-family end conditions. `tests/test_enum_fallbacks.py`
re-reads them from source and fails on drift without needing SolidWorks; adding a
new fallback requires running `t13_enum_fallback_audit.py` first. The emitted VBA
is immune by construction — it uses named constants, never numeric literals.

### E026 — OPEN: chamfers fail on real parts for a reason not yet found
**Symptom:** `InsertFeatureChamfer` returns `None`, raises nothing, no geometry
changes — on real drawings, repeatedly. TEST3 batch, three failures in the first
four parts, every one with scope 'all edges':

    4079-D  F005  1.76 in   49 edges -> None
    4079-D  F006  0.12 in   49 edges -> None
    4088-A  F003  0.06 in   26 edges -> None    (on a 0.500 thick key)

The last one rules out size: 0.06 on a 0.500 plate is far inside the E024 limit.

**Two hypotheses tested live and BOTH DISPROVED** (iterations 22-23):
  * the all-edges scope — an all-edges chamfer builds fine on a clean
    4 x 3 x 0.5 plate (6.0 -> 5.94715), while the all-edges fillet control also
    builds. Scope is not it.
  * circular hole edges — 0, 1 and 4 holes all chamfer successfully
    (12, 14 and 20 edges). Hole edges are not it.

**Cause unknown.** The most likely remaining factor is the accumulated feature
state where the chamfer runs: 4079-D's chamfer fires on geometry already
corrupted by the depth-semantics defect, and its 49 edges include faces from five
prior features. That is a hypothesis, not a finding.

**No fix made.** The builder already fails loudly here (`SolidWorksError` naming
distance, angle, edge count and scope) and the feature is deferred rather than
silently dropped, so the failure is visible and the part still ships. Left open
rather than closed with a story — see E023 for what a confident wrong explanation
nearly cost.

### E027 — the scorecard reports PASS on a part with missing geometry
**Symptom:** `validation.json` says `build_health: PASS — "N planned feature(s),
none reported a build failure"` while the same part's `model_check.txt` lists a
chamfer that returned None and a cut that was deferred open. Observed on TEST3
parts 4088-A-RevA and 4092-B, 2026-08-17.
**Cause:** `validation._layer_build_health` counts only `macro_result.json`
entries whose status is fail/failed/error. Features ending `deferred_open` or
`skipped` never appear there. The layer DOES compute
`excluded = [... state == "EXCLUDED_INCOMPLETE"]` and then appends it to
`card.advisories` **without affecting the layer status** — the one signal that
knows a feature is missing is routed away from the verdict.
**Compounding cause:** `feature_audit` is SKIPPED on every part because
`*_feature_verification.json` is never written — `main.py` does not call
`feature_verify` at all, so Stage 10.6 is in the documented stage index but not
in the run. The only geometric check that executes is the three-extent bounding
box, which cannot see a missing hole.
**Fix:** NOT YET MADE — planned as Tier A of
`test_drawings/TEST3_Drawings/REMEDIATION_PLAN.md`. Until then, trust
`<part>_model_check.txt` over `validation.json` for build completeness.

### E028 — a hole reports PASS and is not found in the STL (framing CORRECTED)
**Symptom:** `macro_result.json` says `F002 PASS`; Stage 10.6 measures the STL
and reports `F002 MISSING, measured: None`. Observed on TEST3 parts
4086-A-RevA and 4088-A-RevA, 2026-08-17, immediately after Stage 10.6 was wired
in for the first time.
**CORRECTION (2026-08-17, same day).** This entry first said "the builder is
lying — it logged PASS for absent geometry". That framing is **not supported**,
and checking the source before acting on it is what caught it:
`solidworks_builder` ALREADY measures `_total_volume` either side of every
material-removing feature and raises when a cut removed nothing —

> reported success but removed no material (volume before=..., after=...) — the
> cut profile likely does not overlap the body

So the builder's PASS means material genuinely was removed. Something else
explains the gap, and there are two live candidates, neither yet tested:

  (i)  **the hole is cut in the wrong place.** These are faint scans with no
       vector line work, so every position is a Hough estimate — the extraction
       says so itself. But `extras: []` on both parts means Stage 10.6 found no
       unexpected cylinder anywhere either, which argues against a simple
       mispositioning.
  (ii) **Stage 10.6 cannot see these holes.** 4086-A's is a BLIND counterbore
       (`through: false`, 0.38 deep). If the STL detector only recognises
       through-cylinders, a present blind hole reads MISSING. That would make
       this a false positive in the brand-new verification stage — which is
       exactly what a stage gets scrutinised for on its first day.

Candidate (ii) must be ruled out before any builder change: "the verifier is
wrong" and "the builder is wrong" produce identical symptoms here, and E020 is
the standing reminder of what happens when a new measurement tool is trusted
over the thing it measures.
**Contributing factor found on 4088-A:** the planned hole diameter is `0.499`,
which is that key's own HEIGHT (`1.75 x .499`); the drawing calls the hole out
only as "DR & C'BORE FOR #10 SOC. HD. CAP SCR" with no explicit diameter. A hole
as wide as the part is thick is a degenerate cut. Same class as the
depth-semantics defect: a dimension used for a purpose its own label contradicts.
**Partial mitigation (2026-08-17):** candidate (b) implemented as
`validator._check_feature_size_duplicates_envelope` — a hole/cut size that
exactly repeats an envelope extent or the material thickness is flagged at plan
time as borrowed rather than read.

**It does NOT catch 4088-A, and that is stated rather than glossed.** The hole
came out `0.499` against a `0.500` envelope — a tolerance-band difference, not a
repeated number. Widening the match to span it would flag a 0.499 bore in a
0.500 plate, which is exactly what a legitimate tight fit looks like, so the
strict rule is deliberate. `tests/test_feature_size_duplicates_envelope.py`
pins the limit with a test named for it.

**Candidate (a) — "verify the cut removed material before logging PASS" — turns
out to ALREADY EXIST** (see the correction above), so it was never the fix. The
real next step is to settle candidate (ii): build a plate with a known blind
counterbore, export the STL, and check whether `feature_verify` classifies it OK
or MISSING. One experiment, no ambiguity, and it decides which component is at
fault. See Tier B1 of `test_drawings/TEST3_Drawings/REMEDIATION_PLAN.md`.
