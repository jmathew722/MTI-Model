"""Tier 7, iteration 20 — is it SelectByID2, or is it the Mark?

Iteration 19 got linear patterns building for the first time:

    FeatureLinearPattern4 + IEdge::Select4         -> None, nothing built
    FeatureLinearPattern4 + SelectByID2 "EDGE"     -> BUILT, holes 1 -> 3

Both selections reported success (`dir=True`), so "use SelectByID2" is a recipe
that works without saying why - and a recipe cannot be generalised. There is an
obvious candidate mechanism: **`IEntity::Select4(Append, Callout)` has no Mark
parameter.** It selects at Mark 0. A pattern's direction reference must be at
Mark 1, which `SelectByID2` can set and `Select4` cannot express at all.

If that is the mechanism then `IEntity::Select2(Append, Mark)` - same object,
same access route as the failing call, but able to carry a Mark - must ALSO
build the pattern when passed Mark 1, and must FAIL at Mark 0.

That is a real prediction that can be wrong, which is the point of making it.
"""
from __future__ import annotations

import json

from swlab import RESULTS, Lab, _null_dispatch
from t14_linear_pattern_last_stand import (
    call_pattern4,
    fresh_part,
    longest_edge,
    n_cyl,
    sel_seed,
)

lab = Lab("tier15_mark")
LOG = []


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<48} {detail}", flush=True)


def attempt(label, selector, expect_built):
    doc = fresh_part()
    before = n_cyl(doc)
    doc.ClearSelection2(True)
    dir_ok, how = selector(doc)
    seed_ok = sel_seed(doc)
    f = None
    try:
        f = call_pattern4(doc)
    except Exception:
        pass
    after = n_cyl(doc)
    built = f is not None and after > before
    as_predicted = built == expect_built
    record(f"20 {label}", as_predicted,
           f"{how}; dir={dir_ok} seed={seed_ok}; holes {before}->{after}; "
           f"{'BUILT' if built else 'nothing'} "
           f"({'as predicted' if as_predicted else 'PREDICTION WRONG'})")
    lab.close(doc)
    return built


def sel2(mark):
    def _sel(doc):
        e, ln = longest_edge(doc)
        if e is None:
            return False, "no edge"
        try:
            return bool(e.Select2(True, mark)), f"IEntity::Select2(mark={mark}) on {ln:.2f}in"
        except Exception as ex:
            return False, f"Select2(mark={mark}) raised {type(ex).__name__}"
    return _sel


def sel4(doc):
    e, ln = longest_edge(doc)
    if e is None:
        return False, "no edge"
    try:
        return bool(e.Select4(True, _null_dispatch())), f"IEdge::Select4 on {ln:.2f}in (no Mark arg)"
    except Exception as ex:
        return False, f"Select4 raised {type(ex).__name__}"


def main():
    # the prediction: Mark is what matters, not which select method
    m1 = attempt("Select2(append, Mark=1)  -> should BUILD", sel2(1), True)
    m0 = attempt("Select2(append, Mark=0)  -> should FAIL", sel2(0), False)
    s4 = attempt("Select4 (Mark 0 implied) -> should FAIL", sel4, False)

    if m1 and not m0 and not s4:
        verdict = ("CONFIRMED: the Mark is the mechanism. The same IEdge selected "
                   "with Select2 builds the pattern at Mark 1 and fails at Mark 0, "
                   "and Select4 - which has no Mark parameter and therefore cannot "
                   "express Mark 1 - always fails. SelectByID2 worked only because "
                   "it can set the Mark.")
    elif m1:
        verdict = ("PARTLY confirmed: Mark 1 builds, but the Mark-0 controls did not "
                   "behave as predicted - see the rows above")
    else:
        verdict = ("PREDICTION WRONG: Select2 at Mark 1 does not build, so the Mark "
                   "is not the whole mechanism; SelectByID2 remains the only known "
                   "working route and the reason is still unexplained")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": bool(m1 and not m0 and not s4), "detail": verdict})
    (RESULTS / "tier15_mark.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
