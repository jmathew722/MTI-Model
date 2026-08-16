# 02 — SolidWorks API Fundamentals (Read Before Writing Any Code)

## The object model hierarchy
Everything hangs off the application object:

```
ISldWorks (the application)
 └── IModelDoc2 (a document: part, assembly, or drawing)
      ├── IPartDoc / IAssemblyDoc / IDrawingDoc (cast IModelDoc2 to these)
      ├── IFeatureManager   — creates features (extrudes, revolves, fillets...)
      ├── ISketchManager    — creates sketches and sketch entities
      ├── ISelectionMgr     — inspects what is currently selected
      ├── IModelDocExtension— SelectByID2, mass properties, measure, custom props
      └── IFeature → IFeature.GetNextFeature() — walk the feature tree
```

Get the extension once: `IModelDocExtension ext = swModel.Extension;`

## RULE #1: UNITS ARE METERS. ALWAYS.
The single most common source of wrong geometry in AI-generated SolidWorks code:

- **Every length in every API call is in METERS.** A 50 mm dimension is `0.05`.
- **Every angle is in RADIANS.** 45° is `Math.PI / 4`.
- The document's display units (mm, inches) affect only the UI, never the API.

Write one conversion boundary and use it everywhere:
```csharp
static double MM(double mm) => mm / 1000.0;
static double IN(double inches) => inches * 0.0254;
static double DEG(double deg) => deg * Math.PI / 180.0;
```
Never inline `* 0.001` in scattered places. If the drawing is in inches, convert at parse time and carry mm or meters internally — pick ONE internal unit and document it in the plan JSON.

## RULE #2: Create documents from a template, explicitly
```csharp
string template = swApp.GetUserPreferenceStringValue(
    (int)swUserPreferenceStringValue_e.swDefaultTemplatePart);
IModelDoc2 swModel = (IModelDoc2)swApp.NewDocument(template, 0, 0, 0);
```
`NewDocument` returns null if the template path is wrong — check for null and fail loudly.

## RULE #3: Selection is state. Manage it explicitly.
Most feature-creation calls operate on the current selection. Bugs come from stale selections.

- **Clear before selecting:** `swModel.ClearSelection2(true);`
- **Select by ID:** 
  ```csharp
  bool ok = ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
  ```
  ALWAYS check the returned bool. A silent `false` means the next feature call operates on nothing or the wrong thing.
- **Plane names are localization-dependent.** "Front Plane" is English. Safer: get the standard planes by index:
  ```csharp
  IFeature front = (IFeature)swModel.FeatureByPositionReverse( /* or walk tree */ );
  ```
  or at minimum, try "Front Plane" and fall back to walking the feature tree for the first three features of type `"RefPlane"` (they are always Front, Top, Right in creation order).
- **Marks matter.** The `Mark` argument in `SelectByID2` routes selections to specific slots of multi-selection features (e.g., sweep profile vs path). Look up the required mark for each feature type in the API docs; wrong marks are a classic silent failure.
- **Prefer selecting entities you just created.** After creating a sketch, keep its `IFeature`/name. Name features immediately (see doc 03) and select by your own known names, never by default names like "Boss-Extrude1" hardcoded from a recording.

## RULE #4: Sketch lifecycle
```csharp
ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
swModel.SketchManager.InsertSketch(true);   // open sketch
// ... create entities via SketchManager (CreateLine, CreateCircleByRadius, ...)
swModel.SketchManager.InsertSketch(true);   // CLOSE the sketch (same call toggles)
```
- Forgetting to close a sketch before calling a feature method is a common failure.
- Turn OFF "add to DB" grid-snapping side effects: `swModel.SketchManager.AddToDB = true;` before drawing, `= false` after. This prevents SolidWorks from snapping your precise coordinates to the grid, and speeds things up.
- Also consider `swModel.SketchManager.DisplayWhenAdded = false;` for speed on complex sketches (re-enable after).

## RULE #5: Check every return value
The API signals failure by returning `null`, `false`, or an error enum — it rarely throws. AI-generated code that ignores return values "succeeds" while building nothing.

```csharp
IFeature feat = swModel.FeatureManager.FeatureExtrusion3(...) as IFeature;
if (feat == null) throw new BuildException("Boss extrude failed", lastPlanStep);
```
Wrap this pattern in a helper: `Require(feat, "step 3: base extrude")`.

## RULE #6: Rebuild and inspect errors
After each feature (or at checkpoints):
```csharp
swModel.ForceRebuild3(false);
int errCount = 0; // walk features checking GetErrorCode2
```
Use `IFeature.GetErrorCode2` on each feature to detect rebuild errors/warnings programmatically (see doc 07 for the full validation routine).

## RULE #7: Coordinate system conventions
- Part origin is (0,0,0); the three standard planes intersect there.
- Front Plane = XY plane (Z normal), Top Plane = XZ (Y normal), Right Plane = YZ (X normal).
- When a sketch is open, `CreateLine`/`CreateCircleByRadius` coordinates are in the **sketch's 2D space** (still meters). Know which model axes the sketch X/Y map to for the chosen plane.
- Convention for this pipeline: put the drawing's FRONT view on the Front Plane, with drawing X → sketch X and drawing Y → sketch Y. The extrusion depth then comes from the side/top view.

## RULE #8: Performance & stability basics
- One SolidWorks instance, reused. Starting SW per part is slow (30+ s).
- Suppress dialogs: run with `swApp.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swStopDebuggingVstaOnExit, ...)` as needed; more importantly, avoid any API that pops UI (no `MessageBox`, no dialogs).
- Batch operations between rebuilds; call `ForceRebuild3` at checkpoints, not after every sketch segment.
- Release COM objects deterministically in long runs (`Marshal.ReleaseComObject`) or you will leak RCWs and eventually destabilize the session.

## Constants
Use `SolidWorks.Interop.swconst` enums (`swEndConditions_e`, `swSelectType_e`, etc.) — never magic integers. This makes generated code self-documenting and lets the compiler help.

## Where to find call signatures
The API Help (help.solidworks.com → API section) documents every interface with VBA/C# examples. When unsure of a signature, the agent should:
1. Search local knowledge of common calls (doc 06 has vetted recipes).
2. If a call keeps failing with type errors, record a VBA macro of the equivalent UI action and read the recorded arguments — then port to C#.
