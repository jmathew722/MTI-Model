"""Tier 8, iteration 22 — does an ALL-EDGES chamfer fail where a fillet succeeds?

Driven by real drawings, not curiosity. The TEST3 batch produced three chamfer
failures in its first four parts, and every one of them used scope 'all edges':

    4079-D  F005  1.76 in   49 edges  -> None
    4079-D  F006  0.12 in   49 edges  -> None
    4088-A  F003  0.06 in   26 edges  -> None   <-- on a 0.500 thick key

That last one matters. 0.06 on a 0.5 plate is nowhere near the E024 limit
(R < t/2 = 0.25), so "the chamfer is too big" does not explain it. Something
about the all-edges CASE is the problem.

E020 once claimed exactly this about fillets and was RETRACTED - the all-edges
fillet works fine, and the apparent failure was my harness clearing the
selection. So the honest question is whether chamfers genuinely differ from
fillets here, or whether this is E020 all over again.

Tests, on one clean 4 x 3 x 0.5 plate, measuring volume before/after with the
rebuild BEFORE the selection every time (rule 14):

  A  all-edges FILLET  0.06   - the known-good control
  B  all-edges CHAMFER 0.06   - what the pipeline does, and what fails
  C  edge-by-edge CHAMFER 0.06 - the candidate fix, counting which edges took it

If C succeeds where B fails, build_chamfer can convert a total failure into a
partial success, which is the guiding principle in one line: a complete
approximate model beats an incomplete one.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch

lab = Lab("tier17_chamfer_all")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
LOG = []
SIZE = 0.06


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<44} {detail}", flush=True)


def plate(doc):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 4.0 * IN, 3.0 * IN, 0)
    doc.SketchManager.AddToDB = False
    doc.SketchManager.InsertSketch(True)
    f = doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.5 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "plate"


def vol(doc):
    return lab.measure(doc).get("volume_in3")


def select_all_edges(doc):
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


def a_all_edges_fillet():
    doc = lab.new_part()
    plate(doc)
    before = vol(doc)                       # measure FIRST (rule 14)
    n = select_all_edges(doc)
    f = None
    try:
        f = doc.FeatureManager.FeatureFillet3(
            195, SIZE * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
    except Exception:
        pass
    after = vol(doc)
    built = f is not None and after != before
    record(f"22.A all-edges FILLET R{SIZE} (control)", built,
           f"{n} edges; vol {before} -> {after}; returned={'feature' if f else 'None'}")
    lab.close(doc)
    return built


def b_all_edges_chamfer():
    doc = lab.new_part()
    plate(doc)
    before = vol(doc)
    n = select_all_edges(doc)
    c, err = None, ""
    try:
        c = doc.FeatureManager.InsertFeatureChamfer(4, 1, SIZE * IN, 0.7853981633974483,
                                                    0, 0, 0, 0)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:40]}"
    after = vol(doc)
    built = c is not None and after != before
    record(f"22.B all-edges CHAMFER {SIZE} (the pipeline)", built,
           f"{n} edges; vol {before} -> {after}; returned={'feature' if c else 'None'}"
           + (f"; {err}" if err else ""))
    lab.close(doc)
    return built


def c_edge_by_edge_chamfer():
    doc = lab.new_part()
    plate(doc)
    applied, skipped = 0, 0
    total_before = vol(doc)
    # Re-enumerate edges every pass: each successful chamfer rebuilds the model,
    # which disconnects any edge pointers held from before it (E022).
    index = 0
    while True:
        before = vol(doc)                    # measure FIRST, then collect+select
        edges = []
        for body in lab.bodies(doc):
            edges.extend(lab.edges_of(body))
        if index >= len(edges):
            break
        doc.ClearSelection2(True)
        try:
            ok = bool(edges[index].Select4(True, _null_dispatch()))
        except Exception:
            ok = False
        if not ok:
            index += 1
            continue
        c = None
        try:
            c = doc.FeatureManager.InsertFeatureChamfer(4, 1, SIZE * IN,
                                                        0.7853981633974483, 0, 0, 0, 0)
        except Exception:
            c = None
        after = vol(doc)
        if c is not None and after != before:
            applied += 1
        else:
            skipped += 1
        index += 1
    total_after = vol(doc)
    built = applied > 0
    record(f"22.C EDGE-BY-EDGE chamfer {SIZE} (candidate fix)", built,
           f"{applied} edge(s) took it, {skipped} skipped; "
           f"vol {total_before} -> {total_after}")
    lab.save(doc, "t17_edge_by_edge_chamfer.sldprt")
    lab.close(doc)
    return built, applied, skipped


if __name__ == "__main__":
    a = a_all_edges_fillet()
    b = b_all_edges_chamfer()
    c, applied, skipped = c_edge_by_edge_chamfer()
    if b:
        verdict = ("all-edges CHAMFER works here - the TEST3 failures are NOT explained "
                   "by the all-edges scope and need another cause")
    elif c:
        verdict = (f"CONFIRMED: all-edges chamfer returns None while edge-by-edge applies "
                   f"{applied} of {applied + skipped} edges. Chamfers genuinely differ "
                   f"from fillets (E020 retracted for fillets does NOT carry over). "
                   f"build_chamfer should fall back to edge-by-edge.")
    else:
        verdict = ("neither all-edges nor edge-by-edge chamfer built - the problem is "
                   "the chamfer call itself on this geometry, not the scope")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": bool(c and not b), "detail": verdict})
    (RESULTS / "tier17_chamfer_all.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
