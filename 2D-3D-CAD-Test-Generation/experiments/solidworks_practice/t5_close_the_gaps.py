"""Iteration 9 — close the remaining gaps.

  A. Shell via the RAW dispid Invoke — E021 said to try this before declaring
     an API absent, so test my own advice.
  B. Linear pattern: probe which pattern methods actually exist, then retry.
  C. Bolt circle TWO WAYS (Tier 3): 4 circles in ONE sketch + one cut, versus
     seed + circular pattern. Compare reliability and feature count — the
     pipeline currently sketches per-position, so this checks that choice.
  D. Tier 1 leftovers: variable-radius fillet, chamfer by distance+angle.
"""
from __future__ import annotations

import json
import math

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get, sw_invoke

lab = Lab("tier5_gaps")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<44} {detail}", flush=True)


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


def cut(name):
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)

    def _t(flip):
        return DOC.FeatureManager.FeatureCut4(
            True, False, flip, EC_THROUGH_ALL, EC_BLIND, 0.0, 0.01,
            False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)

    f = _t(True) or _t(False)
    if f is not None:
        f.Name = name
    return f


def cyl():
    out = []
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                s = sw_any(face, "GetSurface")
                if not s or not sw_get(s, "IsCylinder"):
                    continue
                p = list(sw_get(s, "CylinderParams"))
                out.append((round(p[0] / IN, 3), round(p[1] / IN, 3),
                            round(2 * p[6] / IN, 3)))
            except Exception:
                continue
    return out


def plate(w=6.0, h=6.0, t=0.375, name="plate"):
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, w * IN, h * IN, 0)
    return extrude(t, name)


# --------------------------------------------------------------------------- #
# A. Shell — does the raw dispid Invoke rescue it? (my own E021 advice)
# --------------------------------------------------------------------------- #
def a_shell_raw_invoke():
    fm = DOC.FeatureManager
    names = ["InsertFeatureShell", "InsertFeatureShell2", "InsertShell",
             "FeatureShell", "InsertFeatureShellByFace"]
    present = []
    for n in names:
        try:
            getattr(fm, n)
            present.append(n)
        except Exception:
            pass
    # try the raw dispid route for each candidate
    raw = {}
    for n in names:
        try:
            ole = fm._oleobj_
            ole.GetIDsOfNames(n)
            raw[n] = "dispid found"
        except Exception as e:
            raw[n] = f"{type(e).__name__}"
    record("9.A shell API discovery", bool(present) or "dispid found" in raw.values(),
           f"getattr-present={present}; dispids={raw}")
    return present, raw


# --------------------------------------------------------------------------- #
# B. Linear pattern — what pattern methods exist at all?
# --------------------------------------------------------------------------- #
def b_linear_pattern():
    fm = DOC.FeatureManager
    found = {}
    for n in ("FeatureLinearPattern", "FeatureLinearPattern2",
              "FeatureLinearPattern3", "FeatureLinearPattern4",
              "FeatureLinearPattern5", "FeatureCircularPattern5"):
        try:
            ole = fm._oleobj_
            ole.GetIDsOfNames(n)
            found[n] = "dispid found"
        except Exception:
            found[n] = "absent"
    record("9.B linear-pattern API discovery", True, json.dumps(found))

    # retry FeatureLinearPattern4 with an explicit EDGE direction reference
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(1.0 * IN, 1.0 * IN, 0, 0.125 * IN)
    seed = cut("lin_seed")
    before = len(cyl())
    # the direction reference must be an EDGE selected at Mark 1
    DOC.ClearSelection2(True)
    edge_ok = False
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    edge_ok = True
                    break
            except Exception:
                continue
        if edge_ok:
            break
    seed_ok = DOC.Extension.SelectByID2("lin_seed", "BODYFEATURE", 0, 0, 0,
                                        True, 4, _null_dispatch(), 0)
    f, err = None, ""
    try:
        f = DOC.FeatureManager.FeatureLinearPattern4(
            3, 1.5 * IN, 1, 0.0, False, False, "NULL", "NULL",
            False, False, False, False, False, False, True, True, False, False,
            False, False)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    after = len(cyl())
    record("9.B linear pattern retry (edge Mark 1 + seed Mark 4)",
           f is not None and after > before,
           f"edge={edge_ok} seed={bool(seed_ok)} faces {before}->{after} "
           f"feature={f is not None} {err}")
    if f is not None:
        try:
            f.Name = "lin_pat"
            lab.delete_feature(DOC, "lin_pat")
        except Exception:
            pass
    lab.delete_feature(DOC, "lin_seed")


# --------------------------------------------------------------------------- #
# C. Bolt circle TWO WAYS — the comparison the pipeline's design rests on
# --------------------------------------------------------------------------- #
def c_bolt_circle_two_ways():
    """4 holes on a Ø4.0 bolt circle, built two different ways."""
    bc_r, dia, n = 2.0, 0.3125, 4
    cx, cy = 3.0, 3.0
    pts = [(cx + bc_r * math.cos(2 * math.pi * i / n),
            cy + bc_r * math.sin(2 * math.pi * i / n)) for i in range(n)]

    # --- WAY 1: all four circles in ONE sketch, one cut -------------------- #
    before = len(cyl())
    lab.open_sketch(DOC, "Front Plane")
    for x, y in pts:
        DOC.SketchManager.CreateCircleByRadius(x * IN, y * IN, 0, dia / 2 * IN)
    f1 = cut("bolt_one_sketch")
    after1 = len(cyl())
    got1 = [c for c in cyl() if abs(c[2] - dia) < 0.01]
    ok1 = f1 is not None and len(got1) == n
    record("9.C way 1: 4 circles in ONE sketch + 1 cut", ok1,
           f"features=1, faces {before}->{after1}, holes at "
           f"{sorted((round(p[0], 2), round(p[1], 2)) for p in got1)}")

    # --- WAY 2: seed + circular pattern (needs a concentric axis) ---------- #
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(cx * IN, cy * IN, 0, 0.5 * IN)
    cut("centre_bore2")
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius((cx + bc_r) * IN, cy * IN, 0, dia / 2 * IN)
    seed2 = cut("bolt_seed2")

    axis_name = None
    for x, y, d in cyl():
        if abs(x - cx) < 0.01 and abs(y - cy) < 0.01 and abs(d - 1.0) < 0.01:
            for body in lab.bodies(DOC):
                for face in lab.faces_of(body):
                    try:
                        s = sw_any(face, "GetSurface")
                        if not s or not sw_get(s, "IsCylinder"):
                            continue
                        p = list(sw_get(s, "CylinderParams"))
                        if (abs(p[0] / IN - cx) < 0.01 and abs(p[1] / IN - cy) < 0.01
                                and abs(2 * p[6] / IN - 1.0) < 0.01):
                            DOC.ClearSelection2(True)
                            if face.Select4(False, _null_dispatch()) and DOC.InsertAxis2(True):
                                ax = None
                                feat = sw_get(DOC, "FirstFeature")
                                while feat is not None:
                                    try:
                                        if sw_get(feat, "GetTypeName2") == "RefAxis":
                                            ax = feat
                                    except Exception:
                                        pass
                                    feat = sw_get(feat, "GetNextFeature")
                                if ax is not None:
                                    axis_name = sw_get(ax, "Name")
                            break
                    except Exception:
                        continue
                if axis_name:
                    break
            break

    before2 = len(cyl())
    f2, err = None, ""
    if axis_name:
        DOC.ClearSelection2(True)
        DOC.Extension.SelectByID2(axis_name, "AXIS", 0, 0, 0, False, 1,
                                  _null_dispatch(), 0)
        DOC.Extension.SelectByID2("bolt_seed2", "BODYFEATURE", 0, 0, 0, True, 4,
                                  _null_dispatch(), 0)
        try:
            f2 = DOC.FeatureManager.FeatureCircularPattern5(
                n, 2 * math.pi / n, False, "NULL", False, True, False,
                False, False, False, 1, 2 * math.pi / n, "NULL", False)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
    after2 = len(cyl())
    ok2 = f2 is not None and after2 - before2 == n - 1
    record("9.C way 2: seed + circular pattern", ok2,
           f"features=4 (bore+seed+axis+pattern), axis={axis_name!r}, "
           f"faces {before2}->{after2} (+{after2 - before2}) {err}")

    LOG.append({"step": "9.C comparison", "ok": True, "detail": json.dumps({
        "one_sketch": {"features": 1, "holes": len(got1), "worked": ok1,
                       "prerequisites": "none"},
        "circular_pattern": {"features": 4, "gained": after2 - before2, "worked": ok2,
                             "prerequisites": "a concentric cylindrical face to "
                                              "derive the axis from"}})})
    lab.save(DOC, "t5_bolt_circle_two_ways.sldprt")


# --------------------------------------------------------------------------- #
# D. Tier 1 leftovers — variable-radius fillet, chamfer distance+angle
# --------------------------------------------------------------------------- #
def d_fillet_chamfer_variants():
    # chamfer by distance+angle on ONE edge (E020: never all edges)
    DOC.ClearSelection2(True)
    picked = None
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(False, _null_dispatch()):
                    picked = e
                    break
            except Exception:
                continue
        if picked:
            break
    before = lab.measure(DOC).get("volume_in3")
    f, err = None, ""
    try:
        f = DOC.FeatureManager.InsertFeatureChamfer(
            4, 1, 0.05 * IN, math.radians(45), 0, 0, 0, 0)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    after = lab.measure(DOC).get("volume_in3")
    record("9.D chamfer 0.05 x 45deg on ONE edge",
           f is not None and after is not None and after < (before or 0),
           f"vol {before} -> {after} {err}")
    if f is not None:
        try:
            f.Name = "cham1"
            lab.delete_feature(DOC, "cham1")
        except Exception:
            pass

    # single-edge fillet (contrast with the all-edges no-op of E020)
    DOC.ClearSelection2(True)
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(False, _null_dispatch()):
                    break
            except Exception:
                continue
        break
    before = lab.measure(DOC).get("volume_in3")
    f2, err2 = None, ""
    try:
        f2 = DOC.FeatureManager.FeatureFillet3(
            195, 0.0625 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception as e:
        err2 = f"{type(e).__name__}: {str(e)[:60]}"
    after = lab.measure(DOC).get("volume_in3")
    changed = after is not None and before is not None and abs(after - before) > 1e-6
    record("9.D fillet R.0625 on ONE edge (vs all-edges no-op)",
           f2 is not None and changed, f"vol {before} -> {after} {err2}")
    if f2 is not None:
        try:
            f2.Name = "fil1"
            lab.delete_feature(DOC, "fil1")
        except Exception:
            pass


if __name__ == "__main__":
    plate()
    a_shell_raw_invoke()
    b_linear_pattern()
    c_bolt_circle_two_ways()
    d_fillet_chamfer_variants()
    final = lab.measure(DOC)
    print(f"\nfinal: bbox {final.get('bbox_in')} vol {final.get('volume_in3')} "
          f"bodies {final.get('body_count')} clean {final.get('rebuild_clean')}")
    (RESULTS / "tier5_gaps.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"gaps: {sum(1 for r in LOG if r.get('ok'))}/{len(LOG)} OK")
    lab.close(DOC)
