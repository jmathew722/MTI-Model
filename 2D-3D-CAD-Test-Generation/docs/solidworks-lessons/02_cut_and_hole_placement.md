# Extrude cuts, hole placement, and edge sides

Tested on **SolidWorks 2026 rev 34.3.2** in ONE part
(`experiments/solidworks_practice/parts/practice_master.sldprt`, built by
`master_session.py`): a 6.0 × 4.0 × 0.5 in block, corner at the origin, Front
Plane, then 5 holes + 4 edge notches added and measured.

## Verified working call

```python
# 1 open sketch   2 draw (metres)   3 CLOSE it   4 cut with a flip retry
doc.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0,
                          _null_dispatch(), 0)
doc.SketchManager.InsertSketch(True)
doc.SketchManager.AddToDB = True
doc.SketchManager.CreateCircleByRadius(x_m, y_m, 0, r_m)
doc.SketchManager.AddToDB = False
doc.SketchManager.InsertSketch(True)          # closing SELECTS the profile

def _try(flip):
    return doc.FeatureManager.FeatureCut4(
        True, False, flip, 1, 0, 0.0, 0.01,   # 1 = swEndCondThroughAll
        False, False, False, False, 0, 0, False, False, False, False, False,
        True, True, True, True, False, 0, 0, False, False)
feat = _try(True) or _try(False)
feat.Name = "hole_LL"
```

## Measured placement results (10/12 verified)

Holes — sketch (x, y) lands at model (x, y) **exactly**, 5/5 to 3 dp:
(0.75,0.75) (5.25,0.75) (0.75,3.25) (5.25,3.25) all ⌀.375, and (3.0,2.0) ⌀.75.

Edge notches — corner-origin frame holds on all four sides, 4/4, each removing
0.245 in³ against an intended 0.25 (difference = the deliberate 0.01 overshoot):

| Edge | Sketch corner | Formula |
|---|---|---|
| LEFT | (−0.01, 1.5) | `x0 = 0` |
| RIGHT | (5.51, 1.5) | `x0 = W − depth` |
| BOTTOM | (2.5, −0.01) | `y0 = 0` |
| TOP | (2.5, 3.51) | `y0 = H − depth` |

**Y is UP.** A top notch computed as `y = depth` lands on the BOTTOM edge and
builds perfectly cleanly — the 158-C class. Only measuring which side lost
material catches it.

**Non-Front planes do not share the frame.** The same (1.25, 0.5) sketched on the
Top and Right planes produced no new cylindrical face and removed no material.
Resolve the sketch-plane → model-axis mapping per plane; never reuse Front-Plane
coordinates elsewhere.

## What the reference docs got right / missed

* Doc 06's advice to sketch holes on a standard plane in absolute coordinates and
  cut through-all is **confirmed** — exact placement, no face hunting.
* Doc 06 shows a cut with the profile selected by name; on this install that
  selects nothing (E006). Close the sketch and let the selection stand.
* Doc 10's zero-thickness warning is **confirmed** — the overshoot is required.

## Failure modes tested
E014 (`ClearSelection2` between closing and cutting silently removes nothing),
plus E006 re-confirmed on the COM path.

## Confidence
**HIGH** for Front-Plane placement (10/12, twice). **MEDIUM** for non-Front
planes — established that the frame differs, not yet what the mapping is.
