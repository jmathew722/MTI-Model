# Phase 3 — Implementation Notes (2026-07-24)

Implements the gaps from `docs/phase1-feature-audit.md` using the APIs researched
in `docs/phase2-research-notes.md`. All work is additive and both-engine; the
canonical coordinate resolver (`coordinate_normalize.py`) was NOT modified, and
the VBA/COM paths were NOT collapsed.

## 3a — Full feature coverage (both engine paths)

| type | COM (`solidworks_builder.py`) | VBA (`macro_generator.py`) | mode |
|---|---|---|---|
| `shell` | `build_shell` → `InsertFeatureShell(thk, False)` | `_macro_shell` (real call) | **real** (promoted from prohibited) |
| `sweep` | `build_sweep` | `_macro_coverage_skeleton` | real-or-skeleton |
| `loft` | `build_loft` | `_macro_coverage_skeleton` | real-or-skeleton |
| `rib` | `build_rib` | `_macro_coverage_skeleton` | real-or-skeleton |
| `draft` | `build_draft` | `_macro_coverage_skeleton` | real-or-skeleton |

- New `FeatureType` members `SHELL`(existing)/`SWEEP`/`LOFT`/`RIB`/`DRAFT` plus
  aliases (`sweep_boss`, `loft_cut`, `mold_draft`, …) in `_FEATURE_ALIASES`.
- COM `_BUILDERS` registers all five; VBA `SUPPORTED` adds all five and
  `PROHIBITED` is now empty (nothing silently dropped — an unsupported type still
  gets a numbered MANUAL step).
- **Real-or-skeleton** follows the existing `revolve`/`mirror` precedent: a
  sweep/loft/rib/draft needs a path / multiple sections / an open profile / a face
  selection that a single 2D sheet cannot supply, so absent that data it emits a
  `needs_review` skeleton macro carrying the extracted values — never a fabricated
  3D guess. Shell needs only a wall thickness (derivable), so it builds for real.

## 3b — Real Hole Wizard family

- `pipeline/hole_wizard_constants.py` — every `swWzd*`/`swEndCond*`/`swStandard*`
  enum BY NAME, resolved at call time from the installed typelib
  (`solidworks_builder._const`) with the recorded v32 (SW2024) integers as
  fallbacks. **No inline magic integers anywhere in the hole path.** Plus the ANSI
  Inch / Metric clearance-hole tables.
- `pipeline/hole_wizard.py` — one builder per sub-type
  (simple/tapped/counterbore/countersink/clearance) sharing `_invoke_wizard`, and
  a `build_wizard_hole` dispatcher. **Pure logic** (`plan_wizard_hole`,
  `resolve_clearance_diameter`, `reconcile_callout_count`) is separated from the
  live COM call so it is unit-tested off-Windows.
- **Bolt circle / hole pattern**: one `HoleWizard5` call over N pre-selected
  points reuses a single hole definition across the pattern (no once-per-hole
  calls); the proven seed-hole + `FeatureCircularPattern5` route in
  `build_circular_pattern_holes` is unchanged for the parametric-pattern case.
- **Placement** is cross-checked against `coordinate_normalize.validate_bounds`
  (`validate_centers`); this module writes NO coordinate math of its own.
- `solidworks_builder._try_hole_wizard` now delegates here. The path stays
  **opt-in (`MTI_ENABLE_HOLE_WIZARD`, default OFF)**: SW2024 returned `None` from
  `HoleWizard5` for the legacy hole (documented), so the proven sketch-cut remains
  the default until the version/locale Value-slot mapping is nailed down live.

## 3c — Schema hole sub-type reaches the builder

- `HoleType.CLEARANCE` added (additive). `hole_wizard.hole_subtype()` also
  **infers** a clearance hole from a thru/blind callout naming a standard fastener,
  so old JSONs without the member still route correctly. The sub-type is carried
  end-to-end (callout → `plan_wizard_hole` → enum), never flattened to a generic
  "hole".

## 3d — Callout-vs-count reconciliation (A050211E 5-vs-6)

- Pure `hole_wizard.reconcile_callout_count(qty, countable, fid)` returns a
  CRITICAL, build-blocking conflict object when a callout multiplier disagrees
  with the countable dimensioned positions.
- Wired into `resolver.py::_callout_count_flags` (fires only when ≥2 explicit
  `instance_positions` exist and their count ≠ `qty` — a deterministic
  drawing-vs-callout disagreement), producing a flag on `result.flags` with
  `source: "callout_vs_count"`.
- Routed through the EXISTING escalation surface: the engineering review lists it,
  and `human_assist.generate_assist_queue` turns it into a narrow question
  (ready-made gate text + the two candidate counts). No second escalation
  mechanism was invented.

## 3e — Tests

`tests/test_hole_wizard.py` (21 tests): constants resolution + fallbacks, the ANSI
clearance table, one planning assertion per hole sub-type, the `HoleWizard5`
27-arg shape, the pure 5-vs-6 reconciliation, the resolver flag end-to-end, and
the assist-queue routing; plus `TestFeatureCoverageBothPaths` proving the real
shell VBA call, the sweep skeleton, and the COM builder registrations. Updated the
three tests that asserted the old prohibited-shell behavior. Golden regenerated
(shell F004: MANUAL step → real shell macro). **Full suite: 869 passed.**
