"""2026-07-28 fix: pipeline.build_sequencer._feature_dim_values used to pick the
LARGER value on a canonical-key collision (two of a feature's related
dimensions both reading as e.g. "length") — an arbitrary, unjustified rule that
could size a base/cut from the wrong dimension. The FIRST-declared dimension
(the drawing's own linking order) now wins instead of "biggest number wins".
"""
from __future__ import annotations

from pipeline.build_sequencer import _feature_dim_values
from pipeline.schema import DrawingData, Feature


def _model(dims):
    return DrawingData.model_validate({
        "units": "inch", "confidence": 0.9,
        "dimensions": dims,
        "features": [],
    })


def test_first_declared_dimension_wins_on_collision():
    # D001 (smaller, declared FIRST) and D002 (larger, declared SECOND) both
    # canonicalize to "length" — the first-declared D001 must win, not the
    # larger D002.
    m = _model([
        {"id": "D001", "type": "linear", "value": 3.0, "unit": "inch", "applies_to": "length"},
        {"id": "D002", "type": "linear", "value": 9.0, "unit": "inch", "applies_to": "overall length"},
    ])
    feat = Feature(id="F001", type="extrude_boss", description="base",
                   related_dimensions=["D001", "D002"])
    out = _feature_dim_values(m, feat)
    assert out["length"] == 3.0   # first-declared, NOT the larger 9.0


def test_reversed_declaration_order_still_takes_first():
    m = _model([
        {"id": "D001", "type": "linear", "value": 3.0, "unit": "inch", "applies_to": "length"},
        {"id": "D002", "type": "linear", "value": 9.0, "unit": "inch", "applies_to": "overall length"},
    ])
    feat = Feature(id="F001", type="extrude_boss", description="base",
                   related_dimensions=["D002", "D001"])   # D002 (larger) declared FIRST
    out = _feature_dim_values(m, feat)
    assert out["length"] == 9.0   # D002 is first here, so it wins — not "biggest"


def test_no_collision_unaffected():
    m = _model([
        {"id": "D001", "type": "linear", "value": 4.0, "unit": "inch", "applies_to": "length"},
        {"id": "D002", "type": "linear", "value": 2.0, "unit": "inch", "applies_to": "width"},
    ])
    feat = Feature(id="F001", type="extrude_boss", description="base",
                   related_dimensions=["D001", "D002"])
    out = _feature_dim_values(m, feat)
    assert out == {"length": 4.0, "width": 2.0}
