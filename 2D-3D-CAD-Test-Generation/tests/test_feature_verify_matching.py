"""Direct unit tests for the hole-matching assignment in pipeline.feature_verify
(2026-07-28 hardening): greedy nearest-first matching could let an EARLIER
expected hole steal the measured hole a LATER expected hole actually needed,
producing spurious MISPLACED/MISSING/EXTRA classifications. The fix uses a
global optimal (Hungarian) assignment. No CadQuery/mesh required — these test
the assignment logic in isolation."""
from __future__ import annotations

import pytest

scipy = pytest.importorskip("scipy")

from pipeline.feature_verify import MISPLACED, MISSING, OK, _assign_holes, _match_holes


def _hole(fid, x, y, dia=0.25, through=True):
    return {"feature_id": fid, "type": "hole", "x": x, "y": y,
            "diameter": dia, "through": through}


def test_optimal_assignment_beats_greedy_stealing():
    """A mathematically-constructed adversarial case: E1's NEAREST measured hole
    (M1, d=0.3) is also, on its own, a fine match — a greedy nearest-first scan
    grabs it for E1 immediately. But the GLOBALLY cheaper total pairing is
    E1<->M2 (d=0.5) and E2<->M1 (d=0.4) (total 0.9) versus greedy's E1<->M1,
    E2<->M2 (total 1.2, because E2's only remaining option M2 is d=0.9 away).
    With pos_tol=0.6, greedy's leftover pairing MISPLACES E2 (0.9 > 0.6) even
    though a valid within-tolerance assignment exists; the optimal (Hungarian)
    assignment must find it."""
    expected = [_hole("F101", 0.0, 0.0), _hole("F102", 0.6931, -0.0741)]
    measured = [_hole("m0", 0.3, 0.0), _hole("m1", 0.0, 0.5)]
    assignment = _assign_holes(expected, measured)
    # Optimal: E1(idx0)->M2(idx1), E2(idx1)->M1(idx0) — the swap, not the greedy pick.
    assert assignment[0][0] == 1 and assignment[1][0] == 0

    results, extras = _match_holes(expected, measured, pos_tol=0.6, dia_tol=0.02)
    by_id = {r["feature_id"]: r for r in results}
    assert by_id["F101"]["classification"] == OK
    assert by_id["F102"]["classification"] == OK
    assert not extras


def test_missing_and_extra_still_detected():
    expected = [_hole("F101", 0.0, 0.0), _hole("F102", 5.0, 0.0)]
    measured = [_hole("m0", 0.0, 0.0)]  # F102's hole genuinely absent
    results, extras = _match_holes(expected, measured, pos_tol=0.02, dia_tol=0.02)
    by_id = {r["feature_id"]: r for r in results}
    assert by_id["F101"]["classification"] == OK
    assert by_id["F102"]["classification"] == MISSING
    assert not extras


def test_extra_hole_reported_when_unassigned():
    expected = [_hole("F101", 0.0, 0.0)]
    measured = [_hole("m0", 0.0, 0.0), _hole("m1", 10.0, 10.0)]  # stray extra hole
    results, extras = _match_holes(expected, measured, pos_tol=0.02, dia_tol=0.02)
    assert results[0]["classification"] == OK
    assert len(extras) == 1


def test_far_pair_not_forced_into_assignment():
    # A single expected/measured pair farther than MISPLACED_SEARCH_IN (1.5") must
    # NOT be matched at all — MISSING + EXTRA, not a bogus MISPLACED.
    expected = [_hole("F101", 0.0, 0.0)]
    measured = [_hole("m0", 5.0, 5.0)]
    results, extras = _match_holes(expected, measured, pos_tol=0.02, dia_tol=0.02)
    assert results[0]["classification"] == MISSING
    assert len(extras) == 1
