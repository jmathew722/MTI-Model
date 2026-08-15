"""Image-space geometry for the fixed-region high-resolution extraction pass
(2026-07-24).

⚠ TWO COORDINATE SYSTEMS — DO NOT CROSS THEM ⚠
This module lives entirely in IMAGE PIXEL SPACE: origin TOP-LEFT, +X right,
+Y DOWN. It is a different, Y-inverted world from
:mod:`pipeline.coordinate_normalize`, which is the canonical 3D MODEL space
(origin lower-left, +X right, +Y up, +Z extrusion). A silent Y-flip between the
two builds a clean-but-wrong part — the failure mode most likely to slip past
testing. Therefore:

  * This module NEVER imports ``coordinate_normalize`` and
    ``coordinate_normalize`` never imports this. (Enforced by a test.)
  * Every pixel quantity is suffixed ``_px`` / ``_master_px`` / ``_sent_px``.
    Model-space millimetre quantities (``_model_mm``) never appear here.

What this module owns:
  * :func:`count_image_tokens` / :func:`resized_size` — the exact resize the
    Claude Vision API applies (edge + visual-token limits), so we know the
    scale factor between the master raster and the copy the model actually
    sees. Verified against platform.claude.com/docs/en/build-with-claude/vision.
  * :func:`compute_regions` — tile a master raster into OVERLAPPING regions
    sized to reach near-native model resolution; region count scales with sheet
    size (never a hardcoded 4).
  * :func:`scale_sent_to_master` / :func:`scale_master_to_sent` — the ONE place
    a bbox crosses between the sent (downsampled) copy and the master raster.
  * :func:`render_region_overlay` — debug: draw region boundaries + overlap
    zones on the master raster.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------------- #
# API resize tiers — NAMED CONFIG, not literals.
# The extraction model's tier (standard vs high-resolution) is NOT hardcoded as
# a magic number so a docs change is a one-line edit. Per
# platform.claude.com/docs/en/build-with-claude/vision: standard tier caps the
# long edge at 1568 px and the visual-token budget at 1568; the high-resolution
# tier raises these to 2576 / 4784. Sonnet 5 is NOT confirmed on the
# high-resolution tier as of 2026-07, so the extraction pass uses the STANDARD
# tier by default — flip EXTRACTION_TIER to "hires" here if/when confirmed.
MAX_EDGE_STD = 1568
MAX_TOKENS_STD = 1568
MAX_EDGE_HIRES = 2576
MAX_TOKENS_HIRES = 4784
_TOKEN_CELL = 28  # the model tiles the image into 28x28-px visual tokens

EXTRACTION_TIER = "standard"  # "standard" | "hires"


def tier_limits(tier: str = EXTRACTION_TIER) -> tuple[int, int]:
    """(max_edge_px, max_tokens) for the named tier."""
    if tier == "hires":
        return MAX_EDGE_HIRES, MAX_TOKENS_HIRES
    return MAX_EDGE_STD, MAX_TOKENS_STD


# --------------------------------------------------------------------------- #
# The model's own resize math (edge + visual-token limits)
# --------------------------------------------------------------------------- #
def count_image_tokens(width: int, height: int) -> int:
    """Visual tokens the API charges for a ``width`` x ``height`` image."""
    return math.ceil(width / _TOKEN_CELL) * math.ceil(height / _TOKEN_CELL)


def resized_size(width: int, height: int,
                 max_edge: int = MAX_EDGE_STD,
                 max_tokens: int = MAX_TOKENS_STD) -> tuple[int, int]:
    """The (w, h) the API resizes a ``width`` x ``height`` image down to, given
    the edge and visual-token limits. Mirrors the documented behavior so callers
    can compute the exact master->sent scale factor. Never upscales."""
    def fits(w: int, h: int) -> bool:
        return (math.ceil(w / _TOKEN_CELL) * _TOKEN_CELL <= max_edge
                and math.ceil(h / _TOKEN_CELL) * _TOKEN_CELL <= max_edge
                and count_image_tokens(w, h) <= max_tokens)

    if fits(width, height):
        return (width, height)
    if height > width:
        rh, rw = resized_size(height, width, max_edge, max_tokens)
        return (rw, rh)
    aspect = width / height
    lo, hi = 1, width
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect), 1))


# --------------------------------------------------------------------------- #
# Fixed-region tiling of the master raster
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Region:
    """One tile of the master raster, in MASTER pixel space (top-left origin)."""
    id: str
    x0_master_px: int
    y0_master_px: int
    x1_master_px: int
    y1_master_px: int

    @property
    def bbox_master_px(self) -> list[int]:
        return [self.x0_master_px, self.y0_master_px,
                self.x1_master_px, self.y1_master_px]

    @property
    def width_px(self) -> int:
        return self.x1_master_px - self.x0_master_px

    @property
    def height_px(self) -> int:
        return self.y1_master_px - self.y0_master_px


MIN_OVERLAP_FRAC = 0.15  # below this, a boundary feature can be clipped in every tile
DEFAULT_TARGET_EDGE_PX = 1400
DEFAULT_OVERLAP_FRAC = 0.20


def compute_regions(master_w_px: int, master_h_px: int,
                    target_edge_px: int = DEFAULT_TARGET_EDGE_PX,
                    overlap_frac: float = DEFAULT_OVERLAP_FRAC,
                    page: int = 1) -> list[Region]:
    """Tile the master raster into OVERLAPPING regions sized so each region,
    once resized for the API, lands near the model's native-resolution limit
    instead of being downsampled again.

    Region COUNT is a function of sheet size (``ceil(master/target_edge)`` cols
    and rows), never a fixed constant — a D-size sheet yields ~6-9 regions, an
    A-size sheet may yield 1 (the overview pass is already near native and
    Stage C becomes a fast pass-through). Each tile is padded by
    ``overlap_frac`` on all interior sides so any feature within that fraction
    of a boundary appears whole in at least one region. The union of the regions
    always covers the whole master with no gaps.

    Raises ValueError if ``overlap_frac`` < :data:`MIN_OVERLAP_FRAC` — a lower
    overlap can clip a boundary feature in every tile, the silent-loss failure
    this design exists to prevent.
    """
    if overlap_frac < MIN_OVERLAP_FRAC:
        raise ValueError(
            f"overlap_frac {overlap_frac} < {MIN_OVERLAP_FRAC}: too low — a feature on a "
            f"tile boundary could be clipped in every region (silent loss).")
    if master_w_px <= 0 or master_h_px <= 0:
        raise ValueError(f"master dims must be positive, got {master_w_px}x{master_h_px}")

    # The grid itself is planned by the shared high-res subsystem
    # (REFACTOR_ANALYSIS §1.4) — the same code the tiled zoom pass plans with,
    # so the two second-look systems cannot drift apart on coverage. The region
    # ID scheme and the overlap floor above stay this module's contract.
    from pipeline.highres_pass import assert_full_coverage, windows_by_division

    windows = windows_by_division(master_w_px, master_h_px, target_edge_px,
                                  overlap_frac)
    assert_full_coverage(windows, master_w_px, master_h_px)
    return [Region(id=f"p{page:02d}_r{idx:02d}",
                   x0_master_px=w.x0, y0_master_px=w.y0,
                   x1_master_px=w.x1, y1_master_px=w.y1)
            for idx, w in enumerate(windows, start=1)]


def coverage_gap_pixels(master_w_px: int, master_h_px: int,
                        regions: list[Region]) -> int:
    """Number of master pixels covered by NO region (0 = full coverage). Uses a
    per-column interval sweep — no numpy dependency — so the coverage guarantee
    is cheap to assert in production, not only in tests."""
    if not regions:
        return master_w_px * master_h_px
    uncovered = 0
    for x in range(master_w_px):
        spans = sorted((rg.y0_master_px, rg.y1_master_px) for rg in regions
                       if rg.x0_master_px <= x < rg.x1_master_px)
        cursor = 0
        for y0, y1 in spans:
            if y0 > cursor:
                uncovered += (min(y0, master_h_px) - cursor)
            cursor = max(cursor, min(y1, master_h_px))
            if cursor >= master_h_px:
                break
        if cursor < master_h_px:
            uncovered += master_h_px - cursor
    return uncovered


# --------------------------------------------------------------------------- #
# Sent <-> master bbox scaling (the ONE crossing point)
# --------------------------------------------------------------------------- #
def master_to_sent_scale(master_w_px: int, sent_w_px: int) -> float:
    """Scale factor mapping MASTER px to SENT px (sent = master * scale). Derived
    from the RESIZED dimensions only — never the padded ones (padding is added to
    the bottom/right and carries no content)."""
    if master_w_px <= 0:
        raise ValueError("master_w_px must be positive")
    return sent_w_px / master_w_px


def scale_master_to_sent(bbox_master_px, scale: float) -> list[float]:
    """A master-space bbox [x0,y0,x1,y1] mapped into the sent (downsampled) copy."""
    return [c * scale for c in bbox_master_px]


def scale_sent_to_master(bbox_sent_px, scale: float) -> list[float]:
    """A sent-space bbox [x0,y0,x1,y1] mapped back onto the master raster."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    return [c / scale for c in bbox_sent_px]


# --------------------------------------------------------------------------- #
# Debug overlay (image-space; PIL)
# --------------------------------------------------------------------------- #
def render_region_overlay(master_image, regions: list[Region],
                          tile_boundaries: Optional[list[tuple[int, int, int, int]]] = None):
    """Return a copy of ``master_image`` (a PIL image) with every region's
    boundary drawn, so gaps/overlap are visually confirmable. Region rectangles
    are drawn semi-thick; if ``tile_boundaries`` (the non-overlapped grid) are
    given they are drawn dashed so the overlap band is visible between them."""
    from PIL import Image, ImageDraw

    img = master_image.convert("RGB").copy()
    draw = ImageDraw.Draw(img, "RGBA")
    colors = [(240, 84, 92), (95, 191, 228), (227, 161, 60), (141, 164, 190),
              (120, 200, 120), (200, 120, 200)]
    for i, rg in enumerate(regions):
        col = colors[i % len(colors)]
        draw.rectangle(rg.bbox_master_px, outline=col + (255,), width=6)
        # A translucent fill makes the overlap bands (where fills stack) obvious.
        draw.rectangle(rg.bbox_master_px, fill=col + (26,))
        draw.text((rg.x0_master_px + 12, rg.y0_master_px + 12), rg.id,
                  fill=(20, 20, 20, 255))
    for tb in (tile_boundaries or []):
        draw.rectangle(list(tb), outline=(20, 20, 20, 160), width=2)
    return img
