# MTI 2D → 3D SolidWorks Pipeline — FINAL PRESENTATION

### The complete system reference: every stage, every workstream, every UI sheet, every output file

*Source of truth: `CLAUDE.md`, `README.md`, `2D-3D-CAD-Test-Generation/README.md`, `docs/PRESENTATION.md`, `docs/PIPELINE_PRESENTATION.md`, `docs/NEWREAD.md`, and the current codebase under `2D-3D-CAD-Test-Generation/pipeline/` and `2D-3D-CAD-Test-Generation/webapp/`, current as of the 2026-07-17 dimensioning-architecture overhaul.*

---

## How to use this document

This is the **superset** document — it folds the CLI pipeline, every reliability workstream added since the original build, and the full web UI into one linear reference, ordered the way you'd actually walk someone through the system in a presentation:

1. **Part 0** — the one-sentence pitch and the guiding principle everything else serves.
2. **Part I** — the pipeline, stage by stage, in execution order, including every "workstream" and hardening pass bolted on since the original design (tiled extraction, reference geometry, slot decomposition, coordinate normalization, the dimensioning-architecture overhaul, deferred retry, reconciliation, geometric correction loop, human-assist).
3. **Part II** — the priority-tier model that makes every number traceable.
4. **Part III** — the web UI, sheet by sheet, including the two most recent additions (the Pipeline Explainer chat and the Visual Summary tables).
5. **Parts IV–VII** — outputs, module map, tests, and the reliability guarantees worth saying out loud in a room.
6. **Parts VIII–X** — how to actually run it, what it costs, and what a human still has to check.
7. **Part XI** — a suggested live-demo script.
8. **Appendix** — full docs index and repo map.

---

# Part 0 · The Pitch

> **In one sentence:** this system takes a folder of 2D engineering drawings (PDF, scanned JPG/PNG, DWG/DXF, or eDrawings), reads them with Claude Vision, resolves everything the drawing leaves ambiguous, builds a real SolidWorks `.sldprt` part (plus a portable VBA macro package and a C# equivalent), verifies its own output against the drawing and the operator's specs, and hands a human a single severity-ranked report of everything it assumed — all with the token cost logged per run.

## The Guiding Principle

> **A complete approximate model is always the correct outcome; an incomplete model is always the wrong outcome.**

Every design decision in this codebase serves this one rule. The pipeline **never refuses to build** because a dimension was unclear, a callout was ambiguous, or a feature type wasn't fully specified. Instead:

- Every unclear value is **resolved to a defensible number** — chosen from what was actually extracted (candidate readings, arithmetic chains, sibling features, operator specs), **never invented from nothing**.
- Every resolution is **tagged with a severity tier** (CRITICAL / HIGH / MEDIUM / LOW) and a **plain-English reason**.
- Every skipped or unsupported feature becomes an **explicit manual step**, never a silent drop.
- The engineer's job shifts from *"model this drawing from scratch"* to *"review the flagged assumptions and confirm them against the drawing"* — a five-minute read of one report instead of hours of manual CAD.

---

# System Architecture at a Glance

```
INPUT                    THE PIPELINE (any OS, pure Python)                    OUTPUT
─────                    ──────────────────────────────────                    ──────
PDF / PNG / JPG    ┐
DWG / DXF          ├──►  image prep                                       ┌──► .SLDPRT (Windows+SW2024)
eDrawings          ┘         │                                            │    .STL (any OS)
                              ▼                                            │    VBA macro package (any OS)
                    [1.2] tiled extraction (escalation only, large sheets) │    C# macro package (any OS)
                              │                                            │    engineering_review.txt
                              ▼                                            │    build_plan.json
                    [1.5] Stage 1.5 — Holistic Overview Analysis           │    resolved_extraction.json
                              │                                            │    verification_report.txt
                              ▼                                            │    constraint_verification.json
                    [2]   Per-view extraction (Claude Sonnet 5)            │    macro_result.json
                              │                                            │    token_usage_log.txt
                    [2.2] Vector hole extraction (DXF/DWG/vector-PDF)      │    lessons_learned.jsonl
                              │                                            │
                    [2.6] Spec Reconciliation (must-meet → MM-xxx)        │
                              │                                            │
                    [2.5] Ambiguity Resolution — the chief-engineer pass   │
                              │                                            │
                    [3]   Verification (advisory by default)              │
                              │                                            │
                    [6.5] Canonical Build Sequencer (fixed 7-stage order)  │
                              │                                            │
                    [4]   VBA + C# macro generation, statically audited   │
                              │                                            │
                    [4.5] CadQuery pre-validation (headless geometry)      │
                              │                                            │
                    [5]   SolidWorks COM build → .sldprt + .stl            │
                              │                                            │
                    [6]   Post-build constraint verification (measure STL)│
                              │                                            │
                    [10.5] Reconciliation Pass (checklist vs. build)       │
                    [10.6] Per-feature geometric verification              │
                    [10.7] Geometric correction loop (build→measure→fix)   │
                    [10.8] Human-assist escalation (narrow questions only) │
                              │                                            │
                    [11]  Final gate checks (overview + requirements)     │
                              │                                            │
                    [12]  Engineering Review (the one report to read)     │
                              ▼                                            │
                    [13]  Learning Loop → "Learning Loop/<Part>__ts.txt"  ┘

     THE WEB UI (FastAPI, port 8092) wraps main.py as a subprocess.
     The CLI is always the single source of truth — UI and CLI produce identical output.
```

---

# Part I · The Pipeline, Stage by Stage

Orchestrated by `main.py`; every stage lives as its own module under `2D-3D-CAD-Test-Generation/pipeline/`. Multi-view input (`--views-folder`) treats each subfolder as one part; view role comes from filename keywords (`front`/`side`/`top`…) or a leading `01`–`05`; a file named `full`/`overview`/`isometric`, or matching the folder name, is overview context only — never built as its own plane. **A front view plus one more orthographic view is required.**

## Stage 1 · Image Prep
**Module:** `utils/image_prep.py` · **Runs on:** any OS

Normalizes, orients, and downscales every input image to a max long edge (default **2576px**, tunable via `MAX_IMAGE_LONG_EDGE`). Emits warnings ("image appears nearly blank") that propagate all the way to the final engineering review.

## Stage 1.2 · Tiled High-Resolution Extraction *(Workstream 2 — escalation only, not the default path)*
**Module:** `utils/tiled_extraction.py`

For large-format sheets whose thin line work dilutes to sub-pixel at the standard raster cap — the "image appears nearly blank" failure class.

- **`should_tile()`** fires on **any** of: the blank-image heuristic, ink density < 0.5%, extraction confidence < 0.6, more than 25% of dimensions flagged unclear, or a C-size-or-larger page still at the ≤2576px cap.
- **`adaptive_render()`** re-renders the **vector PDF** at escalating DPI (300 → 600 → 900) until the median line width reaches ≥ 2.5px — a lossless zoom; it never upscales a raster image.
- A cheap global pass maps view boundaries, the title block, and datum candidates.
- **`make_tiles()`** cuts ~1500px tiles with **22% overlap**; each content-bearing tile is extracted in **sheet coordinates**, and blank margins are skipped to control cost.
- **`stitch()`** merges results by anchor + value; conflicting readings are kept as candidate `possible_values` for Stage 2.5 to resolve, never silently averaged.
- **`datum_anchor()`** re-expresses every position relative to the drawing's datum (feeding directly into Workstream 3's reference-geometry skeleton, below).

Tile cost logs as its own ledger line (`extraction_tiled`); tiles are cached by `(page hash, DPI, grid)`. The Vision-model calls are dependency-injected so all of this machinery is unit-tested without a single paid API call. Clean, small drawings never touch this path — they keep the cheap single-shot flow.

## ★ Stage 1.5 · Holistic Overview Analysis
**Module:** `overview_analysis.py`

Before any cropped view is individually extracted, the **full uncropped sheet** (the "FULL OVERVIEW VIEW" image / `00_full.jpg`) goes to Claude Sonnet 5 with a dedicated **relational** prompt. This stage does **not** re-extract dimensions — it answers what a single cropped view structurally cannot:

- What views are on the sheet, and what is each one?
- Which features **correspond across views** — e.g. does the 3.880 DIA bore in the front view line up with full-height hidden lines in the side view (→ a **through**-bore, not blind)?
- What is the **overall 3D shape** implied by combining every view?
- Is anything **visible in one view but absent or contradicted in another**? Each conflict gets a severity and a concrete recommendation.
- Does the part exhibit **symmetry** that should constrain patterning — verified against the actual dimensioning style, never assumed?
- What do **global notes** govern — e.g. "(6) HLS" — including a `resolved_count`?

**Output:** `overview_analysis.json`, fed into Stage 2.5 as **priority tier 2** (see Part II). Conflicts, plus a deterministic callout-count cross-check, become flags tagged `source: overview_analysis`. **Purely additive** — no API key or any failure simply skips the stage; `--from-json` reuses a sibling `overview_analysis.json` at zero cost. Token cost is its own ledger line (`stage_1_5_overview_analysis`). Surfaced in the UI as the collapsible **Overview Analysis** panel, live during a run.

**Why it exists — the canonical failure class:** a callout says **"(6) HLS"**, but only 5 holes render clearly in the cropped front view. A per-view-only pass would silently build a 5-hole part. The holistic pass instead flags this **CRITICAL** — *"check for an occluded hole behind the title block"* — before a wrong part is ever built. (Real case: drawing **A050211E**.)

## Stage 2 · Per-View Extraction
**Module:** `extractor.py` · **The only paid step** (model: `claude-sonnet-5`, override via `EXTRACTION_MODEL`)

One Claude Vision call per part, covering every labeled view, via a **forced tool call** validated against a strict Pydantic v2 schema (`schema.py`), with one repair retry.

- **Specs-first:** the operator's must-meet specifications are read *before* extraction and injected into the prompt, so the model actively hunts for those features from the very first pass — not just checked at the end. The spec text is part of the extraction cache key, so a changed spec forces a fresh (paid) extraction.
- **Multi-view coherence:** all views go to Claude in one call, labeled by view, so each feature's sketch plane ties to the view it was actually read from, and a feature seen in two views isn't double-counted.
- **Cost controls:** the system prompt + tool schema (~4.6k tokens) and images use `cache_control` so later calls in a batch read the prefix from cache; an on-disk **extraction cache** (`<output>/.extraction_cache/`) returns an identical image+model result at **zero API cost** — this is why a stable `--output` path is the standard advice, since re-runs become free.
- Raw extraction JSON is **always saved**, whether the run ends READY or not — nothing paid is ever lost. Token/USD cost appends to `token_usage_log.txt` via `usage_log.py`.

## Stage 2.2 · Vector Hole Extraction
**Modules:** `vector_extract/` + `hole_resolution.py`

Exact hole positions pulled straight from the **original vector file** — DXF/DWG entities via `ezdxf`, vector-PDF Bézier circles via PyMuPDF, `HoughCircles` as the raster fallback for scans.

**Precedence rule:** vector geometry **owns position**; the vision callout **owns semantics** (diameter / thread / depth). A disagreement between the two keeps **both** readings and flags **CRITICAL** — never silently picks one. Every hole carries `position_source` and `position_confidence`.

## Stage 2.6 · Spec Reconciliation ("Must-Meet")
**Module:** `must_meet.py`

The operator's MUST-MEET text (`must_meet_spec.txt` — the amber box in the UI; the legacy `notes.txt` still works) is parsed into structured **`MM-xxx` constraints** (`must_meet_constraints.json`) via a dedicated Claude call **with a deterministic regex fallback that needs no API key at all**.

- **Constraints are priority tier 0** — they override vision-extracted values on any conflict. Every override is logged to `lessons_learned.jsonl` (`resolution: spec_override`) — never silently dropped.
- Missing geometry parameters are derived directly from the extraction — e.g. a bolt-circle fit: `radius = mean(√(x²+y²))` about the hole centroid; radii disagreeing by more than 0.005in is flagged **CRITICAL**.
- **Exception carved out deliberately:** if the spec states a hole **count** that contradicts explicitly dimensioned drawing positions, the drawing's actual geometry wins, the conflict is flagged **CRITICAL**, and the MM check is allowed to **FAIL** with measured-vs-required values (`spec_vs_drawing_disagreement`) — the spec doesn't get to silently overwrite geometry that's plainly drawn.

## Stage 2.5 · Ambiguity Resolution — "the chief-engineer pass"
**Module:** `resolver.py` — **the heart of the system**

This is the core design decision of the entire pipeline: **it never blocks on ambiguity.** Every unclear, under-dimensioned, or unknown-position value is walked through a deterministic decision tree and assigned a `resolved_value` chosen only from what was actually extracted — never fabricated:

| Step | Logic | Tier |
|---|---|---|
| 0. Spec-driven | an operator must-meet value that clarifies the ambiguity | (tier 0, `spec_driven`) |
| 1. Arithmetic chain | the only reading that closes a dimension chain within tolerance | **HIGH** |
| 2. Geometric validity | eliminate readings that can't physically fit | **MEDIUM** |
| 3. Conservative geometry | among survivors, prefer the smallest / shallowest | **LOW** |
| 4. Last resort | derive from an adjacent dim, default depth → through-all, radius → general tolerance, or center on parent | **CRITICAL** |

**Commit-to-extraction mode** (`commit_mode`, **default ON**): there is no human in the loop at build time — the pipeline commits to the extraction and **builds every extracted feature**. `EXCLUDED_INCOMPLETE` and `needs_markup_review` are no longer terminal states for anything buildable:

- **Positional dimensions are consumed before any escalation** (`_feature_positional_xy`). This closes the historical "Bug-1" root cause, where a positional `applies_to` label (`slot_offset` / `hole_position_x` / `position`) canonicalized to an empty string and a fully-extracted location was dropped on the floor, defaulting to `[0,0]` / `needs_markup_review` (real case: **158-C**, `D002 = 1.56`).
- A step/notch cut missing length+width has its rectangle **derived from the outer-profile envelope minus its partial anchor dims** (`_derive_profile_delta`, `basis: profile_delta` — real case: **M_121-B** F002/F003).
- A hole missing a diameter **inherits its most-common sibling's diameter** (`_sibling_diameter` — real case: M_121-B F005 → `.422`).
- A genuinely undimensioned size/position commits a **declared-basis conservative value/placement** (`committed_conservative`, never `[0,0]`) — applied, built, and flagged **CRITICAL** with the value, its basis, and the empty resolution "rungs" it fell through.
- `commit_mode=False` restores the old exclude/review behavior, kept for comparison.
- The **only remaining hard failure** is the pre-existing one: no closed outer profile at all.
- Non-committable **edge/pattern treatments** (a fillet/chamfer with no size, a pattern with no count+spacing) still stay excluded under both modes — the system draws the line at fabricating a purely cosmetic value.

**Per-instance hole placement + datum chaining** (real case: **A001271E**): `_classify_hole_groups` classifies every hole group as either:
- **`placement: pattern`** — only with **hard evidence**: a bolt-circle or a uniform pitch with one single owning feature, or
- **`placement: individual`** (the **default**) — each instance owns its own resolved coordinate.

Every hole group records `pattern_evidence` and, for individually-placed holes, a per-instance **`position_basis` datum chain**: `[{anchor: left_edge|top_edge|hole_center|origin, dim, value, axis}]`, where `anchor: hole_center` points at a `REF_PT_<fid>` reference-geometry datum point. **The bias is deliberately toward `individual`** — an individual group misbuilt as a pattern produces *wrong geometry*, while a real pattern built as individuals is merely more (correct) lines.

## Stage 3 · Verification
**Module:** `validator.py`

Checks dimensional closure, unit consistency, view consistency, and feature feasibility; computes a drawing-completeness score. **Advisory by default** — Stage 2.5 has already resolved everything, so a failing check is annotated, not blocking. `--strict-gate` restores the old hard-blocking behavior.

## Stage 6.5 · Canonical Build Sequencer *(2026-07-10 redesign)*
**Module:** `build_sequencer.py`

The **one** deterministic build-order pass, called once at the top of `generate_macro_package`. Re-orders every feature that survives the completeness gate into a fixed **seven-stage** sequence:

```
0 reference geometry  →  1 base solid (largest closed profile)  →  2 additive bosses
   →  3 profile subtractions  →  4 holes (plain → cbore/csk → tapped)
   →  5 patterns  →  6 chamfers-then-fillets  →  7 non-geometric
```

with a **stable within-stage sort keyed to feature id**, so `build_order` is **byte-identical across runs**.

**No type-based omission:** every single feature ends in exactly one of three states, recorded in `<Part>_build_dispositions.json` (also nested in `build_plan.json`):
- `BUILT`
- `BUILT_WITH_DERIVED_VALUE` (resolver-inferred value)
- `EXCLUDED_INCOMPLETE` (gated out, with the missing parameter named — in commit-mode this is reached only by non-committable edge/pattern treatments)

Because the same `model` object flows onward from here, **macros, `build_plan.json`, CadQuery pre-validation, and the actual COM build all inherit this exact order.**

**Generation-time invariants enforced here / at macro-generation time:**
- **Bug-1 invariant** (`macro_generator._assert_no_dropped_positions`) — generation **REFUSES** (`MacroGenerationError`) to emit a build whose disposition marks a position unresolved while a positional dimension for that feature actually exists.
- **Per-instance hole placement** (`macro_generator._hole_feature_positions`) — when a qty>1 callout attaches to one feature while sibling features of the same diameter also exist, those siblings **are** the other instances; each feature drills exactly the one position it owns, never the whole shared layout duplicated on top of the siblings (real case: A001271E's 4 asymmetric inner holes).
- **`_assert_no_overlapping_holes`** — refuses a plan that places two same-diameter instances within half a diameter of each other (the "collapsed instance" bug class).
- **Macro-package dedup** — `macros/` is cleared of stale `*.vba` every run, and a duplicate feature id in the build order is refused outright.
- Hole-to-hole datum points are emitted as `REF_PT_<fid>` reference points in `01a_reference_geometry.vba` **before** the Stage-4 holes that reference them.

## Stage 4 · VBA & C# Macro Generation + Static Audit
**Modules:** `macro_generator.py` + `macro_audit.py` (+ `csharp_macro.py`)

Writes numbered VBA macros (`00_setup` … `ZZZ_export_stl`, `RUN_ALL.vba`); features on the prohibited list (loft, sweep, boundary, shell, draft, surfacing, helical threads) become explicit `NN_Fxxx_MANUAL_*.vba` steps rather than vanishing. **Every macro is statically audited before it is written** — a banned/nonexistent SolidWorks API call fails generation outright.

This stage has accumulated several dedicated reliability sub-layers, each shipped as its own dated addition:

### Circular-Pattern Reliability Layer
A hole group routed to `circular_pattern` (spec says so, or the drawing dimensions it polar-style; requires a concentric bore to derive the axis) emits **three** macros in sequence: seed hole (`Fxxx_SeedHoleCut`) → a named reference axis (`PatternAxisN`, via `InsertAxis2` off the bore's cylindrical face) → the pattern itself through the single `CreateCircularPatternSafe` helper (version-pinned `FeatureCircularPattern5` signature read from the installed `sldworks.tlb`, with a fallback to `...4`; axis at Mark=1, seed at Mark=4; a hard `Nothing`-check stop). `total_instances` **includes the seed** — asserted once in the canonical build-plan schema; generation refuses if any canonical field is null.

### Workstream 3 — Reference-Geometry Datum Skeleton *(2026-07-11, `reference_geometry.py`)*
**Before any feature**, `01a_reference_geometry.vba` builds the drawing's own datum structure as **named** SolidWorks reference geometry: `REF_DATUM_A` (always present) + `REF_DATUM_B/C` (from GD&T/dimension `datum_ref`) + `REF_SYM_X/Y` (symmetry) + `REF_AXIS_*` (concentric/circular axes) + `REF_PT_<fid>` (pattern origins), via `InsertRefPlane`/`InsertAxis2`. `build_plan.json` gains a `reference_geometry[]` block, and each feature step gets a `positioned_from` handle. This is **purely additive** — the proven absolute-coordinate build stays the audit trail and fallback; the skeleton adds human-readable landmarks and stable named selection handles for the deferred-retry loop.

### Canonical Slot / U-Notch Decomposition *(2026-07-11, `slot_cut.py`)*
A U-shaped cutout, open notch, or slot is **never** built as one arc-bearing sketch. It is always decomposed into exactly two ordered, adjacent build steps:
1. **`slot_rect_cut`** — a rectangular through-cut at the dimensioned position (`must_complete: true` — 4 lines / 4 dims is near-unfailable, and this carries the slot's true position + size), immediately followed by
2. **`slot_corner_fillet`** — constant-radius fillets on the rectangle's interior corners (`defer_on_failure: true`, routed through the deferred-retry queue — a fillet failure never destroys the already-correct slot).

`corner_array()` is the single source of truth both the rectangle sketch and the fillet edge selection derive from. The Stage 2.5 resolver validates fit (anchor + width ≤ extent), radius (2R ≤ width, R ≤ depth), anchor semantics, and through-all-from-a-single-view — a geometry violation is **CRITICAL** with a ready-made clarification-gate question, **never silently clamped**.

### Stage-7 Hardening *(2026-07-12, four generation-time guards)*
1. **Macro echo check** (`macro_echo.py`) — every emitted geometry literal is parsed back out of the VBA and must round-trip to the build-plan value for the **same** feature. A literal matching a different feature's value is `cross_contamination`, one matching nothing is `orphan_literal`, a planned position never emitted is `missing_value` — any of these raises `MacroEchoError`.
2. **Template-based emission** (`macro_template_engine.py` + `macro_templates/*.vba.tmpl`) — the circle/rectangle primitives are filled from **exactly one** feature's record; `fill()` is strict both ways (a missing placeholder *or* an unused key both raise `TemplateFillError`), so a template structurally cannot reference another feature's data.
3. **Open-edge overshoot** — an open notch's open side is deliberately pushed *past* the part edge by `EDGE_OVERSHOOT_EPS = 0.050`; `_assert_open_edge_overshoot` refuses a cut whose open-axis span exactly equals its depth (the coincident-with-edge bug that built an enclosed window instead of an open notch — real case: 158-C).
4. **Label/payload agreement** — `_assert_label_payload_agreement` refuses a step whose description names a foreign feature id.

Plus a **falsy-basis sweep** — an assumption with a blank basis now reads as `BUILT_WITH_DERIVED_VALUE ("unspecified_basis")`, never as directly-extracted. And a **fully-defined gate** — `ReportSketchStatus` logs PASS/WARN after `FullyDefineSketch` so under-defined sketches are observable, not silently accepted.

### Centralized Coordinate Normalization *(2026-07-13, `coordinate_normalize.py`)*
The **one** place semantic drawing anchors become global CAD coordinates, so the UI table and the VBA can never disagree. An explicit `Anchor` enum (`TOP_EDGE`/`BOTTOM_EDGE`/`LEFT_EDGE`/`RIGHT_EDGE`/`LOWER_LEFT`/`LOWER_RIGHT`/`UPPER_LEFT`/`UPPER_RIGHT`/`CENTER`/`DATUM_POINT`/`DATUM_AXIS`/`FEATURE_RELATIVE`/`ABSOLUTE_GLOBAL`) plus `resolve_notch_anchor` (the single locus of the `y = parent_height − depth` math), `resolve_point_anchor`, `validate_bounds`, and `assert_edge_orientation`. A single `INCH_TO_M = 0.0254` conversion is applied only at the VBA boundary. A generation-time guard (`macro_generator._assert_notch_orientation`) re-checks every built open-edge slot against the real parent envelope and refuses a top-edge notch resolved to `y = 0..depth` (the exact bug class this fixed on 158-C). The Three.js viewer applies **no** compensating orientation flip — correctness lives entirely in the geometry.

### Dimensioning-Architecture Overhaul *(2026-07-17, `position_solver.py` — the newest layer)*
The philosophical shift: the macros are a **faithful execution of the drawing's own dimensioning scheme**, not a coordinate dump. Every positioned feature may now carry `anchors: list[PositionAnchor]` — `scheme` (chain / baseline / ordinate / coordinate / polar_bsc / datum_frame), `anchor_ref` (part edge, origin, another feature, a datum hole, a datum-reference-frame letter), `dimension_ids`, `axis`, `value`, and `semantics` (to-near-edge / to-center / to-far-edge / true-position).

`position_solver.py` is the **sole coordinate authority** for anchored features: a topological solve over the anchor graph (chains accumulate in drawing order; polar coordinates resolve as `center + r·(cos,sin)`; far edges measure back from length/width), emitting a **human-readable derivation trace** per feature — e.g. `"x = part_edge_left(0) + D002(1.56) [baseline]"`. An anchorless feature is treated as the degenerate `coordinate` case, so legacy behavior is the base case (goldens byte-identical). A cycle or unresolvable anchor falls back to stored offsets, `grounded=false`, and a MEDIUM flag — **never blocks**.

**Datum-frame selection priority:** a datum-hole pair beats a declared `dimension_origin` (ordinate zeros / origin symbol / datum letters), which beats the default lower-left-corner fallback. Explicitly-anchored features get a `---- DIMENSION ANCHORS` comment block written into their VBA, enforced by `macro_audit.check_anchor_annotations` (a missing annotation is a `MacroGenerationError`).

**Correction propagation:** `position_solver.movers(model, changed_dim_ids)` names exactly the features anchored — directly or transitively — to a changed dimension, so a correction only moves what's actually downstream of it.

**Anchor fidelity** (feeds Stage 10.6, below): `feature_verify.verify_anchor_fidelity` re-measures each anchored feature **relative to its own anchor** (edge / chain target / polar center), catching the "right hole, measured from the wrong edge" class of compensating error that a plain absolute-XY check would miss entirely.

### Overview-Analysis Macro Validation *(2026-07-16, `overview_macro_validate.py`)*
After the echo check, the **package as a whole** is validated against the Stage 1.5 overview words. A global note's `resolved_count` must equal the hole instances the macros actually drill (baked circles + circular-pattern copies, seed counted once) — short is **FAIL CRITICAL**, over is **WARN**. Every cross-view correspondence must match at least one build step on canonicalized vocabulary (a bore/HLS/drilled → hole synonym map; sheet-furniture terms like `title_block` are skipped). A relation confirming **through** must not meet a **blind** step (**FAIL CRITICAL**). Advisory by default — never blocks; writes `<Part>_macro_overview_validation.json` and folds FAILs into the engineering review.

### C# Macro Companion Output *(2026-07-16, `csharp_macro.py`)*
Every package **also** emits `macros_csharp/` — a SolidWorks-compatible C# console program built from the **same** `BuildStep` data as the VBA (never transpiled), late-bound COM, mirroring the same verified `FeatureExtrusion3` / `FeatureCut4` / `FeatureCircularPattern5` calls. Emission is self-echo-checked (`CSharpEmitError` on a dropped literal) and never touches `macros/` — the VBA remains the canonical build path; this is a parallel, verified alternative for teams that prefer C#.

## Stage 4.5 · CadQuery Pre-Validation
**Module:** `cq_prevalidate.py`

Builds the **same** geometry headlessly from `build_plan.json` (circular patterns via `.polarArray(...)` + `cutThruAll`), checks watertightness, volume, and hole counts against every must-meet constraint. **A failed check aborts the SolidWorks build** and surfaces the exact constraint (`"MM-001 FAILED: …"`) before SolidWorks is even touched. Writes `prevalidation.stl` + `prevalidation_report.json`; degrades to a graceful no-op if `cadquery` isn't installed.

## Stage 5 · SolidWorks COM Build
**Modules:** `solidworks_builder.py` + `model_validator.py` · **Windows + SolidWorks 2024 only**

Drives SolidWorks directly over COM to produce the real `.sldprt` + STL export + mass/bbox check. Features are renamed deterministically right after creation; per-feature outcomes are written to `macro_result.json` (JSON Lines) so a failure surfaces as the *exact feature*, never a generic exit code.

- **Optional `HoleWizard5` path** (`MTI_ENABLE_HOLE_WIZARD=1`, **default OFF**) — real diameter-driven legacy wizard holes with a verified fallback to the proven sketch-circle cut. The 27-arg signature is confirmed against the installed `sldworks.tlb`, but SW2024 returned `None` even on a clean part in testing, so it stays default-off until the version/locale-specific parameter mapping is confirmed live.
- **Workstream 1 — Deferred Feature Retry** *(2026-07-11, `deferred_retry.py`)*: in non-strict mode, a hard feature failure no longer skips forever. The feature is **quarantined** and the build **continues**; once the rest of the solid is complete, deferred features are retried (cap 3) with the finished-solid topology now available as context, using an escalating failure-classification playbook (`classify_failure` → selection / sketch-under/over-defined / zero-thickness / missing-parent / COM-timeout / param-out-of-range — each retry attempt changes strategy, never repeats one). A recovered feature becomes `BUILT`; a still-open one ends `deferred_open` with a ready-to-answer clarification question folded straight into the human-assist queue — **never a silent skip**.

Everything upstream of this stage runs on any OS; this is the one Windows-only, SolidWorks-only step.

## Stage 6 · Post-Build Constraint Verification
**Module:** `constraint_verify.py`

The **built STL** is measured with `trimesh` (cross-section circle fitting; through-all detection = the hole appears near both faces), and every MM constraint is graded **PASS/FAIL with measured vs. required** values. **A run with must-meet constraints is only READY when every one of them passes.** Every failure is appended to `lessons_learned.jsonl` with the responsible VBA snippet attached.

## Stage 10.5 · Reconciliation Pass *(2026-07-10 self-correcting-loop upgrade)*
**Module:** `reconciliation.py`

The pipeline's own closing check of its output against the **original raw** `_extraction.json` — deliberately never the resolved/downstream artifacts, which could themselves be hiding the bug. Builds a ground-truth checklist (every feature id + its expected instance count) and diffs it against the disposition table and the build plan's actual instance positions. A justified `skipped_prohibited` is accepted; anything else missing or short is named exactly.

On a gap, it re-runs **only** `resolve_extraction` (never the paid extractor) with every requirements/overview signal freshly reloaded from disk, up to `max_passes` (default 3). Since the resolver is a deterministic pure function, a pass that recovers nothing new stops the loop immediately. A recovered feature is spliced into the **existing** `build_plan.json` and a new `RECONCILE_pass<N>_*.vba` is added to `macros/` — no existing macro is renumbered or touched (the already-built `.sldprt` is *not* hot-patched; a full rebuild is needed and the report says so explicitly).

Writes `<Part>_reconciliation_report.json` (`checklist_total`, `confirmed_built`, `loop_passes_used`, `unresolved[]`, `splices_applied[]`, `final_status: READY | READY_WITH_OPEN_ITEMS`). Any unresolved item gates the run's binary READY/NOT READY status (exit code 8).

## Stage 10.6 · Per-Feature Geometric Verification *(2026-07-10 accuracy layer)*
**Module:** `feature_verify.py`

Where Stage 6 grades the STL against operator constraints only, this measures **every planned feature** against `build_plan.json`: each hole's position + diameter + through/blind, each profile cut/notch's location (a material-absence probe), each slot's obround, and the base envelope (plus a COM-vs-CadQuery volume cross-check). Every feature ends with exactly one classification into `<Part>_feature_verification.json`:

`OK` · `MISSING` · `MISPLACED` (with measured position) · `WRONG_SIZE` (with measured size) · `EXTRA` · `UNMEASURABLE` (always with a stated reason).

Also folds in **anchor fidelity** (`anchor_fidelity[]`) from the Stage-4 dimensioning-architecture work, above.

## Stage 10.7 · Geometric Correction Loop *(Phase B)*
**Module:** `reconciliation.py::geometric_correction_loop`

Wraps Stage 10.6 in a bounded **build → measure → correct → rebuild** loop (cap 3). Correction policy by class:
- A **systematic** transform error (origin offset / axis swap / uniform scale — detected only when ≥2 features share one consistent error) is corrected once and pre-compensated on every affected step.
- A one-off `MISPLACED` re-emits the single step with the **resolver-derived** position — the drawing is truth, never the raw measured value.
- `MISSING` / `EXTRA` / unresolvable `WRONG_SIZE` are flagged, **never fabricated**.

Terminates on all-PASS (READY), the retry cap, no applicable correction (stops immediately, deterministic), or **oscillation** — a previously-PASS feature regressing stops the loop rather than thrashing. Writes `<Part>_geometric_loop_report.json`. The COM builder is dependency-injected, so this entire loop is unit-tested without needing SolidWorks installed.

## Stage 10.8 · Human-Assist Escalation *(2026-07-10)*
**Module:** `human_assist.py`

The exit ramp for when the fully automated ladder is genuinely exhausted. A feature/dimension becomes a question **only** after all four automated stages have failed: the resolver's plausibility ladder → TYP/derivation → the Phase-B correction loop (3-pass cap) → Phase-D method experiments (for chronic construction-method issues).

Each eligible item becomes a **narrow question object** — one sentence, pre-populated candidates with basis, a tight region crop, and a **`default_if_unanswered` that is always populated** — the best-available value ships regardless. Capped at 3 by default, prioritized by leverage (a base/envelope dimension outranks one slot's radius). **This never blocks:** a pending question is an additive `NEEDS_HUMAN_INPUT` overlay flag — the part still produces its full approximate model and its normal READY status; questions do not gate READY.

Persists to `<Part>_assist_queue.json`. **Answer feedback** (`apply_answers`/`rerun_with_answers`) feeds a human's answer back as the resolver's *highest-priority* candidate (above even spec-driven values) and re-splices the feature — no paid re-extraction. `learned_patterns.py` generalizes a recurring ambiguity answered the same way across ≥2 parts into `LEARNED_PATTERNS.md` — a priority bias for future runs, never an auto-applied number.

## Stage 11 · Final Gate Checks
**Modules:** `overview_check.py` + `requirements_check.py`

- `overview_check.py` re-examines the part's overview drawing **alone** and diffs it against the finished build — a visible feature that's missing is **CRITICAL** and gates READY.
- `requirements_check.py` grades every operator must-meet note `met` / `partial` / `unmet` / `not_applicable` — an unmet line gates READY.

Both are overridable (`--skip-overview-check` / `--skip-requirements-check`) — the model and macros are still produced in every case; only the READY status changes.

## Stage 12 · Engineering Review
**Module:** `engineering_review.py`

**The single severity-ranked human report** (`<Part>_engineering_review.txt`), regenerated after the COM build so skipped features are included too. Folds in every assumption, every Stage 1.5 conflict, every skipped/manual feature, and every requirement grade, CRITICAL first, in plain language: what was ambiguous, the decision made, why, and what it affects. **This is the one file to read first.**

## Stage 13 · The Learning Loop
**Module:** `learning_loop.py`

At the end of every run, reads the run's artifacts and writes one plain-text failure report to the repo-root `Learning Loop/<Part>__<timestamp>.txt` — gate reasons, MM constraint failures, Stage 1.5 conflicts, **every** engineering-review flag at every severity, and macro/build failures — ending in a paste-ready **"FIXES FOR FABLE"** brief with a suspected code area per failure. `Learning Loop/INDEX.md` logs one line per run. This is how a human hands the model's own mistakes back to an AI coding assistant to plan the next generalized fix. Exception-safe — never breaks a run.

---

# Part II · The Priority-Tier Model — why every number is traceable

When information sources disagree, resolution follows a **fixed, recorded** priority:

| Tier | Source | Authoritative on |
|---|---|---|
| **Tier 0** | Operator must-meet specifications | Everything — human-authored intent wins |
| **Tier 1** | Per-view extraction (+ vector geometry) | Individual dimension **values** and exact positions |
| **Tier 2** | Stage 1.5 holistic overview analysis | **Cross-view relationships** — through vs. blind, symmetry, counts, whole-part consistency |

Every resolved dimension and every flag carries **`resolved_by_tier`** (`tier0_spec` / `tier1_per_view` / `tier2_overview`), so for any number in the final model you can answer *"why this value?"* directly from the run artifacts, with no guessing. Tier 2 never silently overwrites a tier-1 value — it only adds flags a cropped view could never raise on its own.

---

# Part III · The Web UI, Sheet by Sheet

`app.py` (FastAPI, port **8092**) is a thin wrapper that **runs `main.py` as a subprocess** (`--views-folder <part> --output <part>/output`) and streams its console live. **The CLI remains the single source of truth** for all pipeline behavior — the UI never re-implements logic. Frontend is one `index.html`, four sheet-tabs, vanilla HTML/CSS/JS with **zero CDN dependencies** (Three.js, pdf.js, and the DrawingCrop app are all vendored in-repo) so every clone renders identically.

### Design system *(NEWREAD.md — the graphite / copper / slate-teal pass)*
A dark, single-theme, Fusion-360-style workspace: **graphite** surfaces in lightness steps only (`#101216` wells → `#282D34` hover), **copper** `#C9762A`/`#D98B3F` as the *one* interactive accent (buttons, active tabs, the READY banner — roughly 10% of any screen), **slate-teal** `#4A7A78` for secondary/informational states, warm off-white ink in four hierarchy tiers, and a fixed **severity ladder** (CRITICAL `#E5484D` · HIGH `#E29A3C` · MEDIUM `#D4C14A` · LOW `#8FA0B2`) used identically everywhere severity appears. Depth is borders only — no drop shadows. Mono type for every technical value (dimensions, paths, JSON, console, token counts), always `tabular-nums`. Single stylesheet of tokens (`static/design-tokens.css`) shared by both the host app and the Tab-1 cropper.

### Sheet 1 · Drawing Crop & Preprocessing Markup
The intake surface. Two modes:
- **✂ Crop views** — load a multi-view sheet and crop each orthographic view into its own image; crops queue and pull into Sheet 2 with one click. The uncropped sheet is preserved as the **Full Overview View**, which powers the Sheet-2 drawing viewer, the Stage 1.5 analysis, and the post-build overview cross-check. Accepts PDF, JPG/PNG, **and DWG/DXF/eDrawings directly** — CAD formats convert server-side automatically through `/api/convert-dwg` (engine chain: ezdwg → SolidWorks translator → ODA), cached in `.convert_cache/` and logged to `conversion_log.jsonl`.
- **✎ Mark regions** *(historical note: superseded)* — an earlier iteration supported per-view crop + colored markup boxes feeding a `reference_regions.json`/datum-placement scheme. **This has been superseded — there is no region-markup, datum-tool, or "Marked View" intake type in the current UI.** The model reads every view straight from the sheet itself, with no human crop/markup preprocessing step.

**Correction & re-run** — the foot of the Sheet-3 Pipeline tab (not Sheet 1) carries a feedback box: describe what's wrong ("the 6th bolt hole is missing") and **↻ Re-run with correction** appends the note to `must_meet_spec.txt`/`notes.txt` as an authoritative `CORRECTION (…)` line and forces `--no-extract-cache` — a tight human-in-the-loop fix cycle without leaving the app.

### Sheet 2 · Part Setup & 3D Model
Where a part is assembled and the finished model is inspected. Three input groups, then a two-panel viewer:

1. **Add images** — upload a single drawing or a whole parts folder, or pull queued crops from Sheet 1. Multi-sheet PDFs get a sheet picker.
2. **Assign view types** — exactly eight options: **Front, Back, Left Side, Right Side, Top, Bottom, Full Overview View, Marked View** (with rotation for sideways scans). **Front + one more orthographic view is required to save** — the front view defines the base profile the part extrudes from.
3. **Name & save + Must-Meet Specifications** — the part name becomes the folder name, saved in the exact layout the CLI consumes (UI and CLI are always interchangeable). The amber **must-meet** box here is free-text, human-authored, authoritative requirements — persisted as `must_meet_spec.txt` and enforced through the entire pipeline as tier 0.

**Viewer (bottom split):**
- **Left — Full Overview View**: the complete original drawing, zoom/pan/reset.
- **Right — 3D Model (STL)**: orbit/zoom/pan, with a **Select Model dropdown** that loads *any* part that has ever completed a run — this session or a prior one — instantly, with no re-run required; the left panel simultaneously swaps to that part's drawing. Shows the CadQuery **pre-validated** preview (badged) until the real SolidWorks build replaces it.
- **Must-Meet checklist strip** — every `MM-xxx` constraint rendered ✓/✕ with measured-vs-required values, pre-validation first and post-build verification once the SolidWorks model exists.

### Sheet 3 · Pipeline
The action surface. Saved-part cards → **▶ Pull & Run Pipeline** (or **▶▶ Run All Parts**) → Cancel, plus a **Run demo** mode that replays saved extractions with no API key at all. A stage strip + progress bar tracks the live run (`[STAGE]` markers streamed from the CLI), with the full live console beneath it, and the **Overview Analysis** panel auto-expands the moment Stage 1.5 writes its output — the reviewer sees the model's understanding of the part *while it is still being built*.

**The Pipeline Explainer chat** *(`webapp/explainer.py`, 2026-07-11)* — a collapsible band at the bottom of this sheet, in its own **muted periwinkle-violet** zone (`#8F86C4`, used nowhere else in the UI). A **dual-provider**, read-only chat grounded strictly in one part's run artifacts:

- **`local`** — Ollama (qwen) on localhost, **zero cost, fully private**. Every request passes through a guard (`assert_local`) that raises before a socket even opens for any non-localhost host. Sets a 16k context window (Ollama's silent 2–4k truncation is the single biggest way this would quietly fail) and disables qwen's invisible "thinking" stream.
- **`claude`** — the Anthropic API with the same key the pipeline uses, opt-in and **paid**, cost estimated live from the pipeline's own pricing table.

Only the `claude` path ever touches the internet. A **full-pipeline artifact registry** with keyword routing packs context under an 8k-token budget, and a dedicated `trace_field()` follows one field (e.g. `D009`) across *every* stage in order with citations — "why was D009 resolved this way?" gets a cited, traceable answer instead of a guess. UI: provider toggle (**Local · qwen ⟷ Claude API**), quick-question chips, clickable citations that open the cited artifact, "Copy as note," "Send to corrections," and a running `Session: N msgs · $cost` footer.

### Sheet 4 · Run Outputs
The inspection surface for every artifact of any completed run, past or present. A **Select Run** dropdown lists every completed run across every part and session; the run that just finished on Sheet 3 auto-selects here. A **✕ Clear all models** button wipes every stored run output (never the saved part inputs or delivered copies).

**Run-outputs dock — ten sub-tabs**, each showing a ✓ once its file exists for the selected run:

| Sub-tab | Shows |
|---|---|
| Extraction JSON | Raw Claude Vision extraction — never lost, re-runnable free via `--from-json` |
| Resolved Extraction | Every dimension's `resolved_value`, basis, flag tier, and `resolved_by_tier` |
| Build Plan | The self-contained `build_plan.json` — single source of truth for VBA + CadQuery |
| Verification | Arithmetic/envelope report + must-meet constraint story + final-check results |
| Engineering Flags | The severity-ranked human review — What / Decision / Why / Affects |
| Model Check | Post-build mass/bbox validation |
| VBA Macros | Every numbered macro, viewable in-browser |
| Token / Cost | The API cost ledger — per-stage line items and running totals |
| Files | Every output file with sizes and download links |
| Console | The pipeline log — live during a run, persisted with the run afterward |

**Shared run history:** Sheet 2's model dropdown and Sheet 4's run dropdown both read from **one persistent, disk-backed inventory** (`/api/run-history`) — a run in one always appears in the other, surviving server restarts and new sessions.

**Visual Summary Tables** *(2026-07-12, above the dock)* — a collapsible band with a part-header strip (envelope · feature counts · severity flag counts · READY status · open-question `?N` affix) and two linked, scannable tables: **Extracted Features & Dimensions** (ID · Type · Size · Position · Basis · Qty · Status) and **Build Plan** in build order (Step · Feature · Stage · Operation · Key values · Placement · Result). Fed by `pipeline/summary_view.py::build_summary()` — a pure presentation layer with **no new computation**, just consistent number formatting (drawing-style trimming, `⌀` diameters, `(x, y)` positions, meters never surfaced to a human, absent → `—`). Rows expand to full detail; a feature id cross-highlights the twin row across both tables; headers sort client-side; a **⎙ Print** button expands everything and opens the app's only print stylesheet.

**Delivery:** every successful run copies to `UI_Output/<Part>/` and `~/Downloads/SolidWorksModel_Parts/<Part>/` automatically.

---

# Part IV · Outputs & Artifacts — the full per-part file reference

```
<output>/<Part>/
├── <Part>.SLDPRT                       # the 3D model (when SolidWorks is available)
├── <Part>.STL                          # STL export (3D viewer)
├── <Part>_engineering_review.txt       # ← read this first: severity-ranked review
├── <Part>_extraction.json              # raw Claude extraction, verbatim, never lost
├── overview_analysis.json              # Stage 1.5 holistic cross-view read
├── <Part>_resolved_extraction.json     # Stage 2.5: resolved_value + tier + basis per dim
├── <Part>_verification_report.txt      # READY/NOT READY + overview + requirements sections
├── <Part>_requirements.json            # must-meet notes graded met/partial/unmet/not_applicable
├── <Part>_build_plan.json              # self-contained build steps, flags, dispositions
├── <Part>_build_dispositions.json      # per-feature BUILT / BUILT_WITH_DERIVED_VALUE / EXCLUDED_INCOMPLETE
├── <Part>_reconciliation_report.json   # Stage 10.5: checklist vs. build diff
├── <Part>_feature_verification.json    # Stage 10.6: per-feature OK/MISSING/MISPLACED/WRONG_SIZE/EXTRA/UNMEASURABLE
├── <Part>_geometric_loop_report.json   # Stage 10.7: correction-loop iteration ledger
├── <Part>_assist_queue.json            # Stage 10.8: human-assist questions, candidates, defaults
├── <Part>_deferred_log.json            # Workstream 1: quarantined/retried/recovered features
├── <Part>_audit_report.json            # static self-validation of the macros
├── <Part>_model_check.txt              # mass/bbox validation + any skipped features
├── <Part>_macro_overview_validation.json  # macro package vs. Stage 1.5 overview words
├── must_meet_spec.txt                  # the operator's raw must-meet text, persisted
├── must_meet_constraints.json          # parsed MM-001, MM-002, … constraints
├── prevalidation.stl / prevalidation_report.json / prevalidate.py  # CadQuery pre-check
├── constraint_verification.json        # post-build PASS/FAIL, measured vs. required
├── macro_result.json                   # per-feature build outcome (JSON Lines)
├── macros/                             # 00_setup … ZZZ_export_stl, RUN_ALL.vba, README.md
└── macros_csharp/                      # Program.cs + SwBuildHelpers.cs + .csproj + README

<output>/
├── multiview_summary.csv                # triage table across all parts in the batch
├── token_usage_log.txt                  # running API cost, per stage
├── lessons_learned.jsonl                # accumulates spec-overrides + constraint failures
└── .extraction_cache/                   # internal — not exported

Delivered copies:  UI_Output/<Part>/  and  ~/Downloads/SolidWorksModel_Parts/<Part>/
Repo-root:         Learning Loop/<Part>__<timestamp>.txt  +  Learning Loop/INDEX.md
```

---

# Part V · Module Map

| Stage | Module | Runs on |
|---|---|---|
| Image prep | `utils/image_prep.py` | any OS |
| Tiled extraction (escalation) | `utils/tiled_extraction.py` | any OS |
| Stage 1.5 overview analysis | `overview_analysis.py` | any OS |
| Extraction | `extractor.py`, `schema.py` | any OS (paid) |
| Vector hole extraction | `vector_extract/`, `hole_resolution.py` | any OS |
| Spec reconciliation | `must_meet.py` | any OS |
| Ambiguity resolution | `resolver.py`, `callout_qty.py`, `drill_sizes.py`, `gauge.py` | any OS |
| Verification | `validator.py` | any OS |
| Build sequencer | `build_sequencer.py` | any OS |
| Reference geometry | `reference_geometry.py` | any OS |
| Slot/notch decomposition | `slot_cut.py` | any OS |
| Coordinate normalization | `coordinate_normalize.py` | any OS |
| Dimensioning / anchors | `position_solver.py` | any OS |
| VBA macros + audit | `macro_generator.py`, `macro_audit.py`, `macro_echo.py`, `macro_template_engine.py`, `macro_templates/` | any OS |
| Overview↔macro validation | `overview_macro_validate.py` | any OS |
| C# companion output | `csharp_macro.py` | any OS |
| CadQuery pre-validation | `cq_prevalidate.py` | any OS |
| COM build | `solidworks_builder.py`, `model_validator.py` | Windows + SW 2024 |
| Deferred retry | `deferred_retry.py` | Windows + SW 2024 |
| Constraint verification | `constraint_verify.py` | any OS |
| Reconciliation / correction loop | `reconciliation.py` | any OS |
| Feature verification | `feature_verify.py` | any OS |
| Construction methods | `methods_config.py`, `construction_experiment.py`, `METHODS.md` | any OS |
| Human-assist | `human_assist.py`, `learned_patterns.py` | any OS |
| Final gates | `overview_check.py`, `requirements_check.py` | any OS |
| Engineering review | `engineering_review.py` | any OS |
| Learning loop | `learning_loop.py` | any OS |
| Batch orchestration | `batch.py`, `view_ingest.py` | any OS |
| Token ledger | `usage_log.py` | any OS |
| Summary view | `summary_view.py` | any OS |
| Orchestrator | `main.py` | any OS |
| Alternate entry points | `run_models.py`, `build_sldprt.py` | any OS / Windows |

---

# Part VI · Testing & Verification Strategy

`2D-3D-CAD-Test-Generation/tests/` — run with `python -m pytest tests/ -q` from inside `2D-3D-CAD-Test-Generation/`. Coverage spans every stage above, including dedicated suites for each hardening layer:

`test_build_sequencer` · `test_com_builder` · `test_commit_mode` · `test_coordinate_normalize` · `test_coverage_additions` · `test_csharp_macro` · `test_deferred_retry` · `test_dwg_convert` · `test_engineering_review` · `test_explainer` · `test_extractor` / `test_extractor_repair` · `test_feature_verify` · `test_geometric_loop` · `test_golden_macros` · `test_hole_placement` / `test_hole_resolution` · `test_human_assist` · `test_learning_fixes` (1 & 2) · `test_macro_echo` · `test_macro_generator` · `test_methods_config` · `test_multiview` · `test_must_meet` · `test_overview_analysis` / `test_overview_check` / `test_overview_macro_validate` · `test_position_solver` · `test_preview_extract` · `test_reconciliation` · `test_reference_geometry` · `test_reliability_hardening` · `test_requirements_check` · `test_resolver` · `test_slot_cut` · `test_summary_view` · `test_tiled_extraction` · `test_unit_converter` · `test_usage_log` · `test_validator` · `test_vector_extract`.

Plus **golden macro snapshots** (`tests/test_golden_macros.py`, `tests/golden/`) — after any *intentional* change to macro output, `UPDATE_GOLDEN=1 python -m pytest tests/test_golden_macros.py -q` regenerates them and the diff is reviewed like code, guaranteeing byte-identical output is never accidentally lost across a refactor. All VLM (vision-model) calls throughout the test suite are dependency-injected, so the entire decision tree — including real historical bug regressions like the A050211E 5-vs-6-hole case and the 158-C notch-orientation bug — runs end-to-end without a single paid API call.

---

# Part VII · Reliability Guarantees Worth Stating Out Loud

- **Never blocks, never silent.** Every ambiguity resolves to a flagged, defensible number; every skipped feature becomes an explicit manual step; every automated-ladder exhaustion becomes a narrow, defaulted question — never a dead stop.
- **Numbers are chosen, never invented.** Resolved values come only from extracted candidates, vector geometry, sibling features, or the operator's own specs.
- **Human authority is absolute.** Must-meet specs are tier 0, enforced from the extraction prompt through post-build measurement, and every override is logged.
- **Cross-view sanity is first-class.** The model reasons about the drawing as one coherent object (Stage 1.5) before any per-view extraction happens, catching count/through-bore/symmetry errors at the cheapest possible point.
- **Coordinates are centralized, not scattered.** One module (`coordinate_normalize.py`) owns anchor-to-global math; one module (`position_solver.py`) owns the drawing's own dimensioning scheme. The UI table and the VBA can never disagree.
- **Generation-time guards, not runtime hope.** Macro echo checking, template strictness, notch-orientation assertion, dropped-position refusal, and overlapping-hole refusal all fail *generation* — before anything reaches SolidWorks — rather than producing a silently wrong part.
- **A failure never destroys prior progress.** Deferred retry quarantines one failing feature and keeps building; the geometric correction loop stops on oscillation rather than thrashing; reconciliation splices recovered features into the existing plan without touching what already works.
- **Everything is re-runnable and auditable.** Raw extractions persist forever; re-runs from cache or `--from-json` are free; every value, flag, and dollar traces to the exact stage and tier that produced it.
- **The system learns from its own failures.** The Learning Loop turns every run's flags into a paste-ready brief for the next round of fixes.

---

# Part VIII · Running the System

## One-time setup
```powershell
cd 2D-3D-CAD-Test-Generation
python setup.py                      # checks Python, installs deps, creates .env
# edit .env:  ANTHROPIC_API_KEY=sk-ant-...
```
> `python` is often not on PATH on Windows dev machines — use `2D-3D-CAD-Test-Generation\webapp\.venv\Scripts\python.exe` if a venv already exists.

## Web UI — the primary path
```powershell
cd 2D-3D-CAD-Test-Generation\webapp
.\run.ps1        # Windows — or double-click run.bat
```
```bash
./run.sh         # macOS/Linux
```
→ `http://127.0.0.1:8092/` (first run creates `.venv` and installs pinned dependencies). **Note:** Python must be `>=3.10,<3.13` — `cadquery==2.8.0` pulls in `numba`, which has no wheel for 3.13+; on 3.13 the install aborts halfway and leaves other dependencies (rich, anthropic, …) uninstalled.

## CLI — the advanced / batch path
```powershell
cd 2D-3D-CAD-Test-Generation
python main.py --views-folder ..\test_drawings\Test2 --output ..\test_drawings\Test2\output
```
One command: resolves → verifies → writes macros + `.sldprt` → logs tokens → copies to `~/Downloads/SolidWorksModel_Parts` → prints a summary table → ends with `N/N READY`.

## Useful flags

| Flag | Effect |
|---|---|
| `--views-folder DIR` | multi-view batch mode — each subfolder is a part |
| `--drawing FILE` | single drawing instead of a folder |
| `--batch DIR` | flat folder of drawings / `*_extraction.json` |
| `--from-json FILE` | rebuild from a saved extraction — **zero API cost** |
| `--output DIR` | keep stable to reuse the extraction cache |
| `--requirements FILE` | explicit must-meet notes file |
| `--skip-overview-check` / `--skip-requirements-check` | bypass the final READY gates |
| `--no-resolve` | skip Stage 2.5 (legacy blocking behavior) |
| `--strict-gate` | block on failing verification instead of building with assumptions |
| `--no-sldprt` | macros + reports only |
| `--no-export` | don't copy to Downloads |
| `--no-extract-cache` | force a fresh (paid) re-extraction |

**Exit codes:** `0` = all parts READY · `8` = completed but not all READY · `2` = bad arguments.

**Other entry points:** `run_models.py <folders> --output ./output` (batch driver, always non-strict so a run always completes); `build_sldprt.py <output_root>` (COM-builds every saved extraction under a root, one shared SolidWorks session, zero API cost).

## Tests
```powershell
cd 2D-3D-CAD-Test-Generation
python -m pytest tests/ -q
```

---

# Part IX · Cost Transparency

Every paid API call lands in a per-output-folder ledger (`token_usage_log.txt`) broken out **per stage**:
- `extraction` — the per-view vision call
- `stage_1_5_overview_analysis` — the holistic full-sheet pass (its own cost center)
- `stage_2_6_spec_reconciliation` — must-meet parsing
- `extraction_tiled` — the escalation-only tiled zoom pass
- `final_overview_check` — the post-build cross-verification

**Caching is aggressive:** identical re-runs are free (on-disk extraction cache), prompt prefixes and images use API-side prompt caching, and `--from-json` rebuilds a part at **zero** API cost, including reuse of a saved `overview_analysis.json`. The Sheet-3 Explainer's `local` (Ollama) provider is **always** zero-cost; only its opt-in `claude` provider is paid, and its cost is estimated live per answer.

---

# Part X · Known Limitations — what a human must still verify

- **Positions are only as good as the callouts.** When a feature isn't dimensioned from an origin, the resolver centers it and flags it — verify placement against the drawing before trusting it.
- **A CRITICAL value is a defensible default, not a confirmed reading.** Always review the resolution summary and any MEDIUM/LOW/CRITICAL flags before release.
- **Prohibited features are never guessed.** Loft, sweep, boundary, shell, draft, surfacing, and helical threads are always emitted as explicit manual steps — never fabricated API calls.
- **The COM build and `HoleWizard5` path are Windows + SolidWorks 2024 only.** Everything upstream of the actual build runs on any OS, including macOS/Linux for macro/report generation and testing.
- **`HoleWizard5` stays default-off** pending confirmation of SolidWorks 2024's parameter-mapping behavior on a live machine.
- **No checkpoint resume on the COM path** beyond partial-save/auto-save.
- **A `.env` file with `ANTHROPIC_API_KEY` is required for any live extraction** — without it, only demo mode (replaying saved extractions) works.

---

# Part XI · Suggested Live Demo Script (5–7 minutes)

1. **Sheet 1** — load a drawing PDF, crop the front and side views.
2. **Sheet 2** — pull the crops, assign view types, type a must-meet spec ("6 holes, circular pattern, all through"), save the part. Use **Select Model** to show a *previous* run's model loading instantly, no re-run.
3. **Sheet 3** — ▶ Run. Narrate the stage strip; point at the **Overview Analysis** panel auto-expanding mid-run — the model's one-sentence read of the part and any cross-view conflicts, live next to the console. Optionally open the **Explainer chat** and ask "why was D009 resolved this way?" to show the traced, cited answer.
4. **Sheet 2** — watch the pre-validated STL appear, then the SolidWorks model replace it; show the must-meet checklist flip to ✓ with measured values.
5. **Sheet 4** — the finished run auto-selects. Open the **Visual Summary tables** first (fast scan), then **Engineering Flags** — show a CRITICAL item's What / Decision / Why / Affects and the tier that resolved it. Switch **Select Run** to an older run to show all sub-tabs reload for it.
6. Finish on **Token / Cost** — the run's exact dollar cost, per stage.

---

# Appendix · Docs Index & Repo Map

| Doc | What it covers |
|---|---|
| `README.md` (repo root) | Operator run guide — the practical "how do I run this" reference |
| `2D-3D-CAD-Test-Generation/README.md` | Deep technical documentation |
| `docs/PRESENTATION.md` | Prior presentation deck (2026-07-16 snapshot) |
| `docs/PIPELINE_PRESENTATION.md` | Earlier pipeline walkthrough (pre-Stage-1.5 snapshot) |
| `docs/NEWREAD.md` | Web UI visual design system (graphite/copper/slate-teal) |
| `docs/RUN_PROMPT.md` | Prompt template for having an agent run + verify a batch |
| `2D-3D-CAD-Test-Generation/docs/*.md` | Dated design docs for each workstream (build-order redesign, commit-to-extraction, coordinate normalization, macro-echo hardening, human-assist, learning loop, visual-summary-tables, upgrade-3pack, dimensioning-architecture research/audit) |
| `pipeline/METHODS.md` | Evidence-backed construction-method library per feature class |
| `docs/sw_api_reference/` | SolidWorks 2024 API signatures used by the macro generator |

## Repo layout
```
MTI-Model_Finalized/
├── CLAUDE.md                          # canonical architecture reference for AI agents
├── README.md                          # operator run guide
├── docs/                              # presentation + design docs (this file lives here)
├── Learning Loop/                     # one failure report per run + INDEX.md
├── test_drawings/                     # Test2, E2ETest, DrawingPDFs, AcceptanceMM, …
└── 2D-3D-CAD-Test-Generation/
    ├── main.py, run_models.py, build_sldprt.py, setup.py
    ├── pipeline/                      # every stage module (Part V above)
    ├── utils/                         # image prep, tiled extraction
    ├── tests/                         # full test suite + golden macros + fixtures
    ├── webapp/                        # FastAPI UI: app.py, explainer.py, index.html, vendor/, photoapp/
    ├── docs/                          # deep technical + dated workstream docs
    └── samples/, templates/
```
