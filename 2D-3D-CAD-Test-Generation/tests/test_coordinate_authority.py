"""Coordinate-authority precedence (REFACTOR_ANALYSIS §1.1 / §2.6).

Five modules touch position resolution. The ORDER between them — spec override
> vector position > grounded anchor solve > resolved dimension > conservative
commit — is exactly the kind of cross-module contract that silently breaks when
one module is refactored alone, and no per-module unit test can catch it. These
tests assert the full chain end-to-end against the real schema.
"""
import pytest

from pipeline.coordinate_authority import (
    PHASES,
    PRECEDENCE,
    SOURCE_CONSERVATIVE,
    SOURCE_RESOLVER,
    SOURCE_SOLVER,
    SOURCE_SPEC,
    SOURCE_UNKNOWN,
    SOURCE_VECTOR,
    describe,
    disagreements,
    outranks,
    rank_of,
    resolve_position,
    to_global_meters,
)
from pipeline.position_solver import SolvedPosition, solve_positions
from pipeline.schema import DrawingData


def _model(feature_over=None, hole=None, dims=None) -> DrawingData:
    feat = {"id": "F002", "type": "hole", "description": "mounting hole",
            "offset_x": 1.0, "offset_y": 2.0, "position_known": True,
            "related_dimensions": ["D010"]}
    feat.update(feature_over or {})
    base = {
        "part_number": "AUTH-1",
        "units": "inch",
        "confidence": 0.9,
        "dimensions": (dims if dims is not None else [
            {"id": "D001", "type": "linear", "value": 10.0, "unit": "inch",
             "applies_to": "length"},
            {"id": "D002", "type": "linear", "value": 6.0, "unit": "inch",
             "applies_to": "width"},
            {"id": "D010", "type": "linear", "value": 1.0, "unit": "inch",
             "applies_to": "hole_position_x"},
        ]),
        "features": [
            {"id": "F001", "type": "extrude_boss", "description": "base plate"},
            feat,
        ],
        "hole_callouts": ([hole] if hole else []),
    }
    return DrawingData.model_validate(base)


def _resolution(basis: str, dim_id: str = "D010"):
    """A stand-in ResolutionResult carrying just the assumption basis the
    authority module reads (the same field build_sequencer reads)."""
    from types import SimpleNamespace

    return SimpleNamespace(dim_resolutions={
        dim_id: SimpleNamespace(assumption_made=True, assumption_basis=basis)})


def _vector_hole(**over):
    h = {"id": "H001", "type": "thru", "diameter": 0.25, "qty": 1,
         "feature_ref": "F002", "instance_positions": [[3.0, 4.0]],
         "position_source": "dxf_entity", "position_confidence": 0.98}
    h.update(over)
    return h


class TestPrecedenceOrder:
    def test_precedence_is_a_strict_documented_order(self):
        assert PRECEDENCE == (SOURCE_SPEC, SOURCE_VECTOR, SOURCE_SOLVER,
                              SOURCE_RESOLVER, SOURCE_CONSERVATIVE, SOURCE_UNKNOWN)
        assert rank_of(SOURCE_SPEC) < rank_of(SOURCE_VECTOR) < rank_of(SOURCE_SOLVER)
        assert rank_of(SOURCE_SOLVER) < rank_of(SOURCE_RESOLVER) < rank_of(SOURCE_CONSERVATIVE)
        assert outranks(SOURCE_VECTOR, SOURCE_RESOLVER)
        assert not outranks(SOURCE_RESOLVER, SOURCE_VECTOR)

    def test_unknown_source_ranks_last(self):
        assert rank_of("something-invented") >= rank_of(SOURCE_UNKNOWN)

    def test_phases_are_ordered_and_name_their_module(self):
        assert [p.order for p in PHASES] == [1, 2, 3, 4]
        assert [p.module for p in PHASES] == [
            "pipeline.hole_resolution", "pipeline.resolver",
            "pipeline.position_solver", "pipeline.coordinate_normalize"]
        text = describe()
        for p in PHASES:
            assert p.module in text


class TestChainEndToEnd:
    """Each test adds ONE higher-authority source and asserts it takes over."""

    def test_resolver_dimension_is_the_base_case(self):
        model = _model()
        auth = resolve_position(model, "F002")
        assert auth.source == SOURCE_RESOLVER
        assert (auth.x, auth.y) == (1.0, 2.0)
        assert auth.known and auth.grounded

    def test_conservative_commit_ranks_below_a_read_dimension(self):
        model = _model()
        auth = resolve_position(model, "F002",
                                resolution=_resolution("committed_conservative"))
        assert auth.source == SOURCE_CONSERVATIVE
        assert not auth.grounded          # it ships, but never claims to be measured
        assert "CRITICAL-flagged" in auth.derivation

    def test_blank_basis_is_not_read_as_a_measured_dimension(self):
        """The 2026-07-12 falsy-basis rule: an assumption with no stated basis
        must surface as derived, never as directly-extracted."""
        model = _model()
        auth = resolve_position(model, "F002", resolution=_resolution(""))
        assert auth.source == SOURCE_RESOLVER      # still ships from the stored value
        assert not auth.grounded                   # but does not claim to be read
        assert "unspecified_basis" in auth.derivation

    def test_a_named_derived_basis_is_reported_as_derived(self):
        model = _model()
        auth = resolve_position(model, "F002", resolution=_resolution("profile_delta"))
        assert auth.source == SOURCE_RESOLVER
        assert not auth.grounded
        assert "profile_delta" in auth.derivation

    def test_grounded_solver_beats_the_resolved_dimension(self):
        model = _model()
        solved = {"F002": SolvedPosition("F002", 5.5, 2.25, ["x = part_edge_left(0) + D010"],
                                         "baseline", True)}
        auth = resolve_position(model, "F002", solved=solved)
        assert auth.source == SOURCE_SOLVER
        assert (auth.x, auth.y) == (5.5, 2.25)
        assert "part_edge_left" in auth.derivation

    def test_ungrounded_solver_does_not_take_authority(self):
        model = _model()
        solved = {"F002": SolvedPosition("F002", 9.9, 9.9, [], "coordinate", False)}
        auth = resolve_position(model, "F002", solved=solved)
        assert auth.source == SOURCE_RESOLVER
        assert (auth.x, auth.y) == (1.0, 2.0)

    def test_vector_geometry_beats_the_solver(self):
        model = _model(hole=_vector_hole())
        solved = {"F002": SolvedPosition("F002", 5.5, 2.25, [], "baseline", True)}
        auth = resolve_position(model, "F002", solved=solved)
        assert auth.source == SOURCE_VECTOR
        assert (auth.x, auth.y) == (3.0, 4.0)
        assert auth.confidence == pytest.approx(0.98)

    def test_a_vision_sourced_hole_does_not_claim_vector_authority(self):
        model = _model(hole=_vector_hole(position_source="vision"))
        auth = resolve_position(model, "F002")
        assert auth.source == SOURCE_RESOLVER

    def test_spec_beats_everything(self):
        model = _model(hole=_vector_hole())
        solved = {"F002": SolvedPosition("F002", 5.5, 2.25, [], "baseline", True)}
        auth = resolve_position(model, "F002", solved=solved,
                                spec_positions={"F002": (7.0, 7.5)})
        assert auth.source == SOURCE_SPEC
        assert (auth.x, auth.y) == (7.0, 7.5)
        # every lower-ranked candidate is still recorded, never dropped
        assert {c["source"] for c in auth.considered} == {
            SOURCE_SPEC, SOURCE_VECTOR, SOURCE_SOLVER, SOURCE_RESOLVER}

    def test_full_chain_ranks_are_monotonic(self):
        model = _model(hole=_vector_hole())
        solved = {"F002": SolvedPosition("F002", 5.5, 2.25, [], "baseline", True)}
        auth = resolve_position(model, "F002", solved=solved,
                                spec_positions={"F002": (7.0, 7.5)})
        ranks = [c["rank"] for c in auth.considered]
        assert ranks == sorted(ranks)


class TestDisagreements:
    def test_disagreement_is_reported_not_averaged(self):
        model = _model(hole=_vector_hole())
        auth = resolve_position(model, "F002")
        diffs = disagreements(auth)
        assert auth.source == SOURCE_VECTOR
        assert (auth.x, auth.y) == (3.0, 4.0)          # NOT (2.0, 3.0), the mean
        assert [d["source"] for d in diffs] == [SOURCE_RESOLVER]
        assert diffs[0]["winner"] == SOURCE_VECTOR

    def test_agreeing_sources_raise_no_disagreement(self):
        model = _model(hole=_vector_hole(instance_positions=[[1.0, 2.0]]))
        auth = resolve_position(model, "F002")
        assert disagreements(auth) == []


class TestNoEvidence:
    def test_a_feature_with_no_position_evidence_says_so(self):
        model = _model(feature_over={"position_known": False})
        auth = resolve_position(model, "F002")
        assert auth.source == SOURCE_UNKNOWN
        assert not auth.known
        assert "no positional evidence" in auth.derivation

    def test_unknown_feature_id_is_not_an_exception(self):
        auth = resolve_position(_model(), "F999")
        assert auth.feature_id == "F999"
        assert not auth.known


class TestEmissionBoundary:
    def test_inches_convert_to_meters_exactly_once(self):
        assert to_global_meters(1.0, 2.0) == pytest.approx((0.0254, 0.0508))

    def test_conversion_delegates_to_coordinate_normalize(self):
        from pipeline.coordinate_normalize import INCH_TO_M, to_meters

        assert to_global_meters(3.0, 0.0)[0] == to_meters(3.0) == 3.0 * INCH_TO_M


class TestAgreesWithTheRealSolver:
    """The authority module must read the REAL solver's output shape, not a
    hand-made stand-in — that is precisely the drift this test exists to catch."""

    def test_real_solve_positions_output_is_consumable(self):
        model = _model(feature_over={
            "offset_x": 1.0, "offset_y": 2.0, "position_known": True,
            "anchors": [{"scheme": "baseline", "anchor_ref": "part_edge_left",
                         "axis": "x", "value": 4.0, "dimension_ids": ["D010"],
                         "semantics": "to_center"},
                        {"scheme": "baseline", "anchor_ref": "part_edge_bottom",
                         "axis": "y", "value": 3.0, "dimension_ids": ["D010"],
                         "semantics": "to_center"}]})
        solved = solve_positions(model)
        auth = resolve_position(model, "F002", solved=solved)
        assert auth.source == SOURCE_SOLVER
        assert (auth.x, auth.y) == pytest.approx((4.0, 3.0))
        assert auth.derivation                      # the solver's own trace text
