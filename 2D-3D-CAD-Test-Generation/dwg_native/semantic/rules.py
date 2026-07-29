"""Deterministic geometry rules — the free, debuggable core of the semantic layer.

Rules first: closed-loop detection, base-profile selection, circle
classification, title-block/border exclusion, multiplier parsing, and
proximity-based value attachment. The LLM is reserved for genuine ambiguity
(mapper.py), never for arithmetic or geometry. Pure Python; no SolidWorks.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .numbers import ParsedNumber, parse_number

_SNAP = 2.0e-4          # 0.2 mm endpoint-snap tolerance (meters)


@dataclass
class Loop:
    segment_ids: List[str]
    vertices: List[Tuple[float, float]]

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def bbox_area(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return (x1 - x0) * (y1 - y0)

    @property
    def width(self) -> float:
        x0, _, x1, _ = self.bbox
        return x1 - x0

    @property
    def height(self) -> float:
        _, y0, _, y1 = self.bbox
        return y1 - y0


@dataclass
class Circle:
    id: str
    center: Tuple[float, float]
    radius: float                    # the THROUGH-hole radius (build diameter)
    role: str = "unknown"       # hole | construction | border | center_mark
    instances: int = 1          # merged concentric duplicates
    layer: str = ""
    cbore_radius: float = 0.0   # >0 if a larger concentric circle (counterbore
                                # mouth) merged into this hole — a SEPARATE
                                # stepped cut, never the drilled diameter


def _snap(v: float) -> float:
    return round(v / _SNAP) * _SNAP


def _key(pt: Sequence[float]) -> Tuple[float, float]:
    return (_snap(pt[0]), _snap(pt[1]))


def detect_closed_loops(segments: List[dict]) -> List[Loop]:
    """Find closed loops from line/arc segments by endpoint chaining.

    ``segments`` are geometry dicts with id/type/start_2d_m/end_2d_m. Full circles
    are NOT segments here (they are their own closed shape -> classify as holes).
    A simple planar chain-walk that reliably closes plate outlines/rectangles.
    """
    # Build endpoint adjacency.
    endpoints: List[Tuple[str, Tuple[float, float], Tuple[float, float]]] = []
    for s in segments:
        a, b = s.get("start_2d_m"), s.get("end_2d_m")
        if a is None or b is None:
            continue
        endpoints.append((s["id"], _key(a), _key(b)))

    adj: Dict[Tuple[float, float], List[int]] = {}
    for i, (_, a, b) in enumerate(endpoints):
        adj.setdefault(a, []).append(i)
        adj.setdefault(b, []).append(i)

    used = set()
    loops: List[Loop] = []
    for start_i in range(len(endpoints)):
        if start_i in used:
            continue
        sid, a0, b0 = endpoints[start_i]
        chain = [start_i]
        verts = [a0, b0]
        cur_node = b0
        local_used = {start_i}
        closed = False
        for _ in range(len(endpoints) + 1):
            nxt = None
            for cand in adj.get(cur_node, []):
                if cand in local_used or cand in used:
                    continue
                nxt = cand
                break
            if nxt is None:
                break
            _, na, nb = endpoints[nxt]
            local_used.add(nxt)
            chain.append(nxt)
            cur_node = nb if na == cur_node else na
            verts.append(cur_node)
            if cur_node == a0 and len(chain) >= 3:
                closed = True
                break
        if closed:
            used |= local_used
            # dedupe consecutive vertices
            clean: List[Tuple[float, float]] = []
            for v in verts:
                if not clean or clean[-1] != v:
                    clean.append(v)
            if clean and clean[0] == clean[-1]:
                clean.pop()
            loops.append(Loop(segment_ids=[endpoints[i][0] for i in chain], vertices=clean))
    return loops


def _contains(outer: Loop, inner: Loop, tol: float = _SNAP) -> bool:
    ox0, oy0, ox1, oy1 = outer.bbox
    ix0, iy0, ix1, iy1 = inner.bbox
    return (ox0 - tol <= ix0 and oy0 - tol <= iy0 and
            ix1 <= ox1 + tol and iy1 <= oy1 + tol and outer is not inner)


def exclude_furniture(loops: List[Loop], sheet: dict) -> Tuple[List[Loop], List[Loop]]:
    """Split loops into (part loops, furniture loops = border/title-block).

    A loop is furniture only on POSITIVE evidence, never by matching the geometry
    bounding box (that box IS the outer profile for a part drawn to fill its
    view — a circular test that used to discard the real base profile):
      * a title-block-style small rectangle hugging the lower-right corner, or
      * a sheet border: a loop that strictly CONTAINS another loop and whose area
        is far larger than that inner loop (a frame around the part).
    """
    x0 = sheet.get("min_x_m", 0.0)
    y0 = sheet.get("min_y_m", 0.0)
    sw = sheet.get("width_m", 0.0)
    sh = sheet.get("height_m", 0.0)
    part, furniture = [], []
    for lp in loops:
        lx0, ly0, lx1, ly1 = lp.bbox
        in_lower_right = (sw and sh and lx0 >= x0 + 0.55 * sw and ly1 <= y0 + 0.35 * sh)
        small = sw and sh and lp.bbox_area <= 0.12 * (sw * sh)
        is_title_block = bool(in_lower_right and small)
        # Border: contains a materially smaller loop (there is a part inside it).
        contains_smaller = any(_contains(lp, other) and other.bbox_area < 0.7 * lp.bbox_area
                               for other in loops)
        if is_title_block or contains_smaller:
            furniture.append(lp)
        else:
            part.append(lp)
    return part, furniture


def _merge_intervals(iv: List[Tuple[float, float]], gap: float) -> List[Tuple[float, float]]:
    if not iv:
        return []
    iv = sorted(iv)
    out = [list(iv[0])]
    for lo, hi in iv[1:]:
        if lo <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(x) for x in out]


def _covers(intervals: List[Tuple[float, float]], lo: float, hi: float, tol: float) -> bool:
    for a, b in intervals:
        if a <= lo + tol and b >= hi - tol:
            return True
    return False


def rectangle_candidates(segments: List[dict], gap: float = 1.5e-3) -> List[Loop]:
    """Every axis-aligned rectangle whose 4 sides are covered by (possibly several
    collinear) line segments, largest-area first. Robust to the dozens of
    dimension/extension/centre lines in a real detail drawing."""
    horiz: Dict[float, List[Tuple[float, float]]] = {}
    vert: Dict[float, List[Tuple[float, float]]] = {}
    for s in segments:
        a, b = s.get("start_2d_m"), s.get("end_2d_m")
        if not a or not b:
            continue
        if abs(a[1] - b[1]) < _SNAP:
            y = _snap(a[1]); horiz.setdefault(y, []).append((min(a[0], b[0]), max(a[0], b[0])))
        elif abs(a[0] - b[0]) < _SNAP:
            x = _snap(a[0]); vert.setdefault(x, []).append((min(a[1], b[1]), max(a[1], b[1])))
    if len(horiz) < 2 or len(vert) < 2:
        return []
    hy = {y: _merge_intervals(iv, gap) for y, iv in horiz.items()}
    vx = {x: _merge_intervals(iv, gap) for x, iv in vert.items()}
    ys = sorted(hy); xs = sorted(vx)
    out: List[Loop] = []
    for i in range(len(ys)):
        for j in range(i + 1, len(ys)):
            y_bot, y_top = ys[i], ys[j]
            for a in range(len(xs)):
                for b in range(a + 1, len(xs)):
                    x_left, x_right = xs[a], xs[b]
                    if (_covers(hy[y_bot], x_left, x_right, gap) and
                            _covers(hy[y_top], x_left, x_right, gap) and
                            _covers(vx[x_left], y_bot, y_top, gap) and
                            _covers(vx[x_right], y_bot, y_top, gap)):
                        out.append(Loop(segment_ids=["rect"],
                                        vertices=[(x_left, y_bot), (x_right, y_bot),
                                                  (x_right, y_top), (x_left, y_top)]))
    out.sort(key=lambda lp: lp.bbox_area, reverse=True)
    return out[:60]


def detect_rectangle_profile(segments: List[dict], gap: float = 1.5e-3) -> Optional[Loop]:
    """The largest covered rectangle (backward-compatible helper)."""
    c = rectangle_candidates(segments, gap)
    return c[0] if c else None


def select_rectangle_profile(segments: List[dict], stated_values_m: List[float],
                             gap: float = 1.5e-3, tol: float = 0.6e-3):
    """Ground the base profile in the drawing's OWN numbers: among candidate
    rectangles, prefer the one whose width AND height each match a stated overall
    dimension (from the MTEXT), so a rectangle formed by dimension/extension lines
    cannot win over the real outline. Returns (Loop|None, matched_sides:int).

    matched_sides == 2 -> both edges confirmed by a stated dimension (confident);
    1 or 0 -> profile is the largest rectangle but UNVERIFIED (caller flags it)."""
    cands = rectangle_candidates(segments, gap)
    if not cands:
        return None, 0

    def matched(lp: Loop) -> int:
        return sum(1 for dim in (lp.width, lp.height)
                   if any(abs(dim - sv) <= tol for sv in stated_values_m))
    # prefer 2 matched sides, then 1, then area — but never let a tiny
    # dimension-box rectangle with 2 matches beat a big outline with 2 matches.
    best = max(cands, key=lambda lp: (min(matched(lp), 2), lp.bbox_area))
    return best, matched(best)


def largest_profile_loop(loops: List[Loop], sheet: dict,
                         segments: Optional[List[dict]] = None,
                         stated_values_m: Optional[List[float]] = None):
    """The base profile. Prefer a rectangle grounded in stated dimensions; else
    the largest covered rectangle; else the largest non-furniture closed loop.
    Returns (Loop|None, matched_sides) — matched_sides is 2 when the profile is
    confirmed by the drawing's own overall dimensions."""
    if segments:
        rect, matched = select_rectangle_profile(segments, stated_values_m or [])
        if rect is not None:
            return rect, matched
    if not loops:
        return None, 0
    part, _ = exclude_furniture(loops, sheet)
    pool = part if part else loops
    return max(pool, key=lambda lp: lp.bbox_area), 0


def classify_circles(circles: List[dict], profile: Optional[Loop],
                     max_hole_frac: float = 0.5) -> List[Circle]:
    """Classify full circles as hole / construction / border and merge concentric.

    A circle inside the profile bbox whose diameter is below a fraction of the
    profile's smaller dimension is a hole candidate. Concentric duplicates
    (a hole + its center mark, or a counterbore's two circles) collapse to one.
    """
    out: List[Circle] = []
    for c in circles:
        ctr = c.get("center_2d_m")
        r = c.get("radius_m")
        if ctr is None or r is None:
            continue
        out.append(Circle(id=c["id"], center=(ctr[0], ctr[1]), radius=float(r),
                          layer=c.get("layer", "")))

    # Merge concentric (same center within snap). The SMALLER circle is the
    # through-drilled hole (the diameter that is actually drilled/cut all the
    # way); a larger concentric circle is its counterbore MOUTH — recorded
    # separately (cbore_radius), never folded into the build diameter. Merging
    # into the outer radius used to build the cbore's mouth diameter as a plain
    # through-hole (drilling a .50 cbore relief clear through a part that only
    # needed a .19 through-hole).
    merged: List[Circle] = []
    for c in sorted(out, key=lambda z: z.radius):
        hit = None
        for m in merged:
            if abs(m.center[0] - c.center[0]) < _SNAP and abs(m.center[1] - c.center[1]) < _SNAP:
                hit = m
                break
        if hit:
            hit.instances += 1
            if c.radius > hit.radius:
                hit.cbore_radius = c.radius   # larger concentric circle = cbore mouth
        else:
            merged.append(c)

    # Role assignment.
    if profile is not None:
        x0, y0, x1, y1 = profile.bbox
        min_dim = min(profile.width, profile.height) or 1.0
        for c in merged:
            inside = (x0 - _SNAP <= c.center[0] <= x1 + _SNAP and
                      y0 - _SNAP <= c.center[1] <= y1 + _SNAP)
            if not inside:
                c.role = "border"
            elif (2 * c.radius) <= max_hole_frac * min_dim:
                c.role = "hole"
            else:
                c.role = "construction"
    else:
        for c in merged:
            c.role = "hole"
    return merged


def parse_multipliers(text_tokens: List[dict]) -> List[ParsedNumber]:
    """Parse tokens that carry a count/multiplier (2X, 6 HOLES, (6) HLS, TYP)."""
    out = []
    for t in text_tokens:
        pn = parse_number(t.get("text", ""))
        if pn.count is not None or pn.is_typical:
            out.append(pn)
    return out


def attach_by_proximity(tokens: List[dict], geometry: List[dict],
                        max_dist: float = 0.03) -> List[dict]:
    """Attach each numeric text token to the nearest geometry entity.

    Phase 0: no attached-entity refs survive import, so proximity is the only
    signal. Returns attachment records with a distance-based match confidence;
    a token that cannot attach within ``max_dist`` gets confidence 0 (which the
    conflict checker treats as a blocking un-attached dimension).
    """
    attachments = []
    for t in tokens:
        pn = parse_number(t.get("text", ""))
        if not pn.numeric:
            continue
        pos = t.get("position_2d_m")
        if not pos:
            continue
        best_id, best_d = None, float("inf")
        for g in geometry:
            gp = _geom_anchor(g)
            if gp is None:
                continue
            d = math.hypot(pos[0] - gp[0], pos[1] - gp[1])
            if d < best_d:
                best_d, best_id = d, g["id"]
        conf = 0.0 if best_id is None or best_d > max_dist else max(0.0, 1.0 - best_d / max_dist)
        attachments.append({
            "token_id": t.get("id"), "text": t.get("text"),
            "value": pn.value, "kind": pn.kind,
            "attached_to": best_id if conf > 0 else None,
            "distance_m": None if best_id is None else round(best_d, 6),
            "confidence": round(conf, 3),
        })
    return attachments


def classify_hole_callout(text: str) -> dict:
    """Classify a hole callout's manufacturing type from its text. The subtype
    decides how the hole is built/annotated; params are recorded for the output.
    countersink > counterbore > tapped > simple (most specific wins)."""
    t = (text or "").upper()
    if re.search(r"C'?SINK|CSK|COUNTERSINK|FLAT\s*HD", t):
        sub = "countersink"
    elif re.search(r"C'?BORE|CBORE|COUNTERBORE|SOC\.?\s*HD|SHCS", t):
        sub = "counterbore"
    elif re.search(r"\bTAP\b|THREAD|NPT|\d+\s*-\s*\d+\b", t):
        sub = "tapped"
    else:
        sub = "simple"
    return {"subtype": sub, "callout": (text or "").strip()}


def gauge_thickness(text_tokens: List[dict]):
    """Sheet-metal thickness from a gauge callout, e.g. '7 GA. (.179)' -> .179.
    Returns (value, token_id) or None. High confidence — an explicit thickness."""
    for t in text_tokens:
        m = re.search(r"\bGA(?:UGE)?\.?\s*\(?\s*(\.\d+|\d+\.\d+)\s*\)?",
                      t.get("text", ""), re.IGNORECASE)
        if m:
            return float(m.group(1)), t.get("id")
    return None


def _geom_anchor(g: dict) -> Optional[Tuple[float, float]]:
    if g.get("center_2d_m"):
        return tuple(g["center_2d_m"])
    a, b = g.get("start_2d_m"), g.get("end_2d_m")
    if a and b:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return None
