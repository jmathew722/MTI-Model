# EXTREME README — The MTI 2D→3D CAD Pipeline, In Full Detail

> A complete, recreate-from-scratch reference for the entire project. This document
> explains **what every part does, how the data flows, why each decision was made,
> and how to rebuild it identically and improve it.** It is deliberately exhaustive.
>
> **This file is CANONICAL** for how the pipeline works (decided 2026-08-15,
> REFACTOR_ANALYSIS §1.8/§2.5). `CLAUDE.md` used to carry a second hand-maintained
> copy of the same stage-by-stage narrative; it is now scoped to commands,
> environment, invariants and a stage INDEX that points here. Where the two
> disagree, this file wins — and the disagreement is a bug to fix.
>
> Last reconciled against the code: 2026-08-15. Branch context: `MTI_Codex` /
> `MTI_OCRDWG` / `REFRACTOR_UI` (all carry the OpenAI provider and the P0–P4 audit
> fixes; `main` stays on Anthropic).

---

## 0. THE ONE-PARAGRAPH SUMMARY

This is a Windows-oriented Python pipeline that turns a **2D engineering drawing**
(PDF / PNG / JPG / TIFF / DWG / DXF / eDrawings) into a **SolidWorks 2024 part**
(`.sldprt` + `.stl`). It does this by: (1) rasterizing/normalizing the drawing,
(2) reading it with a vision LLM (Claude Sonnet 5 by default, or OpenAI GPT-5.6)
into a strict Pydantic data structure, (3) cross-checking that reading against the
original vector geometry and a holistic second look, (4) **resolving every
ambiguity to a defensible number instead of ever blocking**, (5) verifying the
plan arithmetically and against operator "must-meet" specs, (6) emitting a
deterministic, statically-audited sequence of **numbered VBA macros** (and a C#
twin, and a CadQuery pre-validation build), (7) optionally driving SolidWorks over
COM to actually build the solid, and (8) measuring the built STL back against the
plan in a self-correcting loop, escalating only what is genuinely unresolvable to a
short human question. **The guiding principle: a complete approximate model is
always the right outcome; an incomplete model is always the wrong outcome. Resolve
and flag — never block, never silently drop.**

---

## 1. GUIDING PRINCIPLES & INVARIANTS (read these first — they explain every design choice)

1. **Never block on ambiguity.** Every unclear or under-dimensioned value is
   resolved to a *defensible* number and annotated with its basis and a severity
   flag. The pipeline always produces a complete, buildable, approximate model.
2. **Numbers are chosen from extracted candidates, never invented.** The resolver
   picks among values that were actually read from the drawing (or derived by a
   declared geometric rule). It never fabricates a magnitude out of thin air.
   Compliance grades are likewise never fabricated — a non-geometric note grades
   `not_applicable`, not "pass".
3. **Vector geometry owns position; the vision callout owns semantics.** Exact hole
   centers come from the DXF/DWG/PDF vector entities when available; the diameter,
   thread, and depth come from the callout. Disagreement keeps *both* and flags
   CRITICAL — it is never silently reconciled.
4. **Specs are tier 0.** Operator must-meet specifications override vision-extracted
   values on any conflict, and every override is logged to `lessons_learned.jsonl`.
5. **Determinism where it matters.** The build-order sequencer and macro emitter
   are pure functions of the model: the same input yields **byte-identical**
   `build_order` and macros across runs (enforced by golden-snapshot tests).
6. **Backward compatibility is sacred.** The extraction JSON schema is
   *additive-only*: new fields get non-null defaults, and every old JSON must keep
   loading (`--from-json` and the test fixtures depend on it).
7. **Every feature ends in a named state**, never a silent skip:
   `built` / `built_with_flag` / `skipped_prohibited` / `deferred_open`. Deferred-open
   ships with a ready-to-answer clarification question.
8. **Generation-time invariants crash loudly rather than ship wrong geometry.** The
   macro generator *refuses* to emit a build with a dropped position, an overlapping
   hole, a mislabeled step, a foreign-feature literal, or a notch resolved to the
   wrong edge. A known-bad pattern can never silently reship (the E-number ledger).
9. **The CLI is the single source of truth.** The web UI shells out to `main.py`;
   it never re-implements pipeline logic.

---

## 2. ENVIRONMENT & EXACT SETUP (to recreate identically)

### 2.1 Platform reality
- **OS:** Windows 11 is the target (the `.sldprt`/`.stl` COM build is Windows-only,
  via `pywin32`). *Everything upstream of the SolidWorks build runs on any OS* —
  extraction, resolution, verification, macro generation, CadQuery pre-validation.
- **Shell on this machine:** PowerShell primary; a Bash (Git Bash / POSIX) tool is
  also available. `python` is frequently **not** on PATH — use the venv interpreter:
  `2D-3D-CAD-Test-Generation\webapp\.venv\Scripts\python.exe`.
- **SolidWorks:** 2024, required only for the actual COM build and the optional
  DWG-native import path. Type library (`sldworks.tlb`) signatures are version-pinned
  in code and mirrored in `docs/sw_api_reference/`.

### 2.2 Python dependencies (PINNED — `requirements.txt`)
These exact versions were verified together (full test suite + browser e2e). The
pins matter — some are load-bearing:

```
anthropic==0.116.0          # default LLM provider
openai==2.46.0              # optional provider (AI_PROVIDER=openai); never imported unless set
pywin32==312; sys=Windows   # SolidWorks COM
pillow==12.3.0              # image prep
pdf2image==1.17.0           # PDF→raster (needs Poppler on PATH)
PyMuPDF==1.28.0             # vector-PDF parsing (fitz) — Bézier circle extraction, DPI re-render
ezdxf==1.4.4                # DXF entity parsing (exact hole centers)
ezdwg==0.9.0                # DWG→DXF conversion engine (one of several)
opencv-python-headless==5.0.0.93   # HoughCircles raster fallback
numpy==2.4.6               # CAPPED <2.5: numba(cadquery) needs <2.5, opencv needs >=2
pydantic==2.13.4           # the schema layer (v2)
python-dotenv==1.2.2       # .env loading
rich==15.0.0               # console UI (subprocess stdout must be decoded UTF-8!)
pytest==9.1.1
cadquery==2.8.0            # headless geometry pre-validation
trimesh==4.12.2            # post-build STL measurement
scipy==1.18.0             # REQUIRED by trimesh.section().discrete (pinned explicitly)
networkx==3.6.1           # anchor-graph topological solve (position_solver)
shapely==2.1.2            # 2D geometry ops
```

External native deps not in pip: **Poppler** (for `pdf2image`), and for DWG
conversion the engine chain **ezdwg → SolidWorks DWG translator → ODA File
Converter** (whichever is present).

### 2.3 Environment variables (`.env`, gitignored; template is keyless)
- `ANTHROPIC_API_KEY` — required for the default provider.
- `EXTRACTION_MODEL` — default `claude-sonnet-5`; can be `claude-opus-4-8` for hard
  drawings, or `gpt-5.6` under the OpenAI provider.
- `AI_PROVIDER` — unset/`anthropic` (default, so `main` is untouched) or `openai`.
- `OPENAI_API_KEY` — required only when `AI_PROVIDER=openai`.
- `SOLIDWORKS_TEMPLATE_PATH` — path to a real `.prtdot`; falls back to the SW default.
- `MAX_IMAGE_LONG_EDGE` — raster cap override.
- `MTI_ENABLE_HOLE_WIZARD=1` — opt-in real HoleWizard5 hole path (default OFF).
- `MTI_METHOD_<CLASS>` / `methods.json` — override the construction-method dispatch.
- `EXPLAINER_OLLAMA_MODEL` — local explainer model (default `qwen3.6:latest`).

### 2.4 First-run setup
```powershell
python setup.py                      # scaffolds dirs, checks deps
copy .env.template .env              # then edit in your key
cd webapp; .\run.ps1                 # creates .venv, installs pinned deps, starts UI on :8092
```

---

## 3. COMPLETE DIRECTORY MAP

```
2D-3D-CAD-Test-Generation/
├── main.py                     # PRIMARY entry point / CLI orchestrator (~54 KB)
├── run_models.py               # batch driver: extract→auto-resolve→build NON-STRICT (always completes)
├── build_sldprt.py             # COM-build every saved *_extraction.json under a root (zero API cost)
├── setup.py                    # environment scaffolding
├── conftest.py                 # pytest: adds project root to sys.path
├── requirements.txt            # PINNED deps
├── .env / .env.template        # keys & provider config (real .env gitignored)
├── README.md                   # narrative overview
├── CLAUDE.md                   # agent working brief (authoritative per-stage detail)
├── EXTREME_README.md           # THIS FILE
│
├── pipeline/                   # THE PIPELINE — one module per stage (~25 k lines)
│   ├── schema.py               # ★ Pydantic v2 data contract — single source of truth
│   ├── ai_provider.py          # provider abstraction (Anthropic ⇄ OpenAI, one .messages.create surface)
│   ├── extractor.py            # Stage 2: one vision call → DrawingData (specs-first, cached)
│   ├── region_extraction.py    # Stage 2.3: unconditional fixed-region hi-res re-read + reconcile
│   ├── overview_analysis.py    # Stage 1.5: holistic whole-sheet relational read
│   ├── dwg_crosscheck.py       # Stage 2.4: DWG-native exact dimension text corrects OCR
│   ├── must_meet.py            # Stage 2.6: parse operator must-meet spec → MM-xxx constraints (tier 0)
│   ├── resolver.py             # ★ Stage 2.5: the core "resolve every ambiguity, never block" engine
│   ├── position_solver.py      # the sole coordinate authority: anchor-graph topological solve
│   ├── coordinate_normalize.py # the ONE semantic-anchor → global-CAD-coordinate + inch→meter locus
│   ├── slot_cut.py             # canonical U-notch/slot = rectangle + corner fillets (all 3 builders)
│   ├── hole_resolution.py      # merge vector hole centers with vision callouts (position vs semantics)
│   ├── vector_extract/         # exact geometry from source files
│   │   ├── dxf_holes.py        #   DXF circle entities (ezdxf)
│   │   ├── pdf_holes.py        #   vector-PDF Bézier circles (PyMuPDF)
│   │   ├── raster_holes.py     #   HoughCircles fallback (OpenCV)
│   │   ├── dwg_convert.py      #   DWG→DXF engine chain
│   │   └── geometry.py         #   shared circle-fit / geometry helpers
│   ├── validator.py            # Stage 6: arithmetic/envelope verification (advisory unless --strict-gate)
│   ├── build_sequencer.py      # ★ Stage 6.5: canonical 7-stage deterministic build order
│   ├── reference_geometry.py   # Workstream 3: named datum skeleton (REF_DATUM_A, REF_SYM_*, REF_AXIS_*, REF_PT_*)
│   ├── macro_generator.py      # ★ Stage 7: numbered VBA macros + generation-time invariants (~3.4 k lines)
│   ├── macro_audit.py          # static audit of every macro (banned/required APIs — the E-ledger)
│   ├── macro_echo.py           # parse literals back out of VBA, round-trip vs build plan
│   ├── macro_template_engine.py# strict %%placeholder templating (one feature's data only)
│   ├── macro_templates/        # profile_rect.vba.tmpl, sketch_circle.vba.tmpl
│   ├── csharp_macro.py         # C# console-program twin of the VBA build (same build-plan data)
│   ├── cq_prevalidate.py       # Stage 8: CadQuery headless build from build_plan.json → prevalidation.stl
│   ├── solidworks_builder.py   # ★ Stage 9: Windows COM build of .sldprt + STL (~2.4 k lines)
│   ├── model_validator.py      # mass/bbox sanity check of the built model
│   ├── deferred_retry.py       # Workstream 1: quarantine a failed feature, retry after solid completes
│   ├── constraint_verify.py    # Stage 10: measure built STL vs MM constraints (trimesh) PASS/FAIL
│   ├── feature_verify.py       # Stage 10.6: measure EVERY planned feature vs build_plan (OK/MISSING/...)
│   ├── reconciliation.py       # ★ Stage 10.5 + 10.7: checklist-vs-build diff + geometric correction loop
│   ├── human_assist.py         # Stage 10.8: escalate genuinely-stuck items to a narrow human question
│   ├── verification_questions.py # phrase engineering flags as confirmation questions (Sheet 4)
│   ├── learned_patterns.py     # generalize recurring ambiguities across parts → LEARNED_PATTERNS.md
│   ├── overview_check.py       # final READY gate: re-examine overview drawing vs build
│   ├── requirements_check.py   # final READY gate: grade operator notes met/partial/unmet
│   ├── overview_macro_validate.py # validate macro package vs Stage 1.5 overview words
│   ├── engineering_review.py   # ★ the single severity-ranked human report (_engineering_review.txt)
│   ├── learning_loop.py        # writes Learning Loop/<Part>__<ts>.txt failure report + FIXES FOR FABLE
│   ├── summary_view.py         # pure presentation layer for the UI Visual Summary tables
│   ├── batch.py                # the --views-folder orchestration (process_drawing_data)
│   ├── construction_experiment.py # scratch base+feature through candidate methods → seed METHODS
│   ├── methods_config.py       # machine-readable construction-method dispatch
│   ├── hole_wizard.py / hole_wizard_constants.py # opt-in real HoleWizard5 + ANSI clearance tables
│   ├── drill_sizes.py / gauge.py / callout_qty.py / unit helpers
│   ├── usage_log.py            # token/USD ledger (PRICING table for both providers)
│   ├── image_coordinates.py / view_ingest.py  # multi-view ingest helpers
│   └── METHODS.md              # human evidence-backed construction recipe library
│
├── dwg_native/                 # ALTERNATE pipeline: DWG import → exact geometry → build → verify
│   ├── pipeline_run.py         #   the job runner (import→extract→map→build→verify)
│   ├── extract/                #   importer.py, native_extract.py, schema.py
│   ├── semantic/               #   mapper.py (2D→3D intents), rules.py, conflicts.py, ocr_correction.py, numbers.py
│   ├── build/                  #   builder.py (COM), vba_emit.py (reviewable macro)
│   ├── verify/                 #   verifier.py (5 gates)
│   ├── session/                #   com_session.py, job_queue.py (single serialized SW worker)
│   ├── api/app.py              #   FastAPI service for the DWG-native path
│   └── spike/                  #   probe_*.py — live SolidWorks API investigation scripts
│
├── webapp/                     # The web UI (FastAPI, port 8092)
│   ├── app.py                  #   wrapper that runs main.py as a subprocess + streams console (~2 k lines)
│   ├── index.html              #   SINGLE monolithic frontend (4 sheet-tabs, embedded CSS/JS)
│   ├── explainer.py            #   dual-provider read-only chat over one run's artifacts (local Ollama / Claude)
│   ├── preview_extract.py      #   quick preview extraction endpoint
│   ├── bridge.js               #   hooks the vendored DrawingCrop app into the server
│   ├── run.ps1                 #   venv bootstrap + uvicorn launch
│   ├── vendor/                 #   Three.js, STLLoader, OrbitControls (no CDN)
│   ├── photoapp/               #   vendored DrawingCrop + pdf.js (Sheet-1 crop tool)
│   └── parts/<session>/<part>/ #   saved inputs + per-run output/ (the run history store)
│
├── tests/                      # 65 pytest files + golden/ snapshots + fixtures/
├── docs/                       # design docs, audits, research notes, SW API reference, error ledger
├── utils/                      # image_prep.py, tiled_extraction.py, unit_converter.py, logger.py
├── templates/                  # SolidWorks part template(s)
├── samples/                    # demo drawings
└── frontend-dwg/               # frontend for the DWG-native API
```

---

## 4. THE DATA CONTRACT (`pipeline/schema.py`) — memorize this; everything flows through it

The schema is **the single source of truth for the data shape.** It is used two
ways: (1) as the `input_schema` of the forced tool call the vision model must make,
and (2) as the validation layer for any ingested JSON. **Convention: no
Optional/None fields** — every field has a non-null default (`""`, `0.0`, `[]`),
because tool-use extraction emits these far more reliably than nullable fields.

### 4.1 Enums
- `Units`: `mm | cm | inch`
- `DimensionType`: `linear | radial | diameter | angular | depth | thread`
- `ToleranceType`: `bilateral | unilateral | limit | reference`
- `FeatureType`: `extrude_boss | extrude_cut | revolve | hole | fillet | chamfer |
  thread | pattern | mirror | shell | sweep | loft | rib | draft`. **The first
  feature in a build MUST be an `extrude_boss`** (a solid base) before any cut/hole.
  `sweep/loft/rib/draft` are coverage-completeness types (Phase 3a): each builds
  when its profile/path data is present, else emits a numbered MANUAL step — never a
  wrong-geometry guess.
- `HoleType`: `thru | blind | counterbore | countersink | spotface | tapped |
  clearance` (clearance pulls drill dia from ANSI tables in `hole_wizard_constants`).
- `PatternKind`: `none | linear | circular`.

### 4.2 Alias normalization (robustness against LLM phrasing)
- `_FEATURE_ALIASES` maps ~40 synonyms to canonical names (`extrude`→`extrude_boss`,
  `circular_pattern`→`pattern`, `lathe`→`revolve`, `blend`→`loft`, …). Applied in a
  `mode="before"` validator so a `"circular_pattern"` feature never fails validation
  and silently drops to the value-only fallback (which would skip the completeness
  gate — a real bug fixed 2026-07-10).
- `canonicalize_applies_to(label)` maps free-text dimension labels (e.g.
  `"width (top view, overall horizontal)"`, `"counterbore depth"`) to a canonical
  token. **Rules are ordered MOST-SPECIFIC FIRST** so compound hole labels
  (`cbore_depth`, `csink_diameter`) win before the plain `depth`/`diameter` rules
  (both contain those substrings). Returns `""` when nothing matches, so "unknown"
  is distinguishable from a real token. This fixes failure class E010 (verbose
  labels silently missing an exact-string match → "profile needs a diameter or
  length+width" build failure).
- `is_envelope_label(label)` — True only for overall length/width/height (accepts
  "overall …" but rejects feature-local sizes), guarding the envelope used for
  hole-centering and feasibility.

### 4.3 The models (every field matters)
- **`View`** — `view_type`, `description`, `dimensions_shown[]`, `visible_features[]`
  (solid lines), `hidden_features[]` (dashed = features behind the plane),
  `centerline_notes` (holes/symmetry/revolves implied by centerlines).
- **`HoleCallout`** — one callout, possibly many instances. Carries: `type`,
  `diameter` (>0 validated), `thru`/`depth`, `thread_spec`, counterbore &
  countersink params, `qty`, first-instance `x/y_position` + `position_known`,
  `is_datum_hole` (grounds coordinates at the hole center, not part edges),
  `pattern`/`pattern_spacing`, `bolt_circle_diameter`/`_center`/`start_angle`,
  **`arc_angle`** (360 = full ring no-wrap; <360 = partial arc, step =
  arc/(qty−1), both ends inclusive), **`instance_positions[]`** (explicit [x,y] of
  EVERY instance, edge-referenced from lower-left — the most reliable multi-hole
  placement), `feature_ref`, `view`, `notes`, and pipeline-filled
  `position_source` (`dxf_entity|pdf_vector|hough|vision`) + `position_confidence`.
- **`PositionAnchor`** — **WHAT a feature's position is measured FROM**, preserving
  the drawing's own dimensioning scheme instead of flattening to bare floats:
  `scheme` (`chain|baseline|ordinate|coordinate|polar_bsc|datum_frame`), `anchor_ref`
  (`part_edge_left|right|top|bottom`, `origin`, `part_center`, `F00x` (chain prev),
  `F00x_center` (polar center), `DATUM_HOLE_1`, `DRF_A|B|C`), `dimension_ids[]`,
  `axis` (`x|y|radial|angular`), `value`, `semantics`
  (`to_near_edge|to_center|to_far_edge|true_position`). Absolute build-plan
  coordinates are DERIVED from these by `position_solver.py`.
- **`Feature`** — `id`, `type` (alias-normalized), `description`,
  `related_dimensions[]`, `sketch_plane`, `depth_dimension_id`, `parent_feature`
  (pattern seed / fillet host), `offset_x/y` + `position_known`, **`anchors[]`**
  (typically one per axis; empty = degenerate coordinate scheme = legacy behavior),
  `quantity`, **`revolve_profile[]`** (ordered [axial, radial] half-profile, radial
  ≥0 validated, closed to axis automatically), `mirror_plane`.
- **`SlotCut`** — the canonical **rectangle-then-fillet** representation of a
  U-notch/open-slot/obround (a slot is NEVER a single arc-bearing sketch):
  `slot_kind` (`open_notch|closed_slot|obround`), `open_edge`, `anchor_edge`/
  `anchor_offset`/`anchor_dimension_id`, `anchor_semantics`
  (`edge_to_near_edge|edge_to_centerline`), `width`, `depth` (both >0), `corner_radius`,
  `thru`/`thru_basis`. The drawing dims constrain the rectangle (sharp theoretical
  corners); "R TYP" is a corner treatment on it.
- **`Dimension`** — `id`, `type`, `value` (>0, magnitude only), `unit`, tolerances,
  `applies_to` (+ `canonical_applies_to` property), `feature_ref`, `view`,
  `datum_ref`, `gdt_symbol`, `is_reference` (REF/parenthesized = non-controlling),
  `value_unclear` + `ambiguity_reason` + `possible_values[]` (candidate readings,
  best-guess first), `resolution_required`, `notes`. Has `is_envelope` property.
- **`DimensionChain`** — a closed loop: `total = Σ components` (closure-checkable).
- **Relationship models** — `SymmetryNote`, `ConcentricGroup`, `SpacingNote`,
  bundled into `RelationshipMap` (+ `dimension_chains`, `derived_dimension_ids`,
  `reference_dimension_ids`).
- **`GeometricTolerance`** — `symbol`, `value`, `datum`.
- **`DrawingData`** (top level) — title-block fields (`part_name`, `part_number`,
  `revision`, `drawing_standard`, `units`, `scale`, `material`, `finish`,
  `general_tolerance`), **`coordinate_frame`** (the resolver's chosen ground:
  `datum_hole_pair | declared_origin | lower_left_corner`), **`dimension_origin`**
  (declared origin text), plus `views[]`, `dimensions[]`, `hole_callouts[]`,
  `slot_cuts[]`, `features[]`, `geometric_tolerances[]`, `relationships`,
  `build_order[]` (first must be a base solid), `warnings[]`, `confidence` (0..1).
  Convenience lookups: `feature_by_id`, `dimension_by_id`, `hole_callout_by_id`,
  `hole_callout_for_feature`, `slot_cut_for_feature`, and **`display_name`**
  (part_number > part_name > "part", + `-Rev<x>`; junk title blocks like `""` are
  treated as unread so folder names never become garbage).

**Field-level guarantees** (positivity, enums, ranges) live in `schema.py`.
**Cross-field build-readiness rules** (base feature exists, build_order deps
satisfied, dimensional closure, pattern envelopes) live in `validator.py`, which
produces a rich human report instead of a raw `ValidationError`.

---

## 5. ENTRY POINTS & CLI

### 5.1 `main.py` — the orchestrator
Key internal functions: `_prepare_and_extract` (image prep + extraction + region
pass + augment), `_resolve_stage`, `_run_batch` / `_run_views_folder` /
`_extract_one_drawing`, `_export_to_downloads`, `_connect_solidworks_optional`,
`_run_region_pass_into`, `_augment_holes`, `_save_extraction`.

**Complete flag reference:**
- Source (mutually exclusive group): `--drawing <file>` | `--views-folder <dir>`
  (each subfolder is a part of per-view images) | `--from-json <extraction.json>`
  (rebuild from a saved extraction, **zero API cost**) | (batch folder form).
- `--output <dir>` (default `./output`), `--page N` (multi-page PDF, default 1).
- `--source-file <PDF/DXF/DWG>` — original vector file for EXACT hole positions
  (defaults to `--drawing` when it's already vector; auto-discovered in a part folder).
- `--debug` — save intermediate extraction JSON.
- `--no-extract-cache` — force paid re-extraction (ignore the on-disk cache).
- `--engine vba|com` — `vba` (default, any OS: generate macros) or `com` (Windows: drive SW directly).
- `--validate-only` — extract + verify only; no macros, no SolidWorks (cheapest live check).
- `--no-resolve` — skip Stage 2.5 resolver (restores old block-on-ambiguity behavior).
- `--strict-gate` — a BLOCKED verification stops the run (resolver-off v2 behavior); default is advisory.
- `--requirements <file>` — operator must-meet notes (auto-discovered as
  `notes.txt`/`requirements.txt`/`<part>_notes.txt` in a part folder); an unmet line gates READY.
- `--skip-overview-check` / `--skip-requirements-check` — bypass the final READY gates.
- `--no-sldprt` — macros/reports only, do not COM-build.
- `--no-export` — do not copy outputs to `~/Downloads/SolidWorksModel_Parts`.
- `--region-pass on|off` (default on) — unconditional fixed-region hi-res re-read.
- `--keep-regions all|conflicts-only|none` (default all) — which region crops to keep.
- `--no-dwg-crosscheck` — disable Stage 2.4 (no-op off Windows / without SolidWorks).

**Exit codes:** `0` = all parts READY; `8` = completed but not all READY; `2` = bad args.

### 5.2 Alternate entry points
- **`run_models.py <folders> --output ./output`** — batch driver: extract →
  auto-resolve → build **NON-STRICT** so the run always completes, writing
  `<Part>_design_decisions.txt` per part.
- **`build_sldprt.py <output_root>`** — COM-builds every saved `<part>_extraction.json`
  under a root into `.sldprt`, in one shared SolidWorks session, **zero API cost**.
- **`webapp/` (FastAPI :8092)** — see §10.

### 5.3 Standard commands
```powershell
# Tests
python -m pytest tests/ -q
python -m pytest tests/test_multiview.py -q
$env:UPDATE_GOLDEN=1; python -m pytest tests/test_golden_macros.py -q   # regenerate golden after INTENTIONAL macro change

# Full pipeline on a batch of part folders
python main.py --views-folder ..\test_drawings\Test2 --output ..\test_drawings\Test2\output

# Single drawing, extract+verify only (cheapest live check)
python main.py --drawing path\to\drawing.pdf --validate-only --debug

# Rebuild from saved extraction — zero API cost
python main.py --from-json <Part>_extraction.json --output .\output
```

---

## 6. THE PIPELINE, STAGE BY STAGE

Orchestrated by `main.py` (single drawing) and `pipeline/batch.py::process_drawing_data`
(the `--views-folder` path). Stages are numbered to match the code and `CLAUDE.md`.

### Stage 1 — Image prep (`utils/image_prep.py`)
Normalize/downscale input images to the raster cap (`MAX_IMAGE_LONG_EDGE`). Multi-view
input: each part is a folder of per-view images; view role comes from filename
keywords (`front`/`side`/`top`…) or leading `01`–`05`; a file named
`full`/`overview`/`isometric` (or matching the folder name) is **overview context
only** (not built as a plane). **Front + one other orthographic view is required.**

### Stage 1.2 — Tiled high-res extraction (`utils/tiled_extraction.py`) — ESCALATION, not default
Fires via `should_tile()` on ANY of: blank-image heuristic, ink density < 0.5 %,
extraction confidence < 0.6, > 25 % of dims flagged unclear, or a C-size+ page at
the raster cap. Then `adaptive_render()` re-renders the **vector** PDF at escalating
DPI (300→600→900) until median line width ≥ 2.5 px (lossless zoom; never upscales a
raster); a cheap global pass maps views/title-block/datum candidates; `make_tiles()`
cuts ~1500 px tiles with **22 % overlap**; each content tile is extracted in SHEET
coordinates; `stitch()` merges by anchor+value (conflicts kept as candidate
`possible_values`); `datum_anchor()` re-expresses positions from the datum. Cost logs
as `extraction_tiled`; tiles cache by (page hash, DPI, grid). VLM calls are injected
so the machinery is unit-tested without paid calls. Clean small drawings keep the
single-shot path.

### Stage 1.5 — Holistic overview analysis (`overview_analysis.py`)
The **full uncropped sheet** (`00_full.jpg` / "FULL OVERVIEW VIEW") goes to the model
with a **relational** prompt: which views are on the sheet, cross-view feature
correspondences (through-vs-blind), overall 3D shape, cross-view conflicts
(severity + recommendation), symmetry, and global notes like "(6) HLS" with a
`resolved_count`. **It does NOT re-extract dimensions.** Output `overview_analysis.json`
feeds Stage 2.5 as **priority tier 2** (tier 0 = must-meet specs; tier 1 = per-view
extraction owns dimension values; tier 2 = overview owns cross-view relationships);
every resolution records `resolved_by_tier`. A deterministic callout-count
cross-check becomes a flag (e.g. A050211E's 5-visible-vs-"(6) HLS" → CRITICAL).
Purely additive: no key / failure → stage skipped. Cost line
`stage: stage_1_5_overview_analysis`. Shown in the UI as the "Overview Analysis" panel.

### Stage 2 — Vision extraction (`extractor.py`)
One vision call per part (all views labeled, **forced tool call**, Pydantic v2
schema). **Specs-first:** operator must-meet specs are injected into the prompt so
the model actively looks for those features from the start (spec text is part of the
cache key — changed specs force a fresh extraction). Raw extraction JSON is always
saved; an on-disk cache (`<output>/.extraction_cache/`) makes identical re-runs free.
Token/USD cost appends to `token_usage_log.txt` via `usage_log.py`.

### Stage 2.3 — Region pass (`region_extraction.py`) — DEFAULT ON
The rejected prior design gated a hi-res re-read on the model's *self-reported
confidence* — so a field read **wrong but confidently** never got a second look. This
pass runs over **fixed overlapping regions of every drawing unconditionally** at
near-native resolution and reconciles. Confidence is used only afterward, to decide
whether a merged field needs a human — never whether it got a hi-res read. Guarantees:
every overview field is accounted for at merge time (`merge_fields` raises
`RegionMergeError` if any lacks a merge-log entry); two HIGH-confidence disagreements
never auto-tie-break (both escalate with both crops); `coverage_gap` fires only on a
literal silent-skip.

### Stage 2.4 — DWG native cross-check (`dwg_crosscheck.py`) — Windows + DWG only
When the input is a DWG, SolidWorks imports it and its **exact dimension text** is
used to verify/correct the OCR extraction before the build. No-op off Windows /
without SolidWorks / with `--no-dwg-crosscheck`.

### Stage 2.5 — The resolver (`resolver.py`) — ★ THE CORE DESIGN DECISION (~2.4 k lines)
**The pipeline never blocks on ambiguity.** Every unclear dimension gets a numeric
`resolved_value` chosen from extracted candidates (never fabricated), via a
deterministic ladder, tagged HIGH/MEDIUM/LOW/CRITICAL:
- **Step 0 (specs-first):** an operator must-meet value that clarifies an ambiguous
  reading takes precedence (`assumption_basis="spec_driven"`).
- Then: arithmetic-chain closure → geometric validity → conservative geometry →
  last-resort declared default.
- **Positional dimensions are consumed BEFORE any escalation** (`_feature_positional_xy`)
  — the Bug-1 root cause was positional `applies_to` labels
  (`slot_offset`/`hole_position_x`/`position`) canonicalizing to `""`, so a fully
  extracted location (158-C `D002=1.56`) was dropped and recorded `[0,0]`/needs_review.
- **Commit-to-extraction mode (default ON):** no human in the loop — the pipeline
  commits to the extraction and BUILDS every extracted feature. A step/notch missing
  length+width is derived from the outer-profile envelope minus its partial anchor
  (`_derive_profile_delta`, `basis="profile_delta"`); a hole missing a diameter
  inherits the most-common sibling diameter (`_sibling_diameter`); a genuinely
  undimensioned size/position commits a **declared-basis conservative** value/placement
  (`committed_conservative`, never `[0,0]`), built and CRITICAL-flagged. The only
  remaining hard failure is "no closed outer profile". Non-committable edge/pattern
  treatments (fillet/chamfer with no size, pattern with no count+spacing) stay excluded.
- **Per-instance hole placement + datum chaining** (`_classify_hole_groups`): every
  hole group is `placement: pattern` (only with hard evidence — a bolt-circle or
  uniform pitch with a single owning feature) or `placement: individual` (default;
  each instance owns its coordinate), with a per-instance `position_basis` datum
  chain. **Bias toward individual** — an individual group misbuilt as a pattern is
  wrong geometry, while a pattern built as individuals is merely more lines.
- `resolve_extraction` is a **deterministic pure function** — re-running it recovers
  nothing new, which is what lets the reconciliation loop terminate.

### The coordinate authorities (used by 2.5 and downstream)
- **`position_solver.py`** — the **sole coordinate authority for anchored features**.
  A topological solve over the anchor graph (chains accumulate in drawing order;
  polar = center + r·(cos,sin); far edges measure back from length/width), emitting
  per-feature derivation traces (`"x = part_edge_left(0) + D002(1.56) [baseline]"`).
  An anchorless feature is the degenerate `coordinate` case, so **legacy behavior is
  the base case and goldens stay byte-identical.** A cycle/unresolvable anchor falls
  back to stored offsets, `grounded=false`, MEDIUM flag — never blocks. Datum-frame
  selection: datum-hole pair > declared `dimension_origin` > default lower-left.
  `movers(model, changed_dim_ids)` names exactly the features that move when a
  dimension changes (chain corrections propagate downstream only). As of 2026-07-21
  the solver is **AUTHORITATIVE**: it stamps the solved coordinate onto an
  explicitly-anchored, grounded, single-instance feature BEFORE body generation, so
  the VBA / build plan / echo check all read the drawing-anchored coordinate.
- **`coordinate_normalize.py`** — the ONE place semantic anchors become global CAD
  coordinates, so the UI table and the VBA can never disagree. `Anchor` enum
  (TOP/BOTTOM/LEFT/RIGHT edges, four corners, CENTER, DATUM_POINT/AXIS,
  FEATURE_RELATIVE, ABSOLUTE_GLOBAL) + `resolve_notch_anchor` (the single locus of
  the `y = parent_height − depth` math, e.g. 158-C `6.25 − 1.88 = 4.37`) +
  `resolve_point_anchor` + `validate_bounds` + `assert_edge_orientation`.
  `INCH_TO_M = 0.0254` / `to_meters()` is the one inch→meter conversion, applied only
  at the VBA boundary.

### Stage 2.6 — Spec reconciliation (`must_meet.py`) — TIER 0
The operator's must-meet text (`must_meet_spec.txt` / legacy `notes.txt`) is parsed
into structured `MM-xxx` constraints (`must_meet_constraints.json`) via a dedicated
LLM call with a **deterministic regex fallback** (no key needed). **Constraints
override vision-extracted values on any conflict**; every conflict → `lessons_learned.jsonl`
(`resolution: spec_override`). Missing geometry is derived (bolt-circle fit: radius =
mean √(x²+y²) about the hole centroid; radii disagreeing >0.005 in = CRITICAL).
Exception: a spec hole COUNT contradicting explicitly-dimensioned drawing positions
keeps the drawing geometry, flags CRITICAL, and lets the MM check fail with
measured-vs-required (`spec_vs_drawing_disagreement`).

### The vector-geometry layer (`vector_extract/` + `hole_resolution.py`)
Exact hole positions from the original file: DXF entities (ezdxf), vector-PDF Bézier
circles (PyMuPDF), HoughCircles raster fallback (OpenCV). **Precedence: vector
geometry owns position; the vision callout owns semantics (diameter/thread/depth);
disagreement keeps both and flags CRITICAL.** Each hole carries `position_source`
and `position_confidence`.

### Stage 6 — Validator (`validator.py`)
Arithmetic/envelope verification (base feature exists, build_order deps, dimensional
closure, pattern envelopes). **Advisory by default**; `--strict-gate` makes it
blocking. Produces a human-readable report, not a raw ValidationError.

### Stage 6.5 — Canonical build sequencer (`build_sequencer.py`) — ★ THE ONE BUILD-ORDER PASS
Called once at the top of `generate_macro_package`. Re-orders the completeness-gate
survivors into a fixed **seven-stage** sequence with a **stable within-stage sort
keyed to feature id → byte-identical `build_order` across runs**:
```
0 reference geometry
1 base solid (largest closed profile)
2 additive bosses
3 profile subtractions
4 holes: plain → cbore/csk → tapped
5 patterns
6 chamfers, then fillets
7 non-geometric
```
**No type-based omission:** every feature ends `BUILT` / `BUILT_WITH_DERIVED_VALUE` /
`EXCLUDED_INCOMPLETE` (with the missing parameter named) in
`<Part>_build_dispositions.json`. `_feature_xy` is slot-aware (reports a slot's true
near-corner, never `[0,0]`). `_feature_dim_values` picks the **first-declared**
dimension on a canonical-key collision (not "biggest number wins"). A falsy-basis
sweep keeps `""` out of `_EXPLICIT_BASES`/`_READ_POSITIONS`, so a blank basis reads
as derived, never as directly-extracted. Because the same `model` object flows
onward, macros, `build_plan.json`, CadQuery, and the COM build all inherit this order.

### Workstream 3 — Reference-geometry datum skeleton (`reference_geometry.py`)
BEFORE any feature, `01a_reference_geometry.vba` builds the drawing's datum structure
as **named** SolidWorks reference geometry: `REF_DATUM_A` (always) + `REF_DATUM_B/C`
(GD&T/dimension `datum_ref`) + `REF_SYM_X/Y` (symmetry mid-planes; X uses length/2,
Y uses width/2 — axis-correct) + `REF_AXIS_*` (concentric/circular) + `REF_PT_<fid>`
(pattern/hole origins), via `InsertRefPlane`/`InsertAxis2`. `build_plan.json` gains a
`reference_geometry[]` block; each feature step a `positioned_from` handle. **Additive**
— the proven absolute-coordinate build stays as the audit trail + fallback; the
skeleton gives human landmarks + stable named selection handles for the deferred-retry
loop. **Naming contract (stable — do not rename without updating both sides):**
`REF_DATUM_<A|B|C>`, `REF_SYM_<X|Y>`, `REF_AXIS_<purpose>`, `REF_PT_<feature_id>`.

### Stage 7 — Macro generation (`macro_generator.py` + friends) — ★ ~3.4 k lines
Emits numbered VBA macros (`00_setup` … `ZZZ_export_stl`, `RUN_ALL.vba`). Prohibited
features (loft/sweep/shell/etc. when un-buildable) become `NN_Fxxx_MANUAL_*.vba`
steps. **Every macro is statically audited before writing** (`macro_audit.py` — banned
APIs fail generation). Key sub-systems:
- **Circular-pattern reliability trio:** a hole group routed to `circular_pattern`
  (spec says so, or polar-dimensioned; requires a concentric bore for the axis) emits
  seed hole (`Fxxx_SeedHoleCut`) → named `PatternAxisN` (`InsertAxis2` off the bore
  face) → pattern via the single `CreateCircularPatternSafe` helper
  (version-pinned `FeatureCircularPattern5`, fallback `...4`; axis Mark=1, seed
  Mark=4; Nothing-check + hard stop). `total_instances` INCLUDES the seed.
- **Canonical slot decomposition** (`slot_cut.py`): `slot_rect_cut` (mandatory 4-line
  rectangle + through-cut — near-unfailable, carries position+size truth) then
  `slot_corner_fillet` (constant-radius interior-corner fillets, deferred-safe; a
  fillet failure never destroys the correct slot). `corner_array()` is the single
  source of truth both the rectangle and the fillet edge-selection derive from. Each
  arc centre is `(r, r)` inset from the sharp corner (proven by `arc_centers` /
  `rounded_profile_from_corners`, asserted in tests). `expand_slot_patterns`: a
  pattern whose seed is a slot becomes N−1 explicit per-instance slots (a U-notch
  can't be reliably feature-patterned); the pattern id is recorded in `resolved_away`.
- **Stage-7 hardening (generation-time guards that turn bugs into named failures
  BEFORE the macro leaves the machine):**
  1. **Macro echo check** (`macro_echo.py`, `assert_macro_echo`): every emitted
     geometry literal is parsed back out of the VBA (parsing anchored to known call
     signatures, never arbitrary regex) and must round-trip to the build-plan value
     for the SAME feature — a literal matching a *different* feature =
     `cross_contamination`, matching nothing = `orphan_literal`, a planned position
     never emitted = `missing_value`; any raises `MacroEchoError`.
  2. **Template emission** (`macro_template_engine.py` + `macro_templates/*.vba.tmpl`,
     `%%NAME` placeholders): circle/rectangle primitives are filled from EXACTLY one
     feature's record; `fill()` is strict both ways (missing placeholder OR unused
     key → `TemplateFillError`), so a template structurally cannot reference another
     feature's data; output byte-faithful.
  3. **Open-edge overshoot** (`EDGE_OVERSHOOT_EPS = 0.050`): pushes an open notch's
     open side PAST the part edge; `_assert_open_edge_overshoot` refuses a cut whose
     open-axis span equals the depth (the 158-C enclosed-window bug).
  4. **Label/payload agreement** (`_assert_label_payload_agreement`): refuses a step
     whose description names a FOREIGN feature id.
  - Plus: `_assert_no_dropped_positions` (refuses a build that drops a position while
    a positional dimension for that feature exists), `_assert_no_overlapping_holes`
    (two same-diameter instances within half a diameter), `_assert_notch_orientation`
    (re-checks every open-edge slot vs the real parent envelope; refuses a `TOP_EDGE`
    notch resolved to y=0..depth — the 158-C top/bottom bug), macro-package dedup
    (`macros/` cleared of stale `*.vba` each run; duplicate feature id refused).
- **Per-instance hole placement** (`_hole_feature_positions`): when a qty>1 callout is
  attached to ONE feature while sibling features of the same diameter also exist
  (A001271E's 4 asymmetric inner holes), those siblings ARE the other instances —
  each feature drills exactly its own position, never the whole shared layout. Only a
  verified regular pattern with a single owning feature lays out multiple instances.
- **Fully-defined gate** (`ReportSketchStatus`, read-only `GetConstrainedStatus`
  after `FullyDefineSketch`): logs PASS/WARN so under-defined sketches are observable,
  not silently accepted — a gate, not an unverified `AddDimension2` fixer (smart
  dimensioning stays opt-in-future until verified live).
- **Dimension-anchor annotations:** explicitly-anchored features get a
  `' ---- DIMENSION ANCHORS` comment block in their VBA, enforced by
  `macro_audit.check_anchor_annotations`.
- Reference idioms mined from `third_party/` (gitignored codestack checkout); SW 2024
  signatures in `docs/sw_api_reference/`.

### The macro static auditor (`macro_audit.py`) & the E-number ledger
Enforces `docs/solidworks-macro-error-log.md`. Auditor-enforced examples:
- **E004** — `GetModelBoundingBox` is banned (invented API); use `IBody2.GetBodyBox`.
- **E006** — never re-select a closed sketch by name (`SelectByID2(..., "SKETCH", …)`);
  consume the ACTIVE sketch (recorder pattern) or re-find by `ProfileFeature` type.
The rest are enforced by generation-time invariants above. **When a resolved
correction traces to a documented build-failure class, add its `EXXX` cross-reference.**

### C# macro output (`csharp_macro.py`)
Every package also emits `macros_csharp/` (Program.cs + SwBuildHelpers.cs +
BuildPart.csproj net48 + README) — a SolidWorks-compatible C# console program
generated from the SAME BuildStep data as the VBA (never transpiled), late-bound COM
(`Type.GetTypeFromProgID`, no interop DLLs), mirroring the verified
`FeatureExtrusion3`/`FeatureCut4`(flip-retry)/`FeatureCircularPattern5`(Mark 1/4)
calls and the `../logs` contract. Interactive steps stay logged WARN/MANUAL.
Deterministic, self-echo-checked (`CSharpEmitError`), never fatal to the VBA package,
never touches `macros/` (goldens byte-identical). The VBA remains the canonical path.

### Stage 8 — CadQuery pre-validation (`cq_prevalidate.py`)
Builds the same geometry headlessly from `build_plan.json` (single source of truth;
circular patterns via `.polarArray(radius, seedAngle, 360, count)` + `cutThruAll`;
slots via `rounded_profile_from_corners` in one shot; revolve + cbore/csk branches;
countersink conical relief). Checks watertightness / volume / hole counts against the
MM constraints, writes `prevalidation.stl` + `prevalidation_report.json` + a per-run
`prevalidate.py`. **A failed check ABORTS the SolidWorks build** and surfaces the exact
constraint (`MM-001 FAILED: …`). Graceful no-op when cadquery isn't installed.

### Stage 9 — SolidWorks COM build (`solidworks_builder.py` + `model_validator.py`) — Windows only, ~2.4 k lines
COM build of `.sldprt` + STL export + mass/bbox check. Same circular-pattern trio as
the VBA path (`build_circular_pattern_holes`); features renamed deterministically
right after creation; per-feature outcomes written to `macro_result.json` so a failure
surfaces as the exact feature, never a generic exit code. Slots route through
`build_slot` (rectangle cut + interior fillets), aligned to the actual body's min
corner. Base orientation via the shared `profile_extents` sizer (horizontal=length/
width, vertical=height). Countersink builds real conical geometry. Optional
`HoleWizard5` path (`_try_hole_wizard`, `MTI_ENABLE_HOLE_WIZARD=1`, default OFF: 27-arg
signature verified against the tlb, but SW2024 returned `None` on a clean part, so
default-off until the version/locale Value-slot mapping is nailed down; falls back to
the proven `_circular_cut_at` sketch-circle cut). **Live-discovered API surprises now
guarded:** rebuild-error-count APIs never resolve; `SaveAs3` returns an int bitmask,
not a bool.

### Workstream 1 — Deferred feature retry (`deferred_retry.py`)
In non-strict mode a hard feature failure no longer skips forever — it is
**quarantined** and the build **continues**; after the solid is complete, deferred
features are retried (cap 3) with the completed topology as context (their target
faces now exist), using an escalating taxonomy playbook (`classify_failure` →
selection / sketch-over-under-defined / zero-thickness / missing-parent / com-timeout
/ param-out-of-range; each attempt changes strategy). A recovered feature becomes
BUILT; a still-open one ends `deferred_open` with a clarification question. Ledger
`_deferred_log.json`.

### Stage 10 — Post-build must-meet verification (`constraint_verify.py`)
The built STL is measured with **trimesh** (cross-section circle fitting; through-all =
the hole appears near both faces) and every MM constraint graded PASS/FAIL with
measured-vs-required → `constraint_verification.json`. **A run with MM constraints is
only READY when every constraint passes**; each failure → `lessons_learned.jsonl`
with the responsible VBA snippet.

### Stage 10.5 — Reconciliation pass (`reconciliation.py`)
The pipeline's own closing check against the ORIGINAL raw `_extraction.json` (never
the resolved/downstream artifacts — those could hide the bug). Builds a ground-truth
checklist (every feature id + expected instance count from `hole_callouts[].qty`/
`instance_positions`) and diffs it against the sequencer's disposition table +
`build_plan.json`'s actual positions. A justified `skipped_prohibited` is accepted;
anything else missing/short is named. On a gap it re-runs **only** `resolve_extraction`
(never the extractor — no paid call) up to `max_passes` (default 3); since the
resolver is deterministic, a pass that recovers nothing stops the loop. A recovered
feature is spliced into the existing `build_plan.json` and a new `RECONCILE_pass<N>_*.vba`
is added — no existing file renumbered. Writes `<Part>_reconciliation_report.json`
(`checklist_total`, `confirmed_built`, `loop_passes_used`, `unresolved[]`,
`splices_applied[]`, `final_status: READY | READY_WITH_OPEN_ITEMS`); unresolved items
gate the binary READY status (exit 8).

### Stage 10.6 — Per-feature geometric verification (`feature_verify.py`)
Where 10 grades the STL against MM constraints only, this measures **every** planned
feature against `build_plan.json`: each hole's position + diameter + through/blind,
each cut/notch's location (material-absence probe), each slot's obround, the base
envelope (+ a COM-vs-CadQuery volume cross-check). The STL is in the lower-left-origin
drawing frame. Every feature → `OK` / `MISSING` / `MISPLACED` (measured pos reported) /
`WRONG_SIZE` (measured size) / `EXTRA` / `UNMEASURABLE` (always with a stated reason)
→ `<Part>_feature_verification.json`. A slot's own boundary loop is recognised as its
footprint, never a phantom EXTRA hole. **Anchor fidelity** (`verify_anchor_fidelity`):
each anchored feature is re-measured RELATIVE TO ITS ANCHOR (edge/chain target/polar
center) → `anchor_fidelity[]`; `ANCHOR_MISMATCH` catches "right hole, measured from
the wrong edge" (a compensating-error class absolute-XY checks miss).

### Stage 10.7 — Geometric correction loop (`reconciliation.py::geometric_correction_loop`)
Wraps 10.6 in a bounded build→measure→correct→rebuild loop (cap 3). Policy by class:
a **systematic** transform error (origin offset / axis swap / uniform scale — detected
only when ≥2 features share one consistent error, `classify_transform`) is corrected
once and pre-compensated on every affected step; a one-off `MISPLACED` re-emits the
single step with the **resolver-derived** position (the drawing is truth, never the
measured value); `MISSING`/`EXTRA`/unresolvable `WRONG_SIZE` are flagged, never
fabricated. Terminates on all-PASS, the cap, no-applicable-correction, or
**oscillation** (a previously-PASS feature regressing → stop, never thrash). Writes
`<Part>_geometric_loop_report.json`; appends `geometric_loop_iteration` to
`lessons_learned.jsonl`. The COM builder is injected so the loop is unit-tested
without SolidWorks.

### Stage 10.8 — Human-assist escalation (`human_assist.py`)
The exit ramp when the automated ladder is genuinely exhausted. A feature/dimension
becomes a question ONLY after all four automated stages fail (`escalation_eligible`):
resolver ladder → TYP/derivation → the 10.7 correction loop → Phase-D method
experiments. Each eligible item → a NARROW question object: `question_text` (one
sentence), pre-populated `candidates` with basis, a tight `region_crop`, and a
`default_if_unanswered` that is **always populated** (the best value that ships).
Capped (default 3), prioritized by leverage (a base/envelope dim outranks one slot's
radius; higher fan-out & CRITICAL rank higher). **Never blocks:** a pending question
is a `NEEDS_HUMAN_INPUT` disposition overlay whose default still ships — the part
still produces its complete model and its usual READY status; questions do NOT gate
READY. Queue → `<Part>_assist_queue.json`. **Answer feedback:** `apply_answers`/
`rerun_with_answers` feed a human answer back as the resolver's highest-priority
candidate (`assumption_basis="human_provided"`, `tier_human`, above spec) and
re-splice via the reconciliation splice-back — **no paid re-extraction**.
`learned_patterns.py` generalizes a recurring ambiguity (same value-free `signature`
answered the same way across ≥2 parts) into `LEARNED_PATTERNS.md` — a priority bias,
never an auto-applied numeric value across drawings.

### Stage 11 — Final READY gates (status only; outputs still produced)
- **`overview_check.py`** re-examines the part's overview drawing alone and diffs it
  against the build (missing visible feature = CRITICAL).
- **`requirements_check.py`** grades operator notes met/partial/unmet — an unmet line
  gates READY.
- **`overview_macro_validate.py`** validates the macro PACKAGE vs Stage 1.5 words: a
  note's `resolved_count` must equal the holes the macros actually drill; every
  cross-view correspondence must match ≥1 build step (synonym map); a THROUGH relation
  must not meet a `blind` step. Advisory by default; `assert_overview_macro_validation`
  is the opt-in strict mode.

### Stage 12 — Engineering review (`engineering_review.py`)
The single severity-ranked human report `<Part>_engineering_review.txt`, regenerated
after the COM build so skipped features are included. **This is the canonical "what
needs human attention" surface.**

### Stage 13 — Learning loop (`learning_loop.py`)
At the end of every run, reads the artifacts and writes one plain-text failure report
to the repo-root `Learning Loop/<Part>__<timestamp>.txt` (gate reasons, MM fails,
Stage-1.5 conflicts, EVERY engineering-review flag at ALL severities, macro/build
failures) ending with a paste-ready "FIXES FOR FABLE" brief (suspected code area per
failure). A human hands these to Claude to plan generalized fixes so the pipeline
improves run over run. `Learning Loop/INDEX.md` logs one line per run. Exception-safe.

---

## 7. THE PROVIDER ABSTRACTION (`ai_provider.py`) — how one contract serves two LLMs

Every LLM call site (extractor, Stage 1.5, must_meet spec parsing, overview cross-check)
shares ONE contract, unchanged since it was written for Anthropic:
`client.messages.create(model=, max_tokens=, system=, messages=, tools=, tool_choice=)`
returning `.content` / `.usage` / `.stop_reason`. `AI_PROVIDER=openai` swaps
`_build_client()`/`DEFAULT_MODEL` to a `_OpenAIAdapterClient` exposing the **identical**
`.messages.create(...)` surface and translating Anthropic-shaped requests/responses
to/from OpenAI Chat Completions internally — **no call site changes.** OpenAI model:
`gpt-5.6` (vision-capable frontier tier) with `reasoning_effort="none"` forced whenever
tools are attached (live-verified: GPT-5.6's `/v1/chat/completions` 400s on function
tools + any other reasoning_effort). Usage translation mirrors Anthropic's convention
so `usage_log.estimate_cost` prices either provider: OpenAI's `prompt_tokens`
(includes cache hits) splits into `input_tokens = prompt_tokens − cached_tokens` and
`cache_read_input_tokens = cached_tokens`; `cache_creation_input_tokens` always 0.
`PRICING` carries `gpt-5.6`/`-terra`/`-luna`. `is_transient_error`/`is_nonretryable_status`
classify retries for either SDK by exception class name, without importing either
package unless active. Default unset = Anthropic, so `main` is untouched.

---

## 8. THE TWO BUILD PATHS + THE DWG-NATIVE PIPELINE

### 8.1 VBA macro path (default, any OS)
`generate_macro_package` → numbered `.vba` in `macros/` + `RUN_ALL.vba`. The user (or
the COM path) runs them in SolidWorks. This is the **canonical build path** and the
audit trail; all invariants above apply here.

### 8.2 COM path (`--engine com`, Windows)
`solidworks_builder.py` drives SolidWorks directly over `pywin32`. Same geometry, same
circular-pattern trio, same slot routing. Injected `build_fn` so the correction loop
is testable headless.

### 8.3 DWG-native pipeline (`dwg_native/`) — a separate, geometry-first path
For DWG inputs where exact geometry is available. `pipeline_run.run_job`:
1. **import** (`import_dwg`) — SolidWorks imports the DWG → DXF + optional sheet PDF.
2. **extract** (`extract_raw` → `raw_extraction.json`) — geometry + text, with provenance.
3. **map** (`map_to_build_plan`, rules-first) → `build_plan.json`; `assert_provenance`
   **fails the job on any unsourced value** (no fabricated numbers).
4. **OCR correction** (`correct_ocr`) — DWG-exact values correct a sibling vision
   `*_extraction.json` → `ocr_correction.json` (the stated purpose of this path).
5. Always emit a reviewable `macros/build.vba` (`vba_emit.py`). Both VBA and COM paths
   sketch every feature on **"Front Plane" at z=0** (2026-07-28 fix: they used to
   diverge — the VBA selected a face by coordinate-hit at z=thickness). E004/E006
   honored. A **blocking semantic conflict blocks the build** (surfaced + stopped,
   no silent skip).
6. **build** (`builder.build_part`) — COM `.SLDPRT` + `.STL`.
7. **verify** (`verifier.verify_build`) — 5 gates: `rebuild`, `fully_defined`
   (under-defined is advisory/non-blocking since coordinate-drawn sketches read
   under-defined but are pinned by exact coords; over-defined fails), `dimension_roundtrip`
   (body box vs plan), `feature_count`, `bounding_box`. A failing part lands in
   `failed/<job>/` with the failing gate named.
`session/job_queue.py` serializes one SolidWorks worker; `api/app.py` exposes it; the
`spike/` scripts are live API probes used to nail down signatures.

---

## 9. THE MUST-MEET / VERIFICATION LAYER (the accuracy spine)

Two applications of the operator's must-meet text: (1) **specs-first** at extraction
(Stage 2) and resolution (Stage 2.5 Step 0) and constraint parsing (Stage 2.6), and
(2) a **final re-grade** against the built part (Stages 10, 11). Files:
`must_meet_spec.txt` (raw, persisted), `must_meet_constraints.json` (MM-001…),
`constraint_verification.json` (post-build PASS/FAIL, measured vs required),
`prevalidation_report.json` (CadQuery pre-check). Cross-run learning accumulates in
`<output>/lessons_learned.jsonl` (spec-override conflicts + constraint failures).

---

## 10. THE WEB UI (`webapp/`)

`app.py` (FastAPI, :8092) is a **wrapper that runs `main.py` as a subprocess**
(`--views-folder <part> --output <part>/output`) and streams its console — the CLI is
the single source of truth. **Subprocess stdout must be decoded as UTF-8 with
replacement** (rich output once crashed the cp1252 reader; do not reintroduce locale
decoding). **After editing `app.py` or pipeline code, restart uvicorn on 8092** —
it otherwise serves stale endpoints (`index.html` is served fresh per request, so
static edits need only a browser refresh).

### 10.1 The four sheet-tabs (single monolithic `index.html`)
Tab bar order and identities (as of the 2026 Sheet-3/4 swap):
- **Sheet 1 · Upload & 3D Model** — intake; full overview drawings uploaded/cropped
  here (the model reads every view from the sheet itself — **no human crop/markup
  preprocessing**; the old per-view region-markup / `reference_regions.json` scheme is
  gone, none of those endpoints exist). Hosts the Three.js STL viewer.
- **Sheet 2 · Pipeline** — part setup, run controls, the live Overview Analysis panel,
  the correction & re-run box (`POST /api/run-part` appends a `CORRECTION (…)` line +
  forces `--no-extract-cache`), and the Pipeline Explainer band at the bottom. **The
  Sheet-2 save gate requires a FRONT view** (+ one more orthographic) because the
  front view defines the base profile.
- **Sheet 3 · Run Outputs** — the run-outputs dock (ten sub-tabs: Extraction JSON,
  Resolved Extraction, Build Plan, Verification, Engineering Flags, Model Check, VBA
  Macros, Token/Cost, Files, Console) + the **Visual Summary** tables above it. Backed
  by the persistent `/api/run-history` (disk-scan of `webapp/parts/*/*/output`,
  survives restarts); each run's console persists to `ui_console.log`. "✕ Clear all
  models" (`POST /api/run-history/clear`) deletes run outputs but never saved inputs
  or the delivered copies.
- **Sheet 4 · Human Verification** — each engineering flag → a concise LLM-phrased
  confirmation question + fill-in; answers compile into ONE must-meet CORRECTION block
  fed back through specs-first extraction + Stage 2.5 on re-run, clearing flags at the
  source instead of shipping assumptions.

**JS tab plumbing:** `TOP_TABS = ['intake','pipeline','outputs','verify']`; `showTab`
toggles `main > .panel` by `panel-<name>`; `showSub` toggles the dock's `.subpanel`;
legacy sub-tab names route via `showTab('outputs'); showSub(name)`. Panels are
addressed by semantic `data-tab` name, not sheet number — so the Sheet 3/4 swap was
purely presentational (no logic changed).

### 10.2 Visual Summary (`summary_view.py` + `/api/parts/{s}/{p}/summary`)
A **pure presentation layer** (no pipeline stage, no new computation) that assembles a
view-model from artifacts already on disk: a header strip (envelope · feature counts ·
severity flags · READY · open-question `?N`) plus two linked tables — Extracted
Features & Dimensions and Build Plan (in build order). **All number formatting lives in
this ONE module:** drawing-style (trailing zeros trimmed, leading zero dropped below 1
→ `.105`), `⌀` diameters, `(x, y)` positions, meters never surfaced, absent → `—`.
Discovers the artifact filename PREFIX from disk (folder `A001581E/` holds `158-C_*`
files — never derive from the folder name), prefers standalone
`*_build_dispositions.json`, reads the envelope from dimensions. Degrades gracefully
(missing artifact → pending column). Tested against frozen golden-part fixtures.

### 10.3 Pipeline Explainer (`explainer.py`) — dual-provider read-only chat
Grounds an LLM strictly in ONE run's artifacts. Two providers chosen per-message:
- **`local`** — Ollama (qwen) on localhost, **zero cost**, private. Every request
  passes `_urlopen` → `assert_local` (raises `ExternalHostError` before a socket opens
  for any non-localhost host, unit-tested at the socket layer), sets `num_ctx: 16384`
  (Ollama's silent 2–4k truncation is the #1 silent breakage) and `think: false`
  (qwen thinking models otherwise stream invisible reasoning and appear to output
  nothing). Empty answer → explicit error, not false success. **Qwen only, never a
  llama:** `choose_model()` prefers the configured default if installed → any
  already-installed qwen → small qwen fallback only on a low-RAM box → else the
  default (UI offers to pull).
- **`claude`** — the Anthropic API (opt-in, paid; streams via `messages.stream`, cost
  from `usage_log.PRICING`). Only this path touches the internet; `anthropic` imported
  lazily.
Context assembler: a full-pipeline `ARTIFACT_REGISTRY` + keyword `ROUTING`, ID-sliced
packing under an 8k-token budget, and `trace_field()` (follows one field, e.g. `D009`,
across every stage with citations). Endpoints: `/api/explainer/health|pull|history|chat`.
Its own muted periwinkle-violet UI zone (`--explain*`).

### 10.4 DWG/DXF/eDrawings conversion
`/api/convert-dwg` (engine chain ezdwg → SolidWorks translator → ODA), cached in
`.convert_cache/`, logged to `conversion_log.jsonl`.

---

## 11. OUTPUTS (every artifact, per part, under `<output>/<Part>/`)

- `.SLDPRT`, `.STL` — the built model (COM path).
- **`_engineering_review.txt`** — read this first (severity-ranked human report).
- `_extraction.json` — raw vision extraction, **never lost**.
- `overview_analysis.json` — Stage 1.5 holistic read.
- `_resolved_extraction.json` — post-resolver (+ `must_meet` block + `coordinate_frame`).
- `_verification_report.txt`, `_requirements.json`, `_audit_report.json`, `_model_check.txt`.
- `_build_plan.json` — self-contained steps + flags + per-feature `dispositions` +
  `reference_geometry[]` + `coordinate_frame` header + `resolved_away`.
- `_build_dispositions.json` — the sequencer's BUILT / BUILT_WITH_DERIVED_VALUE /
  EXCLUDED_INCOMPLETE table.
- `_reconciliation_report.json` — Stage 10.5 checklist-vs-build diff.
- `_feature_verification.json` — Stage 10.6 per-feature verdicts + `anchor_fidelity`.
- `_geometric_loop_report.json` — Stage 10.7 iteration ledger + transforms applied.
- `_assist_queue.json` — Stage 10.8 questions (pending/answered, candidates, defaults).
- `_deferred_log.json` — Workstream 1 quarantine/retry ledger.
- `_macro_overview_validation.json` — macro package vs Stage 1.5 words.
- `macros/` (+ any `RECONCILE_pass<N>_*.vba`), `macros_csharp/`.
- Must-meet layer: `must_meet_spec.txt`, `must_meet_constraints.json`,
  `prevalidation.stl` + `prevalidation_report.json` + `prevalidate.py`,
  `constraint_verification.json`, `macro_result.json`.
- `ui_console.log` — the run's console transcript (for the UI's historical Console tab).
- `<output>/lessons_learned.jsonl` — cross-run accumulation of spec-override conflicts
  and constraint failures.
- Successful runs also copied to `UI_Output/<Part>/` (gitignored) and
  `~/Downloads/SolidWorksModel_Parts/<Part>/`.
- Repo-root `Learning Loop/<Part>__<timestamp>.txt` + `Learning Loop/INDEX.md`.

---

## 12. TESTING (65 files in `tests/`)

- Run all: `python -m pytest tests/ -q`. `conftest.py` puts the project root on `sys.path`.
- **Golden macro snapshots** (`tests/test_golden_macros.py`): after an INTENTIONAL
  change to macro output, regenerate with `$env:UPDATE_GOLDEN=1` and review the diff
  like code. This is the guard that keeps macro emission byte-deterministic.
- Coverage highlights (file → what it locks in): `test_ai_provider` (message/tool/
  response translation, provider selection, pricing — no network), `test_position_solver`
  (baseline plate, chain propagation, polar BSC, datum-hole, cycle fallback, wrong-edge
  fidelity), `test_slot_cut`/`test_slot_geometry` ((r,r) inset, rounded profile,
  pattern expansion, partial-arc, revolve validation, live CadQuery slot),
  `test_coordinate_normalize` (all anchor types + inch→meter + the 158-C regression +
  the bottom-placed-top-notch guard), `test_macro_echo`/`test_macro_audit_hardening`
  (the generation-time guards), `test_reconciliation`/`test_geometric_loop`,
  `test_feature_verify`/`_matching`, `test_summary_view` (frozen golden-part fixtures),
  `test_dwg_build_regressions`/`test_dwg_semantic`/`test_dwg_crosscheck`,
  `test_feature_dim_values_collision` (first-declared wins), `test_hole_placement`,
  `test_reference_geometry` (datum skeleton + axis-correct symmetry offset),
  `test_reliability_hardening`, `test_learning_fixes[_2]`, and the P0 regression set
  `test_macro_regressions_p0`. Live-SolidWorks behaviors (`test_save_model_saveas3_codes`,
  `test_check_rebuild_errors`, `test_solidworks_builder_volume_check`) encode the
  API surprises discovered on the live machine.

---

## 13. HOW TO RECREATE THIS FROM SCRATCH (the short path)

1. **Skeleton:** create the directory map (§3). Start with `pipeline/schema.py` — it
   is the contract everything else depends on. Get the Pydantic models + the
   `canonicalize_applies_to`/alias tables exactly right; write `test_extractor`-style
   round-trip tests first.
2. **Provider layer:** `ai_provider.py` with the single `.messages.create` surface, so
   later code never branches on provider. Add `usage_log.py` PRICING alongside.
3. **Extraction:** `extractor.py` (forced tool call, specs-first prompt, disk cache),
   then the reliability layers (`region_extraction.py`, `overview_analysis.py`,
   `utils/tiled_extraction.py`, `vector_extract/*`). Inject the VLM so everything is
   testable without paid calls.
4. **The resolver + coordinate authorities:** `resolver.py` (deterministic pure
   function, commit-mode, per-instance placement), `position_solver.py`,
   `coordinate_normalize.py`, `slot_cut.py`, `must_meet.py`. This is the heart — get
   the "never block, choose from candidates, flag with basis+severity" ladder right.
5. **Sequencing + emission:** `build_sequencer.py` (the fixed 7-stage order, stable
   sort → byte-identical output), then `macro_generator.py` + `macro_audit.py` +
   `macro_echo.py` + templates. Write golden-snapshot tests immediately so emission
   stays deterministic. Port the E-number ledger as static rules + generation-time
   invariants.
6. **Pre-validation + build:** `cq_prevalidate.py` (headless, aborts on MM failure),
   then `solidworks_builder.py` (Windows/COM) mirroring the same calls. Keep the build
   function injectable.
7. **Closing loops:** `constraint_verify.py`, `feature_verify.py`, `reconciliation.py`
   (10.5 + 10.7), `deferred_retry.py`, `human_assist.py`, the final gates
   (`overview_check.py`, `requirements_check.py`, `overview_macro_validate.py`),
   `engineering_review.py`, `learning_loop.py`.
8. **Surfaces:** `main.py` CLI (§5), `batch.py`, then the webapp (`app.py` shells out
   to `main.py`; `summary_view.py`; `explainer.py`).
9. **Determinism check:** the whole thing is right when `--from-json` on a saved
   extraction reproduces byte-identical macros and the golden tests pass.

---

## 13.5 CROSS-CUTTING MODULES (the 2026-08-15 consolidation)

Five modules were added/merged to give one owner to things that previously had
several. Each resolves a numbered item in `REFACTOR_ANALYSIS.md` (repo root), which
is the standing audit + action list.

### `pipeline/coordinate_authority.py` — where a feature IS (§1.1)
Five modules touch position resolution (`hole_resolution`, `resolver`,
`position_solver`, `coordinate_normalize`, `build_sequencer._feature_xy`). Their
logic is genuinely different and is NOT merged; what is merged is the **ownership
story**. This module holds the four ordered phases (vector-vs-vision merge →
ambiguity resolution → anchor-graph solve → normalize/emit), the strict precedence
`spec_driven > vector_geometry > anchor_solver > resolved_dimension >
committed_conservative`, and ONE entry point `resolve_position()` that answers
"where is this feature and who decided that" with the losing candidates recorded
(`disagreements()`), never averaged away. `tests/test_coordinate_authority.py`
asserts the chain end-to-end — the precedence is exactly what silently breaks when
one of the five is refactored alone.

### `pipeline/feature_ledger.py` — the per-feature history (§1.2)
Six artifacts tracked feature state in six shapes (`_build_dispositions.json`, its
duplicate inside `build_plan.json`, `_reconciliation_report.json`,
`_feature_verification.json`, `_geometric_loop_report.json`, `_assist_queue.json`,
`_deferred_log.json`). `build_ledger(output_dir)` assembles them into ONE
append-only, stage-tagged history per feature id (`{stage, status, basis, detail,
source}`, ordered by `STAGE_ORDER`), written as `<Part>_feature_ledger.json`.
Deliberately a **view**, so consumers migrate one at a time and are verifiable
against frozen goldens; `summary_view.py` is the first (its golden fixtures are
byte-identical through the ledger). Each source file becomes a filtered view or is
retired once nothing reads it directly.

### `pipeline/retry_ladder.py` — the bounded-retry contract (§1.5)
Stage 10.5, Stage 10.7 and the deferred-feature queue each had their own cap /
"made no progress" / oscillation logic. They now share one driver:
`run_ladder(attempt, cap=3, …)` owns termination only — **cap**, **completion**,
**oscillation** (a unit that passed before and fails now, checked BEFORE
completion so a regression is never masked), **no progress** (deterministic work
cannot get luckier by repeating; opt out for ladders whose passes escalate
strategy), **exhaustion**, and **error** (recorded, never swallowed). Callers keep
their domain logic and may consult the policy mid-pass via `LadderContext.regressed`
— which is how the geometric loop still skips correction work it would throw away.
`tests/test_retry_ladder.py` is the one place the guarantee is tested.

### `pipeline/highres_pass.py` — the shared second look (§1.4)
Stage 1.2 (tiled, escalation-triggered) and Stage 2.3 (region, unconditional)
differ in exactly one design dimension — **trigger policy** — and in the key their
readings match on. Shared here: `evaluate_trigger(ALWAYS | ON_CONFIDENCE_HEURISTIC
| NEVER)`, window planning (`windows_fixed_size` / `windows_by_division`) with a
common `assert_full_coverage` guarantee, and the reconciliation decision core
(`values_agree`, `rank_confidence`, `reconcile_pair` → AGREED/A_WINS/B_WINS/
CONFLICT, `group_by_proximity`, `best_by_rank`). What each caller DOES with a
conflict stays its own (candidate values for the resolver vs. human review).
`run_region_extraction(..., trigger_policy=ALWAYS)` keeps the unconditional
default on purpose: a confidently-wrong field never trips a confidence heuristic.

### `pipeline/overview_validate.py` — Stage 11, merged (§1.3)
`overview_check.py` + `overview_macro_validate.py` are one module with named
sub-checks: `check_missing_features` (alias `cross_check`) for the overview IMAGE
vs the build; `check_hole_counts`, `check_correspondences`,
`check_through_vs_blind`, `check_conflict_carryover`, `check_symmetry_advisory`
for the Stage 1.5 WORDS vs the macro package. Advisory by default in both halves;
strict is opt-in. Report artifacts are unchanged.

### `pipeline/dwg_routing.py` — which DWG path (§1.6)
`route_for(path, entry_point=…)` is the single answer, with the full decision in
`docs/DWG_PATHS.md`: `dwg_native/` is a permanent second product (entities are
truth, gate don't resolve), a DWG on the vision path stays there and gains Stage
2.4's exact dimension text, and neither path silently hands a file to the other.

---

## 14. WHERE TO IMPROVE (extension points that respect the invariants)

- **Live-verify HoleWizard5** on the target SW2024/locale, nail the Value-slot mapping,
  and promote it to default in `methods_config.py` once it passes Phase A across the
  golden set. It is now QUARANTINED in `pipeline/experimental/` behind
  `MTI_ENABLE_HOLE_WIZARD` with the exact blocker and revival steps in
  `pipeline/experimental/README.md` (REFACTOR_ANALYSIS §2.1) — if the live
  verification is never scheduled, the honest next step is deletion.
- **Smart sketch dimensioning** (`AddDimension2`) is deliberately opt-in-future — the
  fully-defined gate currently only *observes* under-defined sketches. Verify it live
  before turning it into a fixer.
- **New feature types** (sweep/loft/rib/draft) already have real-or-skeleton builders;
  extend them from MANUAL-step to full geometry the same way revolve/mirror were done —
  build only when the profile/path data is present, never guess.
- **Construction-method library:** when a feature class fails twice on one part, run
  `construction_experiment.py`, record the winner in `METHODS.md` + `methods.json`
  rather than a third blind retry. `lessons_learned.jsonl` and the `Learning Loop/`
  reports are the raw material.
- **New providers/models:** add to the `_OpenAIAdapterClient` pattern and `PRICING`;
  no call site should change. The OpenAI path's real status (adapter-tested, NOT
  production-verified) and the exact steps to promote or remove it are in
  `docs/PROVIDER_STATUS.md` (REFACTOR_ANALYSIS §2.2).
- **C# companion package** is opt-in (`--emit-csharp` / `MTI_EMIT_CSHARP=1`,
  REFACTOR_ANALYSIS §1.7). The VBA package stays the canonical build path; if you
  change `macro_generator`, either update `csharp_macro.py` too or leave it off.
- **Golden discipline:** any intentional macro change goes through
  `$env:UPDATE_GOLDEN=1` with the diff reviewed like code. Never loosen a
  generation-time invariant to make a build pass — fix the upstream value instead.

---

## 15. CONVENTIONS (quick reference)

- A complete approximate model is always correct; an incomplete model is always wrong.
- Numbers chosen from extracted candidates, never invented; grades never fabricated.
- Extraction JSON is additive/backward-compatible; old JSONs must keep loading.
- Never commit `.env` or a real key; `.env.template` stays keyless.
- Reference-geometry naming contract is stable (§Stage-3): `REF_DATUM_<A|B|C>`,
  `REF_SYM_<X|Y>`, `REF_AXIS_<purpose>`, `REF_PT_<feature_id>`.
- Every feature ends `built` / `built_with_flag` / `skipped_prohibited` / `deferred_open`.
- Tiled extraction is an escalation, never the default. `third_party/` is gitignored.
- The web UI never re-implements pipeline logic — it shells out to `main.py`.
- Restart uvicorn after editing `app.py` / pipeline code; refresh the browser for
  static `index.html` edits.

---

*End of EXTREME README — the canonical description of this pipeline. For the terse
working brief (commands, environment, stage index) see `CLAUDE.md`; for the standing
redundancy/improvement audit see `REFACTOR_ANALYSIS.md` at the repo root; for
narrative onboarding see `README.md`; for per-decision history see the dated files in
`docs/`.*
