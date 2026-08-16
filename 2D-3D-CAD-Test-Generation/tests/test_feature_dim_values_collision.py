"""Canonical-key dimension collisions — the ONE policy, and its two callers.

History of this rule, because it has been revised twice on evidence:

* originally "biggest value wins" — arbitrary, and could size a base or a cut
  from the wrong dimension;
* 2026-07-28: "FIRST-declared wins" — the drawing's own linking order, a
  principled tie-break instead of a magnitude heuristic;
* 2026-08-16: first-declared REMAINS the default, but a label that declares
  TOTALITY ("overall", "total", …) now wins. Live evidence: 16247 has
  ``total_flange_width`` 2.0 and ``flange_width`` 1.0, which both canonicalize
  to "width". First-declared kept the COMPONENT, and the real COM build came
  out 1.0 wide against a 2.0 drawing — measured off the built STL's bounding
  box, not caught by any unit test.

This is NOT a return to "biggest wins": a smaller total still wins, and that
case is asserted below. The policy lives in
:func:`pipeline.schema.collapse_dimension_values`; both consumers
(``build_sequencer._feature_dim_values`` and ``macro_generator._dims_map``) must
use it — they used to disagree, which is how the base solid and the disposition
table could describe different geometry.
"""
from __future__ import annotations

from pipeline.build_sequencer import _feature_dim_values
from pipeline.macro_generator import _dims_map
from pipeline.schema import (
    DrawingData,
    Feature,
    collapse_dimension_values,
    declares_totality,
)


def _model(dims):
    return DrawingData.model_validate({
        "units": "inch", "confidence": 0.9,
        "dimensions": dims,
        "features": [],
    })


def _feat(ids):
    return Feature(id="F001", type="extrude_boss", description="base",
                   related_dimensions=ids)


# --------------------------------------------------------------------------- #
# The policy itself
# --------------------------------------------------------------------------- #
class TestPolicy:
    def test_first_declared_wins_when_neither_declares_totality(self):
        values, notes = collapse_dimension_values([
            ("length", "web_length", 3.0),
            ("length", "flange_length", 9.0),
        ])
        assert values["length"] == 3.0        # first-declared, NOT the larger
        assert "kept the first-declared" in notes[0]

    def test_a_totality_label_wins_over_a_component(self):
        values, notes = collapse_dimension_values([
            ("width", "flange_width", 1.0),
            ("width", "total_flange_width", 2.0),
        ])
        assert values["width"] == 2.0
        assert "OVERALL extent" in notes[0]

    def test_totality_wins_even_when_it_is_the_SMALLER_value(self):
        """Proves the rule reads the label, not the magnitude."""
        values, _ = collapse_dimension_values([
            ("length", "rib_length", 12.0),
            ("length", "overall length", 5.0),
        ])
        assert values["length"] == 5.0

    def test_two_totality_labels_fall_back_to_first_declared(self):
        values, notes = collapse_dimension_values([
            ("length", "overall length", 8.0),
            ("length", "total length", 9.0),
        ])
        assert values["length"] == 8.0
        assert "kept the first-declared" in notes[0]

    def test_a_component_never_displaces_an_established_total(self):
        values, _ = collapse_dimension_values([
            ("width", "overall width", 2.0),
            ("width", "flange_width", 1.0),
        ])
        assert values["width"] == 2.0

    def test_equal_values_are_not_a_collision(self):
        values, notes = collapse_dimension_values([
            ("depth", "depth", 0.28), ("depth", "depth", 0.28)])
        assert values == {"depth": 0.28} and notes == []

    def test_blank_keys_are_ignored(self):
        values, _ = collapse_dimension_values([("", "mystery", 5.0)])
        assert values == {}

    def test_every_collision_is_reported(self):
        _, notes = collapse_dimension_values([
            ("length", "a", 1.0), ("length", "b", 2.0), ("width", "c", 3.0)])
        assert len(notes) == 1          # visible, never silently resolved


class TestTotalityMarkers:
    def test_recognised_words(self):
        for label in ("overall length", "total_flange_width", "OUTSIDE dia",
                      "full width", "over-all height", "outer_width"):
            assert declares_totality(label), label

    def test_component_labels_are_not_totality(self):
        for label in ("flange_width", "web_thickness", "hole_spacing_x", "",
                      "length", "corner radius"):
            assert not declares_totality(label), label


# --------------------------------------------------------------------------- #
# Both callers must apply it identically
# --------------------------------------------------------------------------- #
class TestBothCallersAgree:
    DIMS = [
        {"id": "D003", "type": "linear", "value": 1.0, "unit": "inch",
         "applies_to": "flange_width"},
        {"id": "D005", "type": "linear", "value": 2.0, "unit": "inch",
         "applies_to": "total_flange_width"},
        {"id": "D001", "type": "linear", "value": 19.25, "unit": "inch",
         "applies_to": "overall_height"},
    ]

    def test_the_16247_regression_in_the_sequencer(self):
        out = _feature_dim_values(_model(self.DIMS), _feat(["D003", "D005", "D001"]))
        assert out["width"] == 2.0        # was 1.0 -> half-width part

    def test_the_16247_regression_in_the_macro_generator(self):
        out = _dims_map(_model(self.DIMS), _feat(["D003", "D005", "D001"]))
        assert out["width"] == 2.0

    def test_the_two_callers_produce_the_same_values(self):
        model, feat = _model(self.DIMS), _feat(["D003", "D005", "D001"])
        seq = _feature_dim_values(model, feat)
        gen = _dims_map(model, feat)
        for key in set(seq) & set(gen):
            assert seq[key] == gen[key], key

    def test_no_collision_unaffected(self):
        m = _model([
            {"id": "D001", "type": "linear", "value": 4.0, "unit": "inch",
             "applies_to": "length"},
            {"id": "D002", "type": "linear", "value": 2.0, "unit": "inch",
             "applies_to": "width"},
        ])
        assert _feature_dim_values(m, _feat(["D001", "D002"])) == {
            "length": 4.0, "width": 2.0}

    def test_declaration_order_still_decides_a_plain_collision(self):
        m = _model([
            {"id": "D001", "type": "linear", "value": 3.0, "unit": "inch",
             "applies_to": "web_length"},
            {"id": "D002", "type": "linear", "value": 9.0, "unit": "inch",
             "applies_to": "rib_length"},
        ])
        assert _feature_dim_values(m, _feat(["D001", "D002"]))["length"] == 3.0
        assert _feature_dim_values(m, _feat(["D002", "D001"]))["length"] == 9.0
