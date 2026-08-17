"""A feature size that repeats an envelope value was borrowed, not read.

Generalises the depth-semantics guard from `depth_dimension_id` to any feature's
defining SIZE. Motivated by TEST3 `4088-A-RevA` (E028), where a KEY's hole came
out with `diameter 0.499` and the drawing gives the hole no diameter at all.

SCOPE, STATED HONESTLY: this catches an EXACT repeat. It does **not** catch
4088-A, whose hole diameter is 0.499 against a 0.500 envelope — a tolerance-band
difference rather than a repeated number. Loosening the match to span that gap
would flag a 0.499 bore in a 0.500 plate, which is what a legitimate tight fit
looks like, so the strict rule is deliberate and the 4088-A case stays open under
E028.
"""
from pipeline.schema import DrawingData, Feature, FeatureType
from pipeline.validator import ValidationReport, _check_feature_size_duplicates_envelope


def _part(hole_diameter: float) -> DrawingData:
    return DrawingData(
        units="inch", confidence=0.8,
        dimensions=[
            {"id": "D1", "type": "linear", "value": 1.75, "unit": "inch",
             "applies_to": "length"},
            {"id": "D2", "type": "linear", "value": 0.5, "unit": "inch",
             "applies_to": "width"},
            {"id": "D3", "type": "linear", "value": 0.5, "unit": "inch",
             "applies_to": "thickness"},
            {"id": "D4", "type": "diameter", "value": hole_diameter, "unit": "inch",
             "applies_to": "hole_diameter"},
        ],
        features=[
            Feature(id="F001", type=FeatureType.EXTRUDE_BOSS, description="key",
                    related_dimensions=["D1", "D2", "D3"], depth_dimension_id="D3"),
            Feature(id="F002", type=FeatureType.HOLE, description="c'bore",
                    related_dimensions=["D4"], parent_feature="F001"),
        ],
        build_order=["F001", "F002"],
    )


def _warns(model: DrawingData) -> list[str]:
    report = ValidationReport()
    _check_feature_size_duplicates_envelope(model, report)
    return [w for w in report.warnings if "F002" in w]


def test_a_hole_as_wide_as_the_part_is_flagged():
    warns = _warns(_part(0.5))
    assert warns, "a hole diameter equal to the part width must be flagged"
    assert "0.5" in warns[0] and "borrowed" in warns[0]


def test_a_normal_hole_passes_silently():
    assert _warns(_part(0.1875)) == []


def test_the_4088a_tolerance_band_case_is_NOT_caught():
    """Documents the known limit rather than pretending coverage.

    0.499 against a 0.500 envelope is not an exact repeat. Widening the match to
    catch it would flag legitimate tight-fit bores, so this case stays open under
    E028 and is recorded here so nobody assumes it is handled.
    """
    assert _warns(_part(0.499)) == []


def test_advisory_never_blocks():
    report = ValidationReport()
    _check_feature_size_duplicates_envelope(_part(0.5), report)
    assert report.errors == [] and report.ok
