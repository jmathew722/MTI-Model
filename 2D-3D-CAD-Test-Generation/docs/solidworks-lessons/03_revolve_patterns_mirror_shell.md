# Revolve, patterns, mirror, shell (Tier 2)

Run on **SolidWorks 2026 rev 34.3.2** in the SAME part
(`practice_master.sldprt`) via `experiments/solidworks_practice/t2_revolve_pattern_mirror_shell.py`,
using add → measure → **delete** so one file hosts every experiment.

Result: **5 of 10 verified.** The non-verifications are recorded here as
findings, not skipped — three of them are silent failures.

## Verified working calls

```python
# REVOLVE — one centerline, closed half-profile on one side of it
sm.CreateCenterLine(8*IN, 0, 0, 8*IN, 3*IN, 0)
# ... four CreateLine calls forming the closed profile, all x >= 8*IN ...
sm.AddToDB = False
sm.InsertSketch(True)                       # close -> profile selected
feat = doc.FeatureManager.FeatureRevolve2(
    True, True, False, False, False, False, EC_BLIND, 0,
    math.radians(360), 0.0, False, False, 0, 0, 0, 0, 0, True, True, True)
```

| Case | Measured |
|---|---|
| Revolve 360° | **+4.712 in³**, feature created |
| Revolve 270° | **+3.534 in³** — exactly 0.75 × the 360° volume, so the angle scales linearly as expected |
| Mirror about a plane | feature created (see the caveat below) |

## Gotchas found empirically

* **A revolve clear of the base solid creates a SECOND body** — body count went
  1 → 2. Nothing errors. This is doc 10's "boss creates a second body" and the
  body-count check catches it; it is why that check belongs after *every*
  additive feature, not just at the end.
* **`InsertAxis2` is on `IModelDoc2`, not `IFeatureManager`**, and it needs a
  cylindrical FACE selected first. Calling `FeatureManager.InsertAxis2` raises
  `AttributeError`.
* **`Select4`'s second argument is VT_DISPATCH too** — `face.Select4(False, None)`
  fails the call with `Type mismatch (-2147352571)`, exactly like `SelectByID2`
  (E013). Use `_null_dispatch()` everywhere an object argument appears.
* **Mirroring about the Right Plane moved the hole off the part.** The feature
  was created and reported success while the cylindrical-face count stayed 1 →
  the mirrored copy landed at x = −1.5 on a block spanning x = 0…6. A mirror
  about the wrong plane is silent: only a face/volume measurement reveals it.

## Failure modes tested (deliberate)

| Attempt | Actual behaviour |
|---|---|
| Revolve with the profile **crossing the axis** | returns `None`, **no exception** — matches doc 06's warning, but note it is silent, not an error |
| Revolve with **two centerlines** | **SUCCEEDED** — a feature was created. This CONTRADICTS doc 06, which says two centerlines make the axis ambiguous and the revolve fail. On this install SolidWorks picks one and builds. Recorded as E016. |

## Not verified — and why (honest gaps)

* **Linear and circular patterns did not build.** With the seed selected as
  `BODYFEATURE` Mark 4 and a direction/axis at Mark 1, both
  `FeatureLinearPattern4` and `FeatureCircularPattern5` (14-arg tlb signature,
  dispid 261) returned `None` with **no exception**, and the cylindrical-face
  count was unchanged. Because the baseline never built, the intended
  **correct-Mark vs wrong-Mark comparison could not be isolated** — both Mark 4
  and Mark 1 produced the identical silent `None`. The production path
  (`solidworks_builder.build_circular_pattern_holes`) does build these; the
  difference is that it derives the axis from the bore's cylindrical face and
  then re-finds it via `_last_feature_of_type("RefAxis")` rather than assuming
  the name `Axis1`. Reproducing that in the lab is the next iteration.
* **Shell did not run**: `FeatureManager.InsertFeatureShell` raised
  `AttributeError: <unknown>.InsertFeatureShell` in the lab harness, though
  production calls exactly that signature. Suspected doc/selection state after
  the failed pattern calls; unresolved.

## What the reference docs got right / missed

* Doc 06 right: one centerline, closed profile on one side, `FeatureRevolve2`
  argument shape.
* Doc 06 **wrong on this install**: two centerlines do not fail the revolve.
* Doc 06 right in spirit about Marks being "suspect #1" — but the observed
  symptom is a silent `None`, not a wrong count, so a caller that only checks
  "did it throw" will believe the pattern worked.

## Confidence

**HIGH** for revolve 360/270 and the second-body finding (measured, repeated).
**MEDIUM** for the mirror caveat (one observation).
**Not established** for patterns and shell — see the gaps above.
