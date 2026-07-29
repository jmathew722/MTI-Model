"""2026-07-28: a partial-arc (<360deg) circular bolt pattern's baked seed-hole
layout (macro_generator._circular_positions, step = arc/(qty-1), a hole at BOTH
ends) is NOT independently verified to match how SolidWorks' EqualSpacing
divides the same total angle across pattern instances for a partial arc (the
audit's H4 finding) — and the fix touches four independent geometry consumers
(VBA, C#, the COM builder, and CadQuery's polarArray), each with its own
angle-division convention, none of which have been live-verified this session.
Rather than guess at a geometric change across all four, the emitted VBA now
carries an explicit, unmissable comment for a human to verify partial-arc
instance angles against the drawing. This is purely additive (a comment only)
— zero effect on any emitted API call argument or build-plan value."""
from __future__ import annotations

from pipeline.macro_generator import _macro_circular_pattern
from pipeline.schema import Feature, FeatureType


def _feature():
    return Feature(id="F005", type=FeatureType.PATTERN, description="bolt pattern",
                   related_dimensions=[])


def _spec(total_angle_deg):
    return {
        "pattern_axis": {"axis_name": "PatternAxis1"},
        "seed_feature_name": "F005_SeedHoleCut",
        "total_instances": 4,
        "bolt_circle_radius_in": 1.5,
        "seed_angle_deg": 0.0,
        "total_angle_deg": total_angle_deg,
        "reverse_direction": False,
        "geometry_pattern": False,
        "vary_sketch": False,
    }


def test_partial_arc_carries_verification_comment():
    vba = _macro_circular_pattern(_spec(90.0), _feature(), "05_F005_pattern")
    assert "PARTIAL ARC" in vba
    assert "VERIFY instance" in vba


def test_full_circle_carries_no_verification_comment():
    # The well-tested, already-working 360deg case must be BYTE-IDENTICAL to
    # before this change — no new comment, no behavior change.
    vba = _macro_circular_pattern(_spec(360.0), _feature(), "05_F005_pattern")
    assert "PARTIAL ARC" not in vba
    assert "VERIFY instance" not in vba


def test_partial_arc_note_does_not_alter_the_actual_pattern_call_arguments():
    # The fix is comment-only: the CreateCircularPatternSafe call itself (the
    # actual geometry-driving arguments) must be identical whether or not the
    # note is present.
    vba_partial = _macro_circular_pattern(_spec(90.0), _feature(), "05_F005_pattern")
    vba_full = _macro_circular_pattern(_spec(360.0), _feature(), "05_F005_pattern")
    call_partial = next(l for l in vba_partial.splitlines() if "CreateCircularPatternSafe(" in l)
    call_full = next(l for l in vba_full.splitlines() if "CreateCircularPatternSafe(" in l)
    # Same call shape (only the numeric total-angle argument legitimately differs).
    assert call_partial.split("90")[0] == call_full.split("360")[0]
