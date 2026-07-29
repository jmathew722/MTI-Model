"""Regression tests for the DWG-native BUILD path against the SolidWorks macro
error ledger (docs/solidworks-macro-error-log.md) and the verification gates.
No SolidWorks required — these check the emitted VBA + the pure verify logic.
"""
from __future__ import annotations

import pytest

from dwg_native.build.vba_emit import emit_vba
from dwg_native.verify import verify_build
from pipeline.macro_audit import audit_text


def _plan():
    return {"part": "P", "unit_factor_to_meters": 0.0254, "steps": [
        {"type": "extrude_boss", "feature_id": "F001",
         "dimensions_drawing_units": {"length": 4, "width": 2, "thickness": 0.25},
         "positions_xy": [[0, 0]]},
        {"type": "hole", "feature_id": "F101",
         "dimensions_drawing_units": {"diameter": 0.5}, "positions_xy": [[1, 1]]},
    ]}


def test_emitted_vba_passes_static_audit():
    vba = emit_vba(_plan(), "P.SLDPRT")
    findings = audit_text("build.vba", vba)
    assert findings == [], f"emitted VBA tripped the auditor: {findings}"


def test_vba_and_com_paths_sketch_holes_on_the_same_plane():
    """2026-07-28 fix: the VBA emitter used to sketch a hole on a FACE selected
    by an (x,y,z) coordinate hit at z=thickness, while the COM builder
    (dwg_native/build/builder.py, live-verified) sketches on "Front Plane" at
    z=0 for every feature — a genuine cross-path divergence where a user
    running the VBA macro could get different geometry than the automated
    build. Both paths must now select "Front Plane" for every feature,
    matching the COM builder's live-proven approach (not the fragile
    coordinate-hit face selection)."""
    vba = emit_vba(_plan(), "P.SLDPRT")
    # Both the base extrude AND the hole cut select the plane by name, never a
    # coordinate-hit FACE selection.
    assert vba.count('SelectByID2 "Front Plane", "PLANE"') == 2
    assert '"FACE"' not in vba


def test_e004_no_invented_model_bounding_box_api():
    # E004: never IModelDoc2.GetModelBoundingBox; use IBody2.GetBodyBox.
    vba = emit_vba(_plan(), "P.SLDPRT")
    assert "GetModelBoundingBox" not in vba
    assert "GetBodyBox" in vba


def test_e006_sketch_consumed_active_not_reselected_by_name():
    # E006: never re-select a closed sketch by name; consume the ACTIVE sketch.
    vba = emit_vba(_plan(), "P.SLDPRT")
    assert 'SelectByID2' not in vba or '"SKETCH"' not in vba
    assert vba.count("InsertSketch True") >= 4  # open+close per sketch, consumed active


def test_verified_calls_present():
    vba = emit_vba(_plan(), "P.SLDPRT")
    assert "FeatureExtrusion3" in vba and "FeatureCut4" in vba


# ---- verification gates --------------------------------------------------- #
def _good_result():
    return {"features": [{"feature_id": "F001", "type": "extrude_boss", "status": "PASS"},
                         {"feature_id": "F101", "type": "hole", "status": "PASS"}],
            "fully_defined": [{"feature_id": "F001", "status": "fully"},
                              {"feature_id": "F101", "status": "fully"}],
            "body_box_m": [0, 0, 0, 4 * 0.0254, 2 * 0.0254, 0.25 * 0.0254]}


def test_verify_all_gates_pass_on_good_build():
    rep = verify_build(_plan(), _good_result())
    assert rep.passed
    assert {c["check"] for c in rep.checks} == {
        "rebuild", "fully_defined", "dimension_roundtrip", "feature_count", "bounding_box"}


def test_verify_underdefined_is_advisory_not_blocking():
    # Programmatic coordinate-drawn sketches read under-defined but are pinned by
    # the exact coordinates — advisory, must not gate (repo guiding principle).
    br = _good_result()
    br["fully_defined"][0]["status"] = "under"
    rep = verify_build(_plan(), br)
    assert rep.passed
    fd = next(c for c in rep.checks if c["check"] == "fully_defined")
    assert fd["status"] == "PASS"


def test_verify_fails_on_overdefined_sketch():
    br = _good_result()
    br["fully_defined"][0]["status"] = "over"
    rep = verify_build(_plan(), br)
    assert not rep.passed and rep.failing_check == "fully_defined"


def test_verify_fails_on_dimension_roundtrip_mismatch():
    br = _good_result()
    br["body_box_m"] = [0, 0, 0, 9 * 0.0254, 2 * 0.0254, 0.25 * 0.0254]  # wrong length
    rep = verify_build(_plan(), br)
    assert not rep.passed and rep.failing_check == "dimension_roundtrip"


def test_verify_fails_on_missing_hole():
    br = _good_result()
    br["features"] = [f for f in br["features"] if f["type"] != "hole"]
    rep = verify_build(_plan(), br)
    assert not rep.passed and rep.failing_check in ("feature_count",)
