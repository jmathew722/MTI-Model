"""Iteration 24 — can Stage 10.6 SEE a blind counterbore?

The decisive experiment for E028. Both TEST3 keys report `F002 MISSING` while the
builder logged PASS, and the builder's PASS is trustworthy (it already measures
volume either side of every cut and raises when nothing was removed). Two
explanations survive, and they produce identical symptoms:

  (i)  the hole is cut in the wrong place
  (ii) `feature_verify` cannot detect a BLIND hole from an STL, and is reporting
       a false MISSING on a hole that is really there

4086-A's hole is blind (`through: false`, 0.38 deep). If (ii) is true then the
verification stage wired in yesterday has a false positive, and my claim that
"every part is worse than the envelope said" is overstated.

Builds ONE plate carrying BOTH kinds of hole at known positions, exports the STL,
and asks feature_verify about each. A through hole classified OK next to a blind
hole classified MISSING is (ii), proven.
"""
from __future__ import annotations
import json
from pathlib import Path
from swlab import IN, RESULTS, Lab, _null_dispatch

lab = Lab("tier19_blind")
from pipeline.solidworks_builder import _const          # noqa: E402
from pipeline.feature_verify import verify_features     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0); EC_THRU = _const("swEndCondThroughAll", 1)
W, H, T = 4.0, 3.0, 1.0
THRU_XY, BLIND_XY = (1.0, 1.5), (3.0, 1.5)
DIA, BLIND_DEPTH = 0.5, 0.38


def cut(doc, x, y, dia, through, depth=0.0):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCircleByRadius(x * IN, y * IN, 0, dia / 2 * IN)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    end, d = (EC_THRU, 0.0) if through else (EC_BLIND, depth * IN)

    def _t(flip):
        return doc.FeatureManager.FeatureCut4(
            True, False, flip, end, EC_BLIND, d, 0.01,
            False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)
    return _t(True) or _t(False)


def main():
    out = Path(RESULTS) / "t19_part"; out.mkdir(parents=True, exist_ok=True)
    doc = lab.new_part()
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, W * IN, H * IN, 0)
    doc.SketchManager.AddToDB = False; doc.SketchManager.InsertSketch(True)
    base = doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, T * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if base is not None: base.Name = "F001"
    v0 = lab.measure(doc).get("volume_in3")
    f_thru = cut(doc, *THRU_XY, DIA, True)
    v1 = lab.measure(doc).get("volume_in3")
    f_blind = cut(doc, *BLIND_XY, DIA, False, BLIND_DEPTH)
    v2 = lab.measure(doc).get("volume_in3")
    print(f"  through cut: {'built' if f_thru else 'None'}  vol {v0} -> {v1}", flush=True)
    print(f"  blind   cut: {'built' if f_blind else 'None'}  vol {v1} -> {v2}", flush=True)
    if v2 is None or v1 is None or v2 >= v1:
        print("\nverdict: the BLIND cut removed no material - cannot test detection"); return

    stl = out / "P.STL"
    doc.SaveAs3(str(stl), 0, 2) if hasattr(doc, "SaveAs3") else doc.SaveAs(str(stl))
    lab.close(doc)
    if not stl.is_file():
        print("\nverdict: STL export failed - cannot test detection"); return

    plan = {"part": "P", "units": "inch", "steps": [
        {"seq": 1, "feature_id": "F001", "type": "extrude_boss",
         "dimensions_drawing_units": {"length": W, "width": H, "depth": T}},
        {"seq": 2, "feature_id": "F_THRU", "type": "hole",
         "dimensions_drawing_units": {"diameter": DIA, "qty": 1.0},
         "positions_xy": [list(THRU_XY)]},
        {"seq": 3, "feature_id": "F_BLIND", "type": "hole",
         "dimensions_drawing_units": {"diameter": DIA, "qty": 1.0, "depth": BLIND_DEPTH},
         "positions_xy": [list(BLIND_XY)]},
    ]}
    rep = verify_features(stl, plan, out, part="P", write=False)
    got = {f.get("feature_id"): f.get("classification") for f in rep.get("features", [])}
    print("  audit:", got, "| extras:", len(rep.get("extras") or []), flush=True)
    thru_ok, blind_ok = got.get("F_THRU") == "OK", got.get("F_BLIND") == "OK"
    if thru_ok and not blind_ok:
        v = ("CONFIRMED (ii): feature_verify sees the THROUGH hole and calls the BLIND "
             "hole MISSING though it is measurably there. Stage 10.6 has a FALSE "
             "POSITIVE on blind holes; 4086-A's MISSING is not evidence of a bad model.")
    elif thru_ok and blind_ok:
        v = ("both detected - Stage 10.6 handles blind holes, so the TEST3 MISSING "
             "results stand and the holes really are absent or mispositioned (i)")
    else:
        v = f"inconclusive - the THROUGH hole itself was not detected ({got})"
    print(f"\nverdict: {v}")
    (Path(RESULTS) / "tier19_blind.json").write_text(json.dumps(
        {"volumes": [v0, v1, v2], "audit": got, "verdict": v}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
