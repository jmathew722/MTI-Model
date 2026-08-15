"""Where a feature IS: the one ownership story (REFACTOR_ANALYSIS §1.1).

Five modules each described themselves as owning some part of "where is this
feature", because each was added to fix one specific historical bug rather than
as part of one designed coordinate system:

  * :mod:`pipeline.hole_resolution` — vector geometry vs. the vision callout;
  * :mod:`pipeline.resolver` (``_feature_positional_xy``) — consume extracted
    positional dimensions before any escalation (the Bug-1 fix);
  * :mod:`pipeline.position_solver` — topological solve over the drawing's own
    anchor graph (authoritative for anchored features since 2026-07-21);
  * :mod:`pipeline.coordinate_normalize` — semantic anchors → global CAD
    coordinates, and the single inch→meter conversion;
  * :mod:`pipeline.build_sequencer` (``_feature_xy``) — the slot-aware position
    getter used when emitting.

Their LOGIC is not redundant — vector-vs-vision precedence and anchor-graph
solving are genuinely different jobs — so this module does not merge it. What
was redundant was the OWNERSHIP: five answers to one question, with the
precedence between them bolted on after the fact and documented in five places.

This module is that single place. It holds:

  * :data:`PHASES` — the ordered coordinate-resolution pipeline, each phase
    naming the module that performs it;
  * :data:`PRECEDENCE` — which source wins when two disagree, highest first;
  * :func:`resolve_position` — ONE entry point that answers "where is this
    feature, and who decided that?" with a :class:`PositionAuthority` record
    carrying the winning source, its rank, and a human-readable derivation;
  * :func:`to_global_meters` — the emission boundary, delegating to
    :mod:`pipeline.coordinate_normalize` (the only inch→meter conversion).

THE FOUR PHASES (runtime order — the order the pipeline actually executes):

  1. **Vector-vs-vision merge** (Stage 3, ``hole_resolution``) — exact geometry
     from the DXF/DWG/vector-PDF owns POSITION; the vision callout owns
     SEMANTICS (diameter / thread / depth). Disagreement keeps both and flags
     CRITICAL. Records ``position_source`` + ``position_confidence``.
  2. **Ambiguity resolution** (Stage 2.5, ``resolver``) — every extracted
     positional dimension is consumed into an (x, y) BEFORE any escalation; an
     operator must-meet spec value outranks a vision reading (specs-first);
     genuinely undimensioned locations commit a declared-basis conservative
     value, never a silent ``[0,0]``.
  3. **Anchor-graph solve** (Stage 7 pre-emission, ``position_solver``) — the
     drawing's OWN dimensioning scheme (chain / baseline / ordinate / polar /
     datum frame) is solved topologically. For an explicitly-anchored, grounded,
     single-instance feature this is AUTHORITATIVE and replaces the stored
     coordinate; an ungrounded solve falls back and flags, never silently.
  4. **Normalization + emission** (Stage 7, ``coordinate_normalize`` →
     ``build_sequencer._feature_xy`` → VBA/COM/CadQuery) — semantic anchors
     become global coordinates in one place (including the ``y = parent_height -
     depth`` edge-notch math), and inches become meters exactly once, at the CAD
     boundary.

The precedence is a strict order, not a heuristic — see :data:`PRECEDENCE` and
``tests/test_coordinate_authority.py``, which asserts the full chain end-to-end
(spec override > vector position > solver-derived > resolver ladder >
conservative default). That test exists because the precedence is exactly the
kind of cross-module ordering that silently breaks when one of the five modules
is refactored on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from utils.logger import get_logger

log = get_logger()

# --------------------------------------------------------------------------- #
# The ordered phases
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Phase:
    order: int
    name: str
    module: str
    owns: str


PHASES: tuple[Phase, ...] = (
    Phase(1, "vector_vs_vision_merge", "pipeline.hole_resolution",
          "exact vector geometry owns POSITION; the vision callout owns SEMANTICS"),
    Phase(2, "ambiguity_resolution", "pipeline.resolver",
          "extracted positional dimensions consumed before any escalation; "
          "specs-first; conservative commit instead of a silent [0,0]"),
    Phase(3, "anchor_graph_solve", "pipeline.position_solver",
          "the drawing's own dimensioning scheme solved topologically; "
          "authoritative for grounded, explicitly-anchored, single-instance features"),
    Phase(4, "normalize_and_emit", "pipeline.coordinate_normalize",
          "semantic anchors -> global coordinates, and the ONE inch->meter "
          "conversion, at the CAD boundary"),
)

# --------------------------------------------------------------------------- #
# Precedence — highest authority first. A source only wins if it actually has a
# position; the chain always terminates in a declared-basis conservative value,
# so a feature is never left without a coordinate (and never with a fabricated
# one presented as measured).
# --------------------------------------------------------------------------- #
SOURCE_SPEC = "spec_driven"           # operator must-meet constraint (tier 0)
SOURCE_VECTOR = "vector_geometry"     # DXF/DWG entity or vector-PDF circle
SOURCE_SOLVER = "anchor_solver"       # grounded anchor-graph solve
SOURCE_RESOLVER = "resolved_dimension"  # positional dimension read from the drawing
SOURCE_CONSERVATIVE = "committed_conservative"  # declared-basis commit, CRITICAL-flagged
SOURCE_UNKNOWN = "unknown"

PRECEDENCE: tuple[str, ...] = (
    SOURCE_SPEC, SOURCE_VECTOR, SOURCE_SOLVER, SOURCE_RESOLVER,
    SOURCE_CONSERVATIVE, SOURCE_UNKNOWN,
)

# Vector-extraction backends whose positions outrank a vision reading, in the
# precedence order hole_resolution itself uses.
_VECTOR_SOURCES = ("dxf_entity", "dwg_entity", "pdf_vector", "hough")


def rank_of(source: str) -> int:
    """Authority rank of a position source — LOWER wins (0 is highest)."""
    try:
        return PRECEDENCE.index(source)
    except ValueError:
        return len(PRECEDENCE)


def outranks(a: str, b: str) -> bool:
    """Whether source ``a`` wins over source ``b`` on a disagreement."""
    return rank_of(a) < rank_of(b)


@dataclass
class PositionAuthority:
    """Where one feature is, and who decided it."""

    feature_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    source: str = SOURCE_UNKNOWN
    confidence: float = 0.0
    grounded: bool = False
    derivation: str = ""
    considered: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rank(self) -> int:
        return rank_of(self.source)

    @property
    def known(self) -> bool:
        return self.x is not None and self.y is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "position": [self.x, self.y] if self.known else None,
            "source": self.source,
            "rank": self.rank,
            "confidence": round(float(self.confidence), 4),
            "grounded": self.grounded,
            "derivation": self.derivation,
            "considered": self.considered,
        }


# --------------------------------------------------------------------------- #
# The single entry point
# --------------------------------------------------------------------------- #
def resolve_position(model, feature, *, solved: Optional[dict] = None,
                     spec_positions: Optional[dict[str, tuple[float, float]]] = None,
                     resolution: Any = None) -> PositionAuthority:
    """Answer "where is this feature, and who decided that?" under :data:`PRECEDENCE`.

    ``model`` is a :class:`~pipeline.schema.DrawingData`; ``feature`` is one of
    its features (or a feature id). ``solved`` is the
    :func:`pipeline.position_solver.solve_positions` result — passed in rather
    than recomputed so this stays a pure read over state the pipeline already
    produced. ``spec_positions`` carries any operator must-meet position (tier 0)
    for a feature id. ``resolution`` is the Stage 2.5
    :class:`~pipeline.resolver.ResolutionResult`, read for the assumption BASIS
    behind a stored coordinate — the same source
    :func:`pipeline.build_sequencer._derivation_source` reads; without it a
    stored coordinate is taken at face value as a read dimension.

    Every candidate that had a position is recorded in ``considered`` with its
    rank, so a disagreement is visible rather than silently collapsed — the
    winner is chosen by precedence, never by averaging or by a tie-break the
    drawing does not support.
    """
    feat = _as_feature(model, feature)
    if feat is None:
        fid = feature if isinstance(feature, str) else getattr(feature, "id", "")
        return PositionAuthority(feature_id=str(fid), derivation="no such feature")

    fid = feat.id
    candidates: list[PositionAuthority] = []

    # 1. Operator must-meet spec (tier 0) — outranks everything the drawing says.
    spec = (spec_positions or {}).get(fid)
    if spec is not None:
        candidates.append(PositionAuthority(
            fid, float(spec[0]), float(spec[1]), SOURCE_SPEC, confidence=1.0,
            grounded=True,
            derivation="operator must-meet specification (tier 0) fixes this position"))

    # 2. Vector geometry (position owns; the callout still owns semantics).
    hole = _hole_for(model, fid)
    if hole is not None and str(getattr(hole, "position_source", "")) in _VECTOR_SOURCES:
        pos = _hole_position(hole)
        if pos is not None:
            candidates.append(PositionAuthority(
                fid, pos[0], pos[1], SOURCE_VECTOR,
                confidence=float(getattr(hole, "position_confidence", 0.0) or 0.0),
                grounded=True,
                derivation=f"exact {hole.position_source} geometry from the source file"))

    # 3. Grounded anchor-graph solve.
    sol = (solved or {}).get(fid)
    if sol is not None and getattr(sol, "grounded", False):
        candidates.append(PositionAuthority(
            fid, float(sol.x), float(sol.y), SOURCE_SOLVER, confidence=0.9,
            grounded=True,
            derivation=" ; ".join(getattr(sol, "trace", []) or [])
                       or "solved from the drawing's own anchors"))

    # 4/5. What the resolver committed onto the feature.
    if getattr(feat, "position_known", False):
        basis = _positional_basis(feat, resolution)
        if basis == "committed_conservative":
            candidates.append(PositionAuthority(
                fid, float(feat.offset_x), float(feat.offset_y), SOURCE_CONSERVATIVE,
                confidence=0.3, grounded=False,
                derivation="no dimension for this location — a declared-basis "
                           "conservative value was committed and CRITICAL-flagged"))
        elif basis:
            # A DERIVED value (including the blank-basis case, which reads as
            # 'unspecified_basis', never as directly-extracted): it ranks with the
            # resolver ladder but is not grounded in a read dimension.
            candidates.append(PositionAuthority(
                fid, float(feat.offset_x), float(feat.offset_y), SOURCE_RESOLVER,
                confidence=0.5, grounded=False,
                derivation=f"derived by the resolver ({basis}), not read directly "
                           f"off the drawing"))
        else:
            candidates.append(PositionAuthority(
                fid, float(feat.offset_x), float(feat.offset_y), SOURCE_RESOLVER,
                confidence=0.7, grounded=True,
                derivation="positional dimension(s) read from the drawing"))

    if not candidates:
        return PositionAuthority(fid, derivation="no positional evidence of any kind")

    candidates.sort(key=lambda c: c.rank)
    winner = candidates[0]
    winner.considered = [
        {"source": c.source, "rank": c.rank,
         "position": [c.x, c.y] if c.known else None} for c in candidates]
    return winner


def disagreements(authority: PositionAuthority, tol: float = 1e-3) -> list[dict[str, Any]]:
    """Candidates that disagreed with the winner by more than ``tol``.

    A disagreement is never resolved by averaging — the higher-authority source
    wins outright and the loser is reported, which is what lets a wrong vector
    scale or a mis-anchored dimension be SEEN instead of quietly absorbed.
    """
    if not authority.known:
        return []
    out: list[dict[str, Any]] = []
    for c in authority.considered:
        pos = c.get("position")
        if not pos or c.get("source") == authority.source:
            continue
        if (abs(float(pos[0]) - authority.x) > tol
                or abs(float(pos[1]) - authority.y) > tol):
            out.append({**c, "winner": authority.source,
                        "winner_position": [authority.x, authority.y]})
    return out


def to_global_meters(x_in: float, y_in: float) -> tuple[float, float]:
    """Phase 4's boundary: drawing inches → CAD meters, via the ONE conversion in
    :mod:`pipeline.coordinate_normalize`. Never convert units anywhere else."""
    from pipeline.coordinate_normalize import to_meters

    return (to_meters(x_in), to_meters(y_in))


def describe() -> str:
    """The ownership story as text (for reports, docs, and the explainer)."""
    lines = ["Coordinate resolution phases (runtime order):"]
    for p in PHASES:
        lines.append(f"  {p.order}. {p.name} [{p.module}] — {p.owns}")
    lines.append("Precedence on disagreement (highest first): "
                 + " > ".join(PRECEDENCE[:-1]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Small readers over the schema (kept private — the schema is the contract)
# --------------------------------------------------------------------------- #
def _as_feature(model, feature):
    if isinstance(feature, str):
        return model.feature_by_id(feature)
    return feature


def _hole_for(model, feature_id: str):
    for h in getattr(model, "hole_callouts", []) or []:
        if getattr(h, "feature_ref", "") == feature_id:
            return h
    return None


def _hole_position(hole) -> Optional[tuple[float, float]]:
    positions = getattr(hole, "instance_positions", None) or []
    if positions and len(positions[0]) == 2:
        return (float(positions[0][0]), float(positions[0][1]))
    return None


def _positional_basis(feat, resolution) -> str:
    """The assumption basis behind this feature's stored coordinate.

    Read from the Stage 2.5 resolution (where the basis actually lives — the
    Dimension schema has no such field), for the dimensions that drive this
    feature. A blank basis on a made assumption reads as ``unspecified_basis``,
    never as directly-extracted (the 2026-07-12 falsy-basis rule).
    """
    if resolution is None:
        return ""
    dim_res = getattr(resolution, "dim_resolutions", None) or {}
    ids = list(getattr(feat, "related_dimensions", []) or [])
    depth_id = getattr(feat, "depth_dimension_id", "")
    if depth_id:
        ids.append(depth_id)
    for did in ids:
        dr = dim_res.get(did)
        if dr is None or not getattr(dr, "assumption_made", False):
            continue
        return str(getattr(dr, "assumption_basis", "") or "").strip().lower() \
            or "unspecified_basis"
    return ""
