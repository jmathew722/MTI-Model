# Phase 2 — Research Notes (2026-07-24)

Pointed at `docs/phase1-feature-audit.md`. Goal: close every gap Phase 1 found —
**sweep, loft, rib, draft, shell**, and a **true Hole Wizard** hole family — with
API signatures specific enough that Phase 3 implements directly from here.

## 2a. Hole Wizard — `IFeatureManager::HoleWizard5`

### Signature (verified against the installed `sldworks.tlb`, dispid 222)
Already recorded in `solidworks_builder.py`; restated with types:

```
HoleWizard5(
  GenericHoleType:  long   (swWzdGeneralHoleTypes_e)
  StandardIndex:    long   (swWzdHoleStandards_e; 0 for the legacy diameter hole)
  FastenerTypeIndex:long   (swWzdHoleStandardFastenerTypes_e; 0 for legacy)
  SSize:            str     ("" for legacy)
  EndType:          short  (swEndConditions_e)
  Diameter:         double (m)
  Depth:            double (m)
  Length:           double (m)
  Value1..Value12:  double (m; type-specific: cbore dia/depth, csk dia/angle, tap data)
  ThreadClass:      str
  RevDir:           bool
  FeatureScope:     bool
  AutoSelect:       bool
  AssemblyFeatureScope:  bool
  AutoSelectComponents:  bool
  PropagateFeatureToParts: bool
) -> Feature
```
`StandardIndex`/`FastenerTypeIndex` are **longs** — passing strings there raised
"Type mismatch" (-2147352571). Placement is by a pre-selected point sketch (one
point per center on the target face) before the call.

### Enum values — READ FROM THE INSTALLED TYPE LIBRARY (swConst v32 = SW2024)
Not hardcoded from docs (help.solidworks.com 403s on fetch, and integers must
match the build machine's version). Pulled live via
`solidworks_builder._ensure_sw_constants()` + `_const(name)` on this machine:

`swWzdGeneralHoleTypes_e`:
| name | value |
|---|---|
| swWzdCounterBore | 0 |
| swWzdCounterSink | 1 |
| swWzdHole (simple drilled) | 2 |
| swWzdPipeTap | 3 |
| swWzdTap (tapped) | 4 |
| swWzdLegacy (diameter-driven, no fastener table) | 5 |
| swWzdCounterBoreSlot | 6 |
| swWzdCounterSinkSlot | 7 |
| swWzdHoleSlot | 8 |

`swEndConditions_e`: Blind = 0, ThroughAll = 1, UpToSurface = 4, ThroughAllBoth = 9.
`swWzdHoleStandards_e`: swStandardAnsiInch = 0, swStandardAnsiMetric = 1.
(`swWzdHoleStandardFastenerTypes_e` members are data-pack/locale specific and are
NOT used — see the reliability note below.)

**Constants module plan:** `pipeline/hole_wizard_constants.py` defines each member
BY NAME and resolves its value at runtime via `_const(name, fallback)`, using the
integers above as documented fallbacks (so tests / off-Windows still work). This
satisfies "named constants, not inline magic numbers" AND "pull the actual enum
definitions for the version this pipeline targets" — the live typelib wins, the
fallback is the recorded v32 value.

### Legacy vs standard-fastener mode (reliability)
`_wizard_hole_type` today forces `swWzdLegacy` (diameter-driven) for every case
to dodge the fastener-standard/size table lookups (`StandardIndex`/
`FastenerTypeIndex`/`SSize`), which are locale/data-pack specific and the #1
source of wrong-size or failed wizard holes. Phase 3 keeps legacy as the default
placement path but ADDS the sub-type geometry (cbore/csk/tap) through the wizard
`Value1..Value8` slots and, for standard-fastener CLEARANCE holes, an ANSI-inch/
metric clearance-diameter table computed in `hole_wizard.py` (so the drill
diameter is standards-correct without depending on the live Toolbox data pack).

### HoleWizard5 vs 4, and the version pin
No version is pinned in `solidworks_builder.py` — `_ensure_sw_constants` discovers
the loadable swConst typelib (found **v32 = SW2024** on this machine) and
`_registered_typelib_versions` tries registry-advertised versions first. The
installed `sldworks.tlb` exposes `HoleWizard5` (dispid 222). So HoleWizard5 is
available and correct for the target; HoleWizard4 is the older-release fallback
(same leading arguments) and is used only if `HoleWizard5` is absent.

### Feature-data-object vs repeat-call, for bolt circles (A050211E)
For N similar holes on a bolt circle, calling `HoleWizard5` once per hole is both
slower and fragile (each call re-selects a face + rebuilds). The reliable pattern
— already proven in `build_circular_pattern_holes` — is **one seed wizard hole +
`FeatureCircularPattern5`** about a reference axis: a single hole definition
reused across the pattern, which is exactly how a flange is dimensioned (one
callout with a `(N)` multiplier, not N independent holes). The "Hole Wizard
Feature Data Object" copy pattern (get the seed's `IWizardHoleFeatureData2`, edit,
re-create) is an alternative for varying-parameter copies, but for identical
bolt-circle holes the pattern-of-seed is simpler and needs no data-object
round-trip. **Phase 3b uses seed + circular pattern for bolt circles.**

## 2b. Reference repositories (read for patterns; not runtime deps)

`reference/` is gitignored (added to both root and project `.gitignore`); nothing
here is committed into the pipeline or imported as a dependency.

- **xarial/codestack** — CLONED and read (`reference/codestack`). **License: MIT**
  (Copyright 2025 Xarial Pty Limited). Hundreds of real API/VBA macros. No direct
  `HoleWizard5` macro, but `document/features-manager/create-loft/Macro.vba` gives
  a real loft call: `FeatureManager.InsertProtrusionBlend2(False, True, False, 1,
  CONSTRAINT_DEFAULT, CONSTRAINT_DEFAULT, 1, 1, True, True, False, 0, 0,
  THIN_TYPE_ONE_DIR, True, True, True, swGuideCurveInfluenceNextGuide)`. Pattern
  reused as design reference for the loft builder's argument order; no code copied
  verbatim.
- **xarial/xcad + xcad-examples** — **License: MIT.** A .NET framework; the
  pipeline is Python/COM, so **not a dependency**. Read as design reference for
  how a mature framework structures feature-creation + rebuild-error handling
  (its macro-feature and `IFeatureManager` wrappers mirror the argument orders
  used here). Not cloned in this pass to keep the reference checkout small; its
  API-call shapes match the codestack/SW-API signatures already recorded.
- **pySolidWorks** (Python COM wrapper) — searched. It is a **thin, reference-only
  read**: our pipeline already wraps `IFeatureManager` directly through pywin32
  (`solidworks_builder.py`), which is the same mechanism, so there is nothing to
  import. **No code is imported from it**; any future reuse requires an explicit
  license check first (per the task constraint). Treated as reference-only.

## 2c. Full feature catalog — API call per missing type

Standard mechanical set cross-referenced against Phase 1 (✓ = already implemented,
do not re-research):

| feature | status | API call (COM) | notes / gotchas |
|---|---|---|---|
| extruded boss/cut | ✓ | `FeatureExtrusion3` / `FeatureCut4` | done |
| revolved boss/cut | ✓ | `FeatureRevolve2` | single REVOLVE type; needs a centerline + closed profile |
| fillet / chamfer | ✓ | `FeatureFillet3` / `InsertFeatureChamfer` | done |
| mirror | ✓ | `InsertMirrorFeature2` | needs a mirror plane + seed |
| linear / circular pattern | ✓ | `FeatureLinearPattern4` / `FeatureCircularPattern5` | done |
| hole / slot | ✓ approx | `FeatureCut4` (+ `HoleWizard5` opt-in) / `build_slot` | Phase 3b upgrades to real wizard |
| **shell** | **gap** (enum-present, prohibited/manual) | `FeatureManager.InsertFeatureShell(thickness_m, outward_bool)` — pre-select the face(s) to remove; empty selection = hollow all | rebuild-order: must run AFTER the base solid exists; a thickness ≥ half the wall self-intersects → rebuild error. REAL builder feasible (thickness + faces). |
| **rib** | **gap** (not in enum) | `FeatureManager.InsertRib(bothSides, flipSide, thickness_m, extrudeDir, refType, draft, draftOutward, draftWhileExtrude, flipDraft)` — needs an open sketch contour + an existing wall to tie into | 2D extraction rarely yields the rib's open profile → guarded/skeleton unless a profile is present |
| **draft** | **gap** (not in enum) | `FeatureManager.InsertMoldDraft2(draftAngle_rad, faceCount, reverse)` after selecting the neutral plane (Mark 1) + faces (Mark 2) | pure selection-context feature; no profile from a 2D sheet → guarded/skeleton |
| **sweep boss/cut** | **gap** (not in enum) | `FeatureManager.InsertProtrusionSwept4(...)` / `InsertCutSwept4(...)` — needs a profile sketch + a path sketch | 2D sheet gives neither a 3D path nor a swept profile reliably → guarded/skeleton |
| **loft boss/cut** | **gap** (not in enum) | `FeatureManager.InsertProtrusionBlend2(...)` (codestack-confirmed arg order) / `InsertCutBlend2(...)` — needs ≥2 profile sketches on different planes | 2D sheet rarely gives multiple loft sections → guarded/skeleton |

### Design decision for sweep/loft/rib/draft (honest scope)
These four have **never appeared in a processed drawing** (Phase 1 §2) and each
needs multi-sketch / path / selection context that a single 2D sheet extraction
does not provide. Phase 3 follows the pipeline's EXISTING "real-or-skeleton"
precedent (`_macro_revolve` / `build_revolve`, `build_mirror`): add the feature
type to the enum and a builder on **both** engine paths that (a) builds the real
feature when the required profile/path/selection data is present in the
extraction, else (b) emits a numbered MANUAL / `needs_review` step with the
extracted parameters — never a wrong-geometry guess, never a silent skip. This
closes the "no builder / hard error" gap Phase 1 flagged without fabricating 3D
geometry a 2D drawing cannot specify.

**Shell** is the exception: it needs only a thickness + faces, both derivable, so
it gets a REAL builder on both paths (promoted from prohibited/manual).

### Callout-vs-count reconciliation (A050211E, feeds Phase 3d)
The flange showed **5 countable holes vs a 6-hole callout**. Phase 3d adds an
explicit check in the hole module: callout multiplier ≠ countable pattern
instances ⇒ **build-blocking conflict**, routed to the SAME escalation path the
pipeline already uses (the `human_assist` / merge review queue + a CRITICAL
engineering-review flag), never a silent default to either number. This mirrors
the existing overview-count cross-check in `resolver.py`/`overview_analysis.py`
rather than inventing a second escalation mechanism.
