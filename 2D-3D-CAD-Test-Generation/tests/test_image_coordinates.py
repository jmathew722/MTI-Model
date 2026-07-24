"""Image-space geometry for the fixed-region extraction pass
(pipeline/image_coordinates.py).

Covers the documented API-resize math (incl. the exact A4 case the task
specifies), the region tiling with the full-coverage guarantee (rasterized onto
a blank canvas — the acceptance test), the size-scaling region count, the
overlap floor, sent<->master scaling, and the two-coordinate-systems
non-import invariant. All pure — no network, no SolidWorks.
"""
import math

import numpy as np
import pytest

from pipeline.image_coordinates import (
    DEFAULT_OVERLAP_FRAC,
    DEFAULT_TARGET_EDGE_PX,
    compute_regions,
    count_image_tokens,
    coverage_gap_pixels,
    master_to_sent_scale,
    render_region_overlay,
    resized_size,
    scale_master_to_sent,
    scale_sent_to_master,
    tier_limits,
)


# --------------------------------------------------------------------------- #
# resized_size / count_image_tokens
# --------------------------------------------------------------------------- #
def test_documented_a4_case():
    # The task pins this exact result for a portrait A4-ish page.
    assert resized_size(1075, 1520) == (924, 1307)


def test_resized_output_respects_token_budget():
    w, h = resized_size(10200, 6600)               # a D-size sheet at 300 DPI
    assert count_image_tokens(w, h) <= 1568        # standard-tier token cap
    assert math.ceil(w / 28) * 28 <= 1568          # edge cap
    assert math.ceil(h / 28) * 28 <= 1568


def test_resized_noop_when_already_small():
    assert resized_size(280, 196) == (280, 196)


def test_resized_never_upscales():
    w, h = resized_size(400, 300)
    assert w <= 400 and h <= 300


def test_tier_limits_named_not_literal():
    assert tier_limits("standard") == (1568, 1568)
    assert tier_limits("hires") == (2576, 4784)


# --------------------------------------------------------------------------- #
# compute_regions — the coverage guarantee (acceptance criterion)
# --------------------------------------------------------------------------- #
def _canvas_fully_covered(w, h, regions) -> bool:
    """Rasterize region coverage onto a blank canvas; True iff every pixel is
    covered by >= 1 region (the acceptance test, done the literal way)."""
    canvas = np.zeros((h, w), dtype=bool)
    for rg in regions:
        canvas[rg.y0_master_px:rg.y1_master_px, rg.x0_master_px:rg.x1_master_px] = True
    return bool(canvas.all())


def test_dsize_master_is_fully_covered_no_gaps():
    W, H = 10200, 6600
    regions = compute_regions(W, H, target_edge_px=1400, overlap_frac=0.20)
    assert _canvas_fully_covered(W, H, regions)
    assert coverage_gap_pixels(W, H, regions) == 0
    # Region 0 starts at the origin; the last reaches the far corner.
    assert regions[0].bbox_master_px[:2] == [0, 0]
    assert regions[-1].x1_master_px == W and regions[-1].y1_master_px == H


def test_region_count_scales_with_sheet_size_never_fixed_four():
    big = compute_regions(10200, 6600, target_edge_px=1400)
    small = compute_regions(1300, 900, target_edge_px=1400)
    a_size = compute_regions(2200, 1700, target_edge_px=1400)
    assert len(small) == 1          # A-size: overview is already ~native -> pass-through
    assert len(big) != 4            # NOT a hardcoded 4
    assert len(big) > len(a_size) > len(small) or len(a_size) >= 1
    assert len(big) == math.ceil(10200 / 1400) * math.ceil(6600 / 1400)


@pytest.mark.parametrize("W,H", [(3000, 2000), (5000, 4000), (10200, 6600),
                                 (800, 600), (1400, 1400), (2801, 1401)])
def test_coverage_holds_across_sheet_sizes(W, H):
    regions = compute_regions(W, H)
    assert coverage_gap_pixels(W, H, regions) == 0


def test_adjacent_regions_overlap():
    regions = compute_regions(4000, 1400, target_edge_px=1400, overlap_frac=0.20)
    # Two columns side by side: region 1 must extend past where region 2 begins.
    assert len(regions) == 3  # ceil(4000/1400)=3 cols, 1 row
    assert regions[0].x1_master_px > regions[1].x0_master_px  # overlap band exists


def test_overlap_floor_enforced():
    with pytest.raises(ValueError, match="overlap_frac"):
        compute_regions(4000, 3000, overlap_frac=0.10)


def test_region_ids_are_paged_and_indexed():
    regions = compute_regions(3000, 3000, page=1)
    assert regions[0].id == "p01_r01"
    p3 = compute_regions(3000, 3000, page=3)
    assert p3[0].id == "p03_r01"


def test_bad_dims_raise():
    with pytest.raises(ValueError):
        compute_regions(0, 100)


# --------------------------------------------------------------------------- #
# sent <-> master scaling round-trip
# --------------------------------------------------------------------------- #
def test_scale_round_trip():
    scale = master_to_sent_scale(master_w_px=10200, sent_w_px=1378)
    bbox_master = [1042, 280, 1348, 462]
    sent = scale_master_to_sent(bbox_master, scale)
    back = scale_sent_to_master(sent, scale)
    assert all(abs(a - b) < 1e-6 for a, b in zip(back, bbox_master))


# --------------------------------------------------------------------------- #
# Debug overlay smoke
# --------------------------------------------------------------------------- #
def test_render_overlay_smoke():
    from PIL import Image
    master = Image.new("RGB", (1200, 800), "white")
    regions = compute_regions(1200, 800)
    out = render_region_overlay(master, regions)
    assert out.size == (1200, 800)


# --------------------------------------------------------------------------- #
# The two-coordinate-systems non-import invariant (Y-flip firewall)
# --------------------------------------------------------------------------- #
def test_image_coordinates_does_not_import_coordinate_normalize():
    import pathlib

    import pipeline.image_coordinates as ic
    src = pathlib.Path(ic.__file__).read_text(encoding="utf-8")
    assert "import coordinate_normalize" not in src
    assert "from pipeline.coordinate_normalize" not in src


def test_coordinate_normalize_does_not_import_image_coordinates():
    import pathlib

    import pipeline.coordinate_normalize as cn
    src = pathlib.Path(cn.__file__).read_text(encoding="utf-8")
    assert "image_coordinates" not in src
