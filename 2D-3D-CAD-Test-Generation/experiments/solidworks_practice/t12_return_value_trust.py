"""Tier 6, iteration 16 — is the emitted macro's `Is Nothing` check sufficient?

The interactive fillet macro (`04_fillets_chamfers.vba`) is the one place where a
HUMAN owns the selection: the README says select the edges, then press F5. That
puts two MANUAL rules in direct conflict:

  rule 11  compare the volume before and after every fillet
  rule 14  collect, select and call with NOTHING in between (a rebuild clears
           the selection and disconnects held pointers)

The macro cannot obey rule 11 without breaking rule 14 - measuring first would
destroy the selection the human just made. So it obeys 14 and its only signal is
`If swFeatF003 Is Nothing`.

That is only safe if "returned a Feature" and "geometry actually changed" are the
same thing. E020 assumed they were NOT (it claimed a feature comes back having
done nothing) - but E020 was retracted, and it was retracted because the harness
had emptied the selection, which is a different situation. So the question is
still open and the emitted macro's correctness depends on the answer.

Sweeps radii from trivially fine to physically impossible on a 0.5 in plate and
checks the correspondence at every point.
"""
from __future__ import annotations

import json

from swlab import IN, RESULTS, Lab, _null_dispatch

lab = Lab("tier12_trust")
from pipeline.solidworks_builder import _const     # noqa: E402

EC_BLIND = _const("swEndCondBlind", 0)
EC_THROUGH_ALL = _const("swEndCondThroughAll", 1)
LOG = []
DOC = lab.new_part()

# 0.5 thick plate: 0.24 is near the limit, 0.30+ cannot work on a through edge
RADII = [0.01, 0.0625, 0.125, 0.2, 0.24, 0.26, 0.3, 0.5, 1.0, 5.0]


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<40} {detail}", flush=True)


def vol():
    return lab.measure(DOC).get("volume_in3")


def build_plate():
    lab.open_sketch(DOC, "Front Plane")
    DOC.SketchManager.CreateCornerRectangle(0, 0, 0, 4.0 * IN, 3.0 * IN, 0)
    DOC.SketchManager.AddToDB = False
    DOC.SketchManager.InsertSketch(True)
    f = DOC.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, 0.5 * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    if f is not None:
        f.Name = "base"


def select_all_edges():
    """Collect and select with nothing in between (rule 14)."""
    DOC.ClearSelection2(True)
    n = 0
    for body in lab.bodies(DOC):
        for e in lab.edges_of(body):
            try:
                if e.Select4(True, _null_dispatch()):
                    n += 1
            except Exception:
                continue
    return n


def main():
    build_plate()
    rows = []
    for r in RADII:
        before = vol()                       # measure FIRST, then select (rule 14)
        n = select_all_edges()
        f, raised = None, ""
        try:
            f = DOC.FeatureManager.FeatureFillet3(
                195, r * IN, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
        except Exception as e:
            raised = type(e).__name__
        after = vol()
        returned = f is not None
        changed = (before is not None and after is not None
                   and abs(after - before) > 1e-9)
        agree = returned == changed
        rows.append({"radius_in": r, "edges": n, "returned_feature": returned,
                     "volume_changed": changed, "agree": agree,
                     "before": before, "after": after, "raised": raised})
        record(f"16 R{r:<6} returned={str(returned):<5} changed={str(changed):<5}",
               agree,
               f"{n} edges; vol {before} -> {after}"
               + (f"; raised {raised}" if raised else "")
               + ("" if agree else "   <-- SIGNAL AND REALITY DISAGREE"))
        if returned:
            # Roll back so each radius is an INDEPENDENT test. The first run of
            # this sweep guessed the auto-name "Fillet1", the delete silently did
            # nothing, and every later radius ran against an already-filleted
            # plate (12 edges -> 48). Name it explicitly and verify the rollback
            # actually restored the volume - MANUAL rule 5, learned again.
            try:
                f.Name = f"fil_{str(r).replace('.', '_')}"
                lab.delete_feature(DOC, f.Name)
            except Exception:
                pass
            back = vol()
            if back is None or abs(back - before) > 1e-9:
                record(f"16 rollback after R{r}", False,
                       f"vol {after} -> {back}, expected {before}"
                       f"   <-- LATER RADII ARE NOT INDEPENDENT")

    disagreements = [r for r in rows if not r["agree"]]
    if not disagreements:
        verdict = (f"TRUSTWORTHY across {len(rows)} radii: a returned Feature always "
                   f"means the geometry changed, and None always means it did not. "
                   f"The emitted macro's `Is Nothing` check is sufficient, so rule 14 "
                   f"can win over rule 11 where a human owns the selection.")
    else:
        verdict = (f"NOT trustworthy: {len(disagreements)} of {len(rows)} radii "
                   f"disagree ({[d['radius_in'] for d in disagreements]}) - the "
                   f"emitted macro needs a real geometry check, not `Is Nothing`.")
    print(f"\nverdict: {verdict}")
    rows.append({"verdict": verdict})
    (RESULTS / "tier12_trust.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    lab.close(DOC)


if __name__ == "__main__":
    main()
