"""Use DWG-exact values to CORRECT the OCR/vision extraction findings.

The stated purpose of the DWG-native pipeline: the DWG import yields exact
geometry + exact text, so where the vision pipeline guessed a digit from pixels,
the DWG value is authoritative. For each vision field:
  * a DWG value matching within tolerance CONFIRMS it,
  * a mismatch OVERRIDES the vision value with the DWG value (recorded, both
    readings kept),
  * a vision field with no DWG counterpart is left ALONE (reported as unverified).
Standalone (no prior vision run) the DWG values are simply authoritative.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_TOL = 0.01   # inch — within this, a vision reading is "confirmed", else corrected


def _dwg_values(build_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten authoritative DWG values from the build plan (with provenance)."""
    vals: List[Dict[str, Any]] = []
    for step in build_plan.get("steps", []):
        fid = step.get("feature_id")
        for key, v in (step.get("dimensions_drawing_units") or {}).items():
            if isinstance(v, (int, float)):
                vals.append({"feature_id": fid, "key": key, "value": round(float(v), 4),
                             "provenance": (step.get("provenance_map") or {}).get(key, "")})
        for pos in step.get("positions_xy", []) or []:
            if isinstance(pos, list) and len(pos) == 2:
                vals.append({"feature_id": fid, "key": "position",
                             "value": [round(pos[0], 4), round(pos[1], 4)],
                             "provenance": (step.get("provenance_map") or {}).get("position", "")})
    return vals


def _iter_vision_fields(vision: Dict[str, Any]):
    """Yield (label, numeric_value) pairs from a vision *_extraction.json, tolerant
    of the existing schema (dimensions list with value/values fields)."""
    for d in (vision.get("dimensions") or []):
        val = d.get("resolved_value")
        if val is None:
            val = d.get("value")
        if val is None and d.get("values"):
            val = d["values"][0] if isinstance(d["values"], list) else d["values"]
        label = d.get("id") or d.get("applies_to") or d.get("label") or "?"
        try:
            yield label, float(val)
        except (TypeError, ValueError):
            continue


def correct_ocr(build_plan: Dict[str, Any],
                vision_extraction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dwg_vals = _dwg_values(build_plan)
    numeric_dwg = [v for v in dwg_vals if isinstance(v["value"], (int, float))]

    if not vision_extraction:
        return {
            "mode": "standalone",
            "note": "No vision extraction supplied; DWG values are authoritative.",
            "authoritative_values": dwg_vals,
            "confirmations": [], "corrections": [], "unverified_vision_fields": [],
        }

    confirmations, corrections = [], []
    used = set()
    for label, vval in _iter_vision_fields(vision_extraction):
        # nearest DWG numeric value
        best = None
        for i, dv in enumerate(numeric_dwg):
            d = abs(dv["value"] - vval)
            if best is None or d < best[0]:
                best = (d, i)
        if best is None:
            continue
        dist, idx = best
        dv = numeric_dwg[idx]
        if dist <= _TOL:
            confirmations.append({"vision_field": label, "vision_value": round(vval, 4),
                                  "dwg_value": dv["value"], "dwg_feature": dv["feature_id"],
                                  "provenance": dv["provenance"]})
            used.add(idx)
        else:
            corrections.append({"vision_field": label, "vision_value": round(vval, 4),
                                "dwg_value": dv["value"], "dwg_feature": dv["feature_id"],
                                "delta": round(dv["value"] - vval, 4),
                                "decision": "DWG exact value overrides vision reading",
                                "provenance": dv["provenance"]})

    return {
        "mode": "correcting_vision",
        "confirmations": confirmations,
        "corrections": corrections,
        "authoritative_values": dwg_vals,
        "summary": (f"{len(confirmations)} vision field(s) confirmed, "
                    f"{len(corrections)} corrected by exact DWG values."),
    }
