# Codex Change Review Log (2026-07-25)

Completion + verification pass over the work OpenAI Codex committed on **2026-07-24**.

## Range treated as "yesterday's changes"

- Last commit before the Codex work: **`1b6d5b0`** (2026-07-22 04:13, "docs: two-week change presentation…").
- Codex work: the eight commits authored **2026-07-24** (`898486e` → HEAD `7963530`).
- Range reviewed: **`1b6d5b0..HEAD`** (`git diff 1b6d5b0..HEAD`).
- Two workstreams landed in this range:
  1. **Region-extraction** (steps 1–7): `898486e`, `28a4067`, `dce3f9a`, `3f1b356`, `a329b0e`.
  2. **Full feature coverage + Hole Wizard family** (phases 1–3): `d97e788`, `2dd478c`, `7963530`.

## Baseline (before any edits by this pass)

- `pytest tests/ -q` → **869 passed** in ~136s. (Matches the phase3 notes' claim.)
- All 10 touched pipeline modules + `main` import cleanly (smoke test).
- `pipeline/coordinate_normalize.py` — **NOT touched** in the range (verified via `git diff --stat`). Good: the high-risk canonical resolver was left alone, as its own docstring/phase docs require.
- No dependency manifests changed (`requirements*.txt`, `*.toml`) in the range.

## File-by-file inventory

Status legend (filled in Step 6): **Finished** / **Corrected** / **Verified-complete-as-is** / **Judgment call**.

### New pipeline modules

| File | +lines | What it is | Complete? | Resolution |
|---|---|---|---|---|
| `pipeline/image_coordinates.py` | 248 | Pixel-space resize math + region tiling (region-pass step 1). Imported by `region_extraction`. | Coherent unit; called. | Verified-complete-as-is |
| `pipeline/region_extraction.py` | 651 | Unconditional fixed-region high-res second-read pass; master raster → tile → per-region VLM re-read → merge/reconcile. Wired into `main.py`. | Coherent; public API (`run_region_extraction`, `apply_resolved`, `RegionExtractionResult`) matches the `main.py` call site. | Verified-complete-as-is |
| `pipeline/hole_wizard.py` | 413 | Phase 3b real Hole Wizard family: `plan_wizard_hole`, `resolve_clearance_diameter`, `reconcile_callout_count`, per-subtype builders, `build_wizard_hole` dispatcher. Delegated to by `solidworks_builder._try_hole_wizard`. | Complete; opt-in gate (`MTI_ENABLE_HOLE_WIZARD`) preserved so default sketch-cut never regresses. | Verified-complete-as-is |
| `pipeline/hole_wizard_constants.py` | 129 | Named `swWzd*`/`swEndCond*`/`swStandard*` enums (live typelib + v32 fallbacks) + ANSI clearance table. No inline magic ints. | Complete. | Verified-complete-as-is |

### Modified pipeline / entry points

| File | Δ | Change | Complete? | Resolution |
|---|---|---|---|---|
| `main.py` | +51 | `--region-pass`/`--keep-regions` args + `_run_region_pass_into` (exception-safe, single-drawing path only). | Wiring + arg surface complete; attributes used all exist on `RegionExtractionResult`. | Verified-complete-as-is |
| `pipeline/solidworks_builder.py` | +157/−98 | `_try_hole_wizard` now delegates to `hole_wizard`; new COM `build_shell` (real) + `build_sweep/loft/rib/draft` (real-or-skeleton); registered in `_BUILDERS`. | Dual-engine symmetric; registrations present. | Verified-complete-as-is |
| `pipeline/macro_generator.py` | +90/−9 | VBA `_macro_shell` (real) + `_macro_coverage_skeleton`; `SHELL`→`SUPPORTED`, `PROHIBITED` now empty; dispatch branch added. | Mirrors COM path. | Verified-complete-as-is |
| `pipeline/schema.py` | +29 | `FeatureType.{SWEEP,LOFT,RIB,DRAFT}` + aliases; `HoleType.CLEARANCE`. Additive. | Old JSONs still load. | Verified-complete-as-is |
| `pipeline/resolver.py` | +43 | `_callout_count_flags` (5-vs-6 reconciliation) wired into `resolve_extraction`, non-fatal. | Complete. | Verified-complete-as-is |
| `pipeline/human_assist.py` | +29 | Routes `callout_vs_count` flags into the existing assist queue. | Reuses existing escalation surface. | Verified-complete-as-is |
| `webapp/app.py` | +32 | `_active_engine()` + `engine` in `/api/status`; provider-aware `_has_api_key`. | Backend endpoint complete. | Verified-complete-as-is |
| `webapp/index.html` | +10 | Masthead engine label reads `s.engine.label` (was hardcoded). | Frontend↔backend contract matches. | Verified-complete-as-is |

### Tests / fixtures / docs

| File | Change | Resolution |
|---|---|---|
| `tests/test_hole_wizard.py` | +248 (new, 21 tests) | Verified-complete-as-is (passes) |
| `tests/test_image_coordinates.py` | +161 (new) | Verified-complete-as-is (passes) |
| `tests/test_region_extraction.py` | +259 (new) | Verified-complete-as-is (passes) |
| `tests/test_macro_generator.py` | +35/− | Verified-complete-as-is |
| `tests/test_engineering_review.py` | +14/− | Verified-complete-as-is |
| `tests/golden/bracket/macros/02_F004_MANUAL_shell.vba → 02_F004_Shell_body.vba` | Golden regen: shell MANUAL → real | Verified-complete-as-is |
| `tests/golden/bracket/macros/{README.md,RUN_ALL.vba}` | Golden regen | Verified-complete-as-is |
| `docs/phase{1,2,3}-*.md`, `docs/solidworks-macro-error-log.md` | New research/impl notes + reconstructed E-log | Verified-complete-as-is |
| `Learning Loop/*` | Run reports (data, not code) | Verified-complete-as-is |
| `.gitignore`, `2D-3D-CAD-Test-Generation/.gitignore` | Ignore `reference/` (gitignored mining checkout) | Verified-complete-as-is |

## Verifications run this pass (Step 5)

All performed on this Windows machine with a **live SolidWorks 2024 COM** connection.

- **Test suite:** `pytest tests/ -q` → **870 passed** (869 baseline + 1 test added this pass; see below). No skips, no xfails introduced.
- **`--engine vba` end-to-end** on `A050211E` (via `--from-json`, zero API cost): macro package (9 macros) generated, real `.sldprt` **built** via COM, STL exported, **model validation PASSED**, reconciliation **4/4** confirmed, engineering review 0 urgent items. Exit 0.
- **`--engine com` end-to-end** on `A050211E`: F001 (extrude_boss) + F002/F003 (holes) + F004 (extrude_cut) all built, `.sldprt` saved, STL exported, **model validation PASSED**. Exit 0.
- **FastAPI boot:** `webapp/app.py` imports and serves; `GET /api/status` → 200 with the new `engine` payload (`{provider, model, label, key_present}`) that the masthead now reads. The frontend↔backend contract for the engine-label change is intact.
- **Live COM `InsertFeatureShell`:** confirmed the pipeline's COM builder infrastructure works live (A050211E COM run). The new real `build_shell` path itself is covered by unit tests (`TestFeatureCoverageBothPaths`) + the regenerated golden (`02_F004_Shell_body.vba`) rather than a live shell build, because A050211E has no shell feature and the golden bracket's shell has no extracted thickness (correctly emits the real-or-skeleton WARN step).
- **E-number regression check** (`docs/solidworks-macro-error-log.md`): E004 (`GetModelBoundingBox`) and E006 (`SelectByID2 … "SKETCH"`) — neither reintroduced by any new macro emitter. No other E-pattern is expressible/relevant to the added code.
- **Dead-code / import integrity:** all 10 touched pipeline modules + `main` import cleanly; every new function is called (`image_coordinates`←`region_extraction`←`main`; `hole_wizard`←`solidworks_builder`; `reconcile_callout_count`←`resolver`←`human_assist`); no half-renamed symbols; no stray `print`/`breakpoint`/`pdb`; no commented-out code blocks; no new hardcoded magic numbers (hole-wizard enums are named constants).

### Test added this pass
- `tests/test_region_extraction.py::test_run_region_pass_into_is_exception_safe` — covers the one piece of new wiring lacking a direct test: `main.py::_run_region_pass_into` must never break a run (unreadable drawing → caught + logged, overview `data` untouched). The `region_extraction` module functions were already thoroughly covered (17 tests).

## Required judgment calls (Step 3)

**None.** The Codex work in this range was found to be complete and internally consistent — coherent units of work, both-engine symmetry preserved, additive schema, `coordinate_normalize.py` left untouched, and a full passing test suite. No function was unfinished, no import broken, no refactor left half-applied. This pass therefore **finished nothing and corrected nothing**; it **verified** the work end-to-end (both engines, live COM, FastAPI, tests) and added one missing test for the newest glue. That "nothing needed finishing" is itself the reviewed outcome, not an assumption — each item above is marked *Verified-complete-as-is* on the evidence listed here.
