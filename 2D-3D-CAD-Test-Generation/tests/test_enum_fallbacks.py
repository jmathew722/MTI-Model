"""Hardcoded SolidWorks enum fallbacks must match the live type library.

E012 is the worst failure this project has recorded: `swEndCondThroughAllBoth`
was assumed to be 7 from enum ordering. It is 9 — and 7 is `swEndCondUpToBody`,
which with no body reference **deleted the entire solid** while returning a valid
feature and reporting a clean rebuild (volume 12.0 in³ → 0, body count still 1).

`solidworks_builder._const(name, fallback)` reads the live type library and falls
back to a hardcoded number when it cannot. Those fallbacks are the residual E012
risk: they apply precisely when the library is unavailable to correct them, so
nothing downstream can catch a wrong one.

Every value below was READ FROM THE LIVE LIBRARY on SolidWorks 2026 rev 34.3.2
(`experiments/solidworks_practice/t13_enum_fallback_audit.py`, iteration 18, all
ten agreed). This test is the CI-safe half: it re-reads the fallbacks out of the
pipeline source and fails if one is edited to a value the live library contradicts
— no SolidWorks needed. Changing a number here requires re-running that audit.
"""
import re
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"

# name -> value, verified against the live type library (iteration 18)
VERIFIED = {
    "swCM": 1,
    "swDefaultTemplatePart": 8,
    "swINCHES": 3,
    "swMM": 0,
    "swSolidBody": 0,
    # end conditions: the E012 family. Naive enum ordering gets these WRONG.
    "swEndCondBlind": 0,
    "swEndCondThroughAll": 1,
    "swEndCondMidPlane": 6,          # NOT 4
    "swEndCondUpToBody": 7,          # the value that deleted the solid
    "swEndCondThroughAllBoth": 9,    # NOT 7
}

_CONST_CALL = re.compile(r'_const\(\s*"([A-Za-z_0-9]+)"\s*,\s*(-?\d+)\s*\)')


def _fallbacks_in_source() -> list[tuple[str, int, str]]:
    """Every ``_const("name", n)`` literal in pipeline/, with its file."""
    found = []
    for path in sorted(PIPELINE.rglob("*.py")):
        for name, value in _CONST_CALL.findall(path.read_text(encoding="utf-8")):
            found.append((name, int(value), path.name))
    return found


def test_source_has_fallbacks_to_check():
    """Guard the guard: if the regex stops matching, this test is worthless."""
    assert _fallbacks_in_source(), "_const fallbacks not found — has the call shape changed?"


@pytest.mark.parametrize("name,value,where", _fallbacks_in_source())
def test_every_fallback_matches_the_live_library(name, value, where):
    assert name in VERIFIED, (
        f"{where} falls back to {value} for {name!r}, which has never been checked "
        f"against a live SolidWorks type library. Run "
        f"experiments/solidworks_practice/t13_enum_fallback_audit.py and add the "
        f"measured value to VERIFIED — a guessed enum is how E012 deleted a solid."
    )
    assert value == VERIFIED[name], (
        f"{where} falls back to {value} for {name!r}, but the live library says "
        f"{VERIFIED[name]}. This is the E012 failure class: a wrong enum returns a "
        f"valid feature and reports a clean rebuild while building the wrong thing."
    )


def test_end_condition_values_are_not_naive_ordering():
    """The specific trap: these two are NOT what enum ordering suggests."""
    assert VERIFIED["swEndCondMidPlane"] == 6, "MidPlane is 6, not 4"
    assert VERIFIED["swEndCondThroughAllBoth"] == 9, "ThroughAllBoth is 9, not 7"
    assert VERIFIED["swEndCondUpToBody"] == 7, (
        "7 is UpToBody — the value that silently deleted the solid in E012"
    )
