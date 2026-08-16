# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

**Scope of this file (changed 2026-08-15, REFACTOR_ANALYSIS §1.8/§2.5):** commands,
environment, invariants, and a stage INDEX — the things an agent needs turn to
turn. The full stage-by-stage narrative lives in ONE place:
[`2D-3D-CAD-Test-Generation/EXTREME_README.md`](2D-3D-CAD-Test-Generation/EXTREME_README.md),
which is the **canonical** description of the pipeline. This file used to carry a
second copy of that narrative; two hand-maintained descriptions of the same 13
stages drift, and a stale one is worse than no second copy. When they disagree,
EXTREME_README wins — and the disagreement is a bug to fix, not a judgment call.

## What this is

MTI 2D→3D pipeline: converts 2D engineering drawings (PDF/PNG/JPG/DWG/DXF/eDrawings)
into SolidWorks 2024 parts via Claude Vision extraction → ambiguity resolution →
verification → VBA macros → `.sldprt`/`.stl` (COM, Windows-only). All code lives in
`2D-3D-CAD-Test-Generation/`; test drawing sets live under `test_drawings/`
(`Test2/`, `E2ETest/`, `DrawingPDFs/`, etc.).

## Commands

`python` is often NOT on PATH on this machine — use the webapp venv interpreter:
`2D-3D-CAD-Test-Generation\webapp\.venv\Scripts\python.exe`.

All commands run from `2D-3D-CAD-Test-Generation/`:

```powershell
# Tests (conftest.py adds the project root to sys.path)
python -m pytest tests/ -q
python -m pytest tests/test_multiview.py -q          # single suite

# Golden macro snapshot (tests/test_golden_macros.py): after an INTENTIONAL
# change to macro output, regenerate and review the diff like code
$env:UPDATE_GOLDEN=1; python -m pytest tests/test_golden_macros.py -q

# Full pipeline on a batch of part folders (the standard test-batch command)
python main.py --views-folder ..\test_drawings\Test2 --output ..\test_drawings\Test2\output

# Single drawing; extract+verify only (no SolidWorks, cheapest live check)
python main.py --drawing path\to\drawing.pdf --validate-only --debug

# Rebuild from a saved extraction — zero API cost
python main.py --from-json <Part>_extraction.json --output .\output

# Web UI (FastAPI on http://127.0.0.1:8092/) — creates .venv + installs pinned deps on first run
cd webapp; .\run.ps1

# DWG-native pipeline UI (separate product, http://127.0.0.1:8095/) — Windows + SolidWorks
.\run-dwg.ps1
```

Useful `main.py` flags: `--no-sldprt` (macros/reports only), `--no-export` (skip copy
to `~/Downloads/SolidWorksModel_Parts`), `--no-extract-cache` (force paid
re-extraction), `--skip-overview-check` / `--skip-requirements-check` (bypass the
final READY gates), `--strict-gate` (block on failing verification), `--region-pass
on|off`, `--keep-regions all|conflicts-only|none`, `--no-dwg-crosscheck`,
`--emit-csharp` (opt-in C# companion package; OFF by default). Exit codes: 0 = all
parts READY, 8 = completed but not all READY, 2 = bad args.

Alternate entry points: `run_models.py <folders> --output ./output` (batch driver:
extract → auto-resolve → build NON-STRICT so the run always completes, writing
`<Part>_design_decisions.txt` per part); `build_sldprt.py <output_root>` (COM-builds
every saved `<part>_extraction.json` under a root into `.sldprt`, one shared
SolidWorks session, zero API cost).

Setup: `python setup.py`, then put `ANTHROPIC_API_KEY` in `.env` (gitignored).
Optional env: `EXTRACTION_MODEL` (default `claude-sonnet-5`),
`SOLIDWORKS_TEMPLATE_PATH` (must point at a real `.prtdot`), `MAX_IMAGE_LONG_EDGE`,
`AI_PROVIDER` (see `docs/PROVIDER_STATUS.md`), `MTI_EMIT_CSHARP`.

**Web UI dev gotcha:** after editing `webapp/app.py` or pipeline code, restart the
uvicorn server on 8092 — it otherwise serves stale endpoints.

## Guiding principle (this is the one to internalize)

> *A complete approximate model is always the correct outcome; an incomplete model
> is always the wrong outcome* — resolve and flag, never block or silently drop.

Corollaries that are enforced in code, not just intent:

- Numbers are chosen from extracted candidates, **never invented**; compliance
  grades are never fabricated (non-geometric notes → `not_applicable`).
- Every extracted feature ends in a named end-state: `built` / `built_with_flag` /
  `skipped_prohibited` / `deferred_open`. There is no silent skip.
- Extraction JSON is backward-compatible: new fields are additive; old JSONs must
  keep loading (`--from-json`).
- Never commit `.env` or a real API key; `.env.template` stays keyless.
- The DWG-native pipeline has DIFFERENT principles on purpose (gate, don't
  resolve) — see `docs/DWG_PATHS.md` before applying these there.

## Stage index

One line per stage: what it owns and where it lives. **Narrative, rationale and
worked examples: EXTREME_README §6.**

| Stage | Module | Owns |
|---|---|---|
| 1 | `utils/image_prep.py` | normalize/downscale input images |
| 1.2 | `utils/tiled_extraction.py` | tiled high-res zoom pass — **escalation**, not default |
| 1.5 | `overview_analysis.py` | holistic full-sheet relational read (tier 2); `overview_analysis.json` |
| 2 | `extractor.py` | one Vision call per part, forced tool call, specs-first; on-disk cache |
| 2.3 | `region_extraction.py` | unconditional fixed-region high-res re-read + field merge |
| 2.4 | `dwg_crosscheck.py` | DWG's exact dimension text corrects OCR digits |
| 2.5 | `resolver.py` | **the core design decision** — resolve every ambiguity to a defensible number, never block |
| 2.6 | `must_meet.py` | operator must-meet specs → `MM-xxx` constraints (tier 0, override) |
| 3 | `vector_extract/`, `hole_resolution.py` | exact hole positions from vector geometry |
| 6 | `validator.py` | arithmetic/envelope verification (advisory unless `--strict-gate`) |
| 6.5 | `build_sequencer.py` | the ONE deterministic build order + per-feature dispositions |
| 7 | `macro_generator.py`, `macro_audit.py`, `macro_echo.py`, `macro_semantics.py` | numbered VBA + generation-time invariants (structure, literals, **operations**) |
| 8 | `cq_prevalidate.py` | headless CadQuery build of the same plan; a failure aborts the COM build |
| 9 | `solidworks_builder.py`, `deferred_retry.py` | COM `.sldprt` + STL; failed features quarantined and retried |
| 10 | `constraint_verify.py` | measured STL vs must-meet constraints |
| 10.5 | `reconciliation.py` | build vs the RAW extraction; bounded re-resolution + splice |
| 10.6 | `feature_verify.py` | every planned feature measured: OK/MISSING/MISPLACED/WRONG_SIZE/EXTRA |
| 10.7 | `reconciliation.py::geometric_correction_loop` | bounded build→measure→correct→rebuild |
| 10.8 | `human_assist.py` | the exit ramp: narrow questions, defaults that still ship |
| 11 | `overview_validate.py` | overview image vs build **and** overview words vs macro package |
| 11.5 | `validation.py` | the ONE scorecard: PASS / PASS_WITH_ASSUMPTIONS / FAIL over every measured layer (`validation.json`) |
| 12 | `engineering_review.py` | the severity-ranked human report **and** the doc-09 delivery report (`<Part>_delivery_report.txt`) |
| 13 | `learning_loop.py` | per-run failure brief into `Learning Loop/` |

### Cross-cutting modules (added/consolidated 2026-08-15)

| Module | Owns |
|---|---|
| `coordinate_authority.py` | **where a feature is** — the ordered phases + precedence over the five position modules (§1.1) |
| `feature_ledger.py` | the canonical per-feature stage-tagged history; `<Part>_feature_ledger.json` (§1.2) |
| `retry_ladder.py` | the ONE bounded-retry contract: cap, progress, oscillation, exhaustion (§1.5) |
| `highres_pass.py` | shared window planning + reading reconciliation for both second-look systems, with pluggable trigger policy (§1.4) |
| `overview_validate.py` | both Stage-11 overview checks, named sub-checks, one module (§1.3) |
| `dwg_routing.py` | which of the two DWG products handles a file (§1.6) |
| `macro_semantics.py` | the emitted macros perform the OPERATIONS the plan specifies — the verbs, where `macro_echo` checks the nouns |
| `coordinate_normalize.py` | semantic anchor → global coordinate, and the ONE inch→meter conversion |
| `position_solver.py` | the drawing's own dimensioning scheme, solved topologically |

## Naming contracts (don't rename without updating both sides)

- **Reference geometry:** `REF_DATUM_<A|B|C>` (datum planes), `REF_SYM_<X|Y>`
  (symmetry mid-planes), `REF_AXIS_<purpose>` (centerlines/pattern axes),
  `REF_PT_<feature_id>` (pattern origins/anchors). These are stable selection
  handles the deferred-retry loop relies on.
- **Disposition states:** `BUILT` / `BUILT_WITH_DERIVED_VALUE` /
  `EXCLUDED_INCOMPLETE`, plus the `NEEDS_HUMAN_INPUT` overlay.
- **Position sources (precedence order):** `spec_driven` > `vector_geometry` >
  `anchor_solver` > `resolved_dimension` > `committed_conservative`.
- **Dimension collisions** (two of a feature's dimensions canonicalizing to the
  same key): first-declared wins, UNLESS a label declares totality
  (`overall_*`, `total_*`, …) — that one measures the whole extent by
  definition. One owner: `schema.collapse_dimension_values`, used by the build
  sequencer, the VBA generator AND the COM builder. Never re-implement it: when
  the COM path had its own `setdefault` version, it built a part half the
  drawing's width while CadQuery built it correctly.

## Where else to look

| Question | Document |
|---|---|
| How does stage X actually work? | `EXTREME_README.md` §6 (canonical) |
| What is redundant / what to refactor next? | `REFACTOR_ANALYSIS.md` (repo root) |
| Which DWG path handles this file, and why are there two? | `docs/DWG_PATHS.md` |
| Is the OpenAI provider safe to use? | `docs/PROVIDER_STATUS.md` |
| Why was HoleWizard5 removed? | `pipeline/experimental/README.md` (live evidence) |
| How is a feature type actually constructed? | `pipeline/METHODS.md` |
| What do the external reference docs say, and what did we adopt? | `docs/reference/TRIAGE.md`, `docs/reference/INTEGRATION_PLAN.md` |
| Web UI layout, sheets, endpoints | `EXTREME_README.md` §10 |
| Every output artifact per part | `EXTREME_README.md` §11 |

`third_party/` is a gitignored mining checkout (codestack idioms), never a runtime
dependency.
