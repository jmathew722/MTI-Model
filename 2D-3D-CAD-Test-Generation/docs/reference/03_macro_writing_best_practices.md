# 03 — Macro / Automation Code Best Practices

These rules exist because AI-generated CAD code fails in characteristic ways. Follow them mechanically.

## Structure every build program the same way
```
1. Parse plan JSON (the feature plan from doc 05)
2. Connect/attach to SolidWorks
3. New part from template
4. For each plan step: execute → verify → name → log
5. Rebuild + validate (doc 07)
6. Save part (.SLDPRT) + write run report (assumptions, warnings, measurements)
```
The build program is an **interpreter for the plan**, not a hardcoded script. This lets you fix the plan without regenerating code, and fix code without touching the plan.

## Naming discipline
Immediately after creating any feature or sketch, rename it:
```csharp
feat.Name = "base_plate_extrude";     // stable, semantic
sketchFeat.Name = "sk_base_profile";
```
Why: default names ("Sketch1", "Boss-Extrude1") shift when steps are added/removed, and differ across localizations. All later selections must reference YOUR names. Never select "Sketch1".

## Meaningful code style (matters even for machine-written code)
- Descriptive variable names (`largestPlanarFace`, not `face1`) — official SolidWorks guidance, and it makes the agent's own later edits far less error-prone.
- One function per plan-step type: `BuildExtrude(step)`, `BuildRevolve(step)`, `BuildHolePattern(step)`.
- Keep a `Context` object carrying `swApp`, `swModel`, `ext`, unit converter, logger, and a `Dictionary<string, IFeature>` of created features by semantic name.

## Anti-patterns (all seen in recorded macros — NEVER ship these)
| Anti-pattern | Why it breaks | Do instead |
|---|---|---|
| `SendKeys`, mouse coordinates | Depends on window focus/layout; nondeterministic | Real API calls |
| `On Error Resume Next` (VBA) / empty `catch {}` | Hides failures; downstream steps corrupt the model | Check returns, throw with context |
| Hardcoded `SelectByID2("Boss-Extrude1", ...)` from a recording | Name is unstable | Rename features; select your names |
| Copying recorded `View Zoom`/`ShowNamedView` noise | Irrelevant UI chatter; slows runs | Delete all view-manipulation calls |
| Magic numbers for enums | Unreadable; wrong values pass compile | `swconst` enums |
| Selecting by XYZ coordinates on faces | Fragile as geometry changes | Select by feature name or use `IFeature.GetFaces()` and pick by geometric criteria (normal, area) |
| Absolute file paths from the dev machine | Breaks on target | CLI args / config file |
| Continuing after a failed step | Every later feature builds on garbage | Fail fast, report step number |

## Selecting faces/edges robustly (no UI picking available to the agent)
The agent cannot "click" a face. Choose faces programmatically:
```csharp
// e.g., find top-most planar face of a feature for the next sketch
object[] faces = (object[])feature.GetFaces();
IFace2 best = faces.Cast<IFace2>()
    .Where(f => IsPlanar(f))
    .OrderByDescending(f => FaceCentroidZ(f))
    .First();
IEntity ent = (IEntity)best;
ent.Select4(false, null);
```
Criteria to select by: surface type (planar/cylindrical), normal direction, area, centroid position. Write these helpers once.

Alternative that avoids face-hunting entirely: **create reference planes at known offsets from the standard planes** and sketch on those. Offset planes are deterministic; face selection is not. Prefer reference geometry whenever possible.

## Determinism rules
- Fully define sketches with dimensions/relations where practical, or at minimum construct them from exact coordinates (with `AddToDB = true` so nothing snaps).
- Never rely on document default units, grid settings, or user preferences — set what you need explicitly at run start, restore at end.
- Set end conditions explicitly (`swEndCondBlind`, `swEndCondThroughAll`, `swEndCondMidPlane`) — do not rely on defaults.

## Logging (mandatory)
Every step logs one line: step id, action, inputs (already converted values + original drawing values), result, and any guess made.
```
[07] hole_pattern_mounting: 4x Ø5.5mm thru, bolt circle Ø80mm | OK | GUESS: pattern centered on part origin (drawing datum ambiguous)
```
This log is the raw material for the end-of-run assumption report (doc 09).

## Save & export
```csharp
int errs = 0, warns = 0;
bool saved = swModel.Extension.SaveAs3(outPath, 
    (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
    (int)swSaveAsOptions_e.swSaveAsOptions_Silent, null, null, ref errs, ref warns);
```
Check `saved`, `errs`, `warns`. Export STEP as well (`SaveAs3` with `.step` extension) so results can be inspected outside SolidWorks.

## Using macro recording the right way
When the agent doesn't know which API call a UI operation maps to:
1. Ask the user (or a scripted helper) to record the single operation once in the UI, OR consult API Help examples.
2. Read the recorded VBA only to identify the **method name and argument pattern**.
3. Reimplement in C# with named enums, checked returns, and your unit helpers. Discard everything else from the recording.
