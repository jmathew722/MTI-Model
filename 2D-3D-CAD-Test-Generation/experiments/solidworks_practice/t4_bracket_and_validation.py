"""Iteration 8 — Tier 4: a realistic multi-feature part, validated; plus the
two Tier-2 gaps (linear pattern, shell) retried with the corrected accessor.

The part is built in the plan order a real plan.json specifies: base -> boss ->
profile cut -> holes -> pattern -> fillets LAST. Then:
  * it is measured off the model (bbox, volume, body count, hole audit);
  * it is exported to STL and run through the REAL `pipeline.validation`
    scorecard — the first time that module has graded a part built here;
  * a deliberately WRONG variant (hole diameter doubled) is built and the hole
    audit is checked for whether it actually catches it.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

from swlab import IN, PARTS, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier4")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()

PLATE_W, PLATE_H, PLATE_T = 5.0, 3.0, 0.375
HOLE_DIA = 0.375
HOLES = [(0.625, 0.625), (4.375, 0.625), (0.625, 2.375), (4.375, 2.375)]


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<40} {detail}", flush=True)


def extrude(depth_in, name, plane_sketch_open=True):
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


def drill(name, x, y, dia):
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCircleByRadius(x * IN, y * IN, 0, dia / 2 * IN)
    return cut(name)


def cyl_faces():
    out = []
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsCylinder"):
                    continue
                p = list(sw_get(surf, "CylinderParams"))
                out.append({"x": round(p[0] / IN, 3), "y": round(p[1] / IN, 3),
                            "dia": round(2 * p[6] / IN, 3)})
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------- #
def build_bracket():
    """Base -> boss (on a REFERENCE PLANE) -> notch -> holes -> fillets last."""
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, PLATE_W * IN, PLATE_H * IN, 0)
    base = extrude(PLATE_T, "base_plate")
    record("8.1 base plate 5.0 x 3.0 x 0.375", base is not None,
           f"bbox {lab.measure(DOC).get('bbox_in')}")

    # --- Tier 3: reference plane, then sketch on it ------------------------ #
    DOC.ClearSelection2(True)
    sel = DOC.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0,
                                    _null_dispatch(), 0)
    rp, err = None, ""
    try:
        # InsertRefPlane(constraint1, d1, constraint2, d2, constraint3, d3)
        # swRefPlaneReferenceConstraint_Distance = 8 (resolved below)
        c_dist = _const("swRefPlaneReferenceConstraint_Distance", 8)
        rp = DOC.FeatureManager.InsertRefPlane(c_dist, PLATE_T * IN, 0, 0, 0, 0)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    plane_name = None
    if rp is not None:
        try:
            rp.Name = "plane_top"
            plane_name = "plane_top"
        except Exception:
            pass
    record("8.2 reference plane offset 0.375", plane_name is not None,
           f"selected_front={bool(sel)} name={plane_name!r} {err}")

    # --- boss on the reference plane --------------------------------------- #
    boss = None
    if plane_name:
        if lab.open_sketch(DOC, plane_name):
            DOC.SketchManager.CreateCornerRectangle(
                1.75 * IN, 0.75 * IN, 0, 3.25 * IN, 2.25 * IN, 0)
            boss = extrude(0.25, "raised_boss")
    m = lab.measure(DOC)
    record("8.3 boss sketched on the REFERENCE plane", boss is not None,
           f"bbox {m.get('bbox_in')} vol {m.get('volume_in3')} "
           f"bodies {m.get('body_count')}")

    # --- profile notch on the right edge ----------------------------------- #
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(
        (PLATE_W - 0.5 + 0.01) * IN, 1.0 * IN, 0, (PLATE_W + 0.01) * IN, 2.0 * IN, 0)
    notch = cut("edge_notch")
    record("8.4 right-edge notch (0.5 x 1.0)", notch is not None,
           f"vol {lab.measure(DOC).get('volume_in3')}")

    # --- four holes -------------------------------------------------------- #
    for i, (x, y) in enumerate(HOLES, 1):
        drill(f"hole_{i}", x, y, HOLE_DIA)
    holes = [h for h in cyl_faces() if abs(h["dia"] - HOLE_DIA) < 0.01]
    record("8.5 four mounting holes", len(holes) == 4,
           f"{len(holes)} face(s) at d{HOLE_DIA}: {[(h['x'], h['y']) for h in holes]}")

    # --- fillets LAST ------------------------------------------------------ #
    DOC.ClearSelection2(True)
    n = 0
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    before = lab.measure(DOC).get("volume_in3")
    fil, err = None, ""
    try:
        fil = DOC.FeatureManager.FeatureFillet3(
            195, 0.0625 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:50]}"
    after = lab.measure(DOC).get("volume_in3")
    record("8.6 fillet R.0625 (last)", fil is not None,
           f"{n} edges; vol {before} -> {after} {err}")
    return lab.measure(DOC)


def export_stl(path: Path) -> bool:
    try:
        errs, warns = 0, 0
        DOC.Extension.SaveAs3(str(path), 0, 1, None, None, errs, warns)
        return path.is_file()
    except Exception:
        try:
            DOC.SaveAs(str(path))
            return path.is_file()
        except Exception:
            return False


def validate_with_pipeline(measured):
    """Run the REAL pipeline.validation scorecard over this lab part."""
    from pipeline.validation import build_scorecard

    tmp = Path(tempfile.mkdtemp(prefix="lab_validate_"))
    stl = tmp / "BRACKET.STL"
    if not export_stl(stl):
        record("8.7 pipeline.validation scorecard", False, "STL export failed")
        return None
    # the minimum artifacts the scorecard reads
    (tmp / "BRACKET_extraction.json").write_text(json.dumps({
        "units": "inch", "projection_angle": "third_angle",
        "projection_angle_source": "title_block_symbol",
        "dimensions": [{"id": "D001", "value": PLATE_W, "applies_to": "overall_length"},
                       {"id": "D002", "value": PLATE_H, "applies_to": "overall_width"}],
    }), encoding="utf-8")
    card = build_scorecard(tmp, "BRACKET")
    layers = {x.name: x.status for x in card.layers}
    record("8.7 pipeline.validation scorecard", card.overall in ("PASS", "PASS_WITH_ASSUMPTIONS"),
           f"verdict={card.overall} solid={layers.get('solid_body')} "
           f"volume_ratio={layers.get('volume_ratio')}")
    for x in card.layers:
        if x.name in ("solid_body", "volume_ratio", "com_symmetry"):
            LOG.append({"step": f"   layer {x.name}", "ok": x.status != "FAIL",
                        "detail": x.detail[:130]})
            print(f"       {x.name}: {x.status} — {x.detail[:96]}")
    shutil.rmtree(tmp, ignore_errors=True)
    return card


def deliberately_wrong_hole():
    """Tier 4's requirement: prove the hole audit catches a 2x diameter."""
    wrong_dia = HOLE_DIA * 2
    drill("hole_WRONG", 2.5, 1.5, wrong_dia)
    found = cyl_faces()
    expected = {(x, y, HOLE_DIA) for x, y in HOLES}
    mismatched = [h for h in found
                  if abs(h["dia"] - HOLE_DIA) > 0.01 and h["dia"] > 0.1]
    caught = any(abs(h["dia"] - wrong_dia) < 0.01 for h in mismatched)
    record("8.8 hole audit catches a 2x diameter", caught,
           f"expected all d{HOLE_DIA}; found off-spec: "
           f"{[(h['x'], h['y'], h['dia']) for h in mismatched]}")
    lab.delete_feature(DOC, "hole_WRONG")


def retry_linear_pattern():
    drill("lin_seed", 1.0, 1.5, 0.25)
    before = len(cyl_faces())
    DOC.ClearSelection2(True)
    edge_sel = False
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    edge_sel = True
                    break
            except Exception:
                continue
        if edge_sel:
            break
    seed_sel = DOC.Extension.SelectByID2("lin_seed", "BODYFEATURE", 0, 0, 0,
                                         True, 4, _null_dispatch(), 0)
    f, err = None, ""
    try:
        f = DOC.FeatureManager.FeatureLinearPattern4(
            3, 1.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
            False, False, False, False, False, False, True, True, False, False,
            False, False)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    after = len(cyl_faces())
    record("8.9 linear pattern (retry)", f is not None and after > before,
           f"edge_sel={edge_sel} seed_sel={bool(seed_sel)} faces {before}->{after} {err}")
    if f is not None:
        try:
            f.Name = "lin_pat"
            lab.delete_feature(DOC, "lin_pat")
        except Exception:
            pass
    lab.delete_feature(DOC, "lin_seed")


def retry_shell():
    fm = DOC.FeatureManager
    have = [n for n in ("InsertFeatureShell", "InsertFeatureShell2") if hasattr(fm, n)]
    target = None
    for body in lab.bodies(DOC):
        best_area = -1
        for face in lab.faces_of(body):
            try:
                surf = sw_any(face, "GetSurface")
                if not surf or not sw_get(surf, "IsPlane"):
                    continue
                area = sw_get(face, "GetArea")
                if area > best_area:
                    best_area, target = area, face
            except Exception:
                continue
    err = ""
    f = None
    if target is not None:
        DOC.ClearSelection2(True)
        target.Select4(False, _null_dispatch())
        try:
            f = fm.InsertFeatureShell(0.0625 * IN, False)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
    record("8.10 shell (retry, largest planar face)", f is not None,
           f"members={have} target={'found' if target else 'none'} {err}")
    if f is not None:
        try:
            f.Name = "shell_try"
            lab.delete_feature(DOC, "shell_try")
        except Exception:
            pass


if __name__ == "__main__":
    measured = build_bracket()
    validate_with_pipeline(measured)
    deliberately_wrong_hole()
    retry_linear_pattern()
    retry_shell()
    lab.save(DOC, "t4_bracket.sldprt")
    final = lab.measure(DOC)
    print(f"\nbracket: bbox {final.get('bbox_in')} vol {final.get('volume_in3')} "
          f"bodies {final.get('body_count')} clean {final.get('rebuild_clean')}")
    (RESULTS / "tier4.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"tier4: {sum(1 for r in LOG if r.get('ok'))}/{len(LOG)} OK")
    lab.close(DOC)
