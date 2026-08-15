"""Shared quantity-language parser for US drafting hole/feature callouts.

One utility used by BOTH extraction and the overview cross-check so the two
tiers count the same way (learning-loop 2026-07-09, Issue B "hole/thread count
mismatch"). Handles the common forms:

    ".406 DIA THRU (2) HL'S"        -> 2
    ".422 DIA 6-HOLES"              -> 6
    "1/4-20 UNC 4 PLACES"           -> 4
    "(6) HLS"                       -> 6
    "3 PL"  /  "3 PLCS"  /  "2 REQD" -> 3 / 3 / 2
    "R.531 TYP"                     -> qualifier TYP (count defaults to 1)

Public entry points: :func:`parse_quantity`, :func:`is_typ`,
:func:`classify_callout`, :func:`is_hole_callout`,
:func:`reconcile_callout_count`.
"""
from __future__ import annotations

import re
from typing import Optional

# Quantity patterns, tried in order; the first match wins. Each captures the
# integer count in group 1. Ordered specific -> general.
_QTY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\((\d{1,3})\)\s*(?:hl|hls|hl's|hole|holes|pl|pls|pl's|plc|plcs|plcs?|places?)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*[-–]?\s*(?:holes?|hls?|hl's)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:places?|plc?s?|pl's|pl)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:req'?d|reqd|required|x)\b", re.I),
)
_TYP_RE = re.compile(r"\btyp(ical)?\b", re.I)


def is_typ(text: str) -> bool:
    """True if the callout carries a TYP / TYPICAL qualifier (value repeats)."""
    return bool(_TYP_RE.search(text or ""))


def parse_quantity(text: str, default: int = 1) -> int:
    """The instance count a callout specifies (e.g. ``(2) HL'S`` -> 2). Returns
    ``default`` (1) when the callout names no explicit quantity."""
    t = text or ""
    for pat in _QTY_PATTERNS:
        m = pat.search(t)
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if n > 0:
                return n
    return default


# --------------------------------------------------------------------------- #
# Callout typing (learning-loop 2026-07-10, P5)
#
# A count of instances is meaningless without knowing WHAT is being counted.
# A001591E's ".12 R. TYP." (a corner-radius on 4 corners) was compared against an
# 8-hole extraction and fired a false CRITICAL hole-count disagreement. Type the
# callout BEFORE counting: only hole-type callouts enter hole-count
# reconciliation; radius callouts reconcile against fillet features (and their
# TYP instance count drives the fillet count instead).
# --------------------------------------------------------------------------- #
# Order matters: thread → compound (cbore/csk) → hole (dia/dr) → radius.
_THREAD_RE = re.compile(
    r"\b(?:tap(?:ped)?|unc|unf|unef|npt|nptf|m\d+\s*x\s*[\d.]+|\d+\s*[-–]\s*\d+\s*(?:unc|unf|tap)?"
    r"|#\d+\s*[-–]\s*\d+|\d+/\d+\s*[-–]\s*\d+)\b", re.I)
_COMPOUND_RE = re.compile(r"(c'?\s*bore|counterbore|c'?\s*sink|csk|countersink|spotface|sf\b)", re.I)
_HOLE_RE = re.compile(r"(\bdia\b|\bdr\b|\bdrill\b|Ø|\bthru\b|\bhole?s?\b|\bhl'?s?\b|\bhls\b)", re.I)
# Radius: an R glyph as its own token (".12 R", "R.12", "R3", "RAD", "RADIUS"),
# but NOT inside DR. (drill) or a word — require the R to touch a number/space.
_RADIUS_RE = re.compile(r"(?<![A-Za-z])(?:r\s*\.?\d|\.?\d+\s*r\b|\brad\b|\bradius\b)", re.I)


def classify_callout(text: str) -> str:
    """Classify a callout by what it dimensions:
    ``"threaded_hole"`` | ``"compound_hole"`` | ``"hole"`` | ``"radius"`` |
    ``"unknown"``. Checked most-specific first so "1/4-20 TAP" is a threaded hole,
    not a bare hole, and ".12 R. TYP." is a radius, not a hole."""
    t = text or ""
    if _THREAD_RE.search(t):
        return "threaded_hole"
    if _COMPOUND_RE.search(t):
        return "compound_hole"
    if _HOLE_RE.search(t):
        return "hole"
    if _RADIUS_RE.search(t):
        return "radius"
    return "unknown"


_HOLE_KINDS = frozenset({"hole", "threaded_hole", "compound_hole"})


def is_hole_callout(text: str) -> bool:
    """True if the callout counts HOLES (so it may enter hole-count reconciliation).
    A radius callout (fillet/corner-round) returns False and must NOT be counted
    against holes."""
    return classify_callout(text) in _HOLE_KINDS


# ---------------------------------------------------------------------------- #
# Callout-vs-count reconciliation (Phase 3d — the A050211E 5-vs-6 conflict)
# Moved here 2026-08-15 from pipeline/hole_wizard.py (REFACTOR_ANALYSIS §2.1)
# when that module's unverified HoleWizard5 COM path was quarantined: this is
# live resolver logic about callout QUANTITY language, which is what this
# module owns.
# ---------------------------------------------------------------------------- #

def reconcile_callout_count(callout_qty: Optional[int],
                            countable_instances: Optional[int],
                            feature_id: str,
                            *, source: str = "callout_count") -> Optional[dict]:
    """Compare a callout MULTIPLIER against the COUNTABLE pattern instances.

    When both are known and DISAGREE (the A050211E flange: a ``(6)`` callout but 5
    countable holes) this is a **build-blocking conflict**: return a CRITICAL flag
    object shaped for the pipeline's existing escalation surface (engineering
    review + ``human_assist`` queue) — NEVER silently pick a number. Returns
    ``None`` when they agree or either side is unknown (nothing to escalate).
    """
    if not callout_qty or not countable_instances:
        return None
    if int(callout_qty) == int(countable_instances):
        return None
    return {
        "feature_id": feature_id,
        "severity": "CRITICAL",
        "source": source,
        "kind": "callout_vs_count_mismatch",
        "callout_qty": int(callout_qty),
        "countable_instances": int(countable_instances),
        "human_note": (
            f"{feature_id}: callout multiplier ({int(callout_qty)}) does not match "
            f"the {int(countable_instances)} countable hole positions on the sheet. "
            "Build-blocking — resolve which count is correct before drilling."
        ),
        "gate_question": (
            f"How many holes does {feature_id} have — the callout says "
            f"{int(callout_qty)} but {int(countable_instances)} are dimensioned/visible?"
        ),
        "candidates": [int(callout_qty), int(countable_instances)],
        "blocking": True,
    }
