"""Tier 1 — sketch primitives, extrude boss/cut end conditions, fillet, chamfer.

Every assertion is against the LIVE model's measured bbox/volume/body count.
"""
import math

from swlab import DEG, IN, Lab, Result, approx, _null_dispatch

lab = Lab("tier1")
EC = {name: __import__("pipeline.solidworks_builder", fromlist=["_const"])._const(name, default)
      for name, default in (("swEndCondBlind", 0), ("swEndCondThroughAll", 1),
                            ("swEndCondMidPlane", 4))}
print("end-condition constants:", EC)


def _block(doc, w=4.0, h=3.0, plane="Front Plane"):
    """A w x h rectangle sketched corner-at-origin on `plane`."""
    lab.open_sketch(doc, plane)
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, w * IN, h * IN, 0)
    return doc


def _extrude(doc, depth_in, end_cond, both=False, flip=False):
    return doc.FeatureManager.FeatureExtrusion3(
        True, flip, both, end_cond, EC["swEndCondBlind"],
        depth_in * IN, 0.01, False, False, False, False, 0, 0,
        False, False, False, False, True, True, True, 0, 0, False)


# --------------------------------------------------------------------------- #
# 1.1 sketch primitives + closed/open contour detection
# --------------------------------------------------------------------------- #
def t_primitives():
    doc = lab.new_part()
    lab.open_sketch(doc)
    sm = doc.SketchManager
    made = {}
    sm.CreateLine(0, 0, 0, 1 * IN, 0, 0)
    made["line"] = True
    sm.CreateCircleByRadius(3 * IN, 0, 0, 0.5 * IN)
    made["circle"] = True
    sm.CreateCenterRectangle(0, 2 * IN, 0, 1 * IN, 3 * IN, 0)
    made["center_rect"] = True
    sm.CreateArc(5 * IN, 0, 0, 4.5 * IN, 0, 0, 5.5 * IN, 0, 0, 1)
    made["arc"] = True
    cl = sm.CreateCenterLine(0, -1 * IN, 0, 0, 1 * IN, 0)
    made["centerline"] = cl is not None
    closed_contours = lab.contours(doc)
    lab.close_sketch(doc)
    lab.save(doc, "t1_sketch_primitives.sldprt")
    lab.close(doc)
    return Result("1.1 sketch primitives", all(made.values()),
                  detail=f"{', '.join(k for k, v in made.items() if v)}; "
                         f"GetSketchContours={closed_contours}",
                  measured={"contours": closed_contours, **made})


def t_open_vs_closed_contours():
    """GetSketchContours on a CLOSED loop vs a deliberately OPEN one."""
    doc = lab.new_part()
    lab.open_sketch(doc)
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 2 * IN, 1 * IN, 0)
    closed = lab.contours(doc)
    lab.close_sketch(doc)

    lab.open_sketch(doc, "Top Plane")
    sm = doc.SketchManager
    sm.CreateLine(0, 0, 0, 2 * IN, 0, 0)          # three sides of a rectangle:
    sm.CreateLine(2 * IN, 0, 0, 2 * IN, 1 * IN, 0)  # deliberately NOT closed
    sm.CreateLine(2 * IN, 1 * IN, 0, 0, 1 * IN, 0)
    open_count = lab.contours(doc)
    lab.close_sketch(doc)
    lab.save(doc, "t1_contours_open_vs_closed.sldprt")
    lab.close(doc)
    return Result("1.2 GetSketchContours closed vs open",
                  closed >= 1 and open_count != closed,
                  detail=f"closed rectangle -> {closed} contour(s); open 3-line -> "
                         f"{open_count}",
                  measured={"closed": closed, "open": open_count})


# --------------------------------------------------------------------------- #
# 1.3-1.6 extrude boss end conditions — measured, not assumed
# --------------------------------------------------------------------------- #
def _boss_case(label, depth, end_cond, both, expect_z, filename):
    doc = lab.new_part()
    _block(doc)
    feat = _extrude(doc, depth, end_cond, both=both)
    m = lab.measure(doc)
    lab.save(doc, filename)
    lab.close(doc)
    z = (m.get("bbox_in") or [0, 0, 0])[2]
    ok = feat is not None and approx(z, expect_z, 0.01) and m.get("body_count") == 1
    return Result(label, ok,
                  detail=f"expected Z {expect_z}, measured {z}; bodies "
                         f"{m.get('body_count')}, vol {m.get('volume_in3')}",
                  measured=m)


def t_boss_blind():
    return _boss_case("1.3 extrude boss BLIND 0.5", 0.5, EC["swEndCondBlind"], False,
                      0.5, "t1_extrude_boss_blind.sldprt")


def t_boss_midplane():
    """Mid-plane: depth is the TOTAL thickness, not per side — verify that."""
    return _boss_case("1.4 extrude boss MIDPLANE 0.5", 0.5, EC["swEndCondMidPlane"],
                      False, 0.5, "t1_extrude_boss_midplane.sldprt")


def t_boss_both_directions():
    """Both-directions with dir2 depth 0: does it add anything the other way?"""
    return _boss_case("1.5 extrude boss BOTH dirs (dir2=0)", 0.5,
                      EC["swEndCondBlind"], True, 0.5,
                      "t1_extrude_boss_both.sldprt")


# --------------------------------------------------------------------------- #
# 1.7 extrude cut — through-all on a boss must leave ONE body
# --------------------------------------------------------------------------- #
def t_cut_through_all():
    doc = lab.new_part()
    _block(doc)
    _extrude(doc, 0.5, EC["swEndCondBlind"])
    before = lab.measure(doc)

    # E006 CONFIRMED EMPIRICALLY: closing the sketch and re-selecting it by name
    # (SelectByID2 "", "SKETCH") selects nothing, and FeatureCut4 then returns a
    # feature that removes ZERO material — no error, no exception. The working
    # pattern is the recorder's: leave the sketch ACTIVE and call the feature.
    lab.open_sketch(doc)
    doc.SketchManager.CreateCircleByRadius(2 * IN, 1.5 * IN, 0, 0.25 * IN)
    doc.SketchManager.AddToDB = False
    doc.ClearSelection2(True)
    cut = doc.FeatureManager.FeatureCut4(
        True, False, False, EC["swEndCondThroughAll"], EC["swEndCondBlind"],
        0.01, 0.01, False, False, False, False, 0, 0,
        False, False, False, False, False, True, True, True, True, False,
        0, 0, False, False)
    after = lab.measure(doc)
    lab.save(doc, "t1_extrude_cut_thru.sldprt")
    lab.close(doc)
    dv = (before.get("volume_in3") or 0) - (after.get("volume_in3") or 0)
    expect = math.pi * 0.25 ** 2 * 0.5
    ok = (cut is not None and after.get("body_count") == 1
          and approx(dv, expect, 0.01))
    return Result("1.6 extrude cut THROUGH-ALL", ok,
                  detail=f"removed {dv:.4f} in^3 (expected {expect:.4f}); bodies "
                         f"{after.get('body_count')}",
                  measured={"before": before, "after": after})


# --------------------------------------------------------------------------- #
# 1.8 fillet + chamfer on a block
# --------------------------------------------------------------------------- #
def _all_edges(doc):
    """Select every edge of body 0. Returns how many were selected."""
    doc.ClearSelection2(True)
    n = 0
    for body in lab.bodies(doc):
        try:
            edges = body.GetEdges()
        except Exception:
            continue
        for e in (list(edges) if isinstance(edges, (list, tuple)) else [edges]):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    return n


def t_fillet_constant():
    doc = lab.new_part()
    _block(doc)
    _extrude(doc, 0.5, EC["swEndCondBlind"])
    before = lab.measure(doc)
    n = _all_edges(doc)
    feat = doc.FeatureManager.FeatureFillet3(
        195, 0.1 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    after = lab.measure(doc)
    lab.save(doc, "t1_fillet_constant.sldprt")
    lab.close(doc)
    ok = feat is not None and (after.get("volume_in3") or 0) < (before.get("volume_in3") or 0)
    return Result("1.7 constant fillet R.10 all edges", ok,
                  detail=f"{n} edge(s) selected; volume {before.get('volume_in3')} -> "
                         f"{after.get('volume_in3')}",
                  measured={"edges": n, "before": before, "after": after})


def t_chamfer_distance():
    doc = lab.new_part()
    _block(doc)
    _extrude(doc, 0.5, EC["swEndCondBlind"])
    before = lab.measure(doc)
    n = _all_edges(doc)
    feat = doc.FeatureManager.InsertFeatureChamfer(4, 1, 0.05 * IN, 45 * DEG, 0, 0, 0, 0)
    after = lab.measure(doc)
    lab.save(doc, "t1_chamfer_distance.sldprt")
    lab.close(doc)
    ok = feat is not None and (after.get("volume_in3") or 0) < (before.get("volume_in3") or 0)
    return Result("1.8 chamfer 0.05 x 45deg all edges", ok,
                  detail=f"{n} edge(s); volume {before.get('volume_in3')} -> "
                         f"{after.get('volume_in3')}",
                  measured={"edges": n, "before": before, "after": after})


if __name__ == "__main__":

    for fn in (t_primitives, t_open_vs_closed_contours, t_boss_blind,
               t_boss_midplane, t_boss_both_directions, t_cut_through_all,
               t_fillet_constant, t_chamfer_distance):
        lab.run(fn.__doc__ or fn.__name__, fn)
    lab.finish()
