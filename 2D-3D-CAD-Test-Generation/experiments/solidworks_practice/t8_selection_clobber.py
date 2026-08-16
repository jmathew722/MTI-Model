"""Iteration 12 — what silently CLEARS a selection between select and call?

Iteration 11 ruled out the obvious explanation (a failed call poisoning the next
one). One difference remains between the Tier-1 fillet that BUILT and the
Tier-6 fillet that returned None with the identical call:

    Tier 1 / t7:  measure -> select edges -> FeatureFillet3      BUILT
    Tier 6:       select edges -> measure -> FeatureFillet3      None

i.e. Tier 6 measured the volume BETWEEN selecting and calling. If
`GetMassProperties` clears the selection, that is a general hazard: every
"select, sanity-check, then build" sequence an agent would naturally write is
broken, and it fails silently because the feature call just returns None.

This measures the selection count directly at each step, so the answer does not
depend on whether a feature happens to build.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any

lab = Lab("tier8_clobber")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
LOG = []


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<50} {detail}", flush=True)


def n_selected(doc):
    """How many objects are selected right now."""
    try:
        return int(sw_any(doc.SelectionManager, "GetSelectedObjectCount2", -1) or 0)
    except Exception:
        try:
            return int(sw_any(doc.SelectionManager, "GetSelectedObjectCount") or 0)
        except Exception:
            return -1


def plate(doc, w=4.0, h=3.0, t=0.5):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, w * IN, h * IN, 0)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    return doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, t * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)


def all_edges(doc):
    doc.ClearSelection2(True)
    n = 0
    for body in lab.bodies(doc):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    return n


PROBES = [
    ("GetMassProperties (lab.measure)", lambda d: lab.measure(d)),
    ("GetBodies2 / body enumeration", lambda d: lab.bodies(d)),
    ("read doc.Extension.Something", lambda d: getattr(d, "Extension", None)),
    ("FeatureManager.GetFeatureCount", lambda d: sw_any(d.FeatureManager, "GetFeatureCount")),
]


def main():
    doc = lab.new_part()
    plate(doc)

    for label, probe in PROBES:
        n_before = all_edges(doc)
        try:
            probe(doc)
            err = ""
        except Exception as e:
            err = f" ({type(e).__name__})"
        n_after = n_selected(doc)
        survived = n_after == n_before
        record(f"12 selection survives: {label}", survived,
               f"selected {n_before} -> {n_after}{err}"
               + ("" if survived else "  <-- CLOBBERS THE SELECTION"))

    # the decisive end-to-end pair, since the count API could itself be lying
    for label, measure_between in (("no measure between", False), ("measure between", True)):
        d2 = lab.new_part()
        plate(d2)
        before_vol = lab.measure(d2).get("volume_in3")
        n = all_edges(d2)
        if measure_between:
            lab.measure(d2)
        f = None
        try:
            f = d2.FeatureManager.FeatureFillet3(
                195, 0.05 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
        except Exception:
            pass
        after_vol = lab.measure(d2).get("volume_in3")
        built = f is not None and after_vol != before_vol
        record(f"12 end-to-end fillet, {label}", built,
               f"{n} edges; vol {before_vol} -> {after_vol}; "
               f"returned {'feature' if f else 'None'}")
        lab.close(d2)

    (RESULTS / "tier8_clobber.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    lab.close(doc)


if __name__ == "__main__":
    main()
