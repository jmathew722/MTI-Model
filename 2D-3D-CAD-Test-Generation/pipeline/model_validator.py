"""Post-build model validation.

After SolidWorks builds the part, confirm it actually represents the drawing:
a non-zero solid body exists, and the model's overall bounding box matches the
drawing's overall length/width/height dimensions within tolerance.

WINDOWS ONLY at call time (operates on a live SolidWorks document), but imports
cleanly anywhere. Public entry point: :func:`validate_model`.
"""
from __future__ import annotations

from typing import Any, Union

from pipeline.schema import DrawingData
from utils.logger import get_logger
from utils.unit_converter import to_meters

log = get_logger()

# Allowed mismatch between a model bounding-box edge and a drawing dimension.
# A drawing's overall envelope may not exactly equal any single dimension, so we
# use a generous relative tolerance plus an absolute floor.
REL_TOLERANCE = 0.05      # 5%
ABS_TOLERANCE_M = 0.0005  # 0.5 mm floor


def _coerce(data: Union[DrawingData, dict[str, Any]]) -> DrawingData:
    return data if isinstance(data, DrawingData) else DrawingData.model_validate(data)


def _dims_in_meters(model: DrawingData, applies_to: str) -> list[float]:
    """OVERALL-envelope dimension values (in meters) for one axis token.

    Only ENVELOPE dimensions count (``Dimension.is_envelope``: a clean
    length/width/height token, or a label declaring totality — never a
    feature-local size). The bounding box of the whole part can only be compared
    against dimensions that describe the whole part.

    Before 2026-08-16 this matched any dimension whose raw label was exactly
    "length", which swept in the resolver's DERIVED cut lengths: 16247 failed
    validation because a relief cut's inferred 4.8125" length was not among the
    part's overall extents — which it never should have been. Matching is on the
    CANONICAL token too, so "overall_height" is compared as height instead of
    being skipped for not being spelled "height".
    """
    feature_local = _feature_local_dimension_ids(model)
    out = []
    for d in model.dimensions:
        if not d.is_envelope or d.id in feature_local:
            continue
        if (d.canonical_applies_to or (d.applies_to or "").lower().strip()) == applies_to:
            out.append(to_meters(d.value, d.unit.value))
    return out


def _feature_local_dimension_ids(model: DrawingData) -> set[str]:
    """Dimension ids owned ONLY by a non-base feature — a cut's or a boss's own
    size, never the part's envelope.

    The resolver derives sizes for sub-features under generic labels: 16247's
    relief cut got ``D900 applies_to="length" = 4.8125``, which is a perfectly
    good label *within that feature* (the macro generator needs the key "length"
    to size the cut's rectangle) but is meaningless as a whole-part dimension.
    Comparing it to the part's bounding box failed a correct build.

    A dimension linked to the base solid, or to no feature at all (a free-
    standing drawing dimension), is NOT feature-local.
    """
    base_types = {"extrude_boss", "revolve"}
    base_ids: set[str] = set()
    other_ids: set[str] = set()
    for f in model.features:
        ids = set(f.related_dimensions or [])
        if f.depth_dimension_id:
            ids.add(f.depth_dimension_id)
        target = base_ids if str(getattr(f.type, "value", f.type)) in base_types else other_ids
        target |= ids
    return other_ids - base_ids


def _matches_any(actual_m: float, expected_values_m: list[float]) -> bool:
    for exp in expected_values_m:
        tol = max(ABS_TOLERANCE_M, REL_TOLERANCE * exp)
        if abs(actual_m - exp) <= tol:
            return True
    return False


def validate_model(sw_doc, drawing_data: Union[DrawingData, dict[str, Any]]) -> dict[str, Any]:
    """Verify the built model matches the drawing's dimensions.

    Args:
        sw_doc: The live SolidWorks document returned by ``build_model``.
        drawing_data: The validated drawing data the model was built from.

    Returns:
        A report dict with ``passed`` / ``failed`` / ``warnings`` lists, plus
        ``volume_mm3``, ``surface_area_mm2``, and the measured bounding box.
    """
    model = _coerce(drawing_data)
    report: dict[str, Any] = {"passed": [], "failed": [], "warnings": []}

    # --- Mass properties: confirms a solid body exists ---
    # The document-level mass-property objects (CreateMassProperty/2) are not
    # resolvable under late-bound dispatch (DISP_E_MEMBERNOTFOUND), so read mass
    # properties from the solid body itself: IBody2.GetMassProperties(density)
    # returns [comX, comY, comZ, Volume, SurfaceArea, Mass, ...] in SI units.
    try:
        bodies = sw_doc.GetBodies2(0, True)  # swBodyType_e.swSolidBody = 0
    except Exception as e:
        report["failed"].append(f"Could not enumerate solid bodies: {e}")
        return report
    if not bodies:
        report["failed"].append("CRITICAL: no solid body exists — no body was created.")
        return report

    # Measure the LARGEST solid body so a stray/disjoint body can't mask the part.
    def _body_volume(b) -> float:
        try:
            return float(b.GetMassProperties(1000.0)[3])
        except Exception:
            return 0.0

    body = max(bodies, key=_body_volume)
    if len(bodies) > 1:
        report["warnings"].append(
            f"{len(bodies)} disjoint solid bodies present — measuring the largest. "
            "Verify the part is a single connected body."
        )
    try:
        mp = body.GetMassProperties(1000.0)  # density irrelevant to volume/area
        volume_m3 = float(mp[3])
    except Exception as e:
        report["failed"].append(f"Could not read volume from body: {e}")
        return report

    if volume_m3 <= 0:
        report["failed"].append("CRITICAL: Part has zero volume — no solid body was created.")
        return report
    report["passed"].append("Solid body exists (volume > 0).")
    report["volume_mm3"] = volume_m3 * 1e9
    try:
        report["surface_area_mm2"] = float(mp[4]) * 1e6
    except Exception:
        report["warnings"].append("Could not read surface area.")

    # --- Bounding box vs overall drawing dimensions ---
    # IModelDoc2 has no GetModelBoundingBox; read the box from the solid body
    # itself (IBody2::GetBodyBox). [xmin,ymin,zmin,xmax,ymax,zmax] in meters.
    try:
        bbox = body.GetBodyBox()
        if bbox is None:
            report["warnings"].append("No solid body found to read a bounding box from.")
    except Exception as e:
        report["warnings"].append(f"Could not read bounding box: {e}")
        bbox = None

    if bbox and len(bbox) >= 6:
        extents_m = sorted(
            [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]], reverse=True
        )
        report["bounding_box_mm"] = [round(e * 1000, 4) for e in extents_m]

        # Compare each declared overall dimension to the closest bbox extent.
        for label in ("length", "width", "height"):
            expected = _dims_in_meters(model, label)
            if not expected:
                continue
            matched = any(_matches_any(e, expected) for e in extents_m)
            exp_mm = [round(v * 1000, 3) for v in expected]
            if matched:
                report["passed"].append(
                    f"{label}: a model extent matches drawing value(s) {exp_mm} mm."
                )
            else:
                report["failed"].append(
                    f"{label}: drawing value(s) {exp_mm} mm not found among model extents "
                    f"{report['bounding_box_mm']} mm (tolerance {REL_TOLERANCE:.0%})."
                )

    log.info(
        "Model validation: %d passed, %d failed, %d warnings.",
        len(report["passed"]),
        len(report["failed"]),
        len(report["warnings"]),
    )
    for f in report["failed"]:
        log.error("  [FAIL] %s", f)
    for w in report["warnings"]:
        log.warning("  [WARN] %s", w)

    report["ok"] = not report["failed"]
    return report
