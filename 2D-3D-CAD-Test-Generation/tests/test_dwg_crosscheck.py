"""Unit tests for Stage 2.4 DWG native cross-check. No SolidWorks required
(the cross-check logic is pure; the SolidWorks import is exercised live, not here).

Covers the three-tier, kind-separated, one-to-one matching that replaced the old
flat 0.15" snap pool (which could corrupt correct readings, e.g. .25 -> .38).
"""
from __future__ import annotations

import pytest

from pipeline.dwg_crosscheck import (DwgGroundTruth, crosscheck_and_correct,
                                     is_dwg, run_dwg_crosscheck)


def _gt(length=None, diameter=None):
    length = sorted(set(length or []))
    diameter = sorted(set(diameter or []))
    return DwgGroundTruth(dwg_path="X.dwg", units="inch",
                          length_values=length, diameter_values=diameter,
                          exact_values=sorted(set(length) | set(diameter)),
                          available=True, note="test")


def test_is_dwg():
    assert is_dwg("A050381E.DWG") and is_dwg("x.dwg")
    assert not is_dwg("x.pdf") and not is_dwg("x.dxf")


def test_confirms_exact_ocr_reading():
    data = {"dimensions": [{"id": "D1", "value": 5.25, "value_unclear": True}]}
    rep = crosscheck_and_correct(data, _gt(length=[5.25, 4.25]))
    assert len(rep["confirmed"]) == 1 and not rep["corrected"]
    assert data["dimensions"][0]["value"] == 5.25          # unchanged
    assert data["dimensions"][0]["value_unclear"] is False  # confirmed -> certain


def test_auto_corrects_tiny_misread():
    # 5.24 vs exact 5.25 (0.01, within AUTO_CORRECT_TOL) -> snap, but stays flagged.
    data = {"dimensions": [{"id": "D1", "value": 5.24, "value_unclear": True, "notes": ""}]}
    rep = crosscheck_and_correct(data, _gt(length=[5.25]))
    assert len(rep["corrected"]) == 1
    assert data["dimensions"][0]["value"] == 5.25
    assert data["dimensions"][0]["value_unclear"] is True   # NOT cleared on a correction
    assert "DWG-verified" in data["dimensions"][0]["notes"]


def test_larger_mismatch_flags_discrepancy_without_changing_value():
    # 5.60 vs 5.25 (0.35: >auto, <=discrepancy) -> FLAG, do not silently change.
    data = {"dimensions": [{"id": "D1", "value": 5.60, "notes": ""}]}
    rep = crosscheck_and_correct(data, _gt(length=[5.25]))
    assert len(rep["discrepancies"]) == 1 and not rep["corrected"]
    assert data["dimensions"][0]["value"] == 5.60           # NOT changed
    assert data["dimensions"][0]["value_unclear"] is True
    assert 5.25 in data["dimensions"][0]["possible_values"]


def test_length_never_snaps_to_a_diameter():
    # The old bug: a length .40 snapping to a hole diameter .38. Kind separation
    # forbids it — with no length values, it is unverified, not corrected.
    data = {"dimensions": [{"id": "D1", "value": 0.40}]}
    rep = crosscheck_and_correct(data, _gt(diameter=[0.38]))
    assert not rep["corrected"] and not rep["discrepancies"]
    assert data["dimensions"][0]["value"] == 0.40


def test_ambiguous_two_near_values_left_unverified():
    # 0.25 sits between 0.24 and 0.26 -> not unambiguous -> never snapped.
    data = {"dimensions": [{"id": "D1", "value": 0.25}]}
    rep = crosscheck_and_correct(data, _gt(length=[0.24, 0.26]))
    assert not rep["corrected"] and data["dimensions"][0]["value"] == 0.25
    assert any(u["id"] == "D1" for u in rep["unverified"])


def test_hole_diameter_auto_corrected_from_geometry():
    data = {"hole_callouts": [{"id": "H1", "diameter": 0.39}]}
    rep = crosscheck_and_correct(data, _gt(diameter=[0.38]))
    assert data["hole_callouts"][0]["diameter"] == 0.38
    assert any(c["kind"] == "hole_diameter" for c in rep["corrected"])


def test_one_to_one_second_value_not_double_claimed():
    # Two OCR values near the SAME single exact value: only the closest corrects.
    data = {"dimensions": [{"id": "D1", "value": 5.26}, {"id": "D2", "value": 5.27}]}
    rep = crosscheck_and_correct(data, _gt(length=[5.25]))
    corrected_ids = {c["id"] for c in rep["corrected"]}
    assert len(corrected_ids) == 1        # only one may claim 5.25


def test_leaves_unverified_when_no_close_exact_value():
    data = {"dimensions": [{"id": "D1", "value": 3.00}]}
    rep = crosscheck_and_correct(data, _gt(length=[19.25, 48.00]))
    assert len(rep["unverified"]) == 1 and not rep["corrected"]
    assert data["dimensions"][0]["value"] == 3.00


def test_reports_dwg_only_values_as_possible_ocr_miss():
    data = {"dimensions": [{"id": "D1", "value": 5.25}]}
    rep = crosscheck_and_correct(data, _gt(length=[5.25, 2.00]))
    assert 2.00 in rep["dwg_only"]


def test_run_crosscheck_noops_on_non_dwg():
    data = {"dimensions": [{"id": "D1", "value": 1.0}]}
    out, rep = run_dwg_crosscheck("x.pdf", data, ".", "part")
    assert out is data and rep is None


def test_run_crosscheck_graceful_when_ground_truth_unavailable(tmp_path, monkeypatch):
    import pipeline.dwg_crosscheck as m
    monkeypatch.setattr(m, "get_ground_truth",
                        lambda *a, **k: DwgGroundTruth(dwg_path="x.dwg", available=False,
                                                       note="no SolidWorks"))
    data = {"dimensions": [{"id": "D1", "value": 1.0}]}
    out, rep = run_dwg_crosscheck("x.dwg", data, str(tmp_path), "part")
    assert rep["skipped"] == "no SolidWorks"
    assert out is data
