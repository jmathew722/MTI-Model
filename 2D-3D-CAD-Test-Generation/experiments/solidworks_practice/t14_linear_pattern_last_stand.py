"""Tier 7, iteration 19 — the last open capability gap: linear patterns.

State of the evidence. `FeatureLinearPattern4` returns None, raises nothing and
creates no geometry, with a VERIFIED 8.00 in straight edge selected at Mark 1 and
the seed feature at Mark 4 (iteration 10) - and that test is clean of the E022
selection clobber, so the failure is real. Circular patterns build correctly under
exactly the same Mark discipline. Dispids exist for FeatureLinearPattern through
FeatureLinearPattern5, so nothing is missing.

Two things have never been varied, and both are plausible:

  1. HOW the direction is selected. Every attempt so far used IEdge::Select4.
     SolidWorks' own recorder emits Extension.SelectByID2(..., "EDGE", ...), and
     E018 already showed that on this install the ACCESS FORM matters more than
     the API - GetSurface needed a raw dispid Invoke where getattr failed.
  2. WHICH overload. Only ...Pattern4 has been called. ...Pattern5 exists and
     takes a different argument list.

Sweeps both, and - the point of doing it at all - reports each combination
separately so a failure narrows the cause instead of restating it.
"""
from __future__ import annotations

import json

import pythoncom
from win32com.client import VARIANT

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier14_linpat")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<46} {detail}", flush=True)


def n_cyl(doc):
    n = 0
    for body in lab.bodies(doc):
        for face in lab.faces_of(body):
            try:
                s = sw_any(face, "GetSurface")
                if s and sw_get(s, "IsCylinder"):
                    n += 1
            except Exception:
                continue
    return n


def fresh_part():
    """8 x 3 x 0.375 plate with one 0.25 seed hole at (1.0, 1.5)."""
    doc = lab.new_part()
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 8.0 * IN, 3.0 * IN, 0)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    f = doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.375 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "lin_plate"

    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCircleByRadius(1.0 * IN, 1.5 * IN, 0, 0.125 * IN)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)

    def _t(flip):
        return doc.FeatureManager.FeatureCut4(
            True, False, flip, EC_THROUGH_ALL, EC_BLIND, 0.0, 0.01,
            False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)

    s = _t(True) or _t(False)
    if s is not None:
        s.Name = "lp_seed"
    return doc


def longest_edge(doc):
    best, best_len = None, -1.0
    for body in lab.bodies(doc):
        for e in lab.edges_of(body):
            try:
                cp = sw_get(e, "GetCurveParams2")
                if cp is None:
                    continue
                v = list(cp)
                d = sum((v[i + 3] - v[i]) ** 2 for i in range(3)) ** 0.5
                if d > best_len:
                    best, best_len = e, d
            except Exception:
                continue
    return best, best_len / IN if best_len > 0 else 0.0


def sel_via_select4(doc):
    """How every previous attempt selected the direction."""
    e, ln = longest_edge(doc)
    if e is None:
        return False, "no edge"
    try:
        return bool(e.Select4(True, _null_dispatch())), f"Select4 on a {ln:.2f}in edge"
    except Exception as ex:
        return False, f"Select4 raised {type(ex).__name__}"


def sel_via_selectbyid(doc):
    """What SolidWorks' own recorder emits: a coordinate hit on "EDGE"."""
    # midpoint of the long bottom edge of the plate, on the front face
    x, y, z = 4.0 * IN, 0.0, 0.375 * IN
    try:
        ok = doc.Extension.SelectByID2("", "EDGE", x, y, z, True, 1,
                                       _null_dispatch(), 0)
        return bool(ok), f"SelectByID2 EDGE at ({x / IN:.1f},{y / IN:.1f},{z / IN:.3f})"
    except Exception as ex:
        return False, f"SelectByID2 raised {type(ex).__name__}"


def sel_seed(doc):
    try:
        return bool(doc.Extension.SelectByID2("lp_seed", "BODYFEATURE", 0, 0, 0,
                                              True, 4, _null_dispatch(), 0))
    except Exception:
        return False


def call_pattern4(doc):
    return doc.FeatureManager.FeatureLinearPattern4(
        3, 2.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
        False, False, False, False, False, False, True, True, False, False,
        False, False)


def call_pattern5(doc):
    """...Pattern5 adds the direction-2 / geometry-pattern block."""
    null = VARIANT(pythoncom.VT_DISPATCH, None)
    return doc.FeatureManager.FeatureLinearPattern5(
        3, 2.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
        False, False, False, False, False, False, True, True,
        False, False, False, False, null)


def attempt(label, select_dir, call):
    doc = fresh_part()
    before = n_cyl(doc)
    doc.ClearSelection2(True)
    dir_ok, how = select_dir(doc)
    seed_ok = sel_seed(doc)
    f, err = None, ""
    try:
        f = call(doc)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:44]}"
    after = n_cyl(doc)
    built = f is not None and after > before
    record(f"19 {label}", built,
           f"{how}; dir={dir_ok} seed={seed_ok}; holes {before}->{after}; "
           f"returned={'feature' if f else 'None'}"
           + (f"; {err}" if err else ""))
    lab.close(doc)
    return built


def main():
    results = {
        "Pattern4 + Select4": attempt("Pattern4 + Select4 (the known failure)",
                                      sel_via_select4, call_pattern4),
        "Pattern4 + SelectByID2": attempt("Pattern4 + SelectByID2 EDGE (recorder form)",
                                          sel_via_selectbyid, call_pattern4),
        "Pattern5 + Select4": attempt("Pattern5 + Select4",
                                      sel_via_select4, call_pattern5),
        "Pattern5 + SelectByID2": attempt("Pattern5 + SelectByID2 EDGE",
                                          sel_via_selectbyid, call_pattern5),
    }
    wins = [k for k, v in results.items() if v]
    if wins:
        verdict = (f"LINEAR PATTERNS WORK via {wins[0]} - the gap was the "
                   f"{'selection form' if 'SelectByID2' in wins[0] else 'overload'}, "
                   f"not the API")
    else:
        verdict = ("linear patterns fail across BOTH overloads and BOTH selection "
                   "forms - the gap is real and is not about how the direction is "
                   "picked; circular patterns remain the working path")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": bool(wins), "detail": verdict})
    (RESULTS / "tier14_linpat.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
