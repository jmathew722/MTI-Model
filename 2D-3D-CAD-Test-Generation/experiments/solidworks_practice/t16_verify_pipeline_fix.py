"""Tier 7, iteration 21 — does the PIPELINE's linear-pattern fix actually work?

Iteration 19 proved a recipe. This runs the pipeline's own implementation of it -
`solidworks_builder._select_pattern_direction_edge` - against a live part, because
"the recipe works" and "the code that encodes the recipe works" are different
claims and only the second one ships.

The function derives the direction-edge point from the PLAN (envelope midpoint,
top face) rather than from the model, so the test builds a part that matches a
plan and checks that the derived point actually lands on an edge.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("tier16_pipefix")
from pipeline.macro_generator import _envelope, _model_thickness      # noqa: E402
from pipeline.schema import DrawingData, Feature, FeatureType         # noqa: E402
from pipeline.solidworks_builder import (                             # noqa: E402
    _const,
    _select_pattern_direction_edge,
)

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []

LENGTH, WIDTH, THICK = 8.0, 3.0, 0.375


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<48} {detail}", flush=True)


def plan() -> DrawingData:
    """The plan the part is built from — same numbers, independently stated."""
    return DrawingData(
        units="inch", confidence=0.9,
        dimensions=[
            {"id": "D1", "type": "linear", "value": LENGTH, "unit": "inch",
             "applies_to": "length"},
            {"id": "D2", "type": "linear", "value": WIDTH, "unit": "inch",
             "applies_to": "width"},
            {"id": "D3", "type": "linear", "value": THICK, "unit": "inch",
             "applies_to": "thickness"},
        ],
        features=[Feature(id="F1", type=FeatureType.EXTRUDE_BOSS, description="plate",
                          related_dimensions=["D1", "D2", "D3"])],
        build_order=["F1"],
    )


def build_part(doc):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, LENGTH * IN, WIDTH * IN, 0)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    f = doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, THICK * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "plate"
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
        s.Name = "seed_hole"


def n_holes(doc):
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


def main():
    model = plan()
    length, _w = _envelope(model)
    record("21 plan reports the envelope the part was built to",
           abs(length - LENGTH) < 1e-9 and abs(_model_thickness(model) - THICK) < 1e-9,
           f"length={length}, thickness={_model_thickness(model)}")

    doc = lab.new_part()
    build_part(doc)
    before = n_holes(doc)

    # exactly what build_pattern now does, in the same order
    doc.ClearSelection2(True)
    dir_ok, note = _select_pattern_direction_edge(doc, model, None)
    record("21 _select_pattern_direction_edge finds an edge", dir_ok, note)

    seed_ok = bool(doc.Extension.SelectByID2("seed_hole", "BODYFEATURE", 0, 0, 0,
                                             True, 4, _null_dispatch(), 0))
    feat = None
    try:
        feat = doc.FeatureManager.FeatureLinearPattern4(
            3, 2.0 * IN, 1, 0.0, False, False, "NULL", "NULL",
            False, False, False, False, False, False, True, True, False, False,
            False, False)
    except Exception as e:
        record("21 FeatureLinearPattern4 raised", False, f"{type(e).__name__}: {e}")
    after = n_holes(doc)
    built = feat is not None and after > before
    record("21 pattern builds through the pipeline's own selector", built,
           f"seed_sel={seed_ok}; holes {before}->{after}; "
           f"returned={'feature' if feat else 'None'}")

    lab.save(doc, "t16_pipeline_linear_pattern.sldprt")
    verdict = ("the PIPELINE's implementation builds a linear pattern - the fix is "
               "verified in the code that ships, not only in the lab recipe"
               if built and dir_ok else
               f"pipeline implementation did NOT build (direction_ok={dir_ok}, "
               f"note={note!r}) - the fix is not proven and must not be claimed")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": bool(built and dir_ok), "detail": verdict})
    (RESULTS / "tier16_pipefix.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    lab.close(doc)


if __name__ == "__main__":
    main()
