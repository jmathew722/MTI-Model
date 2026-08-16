"""A fillet/chamfer at or past half the material thickness is flagged at plan time.

The threshold is not a guess. Measured on SolidWorks 2026 rev 34.3.2 with
`experiments/solidworks_practice/t12_return_value_trust.py`, filleting all 12
edges of a 4.0 x 3.0 x **0.5** plate, each radius from an independent clean state:

    R0.01  -> built, 6.0 -> 5.99936        R0.26 -> None, no change
    R0.0625-> built, 6.0 -> 5.97518        R0.30 -> None, no change
    R0.125 -> built, 6.0 -> 5.90202        R0.50 -> None, no change
    R0.20  -> built, 6.0 -> 5.75319        R1.00 -> None, no change
    R0.24  -> built, 6.0 -> 5.64768        R5.00 -> None, no change

The limit falls between 0.24 and 0.26 on a 0.5 plate: **R < thickness / 2**, as
the two opposite edges of a through edge each consume the radius. Past it the
call returns nothing and no geometry changes, without raising.

`_check_edge_treatment_radius` says so at plan time. It is ADVISORY: the drawing
is the authority and the callout may be meant for a thicker edge, so it warns and
the build proceeds (guiding principle — flag, never block).
"""
import pytest

from pipeline.schema import DrawingData, Feature, FeatureType
from pipeline.validator import run_verification


def _plate_with_edge_treatment(thickness: float, size: float,
                               kind: FeatureType = FeatureType.FILLET) -> DrawingData:
    """A plate of a known thickness carrying one fillet/chamfer of a known size."""
    applies = "fillet_radius" if kind == FeatureType.FILLET else "chamfer"
    return DrawingData(
        units="inch", confidence=0.9,
        dimensions=[
            {"id": "D1", "type": "linear", "value": 4.0, "unit": "inch", "applies_to": "length"},
            {"id": "D2", "type": "linear", "value": 3.0, "unit": "inch", "applies_to": "width"},
            {"id": "D3", "type": "linear", "value": thickness, "unit": "inch",
             "applies_to": "thickness"},
            {"id": "D4", "type": "radial", "value": size, "unit": "inch",
             "applies_to": applies},
        ],
        features=[
            Feature(id="F1", type=FeatureType.EXTRUDE_BOSS, description="base plate",
                    related_dimensions=["D1", "D2", "D3"]),
            Feature(id="F2", type=kind, description="edge treatment",
                    related_dimensions=["D4"], parent_feature="F1"),
        ],
        build_order=["F1", "F2"],
    )


def _report(model: DrawingData):
    """run_verification returns (model, report) — take the report."""
    out = run_verification(model)
    return out[1] if isinstance(out, tuple) else out


def _warnings_about(model: DrawingData, feature_id: str) -> list[str]:
    return [w for w in _report(model).warnings if feature_id in w]


class TestEdgeTreatmentRadius:
    @pytest.mark.parametrize("radius", [0.01, 0.0625, 0.125, 0.2, 0.24])
    def test_radii_that_built_are_not_flagged(self, radius):
        """Every radius measured as BUILDING on a 0.5 plate must pass silently."""
        assert _warnings_about(_plate_with_edge_treatment(0.5, radius), "F2") == []

    @pytest.mark.parametrize("radius", [0.26, 0.3, 0.5, 1.0, 5.0])
    def test_radii_that_silently_did_nothing_are_flagged(self, radius):
        """Every radius measured as returning None must be warned about."""
        warns = _warnings_about(_plate_with_edge_treatment(0.5, radius), "F2")
        assert warns, f"R{radius} on a 0.5 plate silently does nothing but was not flagged"
        assert "half" in warns[0].lower()

    def test_exactly_half_is_flagged(self):
        """0.25 on a 0.5 plate is the boundary; 0.24 built and 0.26 did not, so
        the boundary itself is not established as buildable — warn."""
        assert _warnings_about(_plate_with_edge_treatment(0.5, 0.25), "F2")

    def test_chamfer_is_checked_too(self):
        warns = _warnings_about(
            _plate_with_edge_treatment(0.5, 0.4, FeatureType.CHAMFER), "F2")
        assert warns and "chamfer distance" in warns[0]

    def test_unknown_thickness_says_nothing(self):
        """No thickness means no comparison — never invent one to warn about."""
        model = _plate_with_edge_treatment(0.5, 5.0)
        model.dimensions = [d for d in model.dimensions
                            if (d.canonical_applies_to or d.applies_to) != "thickness"]
        model.features[0].related_dimensions = ["D1", "D2"]
        assert _warnings_about(model, "F2") == []

    def test_advisory_only_never_blocks(self):
        """The guiding principle: flag, never block."""
        report = _report(_plate_with_edge_treatment(0.5, 5.0))
        assert not any("F2" in e for e in report.errors)

    def test_scales_with_thickness(self):
        """R0.4 is fine on a 1.0 plate and not on a 0.5 one — the rule is
        relative, not an absolute radius limit."""
        assert _warnings_about(_plate_with_edge_treatment(1.0, 0.4), "F2") == []
        assert _warnings_about(_plate_with_edge_treatment(0.5, 0.4), "F2")

    def test_the_warning_actually_reaches_the_human(self):
        """A check nobody reads is not a check.

        The warning is only useful if it survives into the VERIFICATION REPORT
        text that main.py writes out — computing it and dropping it would look
        identical from the unit tests above.
        """
        from pipeline.validator import format_verification_report

        model = _plate_with_edge_treatment(0.5, 0.4)
        out = run_verification(model)
        m, report = out if isinstance(out, tuple) else (model, out)
        text = format_verification_report(m, report)
        assert "F2" in text and "half" in text.lower(), (
            "the edge-treatment warning is computed but never rendered into the "
            "verification report the operator actually reads"
        )
