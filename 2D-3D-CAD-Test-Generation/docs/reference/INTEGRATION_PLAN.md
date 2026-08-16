# INTEGRATION PLAN — reference docs → this pipeline

Phase 2 of `INTEGRATE_PIPELINE_DOCS.md`. Ordered by the ranked queue in
`TRIAGE.md`. Each phase states: files touched, files created, how it is tested,
and what "done" means. Phases are checked off with a result note as they land.

Ground rules carried from the brief: extend existing modules over creating
parallel ones; every new module is tested against a REAL drawing end-to-end, not
only a unit test; do not touch the build-engine language/architecture.

---

## Phase A — Projection angle (doc 04) ✅ DONE

**Why first:** a misread projection angle mirrors the entire part, and the
pipeline currently has no field for it, so it cannot notice.

* **Files touched:** `pipeline/schema.py` (add `projection_angle` +
  `projection_angle_source`), `pipeline/extractor.py` (prompt asks for the
  symbol), `pipeline/validation.py` (mirror-risk check).
* **Created:** none.
* **Test:** unit tests for the field + the inference rule + the check; then a
  real `--from-json` run on A050211E and 16247 confirming old extractions
  without the field still load (additive-schema rule).
* **Done when:** the field round-trips, an unstated angle is INFERRED from the
  title-block/unit evidence and recorded as an assumption (never silently
  assumed), and the scorecard raises a mirror-risk item when the angle is
  unknown or conflicts with the drawing's own evidence.

**Result:** landed. `projection_angle` (`third_angle|first_angle|unknown`) and
`projection_angle_source` on `DrawingData`; `infer_projection_angle()` applies
the doc's rule (ANSI/inch → third, ISO/mm → first) and always reports its basis;
`validation.py` raises `PROJECTION_UNKNOWN` (mirror risk) when nothing is
recorded. Old JSONs load unchanged (asserted by test).

## Phase B — Unified validation scorecard (doc 07) ✅ DONE

**Why:** the repo measures a great deal and concludes nothing in one place.

* **Files touched:** `pipeline/batch.py` (call it after the build), `main.py`
  (single-drawing path).
* **Created:** `pipeline/validation.py` + `tests/test_validation_scorecard.py`.
* **Design:** assemble from artifacts already on disk plus two genuinely new
  measurements, all from the exported STL via `trimesh` (the repo's existing
  measurement idiom — the C# `IMassProperty` route in the doc does not resolve
  under this project's late-bound COM, documented in
  `solidworks_builder.check_rebuild_errors`):
  * Layer 1 rebuild health ← `macro_result.json` / build caveats;
  * body count + watertightness ← STL;
  * bbox vs expected ← `model_validator` output;
  * **volume ratio** (solid ÷ bbox) — NEW, the doc's "fingerprint" check;
  * **centre-of-mass symmetry** — NEW, catches a one-sided feature on a part
    the drawing says is symmetric;
  * hole audit ← `feature_verify.json` (already stronger than the doc's
    cylindrical-face count: it checks position too);
  * MM constraints ← `constraint_verification.json`.
* **Verdict:** `PASS` / `PASS_WITH_ASSUMPTIONS` / `FAIL`, with the rule from the
  doc: any Layer-1 failure ⇒ FAIL; all-pass with low-confidence assumptions ⇒
  PASS_WITH_ASSUMPTIONS.
* **Test:** unit tests for each layer + verdict rule; end-to-end on a real part
  with a live SolidWorks build.
* **Done when:** every real part produces `validation.json` with a verdict, and
  the verdict is consistent with the existing READY gate (it must not contradict
  it — the scorecard explains the gate, it does not replace it).

**Result:** landed. `validation.json` on every run; verified end-to-end on a live
SolidWorks build of 16247 (`FAIL`, correctly — the drawing's 19.25 overall height
vs the built 18.25) and on A050211E (`PASS_WITH_ASSUMPTIONS`). Volume ratio and
CoM symmetry are new information nothing else in the pipeline had.

## Phase C — Per-step evidence + unused-dimension self-check (doc 05) ✅ DONE

* **Files touched:** `pipeline/macro_generator.py` (`BuildStep.evidence`, into
  `build_plan.json`), `pipeline/validation.py` (dimension-coverage check).
* **Created:** none.
* **Test:** unit test that every solid step carries evidence naming its
  dimensions; corpus run asserting the coverage check reports real orphans.
* **Done when:** each build step records the dimension ids and view behind it,
  and the scorecard reports dimensions no step consumed.

**Result:** landed. Every step carries `evidence` (dimension ids + view + the
source values); `validation.py` reports `UNUSED_DIMENSIONS` as an advisory
finding (never a FAIL — an unconsumed reference dimension is legitimate).

## Phase D — Delivery report (doc 09) ✅ DONE

* **Files touched:** `pipeline/engineering_review.py` (extend, do not replace).
* **Created:** none (`<Part>_delivery_report.txt` is written by the extended
  module).
* **Test:** unit tests for ordering (lowest confidence first), the single ask,
  and graceful behaviour with no assumptions; real-part run.
* **Done when:** one report contains result summary → assumptions ranked
  lowest-confidence-first → unresolved failures → exactly one compact ask.

**Result:** landed. `write_delivery_report()` assembles the existing scorecard,
assist queue and review items into the doc's four-section shape. It adds no new
judgement — pure assembly, as the brief requires.

## Phase E — Doc 10 → error ledger merge ✅ DONE

* **Files touched:** `docs/solidworks-macro-error-log.md`.
* **Done when:** doc 10's environment/COM/geometry tables live in the ledger,
  with the repo's own live findings kept where they contradict the generic
  advice, and one canonical troubleshooting doc remains.

**Result:** landed as a new "Environment, COM and geometry failure modes"
section, cross-referenced from `docs/reference/10_common_failure_modes.md`.
Three entries annotated where this repo's live experience differs from the
generic guidance.

## Phase F — COM stability: retry filter + RCW release (doc 02) ✅ DONE

* **Files touched:** `pipeline/solidworks_builder.py`.
* **Test:** unit tests with a fake COM object (no SolidWorks needed) + a live
  build to prove nothing regressed.
* **Done when:** a busy-server COM error retries with backoff instead of failing
  the run, and COM objects created in loops are released.

**Result:** landed. `com_retry()` wraps the busy/retry-later HRESULT family with
bounded backoff; `release_com(obj)` releases RCWs and is used in the body/face
enumeration loops. Live build unaffected.

## Phase G — Per-feature `GetErrorCode2` walk (doc 02/07)  — NOT DONE (deliberate)

Lowest-leverage item in the queue, and the existing
`check_rebuild_errors` docstring records a live finding that the rebuild-error
APIs do **not** resolve under this project's late-bound dispatch on this
install. Adding a second API that probably shares that fate — without a live
failing part to verify against — would be speculative work of the exact kind
`REFACTOR_ANALYSIS.md` §2.1 just removed. Recorded here as the honest next item
for someone with a reproducing part.
