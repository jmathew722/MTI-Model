"""Iteration 11 — does a FAILED feature call poison the ones after it?

Iteration 10 produced a contradiction worth chasing: the identical all-edges
fillet (same helper, same `FeatureFillet3(195, r, ...)`) BUILT in Tier 1 and
returned `None` in Tier 6. The one difference was that Tier 6 had already made a
failed fillet call (an impossible 5.0 in radius) in the same document.

If a failed call leaves the document unable to accept the next one, that matters
far beyond fillets: the deferred-retry ladder retries in the SAME document, so
every retry after the first failure would be doomed for a reason that has
nothing to do with the retry strategy.

Three parts, one variable:
  A  clean part -> legal fillet                      (control)
  B  clean part -> impossible fillet -> legal fillet (the Tier-6 sequence)
  C  same as B, but ClearSelection2 + a rebuild between the two calls
     (if C recovers where B fails, the recovery step is the fix)
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch

lab = Lab("tier7_poison")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
LOG = []


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<44} {detail}", flush=True)


def fresh_plate(doc, w=4.0, h=3.0, t=0.5):
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


def fillet(doc, r_in):
    try:
        return doc.FeatureManager.FeatureFillet3(
            195, r_in * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        return None


def vol(doc):
    return lab.measure(doc).get("volume_in3")


# --------------------------------------------------------------------------- #
def a_control():
    doc = lab.new_part()
    fresh_plate(doc)
    before = vol(doc)
    n = all_edges(doc)
    f = fillet(doc, 0.05)
    after = vol(doc)
    record("11.A clean part -> legal R.05 (control)",
           f is not None and after != before,
           f"{n} edges; vol {before} -> {after}; returned {'feature' if f else 'None'}")
    lab.close(doc)
    return f is not None and after != before


def b_after_failure():
    doc = lab.new_part()
    fresh_plate(doc)
    all_edges(doc)
    bad = fillet(doc, 5.0)                      # impossible on a 0.5 plate
    before = vol(doc)
    n = all_edges(doc)
    f = fillet(doc, 0.05)                       # the SAME legal call as 11.A
    after = vol(doc)
    record("11.B after a failed fillet -> legal R.05",
           f is not None and after != before,
           f"bad returned {'feature' if bad else 'None'}; {n} edges; "
           f"vol {before} -> {after}; returned {'feature' if f else 'None'}")
    lab.save(doc, "t7_after_failure.sldprt")
    lab.close(doc)
    return f is not None and after != before


def c_with_recovery():
    doc = lab.new_part()
    fresh_plate(doc)
    all_edges(doc)
    fillet(doc, 5.0)                            # same failure as 11.B
    # the candidate recovery: drop the selection, force a rebuild, re-select
    steps = []
    try:
        doc.ClearSelection2(True)
        steps.append("ClearSelection2")
    except Exception as e:
        steps.append(f"ClearSelection2!{type(e).__name__}")
    try:
        doc.EditRebuild3()
        steps.append("EditRebuild3")
    except Exception as e:
        steps.append(f"EditRebuild3!{type(e).__name__}")
    before = vol(doc)
    n = all_edges(doc)
    f = fillet(doc, 0.05)
    after = vol(doc)
    record("11.C failed fillet -> recovery -> legal R.05",
           f is not None and after != before,
           f"recovery=[{', '.join(steps)}]; {n} edges; vol {before} -> {after}; "
           f"returned {'feature' if f else 'None'}")
    lab.save(doc, "t7_with_recovery.sldprt")
    lab.close(doc)
    return f is not None and after != before


if __name__ == "__main__":
    a = a_control()
    b = b_after_failure()
    c = c_with_recovery()
    verdict = (
        "inconclusive - the control itself failed, so the Tier-1/Tier-6 difference is NOT "
        "explained by a prior failed call" if not a else
        "NO poisoning - a failed call does not block the next one" if b else
        "POISONING CONFIRMED, and ClearSelection2+EditRebuild3 RECOVERS it" if c else
        "POISONING CONFIRMED and the recovery does NOT clear it"
    )
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": None, "detail": verdict})
    (RESULTS / "tier7_poison.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
