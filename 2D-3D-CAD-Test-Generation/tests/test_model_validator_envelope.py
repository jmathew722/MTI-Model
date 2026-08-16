"""Built-model dimension check: only WHOLE-PART dimensions may be compared to
the bounding box (2026-08-16).

Found on a live COM build of 16247. The check matched any dimension whose raw
label was exactly "length", which swept in the resolver's DERIVED cut length
(``D900 applies_to="length" = 4.8125``) and failed a correct build because that
number is not — and never should be — among the part's overall extents. At the
same time ``total_flange_width`` was not recognised as an envelope dimension at
all, so the width was never checked.

Both halves matter: dropping a bogus FAIL is only an improvement if the real
checks still fire.
"""
from pipeline.model_validator import _dims_in_meters, _feature_local_dimension_ids
from pipeline.schema import DrawingData, is_envelope_label

IN = 0.0254


def _model(**over):
    base = {
        "units": "inch", "confidence": 0.9,
        "dimensions": [
            {"id": "D001", "type": "linear", "value": 19.25, "unit": "inch",
             "applies_to": "overall_height"},
            {"id": "D003", "type": "linear", "value": 1.0, "unit": "inch",
             "applies_to": "flange_width"},
            {"id": "D005", "type": "linear", "value": 2.0, "unit": "inch",
             "applies_to": "total_flange_width"},
            {"id": "D900", "type": "linear", "value": 4.8125, "unit": "inch",
             "applies_to": "length"},          # resolver-derived, owned by the cut
        ],
        "features": [
            {"id": "F001", "type": "extrude_boss", "description": "base",
             "related_dimensions": ["D001", "D003", "D005"]},
            {"id": "F002", "type": "extrude_cut", "description": "relief cut",
             "related_dimensions": ["D003", "D900"]},
        ],
    }
    base.update(over)
    return DrawingData.model_validate(base)


class TestEnvelopeRecognition:
    def test_a_totality_label_is_an_envelope_dimension(self):
        assert is_envelope_label("total_flange_width")
        assert is_envelope_label("overall_height")
        assert is_envelope_label("width")

    def test_a_component_label_is_not(self):
        assert not is_envelope_label("flange_width")
        assert not is_envelope_label("web_thickness")

    def test_the_width_check_now_has_something_to_compare(self):
        """Before the fix this returned [] and the width went unchecked."""
        assert _dims_in_meters(_model(), "width") == [2.0 * IN]


class TestFeatureLocalDimensionsAreExcluded:
    def test_a_cut_only_dimension_is_feature_local(self):
        assert _feature_local_dimension_ids(_model()) == {"D900"}

    def test_the_derived_cut_length_is_not_treated_as_the_part_length(self):
        assert _dims_in_meters(_model(), "length") == []      # was [4.8125 in]

    def test_a_dimension_shared_with_the_base_is_not_feature_local(self):
        # D003 is on both the base and the cut -> still a part dimension
        assert "D003" not in _feature_local_dimension_ids(_model())

    def test_a_free_standing_dimension_is_kept(self):
        """A dimension linked to no feature is a drawing-level dimension."""
        m = _model(features=[{"id": "F001", "type": "extrude_boss",
                              "description": "base", "related_dimensions": []}])
        assert _dims_in_meters(m, "height") == [19.25 * IN]

    def test_depth_dimension_of_a_cut_counts_as_feature_local(self):
        m = _model(features=[
            {"id": "F001", "type": "extrude_boss", "description": "base",
             "related_dimensions": ["D001"]},
            {"id": "F002", "type": "extrude_cut", "description": "cut",
             "related_dimensions": [], "depth_dimension_id": "D900"},
        ])
        assert "D900" in _feature_local_dimension_ids(m)


class TestRealChecksStillFire:
    def test_the_overall_height_is_still_compared(self):
        """The genuine finding on 16247 (built 18.25 vs a 19.25 drawing) must
        survive — removing noise must not remove signal."""
        assert _dims_in_meters(_model(), "height") == [19.25 * IN]
