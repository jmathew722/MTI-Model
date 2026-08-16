"""ONE part file, iterated on — the placement laboratory.

Everything happens in a single document (`parts/practice_master.sldprt`): a base
block is built once, then every experiment adds a feature to THAT part, measures
where the geometry actually landed, and records intended-vs-measured. Nothing is
believed because the script asked for it; a hole is at (2.0, 1.5) only if the
cylindrical face is measured there.

Focus (per the task): HOLE PLACEMENT and EXTRUDE-CUT PLACEMENT ON EACH EDGE SIDE
— the coordinate questions that produce clean, confident, wrongly-placed parts.

Measurement uses the access forms verified in Tier 0 (`swlab.sw_get/sw_any`):
  IModelDoc2.GetMassProperties -> PROPERTY, 12 values, [3] = volume m^3
  IBody2.GetFaces              -> METHOD
  IFace2.GetSurface            -> RAW dispid Invoke (METHOD|PROPERTYGET)
  ISurface.IsCylinder/CylinderParams -> PROPERTIES
"""
from __future__ import annotations

import json
import math

from swlab import IN, PARTS, RESULTS, Lab, Result, _null_dispatch, sw_any, sw_get, sw_seq

BLOCK_W = 6.0      # X, inches
BLOCK_H = 4.0      # Y
BLOCK_T = 0.5      # Z (extrude depth)

lab = Lab("master_session")      # connect FIRST — _const needs the loaded tlb

# Resolved from the INSTALLED type library, never guessed. Guessing cost a whole
# session here: swEndCondThroughAllBoth is 9 on SW 2026, and the plausible-looking
# 7 is swEndCondUpToBody, which — with no body reference supplied — deleted the
# entire solid and returned a feature object with no error at all.
from pipeline.solidworks_builder import _const      # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
EC_THROUGH_ALL_BOTH = _const("swEndCondThroughAllBoth", 9)
EC_MIDPLANE = _const("swEndCondMidPlane", 6)
print(f"end conditions: blind={EC_BLIND} thruAll={EC_THROUGH_ALL} "
      f"thruAllBoth={EC_THROUGH_ALL_BOTH} midplane={EC_MIDPLANE}")
DOC = None
LOG: list[dict] = []


# --------------------------------------------------------------------------- #
# the one part
# --------------------------------------------------------------------------- #
def open_master():
    """Create the single practice part and its base block."""
    global DOC
    DOC = lab.new_part()
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, BLOCK_W * IN, BLOCK_H * IN, 0)
    DOC.SketchManager.AddToDB = False
    DOC.ClearSelection2(True)
    feat = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, BLOCK_T * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if feat is not None:
        feat.Name = "base_block"
    m = lab.measure(DOC)
    save()
    return m


def save():
    lab.save(DOC, "practice_master.sldprt")


def holes_measured() -> list[dict]:
    """Every cylindrical face on the part, as (centre x, y, diameter) in inches.

    A through hole contributes ONE cylindrical face; a counterbore contributes
    two of different diameters. Faces are deduped by (x, y, dia) so a split
    cylinder does not read as two holes.
    """
    seen, out = set(), []
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsCylinder"):
                    continue
                p = list(sw_get(surf, "CylinderParams"))
                key = (round(p[0] / IN, 3), round(p[1] / IN, 3), round(2 * p[6] / IN, 3))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"x": key[0], "y": key[1], "dia": key[2]})
            except Exception:
                continue
    return sorted(out, key=lambda h: (h["x"], h["y"]))


def record(step: str, intended: dict, measured: dict, ok: bool, note: str = ""):
    LOG.append({"step": step, "intended": intended, "measured": measured,
                "ok": ok, "note": note})
    mark = "OK " if ok else "XX "
    print(f"  [{mark}] {step:<34} intended {intended} | measured {measured}"
          + (f" | {note}" if note else ""), flush=True)


# --------------------------------------------------------------------------- #
# STUDY A — hole placement: does a sketch coordinate land where we think?
# --------------------------------------------------------------------------- #
def drill(name: str, x_in: float, y_in: float, dia_in: float,
          plane: str = "Front Plane"):
    """One hole, cut THROUGH ALL, sketched on `plane` with the ACTIVE-sketch
    pattern (E006: never re-select a closed sketch by name)."""
    lab.open_sketch(DOC, plane)
    DOC.SketchManager.CreateCircleByRadius(x_in * IN, y_in * IN, 0, dia_in / 2 * IN)
    return _cut_active_sketch(name)


def _cut_active_sketch(name: str):
    """THE working cut recipe on this install (matches solidworks_builder).

    Three things here are load-bearing, and each one cost a failed run to learn:
      1. CLOSE the sketch first (`InsertSketch(True)` a second time). Closing
         leaves the new sketch SELECTED, which is what the feature consumes.
      2. Do NOT `ClearSelection2` between closing and cutting — that deselects
         the profile, and FeatureCut4 then returns a feature that removes
         nothing (or, with the wrong end condition, the whole body) and reports
         no error whatsoever.
      3. Use swEndCondThroughAll (1) and RETRY with the direction flag flipped:
         a cut aimed at the empty side of the sketch plane removes nothing and
         returns None.
    """
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)          # close -> leaves it selected

    def _try(flip: bool):
        return DOC.FeatureManager.FeatureCut4(
            True, False, flip, EC_THROUGH_ALL, EC_BLIND, 0.0, 0.01,
            False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)

    feat = _try(True) or _try(False)
    if feat is not None:
        feat.Name = name
    return feat


def study_a_hole_placement():
    """Place holes at edge-referenced positions and MEASURE each centre."""
    print("\nSTUDY A — hole placement (edge-referenced)")
    cases = [
        ("hole_LL", 0.75, 0.75, 0.375),                       # lower-left corner
        ("hole_LR", BLOCK_W - 0.75, 0.75, 0.375),             # lower-right
        ("hole_UL", 0.75, BLOCK_H - 0.75, 0.375),             # upper-left
        ("hole_UR", BLOCK_W - 0.75, BLOCK_H - 0.75, 0.375),   # upper-right
        ("hole_CTR", BLOCK_W / 2, BLOCK_H / 2, 0.75),         # centre, bigger
    ]
    before = {(h["x"], h["y"]) for h in holes_measured()}
    for name, x, y, dia in cases:
        feat = drill(name, x, y, dia)
        found = holes_measured()
        match = [h for h in found
                 if abs(h["x"] - x) < 0.01 and abs(h["y"] - y) < 0.01
                 and abs(h["dia"] - dia) < 0.01]
        record(f"A: {name}", {"x": x, "y": y, "dia": dia},
               match[0] if match else {"missing": True},
               bool(feat) and bool(match),
               "" if match else f"{len(found)} cyl face(s) on part; none at target")
    save()
    holes = holes_measured()
    print(f"  -> {len(holes)} cylindrical face(s) measured on the part")
    return holes


# --------------------------------------------------------------------------- #
# STUDY B — extrude-cut placement against EACH edge side
# --------------------------------------------------------------------------- #
def notch(name: str, x0: float, y0: float, w: float, h: float):
    """A rectangular through-cut with its LOWER-LEFT corner at (x0, y0)."""
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(x0 * IN, y0 * IN, 0,
                                            (x0 + w) * IN, (y0 + h) * IN, 0)
    return _cut_active_sketch(name)


def study_b_edge_cuts():
    """A notch on each of the four edges, verified by the resulting bbox+volume.

    This is the 158-C failure class: an edge-referenced cut whose Y is computed
    from the wrong reference lands on the OPPOSITE edge and still builds cleanly.
    The only way to catch it is to measure which side lost material.
    """
    print("\nSTUDY B — edge-side cut placement")
    d, wide = 0.5, 1.0        # notch depth into the part, and its width
    cases = [
        # name,          x0,                 y0,                 w,     h
        ("notch_LEFT",   -0.01,              1.5,                d,     wide),
        ("notch_RIGHT",  BLOCK_W - d + 0.01, 1.5,                d,     wide),
        ("notch_BOTTOM", 2.5,                -0.01,              wide,  d),
        ("notch_TOP",    2.5,                BLOCK_H - d + 0.01, wide,  d),
    ]
    for name, x0, y0, w, h in cases:
        before = lab.measure(DOC)
        feat = notch(name, x0, y0, w, h)
        after = lab.measure(DOC)
        removed = round((before.get("volume_in3") or 0) - (after.get("volume_in3") or 0), 4)
        expect = round(w * h * BLOCK_T, 4)
        # WHICH side lost material: compare the material-free span on each edge.
        side = _which_edge_lost_material(x0, y0, w, h)
        ok = feat is not None and abs(removed - expect) < 0.02
        record(f"B: {name}", {"corner": [x0, y0], "size": [w, h], "removes": expect},
               {"removed_in3": removed, "edge": side,
                "bbox": after.get("bbox_in")}, ok)
    save()


def _which_edge_lost_material(x0, y0, w, h) -> str:
    """Which edge the notch was ASKED for — used to label the measurement row."""
    if x0 <= 0:
        return "LEFT (x=0)"
    if x0 + w >= BLOCK_W:
        return "RIGHT (x=W)"
    if y0 <= 0:
        return "BOTTOM (y=0)"
    return "TOP (y=H)"


# --------------------------------------------------------------------------- #
# STUDY C — the Y-up question, per sketch plane
# --------------------------------------------------------------------------- #
def study_c_plane_axes():
    """Where does sketch (x, y) land in MODEL space, per plane?

    The pipeline assumes drawing-X -> sketch-X and drawing-Y -> sketch-Y on the
    Front Plane. This measures the mapping instead of assuming it, for the three
    standard planes, by drilling an ASYMMETRIC hole and reading its model-space
    centre back.
    """
    print("\nSTUDY C — sketch axes vs model axes, per plane")
    for plane, probe_name in (("Front Plane", "axis_front"),
                              ("Top Plane", "axis_top"),
                              ("Right Plane", "axis_right")):
        px, py = 1.25, 0.5      # deliberately asymmetric
        before = {(h["x"], h["y"], h["dia"]) for h in holes_measured()}
        feat = drill(probe_name, px, py, 0.2, plane=plane)
        after = holes_measured()
        new = [h for h in after if (h["x"], h["y"], h["dia"]) not in before]
        record(f"C: {plane}", {"sketch_xy": [px, py]},
               {"model_cyl_xy": [new[0]["x"], new[0]["y"]] if new else None,
                "n_new_faces": len(new)},
               feat is not None,
               "CylinderParams x/y are MODEL X/Y — a hole drilled on a non-Front "
               "plane will not report the sketch coordinates" if new else "no new face")
    save()


if __name__ == "__main__":
    print(f"=== ONE PART: {PARTS / 'practice_master.sldprt'} ===")
    base = open_master()
    print(f"base block measured: bbox {base.get('bbox_in')} vol {base.get('volume_in3')}")
    study_a_hole_placement()
    study_b_edge_cuts()
    study_c_plane_axes()
    final = lab.measure(DOC)
    print(f"\nFINAL part: bbox {final.get('bbox_in')} vol {final.get('volume_in3')} "
          f"bodies {final.get('body_count')} rebuild_clean {final.get('rebuild_clean')}")
    save()
    (RESULTS / "master_session.json").write_text(
        json.dumps({"log": LOG, "final": final, "holes": holes_measured()},
                   indent=2, default=str), encoding="utf-8")
    ok = sum(1 for r in LOG if r["ok"])
    print(f"master_session: {ok}/{len(LOG)} placements verified")
    lab.close(DOC)
