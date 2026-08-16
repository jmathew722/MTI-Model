"""Iteration 10 — precision pass on two claims I made, plus a last linear-pattern try.

  A. E020 precision: does an all-edges fillet return None (a detectable failure)
     or a feature that silently changed nothing? My lesson said "silent no-op";
     the distinction decides whether the pipeline already handles it.
  B. Linear pattern on a CLEAN plate (no holes), so the direction edge is
     guaranteed to be a straight edge rather than a hole's circular edge.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier6_final")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<46} {detail}", flush=True)


def plate(w, h, t, name):
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, w * IN, h * IN, 0)
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, t * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = name
    return f


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

    f = _t(True) or _t(False)
    if f is not None:
        f.Name = name
    return f


def n_cyl():
    n = 0
    for body in lab.bodies(DOC):
        for face in lab.faces_of(body):
            try:
                s = sw_any(face, "GetSurface")
                if s and sw_get(s, "IsCylinder"):
                    n += 1
            except Exception:
                continue
    return n


# --------------------------------------------------------------------------- #
def a_fillet_return_precision():
    """Is the all-edges fillet failure DETECTABLE (None) or truly silent?"""
    plate(4.0, 3.0, 0.5, "fil_plate")
    # a radius far too large for a 0.5-thick plate, on every edge
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
    f, err = None, ""
    try:
        f = DOC.FeatureManager.FeatureFillet3(
            195, 5.0 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:50]}"
    after = lab.measure(DOC).get("volume_in3")
    returned = "None" if f is None else "a feature object"
    record("10.A oversized all-edges fillet: what comes back?",
           f is None,
           f"{n} edges; returned {returned}; vol {before} -> {after}; {err or 'no exception'}")

    # and a legal radius on all edges, for contrast
    DOC.ClearSelection2(True)
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                e.Select4(True, _null_dispatch())
            except Exception:
                continue
    before2 = lab.measure(DOC).get("volume_in3")
    f2 = None
    try:
        f2 = DOC.FeatureManager.FeatureFillet3(
            195, 0.05 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        pass
    after2 = lab.measure(DOC).get("volume_in3")
    record("10.A legal all-edges fillet R.05 on a clean plate",
           f2 is not None and after2 != before2,
           f"returned {'a feature' if f2 else 'None'}; vol {before2} -> {after2}")
    if f2 is not None:
        try:
            f2.Name = "fil_ok"
            lab.delete_feature(DOC, "fil_ok")
        except Exception:
            pass
    lab.delete_feature(DOC, "fil_plate")


def b_linear_pattern_clean_plate():
    """Linear pattern where the direction edge is guaranteed STRAIGHT."""
    plate(8.0, 3.0, 0.375, "lin_plate")
    drill("lp_seed", 1.0, 1.5, 0.25)
    before = n_cyl()

    # pick the LONGEST edge (a straight 8.0 in edge of the plate) as direction
    best, best_len = None, -1.0
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                cp = sw_get(e, "GetCurveParams2")
                if cp is None:
                    continue
                v = list(cp)
                dx, dy, dz = v[3] - v[0], v[4] - v[1], v[5] - v[2]
                ln = (dx * dx + dy * dy + dz * dz) ** 0.5
                if ln > best_len:
                    best, best_len = e, ln
            except Exception:
                continue
    dir_ok = False
    DOC.ClearSelection2(True)
    if best is not None:
        try:
            dir_ok = bool(best.Select4(True, _null_dispatch()))
        except Exception:
            dir_ok = False
    seed_ok = DOC.Extension.SelectByID2("lp_seed", "BODYFEATURE", 0, 0, 0, True, 4,
                                        _null_dispatch(), 0)
    f, err = None, ""
    try:
        f = DOC.FeatureManager.FeatureLinearPattern4(
            3, 2.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
            False, False, False, False, False, False, True, True, False, False,
            False, False)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    after = n_cyl()
    record("10.B linear pattern, longest straight edge as direction",
           f is not None and after > before,
           f"edge_len={best_len / IN:.2f}in dir_sel={dir_ok} seed_sel={bool(seed_ok)} "
           f"faces {before}->{after} returned={'feature' if f else 'None'} {err}")
    lab.save(DOC, "t6_linear_pattern_try.sldprt")


if __name__ == "__main__":
    a_fillet_return_precision()
    b_linear_pattern_clean_plate()
    (RESULTS / "tier6_final.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"\nfinal gaps: {sum(1 for r in LOG if r.get('ok'))}/{len(LOG)} OK")
    lab.close(DOC)
