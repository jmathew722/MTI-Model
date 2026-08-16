"""Iteration 14 — re-test everything the selection clobber may have faked.

Iteration 13 traced the cause: `lab.measure()` ends with `check_rebuild_errors`,
which calls `ForceRebuild3`, and a rebuild CLEARS the selection. Every lab step
that measured between selecting and calling therefore called the feature with an
EMPTY selection - and got `None` back for a reason that had nothing to do with
the geometry.

Two published findings were produced that way and are now suspect:

  E020  "selecting ALL edges makes FeatureFillet3 a silent no-op"
        (t4 line 155 select -> 159 measure -> 162 call)
  9.D   "single-edge fillet and chamfer on a holed plate change nothing"
        (t5 line 301 select -> 306 measure -> 309 call)

Both are re-run here on the SAME kind of part, with the measurement moved BEFORE
the selection. Whatever this says goes into the docs, including if it means
retracting a lesson I already wrote.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier10_retract")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<50} {detail}", flush=True)


def vol():
    return lab.measure(DOC).get("volume_in3")


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


def drill_many(points, dia, name):
    lab.open_sketch(DOC, "Front Plane")
    for x, y in points:
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


def straight_edges():
    """Every edge whose endpoints differ - i.e. not a full circle."""
    out = []
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                cp = sw_get(e, "GetCurveParams2")
                if cp is None:
                    continue
                v = list(cp)
                d = sum((v[i + 3] - v[i]) ** 2 for i in range(3)) ** 0.5
                if d > 1e-9:
                    out.append((e, d))
            except Exception:
                continue
    return out


def build_holed_plate():
    plate(5.0, 3.0, 0.375, "base")
    drill_many([(0.625, 0.625), (4.375, 0.625), (0.625, 2.375), (4.375, 2.375)],
               0.375, "holes")


# --------------------------------------------------------------------------- #
def a_all_edges_fillet_on_holed_plate():
    """The exact E020 claim, with the measurement moved BEFORE the selection."""
    before = vol()                                  # measure FIRST
    DOC.ClearSelection2(True)
    n = 0
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    f = None
    try:
        f = DOC.FeatureManager.FeatureFillet3(
            195, 0.0625 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        pass
    after = vol()
    ok = f is not None and after != before
    record("14.A E020 re-test: all-edges fillet R.0625", ok,
           f"{n} edges (incl. hole edges); vol {before} -> {after}; "
           f"returned {'feature' if f else 'None'}"
           + ("   => E020 RETRACTED" if ok else "   => E020 stands"))
    if f is not None:
        try:
            f.Name = "fil_all"
            lab.delete_feature(DOC, "fil_all")
        except Exception:
            pass
    return ok


def b_single_edge_fillet_and_chamfer():
    """The 9.D claim: one straight edge on a holed plate."""
    before = vol()                                  # measure FIRST, then collect:
    # a rebuild (which vol() forces) does not only clear the selection, it
    # DISCONNECTS previously-collected edge pointers - collecting before this
    # measurement raised "object invoked has disconnected from its clients".
    edges = straight_edges()
    if not edges:
        record("14.B single-edge fillet on a holed plate", False, "no straight edge found")
        return False
    longest = max(edges, key=lambda t: t[1])[0]

    DOC.ClearSelection2(True)
    sel = bool(longest.Select4(True, _null_dispatch()))
    f = None
    try:
        f = DOC.FeatureManager.FeatureFillet3(
            195, 0.0625 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        pass
    after = vol()
    ok_f = f is not None and after != before
    record("14.B 9.D re-test: SINGLE-edge fillet R.0625", ok_f,
           f"1 straight edge sel={sel}; vol {before} -> {after}; "
           f"returned {'feature' if f else 'None'}")

    before2 = vol()                                 # measure FIRST, then collect
    edges2 = straight_edges()
    target = max(edges2, key=lambda t: t[1])[0] if edges2 else None
    DOC.ClearSelection2(True)
    sel2 = bool(target.Select4(True, _null_dispatch())) if target is not None else False
    c = None
    try:
        c = DOC.FeatureManager.InsertFeatureChamfer(4, 1, 0.0625 * IN, 0.7853981, 0, 0, 0, 0)
    except Exception:
        pass
    after2 = vol()
    ok_c = c is not None and after2 != before2
    record("14.B 9.D re-test: SINGLE-edge chamfer .0625", ok_c,
           f"1 straight edge sel={sel2}; vol {before2} -> {after2}; "
           f"returned {'feature' if c else 'None'}")
    return ok_f and ok_c


if __name__ == "__main__":
    build_holed_plate()
    a = a_all_edges_fillet_on_holed_plate()
    b = b_single_edge_fillet_and_chamfer()
    lab.save(DOC, "t10_edge_treatments.sldprt")
    LOG.append({"step": "verdict", "ok": None, "detail":
                f"E020 {'RETRACTED (all-edges fillet works)' if a else 'stands'}; "
                f"single-edge treatments {'WORK' if b else 'still fail'}"})
    (RESULTS / "tier10_retract.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"\n{LOG[-1]['detail']}")
    lab.close(DOC)
