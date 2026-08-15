"""The shared high-resolution second-look subsystem (REFACTOR_ANALYSIS §1.4).

Two systems in this pipeline solve the same problem — *re-read part of the
drawing at higher resolution and reconcile the readings* — and had grown two
independent implementations of it:

  * **Stage 1.2 tiled extraction** (:mod:`utils.tiled_extraction`) — an
    ESCALATION. Fires only when the sheet looks too sparse to read (blank
    heuristic, ink density, low confidence, large sheet at the raster cap),
    re-renders the vector PDF at escalating DPI, and stitches overlapping tiles
    by anchor position.
  * **Stage 2.3 region extraction** (:mod:`pipeline.region_extraction`) —
    UNCONDITIONAL. Every drawing, every region, no gate, reconciled field-by-
    field against the overview pass.

They differ in exactly one *design* dimension — **trigger policy** — and in the
key their readings are matched on (spatial anchor vs. ``field_path``). Everything
else (overlapping-window planning with a coverage guarantee, comparing two
readings of the same thing, deciding agree / one-wins / genuine-conflict) is one
algorithm that was written twice.

This module owns the shared half:

  * **Trigger policy, pluggable** — :data:`ALWAYS` vs
    :data:`ON_CONFIDENCE_HEURISTIC`, resolved by :func:`evaluate_trigger`. The
    policy is now a value a caller passes, not a property of which module it
    imported. The default for the region pass stays ``ALWAYS``: gating the
    high-res read on the model's own confidence means a *confidently wrong*
    field never gets a second look, which is the silent-skip failure the
    unconditional design exists to prevent.
  * **Window planning** — :func:`windows_fixed_size` (fixed window + step, the
    tiled path) and :func:`windows_by_division` (sheet divided into N×M padded
    cells, the region path), sharing :func:`assert_full_coverage` so neither can
    silently leave a strip of sheet unread.
  * **Reading reconciliation** — :func:`values_agree`, :func:`rank_confidence`,
    :func:`reconcile_pair` and :func:`group_by_proximity`: the decision core both
    merges make. The *outcome vocabulary* stays each caller's own (the tiled path
    keeps conflicting readings as candidate values for the Stage 2.5 resolver;
    the region path routes an equal-confidence conflict to human review) — this
    module decides WHICH case a pair is, not what the caller does about it.

Nothing here performs I/O or calls a model. Pixel space only (top-left origin,
+Y down), like :mod:`pipeline.image_coordinates`; it never touches
:mod:`pipeline.coordinate_normalize` (3D model space).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Trigger policy
# --------------------------------------------------------------------------- #
ALWAYS = "always"
ON_CONFIDENCE_HEURISTIC = "on_confidence_heuristic"
NEVER = "never"

TRIGGER_POLICIES = (ALWAYS, ON_CONFIDENCE_HEURISTIC, NEVER)


@dataclass
class TriggerDecision:
    """Whether the high-res pass runs, under which policy, and why."""

    fire: bool
    policy: str
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.fire

    def as_dict(self) -> dict[str, Any]:
        return {"fire": self.fire, "policy": self.policy, "reasons": self.reasons}


def evaluate_trigger(policy: str = ALWAYS, *, image: Any = None,
                     extraction: Optional[dict] = None,
                     page_area_sqin: Optional[float] = None,
                     raster_long_edge: int = 2576) -> TriggerDecision:
    """Resolve a trigger policy into a decision.

    ``ALWAYS`` fires unconditionally (the region pass's default, and the reason
    it catches confidently-wrong readings). ``ON_CONFIDENCE_HEURISTIC`` delegates
    to the escalation heuristics in :func:`utils.tiled_extraction.should_tile`
    (blank sheet / ink density / extraction confidence / unclear-dimension
    fraction / large sheet at the raster cap) — ANY of them fires it. ``NEVER``
    is the explicit opt-out, recorded as a decision rather than an absence.
    """
    policy = (policy or ALWAYS).strip().lower()
    if policy == NEVER:
        return TriggerDecision(False, NEVER,
                               ["policy=never: high-resolution second look disabled"])
    if policy == ALWAYS:
        return TriggerDecision(True, ALWAYS, [
            "policy=always: every drawing gets the high-resolution second look "
            "(a confidently-wrong reading would never trigger a heuristic)"])
    if policy != ON_CONFIDENCE_HEURISTIC:
        raise ValueError(f"unknown trigger policy {policy!r}; expected one of "
                         f"{TRIGGER_POLICIES}")
    from utils.tiled_extraction import should_tile

    trig = should_tile(image=image, extraction=extraction,
                       page_area_sqin=page_area_sqin,
                       raster_long_edge=raster_long_edge)
    return TriggerDecision(bool(trig.fire), ON_CONFIDENCE_HEURISTIC, list(trig.reasons))


# --------------------------------------------------------------------------- #
# Window planning (overlapping windows over one raster, full coverage)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Window:
    """One overlapping window of a raster, in that raster's pixel space."""

    row: int
    col: int
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def offset(self) -> tuple[int, int]:
        return (self.x0, self.y0)

    @property
    def bbox(self) -> list[int]:
        return [self.x0, self.y0, self.x1, self.y1]

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "col": self.col, "x0": self.x0, "y0": self.y0,
                "x1": self.x1, "y1": self.y1}


def window_starts(length: int, size: int, step: int) -> list[int]:
    """Start offsets of fixed-``size`` windows stepping by ``step`` along an axis
    of ``length``. The last window is CLAMPED to the far edge so the axis is
    always fully covered (the final step is shorter, never a gap)."""
    if length <= size:
        return [0]
    starts = list(range(0, max(1, length - size) + 1, step))
    if starts[-1] != length - size:
        starts.append(length - size)
    return starts


def windows_fixed_size(width: int, height: int, size: int,
                       overlap_frac: float) -> list[Window]:
    """Fixed-size overlapping windows (the tiled-extraction shape): every window
    is ``size`` px square except where clamped at the far edge; the step is
    ``size * (1 - overlap_frac)``."""
    step = max(1, int(round(size * (1.0 - overlap_frac))))
    xs = window_starts(width, size, step)
    ys = window_starts(height, size, step)
    return [Window(r, c, x0, y0, min(x0 + size, width), min(y0 + size, height))
            for r, y0 in enumerate(ys) for c, x0 in enumerate(xs)]


def windows_by_division(width: int, height: int, target_edge: int,
                        overlap_frac: float) -> list[Window]:
    """Divide the raster into ``ceil(edge / target_edge)`` cells per axis and pad
    each cell by ``overlap_frac`` on its interior sides (the region-extraction
    shape). Window COUNT scales with sheet size; window SIZE lands near the
    model's native resolution instead of being downsampled a second time.

    Far edges round UP and near edges round DOWN, so integer truncation can never
    open a one-pixel seam between neighbouring windows.
    """
    cols = max(1, math.ceil(width / target_edge))
    rows = max(1, math.ceil(height / target_edge))
    cell_w, cell_h = width / cols, height / rows
    pad_x, pad_y = cell_w * overlap_frac, cell_h * overlap_frac
    out: list[Window] = []
    for r in range(rows):
        for c in range(cols):
            out.append(Window(
                r, c,
                int(max(0, c * cell_w - pad_x)),
                int(max(0, r * cell_h - pad_y)),
                int(math.ceil(min(width, (c + 1) * cell_w + pad_x))),
                int(math.ceil(min(height, (r + 1) * cell_h + pad_y))),
            ))
    return out


def assert_full_coverage(windows: Sequence[Window], width: int, height: int) -> None:
    """Raise ``ValueError`` unless the windows cover the whole raster.

    Coverage is the guarantee the entire second-look design rests on: a strip of
    sheet that no window contains is a region of the drawing NOBODY re-read, and
    nothing downstream would ever notice. Checked on the union of projected
    spans per axis, which is exact for a full row/column grid.
    """
    if not windows:
        raise ValueError("no windows planned — the whole raster would go unread")

    def _covered(spans: Iterable[tuple[int, int]], length: int) -> bool:
        reach = 0
        for lo, hi in sorted(spans):
            if lo > reach:
                return False
            reach = max(reach, hi)
        return reach >= length

    if not _covered(((w.x0, w.x1) for w in windows), width):
        raise ValueError("planned windows leave a horizontal gap in the raster")
    if not _covered(((w.y0, w.y1) for w in windows), height):
        raise ValueError("planned windows leave a vertical gap in the raster")


# --------------------------------------------------------------------------- #
# Reading reconciliation (the decision core both merges share)
# --------------------------------------------------------------------------- #
AGREED = "agreed"
A_WINS = "a_wins"
B_WINS = "b_wins"
CONFLICT = "conflict"

CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def rank_confidence(value: Any) -> int:
    """LOW/MEDIUM/HIGH (any case) as a comparable rank; unknown ranks lowest."""
    return CONFIDENCE_RANK.get(str(value).upper(), 0)


def values_agree(a: Any, b: Any, rel_tol: float = 1e-3,
                 abs_tol: float = 1e-6) -> bool:
    """Whether two readings of the same thing are the same reading. Numbers
    compare within tolerance; anything else compares as trimmed lowercase text.
    ``None`` agrees only with ``None`` — an absent reading is not a match."""
    if a is None or b is None:
        return a is b
    try:
        return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def numeric_key(value: Any, places: int = 4) -> Any:
    """A hashable identity for a reading's value — rounded for floats so two
    readings of the same number group together, verbatim otherwise."""
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return value


def reconcile_pair(a_value: Any, b_value: Any, a_rank: int = 0, b_rank: int = 0,
                   rel_tol: float = 1e-3) -> str:
    """Classify two readings of one field: :data:`AGREED`, :data:`A_WINS`,
    :data:`B_WINS`, or :data:`CONFLICT`.

    Values that agree need no winner. Otherwise the strictly higher-confidence
    side wins. Equal confidence with different values is a genuine
    :data:`CONFLICT` and is NEVER auto-tie-broken here — the caller escalates it
    (to human review on the region path, to candidate values for the resolver on
    the tiled path). Silently picking one would be exactly the fabricated
    certainty this pipeline forbids.
    """
    if values_agree(a_value, b_value, rel_tol=rel_tol):
        return AGREED
    if a_rank > b_rank:
        return A_WINS
    if b_rank > a_rank:
        return B_WINS
    return CONFLICT


def group_by_proximity(items: Sequence[Any],
                       position_of: Callable[[Any], Optional[tuple[float, float]]],
                       tol: float) -> list[list[int]]:
    """Group item INDICES whose 2-D positions lie within ``tol`` of the group's
    first member (single-link to the seed, deterministic in input order). Items
    with no position are not grouped and are omitted — the caller decides what an
    unanchored reading means. Used to decide "these tile readings are the same
    dimension read twice across the overlap"."""
    positions = [position_of(it) for it in items]
    used = [False] * len(items)
    groups: list[list[int]] = []
    for i, pos in enumerate(positions):
        if used[i] or pos is None:
            continue
        used[i] = True
        group = [i]
        for j in range(i + 1, len(items)):
            if used[j] or positions[j] is None:
                continue
            if math.hypot(pos[0] - positions[j][0], pos[1] - positions[j][1]) <= tol:
                used[j] = True
                group.append(j)
        groups.append(group)
    return groups


def best_by_rank(items: Iterable[Any], key_of: Callable[[Any], Optional[str]],
                 rank_of: Callable[[Any], int]) -> dict[str, Any]:
    """Best item per key by rank (first one wins a tie — input order decides,
    so the result is deterministic)."""
    out: dict[str, Any] = {}
    for it in items:
        key = key_of(it)
        if not key:
            continue
        cur = out.get(key)
        if cur is None or rank_of(it) > rank_of(cur):
            out[key] = it
    return out
