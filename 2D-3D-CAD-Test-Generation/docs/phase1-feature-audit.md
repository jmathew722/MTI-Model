# Phase 1 — Feature-Coverage Audit (2026-07-24)

Read in full before writing this: `pipeline/solidworks_builder.py` (COM build
engine, the primary path both `--engine com` and `--engine vba` build through via
`pipeline/batch.py::build_sldprt_for_part`), `pipeline/macro_generator.py` (VBA
generator, portable secondary deliverable), `pipeline/coordinate_normalize.py`
(canonical coordinate resolver — not to be modified), `pipeline/schema.py`
(the `FeatureType`/`HoleType`/`PatternKind` enums that define a build-plan
feature), `main.py` (`--engine` entry points), and
`docs/solidworks-macro-error-log.md`. Every row below is pulled from actual
function names / dispatch tables in the code, not from memory.

## 1. Feature types currently implemented

Build-plan `type` vocabulary is the `FeatureType` enum
(`schema.py`): `extrude_boss`, `extrude_cut`, `revolve`, `hole`, `fillet`,
`chamfer`, `thread`, `pattern`, `mirror`, `shell`. Plus the slot decomposition
emits the step types `slot_rect_cut` / `slot_corner_fillet` (backed by an
`extrude_cut` feature; see `slot_cut.py`).

| build-plan type | COM builder (`solidworks_builder.py`) | COM API call(s) | VBA builder (`macro_generator.py`) | Status |
|---|---|---|---|---|
| `extrude_boss` | `build_extrude_boss` | `FeatureManager.FeatureExtrusion3` | `_macro_extrude(is_cut=False)` | both ✓ |
| `extrude_cut` | `build_extrude_cut` | `FeatureManager.FeatureCut4` | `_macro_extrude(is_cut=True)` | both ✓ |
| `revolve` | `build_revolve` | `FeatureManager.FeatureRevolve2` | `_macro_revolve` (real when a profile exists, else `_macro_revolve_skeleton` + needs_review) | both ✓ (single REVOLVE type — not split boss/cut) |
| `hole` | `build_hole` (+ `_try_hole_wizard`, `build_circular_pattern_holes`) | `FeatureCut4` sketch-circle cut (default); `HoleWizard5` (opt-in, default OFF); `FeatureCircularPattern5/4` for bolt circles | `_macro_holes` | both ✓ but **cut-extrude approximation, not a true wizard hole** — see §3 |
| `thread` | `build_thread` | cosmetic thread only (no real helical) | `_macro_holes` tap-hole + cosmetic TODO | both ✓ (cosmetic) |
| `fillet` | `build_fillet` (+ `_feature_fillet3`, `_cut_interior_vertical_edges`) | `FeatureManager.FeatureFillet3` | `_macro_fillet_chamfer` | both ✓ |
| `chamfer` | `build_chamfer` | `FeatureManager.InsertFeatureChamfer` | `_macro_fillet_chamfer` | both ✓ |
| `mirror` | `build_mirror` | `FeatureManager.InsertMirrorFeature2` | `_macro_mirror` (real when a seed exists, else needs_review) | both ✓ |
| `pattern` (linear) | `build_pattern` | `FeatureManager.FeatureLinearPattern4` | `_macro_pattern_covered` / `_macro_pattern_skeleton` | both ✓ |
| `pattern` (circular) | `build_circular_pattern_holes` | `FeatureCircularPattern5` (fallback `...4`) | `_emit_circular_pattern_trio` | both ✓ |
| slot / U-notch (`slot_rect_cut`+`slot_corner_fillet`) | `build_slot` | `FeatureCut4` rectangle + `FeatureFillet3` interior corners | `_emit_slot_decomposition` (`_macro_slot_rect` + `_macro_slot_fillet`) | both ✓ |

COM dispatch: `_BUILDERS` maps the eight non-pattern types; `dispatch_feature_builder`
special-cases `PATTERN`; a slot-backed feature is intercepted before dispatch and
sent to `build_slot`. `_NEEDS_FEATURE_MAP = {FILLET, CHAMFER, MIRROR}` (need the
already-built feature map for edge/seed scoping).

## 2. Feature types that appear but have NO dedicated builder (the gaps)

Feature `type` strings actually seen across run-history `*_build_plan.json` /
`*_extraction.json` (grepped): `extrude_boss`, `extrude_cut`, `hole`, `thread`,
`pattern`, `fillet`, `chamfer`, `mirror`, `revolve`, plus the slot step types.
Hole sub-types seen in `hole_callouts`: `thru`, `counterbore`, `tapped`, `blind`,
`countersink`. No `sweep`/`loft`/`rib`/`draft` has appeared in a real drawing;
`shell` appeared only in the frozen golden fixture (`GOLDEN-1`) as a MANUAL step.

| Missing feature | Evidence | Current handling | Needed in Phase 3 |
|---|---|---|---|
| `shell` | In the `FeatureType` enum + golden fixture; `PROHIBITED = {FeatureType.SHELL}` in `macro_generator.py`; no COM builder in `_BUILDERS` | VBA emits a numbered MANUAL step (`NN_Fxxx_MANUAL_shell.vba`); COM raises "not yet supported" | Real builder both paths (`Shell`/`InsertFeatureShell`?) |
| `sweep` (boss/cut) | Not in the `FeatureType` enum at all | Would fail schema validation / route to manual | Add enum + builder both paths |
| `loft` (boss/cut) | Not in the enum | same | Add enum + builder both paths |
| `rib` | Not in the enum | same | Add enum + builder both paths |
| `draft` | Not in the enum | same | Add enum + builder both paths |
| Hole Wizard sub-types as first-class | `HoleType` enum exists (thru/blind/counterbore/countersink/spotface/tapped) but every case is built as a cut-extrude, not a `HoleWizard5` feature | §3 | Dedicated `hole_wizard.py` module |

The standard mechanical set the task names — extruded boss/cut, revolved
boss/cut, fillet, chamfer, mirror, linear pattern, circular pattern, hole/slot —
is **already implemented**; the genuine gaps are **sweep, loft, rib, draft,
shell (currently prohibited/manual), and a true Hole Wizard hole family**.

## 3. How holes are handled today (stated plainly)

- There IS hole logic: `build_hole` (COM) and `_macro_holes` (VBA).
- The DEFAULT build is a **plain cut-extrude approximating a hole**, not a true
  Hole Wizard feature: `build_hole` resolves per-instance centers, then
  `_circular_cut_at` sketches a circle at each center and cuts it with
  `FeatureCut4` (through-all or blind). VBA does the same via a circle sketch +
  `FeatureCut4`.
- Hole sub-types ARE distinguished at the callout level (`HoleType`:
  thru/blind/counterbore/countersink/tapped/spotface), and `build_hole` adds
  sub-type geometry as extra cut-extrudes: a **counterbore** is a second
  concentric blind `FeatureCut4`; a **countersink** is a concentric conical
  relief (`_apply_countersink_relief`); a **tapped** hole drills the tap-drill
  diameter and leaves the thread cosmetic. These are geometric approximations,
  not `HoleWizard5` features.
- A real Hole Wizard path exists — `_try_hole_wizard` → `IFeatureManager.HoleWizard5`
  (dispid 222, 27-arg signature verified against the installed `sldworks.tlb`) —
  but it is **OPT-IN and default OFF** (`MTI_ENABLE_HOLE_WIZARD`). Reason
  recorded in-code: on SolidWorks 2024 `HoleWizard5` returned `None` for the
  diameter-driven legacy hole even on a clean part, so the proven sketch-cut is
  the default to avoid regressing the working build. `_wizard_hole_type` drives
  every case through `swWzdLegacy` (diameter-driven) to avoid the locale/data-pack
  fastener-table lookups. No standard-fastener-clearance sizing (ANSI Inch/Metric
  tables) exists — the wizard is used only in legacy diameter mode.
- Bolt circles: `build_circular_pattern_holes` builds ONE seed hole then a real
  `FeatureCircularPattern5` (fallback `...4`), reusing a single hole definition —
  so the "single callout with a multiplier" pattern is already modeled on the
  COM path when a concentric bore axis can be derived; otherwise it falls back to
  baked per-instance circle cuts.

**Bottom line for Phase 3b:** the sub-type *information* exists in the schema, but
the *builder* approximates every hole with cut-extrudes and the true `HoleWizard5`
path is disabled. Phase 3b builds a dedicated `hole_wizard.py` that turns the
sub-types into real wizard features with the correct enum constants.

## 4. Where the code distinguishes engine mode (vba vs com)

Engine is selected by `main.py --engine {vba|com}` and, in `batch.py`, both
resolve to the SAME primary build path: `build_sldprt_for_part` calls
`solidworks_builder.build_model` (COM) to build the `.sldprt`. The VBA package
(`macro_generator.generate_macro_package`) is generated in BOTH modes as the
portable, hand-runnable deliverable — it is not a separate build engine at
runtime so much as a second emission of the same build plan.

- There is **no per-feature `if engine == "vba"` branch** inside a builder. The
  split is structural: COM logic lives in `solidworks_builder.py` (one
  `build_<type>` function per feature), VBA logic in `macro_generator.py` (one
  `_macro_<type>` emitter per feature). A new feature type must add BOTH a
  `build_<type>` (COM) and a `_macro_<type>` (VBA), and register in the COM
  `_BUILDERS` dispatch and the VBA `SUPPORTED` set.
- `docs/solidworks-macro-error-log.md`: only **E004** (`GetModelBoundingBox`
  does not exist) is confirmed to affect the COM path; E006 and the others were
  fixed on the VBA generator side. So the two paths have genuinely diverged
  before — new code must be implemented and tested on both, or an explicit,
  logged reason given for skipping one.
- `HoleWizard5` is COM-only by nature (a live COM API); the VBA deliverable will
  emit the equivalent `FeatureManager.HoleWizard5` call in a macro. Any part of
  the hole family that cannot be expressed in a portable VBA macro (e.g. a
  fastener-table lookup needing the live data pack) must be called out.

## Phase 3 targets that follow from this audit
1. **Sweep, loft, rib, draft** — add to the `FeatureType` enum and implement a
   `build_<type>` (COM) + `_macro_<type>` (VBA) for each, registering in both
   dispatch tables. (These have not appeared in a real drawing yet, so they are
   coverage-completeness, guarded/skeleton where a profile+path can't be
   synthesized — matching the existing revolve/mirror "real-or-skeleton" pattern.)
2. **Shell** — promote from prohibited/manual to a real builder both paths.
3. **`pipeline/hole_wizard.py`** — a standalone module turning the existing
   `HoleType` sub-types into real `HoleWizard5` features (simple/tapped/cbore/
   csk/clearance), with a constants module for the enums, bolt-circle reuse, and
   the 5-vs-6 callout-vs-count escalation. Consumes `coordinate_normalize`, never
   reimplements it.
4. **Schema** — confirm/extend a hole sub-type enum so the wizard module can pick
   the right builder (the `HoleType` enum already covers this; §3c will verify it
   reaches the builder rather than being flattened to a generic `hole`).
