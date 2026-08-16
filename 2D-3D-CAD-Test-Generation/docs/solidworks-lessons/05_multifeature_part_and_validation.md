# Tier 4 — a realistic multi-feature part, and validating it

Built on **SolidWorks 2026 rev 34.3.2** via
`experiments/solidworks_practice/t4_bracket_and_validation.py`, in the order a
real `plan.json` specifies: **base → boss → profile cut → holes → fillets last**.
Saved as `parts/t4_bracket.sldprt`. **10/13 steps verified.**

## The part, measured at each stage

| Step | Measured off the model |
|---|---|
| base plate 5.0 × 3.0 × 0.375 | bbox `[5.0, 3.0, 0.375]` |
| **reference plane** offset 0.375 from Front | created, named `plane_top` |
| boss 1.5 × 1.5 × 0.25 **sketched on that reference plane** | bbox `[5.0, 3.0, 0.625]`, vol 6.1875, **1 body** (merged) |
| right-edge notch 0.5 × 1.0 | vol 6.00375 |
| four ⌀0.375 mounting holes | 4 cylindrical faces at exactly (0.625, 0.625), (4.375, 0.625), (0.625, 2.375), (4.375, 2.375) |

**Reference planes work and behave identically to standard planes** (the Tier-3
item): `FeatureManager.InsertRefPlane(swRefPlaneReferenceConstraint_Distance,
distance_m, 0, 0, 0, 0)` with the Front Plane selected, then sketch on it by the
name you gave it. The boss merged into one body — no second-body surprise.

## The validation layer, run against a real part

`pipeline.validation.build_scorecard` graded this part for the first time — the
module was written against pipeline artifacts, so this is its first contact with
geometry that came out of SolidWorks:

```
verdict = PASS
  solid_body    PASS — one watertight solid body
  volume_ratio  PASS — solid fills 62.3% of its bounding box
  com_symmetry  SKIPPED — the drawing declares no symmetry to check against
```

62.3% is a plausible fingerprint for a plate with a boss, a notch and four
holes, which is exactly what doc 07 says the ratio is for.

## The audit catches a deliberately wrong part

Tier 4's explicit requirement. A fifth hole was drilled at **twice** the
specified diameter and the cylindrical-face audit was re-run:

```
expected all ⌀0.375; found off-spec: [(2.5, 1.5, 0.75)]
```

**Caught** — position and diameter both reported, from the model, with no
reliance on what the script intended.

## Gotchas found empirically

* **Selecting ALL edges makes `FeatureFillet3` a silent no-op.** 44 edges were
  selected on the finished bracket; the call raised nothing and the volume was
  unchanged (5.83808 → 5.83808). Doc 10's advice to fillet edge-by-edge and skip
  the failures is not a style preference — an all-edges fillet on a part with
  any incompatible edge produces *nothing at all*, not a partial result.
  Recorded as E020.
* **`InsertFeatureShell` is not exposed** on `IFeatureManager` here —
  `hasattr` finds neither `InsertFeatureShell` nor `InsertFeatureShell2`, and
  calling it raises `AttributeError`. Shell could not be tested. Recorded as
  E021.
* **Linear patterns remain a silent no-op** even with a direction edge at Mark 1
  and the seed at Mark 4 (faces 5 → 5, no exception) — while *circular* patterns
  build correctly with the same discipline. The asymmetry is unexplained; the
  circular path is the one to trust.

## What the reference docs got right / missed

* Doc 06 right: reference planes are a deterministic alternative to face
  hunting, and sketching on one is identical to a standard plane. Confirmed.
* Doc 05 right: fillets belong last — and doc 10's edge-by-edge advice is
  stronger than it sounds (see E020).
* Doc 07's volume-ratio fingerprint is useful and cheap; 62.3% for this part.

## Confidence

**HIGH** for the build order, reference plane, hole placement and the audit
catching the wrong diameter (all measured). **Not established**: shell (API
absent) and linear patterns (silent no-op).
