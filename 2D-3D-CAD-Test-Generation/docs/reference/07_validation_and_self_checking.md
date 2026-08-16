# 07 — Validation & Self-Checking (The Agent Grades Its Own Work)

The pipeline is only trustworthy if the model is verified against the drawing automatically. Validation runs after the build, produces a machine-readable scorecard, and drives the repair loop (doc 08).

## Layer 1: Rebuild health
```csharp
swModel.ForceRebuild3(false);
foreach (IFeature f in WalkFeatures(swModel))
{
    object errs = f.GetErrorCode2(...); // check per-feature error/warning codes
}
```
Any feature error (dangling sketch, failed fillet, zero-thickness geometry) = build FAILED regardless of how the part looks. Record feature name + error code.

Also verify exactly ONE solid body exists (unless the plan says otherwise):
```csharp
object[] bodies = (object[])((IPartDoc)swModel).GetBodies2((int)swBodyType_e.swSolidBody, true);
Assert(bodies != null && bodies.Length == 1, "expected 1 solid body, got " + n);
```
Multiple disconnected bodies almost always means a boss didn't merge or a cut severed the part — a plan/geometry error.

## Layer 2: Bounding box vs overall dimensions
```csharp
double[] box = (double[])((IPartDoc)swModel).GetPartBox(true); // [x1,y1,z1,x2,y2,z2] meters
```
Compare (x2-x1, y2-y1, z2-z1) against `plan.expected.bbox`. Tolerance for the check: the drawing's general tolerance, or 0.01 mm for exact-construction sanity. Note: fillets/chamfers on outer corners do NOT change the bbox; a bbox mismatch means a core dimension is wrong (frequently a missed unit conversion — off by 25.4× or 1000×, both instantly recognizable).

## Layer 3: Mass properties sanity
```csharp
IMassProperty mp = (IMassProperty)ext.CreateMassProperty();
double vol = mp.Volume;              // m^3
double[] com = (double[])mp.CenterOfMass;
```
- Volume must be positive and less than bbox volume. Ratio (vol / bboxVol) is a great fingerprint: a plate with 4 small holes ≈ 0.97; a shelled housing ≈ 0.15–0.4. Wildly-off ratios reveal missing cuts or extra material.
- Symmetric parts: center of mass must lie on the symmetry plane(s) within tolerance. A CoM off the centerline of a "symmetric" part = a feature was placed one-sided.
- If material was set, mass can be compared to a title-block weight if present.

## Layer 4: Dimension spot-checks with the Measure API
For each key drawing dimension, verify the model reproduces it:
```csharp
IMeasure measure = ext.CreateMeasure();
// select two faces/edges, then:
bool ok = measure.Calculate(null);
double d = measure.Distance;         // or .Diameter, .Angle
```
Practical approach when face selection is hard: re-derive check values from geometry directly — e.g., find all cylindrical faces via `IFace2.IGetSurface().CylinderParams` and confirm each expected hole diameter and axis position exists:
```csharp
// enumerate faces; for cylinders, CylinderParams = [origin xyz, axis xyz, radius]
```
This gives an automated **hole audit**: count of cylindrical faces at Ø5.5 within tolerance must equal 4, at the planned XY positions. Same trick verifies fillet radii.

## Layer 5: Silhouette check (optional but powerful)
Export front/top/right view images (`SaveBMP` per orientation or export STEP and render externally) and compare silhouettes against the drawing views — even a rough pixel-overlap comparison after scaling catches gross topology errors (missing boss, mirrored part from a projection-angle mistake).

## Layer 6: The scorecard
Emit `validation.json`:
```json
{
  "rebuild_errors": [],
  "body_count": 1,
  "bbox": {"expected":[120,60,25], "actual":[120.0,60.0,25.0], "pass": true},
  "volume_ratio": 0.968,
  "holes": {"expected": 4, "found": 4, "dia_ok": true, "positions_ok": true},
  "dimension_checks": [{"dim":"80 BC","actual":80.001,"pass":true}],
  "com_symmetry": {"plane":"Right","offset_mm":0.0003,"pass":true},
  "overall": "PASS"
}
```
Rules:
- ANY layer-1 failure → overall FAIL → repair loop.
- Dimensional mismatch beyond tolerance → FAIL with the specific step id whose evidence used that dimension (this is why plan steps record their evidence — it makes failures traceable to a plan step).
- All pass but confidence-low assumptions exist → overall "PASS_WITH_ASSUMPTIONS" → these are surfaced to the user at the end (doc 09), not fixed silently.

## Golden rule
**Never report success to the user without a passing scorecard.** A part that "looks done" but was never measured is a failed run.
