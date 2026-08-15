"""Phase 3e tests — the real Hole Wizard family + callout-vs-count escalation.

Covers the PURE logic (no SolidWorks needed): the sub-type → enum mapping and
Value-slot assembly for each hole sub-type, the ANSI clearance table, the
constants module's named-resolution + fallbacks, and the 5-vs-6 callout-vs-count
conflict wired end-to-end through the resolver into the human-assist queue.
"""
import math

import pytest

from pipeline.experimental import hole_wizard as hw
from pipeline.experimental import hole_wizard_constants as hwc
from pipeline.coordinate_normalize import INCH_TO_M
from pipeline.resolver import resolve_extraction
from pipeline.schema import DrawingData, HoleCallout


# --------------------------------------------------------------------------- #
# Constants module — named resolution, no inline magic integers
# --------------------------------------------------------------------------- #
class TestConstants:
    def test_general_hole_types_resolve_to_v32_fallbacks(self):
        assert hwc.general_hole_type("swWzdCounterBore") == 0
        assert hwc.general_hole_type("swWzdCounterSink") == 1
        assert hwc.general_hole_type("swWzdHole") == 2
        assert hwc.general_hole_type("swWzdTap") == 4
        assert hwc.general_hole_type("swWzdLegacy") == 5

    def test_end_condition(self):
        assert hwc.end_condition(through_all=True) == 1
        assert hwc.end_condition(through_all=False) == 0
        assert hwc.end_condition(through_all=False, up_to_surface=True) == 4

    def test_hole_standard_inch_vs_metric(self):
        assert hwc.hole_standard(metric=False) == 0
        assert hwc.hole_standard(metric=True) == 1

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            hwc.resolve("swWzdNotAThing")

    def test_clearance_table_inch_and_metric(self):
        assert hwc.clearance_diameter_in("1/4", fit="normal") == 0.266
        assert hwc.clearance_diameter_in("1/4", fit="close") == 0.257
        # metric converts mm -> inch
        m6 = hwc.clearance_diameter_in("M6", fit="normal")
        assert m6 == round(6.6 / 25.4, 4)
        assert hwc.clearance_diameter_in("bogus") is None


# --------------------------------------------------------------------------- #
# Per-sub-type planning (pure) — one assertion path per hole kind
# --------------------------------------------------------------------------- #
def _callout(**kw) -> HoleCallout:
    base = dict(id="H1", type="thru", diameter=0.25, thru=True, qty=1)
    base.update(kw)
    return HoleCallout(**base)


class TestPlanPerSubtype:
    def test_simple_thru(self):
        p = hw.plan_wizard_hole(_callout(type="thru", diameter=0.25),
                                through_all=True, depth_m=None, unit="in")
        assert p.subtype == "simple"
        assert p.general_hole_type == hwc.general_hole_type("swWzdHole")
        assert p.end_condition == 1
        assert p.diameter_m == pytest.approx(0.25 * INCH_TO_M)
        assert p.values == [0.0] * 12  # no type-specific slots

    def test_blind_gets_positive_depth(self):
        p = hw.plan_wizard_hole(_callout(type="blind", thru=False, depth=0.4),
                                through_all=False, depth_m=0.4 * INCH_TO_M, unit="in")
        assert p.subtype == "blind"
        assert p.end_condition == 0
        assert p.depth_m > 0

    def test_tapped_uses_tap_type(self):
        p = hw.plan_wizard_hole(_callout(type="tapped", diameter=0.201, thread_spec="1/4-20"),
                                through_all=True, depth_m=None, unit="in")
        assert p.subtype == "tapped"
        assert p.general_hole_type == hwc.general_hole_type("swWzdTap")
        assert "tap" in p.description.lower()

    def test_counterbore_fills_value_slots(self):
        p = hw.plan_wizard_hole(
            _callout(type="counterbore", diameter=0.25, cbore_diameter=0.5, cbore_depth=0.2),
            through_all=True, depth_m=None, unit="in")
        assert p.subtype == "counterbore"
        assert p.general_hole_type == hwc.general_hole_type("swWzdCounterBore")
        assert p.values[0] == pytest.approx(0.5 * INCH_TO_M)   # cbore diameter
        assert p.values[1] == pytest.approx(0.2 * INCH_TO_M)   # cbore depth

    def test_countersink_angle_in_radians(self):
        p = hw.plan_wizard_hole(
            _callout(type="countersink", diameter=0.25, csink_diameter=0.5, csink_angle=82.0),
            through_all=True, depth_m=None, unit="in")
        assert p.subtype == "countersink"
        assert p.general_hole_type == hwc.general_hole_type("swWzdCounterSink")
        assert p.values[0] == pytest.approx(0.5 * INCH_TO_M)
        assert p.values[1] == pytest.approx(math.radians(82.0))

    def test_clearance_uses_ansi_table_diameter(self):
        # A plain thru hole naming a 1/4 fastener is a clearance hole; the drill
        # diameter comes from the ANSI table (0.266), not the callout's 0.5.
        p = hw.plan_wizard_hole(_callout(type="thru", diameter=0.5, thread_spec="1/4"),
                                through_all=True, depth_m=None, unit="in")
        assert p.subtype == "clearance"
        assert p.diameter_m == pytest.approx(0.266 * INCH_TO_M)

    def test_hole_wizard5_args_shape(self):
        p = hw.plan_wizard_hole(_callout(), through_all=True, depth_m=None, unit="in")
        args = p.as_hole_wizard5_args()
        assert len(args) == 27
        assert isinstance(args[0], int) and isinstance(args[1], int) and isinstance(args[2], int)
        assert args[3] == "" and args[23 - 1] is not None  # SSize is a string


# --------------------------------------------------------------------------- #
# Callout-vs-count reconciliation (Phase 3d) — pure + wired
# --------------------------------------------------------------------------- #
class TestReconcileCount:
    def test_agreement_returns_none(self):
        assert hw.reconcile_callout_count(6, 6, "H1") is None

    def test_unknown_side_returns_none(self):
        assert hw.reconcile_callout_count(None, 5, "H1") is None
        assert hw.reconcile_callout_count(6, 0, "H1") is None

    def test_conflict_is_critical_blocking(self):
        c = hw.reconcile_callout_count(6, 5, "H1")
        assert c is not None
        assert c["severity"] == "CRITICAL"
        assert c["blocking"] is True
        assert c["candidates"] == [6, 5]
        assert "6" in c["gate_question"] and "5" in c["gate_question"]


def _flange_5_vs_6() -> dict:
    """A050211E-shaped case: a (6) callout but only 5 dimensioned positions."""
    return {
        "part_number": "A050211E",
        "units": "inch",
        "confidence": 0.9,
        "dimensions": [
            {"id": "D001", "type": "linear", "value": 6.0, "unit": "inch", "applies_to": "length"},
            {"id": "D002", "type": "linear", "value": 6.0, "unit": "inch", "applies_to": "height"},
            {"id": "D003", "type": "linear", "value": 0.25, "unit": "inch", "applies_to": "thickness"},
            {"id": "D004", "type": "diameter", "value": 0.281, "unit": "inch", "applies_to": "hole_diameter"},
        ],
        "hole_callouts": [
            {"id": "H001", "type": "thru", "diameter": 0.281, "thru": True, "qty": 6,
             "instance_positions": [[1, 1], [3, 1], [5, 1], [1, 5], [5, 5]],  # only 5
             "feature_ref": "F002"},
        ],
        "features": [
            {"id": "F001", "type": "extrude_boss", "description": "flange",
             "related_dimensions": ["D001", "D002"], "depth_dimension_id": "D003",
             "position_known": True},
            {"id": "F002", "type": "hole", "description": "bolt holes",
             "related_dimensions": ["D004"], "parent_feature": "F001"},
        ],
        "build_order": ["F001", "F002"],
        "relationships": {},
    }


def _shell_and_sweep(tmp_path):
    from pipeline.macro_generator import generate_macro_package
    from pipeline.validator import format_verification_report, run_verification

    data = {
        "part_number": "COV-1", "units": "inch", "confidence": 0.9,
        "dimensions": [
            {"id": "D001", "type": "linear", "value": 4.0, "unit": "inch", "applies_to": "length"},
            {"id": "D002", "type": "linear", "value": 2.0, "unit": "inch", "applies_to": "width"},
            {"id": "D003", "type": "linear", "value": 1.0, "unit": "inch", "applies_to": "height"},
            {"id": "D004", "type": "linear", "value": 0.125, "unit": "inch", "applies_to": "thickness"},
        ],
        "features": [
            {"id": "F001", "type": "extrude_boss", "description": "box",
             "related_dimensions": ["D001", "D002"], "depth_dimension_id": "D003",
             "position_known": True},
            {"id": "F002", "type": "shell", "description": "hollow box",
             "related_dimensions": ["D004"]},
            {"id": "F003", "type": "sweep", "description": "swept rib"},
        ],
        "build_order": ["F001", "F002", "F003"],
        "relationships": {},
    }
    model, report = run_verification(data)
    pkg = generate_macro_package(model, data, format_verification_report(model, report), tmp_path)
    return pkg


class TestFeatureCoverageBothPaths:
    def test_shell_emits_real_insert_feature_shell(self, tmp_path):
        pkg = _shell_and_sweep(tmp_path)
        files = list(pkg.macros_dir.glob("*F002*.vba"))
        assert files, "expected a shell macro for F002"
        text = files[0].read_text()
        assert "InsertFeatureShell" in text  # REAL shell call, not a MANUAL step

    def test_sweep_emits_needs_review_skeleton(self, tmp_path):
        pkg = _shell_and_sweep(tmp_path)
        files = list(pkg.macros_dir.glob("*F003*.vba"))
        assert files, "expected a sweep macro for F003"
        text = files[0].read_text()
        # Real-or-skeleton: no fabricated geometry, a manual-modeling instruction.
        assert "manual modeling" in text.lower()
        assert "InsertProtrusionSwept" not in text  # never guessed a path

    def test_com_builders_registered_for_new_types(self):
        from pipeline import solidworks_builder as sb
        from pipeline.schema import FeatureType

        for ft in (FeatureType.SHELL, FeatureType.SWEEP, FeatureType.LOFT,
                   FeatureType.RIB, FeatureType.DRAFT):
            assert ft in sb._BUILDERS, f"{ft} missing a COM builder"


class TestCalloutCountEscalation:
    def test_resolver_raises_critical_flag(self):
        res = resolve_extraction(_flange_5_vs_6())
        conflict = [f for f in res.flags if f.get("source") == "callout_vs_count"]
        assert conflict, "expected a callout_vs_count flag"
        f = conflict[0]
        assert f["flag_tier"] == "CRITICAL"
        assert f["dimension_id"] == "H001"
        assert f.get("candidates") == [6, 5]

    def test_no_flag_when_counts_agree(self):
        data = _flange_5_vs_6()
        data["hole_callouts"][0]["qty"] = 5  # now matches the 5 positions
        res = resolve_extraction(data)
        assert not [f for f in res.flags if f.get("source") == "callout_vs_count"]

    def test_conflict_routes_into_assist_queue(self, tmp_path):
        from pipeline.human_assist import generate_assist_queue

        model = DrawingData(**_flange_5_vs_6())
        res = resolve_extraction(_flange_5_vs_6())
        queue = generate_assist_queue(
            part="A050211E", part_dir=tmp_path, safe_name="A050211E",
            model=model, resolution=res, cap=5)
        q = [x for x in queue.questions if x.feature_id == "H001"]
        assert q, "the 5-vs-6 conflict should surface as an assist question"
        assert "6" in q[0].question_text and "5" in q[0].question_text
