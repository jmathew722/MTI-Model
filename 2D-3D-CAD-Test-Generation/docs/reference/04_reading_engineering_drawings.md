# 04 — Reading Engineering Drawings (2D Input Interpretation)

The pipeline's accuracy is capped by how well the drawing is parsed. Do this step slowly and completely; write everything extracted into a structured `drawing.json` before any 3D reasoning.

## Step 1: Title block first
The title block (usually bottom-right) is read BEFORE any geometry. Extract:
- **Units** (mm vs inch — often "UNLESS OTHERWISE SPECIFIED DIMENSIONS ARE IN MILLIMETERS"). If absent: dimensions with 2-3 decimal places and values like 0.500 suggest inches; whole numbers like 50, 120 suggest mm. Flag as a GUESS if inferred.
- **Scale** (e.g., 1:2). Scale affects printed size only — dimension VALUES are always true size. Never scale dimension text.
- **Projection angle symbol**: truncated cone icon.
  - **Third angle** (US/ANSI): right view is placed to the RIGHT of front view; top view ABOVE.
  - **First angle** (EU/ISO): right view is placed to the LEFT; top view BELOW.
  - Getting this wrong mirrors the part. If no symbol: ANSI title block / inch units → assume third angle; ISO / mm → could be either, but first angle is common in Europe. Log the guess.
- **Material**, part name/number, revision, general tolerance block (e.g., "±0.1 unless noted").

## Step 2: Inventory the views
Identify and label every view:
- **Orthographic views** (front, top, right/left, bottom, rear). The front view is typically the most detailed / most characteristic profile.
- **Section views** (labeled A-A, B-B; look for cutting-plane lines with arrows in a parent view; hatched areas = solid material cut by the plane). Sections reveal internal features: bores, wall thickness, internal steps.
- **Detail views** (circled region, labeled, at larger scale): fine features — grooves, small fillets, thread reliefs.
- **Auxiliary views**: true shape of inclined faces.
- **Isometric/pictorial view**: not dimensioned, but GOLD for disambiguating topology. Always use it to sanity-check the mental 3D model.

## Step 3: Line-type semantics
| Line | Meaning |
|---|---|
| Thick solid | Visible edge |
| Dashed | Hidden edge (feature behind the visible surface — e.g., a hole seen from the side) |
| Thin chain (long-short) | Centerline / axis of symmetry / bolt circle |
| Thin solid with arrows + number | Dimension |
| Thin zig-zag / wavy | Break (part longer than shown) |
| Hatching | Cut material in a section view |
| Phantom (long-short-short) | Alternate position / adjacent part |

Centerlines are critical: they mark hole axes, revolve axes, and symmetry. A circular view + a rectangular side view with a centerline down the middle = **revolved part** (shaft, bushing).

## Step 4: Dimension extraction
Extract every dimension into structured form: `{value, unit, type, tolerance, applies_to, view}`.

- **Diameter Ø vs radius R.** Ø30 is a 30 diameter (radius 15). Confusing these is a classic 2× error.
- **Typical/repeat notation**: "4X Ø5.5" = four identical holes of Ø5.5. "TYP" = applies to all similar features.
- **Thread callouts**: `M8x1.25 - 6H` (metric) or `1/4-20 UNC` (inch). For modeling: use the Hole Wizard API if available; otherwise model as tap-drill diameter (M8 → Ø6.8 hole) and note the thread in a log — do NOT model helical threads unless the drawing demands it.
- **Countersink/counterbore symbols**: ⌵ (csk, with angle e.g. 90°) and ⌴ (cbore, with diameter and depth ⌵/↧ symbols). Depth symbol ↧ means depth of the feature.
- **THRU / THRU ALL** vs a depth value: sets the extrude/cut end condition.
- **Tolerances**: bilateral (25 ±0.1), limit (25.1/24.9), or from the general tolerance block. Model to the **nominal** value; tolerances matter for validation bands (doc 07), not geometry.
- **Reference dims** in parentheses (25): informational, must not be independently enforced — good validation checks though.
- **Chained vs baseline (datum) dimensioning**: resolve every feature's position into a single consistent coordinate frame relative to a chosen origin before planning.

## Step 5: GD&T (read, mostly don't model)
Feature control frames (rectangular boxes: ⌖ position, ⏥ flatness, ⌭ concentricity, etc.) and datum flags (A, B, C):
- These control manufacturing quality, not nominal shape. For the 3D model, they change nothing geometrically.
- BUT datums tell you the design intent origin: prefer placing the part origin / feature positions relative to datum A/B/C. This makes the model match how the part is inspected.

## Step 6: Notes
Read all general notes. They frequently contain modeling-relevant facts: "BREAK ALL SHARP EDGES 0.5 MAX" (add small chamfers/fillets or log as cosmetic-skip), "MATERIAL: 6061-T6", "ALL FILLETS R2 UNLESS NOTED".

## Step 7: Cross-view consistency check (before planning)
- Every feature must be consistent across views: a hole shown as a circle in the top view must appear as hidden dashed lines of the same width in the front view, at the same X location.
- Sum up chained dimensions and compare to the overall dimension. Mismatch = misread a number → re-examine.
- Count features: "4X" callout should match 4 circles visible in the view.
- If a dimension is missing (drawing under-dimensioned), first try to derive it (symmetry, tangency, overall minus knowns). If underivable, measure the drawing geometry at stated scale as a LAST resort, and log it as a low-confidence GUESS.

## Output of this stage: `drawing.json`
```json
{
  "units": "mm", "projection": "third_angle", "material": "6061-T6",
  "general_tolerance": 0.1,
  "views": [{"id":"front","type":"ortho"},{"id":"A-A","type":"section","parent":"front"}],
  "overall": {"x": 120, "y": 60, "z": 25},
  "features_2d": [
    {"id":"outline","view":"front","shape":"rect_with_fillets","dims":{"w":120,"h":60,"r":10}},
    {"id":"mount_holes","view":"front","shape":"circle","count":4,"dia":5.5,
     "positions":[[10,10],[110,10],[10,50],[110,50]], "thru": true}
  ],
  "notes": ["BREAK SHARP EDGES 0.5 MAX"],
  "guesses": [{"field":"projection","reason":"no symbol; ANSI block → third angle"}]
}
```
