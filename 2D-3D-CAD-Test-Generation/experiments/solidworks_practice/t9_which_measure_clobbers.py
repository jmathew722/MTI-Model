"""Iteration 13 — WHICH measurement clears the selection, and is the pipeline hit?

Iteration 12 proved a measurement between "select" and "call" silently kills the
call. My lab measures with the DOCUMENT-level `IModelDoc2.GetMassProperties`.
The pipeline (`solidworks_builder._total_solid_volume`, `model_validator`)
measures with the BODY-level `IBody2.GetMassProperties(density)`.

If only the document-level call clobbers, the bug is confined to my harness and
the pipeline is clean. If the body-level call clobbers too, the pipeline has a
real latent defect wherever it measures mid-build. Assumption is not good
enough here - measured either way.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch, sw_any

lab = Lab("tier9_which")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
LOG = []


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<52} {detail}", flush=True)


def n_selected(doc):
    try:
        return int(sw_any(doc.SelectionManager, "GetSelectedObjectCount2", -1) or 0)
    except Exception:
        return -1


def plate(doc):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 4.0 * IN, 3.0 * IN, 0)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    return doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.5 * IN, 0.01,
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


def doc_level(doc):
    """What lab.measure uses: IModelDoc2.GetMassProperties."""
    return sw_any(doc, "GetMassProperties")


def body_level(doc):
    """What the PIPELINE uses: IBody2.GetMassProperties(density)."""
    out = []
    for b in lab.bodies(doc):
        out.append(b.GetMassProperties(0.001))
    return out


def main():
    doc = lab.new_part()
    plate(doc)

    for label, fn, is_pipeline in (
        ("IModelDoc2.GetMassProperties  (my lab harness)", doc_level, False),
        ("IBody2.GetMassProperties      (the PIPELINE)", body_level, True),
    ):
        before = all_edges(doc)
        err = ""
        try:
            fn(doc)
        except Exception as e:
            err = f" ({type(e).__name__}: {str(e)[:40]})"
        after = n_selected(doc)
        survived = after == before
        record(f"13 selection survives {label}", survived,
               f"selected {before} -> {after}{err}"
               + ("" if survived else "  <-- CLOBBERS")
               + ("   [pipeline uses this]" if is_pipeline else ""))

    # end-to-end confirmation with the pipeline's own measurement call
    d2 = lab.new_part()
    plate(d2)
    n = all_edges(d2)
    body_level(d2)                       # the pipeline's measurement, mid-selection
    f = None
    try:
        f = d2.FeatureManager.FeatureFillet3(
            195, 0.05 * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        pass
    vols = body_level(d2)
    v = round(vols[0][3] / (IN ** 3), 5) if vols else None
    record("13 end-to-end fillet after a BODY-level measure",
           f is not None,
           f"{n} edges; returned {'feature' if f else 'None'}; vol_in3={v}")
    lab.close(d2)

    (RESULTS / "tier9_which.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    lab.close(doc)


if __name__ == "__main__":
    main()
