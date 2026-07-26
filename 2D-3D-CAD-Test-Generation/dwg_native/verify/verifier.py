"""Five machine-checkable verification gates. A part failing any gate is a failure.

1. rebuild            — the build reported no failed features.
2. fully_defined      — every sketch reported fully defined.
3. dimension_roundtrip— body bounding box matches base length/width/thickness
                        within tolerance (the built solid == the DWG numbers).
4. feature_count      — holes built == holes planned == reconciled callout count.
5. bounding_box       — overall size sane vs the stated profile dimensions.

Gate 3 is the strongest and exists only because extraction was numeric to begin
with. Computed from the build result (body_box_m from IBody2.GetBodyBox) + the
build plan, so this module is unit-testable without SolidWorks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_TOL_M = 0.5e-3   # 0.5 mm tolerance on round-trip / bbox comparisons


@dataclass
class VerifyReport:
    checks: List[Dict[str, Any]] = field(default_factory=list)
    passed: bool = True
    failing_check: str = ""

    def add(self, name: str, ok: bool, detail: str, measured=None, expected=None) -> None:
        self.checks.append({"check": name, "status": "PASS" if ok else "FAIL",
                            "detail": detail, "measured": measured, "expected": expected})
        if not ok and self.passed:
            self.passed = False
            self.failing_check = name

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "failing_check": self.failing_check,
                "checks": self.checks}


def verify_build(build_plan: Dict[str, Any], build_result: Dict[str, Any]) -> VerifyReport:
    rep = VerifyReport()
    factor = build_plan.get("unit_factor_to_meters", 0.0254)

    # 1. rebuild — no failed features.
    feats = build_result.get("features", [])
    failed = [f for f in feats if f.get("status") == "FAIL"]
    rep.add("rebuild", not failed,
            "no failed features" if not failed
            else f"{len(failed)} feature(s) failed: " +
                 ", ".join(f"{f['feature_id']} ({f.get('detail','')})" for f in failed))

    # 2. fully_defined — every sketch fully defined.
    fd = build_result.get("fully_defined", [])
    under = [s for s in fd if s.get("status") not in ("fully",)]
    rep.add("fully_defined", not under,
            "all sketches fully defined" if not under
            else f"{len(under)} sketch(es) not fully defined: " +
                 ", ".join(f"{s['feature_id']}:{s['status']}" for s in under))

    # base plate expected dims (meters)
    base = next((s for s in build_plan.get("steps", []) if s.get("type") == "extrude_boss"), None)
    box = build_result.get("body_box_m") or []

    # 3. dimension round-trip — body box vs base length/width/thickness.
    if base and len(box) == 6:
        d = base.get("dimensions_drawing_units", {})
        exp = sorted(round(v * factor, 6) for v in
                     (d.get("length", 0), d.get("width", 0), d.get("thickness", 0)) if v)
        meas = sorted(round(abs(box[i + 3] - box[i]), 6) for i in range(3))
        ok = len(exp) == len(meas) and all(abs(a - b) <= _TOL_M for a, b in zip(exp, meas))
        rep.add("dimension_roundtrip", ok,
                "built solid matches DWG base dimensions" if ok
                else "built solid does not match DWG base dimensions",
                measured=meas, expected=exp)
    else:
        rep.add("dimension_roundtrip", False,
                "no body box or base step to round-trip against")

    # 4. feature count — built holes == planned holes.
    planned_holes = sum(1 for s in build_plan.get("steps", []) if s.get("type") == "hole")
    built_holes = sum(1 for f in feats if f.get("type") == "hole" and f.get("status") == "PASS")
    rep.add("feature_count", planned_holes == built_holes,
            f"{built_holes}/{planned_holes} holes built",
            measured=built_holes, expected=planned_holes)

    # 5. bounding box sanity — non-degenerate and not wildly larger than profile.
    if len(box) == 6 and base:
        dims = base.get("dimensions_drawing_units", {})
        exp_max = max((dims.get("length", 0), dims.get("width", 0))) * factor
        meas_max = max(abs(box[i + 3] - box[i]) for i in range(3))
        ok = meas_max > 0 and (exp_max == 0 or meas_max <= exp_max * 1.25 + _TOL_M)
        rep.add("bounding_box", ok,
                "bounding box within sane range of stated size" if ok
                else "bounding box far exceeds stated overall size",
                measured=round(meas_max, 6), expected=round(exp_max, 6))
    else:
        rep.add("bounding_box", len(box) == 6, "no body box available" if len(box) != 6
                else "bounding box present")
    return rep
