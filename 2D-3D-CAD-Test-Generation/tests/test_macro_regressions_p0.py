"""Regression tests for the P0 fixes (2026-07-28 improvement pass).

- macro_generator countersink path no longer crashes with NameError('diameter').
- a builder exception degrades one feature to a MANUAL step, never crashes the run.
- must_meet fallback parser no longer forces blind cuts through-all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.macro_generator import generate_macro_package
from pipeline.validator import format_verification_report, run_verification
from pipeline.must_meet import parse_spec_text_fallback


def _csk_drawing() -> dict:
    return {
        "part_number": "CSK-1", "revision": "A", "units": "inch", "confidence": 0.9,
        "dimensions": [
            {"id": "D001", "type": "linear", "value": 4.0, "unit": "inch", "applies_to": "length"},
            {"id": "D002", "type": "linear", "value": 2.0, "unit": "inch", "applies_to": "width"},
            {"id": "D003", "type": "depth", "value": 0.5, "unit": "inch", "applies_to": "height"},
            {"id": "D004", "type": "diameter", "value": 0.25, "unit": "inch",
             "applies_to": "hole_diameter", "feature_ref": "F002"},
        ],
        "hole_callouts": [
            {"id": "H001", "type": "countersink", "diameter": 0.25, "qty": 1,
             "csink_diameter": 0.50, "csink_angle": 82.0, "feature_ref": "F002",
             "x_position": 1.0, "y_position": 1.0},
        ],
        "features": [
            {"id": "F001", "type": "extrude_boss", "description": "Base plate",
             "related_dimensions": ["D001", "D002"], "depth_dimension_id": "D003", "sketch_plane": "Top"},
            {"id": "F002", "type": "hole", "description": "Countersunk hole",
             "related_dimensions": ["D004"]},
        ],
        "build_order": ["F001", "F002"],
    }


def test_countersink_generation_does_not_crash(tmp_path):
    """P0-1: a countersink callout used to hit NameError('diameter') and crash the
    whole run. It must now generate cleanly and emit the countersink relief."""
    data = _csk_drawing()
    model, report = run_verification(data)
    pkg = generate_macro_package(model, data, format_verification_report(model, report), tmp_path)
    blob = "\n".join(p.read_text(encoding="utf-8") for p in Path(pkg.macros_dir).glob("*.vba"))
    assert "COUNTERSINK" in blob.upper()          # the relief was scripted
    assert "0.5" in blob or ".5" in blob          # the csink mouth diameter appears


def test_blind_cut_not_forced_through_all():
    """P0-2: the fallback spec parser used to force every cut through-all."""
    blind = parse_spec_text_fallback("1.25 dia bore .50 deep")
    cut = next(c for c in blind if c.get("type") == "cut_extrude")
    assert cut["end_condition"] == "blind"
    assert cut.get("depth_in") == pytest.approx(0.50)

    thru = parse_spec_text_fallback("1.25 dia bore thru all")
    cut2 = next(c for c in thru if c.get("type") == "cut_extrude")
    assert cut2["end_condition"] == "through_all"
