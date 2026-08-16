"""Tier 2 — revolve, patterns (and the Mark trap), mirror, shell.

Continues in the SAME part file (`practice_master.sldprt`). Structural
experiments use add -> measure -> DELETE so the one part hosts them all without
each one corrupting the next.

The Mark argument is the headline: reference doc 06 calls wrong Marks "suspect
#1" for a silently-missing pattern. This measures what actually happens.
"""
from __future__ import annotations

import json
import math

from swlab import IN, RESULTS, Lab, Result, _null_dispatch, sw_get, sw_seq

lab = Lab("tier2")
from pipeline.solidworks_builder import _const      # noqa: E402  (needs the tlb)

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
DOC = None
LOG: list[dict] = []


def record(step, ok, detail="", measured=None):
    LOG.append({"step": step, "ok": ok, "detail": detail, "measured": measured or {}})
    print(f"  [{'OK ' if ok else 'XX '}] {step:<40} {detail}", flush=True)


def open_master():
    """Reopen the ONE practice part; rebuild its block if it is not there."""
    global DOC
    DOC = lab.new_part()
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, 6 * IN, 4 * IN, 0)
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.5 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "base_block"
    return lab.measure(DOC)


def cut_active(name):
    """The verified cut recipe (MANUAL rule 4)."""
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


# --------------------------------------------------------------------------- #
# 2.1 Revolve — valid 360, valid partial, and the two documented failures
# --------------------------------------------------------------------------- #
def _revolve_profile(cross_axis=False, two_centerlines=False):
    """Half-profile + centerline, sketched clear of the block (x from 8in)."""
    lab.open_sketch(DOC, "Front Plane")
    sm = DOC.SketchManager
    sm.CreateCenterLine(8 * IN, 0, 0, 8 * IN, 3 * IN, 0)
    if two_centerlines:
        sm.CreateCenterLine(9 * IN, 0, 0, 9 * IN, 3 * IN, 0)   # ambiguous axis
    x_left = (7.5 if cross_axis else 8.0) * IN                 # crossing the axis
    sm.CreateLine(x_left, 0.5 * IN, 0, 9.0 * IN, 0.5 * IN, 0)
    sm.CreateLine(9.0 * IN, 0.5 * IN, 0, 9.0 * IN, 2.0 * IN, 0)
    sm.CreateLine(9.0 * IN, 2.0 * IN, 0, x_left, 2.0 * IN, 0)
    sm.CreateLine(x_left, 2.0 * IN, 0, x_left, 0.5 * IN, 0)
    sm.AddToDB = False
    sm.InsertSketch(True)


def _revolve(angle_deg):
    return DOC.FeatureManager.FeatureRevolve2(
        True, True, False, False, False, False, EC_BLIND, 0,
        math.radians(angle_deg), 0.0, False, False, 0, 0, 0, 0, 0,
        True, True, True)


def t_revolve_360():
    before = lab.measure(DOC)
    _revolve_profile()
    feat = _revolve(360)
    after = lab.measure(DOC)
    if feat is not None:
        feat.Name = "rev_360"
    gained = round((after.get("volume_in3") or 0) - (before.get("volume_in3") or 0), 3)
    ok = feat is not None and gained > 0
    record("2.1 revolve 360deg", ok,
           f"volume +{gained} in^3, bodies {after.get('body_count')}", after)
    lab.delete_feature(DOC, "rev_360")
    return ok


def t_revolve_partial():
    before = lab.measure(DOC)
    _revolve_profile()
    feat = _revolve(270)
    after = lab.measure(DOC)
    if feat is not None:
        feat.Name = "rev_270"
    gained = round((after.get("volume_in3") or 0) - (before.get("volume_in3") or 0), 3)
    ok = feat is not None and gained > 0
    record("2.2 revolve 270deg (partial)", ok,
           f"volume +{gained} in^3 (a 360 of this profile adds more)", after)
    lab.delete_feature(DOC, "rev_270")
    return ok


def t_revolve_crossing_axis():
    """Deliberate failure: profile crosses the revolve axis."""
    _revolve_profile(cross_axis=True)
    err = ""
    try:
        feat = _revolve(360)
    except Exception as e:
        feat, err = None, f"{type(e).__name__}: {str(e)[:90]}"
    record("2.3 revolve CROSSING the axis (deliberate)", feat is None,
           f"returned {feat!r}; {err or 'no exception raised'}")
    if feat is not None:
        try:
            feat.Name = "rev_bad_cross"
            lab.delete_feature(DOC, "rev_bad_cross")
        except Exception:
            pass
    DOC.ClearSelection2(True)
    return feat is None


def t_revolve_two_centerlines():
    """Deliberate failure: two centerlines = ambiguous axis."""
    _revolve_profile(two_centerlines=True)
    err = ""
    try:
        feat = _revolve(360)
    except Exception as e:
        feat, err = None, f"{type(e).__name__}: {str(e)[:90]}"
    record("2.4 revolve TWO centerlines (deliberate)", feat is None,
           f"returned {feat!r}; {err or 'no exception raised'}")
    if feat is not None:
        try:
            feat.Name = "rev_bad_two"
            lab.delete_feature(DOC, "rev_bad_two")
        except Exception:
            pass
    DOC.ClearSelection2(True)
    return feat is None


# --------------------------------------------------------------------------- #
# 2.5-2.7 Patterns — and the Mark trap
# --------------------------------------------------------------------------- #
def _count_holes():
    n = 0
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                from swlab import sw_any

                surf = sw_any(face, "GetSurface")
                if surf and sw_get(surf, "IsCylinder"):
                    n += 1
            except Exception:
                continue
    return n


def t_linear_pattern():
    drill("seed_lin", 1.0, 2.0, 0.25)
    before = _count_holes()
    DOC.ClearSelection2(True)
    # A linear pattern needs a DIRECTION entity (Mark 1) as well as the feature
    # (Mark 4) — omitting it is what "Parameter not optional" was complaining
    # about. Use a bottom edge of the block as the X direction.
    edges = lab.edges_of(lab.bodies(DOC)[0]) if lab.bodies(DOC) else []
    dir_sel = False
    for e in edges:
        try:
            if e.Select4(True, _null_dispatch()):
                dir_sel = True
                break
        except Exception:
            continue
    ok_sel = DOC.Extension.SelectByID2("seed_lin", "BODYFEATURE", 0, 0, 0,
                                       True, 4, _null_dispatch(), 0)
    feat = None
    err = ""
    try:
        feat = DOC.FeatureManager.FeatureLinearPattern4(
            4, 1.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
            False, False, False, False, False, False, True, True, False, False,
            False, False)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:80]}"
    after = _count_holes()
    if feat is not None:
        feat.Name = "lin_pattern"
    record("2.5 linear pattern (Mark 4, no direction ref)",
           feat is not None and after > before,
           f"selected={ok_sel}; cyl faces {before} -> {after}; {err}")
    lab.delete_feature(DOC, "lin_pattern")
    lab.delete_feature(DOC, "seed_lin")
    return feat is not None


def t_circular_pattern_marks():
    """Correct Marks (axis=1, feature=4) vs a deliberately WRONG Mark."""
    drill("seed_circ", 4.5, 2.0, 0.25)
    # InsertAxis2 lives on IModelDoc2 (NOT FeatureManager) and needs a
    # CYLINDRICAL FACE selected first — and Select4's 2nd arg is VT_DISPATCH,
    # so plain None fails the whole call (E013 again).
    from swlab import sw_any

    axis_name = ""
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsCylinder"):
                    continue
                cp = list(sw_get(surf, "CylinderParams"))
                if abs(cp[0] / IN - 4.5) > 0.01 or abs(cp[1] / IN - 2.0) > 0.01:
                    continue
                DOC.ClearSelection2(True)
                if face.Select4(False, _null_dispatch()) and DOC.InsertAxis2(True):
                    axis_name = "Axis1"
                break
            except Exception:
                continue
        if axis_name:
            break

    results = {}
    for label, feat_mark in (("correct Mark 4", 4), ("WRONG Mark 1", 1)):
        before = _count_holes()
        DOC.ClearSelection2(True)
        sel_axis = DOC.Extension.SelectByID2(axis_name or "Axis1", "AXIS", 0, 0, 0,
                                             False, 1, _null_dispatch(), 0)
        sel_feat = DOC.Extension.SelectByID2("seed_circ", "BODYFEATURE", 0, 0, 0,
                                             True, feat_mark, _null_dispatch(), 0)
        feat, err = None, ""
        try:
            # 14 args, from the installed sldworks.tlb (dispid 261): Number,
            # Spacing, FlipDirection, DName, GeometryPattern, EqualSpacing,
            # VaryInstance, SyncSubAssemblies, BDir2, BSymmetric, Number2,
            # Spacing2, DName2, EqualSpacing2.
            feat = DOC.FeatureManager.FeatureCircularPattern5(
                4, 2 * math.pi / 4, False, "NULL", False, True, False,
                False, False, False, 1, 2 * math.pi / 4, "NULL", False)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:70]}"
        after = _count_holes()
        results[label] = {"axis_selected": bool(sel_axis), "feat_selected": bool(sel_feat),
                          "feature": feat is not None, "faces_before": before,
                          "faces_after": after, "error": err}
        if feat is not None:
            try:
                feat.Name = f"circ_{feat_mark}"
                lab.delete_feature(DOC, f"circ_{feat_mark}")
            except Exception:
                pass
        record(f"2.6 circular pattern — {label}", feat is not None and after > before,
               f"axis_sel={bool(sel_axis)} feat_sel={bool(sel_feat)} "
               f"faces {before}->{after} {err}")
    lab.delete_feature(DOC, "seed_circ")
    LOG.append({"step": "2.6 mark comparison", "ok": True, "measured": results})
    return results


def t_mirror():
    drill("seed_mir", 1.5, 1.0, 0.3)
    before = _count_holes()
    DOC.ClearSelection2(True)
    sel_plane = DOC.Extension.SelectByID2("Right Plane", "PLANE", 0, 0, 0,
                                          False, 2, _null_dispatch(), 0)
    sel_feat = DOC.Extension.SelectByID2("seed_mir", "BODYFEATURE", 0, 0, 0,
                                         True, 1, _null_dispatch(), 0)
    feat, err = None, ""
    try:
        feat = DOC.FeatureManager.InsertMirrorFeature2(
            False, False, False, False, 0)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:80]}"
    after = _count_holes()
    if feat is not None:
        feat.Name = "mirror_hole"
    record("2.7 mirror about Right Plane", feat is not None,
           f"plane_sel={bool(sel_plane)} feat_sel={bool(sel_feat)} "
           f"faces {before}->{after} {err}")
    lab.delete_feature(DOC, "mirror_hole")
    lab.delete_feature(DOC, "seed_mir")
    return feat is not None


# --------------------------------------------------------------------------- #
# 2.8 Shell
# --------------------------------------------------------------------------- #
def t_shell():
    before = lab.measure(DOC)
    # pick the +Z face (the top of the plate) by largest area at max z
    target = None
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                from swlab import sw_any

                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsPlane"):
                    continue
                params = list(sw_get(surf, "PlaneParams"))
                # normal ~ +Z and the plane sits at the top of the plate
                if abs(params[2]) > 0.9 and abs(params[5] - 0.5 * IN) < 1e-4:
                    target = face
                    break
            except Exception:
                continue
        if target:
            break
    if target is None:
        record("2.8 shell (1 face removed)", False, "no +Z planar face found")
        return False
    DOC.ClearSelection2(True)
    target.Select4(False, _null_dispatch())   # E013: not None
    feat, err = None, ""
    try:
        feat = DOC.FeatureManager.InsertFeatureShell(0.1 * IN, False)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:80]}"
    after = lab.measure(DOC)
    if feat is not None:
        feat.Name = "shell_1face"
    removed = round((before.get("volume_in3") or 0) - (after.get("volume_in3") or 0), 3)
    record("2.8 shell 0.10 wall, 1 face removed", feat is not None and removed > 0,
           f"volume {before.get('volume_in3')} -> {after.get('volume_in3')} "
           f"(removed {removed}) {err}", after)
    lab.delete_feature(DOC, "shell_1face")
    return feat is not None


if __name__ == "__main__":
    base = open_master()
    print(f"master block: bbox {base.get('bbox_in')} vol {base.get('volume_in3')}\n")
    for fn in (t_revolve_360, t_revolve_partial, t_revolve_crossing_axis,
               t_revolve_two_centerlines, t_linear_pattern,
               t_circular_pattern_marks, t_mirror, t_shell):
        try:
            fn()
        except Exception as e:
            record(fn.__name__, False, f"UNCAUGHT {type(e).__name__}: {str(e)[:90]}")
    final = lab.measure(DOC)
    print(f"\nmaster after tier 2: bbox {final.get('bbox_in')} vol {final.get('volume_in3')} "
          f"bodies {final.get('body_count')} clean {final.get('rebuild_clean')}")
    lab.save(DOC, "practice_master.sldprt")
    (RESULTS / "tier2.json").write_text(json.dumps(LOG, indent=2, default=str),
                                        encoding="utf-8")
    ok = sum(1 for r in LOG if r.get("ok"))
    print(f"tier2: {ok}/{len(LOG)} recorded OK")
    lab.close(DOC)
