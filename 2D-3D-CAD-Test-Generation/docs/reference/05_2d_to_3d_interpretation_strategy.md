# 05 — 2D→3D Interpretation Strategy (From Views to a Feature Plan)

Goal of this stage: transform `drawing.json` into `plan.json` — an ordered list of parametric feature operations. **No API code is written until the plan exists and passes its own consistency checks.**

## Core mental model
A machined/manufactured part is a **base solid plus an ordered list of additive and subtractive features**. Reverse-engineer the designer's likely feature tree, not just the final shape. Feature-based models rebuild reliably and are editable; "sculpted" models are fragile.

## Step 1: Classify the part archetype
Look at the views and pick the dominant paradigm:
| Evidence | Archetype | Base feature |
|---|---|---|
| One circular view + rectangular profile with centerline | **Rotational** (shaft, bushing, pulley, flange) | REVOLVE the half-profile about the centerline |
| Constant-thickness outline, mostly one "flat" view carries the shape | **Prismatic/plate** (bracket plate, gasket, cover) | EXTRUDE the outline by the thickness |
| Uniform thin wall, bend lines/radii, flat-pattern notes | **Sheet metal** | Base flange + bends (or extrude if bends absent) |
| Complex in all three views | **Multi-feature prismatic** | Extrude the largest/most stable footprint, add features |

Rotational check: if >70% of dimensions in one view are Ø values sharing one centerline → revolve.

## Step 2: Choose the base feature (most important single decision)
- Base = the largest single volume from which everything else is added/cut. Usually the overall footprint × overall thickness, or the outer revolve envelope.
- Prefer the view with the most 2D detail as the base sketch profile (the "characteristic view" — normally the front view).
- Sketch plane mapping convention (fixed for this pipeline): front view → Front Plane; depth taken from top/right view. For revolves: profile on Front Plane, axis = sketch centerline through origin.
- **Anchor to the origin deliberately**: put the part's primary datum (or symmetry center) at the origin. Symmetric parts: sketch symmetric about origin and use MID-PLANE extrusion — later symmetry-dependent features become trivial.

## Step 3: Decompose remaining geometry into features, in dependency order
Ordering rules:
1. Base solid.
2. Large additive bosses (extrudes/revolves that add material).
3. Large subtractive cuts (pockets, slots, steps).
4. Holes (simple, cbore, csk, tapped) — after the material they cut through exists.
5. Patterns (linear/circular) of holes/features.
6. Shell (if it's a hollow housing — shell late but before small edge features on outer walls if walls would remove them; usually: shell after main bosses, before cosmetic fillets).
7. Fillets and chamfers LAST (they are cosmetic/finishing; putting them early breaks downstream sketches on faces).

Each feature is justified by specific drawing evidence — record the source view + dimensions used. If a solid's shape can be explained two ways (e.g., a step could be a cut in a big block OR a boss on a small block), prefer the interpretation with **fewer features** and **standard manufacturing logic** (stock removal for machined parts).

## Step 4: Resolve the third dimension for every 2D feature
Every closed loop in a view needs a depth from ANOTHER view:
- Circle in top view + dashed lines full-height in front view → THRU hole.
- Dashed lines stopping partway + depth dim → blind hole with that depth.
- No depth found anywhere → apply defaults with a logged GUESS: holes → THRU ALL; pockets → look for section views first; steps → derive from stacked dimensions.

## Step 5: Symmetry & patterns
- Centerlines with equal features on both sides → model one side + MIRROR, or sketch symmetric. This halves the chance of transcription errors.
- Bolt circles ("4X Ø5.5 ON Ø80 B.C.") → one hole + circular pattern about the center axis, count 4, equal spacing (unless angles given).
- Grids of holes with equal spacing → linear pattern.

## Step 6: Emit `plan.json`
```json
{
  "part_name": "mounting_bracket",
  "internal_units": "mm",
  "steps": [
    {"id": 1, "op": "sketch", "plane": "Front", "name": "sk_base",
     "entities": [{"type":"rect_center","w":120,"h":60,"corner_r":10}],
     "evidence": "front view outline, dims 120/60/R10"},
    {"id": 2, "op": "extrude_boss", "sketch": "sk_base", "end": "mid_plane",
     "depth": 25, "name": "base_plate", "evidence": "right view thickness 25"},
    {"id": 3, "op": "hole_simple", "face_of": "base_plate", "dia": 5.5,
     "thru": true, "positions_from_origin": [[-50,-20],[50,-20],[-50,20],[50,20]],
     "name": "mount_holes", "evidence": "front view 4X Ø5.5 THRU"},
    {"id": 4, "op": "fillet_edges", "radius": 2, "scope": "note_all_fillets",
     "name": "edge_fillets", "evidence": "note: ALL FILLETS R2"}
  ],
  "expected": {"bbox": [120, 60, 25], "hole_count": 4},
  "assumptions": [
    {"step": 3, "text": "holes assumed THRU (no depth given; dashed lines full height)", "confidence": "high"}
  ]
}
```

## Step 7: Plan self-check (before any code)
- Does every drawing dimension appear in exactly one plan step (or as derived/reference)? Unused dimensions = something was missed.
- Do plan `expected` values (bounding box, hole counts, key diameters) match the drawing's overall dims?
- Mentally rebuild each orthographic view from the plan and compare silhouettes to the drawing. The isometric view (if present) is the tiebreaker for topology.
- Any step with confidence "low" → attempt an alternative interpretation; if both are plausible, pick the more standard one and record BOTH in the assumptions list so the user can flip it at the end (doc 09).
