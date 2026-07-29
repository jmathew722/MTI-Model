"""2026-07-28 fix: pipeline.resolver._spec_match used to try inch<->mm
conversions on EVERY spec value unconditionally, so a spec line with no stated
unit could match a candidate purely by numeric coincidence (drawing candidate
25.4, spec value "1" -> 1*25.4 = 25.4, locked in as spec-driven HIGH confidence
though the spec never said mm). Conversions are now only attempted when the
spec TEXT explicitly names a unit that contradicts the drawing's units.
"""
from __future__ import annotations

from pipeline.resolver import _spec_line_unit_hint, _spec_match


def test_no_stated_unit_only_matches_directly_no_coincidental_conversion():
    # Spec says "1" (no unit) on an inch drawing; a candidate of 25.4 must NOT
    # match via the old 1*25.4 coincidence.
    result = _spec_match([25.4], [(1.0, "the bore must be at least 1")], drawing_units="inch")
    assert result is None


def test_direct_same_unit_match_still_works():
    result = _spec_match([1.25], [(1.25, "bore diameter 1.25")], drawing_units="inch")
    assert result == (1.25, "bore diameter 1.25")


def test_explicit_mm_on_inch_drawing_converts():
    # Spec explicitly states 38mm; drawing is inches; candidate 1.496 (~38/25.4).
    result = _spec_match([1.496], [(38.0, "hole must be 38mm minimum")], drawing_units="inch")
    assert result is not None and abs(result[0] - 1.496) < 1e-6


def test_explicit_inch_on_mm_drawing_converts():
    result = _spec_match([25.4], [(1.0, 'must be 1" minimum')], drawing_units="mm")
    assert result is not None and abs(result[0] - 25.4) < 1e-6


def test_unit_hint_detection():
    assert _spec_line_unit_hint("must be 38mm") == "mm"
    assert _spec_line_unit_hint("millimeter tolerance") == "mm"
    assert _spec_line_unit_hint('at least 1" thick') == "in"
    assert _spec_line_unit_hint("must be 1 inch") == "in"
    assert _spec_line_unit_hint("the bore must be at least 1") is None


def test_bare_in_preposition_is_not_mistaken_for_the_inch_unit():
    # "in" is an ordinary English preposition and must NEVER trigger a unit
    # hint on its own — only unambiguous signals (spelled "inch", or a digit
    # directly followed by the drafting " symbol) count.
    assert _spec_line_unit_hint("the hole in the plate must be 0.25") is None
    assert _spec_line_unit_hint("verify in SolidWorks before rebuild") is None
    assert _spec_line_unit_hint("1 in the corner") is None
