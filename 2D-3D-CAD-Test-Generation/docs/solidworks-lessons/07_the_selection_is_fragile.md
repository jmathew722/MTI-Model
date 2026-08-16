# The selection is fragile — and it faked two of my findings

Iterations 10–15, on **SolidWorks 2026 rev 34.3.2**. This lesson exists because
two entries in this manual were **wrong**, and the way they were produced is
more useful than either of them was.

## The contradiction that started it

Iteration 10 ran the identical all-edges fillet that Tier 1 had already built:
same helper, same `FeatureFillet3(195, r, ...)`, same 12 edges, same plate.

```
Tier 1   -> BUILT, volume 6.0 -> 5.93696
Tier 6   -> None,  volume 6.0 -> 6.0
```

Same call, different answer. One of the two runs had to be lying about what it
was doing.

## Ruling out the obvious explanation first

The visible difference was that Tier 6 had made a *failed* fillet call (an
impossible 5.0 in radius) earlier in the same document. Hypothesis: a failed
feature call leaves the document unable to accept the next one — which would
matter enormously, because the deferred-retry ladder retries in the same
document, and every retry after a first failure would be doomed for reasons
unrelated to the retry strategy.

`t7_failure_poisoning.py`, three parts, one variable:

| | result |
|---|---|
| A clean part → legal fillet (control) | BUILT 6.0 → 5.98407 |
| B failed fillet → legal fillet | **BUILT** 6.0 → 5.98407 |
| C failed fillet → recovery → legal fillet | BUILT 6.0 → 5.98407 |

**No poisoning.** A failed call does not block the next one. Good news for the
retry ladder, and the hypothesis was dead.

## The actual cause

One difference remained: Tier 6 measured the volume *between* selecting the
edges and calling the fillet. `t8_selection_clobber.py` measured the selection
count directly at each step:

| after this operation | selection |
|---|---|
| 12 edges selected | 12 |
| enumerate bodies | 12 |
| read `doc.Extension` | 12 |
| `FeatureManager.GetFeatureCount` | 12 |
| **`lab.measure()`** | **0** ← |

and the end-to-end pair, on identical parts:

```
select -> fillet                       BUILT   6.0 -> 5.98407
select -> measure -> fillet            None    6.0 -> 6.0
```

`swlab.Lab.measure` ends with `check_rebuild_errors`, which calls
`ForceRebuild3`. **A rebuild clears the selection.** The feature call then runs
against nothing and returns `None`, without raising.

It also **disconnects topology pointers you already hold**: edges collected
before a rebuild raise `com_error: The object invoked has disconnected from its
clients` when you select them afterwards. That one surfaced by crashing a test,
which is the friendlier of the two failure modes.

Neither the document-level `IModelDoc2.GetMassProperties` nor the body-level
`IBody2.GetMassProperties` clears the selection on its own — it is specifically
the rebuild. **The pipeline is unaffected**: every `check_rebuild_errors` call
site in `solidworks_builder.py` runs after a feature completes, never between a
selection and a call.

## What that cost me: two retractions

**E020 — "an all-edges fillet is a silent no-op" — retracted.** The Tier-4 test
selected at line 155, measured at 159, called at 162. Re-run with the
measurement moved before the selection, on a plate with four through holes and
all 20 edges selected including the circular ones:

```
R.0625 all-edges fillet -> BUILT, 5.45933 -> 5.42309
```

**Iteration 9's "single-edge fillet changes nothing" — retracted.** Same cause,
same file pattern. It builds: 5.45933 → 5.45514.

## And one retraction of a retraction

Iteration 14 left a genuine-looking failure: a single-edge chamfer returned
`None` where a fillet on the same edge had built. The pipeline uses that exact
call, `InsertFeatureChamfer(4, 1, dist, angle, 0, 0, 0, 0)`, so iteration 15
swept the `(Options, ChamferType)` enums, found `Options=0` built, stopped, and
concluded the pipeline's `Options=4` was the bug.

The sweep had never tried `Options=4`. An A/B on one part, alternating:

```
Options=4 -> BUILT   5.625   -> 5.61523
Options=0 -> BUILT   5.625   -> 5.61523
Options=4 -> None    5.61523 -> 5.61523
Options=0 -> None    5.61523 -> 5.61523
```

Both values build; both fail on the later attempts, because the edge already
carries a chamfer. **The failures track attempt order, not the flag.** No
pipeline change was made — working code was two minutes from being "fixed" on
the strength of a sweep that stopped at its first success. That is E023, and
it is why rule 15 exists.

## Rules this produced

* **14** — collect topology, select, and call with nothing in between; do all
  measuring and logging first.
* **15** — when an experiment says working code is wrong, run the comparison the
  experiment skipped before changing anything.
* **11 rewritten** — all-edges fillets work; go edge-by-edge to report skips, not
  to dodge a defect.

## What still stands

**Linear patterns genuinely fail.** That test is clean of this bug — it never
rebuilds between selecting and calling — and with a verified 8.00 in straight
edge at Mark 1 and the seed at Mark 4, `FeatureLinearPattern4` returns `None`
and creates nothing, while circular patterns build under the same discipline.
**Shell is genuinely absent**, no dispid under any of five names.

## Confidence

**HIGH** — every claim here is a measured before/after pair on this install, and
the two retracted findings were re-tested rather than reasoned away. The
methodology point is the durable part: an agent driving SolidWorks gets `None`
for "you did it wrong" and `None` for "your harness broke the selection", and
cannot tell them apart without a controlled comparison.
