# TEST3 remediation plan

Written 2026-08-17 after running 15 real drawings through the pipeline against
live SolidWorks. Every item below names the **evidence** that motivated it and
the **root cause** where research has already found one — so this is a work plan,
not a wish list.

Ordering principle: **fix the lying before fixing the building.** A wrong model
that reports itself wrong is recoverable; a wrong model that reports `PASS` is
not, because nobody looks at it. Tier A is therefore first even though Tier B is
what the user feels.

---

## Tier A — Reporting integrity (the UI currently shows PASS on broken parts)

### A1. `build_health` reports PASS while features are missing — **root cause found**

**Evidence.** 4088-A-RevA and 4092-B both show
`build_health: PASS — "N planned feature(s), none reported a build failure"`,
while their own `model_check.txt` lists a chamfer that returned `None` and was
skipped, and (4092-B) an `extrude_cut` that was deferred open.

**Root cause** (`pipeline/validation.py:171-189`). The layer only counts entries
in `macro_result.json` whose status is `fail`/`failed`/`error`. Features that end
`deferred_open` or `skipped` never appear there. It *does* compute
`excluded = [... state == "EXCLUDED_INCOMPLETE"]` — and then appends it to
`card.advisories` **without affecting the layer status**. So the one signal that
knows a feature is missing is deliberately routed away from the verdict.

**Fix.** Count `deferred_open`, `skipped_*` and `EXCLUDED_INCOMPLETE` toward the
layer. A part missing planned geometry must not read PASS — FAIL if any planned
solid feature is absent, WARN for cosmetic ones. Keep the advisory text.

**Verification.** Replay the five TEST3 scorecards; 4088-A, 4092-B, 4080-D and
4079-D must all stop saying PASS. Unit test with a disposition fixture.

### A2. `feature_audit` is SKIPPED on every part — **root cause found**

**Evidence.** All five built parts:
`feature_audit: SKIPPED — "no per-feature verification on disk"`.

**Root cause.** The layer loads `*_feature_verification.json`
(`validation.py:312`). `main.py` never calls `feature_verify` — grep across the
repo finds the module imported by `reconciliation.py`, `summary_view.py`,
`feature_ledger.py` and its own tests, **but not by the main pipeline**. Stage
10.6 is in the CLAUDE.md stage index and is not wired into the run.

So *nothing* checks that individual features landed at the right place and size.
The only geometric check that runs is the three-extent bounding box — which is
why a part can match its envelope and still be missing two holes.

**Fix.** Wire Stage 10.6 into `main.py` after the build, writing
`<Part>_feature_verification.json`. This is the single highest-value item in the
plan: it converts "the box is the right size" into "every feature is where the
drawing says", which is what the user actually means by *perfectly modelled*.

**Verification.** Re-run 4086-A; the audit must report on all 3 features and flag
the over-applied chamfer (C1) without being told about it.

### A3. `validation.json` has `verdict: None` on every part

**Evidence.** All five parts. Stage 11.5's headline — PASS /
PASS_WITH_ASSUMPTIONS / FAIL — is absent, so the scorecard has layers but no
overall answer for the UI to show.

**Fix.** Find why `build_scorecard` leaves the verdict unset on this path and
populate it. Depends on A1 (the verdict is only meaningful once the layers are).

---

## Tier B — Missing geometry

### B1. `FeatureCut4` returns `None` in BOTH directions — 4 features across 3 parts

**Evidence.** 4079-D F007 (hole), 4080-D F007 (extrude_cut) + F009 (hole),
4092-B F003 (extrude_cut). Error class `zero_thickness_or_geometry`. The builder
is explicit and, notably, is probably right about the diagnosis:

> the cut removes no material (coincident with a face or zero-area profile);
> check the profile geometry, not the build call

Both retry strategies failed (`nudge_material_safe_direction`,
`rerun_conservative_resolution`), so the retry ladder is working and the input is
genuinely bad.

**Research needed — do NOT guess.** The pattern to test in the lab
(`experiments/solidworks_practice/`):

1. reproduce a cut whose profile lies **entirely outside** the body → expect
   `None` both directions (the likely 4079-D/4080-D case: positions are Hough
   estimates on a faint scan, so a hole can land off the part);
2. reproduce a cut **exactly coincident** with a face → the classic
   zero-thickness case;
3. reproduce 4092-B specifically — it is a SPACER ring (10.50 OD, 9.002 ID,
   3.000 long) and F003 is plausibly the bore. A bore cut equal to the full
   length is exactly case 2.

**Likely fix, pending that evidence.** A pre-build containment check: assert the
profile overlaps the body in XY *and* that the cut depth is not exactly flush,
then report which condition failed. Overshoot already exists for open-edge cuts
(`slot_cut.EDGE_OVERSHOOT_EPS`) — the same idea generalises to through-cuts.

### B2. Chamfers fail on real parts — **OPEN, two hypotheses already disproved (E026)**

**Evidence.** 4079-D F005 (1.76 in, 49 edges), F006 (0.12, 49), 4088-A F003
(0.06, 26), 4092-B F006 (0.09, 28). All `None`, no exception.

**Already ruled out by measurement** — record these so they are not re-tried:

| Hypothesis | Test | Result |
|---|---|---|
| the all-edges scope | `t17` | all-edges chamfer **builds** on a clean plate |
| circular hole edges | `t18` | 0, 1 and 4 holes all **build** |

**Remaining hypotheses, in order of cheapness:**

1. **Prior edge treatment on the same body.** Iteration 14 saw a chamfer fail on
   an edge that had just been filleted, and `t11b` saw chamfer succeed on attempt
   1 and fail on later attempts. Every failing TEST3 part has other features
   before the chamfer. Test: plate → fillet → chamfer, vs plate → chamfer.
2. **Edge count / mixed edge kinds.** The failures sit at 26–49 edges; the lab
   successes at 12–20. Test: sweep edge count on progressively more featured
   parts until it breaks, then bisect *which* edge.
3. **Geometry already corrupted upstream.** 4079-D's chamfer runs on a plate
   extruded 15.25 in thick. Re-test after Tier C2 — this one may evaporate.

**Fix only after a hypothesis survives.** The builder already fails loudly and
defers, so the part ships with the failure visible; there is no pressure to guess.

---

## Tier C — Correctness of what does get built

### C1. Edge treatments are applied to every edge instead of the ones called out

**Evidence.** 4086-A — the drawing says `.06 × 45° TYP **(4)**`, the build
applied the chamfer to **14 edges**. 4080-D — **84 edges**. The builder already
knows it is guessing; it emits *"chamfer applied to N edges (all edges); verify
against the drawing (selective chamfers need the interactive macro)"*.

This is why **4086-A, the cleanest part in the batch, is still not correct.**

**Root cause.** `_select_fillet_edges` falls back to `_select_all_body_edges`
when it cannot isolate the intended edges, and `plan_fillet_scope` computes an
expected count that is used **only to warn**, never to constrain.

**Fix.** Make the callout quantity authoritative: when the callout says `TYP (4)`
and the selector cannot isolate exactly 4 candidates, do **not** silently apply
to all 14. Options, in preference order — (a) select the 4 best candidates by the
geometric rule already in `_cut_interior_vertical_edges`; (b) apply edge-by-edge
up to the callout count, reporting which edges took it; (c) exclude and flag.
Never (d) apply to everything, which is the current behaviour and is wrong in a
way the volume check cannot see.

**Note.** Extraction already parses `TYP (4)` — 4086-A's warnings quote it. The
count is available; it is simply not used.

### C2. A contradictory depth dimension only warns; the model is still wrong

**Evidence.** 4079-D built 15.25 in thick from a dimension labelled `length`.
Yesterday's fix names the contradiction but does not correct it, so the part is
still wrong.

**Research.** The true `.250` exists on the sheet — in the BOM SIZE column
(`.250 X 4.500 X 15.25`) and in the feature description text — but was never
linked to the feature. Recovering it means parsing the BOM triple, which the
extraction already reads well enough to quote in its warnings.

**Fix, carefully.** If a feature's description carries an `A × B × C` stock
triple and the nominated depth equals the **largest** of the three while a
smaller member is unused, prefer the smallest as thickness and **record the
substitution in the ledger**. This picks among extracted candidates and invents
nothing, which keeps the guiding principle. Gate it behind agreement with the
BOM, and leave the warning in place when it cannot be resolved.

---

## Tier D — Robustness and re-measurement

### D1. The batch stalled silently on SB10011

16 minutes of no output after that part's extraction, in the resolution phase,
with SolidWorks itself still responding (so not a modal dialog). Six parts were
never reached. **Unexplained.** Add a per-stage heartbeat and a per-part
watchdog so a hung part is abandoned with a report instead of stopping the batch;
then reproduce SB10011 alone to find the hang.

### D2. A040871E — tool input failed schema validation

The extractor requested a repair (`units`/`confidence` missing from the tool
call). Outcome unknown; no output folder was produced. Re-run alone and follow
the repair path.

### D3. Re-run the whole batch and re-measure

Only after A1, A2 and C1. The current fixes are **warnings only** and change no
geometry, so today's models are what a UI run produces. The plan is not done
until a re-run shows: SB10009 building at all, 4086-A chamfering 4 edges, and no
part reporting PASS while a feature is missing.

---

## Suggested order

| Step | Item | Why first |
|---|---|---|
| 1 | **A2** wire Stage 10.6 | nothing else can be judged without per-feature truth |
| 2 | **A1** build_health honesty | stops the UI reporting PASS on broken parts |
| 3 | **C1** callout-aware edge scope | fixes the best part in the batch; pure win |
| 4 | **A3** scorecard verdict | cheap once A1 lands |
| 5 | **B1** cut containment research | recovers 4 missing features |
| 6 | **C2** depth recovery from the BOM | fixes 4079-D properly |
| 7 | **B2** chamfer hypotheses | may partly evaporate after C2 |
| 8 | **D1/D2** stall + repair path | unblocks the 7 unmeasured parts |
| 9 | **D3** full re-run | the only proof any of this worked |

**Discipline for every item:** reproduce first, fix second, pin with a test
third, and re-measure on the real drawing fourth. Two findings in this project
were retracted for skipping step one (E020, E023).
