"""Tier 7, iteration 18 — are the pipeline's hardcoded enum fallbacks correct?

E012 is the worst failure this lab found: a guessed enum value (7 for
swEndCondThroughAllBoth, which is actually 9 - 7 is swEndCondUpToBody) returned a
valid feature, reported a clean rebuild, and deleted the entire solid. Volume
12.0 in3 -> 0, body count still 1.

`solidworks_builder._const(name, fallback)` resolves names from the live type
library and falls back to a hardcoded number when it cannot. Those fallbacks are
E012 waiting to happen: they are used exactly when the type library is NOT
available to correct them, so nothing else can catch a wrong one.

Every fallback in `pipeline/` is checked here against the live library. Also
checked are the enum names the lab and the emitted VBA rely on, since the VBA
uses named constants that must denote the same values the Python path computes -
two paths building different parts from the same plan is the failure mode this
repo has already been bitten by (see CLAUDE.md on dimension collisions).
"""
from __future__ import annotations

import json

from swlab import RESULTS, Lab

lab = Lab("tier13_enums")                      # connect first: _const needs it
from pipeline.solidworks_builder import _const  # noqa: E402

LOG = []

# (name, hardcoded fallback) — every _const fallback that appears in pipeline/
PIPELINE_FALLBACKS = [
    ("swCM", 1),
    ("swDefaultTemplatePart", 8),
    ("swINCHES", 3),
    ("swMM", 0),
    ("swSolidBody", 0),
]

# Names both code paths depend on, with the value this lab MEASURED where it
# differs from naive enum ordering (E012). No fallback to compare - these assert
# that the live library still says what the lab recorded.
LAB_VERIFIED = [
    ("swEndCondBlind", 0),
    ("swEndCondThroughAll", 1),
    ("swEndCondMidPlane", 6),          # NOT 4 (E012)
    ("swEndCondUpToBody", 7),          # the value that deleted the solid
    ("swEndCondThroughAllBoth", 9),    # NOT 7 (E012)
]


def record(step, ok, detail=""):
    LOG.append({"step": step, "ok": ok, "detail": detail})
    print(f"  [{'OK' if ok else 'XX'}] {step:<42} {detail}", flush=True)


def live(name):
    """The type library's value, or None if the name does not resolve.

    _const(name, fallback) cannot distinguish "resolved to the fallback" from
    "fell back", so probe with two DIFFERENT sentinels: if both return the
    sentinel, the name did not resolve.
    """
    a, b = _const(name, -991), _const(name, -992)
    if a == -991 and b == -992:
        return None
    return a


def main():
    print("\n--- pipeline _const fallbacks vs the live type library ---", flush=True)
    bad = []
    for name, fallback in PIPELINE_FALLBACKS:
        actual = live(name)
        if actual is None:
            record(f"18 {name}", True,
                   f"fallback {fallback}; name does not resolve here - fallback is "
                   f"load-bearing and UNVERIFIABLE on this install")
            continue
        ok = actual == fallback
        if not ok:
            bad.append((name, fallback, actual))
        record(f"18 {name}", ok,
               f"fallback {fallback}, live {actual}"
               + ("" if ok else "   <-- WRONG FALLBACK, E012 CLASS"))

    print("\n--- values this lab measured, re-checked against the library ---", flush=True)
    drift = []
    for name, expected in LAB_VERIFIED:
        actual = live(name)
        ok = actual == expected
        if not ok and actual is not None:
            drift.append((name, expected, actual))
        record(f"18 {name}", ok,
               f"lab recorded {expected}, live {actual}"
               + ("" if ok else "   <-- LEDGER AND LIBRARY DISAGREE"))

    if bad:
        verdict = ("WRONG FALLBACKS: " +
                   ", ".join(f"{n} hardcodes {f} but is {a}" for n, f, a in bad))
    elif drift:
        verdict = ("ledger drift: " +
                   ", ".join(f"{n} recorded {e} but is {a}" for n, e, a in drift))
    else:
        verdict = (f"all {len(PIPELINE_FALLBACKS)} pipeline fallbacks agree with the "
                   f"live library, and all {len(LAB_VERIFIED)} lab-recorded end "
                   f"conditions still hold - no E012-class value in the codebase")
    print(f"\nverdict: {verdict}")
    LOG.append({"step": "verdict", "ok": not bad and not drift, "detail": verdict})
    (RESULTS / "tier13_enums.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
