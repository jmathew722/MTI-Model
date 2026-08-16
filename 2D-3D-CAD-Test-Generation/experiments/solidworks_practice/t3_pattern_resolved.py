"""Iteration 6 — close the Tier-2 pattern gap, then run the Mark comparison.

Tier 2 could not build a circular pattern, so the correct-vs-wrong Mark question
stayed open. Production DOES build them; the difference is how the axis is
obtained and named. This reproduces production's approach exactly:
  select the bore's cylindrical FACE -> IModelDoc2.InsertAxis2(True)
  -> re-find the created axis by walking the tree for a "RefAxis" feature
  -> select THAT name at Mark 1, the seed feature at Mark 4.
"""
from __future__ import annotations

import json
import math

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier3_pattern")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK ' if ok else 'XX '}] {step:<38} {detail}", flush=True)


def block():
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(0, 0, 0, 3 * IN)      # round plate
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.5 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "round_plate"


def drill(name, x, y, dia):
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(x * IN, y * IN, 0, dia / 2 * IN)
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


def count_cyl():
    n = 0
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if surf and sw_get(surf, "IsCylinder"):
                    n += 1
            except Exception:
                continue
    return n


def last_feature_of_type(type_name: str):
    """Walk the tree for the LAST feature of a type. FirstFeature/GetNextFeature/
    GetTypeName2 are all PROPERTIES here (MANUAL rule 2)."""
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


def make_axis_from_bore(cx, cy):
    """Production's approach: select the bore FACE, then IModelDoc2.InsertAxis2."""
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsCylinder"):
                    continue
                p = list(sw_get(surf, "CylinderParams"))
                if math.hypot(p[0] / IN - cx, p[1] / IN - cy) > 0.01:
                    continue
                DOC.ClearSelection2(True)
                if face.Select4(False, _null_dispatch()) and DOC.InsertAxis2(True):
                    ax = last_feature_of_type("RefAxis")
                    if ax is not None:
                        name = sw_get(ax, "Name")
                        return name
            except Exception:
                continue
    return None


if __name__ == "__main__":
    block()
    drill("centre_bore", 0.0, 0.0, 1.0)       # concentric bore -> the axis source
    drill("seed_hole", 2.0, 0.0, 0.3)         # the seed to pattern
    base = count_cyl()
    record("3.1 plate + bore + seed", base >= 2, f"{base} cylindrical face(s)")

    axis_name = make_axis_from_bore(0.0, 0.0)
    record("3.2 axis from bore face (InsertAxis2)", bool(axis_name),
           f"axis feature name = {axis_name!r}")

    if axis_name:
        for label, mark in (("correct Mark 4", 4), ("WRONG Mark 1", 1)):
            before = count_cyl()
            DOC.ClearSelection2(True)
            a = DOC.Extension.SelectByID2(axis_name, "AXIS", 0, 0, 0, False, 1,
                                          _null_dispatch(), 0)
            f = DOC.Extension.SelectByID2("seed_hole", "BODYFEATURE", 0, 0, 0,
                                          True, mark, _null_dispatch(), 0)
            feat, err = None, ""
            try:
                feat = DOC.FeatureManager.FeatureCircularPattern5(
                    4, 2 * math.pi / 4, False, "NULL", False, True, False,
                    False, False, False, 1, 2 * math.pi / 4, "NULL", False)
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:70]}"
            after = count_cyl()
            gained = after - before
            record(f"3.3 circular pattern — {label}",
                   feat is not None and gained == 3,
                   f"axis_sel={bool(a)} seed_sel={bool(f)} feature={feat is not None} "
                   f"cyl faces {before}->{after} (+{gained}) {err}")
            if feat is not None:
                try:
                    feat.Name = f"pat_{mark}"
                    lab.delete_feature(DOC, f"pat_{mark}")
                except Exception:
                    pass

    lab.save(DOC, "t3_bolt_circle.sldprt")
    (RESULTS / "tier3_pattern.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"tier3_pattern: {sum(1 for r in LOG if r['ok'])}/{len(LOG)} OK")
    lab.close(DOC)
