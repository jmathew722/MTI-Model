# Tier 5 — deliberate failures, and what they actually look like

All five run on **SolidWorks 2026 rev 34.3.2** via
`experiments/solidworks_practice/t3b_axis_and_tier5.py`. **5/5 confirmed.**
The point of this tier: know the SYMPTOM, because four of the five give you no
error to catch.

| # | Deliberate mistake | Actual symptom |
|---|---|---|
| 5.1 | mm passed as metres (no conversion) | Builds fine. A 50 × 25 **mm** rectangle became a **1971 × 985 in** part (50 m). No error. Only a bbox check finds it. |
| 5.2 | extrude with the sketch left **open** | **Returned a valid feature, no exception.** Contradicts doc 08, which says the extrude returns null. SolidWorks consumes the still-open active sketch. |
| 5.3 | fillet R5.0 on a 0.5-thick plate | Returned **`None`**, no exception. 6 edges were selected; nothing was built. |
| 5.4 | boss not touching the base | Built, and **body_count went 1 → 2**. This one IS detectable — the body-count assertion catches it exactly as doc 10 predicts. |
| 5.5 | `SelectByID2` on a name that does not exist | Returned **`False`** (a real bool), never raises. The repo's convention of checking the bool is correct and necessary. |

## Why 5.2 matters more than it looks

Doc 08 lists "sketch still open" as a *cause* of `FeatureExtrusion3` returning
null. On this install the opposite is true: the boss builds. That explains why
the VBA recorder pattern (leave the sketch open, call the feature) works — and
it is why the COM **cut** path still needs the sketch CLOSED (see
`02_cut_and_hole_placement.md`): boss and cut are not symmetric here.

## Cross-references

Recorded in the ledger as E018 (`callable()` discriminator), E019 (open-sketch
extrude succeeds). 5.1, 5.3, 5.4 and 5.5 confirm existing guidance rather than
adding new failure classes, and are cited from
[`../solidworks-macro-error-log.md`](../solidworks-macro-error-log.md).

## Confidence
**HIGH** — each case run to completion with the result measured off the model.
