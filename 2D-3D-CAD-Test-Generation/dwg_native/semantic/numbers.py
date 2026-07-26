"""Parse MTEXT strings into structured numbers. Pure + deterministic + unit-tested.

Phase 0 RED means dimension VALUES arrive as free-floating MTEXT (e.g. '1.000',
'+.000', '-.001', 'Ø.42', 'R.531', '2X', "DRILL & C'BORE .16 DP FOR"). This module
turns a text token into a ParsedNumber (value + qualifier + tolerance + kind) so
the rules layer can reason over exact numbers, never pixels. No SolidWorks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# A drawing-style decimal: optional sign, optional leading digits, required
# fractional part or integer (e.g. '.531', '1.000', '16', '2.00').
_NUM = r"[-+]?(?:\d+\.\d+|\.\d+|\d+)"
_NUM_RE = re.compile(_NUM)
# Leading diameter/radius/count qualifiers.
_DIA_RE = re.compile(r"(?:Ø|⌀|DIA\.?|\bR\b|\bR(?=\.?\d))", re.IGNORECASE)
# Count/multiplier: "6X", "4 PLACES", and hyphenated "12-HOLES"/"4-HLS".
_COUNT_RE = re.compile(r"\b(\d+)\s*[-\s]?\s*(?:X\b|PLACES?|HOLES?|HLS?|REQD?)", re.IGNORECASE)
_TYP_RE = re.compile(r"\bTYP\b", re.IGNORECASE)
_THRU_RE = re.compile(r"\bTHRU\b", re.IGNORECASE)
_DEEP_RE = re.compile(r"\b(?:DP|DEEP)\b", re.IGNORECASE)
_FRACTION_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")


@dataclass
class ParsedNumber:
    raw: str
    value: Optional[float]              # primary numeric value (drawing units)
    kind: str = "length"                # length | diameter | radius | count | tolerance | note
    is_typical: bool = False            # TYP flag
    is_through: bool = False            # THRU flag
    is_depth: bool = False              # DP/DEEP flag
    plus_tol: Optional[float] = None
    minus_tol: Optional[float] = None
    count: Optional[int] = None         # e.g. 2 from "2X" / "2 HOLES"
    extras: List[float] = field(default_factory=list)  # additional numbers in the string

    @property
    def numeric(self) -> bool:
        return self.value is not None


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_number(text: str) -> ParsedNumber:
    """Parse ONE MTEXT string into a ParsedNumber. Never raises."""
    raw = (text or "").strip()
    pn = ParsedNumber(raw=raw, value=None)
    if not raw:
        pn.kind = "note"
        return pn

    pn.is_typical = bool(_TYP_RE.search(raw))
    pn.is_through = bool(_THRU_RE.search(raw))
    pn.is_depth = bool(_DEEP_RE.search(raw))

    # Count qualifier ("2X", "6 HOLES", "(6) HLS").
    m = _COUNT_RE.search(raw)
    if m:
        pn.count = int(m.group(1))
    else:
        mp = re.search(r"\((\d+)\)", raw)  # "(6)"
        if mp:
            pn.count = int(mp.group(1))

    # Fractions like 3/8 -> 0.375.
    frac = _FRACTION_RE.search(raw)
    frac_val = None
    if frac:
        denom = int(frac.group(2)) or 1
        frac_val = int(frac.group(1)) / denom

    # A bare fraction token (e.g. '3/8') is one value, not two integers.
    if frac_val is not None and re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", raw):
        pn.value = round(frac_val, 4)
        pn.kind = "length"
        return pn

    nums = [float(x) for x in _NUM_RE.findall(raw)]
    # Tolerance pairs: a '+.000' and a '-.001' in the same or sibling tokens.
    plus = [n for n in nums if raw.count("+") and n >= 0 and re.search(r"\+\s*" + re.escape(_fmt(n)), raw)]
    minus = [abs(n) for n in nums if re.search(r"-\s*" + re.escape(_fmt(abs(n))), raw)]

    # Primary value: prefer a standalone decimal, then a fraction.
    primary = None
    if nums:
        # drop pure count integers already captured
        candidates = [n for n in nums if not (pn.count is not None and float(pn.count) == n)]
        primary = candidates[0] if candidates else nums[0]
    if primary is None and frac_val is not None:
        primary = frac_val
    pn.value = primary
    if len(nums) > 1:
        pn.extras = nums[1:]

    # Qualifier / kind.
    if _DIA_RE.search(raw) and re.search(r"(?:Ø|⌀|DIA)", raw, re.IGNORECASE):
        pn.kind = "diameter"
    elif re.search(r"\bR\.?\d", raw) or re.match(r"^R", raw):
        pn.kind = "radius"
    elif pn.count is not None and primary is None:
        pn.kind = "count"
        pn.value = float(pn.count)
    elif primary is None:
        pn.kind = "note"

    # Tolerance-only tokens ('+.000' / '-.001').
    if primary is not None and (raw.startswith("+") or raw.startswith("-")) and len(raw) <= 8:
        pn.kind = "tolerance"
        if raw.startswith("+"):
            pn.plus_tol = primary
        else:
            pn.minus_tol = -abs(primary)
    return pn


def _fmt(n: float) -> str:
    s = f"{n:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def parse_tokens(texts: List[str]) -> List[ParsedNumber]:
    return [parse_number(t) for t in texts]
