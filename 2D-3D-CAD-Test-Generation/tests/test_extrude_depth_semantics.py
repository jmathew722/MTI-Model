"""A feature's depth must not come from a dimension labelled as an in-plane extent.

Found on a real drawing, not reasoned about. TEST3 part `4079-D` (BRACKET ASSY,
`test_drawings/TEST3_Drawings/A040791E.PDF`), run 2026-08-17:

  * the base plate is BOM item 3, ``.250 x 4.500 x 15.25`` — a quarter-inch plate
  * the extraction set ``F001.depth_dimension_id = D013``
  * D013 is ``value=15.25, applies_to='length'``

SolidWorks extruded the plate **15.25 inches thick**. The model came out
17.25 x 14.75 x 7.00 in where the drawing's third extent is 6.00, and the
bounding-box check failed. Everything downstream that asks "how thick is this
part" got 15.25 — including `_check_edge_treatment_radius`, which computed a
7.6 in limit and therefore stayed silent about a 1.76 in chamfer that then
failed in SolidWorks. One bad field, three wrong answers.

The true 0.250 was never linked to the feature, so it cannot be recovered
without inventing it. These checks NAME the contradiction instead, with both
values, and let the build proceed (flag, never block).
"""
import pytest

from pipeline.schema import DrawingData, Feature, FeatureType
from pipeline.validator import (
    ValidationReport,
    _check_edge_treatment_radius,
    _check_extrude_depth_semantics,
)


def _plate(depth_applies_to: str, depth_value: float) -> DrawingData:
    """A 15.25 x 4.5 plate whose depth dimension is labelled `depth_applies_to`."""
    return DrawingData(
        units="inch", confidence=0.65,
        dimensions=[
            {"id": "D010", "type": "linear", "value": 4.5, "unit": "inch",
             "applies_to": "height"},
            {"id": "D012", "type": "linear", "value": 14.75, "unit": "inch",
             "applies_to": "length"},
            {"id": "D013", "type": "linear", "value": depth_value, "unit": "inch",
             "applies_to": depth_applies_to},
        ],
        features=[
            Feature(id="F001", type=FeatureType.EXTRUDE_BOSS, description="base plate",
                    related_dimensions=["D010", "D012", "D013"],
                    depth_dimension_id="D013"),
        ],
        build_order=["F001"],
    )


def _warns(model: DrawingData) -> list[str]:
    report = ValidationReport()
    _check_extrude_depth_semantics(model, report)
    return report.warnings


class TestExtrudeDepthSemantics:
    def test_the_real_4079d_case_is_caught(self):
        """The exact field values that produced a 15.25-inch-thick plate."""
        warns = _warns(_plate("length", 15.25))
        assert warns, "a depth dimension labelled 'length' must be flagged"
        assert "D013" in warns[0] and "15.25" in warns[0] and "length" in warns[0]

    @pytest.mark.parametrize("token", ["length", "width", "height", "overall_length",
                                       "overall_width", "span"])
    def test_every_in_plane_label_is_flagged(self, token):
        assert _warns(_plate(token, 15.25))

    @pytest.mark.parametrize("token", ["thickness", "depth", "plate_thickness", "wall"])
    def test_legitimate_thickness_labels_pass_silently(self, token):
        assert _warns(_plate(token, 0.25)) == []

    def test_no_depth_dimension_says_nothing(self):
        model = _plate("thickness", 0.25)
        model.features[0].depth_dimension_id = ""
        assert _warns(model) == []

    def test_advisory_never_blocks(self):
        report = ValidationReport()
        _check_extrude_depth_semantics(_plate("length", 15.25), report)
        assert report.errors == []
        assert report.ok


class TestBogusThicknessDoesNotProduceBogusRadiusWarnings:
    """The second half of the 4079-D failure: one bad field, a silent check."""

    @staticmethod
    def _plate_with_chamfer(depth_applies_to: str, depth_value: float) -> DrawingData:
        return DrawingData(
            units="inch", confidence=0.65,
            dimensions=[
                {"id": "D010", "type": "linear", "value": 4.5, "unit": "inch",
                 "applies_to": "height"},
                {"id": "D012", "type": "linear", "value": 14.75, "unit": "inch",
                 "applies_to": "length"},
                {"id": "D013", "type": "linear", "value": depth_value, "unit": "inch",
                 "applies_to": depth_applies_to},
                {"id": "D014", "type": "linear", "value": 1.76, "unit": "inch",
                 "applies_to": "chamfer_setback"},
            ],
            features=[
                Feature(id="F001", type=FeatureType.EXTRUDE_BOSS, description="base plate",
                        related_dimensions=["D010", "D012", "D013"],
                        depth_dimension_id="D013"),
                Feature(id="F005", type=FeatureType.CHAMFER, description="30 deg chamfer",
                        related_dimensions=["D014"], parent_feature="F001"),
            ],
            build_order=["F001", "F005"],
        )

    def _chamfer_warnings(self, model: DrawingData) -> list[str]:
        report = ValidationReport()
        _check_edge_treatment_radius(model, report)
        return [w for w in report.warnings if "F005" in w]

    def test_radius_check_bails_out_on_an_impossible_thickness(self):
        """A 'thickness' >= the in-plane envelope is a length; warning off it
        would be worse than silence, so the radius check must decline."""
        assert self._chamfer_warnings(self._plate_with_chamfer("length", 15.25)) == [], (
            "the radius check computed a limit from a 15.25 'thickness'; it must "
            "decline rather than reason from a number that cannot be a thickness"
        )

    def test_but_a_real_thickness_still_warns(self):
        """Declining must not disable the check for honest data."""
        assert self._chamfer_warnings(self._plate_with_chamfer("thickness", 0.25)), (
            "1.76 on a 0.25 plate is exactly the E024 case and must still be flagged"
        )
