"""Unit tests for Stage 2.4 DWG native cross-check. No SolidWorks required
(the cross-check logic is pure; the SolidWorks import is exercised live, not here).
"""
from __future__ import annotations

import pytest

from pipeline.dwg_crosscheck import (DwgGroundTruth, crosscheck_and_correct,
                                     is_dwg, run_dwg_crosscheck)


def _gt(values):
    return DwgGroundTruth(dwg_path="X.dwg", units="inch", exact_values=sorted(values),
                          available=True, note="test")


def test_is_dwg():
    assert is_dwg("A050381E.DWG") and is_dwg("x.dwg")
    assert not is_dwg("x.pdf") and not is_dwg("x.dxf")


def test_confirms_exact_ocr_reading():
    data = {"dimensions": [{"id": "D1", "value": 5.25}]}
    gt = _gt([5.25, 4.25, 0.38])
    rep = crosscheck_and_correct(data, gt)
    assert len(rep["confirmed"]) == 1 and not rep["corrected"]
    assert data["dimensions"][0]["value"] == 5.25   # unchanged


def test_corrects_ocr_misread_to_exact():
    # OCR misread 16.00 as 16.80; the DWG has 16.00 -> snap to exact.
    data = {"dimensions": [{"id": "D1", "value": 16.80, "value_unclear": True, "notes": ""}]}
    gt = _gt([16.00, 5.25])
    rep = crosscheck_and_correct(data, gt)
    assert len(rep["corrected"]) == 1
    c = rep["corrected"][0]
    assert c["ocr"] == 16.80 and c["dwg"] == 16.00
    assert data["dimensions"][0]["value"] == 16.00
    assert data["dimensions"][0]["value_unclear"] is False
    assert "DWG-verified" in data["dimensions"][0]["notes"]


def test_leaves_unverified_when_no_close_exact_value():
    data = {"dimensions": [{"id": "D1", "value": 3.00}]}
    gt = _gt([19.25, 48.00])          # nothing near 3.00
    rep = crosscheck_and_correct(data, gt)
    assert len(rep["unverified"]) == 1 and not rep["corrected"]
    assert data["dimensions"][0]["value"] == 3.00   # never fabricated


def test_hole_diameter_corrected_from_geometry():
    data = {"hole_callouts": [{"id": "H1", "diameter": 0.40}]}
    gt = _gt([0.38, 5.25])            # exact circle diameter is .38
    rep = crosscheck_and_correct(data, gt)
    assert data["hole_callouts"][0]["diameter"] == 0.38
    assert any(c["kind"] == "hole_diameter" for c in rep["corrected"])


def test_reports_dwg_only_values_as_possible_ocr_miss():
    data = {"dimensions": [{"id": "D1", "value": 5.25}]}
    gt = _gt([5.25, 2.00])            # 2.00 has no OCR counterpart
    rep = crosscheck_and_correct(data, gt)
    assert 2.00 in rep["dwg_only"]


def test_run_crosscheck_noops_on_non_dwg():
    data = {"dimensions": [{"id": "D1", "value": 1.0}]}
    out, rep = run_dwg_crosscheck("x.pdf", data, ".", "part")
    assert out is data and rep is None


def test_run_crosscheck_graceful_when_ground_truth_unavailable(tmp_path, monkeypatch):
    # Force get_ground_truth to report unavailable (simulates no SolidWorks).
    import pipeline.dwg_crosscheck as m
    monkeypatch.setattr(m, "get_ground_truth",
                        lambda *a, **k: DwgGroundTruth(dwg_path="x.dwg", available=False,
                                                       note="no SolidWorks"))
    data = {"dimensions": [{"id": "D1", "value": 1.0}]}
    out, rep = run_dwg_crosscheck("x.dwg", data, str(tmp_path), "part")
    assert rep["skipped"] == "no SolidWorks"
    assert out is data   # unchanged, never blocks
