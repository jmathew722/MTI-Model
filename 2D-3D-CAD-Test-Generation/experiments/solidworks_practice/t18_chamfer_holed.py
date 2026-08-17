"""Iteration 23 - all-edges chamfer on a plate WITH holes (the TEST3 shape).

Iteration 22 disproved the obvious hypothesis: an all-edges chamfer builds fine
on a clean 12-edge plate, so 'all edges' is NOT why the TEST3 chamfers failed.

What the failing parts have and the clean plate does not is HOLES - 26 edges on
4088-A (a key with a counterbore), 49 on 4079-D. Circular edges are the
candidate. Iteration 14 showed an all-edges FILLET works over hole edges, but
chamfer and fillet have already proved to differ, so it must be measured.
"""
from __future__ import annotations
import json
from swlab import IN, RESULTS, Lab, _null_dispatch

lab = Lab("tier18_holed")
from pipeline.solidworks_builder import _const  # noqa: E402
EC_BLIND = _const("swEndCondBlind", 0); EC_THRU = _const("swEndCondThroughAll", 1)
LOG = []

def record(s, ok, d=""):
    LOG.append({"step": s, "ok": ok, "detail": d})
    print(f"  [{'OK' if ok else 'XX'}] {s:<40} {d}", flush=True)

def build(doc, holes):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 4.0*IN, 3.0*IN, 0)
    doc.SketchManager.AddToDB = False; doc.SketchManager.InsertSketch(True)
    f = doc.FeatureManager.FeatureExtrusion3(True, False, False, EC_BLIND, EC_BLIND,
        0.5*IN, 0.01, False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None: f.Name = "plate"
    if not holes: return
    lab.open_sketch(doc, "Front Plane")
    for x, y in holes:
        doc.SketchManager.CreateCircleByRadius(x*IN, y*IN, 0, 0.1875*IN)
    doc.SketchManager.AddToDB = False; doc.SketchManager.InsertSketch(True)
    def _t(fl):
        return doc.FeatureManager.FeatureCut4(True, False, fl, EC_THRU, EC_BLIND, 0.0,
            0.01, False, False, False, False, 0, 0, False, False, False, False, False,
            True, True, True, True, False, 0, 0, False, False)
    h = _t(True) or _t(False)
    if h is not None: h.Name = "holes"

def trial(label, holes, size):
    doc = lab.new_part(); build(doc, holes)
    before = lab.measure(doc).get("volume_in3")     # measure FIRST (rule 14)
    doc.ClearSelection2(True); n = 0
    for b in lab.bodies(doc):
        for e in lab.edges_of(b):
            try:
                if e.Select4(True, _null_dispatch()): n += 1
            except Exception: pass
    c, err = None, ""
    try:
        c = doc.FeatureManager.InsertFeatureChamfer(4, 1, size*IN, 0.7853981633974483, 0,0,0,0)
    except Exception as e: err = type(e).__name__
    after = lab.measure(doc).get("volume_in3")
    built = c is not None and after != before
    record(label, built, f"{n} edges; {size}in; vol {before} -> {after}; "
                         f"returned={'feature' if c else 'None'}{'; '+err if err else ''}")
    lab.close(doc); return built

if __name__ == "__main__":
    clean = trial("23.A no holes, chamfer .06 (control)", [], 0.06)
    one   = trial("23.B ONE hole, chamfer .06", [(2.0, 1.5)], 0.06)
    four  = trial("23.C FOUR holes, chamfer .06", [(0.6,0.6),(3.4,0.6),(0.6,2.4),(3.4,2.4)], 0.06)
    v = ("holes are the cause: an all-edges chamfer stops working once the part has a "
         "circular edge" if clean and not (one and four) else
         "holes are NOT the cause - a holed plate chamfers fine, so the TEST3 failures "
         "come from something else in those parts" if clean and one and four else
         "inconclusive: even the control failed")
    print(f"\nverdict: {v}")
    LOG.append({"step":"verdict","ok":None,"detail":v})
    (RESULTS/"tier18_holed.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
