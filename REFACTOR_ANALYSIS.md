# REFACTOR_ANALYSIS.md — Redundancy & Improvement Audit

> Companion to `CLAUDE.md` and `EXTREME_README.md`. This document is the output of a
> full-system audit of the MTI 2D→3D CAD pipeline, focused on **what is redundant and
> can be consolidated or removed**, and **what are the highest-leverage improvements**
> to make next. Written for a human or an agent (Claude Code) planning refactor work —
> not a description of current behavior, but a prioritized action list.

---

## STATUS (2026-08-15, branch `REFRACTOR_UI`)

Every item below has been actioned. This section is the index of what landed; the
original analysis text follows unchanged, so the reasoning that motivated each
change stays readable next to the result.

| # | Item | Status | Where it landed |
|---|---|---|---|
| 1.1 | Five "sole coordinate authority" claims | **DONE** | `pipeline/coordinate_authority.py` — ordered phases, strict precedence, one entry point; `tests/test_coordinate_authority.py` |
| 1.2 | Six parallel per-feature status ledgers | **DONE** | `pipeline/feature_ledger.py` — append-only stage-tagged history; `summary_view` migrated first; `<Part>_feature_ledger.json` written by `batch.py` |
| 1.3 | Two Stage-11 overview checks | **DONE** | `pipeline/overview_validate.py` — merged, named sub-checks; old modules deleted |
| 1.4 | Two high-res re-read systems | **DONE** | `pipeline/highres_pass.py` — pluggable trigger policy + shared window planning + shared reconciliation core |
| 1.5 | Three retry loops | **DONE** | `pipeline/retry_ladder.py` — all three call sites converted |
| 1.6 | `dwg_native/` ambiguity | **DONE (option a)** | `docs/DWG_PATHS.md` + `pipeline/dwg_routing.py` — permanent second product, explicit routing, parity checklist |
| 1.7 | C# emitted on every run | **DONE** | opt-in `--emit-csharp` / `MTI_EMIT_CSHARP`; default OFF |
| 1.8 | Two hand-maintained mega-docs | **DONE** | `EXTREME_README.md` is canonical; `CLAUDE.md` rescoped to commands/invariants/stage-index |
| 2.1 | HoleWizard5's fate | **DONE (quarantined)** | `pipeline/experimental/` + README with the exact blocker, revival steps, and the delete-if-never-scheduled decision |
| 2.2 | OpenAI provider status | **DONE** | `provider_status()` + runtime warning; real call-site contract tests; `docs/PROVIDER_STATUS.md` |
| 2.3 | Feature state machine as a module | **DONE** | same as 1.2 |
| 2.4 | DWG path ambiguity before more DWG work | **DONE** | same as 1.6 |
| 2.5 | Single-source the mega-docs | **DONE** | same as 1.8 |
| 2.6 | Coordinate-precedence test coverage | **DONE** | `tests/test_coordinate_authority.py` (full chain, disagreement reporting, real solver output) |
| 2.7 | One retry-ladder test suite | **DONE** | `tests/test_retry_ladder.py` (cap, ordering, oscillation, exhaustion, callers-share-the-contract) |

**Deliberately NOT done** (§3 non-goals): resolver vs must_meet, constraint_verify
vs feature_verify, VBA vs COM, and macro_audit vs macro_echo all stay separate.

---

## ROUND 2 (2026-08-16) — what running everything actually found

The consolidation above was verified by unit tests. Round 2 ran the *system*:
every module imported, all 15 saved extractions rebuilt through the real CLI,
every webapp route exercised, a live SolidWorks COM build, and a live call
through the OpenAI adapter. That surfaced **six real defects that no unit test
saw** — five pre-existing (reproduced on the pre-refactor commit before fixing),
one introduced by the consolidation itself.

| # | Defect | Found by | Fix |
|---|---|---|---|
| R1 | `A001271E` crashed generation: two independently-dimensioned .531 hole groups were treated as instances of ONE callout because sibling-hood was decided by DIAMETER alone, so both collapsed to (0,0) and the overlap guard refused the build | rebuilding all saved extractions | `macro_generator._shares_callout_siblings` — a same-diameter feature that owns its own callout is a separate group. All 8 holes now drill at their dimensioned positions |
| R2 | `A001821M` crashed generation: the echo check demanded a geometry literal from a cosmetic-thread step that emits none by design | same sweep | `macro_echo._is_manual_only_step` — exempt ONLY steps that emit no geometry AND are `needs_review`; a `generated` step that dropped its literals still fails |
| R3 | **Every COM build on this machine failed**: `SOLIDWORKS_TEMPLATE_PATH` still named a 2024 path after an upgrade to 2026, and the configured path was treated as authoritative | live COM build | `solidworks_builder.resolve_part_template` — configured path → SolidWorks' own preference → filesystem search (newest version, plain template preferred); a stale setting is a reported fallback, not a hard stop |
| R4 | **The base solid was built at HALF WIDTH** on 16247: `total_flange_width` (2.0) and `flange_width` (1.0) both canonicalize to "width", and first-declared kept the component | measuring the built STL's bounding box | totality-aware collision policy (below) |
| R5 | **COM and CadQuery silently built different shapes** from the same plan (1.0 vs 2.0 wide) because the collision rule was implemented THREE times — sequencer, VBA generator, COM builder — and the COM one used a bare `setdefault` | comparing both builders' bounding boxes | one owner: `schema.collapse_dimension_values`, used by all three. All three builders now measure identically |
| R6 | Every explainer endpoint 500'd when the app is imported as a package (`webapp.app`) rather than via `--app-dir` | route sweep | `webapp.app._explainer()` resolves either import style; a test now pins both |

**On R4/R5 — a deliberate revision of an earlier decision.** The 2026-07-28 rule
("first-declared wins, never biggest-wins") was right to reject a magnitude
heuristic, but it discarded a stronger signal: the label itself. A dimension
labelled `total_*` / `overall_*` states, in the drawing's own words, that it
measures the whole extent. First-declared remains the default; totality now wins
a collision. This is not a return to "biggest wins" — a *smaller* total still
wins, and that case is asserted in the tests. The evidence for the change was a
part built at half its drawing size, measured off the STL.

**Verification after round 2:** 1130 tests pass (baseline 981); all 15 saved
extractions rebuild with zero errors; the two parts that exit 8 do so for genuine
drawing ambiguity (an extraction declaring 4 instances with one dimensioned
position), correctly flagged with an assist question rather than guessed.

**Still open, and honestly so:** promoting the OpenAI path to "production" needs
the batch quality comparison in `docs/PROVIDER_STATUS.md` (its *plumbing* is now
live-verified — see that file); promoting HoleWizard5 needs the live session in
`pipeline/experimental/README.md`. Both are judgement calls that need a decision
about cost/scope, not more code.

---

**Known follow-ups created by this work** (recorded, not hidden):
- The six source artifacts are still written by their stages; the ledger is a view
  over them. Retiring each file is the incremental next step, per §1.2's own plan.
- ~~`dwg_native/` does not yet write ledger entries~~ — **closed 2026-08-16**: it
  writes `<part>_feature_ledger.json` like the vision pipeline, and the ledger
  reads both naming conventions.
- Promoting or deleting the OpenAI path needs the batch comparison in
  `docs/PROVIDER_STATUS.md`; promoting or deleting HoleWizard5 needs the live
  session in `pipeline/experimental/README.md`. Both are decisions that require a
  machine/run this refactor could not perform.

---

## 0. HOW TO USE THIS DOCUMENT

Each item below names the specific modules/files involved, why they're redundant or
risky, and a concrete consolidation/fix direction. Items are ordered roughly by
impact × cost-to-fix. Before touching anything here, re-read `CLAUDE.md` §"Guiding
principles" — none of these changes should compromise the "never block, never
fabricate, always flag" invariant. A refactor that quietly makes the pipeline block
on ambiguity, or that removes a flag instead of surfacing it more clearly, is a
regression even if it "simplifies" the code.

---

## 1. REDUNDANCIES (consolidate or remove)

### 1.1 Five independent "sole coordinate authority" claims — HIGHEST PRIORITY
Modules that each independently describe themselves as owning position resolution:
- `resolver.py::_feature_positional_xy` — consumes positional dimensions before any
  escalation (the Bug-1 fix).
- `position_solver.py` — "the sole coordinate authority for anchored features";
  topological anchor-graph solve.
- `coordinate_normalize.py` — "the ONE place semantic anchors become global CAD
  coordinates"; owns the `y = parent_height - depth` math and inch→meter conversion.
- `hole_resolution.py` — vector-geometry-vs-vision-callout position precedence.
- `build_sequencer.py::_feature_xy` — slot-aware position getter used at emission time.

**Why this is a problem:** each module was introduced to fix one specific historical
bug (158-C notch orientation, A001271E hole placement, Bug-1 dropped positions) rather
than as part of one designed coordinate system. The result is that "where is this
feature" has five possible answers depending on which stage you ask, and cross-module
invariants (e.g. "the solver is now authoritative as of 2026-07-21") had to be bolted
on after the fact to establish precedence between them.

**Fix direction:** Define one coordinate-resolution pipeline with clearly ordered
phases — (1) anchor-graph topological solve, (2) semantic-anchor → global-coordinate
normalization, (3) vector-vs-vision precedence merge, (4) build-plan emission — living
in one module or one clearly-layered package, with position_solver.py's authority
already established as the final phase. Do not merge the *logic* (vector-position
precedence and anchor-graph solving are genuinely different jobs) — merge the
*ownership story* so there's one entry point and one doc section, not five.

> **Landed as:** `pipeline/coordinate_authority.py`. The phases are recorded in
> RUNTIME order (vector merge → resolution → anchor solve → normalize/emit) rather
> than the order sketched above, because that is the order the pipeline actually
> executes; the precedence is a strict, tested order and losing candidates are
> reported by `disagreements()` instead of being averaged away.

### 1.2 Six parallel per-feature status ledgers
`_build_dispositions.json` (duplicated *again* inside `build_plan.json`'s own
`dispositions` key), `_reconciliation_report.json`, `_feature_verification.json`,
`_geometric_loop_report.json`, `_assist_queue.json`, `_deferred_log.json` all track
feature-level state through the back half of the pipeline. `summary_view.py` already
has to reconcile across most of these to render one UI table.

**Fix direction:** One canonical per-feature ledger, keyed by feature id, with a
stage-tagged append-only history (`{stage, status, basis, timestamp, detail}`). Each
existing file becomes either a filtered view generated from the ledger, or is retired.
Do this incrementally — `summary_view.py` is already a pure presentation layer reading
these files, so it's the natural first consumer to migrate and the safest place to
verify the consolidation didn't lose information.

> **Landed as:** `pipeline/feature_ledger.py`; `summary_view.py` migrated first and
> its frozen golden fixtures are byte-identical through the ledger.

### 1.3 Two Stage-11 "diff the build against the overview" checks
`overview_check.py` (missing visible feature vs. the overview drawing) and
`overview_macro_validate.py` (hole-count/correspondence/through-vs-blind vs. Stage 1.5
words) are the same category of check — build vs. `overview_analysis.json` — split
across two files with no clear boundary between them.

**Fix direction:** Merge into one `overview_validate.py` with named sub-checks
(`check_missing_features`, `check_hole_counts`, `check_correspondences`,
`check_through_vs_blind`), one report file, one advisory/strict toggle.

> **Landed as:** `pipeline/overview_validate.py` with exactly those sub-check names
> (plus `check_conflict_carryover` / `check_symmetry_advisory`). The two REPORT
> ARTIFACTS were kept as-is rather than merged into one file — they are consumed by
> the webapp and the build plan, and changing an output contract was not part of
> this item's value.

### 1.4 Two "re-read a confusing region at higher DPI" systems
Stage 1.2 tiled extraction (`utils/tiled_extraction.py`, escalation-triggered) and
Stage 2.3 region extraction (`region_extraction.py`, unconditional) both solve
"re-render part of the drawing at higher resolution and reconcile," differing mainly
in trigger policy (heuristic escalation vs. always-on).

**Fix direction:** Extract the shared stitching/merge/reconciliation logic into one
subsystem with a pluggable trigger policy (`always` vs. `on_confidence_heuristic`),
rather than maintaining two independently-evolving implementations of the same idea.

> **Landed as:** `pipeline/highres_pass.py`.

### 1.5 Three retry loops, each reinventing "bounded, escalating, cap 3"
Stage 10.5 reconciliation (re-runs resolver, cap 3), Stage 10.7 geometric correction
loop (rebuilds via COM, cap 3), Workstream 1 deferred retry (re-attempts failed
features, cap 3) — same shape (bounded cap, escalating strategy, terminate on
no-progress/oscillation), implemented three times independently.

**Fix direction:** A shared `retry_ladder(attempt_fn, classify_failure_fn, cap=3,
stop_on_oscillation=True)` utility used by all three call sites. This is real code
deduplication, not just doc cleanup — worth doing even if the three callers keep
distinct domain logic.

> **Landed as:** `pipeline/retry_ladder.py`. `classify_failure_fn` stayed with the
> deferred queue (the taxonomy is domain knowledge about COM failures, not
> termination policy); the ladder owns cap/progress/oscillation/exhaustion/error.

### 1.6 `dwg_native/` as a second, independently-evolving pipeline
The main pipeline already has Stage 2.4 (`dwg_crosscheck.py`, DWG-exact dimension
text corrects OCR). Separately, `dwg_native/` is a **complete parallel pipeline**
(import → extract → map → build → verify, 5 gates, its own COM builder, its own VBA
emitter) for DWG inputs specifically.

**Why this is the costliest redundancy in the system:** it's ambiguous which path a
DWG file actually takes, the two pipelines can silently diverge in behavior/quality,
and every future pipeline-wide fix (e.g. a new invariant in `macro_generator.py`) has
to be separately ported to `dwg_native/build/vba_emit.py` or it silently doesn't apply
there.

**Fix direction:** Decide explicitly — either (a) `dwg_native/` is a real, permanent
second product for geometry-first DWG handling and should be documented as such with
its own guiding principles doc, or (b) it should be absorbed as another extraction
front-end feeding the *existing* resolver → build_sequencer → macro_generator chain
(which already implements "never fabricate, always resolve, always flag" well). Do not
leave it in the current ambiguous middle state.

> **Decision: (a).** The two products disagree about what the source of truth IS
> (entities vs. drawing), so one of the two load-bearing guarantees would have to be
> weakened by a merge. `docs/DWG_PATHS.md` carries the rationale, the native path's
> own guiding principles, and the both-paths parity checklist; `pipeline/dwg_routing.py`
> makes the routing rule executable so the ambiguity is gone from the CODE, not just
> from the prose.

### 1.7 C# macro output as a mandatory parallel code-generation surface
`csharp_macro.py` mirrors every VBA feature via the same BuildStep data — but it's
generated on **every run**, purely as an advisory companion nobody builds from,
requiring it be kept in lockstep with every future `macro_generator.py` change.

**Fix direction:** Make it opt-in (`--emit-csharp`) rather than default. This removes
an entire class of "did the C# emitter also get updated" maintenance risk without
losing the capability for whoever actually wants it.

> **Landed as:** `--emit-csharp` / `MTI_EMIT_CSHARP=1`, default OFF.

### 1.8 Two hand-maintained mega-docs describing the same 13 stages
`CLAUDE.md` and `EXTREME_README.md` describe the same pipeline stages in overlapping
detail, maintained by hand in parallel — meaning they will drift, and already show
minor phrasing/version differences.

**Fix direction:** Pick one as the canonical, hand-maintained source (likely
`EXTREME_README.md`, since it's the fuller reference) and either generate `CLAUDE.md`
as a short derived excerpt, or make `CLAUDE.md` explicitly scoped to *only* commands/
environment specifics that an agent needs turn-to-turn, removing the duplicated stage
narrative entirely.

> **Landed as:** the second option. `CLAUDE.md` now carries commands, environment,
> invariants, a one-line-per-stage INDEX table, the naming contracts, and a
> where-to-look table. `EXTREME_README.md` is declared canonical in its own header.

---

## 2. CRITICAL IMPROVEMENTS (beyond redundancy)

### 2.1 Resolve HoleWizard5's fate
Known broken on the target machine (`SW2024 returned None` on a clean part), shipped
default-off behind `MTI_ENABLE_HOLE_WIZARD=1`, but the full 27-arg-signature code path,
fallback logic, and constants (`hole_wizard_constants.py`) are maintained regardless.
Either invest in live-verifying the Value-slot mapping and promote it, or remove the
dead path until there's a concrete plan to fix it.

> **Landed as: quarantine.** Live verification needs a running SolidWorks session
> this refactor could not perform, so the path moved to `pipeline/experimental/`
> with its status, exact blocker, and a four-step revival plan — plus an explicit
> "if step 1 is never scheduled, delete it" decision. `reconcile_callout_count` was
> NOT quarantined: it is live resolver logic that happened to live in that file, and
> now lives in `pipeline/callout_qty.py`.

### 2.2 Decide the OpenAI provider's real status
If `AI_PROVIDER=openai` isn't exercised against production drawings, the adapter
layer + `gpt-5.6` pricing table is ongoing tax for an unused path (extra abstraction
in `ai_provider.py`, extra surface in every prompt-construction call site). Either
give it real test/production coverage or strip it back until there's demand — a
`test_ai_provider.py` with no live traffic behind it is a false sense of coverage.

> **Landed as:** real coverage of what the abstraction claims (the four call sites
> are now driven through the adapter end-to-end in tests), plus an explicit
> `provider_status()` with a once-per-process runtime warning, plus
> `docs/PROVIDER_STATUS.md` stating the honest boundary — plumbing proven, output
> quality unmeasured — with the promote-or-remove steps for both directions.

### 2.3 Build the feature-state-machine abstraction (see 1.2) as a first-class module
This is the single change that would most reduce *accidental* complexity across
Stages 6.5–10.8, since nearly every one of those stages currently reads/writes
disposition state in a slightly different shape and file.

### 2.4 Resolve the DWG path ambiguity (see 1.6) before adding any more DWG features
Any new DWG-specific capability added under the current dual-pipeline structure adds
to the maintenance burden of *both* paths. This should be resolved before, not after,
further DWG investment.

### 2.5 Single-source the two mega-docs (see 1.8) before they diverge further
Low effort, meaningfully reduces the risk of a future agent (or human) trusting a
stale doc mid-task.

### 2.6 Add explicit test coverage for the coordinate-authority precedence order
Given five modules touch position resolution (1.1), the precedence between them
(solver-authoritative since 2026-07-21, vector-owns-position-vision-owns-semantics,
specs-first, positional-dims-before-escalation) is exactly the kind of ordering that
silently breaks when one module is refactored. A single integration test asserting
the full precedence chain end-to-end (spec override > vector position > solver-derived
> resolver ladder) would catch regressions the current per-module unit tests can't.

### 2.7 Formalize the retry-ladder cap/oscillation contract (see 1.5) with one test suite
Once the three retry loops share a utility (1.5), one shared test suite
(`test_retry_ladder.py`) covering cap enforcement, escalating-strategy ordering, and
oscillation detection replaces three separately-verified but subtly different
implementations of the same guarantee.

---

## 3. NON-GOALS / THINGS THAT LOOK REDUNDANT BUT AREN'T

Worth flagging explicitly so a refactor doesn't accidentally remove genuinely
load-bearing separation:

- **Stage 2.5 resolver vs. Stage 2.6 must_meet** are *not* redundant — Stage 2.6 is
  strictly tier-0 operator constraints; Stage 2.5 is the general ambiguity-resolution
  ladder that *reads* tier-0 as its highest-priority input. Keep separate.
- **Stage 10 constraint_verify vs. Stage 10.6 feature_verify** are *not* redundant —
  one grades against operator MM constraints only, the other measures every planned
  feature regardless of whether it's covered by a constraint. Keep separate.
- **VBA path vs. COM path** are not redundant — VBA is the canonical, auditable,
  any-OS build artifact; COM is a Windows-only direct-drive convenience that reuses
  the same build_plan. Keep separate, but make sure any future feature-type support
  (sweep/loft/rib/draft) lands in both, not just one.
- **`macro_echo.py` vs. `macro_audit.py`** are not redundant — audit is a static
  banned-API check before writing; echo is a round-trip literal-value check after
  writing. Different failure classes, keep both.
