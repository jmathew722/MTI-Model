"""Unit tests for the DWG-native semantic layer. No SolidWorks required.

Covers closed-loop detection, circle classification, multiplier parsing, number
parsing, proximity attachment, the provenance invariant, and the A050211E
multiplier conflict (must block, never build).
"""
from __future__ import annotations

import pytest

from dwg_native.extract.schema import RawExtraction, View, Geometry, TextToken
from dwg_native.semantic import (parse_number, detect_closed_loops, classify_circles,
                                 largest_profile_loop, parse_multipliers,
                                 attach_by_proximity, map_to_build_plan,
                                 assert_provenance, find_conflicts, correct_ocr)
from dwg_native.semantic.conflicts import ProvenanceError

IN = 0.0254


def _rect(x0, y0, x1, y1, prefix="G"):
    return [
        {"id": f"{prefix}1", "type": "line", "start_2d_m": [x0, y0], "end_2d_m": [x1, y0]},
        {"id": f"{prefix}2", "type": "line", "start_2d_m": [x1, y0], "end_2d_m": [x1, y1]},
        {"id": f"{prefix}3", "type": "line", "start_2d_m": [x1, y1], "end_2d_m": [x0, y1]},
        {"id": f"{prefix}4", "type": "line", "start_2d_m": [x0, y1], "end_2d_m": [x0, y0]},
    ]


# ---- number parsing ------------------------------------------------------- #
@pytest.mark.parametrize("text,val,kind", [
    ("1.000", 1.0, "length"),
    (".531", 0.531, "length"),
    ("Ø.42", 0.42, "diameter"),
    ("R.531", 0.531, "radius"),
    ("3/8", 0.375, "length"),
])
def test_parse_number_value_and_kind(text, val, kind):
    pn = parse_number(text)
    assert pn.value == pytest.approx(val)
    assert pn.kind == kind


def test_parse_number_counts_and_flags():
    assert parse_number("2X").count == 2
    assert parse_number("6 HOLES").count == 6
    assert parse_number("(6) HLS").count == 6
    assert parse_number("R.53 TYP").is_typical
    assert parse_number("DRILL .16 DP").is_depth
    assert parse_number("Ø.50 THRU").is_through


# ---- closed loops + profile ---------------------------------------------- #
def test_detect_closed_loop_rectangle():
    loops = detect_closed_loops(_rect(0, 0, 4 * IN, 2 * IN))
    assert len(loops) == 1
    lp = loops[0]
    assert lp.width == pytest.approx(4 * IN, abs=1e-4)
    assert lp.height == pytest.approx(2 * IN, abs=1e-4)


def test_largest_profile_picks_the_part_not_a_stray_line():
    segs = _rect(0, 0, 4 * IN, 2 * IN)
    segs.append({"id": "GX", "type": "line", "start_2d_m": [0, 0], "end_2d_m": [1 * IN, 0]})
    loops = detect_closed_loops(segs)
    profile, _ = largest_profile_loop(loops, {"width_m": 4 * IN, "height_m": 2 * IN,
                                              "min_x_m": 0, "min_y_m": 0})
    assert profile is not None
    assert profile.bbox_area == pytest.approx(4 * IN * 2 * IN, rel=1e-3)


def test_border_frame_excluded_in_favour_of_inner_part():
    # outer frame (border) contains a smaller part rectangle
    segs = _rect(0, 0, 10 * IN, 8 * IN, prefix="B") + _rect(2 * IN, 2 * IN, 6 * IN, 5 * IN, prefix="P")
    loops = detect_closed_loops(segs)
    profile, _ = largest_profile_loop(loops, {"width_m": 10 * IN, "height_m": 8 * IN,
                                              "min_x_m": 0, "min_y_m": 0})
    # the inner part (4x3) wins because the outer frame is furniture (contains it)
    assert profile.width == pytest.approx(4 * IN, abs=1e-3)


def test_profile_grounding_rejects_dimension_rectangle():
    # A big dimension-line rectangle (7x5) plus the real 4x2 part; the stated
    # overall dimensions are 4.0 and 2.0, so the 4x2 must win despite being smaller.
    from dwg_native.semantic.rules import select_rectangle_profile
    segs = _rect(0, 0, 7 * IN, 5 * IN, prefix="D") + _rect(0, 0, 4 * IN, 2 * IN, prefix="P")
    prof, matched = select_rectangle_profile(segs, [4 * IN, 2 * IN])
    assert matched == 2
    assert prof.width == pytest.approx(4 * IN, abs=1e-3)
    assert prof.height == pytest.approx(2 * IN, abs=1e-3)


def test_hyphenated_and_paren_counts_parse():
    assert parse_number("12-HOLES").count == 12
    assert parse_number("4-HOLES").count == 4
    assert parse_number("6X").count == 6


def test_gauge_thickness_extracted():
    from dwg_native.semantic.rules import gauge_thickness
    g = gauge_thickness([{"id": "T1", "text": "7 GA. (.179)"}])
    assert g is not None and g[0] == pytest.approx(0.179)


def test_hole_callout_classification():
    from dwg_native.semantic.rules import classify_hole_callout
    assert classify_hole_callout(".38-16 TAP .75 DP.")["subtype"] == "tapped"
    assert classify_hole_callout("DR. & C'SINK FOR .250 FLAT HD. SCR.")["subtype"] == "countersink"
    assert classify_hole_callout("DRILL & C'BORE FOR 3/8 SOC. HD.")["subtype"] == "counterbore"
    assert classify_hole_callout("Ø.50 THRU")["subtype"] == "simple"


# ---- circle classification ------------------------------------------------ #
def test_classify_circles_holes_vs_border_and_concentric_merge():
    from dwg_native.semantic.rules import Loop
    profile = Loop(segment_ids=["G1"], vertices=[(0, 0), (4 * IN, 0), (4 * IN, 2 * IN), (0, 2 * IN)])
    circles = [
        {"id": "C1", "center_2d_m": [1 * IN, 1 * IN], "radius_m": 0.25 * IN},   # hole
        {"id": "C2", "center_2d_m": [1 * IN, 1 * IN], "radius_m": 0.10 * IN},   # concentric -> merges into C1
        {"id": "C3", "center_2d_m": [3 * IN, 1 * IN], "radius_m": 0.25 * IN},   # hole
        {"id": "C4", "center_2d_m": [20 * IN, 20 * IN], "radius_m": 0.25 * IN}, # outside -> border
    ]
    out = classify_circles(circles, profile)
    holes = [c for c in out if c.role == "hole"]
    assert len(holes) == 2
    assert any(c.instances == 2 for c in holes)   # concentric merged
    assert any(c.role == "border" for c in out)


# ---- proximity attachment ------------------------------------------------- #
def test_attach_by_proximity_matches_nearest():
    geo = [{"id": "G5", "type": "circle", "center_2d_m": [1 * IN, 1 * IN], "radius_m": 0.25 * IN}]
    tokens = [{"id": "T1", "text": "Ø.50", "position_2d_m": [1 * IN + 0.002, 1 * IN]}]
    att = attach_by_proximity(tokens, geo)
    assert att[0]["attached_to"] == "G5"
    assert att[0]["confidence"] > 0


# ---- full mapping + provenance -------------------------------------------- #
def _synthetic_raw():
    geo = [Geometry(id=g["id"], type=g["type"], start_2d_m=g.get("start_2d_m"),
                    end_2d_m=g.get("end_2d_m")) for g in _rect(0, 0, 4 * IN, 2 * IN)]
    geo.append(Geometry(id="G5", type="circle", center_2d_m=[1 * IN, 1 * IN], radius_m=0.25 * IN))
    geo.append(Geometry(id="G6", type="circle", center_2d_m=[3 * IN, 1 * IN], radius_m=0.25 * IN))
    txt = [TextToken(id="T1", text=".25", position_2d_m=[2 * IN, -0.01]),
          # Stated overall dimensions confirming the 4x2 rectangle profile (a
          # realistic drawing states its outline; the mapper now hard-flags a
          # profile with NO stated-dimension confirmation as likely-wrong).
          TextToken(id="T2", text="4.000", position_2d_m=[2 * IN, -0.2]),
          TextToken(id="T3", text="2.000", position_2d_m=[-0.2, 1 * IN])]
    return RawExtraction(source_file="SYN.dwg", units_detected="inch",
                         sheet={"width_m": 4 * IN, "height_m": 2 * IN, "min_x_m": 0, "min_y_m": 0},
                         views=[View(name="Model", geometry=geo, text_tokens=txt)])


def test_map_to_build_plan_exact_holes_and_provenance():
    bp = map_to_build_plan(_synthetic_raw())
    assert bp["profile_found"] and bp["hole_count"] == 2 and not bp["blocking"]
    base = next(s for s in bp["steps"] if s["type"] == "extrude_boss")
    assert base["dimensions_drawing_units"]["length"] == pytest.approx(4.0)
    holes = [s for s in bp["steps"] if s["type"] == "hole"]
    assert all(h["dimensions_drawing_units"]["diameter"] == pytest.approx(0.5) for h in holes)
    # hole positions + diameters carry provenance to the exact circle geometry
    assert all(h["provenance_map"]["diameter"].startswith("circle:") for h in holes)
    assert_provenance(bp)   # must not raise


def test_provenance_invariant_raises_on_unsourced_value():
    bp = map_to_build_plan(_synthetic_raw())
    # corrupt: strip provenance from a real feature
    for s in bp["steps"]:
        if s["type"] == "hole":
            s["provenance"] = []
            s["provenance_map"] = {}
            break
    with pytest.raises(ProvenanceError):
        assert_provenance(bp)


# ---- the canonical A050211E multiplier conflict --------------------------- #
def test_a050211e_multiplier_conflict_blocks():
    """Callout says (6) HLS but only 5 countable holes exist -> BLOCKING conflict,
    never a silent tiebreak, never a build."""
    from dwg_native.semantic.rules import Circle
    circles = [Circle(id=f"C{i}", center=(i * IN, IN), radius=0.2 * IN, role="hole")
               for i in range(5)]                              # 5 countable holes
    multipliers = parse_multipliers([{"text": "(6) HLS", "id": "T1"}])
    conflicts = find_conflicts(circles, multipliers, [])
    blocking = [c for c in conflicts if c.get("blocking")]
    assert blocking, "6-vs-5 must produce a blocking conflict"
    assert blocking[0]["type"] == "multiplier_vs_count"
    assert blocking[0]["callout_count"] == 6 and blocking[0]["counted_instances"] == 5


def test_multi_size_holes_reconciled_per_diameter_not_falsely_blocked():
    """'4X Ø.25' + '2X Ø.50' = 6 real holes of two different sizes. The old logic
    took max(4, 2)=4 and compared it to the total 6 -> guaranteed false CRITICAL
    on every multi-size drawing. Grouping by the diameter each callout names must
    NOT block when each group's count is actually correct."""
    from dwg_native.semantic.rules import Circle
    circles = ([Circle(id=f"A{i}", center=(i * IN, IN), radius=0.125 * IN, role="hole")
               for i in range(4)]      # 4x .25 dia
              + [Circle(id=f"B{i}", center=(i * IN, 3 * IN), radius=0.25 * IN, role="hole")
                for i in range(2)])    # 2x .50 dia
    multipliers = parse_multipliers([{"text": "4X Ø.25", "id": "T1"},
                                     {"text": "2X Ø.50", "id": "T2"}])
    conflicts = find_conflicts(circles, multipliers, [])
    assert not any(c.get("blocking") for c in conflicts), conflicts


def test_multi_size_holes_still_blocks_when_a_group_count_is_wrong():
    from dwg_native.semantic.rules import Circle
    circles = ([Circle(id=f"A{i}", center=(i * IN, IN), radius=0.125 * IN, role="hole")
               for i in range(3)]      # only 3, but callout says 4x .25
              + [Circle(id=f"B{i}", center=(i * IN, 3 * IN), radius=0.25 * IN, role="hole")
                for i in range(2)])
    multipliers = parse_multipliers([{"text": "4X Ø.25", "id": "T1"},
                                     {"text": "2X Ø.50", "id": "T2"}])
    conflicts = find_conflicts(circles, multipliers, [])
    blocking = [c for c in conflicts if c.get("blocking")]
    assert blocking and blocking[0]["diameter"] == pytest.approx(0.25)


# ---- profile confirmation hard-flag ---------------------------------------- #
def test_profile_with_zero_stated_confirmation_blocks():
    """A rectangle with NEITHER edge confirmed by a stated dimension (e.g. formed
    by dimension/extension lines, not the real outline) must hard-block, not just
    a soft advisory a human could miss."""
    L, W = 4 * IN, 2 * IN
    geo = [Geometry(id=g["id"], type=g["type"], start_2d_m=g.get("start_2d_m"),
                    end_2d_m=g.get("end_2d_m")) for g in _rect(0, 0, L, W)]
    raw = RawExtraction(source_file="X.dwg", units_detected="inch",
                        sheet={"width_m": L, "height_m": W, "min_x_m": 0, "min_y_m": 0},
                        views=[View(name="Model", geometry=geo, text_tokens=[])])
    bp = map_to_build_plan(raw)
    assert bp["blocking"]
    unverified = [c for c in bp["conflicts"] if c["type"] == "profile_unverified"]
    assert unverified and unverified[0]["blocking"] and unverified[0]["severity"] == "CRITICAL"


# ---- OCR correction ------------------------------------------------------- #
def test_ocr_correction_overrides_vision_mismatch():
    bp = map_to_build_plan(_synthetic_raw())
    vision = {"dimensions": [
        {"id": "D1", "resolved_value": 0.50},   # matches exact hole -> confirmed
        {"id": "D2", "resolved_value": 3.90},   # near base length 4.0 but off -> corrected
    ]}
    corr = correct_ocr(bp, vision)
    assert corr["mode"] == "correcting_vision"
    assert any(c["dwg_value"] == pytest.approx(0.5) for c in corr["confirmations"])
    assert any(abs(c["delta"]) > 0 for c in corr["corrections"])


def test_ocr_correction_standalone_is_authoritative():
    corr = correct_ocr(map_to_build_plan(_synthetic_raw()), None)
    assert corr["mode"] == "standalone"
    assert corr["authoritative_values"]
