# TRIAGE — the 11 reference docs vs. this repo

Phase 1 of `INTEGRATE_PIPELINE_DOCS.md`. Every verdict below was checked against
the code, not assumed; the citation column names the file/function that settles
it. Where the repo already does something by a *different mechanism* than the
doc proposes, that is ALREADY IMPLEMENTED with the mechanism noted — the
guarantee is what matters, not the API used to get it.

Date: 2026-08-16. Branch: `REFRACTOR_UI`.

## Verdicts

| Doc | Verdict | Why (with citation) |
|---|---|---|
| 00 overview | CONTEXT | Index only; no implementable claims. Its pipeline loop (parse → plan → generate → validate → self-repair → report) is the loop this repo already runs (`main.py`, `pipeline/batch.py::process_drawing_data`). |
| 01 language choice (C# over VBA) | **CONFLICT — flagged, not acted on** | Repo is Python + `win32com` with a mature direct-COM engine as the primary path (`pipeline/solidworks_builder.py`, `main.py --engine com`) and VBA as the portable review artifact. Per the integration brief this is Joe's call; see "Flagged decision" below. Note: the repo ALREADY emits a C# companion program (`pipeline/csharp_macro.py`, opt-in `--emit-csharp`), so the doc's capability exists without a rewrite. |
| 02 API fundamentals | **MOSTLY ALREADY / 3 ADAPT items** | Units boundary: `coordinate_normalize.INCH_TO_M` + `to_meters` is the single conversion, at the CAD boundary only (doc's Rule #1) ✔. Template from preference + null check: `solidworks_builder.resolve_part_template` ✔ (hardened 2026-08-16). Selection hygiene: 20 × `ClearSelection2`, checked `SelectByID2` returns ✔. Checked return values: `Require`-equivalent raises with context throughout ✔. Sketch lifecycle + `AddToDB`/`DisplayWhenAdded` ✔ (3/2 uses). **Gaps:** no `Marshal.ReleaseComObject` (RCW leak over long batches), no COM retry filter for `RPC_E_SERVERCALL_RETRYLATER`, no per-feature `GetErrorCode2` walk. |
| 03 macro/automation best practices | **ALREADY IMPLEMENTED — audit clean** | Audited for every anti-pattern in the doc's table: `SendKeys` 0 hits, hardcoded `Boss-Extrude1`-style selection 0 hits, bare `except:` / silent `except: pass` 0 hits in the two build modules, magic enum integers → `_const()` named lookups, plan-as-interpreter → `build_plan.json` drives every path. Naming discipline: `_rename_feature` right after creation. Per-step logging: `logs/macro_result.json` + `LogResult` in every feature macro (enforced by `macro_audit`). **No busywork manufactured — this doc describes what the repo already does.** |
| 04 reading engineering drawings | **1 NEW CAPABILITY, rest ALREADY** | Title block, units, scale (`schema.py:739`), general tolerance (`schema.py:742`), Ø vs R, TYP/qty (`callout_qty.py`), thread callouts → tap-drill (`drill_sizes.py`), cbore/csk, THRU vs depth, reference dims, chained-vs-baseline resolution (`position_solver.py`) — all present. **GAP: projection angle (first vs third) is nowhere in the schema.** The doc is explicit that getting it wrong *mirrors the part*, and nothing in the pipeline records or checks it. Highest-leverage single finding in this triage. |
| 05 2D→3D interpretation strategy | **2 NEW CAPABILITIES** | Base-feature choice, dependency ordering, symmetry/patterns, third-dimension resolution: all implemented (`build_sequencer.py` seven stages; `resolver.py` Stage 2.5). **GAP 1:** no per-step `evidence` field tying a feature to the source dimensions/view. **GAP 2:** no plan self-check that every extracted dimension is consumed by exactly one step — the doc's "unused dimensions = something was missed" test, which is a cheap missed-feature detector. Part-archetype classification is implicitly covered by the base-solid choice; not worth a separate field. |
| 06 geometry construction playbook | **ALREADY IMPLEMENTED** | Every recipe has a counterpart: sketch/extrude/cut/revolve/fillet/chamfer/pattern/mirror/shell in `solidworks_builder.py` + `macro_generator.py`, with the doc's own recommended fallbacks already chosen as defaults — plain sketch-circle cut over Hole Wizard (the doc calls the wizard "the most version-volatile"; this repo live-verified that and removed it, `pipeline/experimental/README.md`), and reference planes over face hunting (`reference_geometry.py`). `methods_config.py` + `METHODS.md` are the machine-readable version of this doc. |
| 07 validation & self-checking | **PARTIAL — highest-leverage NEW work** | Present: single-solid-body + bbox-vs-drawing (`model_validator.py`), per-feature measured verdicts (`feature_verify.py` — the doc's "hole audit", done from the STL rather than cylindrical faces, which is stronger because it also catches position), MM constraint grading (`constraint_verify.py`), rebuild health (`check_rebuild_errors`). **Gaps:** no mass-properties sanity (volume ratio, centre-of-mass symmetry), no per-feature `GetErrorCode2` walk, and — the real one — **no single scorecard**: the verdict is spread across six artifacts with no `PASS / PASS_WITH_ASSUMPTIONS / FAIL` line anywhere. |
| 08 autonomous error handling | **ALREADY IMPLEMENTED** | The escalation ladder is `resolver.py`'s plausibility ladder + `deferred_retry.py` (taxonomy → escalating playbook, never repeats a strategy) + `human_assist.py` (defer to the user at the END, with a default that ships). Retry budget of 3 with no-identical-retry and oscillation detection is `retry_ladder.py` (one shared contract, three callers). Symptom→cause table ≈ `deferred_retry._TAXONOMY`. Defaults table ≈ the resolver's basis ladder. Hard-stop-on-unusable-drawing ≈ the no-closed-outer-profile failure. |
| 09 user interaction protocol | **PARTIAL — ADAPT** | Zero-questions-during-run ✔ (the pipeline never blocks). End-of-run questions with a shipping default ✔ (`human_assist.py`, Sheet-4 review queue). Severity-ranked human report ✔ (`engineering_review.py`). **Gap:** no single Delivery Report in the doc's shape — result summary, assumptions ranked LOWEST-CONFIDENCE FIRST, unresolved failures, and one compact ask. The data all exists; it is not assembled into one document. |
| 10 common failure modes | **MERGE** | `docs/solidworks-macro-error-log.md` (E001–E011) is the canonical ledger and already records several of the doc's entries from live experience. Per the brief: merge doc 10's environment/COM/geometry tables INTO that ledger rather than creating a competing file. |

## Ranked work queue (leverage on part-build correctness ÷ effort)

| # | Item | Doc | Why this rank |
|---|---|---|---|
| 1 | Projection-angle capture + mirror check | 04 | A misread projection angle mirrors the whole part. Currently unrepresented anywhere — the pipeline cannot even notice. Cheap to add, catastrophic to miss. |
| 2 | Unified validation scorecard (`validation.json`) | 07 | The repo measures a great deal and concludes nothing in one place. One verdict line makes every other check actionable; adds volume-ratio + CoM-symmetry, which no existing check covers. |
| 3 | Per-step `evidence` + unused-dimension self-check | 05 | Traceability from a failed check back to the drawing dimension that caused it, plus a genuine missed-feature detector for free. |
| 4 | Delivery report (ranked assumptions + one ask) | 09 | Turns existing data into the single artifact a human acts on. No new measurement, pure assembly. |
| 5 | Doc 10 → error-ledger merge | 10 | Low effort, keeps ONE troubleshooting doc (the alternative is exactly the two-competing-docs problem `REFACTOR_ANALYSIS.md §1.8` just fixed). |
| 6 | COM stability: retry filter + RCW release | 02 | Real, but it manifests as flakiness in long batches, not wrong geometry. Below correctness items. |
| 7 | Per-feature `GetErrorCode2` walk | 02/07 | The existing `check_rebuild_errors` already catches the failure it can (see its docstring: the count APIs do not resolve under late binding on this install); this would add per-feature attribution. Lowest leverage of the real items. |

## Flagged decision for Joe (NOT acted on)

**C# standalone app vs. the current Python/COM engine (doc 01).** The doc's
reasoning is sound in the abstract — a compile-time feedback loop catches
argument/type errors before CAD ever launches, and `.swp` binaries are unusable
in an agentic workflow. Two facts change the calculus here: this repo's build
engine is **Python driving COM directly**, not VBA, so the "`.swp` is a binary
blob" objection does not apply (the source is `.py`, diffable and editable); and
its VBA output is a *generated text artifact* checked by four static guards
(`macro_audit`, `macro_echo`, `macro_semantics`, `overview_validate`), not
hand-written code. The compile-check argument still has real force — Python +
late-bound COM gets no signature checking at all, which is precisely how
`HoleWizard5` could return `None` for months undetected. A middle path already
exists in the repo: `csharp_macro.py` emits a compilable C# program from the
same build plan (`--emit-csharp`), so the compile-loop benefit is available
without rewriting the engine. **Recommendation: keep the Python/COM engine;
if the compile-time loop is wanted, invest in the existing C# emitter rather
than a rewrite.** Joe's call — not applied.
