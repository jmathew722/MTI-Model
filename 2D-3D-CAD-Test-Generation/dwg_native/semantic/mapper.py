"""raw_extraction.json -> build_plan.json. Rules first; provenance on every value.

The DWG gives EXACT geometry: a hole's diameter and position come from the
circle's radius and center (not a read number), and the base profile's length and
width come from the closed outline loop. Thickness/depth come from exact MTEXT
tokens. Every emitted value records the raw object id it came from, so
assert_provenance can fail the job on anything unsourced. This is the mechanism
by which the DWG corrects the OCR/vision findings: exact wins.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..extract.schema import RawExtraction
from .numbers import parse_number
from .rules import (Circle, attach_by_proximity, classify_circles,
                    detect_closed_loops, largest_profile_loop, parse_multipliers)
from .conflicts import find_conflicts

_UNIT_FACTOR = {"inch": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0, "feet": 0.3048}


def _to_draw(v_m: Optional[float], factor: float) -> Optional[float]:
    if v_m is None:
        return None
    return round(v_m / factor, 4)


def map_to_build_plan(raw: RawExtraction) -> Dict[str, Any]:
    factor = _UNIT_FACTOR.get(raw.units_detected, 0.0254)
    geo = [_g(gg) for gg in raw.all_geometry()]
    segs = [g for g in geo if g["type"] in ("line", "arc")]
    circ = [g for g in geo if g["type"] == "circle"]
    texts = [_t(t) for t in raw.all_text()]

    loops = detect_closed_loops(segs)
    profile = largest_profile_loop(loops, raw.sheet, segments=segs)
    circles = classify_circles(circ, profile)
    holes = [c for c in circles if c.role == "hole"]
    multipliers = parse_multipliers(texts)
    # Only dimension-LIKE tokens are candidates for attachment: mostly-numeric,
    # plausible drawing magnitude, not material/notes ("1020 STEEL", "FINISH...").
    numeric_tokens = [t for t in texts if _is_dimension_like(t["text"])]
    attachments = attach_by_proximity(numeric_tokens, geo)
    conflicts = find_conflicts(circles, multipliers, attachments)

    steps: List[Dict[str, Any]] = []
    dispositions: List[Dict[str, Any]] = []
    corrections: List[Dict[str, Any]] = []

    # -- setup ------------------------------------------------------------- #
    steps.append({"seq": 0, "feature_id": "-", "type": "setup",
                  "description": "New part, units, save-as", "provenance": [],
                  "provenance_map": {}, "dimensions_drawing_units": {}})

    # -- base solid from the profile loop ---------------------------------- #
    if profile is None:
        conflicts.append({"type": "no_profile", "blocking": True, "severity": "CRITICAL",
                          "detail": "No closed outer profile loop found in geometry."})
    else:
        length = _to_draw(profile.width, factor)
        width = _to_draw(profile.height, factor)
        thickness, th_prov, th_flag = _find_thickness(texts, profile, factor)
        base_prov = [f"loop:{','.join(profile.segment_ids[:8])}"]
        steps.append({
            "seq": 1, "feature_id": "F001", "type": "extrude_boss",
            "description": (f"Base plate {length}×{width}×{thickness} "
                            f"(profile loop, {len(profile.segment_ids)} edges)"),
            "dimensions_drawing_units": {"length": length, "width": width, "thickness": thickness},
            "positions_xy": [[0.0, 0.0]],
            "sketch_plane": "front",
            "provenance": base_prov,
            "provenance_map": {"length": f"loop:{profile.segment_ids[0]}" if profile.segment_ids else "loop",
                               "width": f"loop:{profile.segment_ids[0]}" if profile.segment_ids else "loop",
                               "thickness": th_prov},
            "flags": ([{"tier": "MEDIUM", "note": th_flag}] if th_flag else []),
        })
        dispositions.append({"feature_id": "F001", "type": "extrude_boss",
                             "state": "BUILT" if not th_flag else "BUILT_WITH_DERIVED_VALUE",
                             "values_used": {"length": length, "width": width, "thickness": thickness}})

    # -- holes from EXACT circle geometry ---------------------------------- #
    # Positions are referenced to the BASE PROFILE's lower-left corner (the base
    # rectangle is drawn from (0,0)), NOT the sheet origin — otherwise holes land
    # in absolute sheet coordinates outside the plate and cut no material.
    if profile is not None:
        px0, py0 = profile.bbox[0], profile.bbox[1]
    else:
        px0, py0 = _origin(raw.sheet)
    for i, h in enumerate(holes, start=1):
        fid = f"F{100 + i}"
        dia = _to_draw(2 * h.radius, factor)
        cx = _to_draw(h.center[0] - px0, factor)
        cy = _to_draw(h.center[1] - py0, factor)
        # OCR correction: if a nearby MTEXT claims a diameter, compare to the exact one.
        corr = _diameter_correction(h, texts, factor, dia)
        if corr:
            corrections.append(corr)
        steps.append({
            "seq": 3 + i, "feature_id": fid, "type": "hole",
            "description": f"Hole ⌀{dia} at ({cx}, {cy}) — exact from circle {h.id}",
            "dimensions_drawing_units": {"diameter": dia},
            "positions_xy": [[cx, cy]],
            "depth_type": "through_all",
            "hole_instances": h.instances,
            "provenance": [f"circle:{h.id}"],
            "provenance_map": {"diameter": f"circle:{h.id}", "position": f"circle:{h.id}"},
            "flags": [],
        })
        dispositions.append({"feature_id": fid, "type": "hole", "state": "BUILT",
                             "values_used": {"diameter": dia, "x": cx, "y": cy},
                             "position_source": "vector_geometry"})

    # -- export / verify tail ---------------------------------------------- #
    steps.append({"seq": 999, "feature_id": "-", "type": "verify",
                  "description": "Rebuild, mass props, bbox", "provenance": [],
                  "provenance_map": {}, "dimensions_drawing_units": {}})
    steps.append({"seq": 1001, "feature_id": "-", "type": "export",
                  "description": "Export STL", "provenance": [],
                  "provenance_map": {}, "dimensions_drawing_units": {}})

    return {
        "part": raw.source_file.rsplit(".", 1)[0],
        "units": raw.units_detected,
        "unit_factor_to_meters": factor,
        "coordinate_origin": "lower_left_corner_of_base_solid",
        "x_direction": "positive_right", "y_direction": "positive_up",
        "source": "dwg_native",
        "profile_found": profile is not None,
        "hole_count": len(holes),
        "steps": steps,
        "dispositions": dispositions,
        "conflicts": conflicts,
        "ocr_corrections": corrections,
        "attachments": attachments,
        "blocking": any(c.get("blocking") for c in conflicts),
    }


# --------------------------------------------------------------------------- #
def _g(g) -> dict:
    return {"id": g.id, "type": g.type, "start_2d_m": g.start_2d_m,
            "end_2d_m": g.end_2d_m, "center_2d_m": g.center_2d_m,
            "radius_m": g.radius_m, "layer": g.layer, "construction": g.construction}


def _t(t) -> dict:
    return {"id": t.id, "text": t.text, "position_2d_m": t.position_2d_m,
            "height_m": t.height_m, "layer": t.layer}


def _origin(sheet: dict):
    return (sheet.get("min_x_m", 0.0), sheet.get("min_y_m", 0.0))


import re as _re
_MATERIAL_WORDS = _re.compile(r"[A-Za-z]{4,}")   # STEEL, FINISH, DRILL, EDUCATIONAL...


def _is_dimension_like(text: str) -> bool:
    """A token that looks like a linear dimension worth trying to attach:
    parses to a number, plausible inch magnitude (< 200), and is not a material
    or free-text note (a long alpha word present)."""
    pn = parse_number(text)
    if not pn.numeric or pn.value is None:
        return False
    if abs(pn.value) >= 200:            # '1020 STEEL', years, etc. — not a dimension
        return False
    if pn.kind in ("count",):
        return False
    if _MATERIAL_WORDS.search(text) and not pn.is_depth and not pn.is_through \
            and pn.kind not in ("diameter", "radius"):
        # letters like STEEL/FINISH dominate -> a note, not a dimension
        if not _re.match(r"^\s*[.\d/]+", text):
            return False
    return True


def _find_thickness(texts, profile, factor):
    """Pick a plate thickness from MTEXT. Returns (thickness_draw_units, provenance,
    flag_or_None). Thickness is the least reliable value on a single-view plate
    (it is often only in a side view or a note), so:
      * a token explicitly labelled THK/THICK is trusted (no flag),
      * otherwise the smallest plausible standalone length is used but ALWAYS
        flagged MEDIUM (it could be a hole/feature size, e.g. a tap-drill),
      * if nothing plausible exists, a conservative 0.25 is committed and flagged.
    Never silently invents; the flag surfaces in the UI + engineering review."""
    len_draw = round(profile.width / factor, 4)
    wid_draw = round(profile.height / factor, 4)
    # 1) an explicitly labelled thickness wins outright.
    for t in texts:
        if _re.search(r"\b(THK|THICK|THICKNESS)\b", t["text"], _re.IGNORECASE):
            pn = parse_number(t["text"])
            if pn.numeric:
                return round(pn.value, 4), f"text:{t['id']} (labelled THK)", None
    # 2) smallest plausible standalone length — used, but flagged as inferred.
    candidates = []
    for t in texts:
        pn = parse_number(t["text"])
        if pn.kind == "length" and pn.numeric and not pn.count and not pn.is_depth:
            v = round(pn.value, 4)
            if v in (len_draw, wid_draw):
                continue
            if 0.0 < v < 0.5 * min(len_draw, wid_draw):
                candidates.append((v, t["id"], t["text"]))
    if candidates:
        v, tid, txt = min(candidates, key=lambda c: c[0])
        return v, f"text:{tid} (inferred)", (
            f"Thickness {v:g} INFERRED from {txt!r} — no side view / THK label; "
            "could be a feature/hole size. Verify against the drawing.")
    return 0.25, "derived:no_thickness_dimension_found", \
        "No thickness/side-view dimension found; committed conservative 0.25 (verify)."


def _diameter_correction(hole: Circle, texts, factor, exact_dia):
    """If an MTEXT near the hole claims a diameter that differs from the exact
    geometry, record a correction (exact geometry wins)."""
    import math
    best = None
    for t in texts:
        pn = parse_number(t["text"])
        if pn.kind != "diameter" or not pn.numeric:
            continue
        pos = t["position_2d_m"]
        d = math.hypot(pos[0] - hole.center[0], pos[1] - hole.center[1])
        if best is None or d < best[0]:
            best = (d, pn.value, t["id"])
    if best and abs(best[1] - exact_dia) > 1e-3 and best[0] < 0.05:
        return {"feature": "hole", "circle_id": hole.id,
                "callout_value": best[1], "exact_geometry_value": exact_dia,
                "source_token": best[2],
                "decision": "exact geometry overrides callout",
                "delta": round(exact_dia - best[1], 4)}
    return None
