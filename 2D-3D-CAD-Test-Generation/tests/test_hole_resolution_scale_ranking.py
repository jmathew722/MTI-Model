"""Regression tests for the scale-consensus ranking fix (2026-07-28).

_consensus_scale used to pick the LARGEST cluster of agreeing anchors by raw
count, with no regard to whether the cluster contained an EXACT anchor (declared
native units, or outline-vs-envelope — both derived from a real dimension) or
was purely a coincidental diameter-ratio vote (fit-error-prone, and on a sheet
with many equal-diameter holes across multiple views, can simply out-number the
one genuine exact cluster). A spurious diameter-vote cluster winning meant every
hole position built from it reported at up to 0.95-0.97 confidence though the
scale itself was never actually confirmed by a dimension.
"""
from __future__ import annotations

from pipeline.hole_resolution import _consensus_scale


def test_exact_cluster_wins_even_when_smaller_than_diameter_cluster():
    """5 diameter-vote anchors coincidentally agree on scale=2.0 (a spurious
    cluster); only 2 anchors carry the real dimension-derived scale=1.0
    (declared units + outline). The exact cluster must win despite being
    numerically smaller — this is exactly the failure the audit described."""
    anchors = (
        [(2.0, f"diam.25/r{i}") for i in range(5)]     # 5-strong spurious cluster
        + [(1.0, "declared-units"), (1.0, "declared-units-2"),  # 2-strong exact cluster
           (1.0, "outline-length:tight")]
    )
    scale, n_agree, exact_backed = _consensus_scale(anchors)
    assert scale == 1.0
    assert exact_backed is True


def test_diameter_only_cluster_wins_when_no_exact_anchor_exists():
    """With NO exact anchor anywhere, the largest diameter-vote cluster is used
    as a fallback (never blocks) but is reported as NOT exact-backed."""
    anchors = [(2.0, f"diam.25/r{i}") for i in range(4)] + [(3.0, "diam.5/r9")]
    scale, n_agree, exact_backed = _consensus_scale(anchors)
    assert scale == 2.0 and n_agree == 4
    assert exact_backed is False


def test_no_anchors_returns_zero():
    scale, n_agree, exact_backed = _consensus_scale([])
    assert scale == 0.0 and n_agree == 0 and exact_backed is False


def test_exact_only_cluster_averages_exact_values_ignoring_diameter_noise():
    # Exact cluster contains 1.0 and 1.001 (tiny real-world dimension rounding);
    # diameter votes that happen to also cluster near there must not perturb it.
    anchors = [(1.0, "declared-units"), (1.001, "outline-length:tight"),
              (0.999, "diam.25/r1")]
    scale, n_agree, exact_backed = _consensus_scale(anchors)
    assert exact_backed is True
    assert abs(scale - 1.0005) < 1e-6   # average of the two EXACT values only
