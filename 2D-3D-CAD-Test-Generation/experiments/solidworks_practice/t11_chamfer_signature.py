"""Iteration 15 — find a chamfer call that actually builds.

Iteration 14 isolated a real failure from the harness noise: on one straight
edge of a holed plate, `FeatureFillet3` BUILDS and

    InsertFeatureChamfer(4, 1, distance, angle, 0, 0, 0, 0)

returns None. Same edge, same selection, same moment - so the edge is fine and
the chamfer CALL is wrong.

That exact call is what `pipeline/solidworks_builder.build_chamfer` uses, so
this is not a lab curiosity. The pipeline raises SolidWorksError on the None
(it fails loudly, it does not ship a wrong part), but every chamfer on this
install would be failing.

Sweeps (Options, ChamferType) against the documented enums and reports the first
combination that changes the volume. Each attempt gets a fresh edge selection
because the previous attempt's measurement rebuilds the model.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_get

lab = Lab("tier11_chamfer")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()

# swChamferType_e as documented; the pipeline currently passes ChamferType=1
TYPES = {
    1: "AngleDistance",
    2: "DistanceDistance",
    3: "Vertex",
    4: "EqualDistance",
    5: "OffsetFace",
    6: "FaceFace",
}
# swFeatureChamferOption_e is a bitmask; 1 = tangent propagation
OPTIONS = {0: "none", 1: "TangentPropagation", 4: "(what the pipeline passes)"}


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else '..'}] {step:<44} {detail}", flush=True)


def vol():
    return lab.measure(DOC).get("volume_in3")


def build_part():
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, 5.0 * IN, 3.0 * IN, 0)
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.375 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "base"


def select_longest_straight_edge():
    """Collect AFTER any rebuild, select, and return immediately - no measuring."""
    best, best_len = None, -1.0
    for body in lab.bodies(DOC):
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
    DOC.ClearSelection2(True)
    if best is None:
        return False
    try:
        return bool(best.Select4(True, _null_dispatch()))
    except Exception:
        return False


def attempt(opts, ctype, dist=0.0625, angle=0.7853981633974483):
    before = vol()                       # measure FIRST (rebuild), then select
    if not select_longest_straight_edge():
        return False, "no edge selected"
    f, err = None, ""
    try:
        f = DOC.FeatureManager.InsertFeatureChamfer(
            opts, ctype, dist * IN, angle, 0, 0, 0, 0)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:38]}"
    after = vol()
    built = f is not None and after is not None and before is not None and \
        abs(after - before) > 1e-6
    detail = f"vol {before} -> {after}; returned {'feature' if f else 'None'}"
    if err:
        detail += f"; {err}"
    if built:                            # keep the model clean for the next try
        try:
            lab.delete_feature(DOC, "Chamfer1")
        except Exception:
            pass
    return built, detail


def main():
    build_part()
    winner = None
    for opts, oname in OPTIONS.items():
        for ctype, tname in TYPES.items():
            built, detail = attempt(opts, ctype)
            record(f"15 chamfer opts={opts} type={ctype} {tname}", built, detail)
            if built and winner is None:
                winner = (opts, ctype, tname, oname)
                break
        if winner:
            break

    if winner:
        o, t, tn, on = winner
        verdict = (f"WORKS with Options={o} ({on}), ChamferType={t} ({tn}). "
                   f"The pipeline passes Options=4, ChamferType=1.")
    else:
        verdict = ("no (Options, ChamferType) combination built a chamfer on this "
                   "install - the failure is NOT the pipeline's argument choice")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": bool(winner), "detail": verdict})
    (RESULTS / "tier11_chamfer.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    lab.save(DOC, "t11_chamfer.sldprt")
    lab.close(DOC)


if __name__ == "__main__":
    main()
