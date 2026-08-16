# 06 — Geometry Construction Playbook (C# API Recipes)

Vetted call patterns for each plan-step `op`. All lengths METERS, angles RADIANS. `sm = swModel.SketchManager`, `fm = swModel.FeatureManager`, `ext = swModel.Extension`. Verify exact signatures against the installed SolidWorks version's API help if a call fails — argument counts occasionally change between versions (e.g., `FeatureExtrusion2` vs `FeatureExtrusion3`).

## Sketch on a standard plane
```csharp
ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
sm.AddToDB = true; sm.DisplayWhenAdded = false;
sm.InsertSketch(true);

// entities (2D sketch coords, meters):
sm.CreateLine(x1, y1, 0, x2, y2, 0);
sm.CreateCircleByRadius(cx, cy, 0, MM(radius_mm));
sm.CreateCenterRectangle(0,0,0, MM(60), MM(30), 0); // center rect: corner at (w/2,h/2)
sm.CreateArc(cx, cy, 0, xs, ys, 0, xe, ye, 0, +1);  // +1 CCW / -1 CW
sm.CreateCenterLine(0, -0.1, 0, 0, 0.1, 0);          // construction line (revolve axis)

sm.AddToDB = false; sm.DisplayWhenAdded = true;
sm.InsertSketch(true);   // close sketch
IFeature sk = (IFeature)swModel.FeatureByPositionReverse(0);
sk.Name = "sk_base";
```
Rules: profiles for boss/cut must be **closed, non-self-intersecting loops**. Multiple disjoint closed loops in one sketch = multiple profiles (fine for extrudes). A loop inside a loop = hole in the profile.

## Extrude (boss)
```csharp
ext.SelectByID2("sk_base", "SKETCH", 0, 0, 0, false, 0, null, 0);
IFeature feat = (IFeature)fm.FeatureExtrusion3(
    true,  false, false,
    (int)swEndConditions_e.swEndCondBlind,      // or swEndCondMidPlane / swEndCondThroughAll
    (int)swEndConditions_e.swEndCondBlind,
    MM(25), 0.0,                                 // depth dir1, dir2
    false, false, false, false, 0, 0,
    false, false, false, false,
    true,                                        // merge result
    true, true,
    (int)swStartConditions_e.swStartSketchPlane, 0, false);
Require(feat, "base extrude"); feat.Name = "base_plate";
```
Mid-plane symmetric: end condition `swEndCondMidPlane`, depth = full thickness.

## Extrude (cut)
```csharp
ext.SelectByID2("sk_pocket", "SKETCH", 0, 0, 0, false, 0, null, 0);
IFeature cut = (IFeature)fm.FeatureCut4(
    true, false, false,
    (int)swEndConditions_e.swEndCondBlind, (int)swEndConditions_e.swEndCondBlind,
    MM(10), 0.0, false, false, false, false, 0, 0,
    false, false, false, false, false,
    true, true, true, true, false, 0, 0, false, false);
```
THRU holes/cuts: `swEndCondThroughAll`.

## Revolve
Sketch = closed half-profile entirely on ONE side of a centerline; include one `CreateCenterLine` as the axis.
```csharp
ext.SelectByID2("sk_profile", "SKETCH", 0, 0, 0, false, 0, null, 0);
IFeature rev = (IFeature)fm.FeatureRevolve2(
    true, true, false, false, false, false,
    (int)swEndConditions_e.swEndCondBlind, 0,
    2.0 * Math.PI, 0.0,          // 360°
    false, false, 0, 0,
    (int)swThinWallType_e.swThinWallOneDirection, 0, 0,
    true, true, true);
```
If the revolve fails: usually the profile touches/crosses the axis illegally or the sketch has 2 centerlines (ambiguous axis). Keep exactly one centerline.

## Holes — prefer the Hole Wizard for standard holes
Simple drilled hole, cbore, csk, tapped:
```csharp
// Select the face to place holes on (Mark = 1 for placement face)
faceEntity.Select4(false, selData);
IFeature hw = (IFeature)fm.HoleWizard5(
    (int)swWzdGeneralHoleTypes_e.swWzdHole,       // or swWzdCounterBore / swWzdCounterSink / swWzdTap
    (int)swWzdHoleStandards_e.swStandardAnsiMetric,
    (int)swWzdHoleStandardFastenerTypes_e.swStandardAnsiMetricDrillSizes,
    "Ø5.5", (int)swEndConditions_e.swEndCondThroughAll,
    ...); // long signature — check API help for the version's exact params
```
The Hole Wizard signature is long and version-sensitive. If it fights you, FALL BACK to: sketch circles at exact positions on the face plane → `FeatureCut4` THRU ALL. That fallback is fully reliable; log "modeled as plain cut, callout was M6 tap" style notes.

Positioning strategy: rather than sketching on a selected face (fragile), sketch the hole circles on the SAME standard plane as the base sketch (or an offset reference plane) and cut THROUGH ALL — positions are then exact model coordinates.

## Reference plane at offset (deterministic alternative to face selection)
```csharp
ext.SelectByID2("Top Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
IRefPlane rp = (IRefPlane)fm.InsertRefPlane(
    (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_Distance,
    MM(25), 0, 0, 0, 0);
((IFeature)rp).Name = "plane_top_of_plate";
```

## Fillet / chamfer (LAST features)
```csharp
// Select edges first (Mark = 1). Selecting ALL edges: iterate body edges and Select4 with append=true.
IFeature fil = (IFeature)fm.FeatureFillet3(
    195, MM(2), 0, 0, 0, 0, 0,
    ...); // simpler & more stable: fm.FeatureFillet with constant radius
```
Chamfer: `fm.InsertFeatureChamfer(4, 1, MM(0.5), DEG(45), 0, 0, 0, 0);` after selecting edges.
Practical tip: for "BREAK ALL EDGES" notes, either select all outer edges programmatically or SKIP cosmetic edge breaks and record the skip in the report — they rarely matter for downstream 3D use.

## Patterns
```csharp
// Circular pattern: select feature to pattern (Mark 4) + axis (Mark 1)
ext.SelectByID2("mount_hole", "BODYFEATURE", 0, 0, 0, false, 4, null, 0);
ext.SelectByID2("Axis1", "AXIS", 0, 0, 0, true, 1, null, 0);
IFeature cp = (IFeature)fm.FeatureCircularPattern4(
    4, 2.0*Math.PI/4, false, "NULL", false, true, false);

// Linear pattern
IFeature lp = (IFeature)fm.FeatureLinearPattern4(...); // direction edge Mark 1, feature Mark 4
```
Marks are the trap: pattern features require specific marks per selection role. If a pattern silently fails, wrong marks are suspect #1.
Simpler agent-friendly alternative: skip pattern features and just sketch ALL hole circles at computed positions in one sketch, one cut. Fewer API calls, fewer failure modes; you lose the "pattern" semantics but the geometry is identical.

## Mirror
```csharp
ext.SelectByID2("Right Plane", "PLANE", 0,0,0, false, 2, null, 0);   // mirror plane Mark 2
ext.SelectByID2("boss_left", "BODYFEATURE", 0,0,0, true, 1, null, 0); // features Mark 1
IFeature mir = (IFeature)fm.InsertMirrorFeature2(false, false, false, false,
    (int)swFeatureScope_e.swFeatureScope_AllBodies);
```

## Shell
```csharp
// select faces to remove (openings), then:
fm.InsertFeatureShell(MM(2), false);
```

## General recipe discipline
- After EVERY feature call: `Require(feat, stepName); feat.Name = plannedName; Rebuild-check at checkpoints.`
- If a call returns null: log the exact arguments, run `swModel.ForceRebuild3(false)`, check `IFeatureManager` state, and consult doc 08's retry ladder — do not blindly retry the identical call.
- When a signature is uncertain, the authoritative source is the local API help for the INSTALLED version; the agent should prefer the fallback constructions above (plain sketches + extrude/cut on standard/offset planes), which use the most stable, oldest API surface.
