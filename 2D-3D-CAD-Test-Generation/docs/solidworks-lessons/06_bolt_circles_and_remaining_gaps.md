# Bolt circles two ways, and the gaps that stayed shut

Iteration 9, `experiments/solidworks_practice/t5_close_the_gaps.py`, on
**SolidWorks 2026 rev 34.3.2**. Three of eight steps passed — and two of the
failures are the useful part.

## Bolt circle, two ways (the Tier-3 comparison)

Four ⌀0.3125 holes on a ⌀4.0 bolt circle, centre (3.0, 3.0).

### Way 1 — four circles in ONE sketch, one cut ✅

```python
lab.open_sketch(doc, "Front Plane")
for x, y in bolt_circle_points:            # computed in Python
    doc.SketchManager.CreateCircleByRadius(x*IN, y*IN, 0, dia/2*IN)
doc.SketchManager.AddToDB = False
doc.SketchManager.InsertSketch(True)       # close -> selected
feat = _try(True) or _try(False)           # one FeatureCut4
```

Measured: **4 cylindrical faces at exactly (1.0, 3.0), (3.0, 1.0), (3.0, 5.0),
(5.0, 3.0)** — the computed bolt-circle points, to 3 dp. **One feature. No
prerequisites.**

### Way 2 — seed hole + circular pattern ⚠️

Needs four features (bore → seed → axis → pattern) and, critically, **a
concentric cylindrical face to derive the axis from**. It was proved working in
iteration 7 (faces 3 → 6, +3 instances). In *this* run it reported +0 — because
the seed sat on a position Way 1 had already drilled, so the pattern's instances
landed on existing holes. **That is a flaw in this test, not in SolidWorks**; the
iteration-7 measurement is the valid one for "does it work".

### Which to prefer

| | Way 1 (one sketch) | Way 2 (circular pattern) |
|---|---|---|
| Features | 1 | 4 |
| Prerequisites | none | a concentric cylindrical face for the axis |
| Failure mode | none observed | silent `None` if the axis or a Mark is wrong (E017) |
| Positions | computed in Python, verified exact | derived by SolidWorks |

**Way 1 is the more reliable construction**, which independently confirms the
choice this pipeline already makes (`_circular_cut_at` sketches every hole at its
resolved centre rather than patterning). Doc 06 suggests exactly this as the
"agent-friendly alternative" — hands-on testing agrees: you lose the *pattern*
semantics in the feature tree and gain a construction with no prerequisites and
no silent-failure mode.

## Gaps that stayed shut

* **Shell is genuinely absent.** E021 suggested trying the raw dispid Invoke
  before declaring an API unavailable — so I tested my own advice, and it
  fails: `GetIDsOfNames` finds **no dispid** for `InsertFeatureShell`,
  `InsertFeatureShell2`, `InsertShell`, `FeatureShell` or
  `InsertFeatureShellByFace`. The method is not on this `IFeatureManager` in any
  form. E021 updated with the disproof.
* **Linear pattern: the API exists, the call refuses.** `GetIDsOfNames` finds
  dispids for `FeatureLinearPattern` through `FeatureLinearPattern5`, so this is
  NOT a missing method — yet `FeatureLinearPattern4` with an edge at Mark 1 and
  the seed at Mark 4 returns `None`, raises nothing, and creates no geometry.
  Narrows E017 but does not close it. Circular patterns work under the same
  discipline; use those, or Way 1 above.
* ~~**Single-edge fillet and chamfer did not change the volume.**~~
  **RESOLVED, and it was this script's fault** (iteration 12). Line 301 selects
  the edge, line 306 measures, line 309 calls — and the measurement forces a
  rebuild that clears the selection, so the fillet ran against nothing. Re-run
  with the measurement first, a single-edge fillet builds normally
  (5.45933 → 5.45514). The guess offered here — "a hole's circular edge cannot
  take the radius" — was wrong; all-edges fillets work *including* over hole
  edges. See E022 and lesson 07.

## Confidence

**HIGH** for Way 1 (measured exact, no prerequisites) and for shell being
absent (five name variants, no dispid). **MEDIUM** for the two-ways comparison
(Way 2's numbers come from iteration 7, not this run). **Unresolved**: linear
patterns only — the fillet gap listed above was closed in iteration 14 and was
an artifact of this script.
