"""Iteration 7 — fix reference-axis creation, finish the Mark comparison,
then run Tier 5's explicit deliberate-failure list.

Hypothesis from iteration 6: `make_axis_from_bore` matched the OUTER cylindrical
wall of the round plate (R 3.0), which is concentric with the bore (R 0.5), so
`InsertAxis2` was handed the wrong face. Fix: match on RADIUS as well as centre.
"""
from __future__ import annotations

import json
import math

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier3b_tier5")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK ' if ok else 'XX '}] {step:<44} {detail}", flush=True)


def extrude(depth_in, name):
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, depth_in * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = name
    return f


def cut_active(name):
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)

    def _t(flip):
        return DOC.FeatureManager.FeatureCut4(
            True, False, flip, EC_THROUGH_ALL, EC_BLIND, 0.0, 0.01,
            False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)

    feat = _t(True) or _t(False)
    if feat is not None:
        feat.Name = name
    return feat


def drill(name, x, y, dia):
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(x * IN, y * IN, 0, dia / 2 * IN)
    return cut_active(name)


def cyl_faces():
    """(centre_x, centre_y, dia, face) for every cylindrical face, inches."""
    out = []
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsCylinder"):
                    continue
                p = list(sw_get(surf, "CylinderParams"))
                out.append((round(p[0] / IN, 3), round(p[1] / IN, 3),
                            round(2 * p[6] / IN, 3), face))
            except Exception:
                continue
    return out


def last_feature_of_type(type_name):
    last = None
    try:
        feat = sw_get(DOC, "FirstFeature")
        while feat is not None:
            try:
                if sw_get(feat, "GetTypeName2") == type_name:
                    last = feat
            except Exception:
                pass
            feat = sw_get(feat, "GetNextFeature")
    except Exception:
        pass
    return last


def axis_from_bore(cx, cy, dia):
    """Select the bore face by centre AND DIAMETER, then InsertAxis2."""
    for x, y, d, face in cyl_faces():
        if math.hypot(x - cx, y - cy) > 0.01 or abs(d - dia) > 0.01:
            continue
        DOC.ClearSelection2(True)
        sel = face.Select4(False, _null_dispatch())
        made = DOC.InsertAxis2(True) if sel else False
        ax = last_feature_of_type("RefAxis") if made else None
        return (sel, made, sw_get(ax, "Name") if ax is not None else None)
    return (False, False, None)


# --------------------------------------------------------------------------- #
def tier3_patterns():
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(0, 0, 0, 3 * IN)
    extrude(0.5, "round_plate")
    drill("centre_bore", 0.0, 0.0, 1.0)
    drill("seed_hole", 2.0, 0.0, 0.3)
    faces = cyl_faces()
    record("7.1 plate + bore + seed",
           len(faces) >= 3, f"{len(faces)} cyl faces: "
           f"{[(f[0], f[1], f[2]) for f in faces]}")

    sel, made, name = axis_from_bore(0.0, 0.0, 1.0)   # <- diameter now matched
    record("7.2 axis from BORE (radius-matched)", bool(name),
           f"face_selected={sel} InsertAxis2={made} axis={name!r}")
    if not name:
        return None

    results = {}
    for label, mark in (("correct Mark 4", 4), ("WRONG Mark 1", 1)):
        before = len(cyl_faces())
        DOC.ClearSelection2(True)
        a = DOC.Extension.SelectByID2(name, "AXIS", 0, 0, 0, False, 1,
                                      _null_dispatch(), 0)
        f = DOC.Extension.SelectByID2("seed_hole", "BODYFEATURE", 0, 0, 0,
                                      True, mark, _null_dispatch(), 0)
        feat, err = None, ""
        try:
            feat = DOC.FeatureManager.FeatureCircularPattern5(
                4, 2 * math.pi / 4, False, "NULL", False, True, False,
                False, False, False, 1, 2 * math.pi / 4, "NULL", False)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
        after = len(cyl_faces())
        results[label] = {"axis_sel": bool(a), "seed_sel": bool(f),
                          "feature": feat is not None, "faces": [before, after],
                          "gained": after - before, "error": err}
        record(f"7.3 circular pattern — {label}",
               feat is not None and after - before == 3,
               f"feature={feat is not None} faces {before}->{after} "
               f"(+{after - before}) {err}")
        if feat is not None:
            try:
                feat.Name = f"pat_{mark}"
                lab.delete_feature(DOC, f"pat_{mark}")
            except Exception:
                pass
    LOG.append({"step": "7.3 mark comparison", "ok": True, "detail": json.dumps(results)})
    lab.save(DOC, "t3_bolt_circle.sldprt")
    return results


# --------------------------------------------------------------------------- #
# TIER 5 — the explicit deliberate-failure list
# --------------------------------------------------------------------------- #
def tier5():
    print("\nTIER 5 — deliberate failures")

    # 5.1 mm passed as metres (no conversion)
    lab.open_sketch(DOC, "Top Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, 50, 25, 0)   # 50 METRES
    f = extrude(0.5, "unit_slip")
    m = lab.measure(DOC)
    bbox = m.get("bbox_in") or [0, 0, 0]
    record("5.1 mm passed as metres", f is not None and max(bbox) > 1000,
           f"bbox {bbox} in — a 50 mm rectangle became {max(bbox):.0f} in "
           f"({max(bbox) / 39.37:.0f} m)")
    lab.delete_feature(DOC, "unit_slip")

    # 5.2 extrude with the sketch left OPEN
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, 1 * IN, 1 * IN, 0)
    err = ""
    try:                                   # deliberately skip InsertSketch(True)
        f = DOC.FeatureManager.FeatureExtrusion3(
            True, False, False, EC_BLIND, EC_BLIND, 0.25 * IN, 0.01,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False)
    except Exception as e:
        f, err = None, f"{type(e).__name__}: {str(e)[:60]}"
    record("5.2 extrude with the sketch left OPEN", True,
           f"returned {'a feature' if f else 'None'}; {err or 'no exception'}")
    if f is not None:
        try:
            f.Name = "open_sketch_boss"
            lab.delete_feature(DOC, "open_sketch_boss")
        except Exception:
            pass
    try:
        DOC.SketchManager.InsertSketch(True)
    except Exception:
        pass
    DOC.ClearSelection2(True)

    # 5.3 fillet radius larger than the local geometry
    before = lab.measure(DOC)
    DOC.ClearSelection2(True)
    n = 0
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    err = ""
    try:
        f = DOC.FeatureManager.FeatureFillet3(
            195, 5.0 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception as e:
        f, err = None, f"{type(e).__name__}: {str(e)[:60]}"
    record("5.3 fillet R5.0 on a 0.5-thick plate", f is None,
           f"{n} edges selected; returned {'a feature' if f else 'None'}; "
           f"{err or 'no exception'}")
    if f is not None:
        try:
            f.Name = "huge_fillet"
            lab.delete_feature(DOC, "huge_fillet")
        except Exception:
            pass

    # 5.4 a boss that does not touch the base -> second body
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(20 * IN, 20 * IN, 0, 21 * IN, 21 * IN, 0)
    f = extrude(0.25, "detached_boss")
    m = lab.measure(DOC)
    record("5.4 boss not touching the base", m.get("body_count", 1) > 1,
           f"body_count = {m.get('body_count')} (a body-count check catches this)")
    lab.delete_feature(DOC, "detached_boss")

    # 5.5 SelectByID2 on a name that does not exist
    DOC.ClearSelection2(True)
    ret = DOC.Extension.SelectByID2("no_such_feature_xyz", "BODYFEATURE", 0, 0, 0,
                                    False, 0, _null_dispatch(), 0)
    record("5.5 SelectByID2 on a stale name", ret is False,
           f"returned {ret!r} (type {type(ret).__name__}) — falsy, never raises")


if __name__ == "__main__":
    tier3_patterns()
    tier5()
    final = lab.measure(DOC)
    print(f"\nfinal: bbox {final.get('bbox_in')} bodies {final.get('body_count')} "
          f"clean {final.get('rebuild_clean')}")
    (RESULTS / "tier3b_tier5.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"recorded {sum(1 for r in LOG if r.get('ok'))}/{len(LOG)} OK")
    lab.close(DOC)
