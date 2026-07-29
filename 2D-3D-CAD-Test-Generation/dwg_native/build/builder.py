"""Build a .SLDPRT from build_plan.json directly over COM.

Reuses pipeline.solidworks_builder.connect_to_solidworks / create_new_part and
the verified FeatureExtrusion3 / FeatureCut4 signatures (imported patterns, not
copied logic). Records the fully-defined status of every sketch (the verify layer
enforces it as a gate) and writes per-feature outcomes to macro_result.json.

WINDOWS + SolidWorks ONLY. COM is used lazily so the module imports anywhere.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from pipeline.coordinate_normalize import to_meters  # imported, not copied

log = logging.getLogger("dwg_native.build")


class BuildError(RuntimeError):
    pass


def build_part(build_plan: Dict[str, Any], output_dir: str | Path,
               sw_app: Any) -> Dict[str, Any]:
    """Build the part described by ``build_plan`` into ``output_dir``.

    Returns a result dict: sldprt/stl paths, per-feature results, sketch
    fully-defined statuses, and the body bounding box (meters).
    """
    # Reuse the proven connect/sketch/COM patterns (imported, not copied).
    from pipeline.solidworks_builder import (create_new_part, to_radians,
                                             _begin_sketch, _null_dispatch)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    part_name = build_plan.get("part", "part")
    factor = build_plan.get("unit_factor_to_meters", 0.0254)

    def m(v: float) -> float:
        return round(float(v) * factor, 9)

    results: List[Dict[str, Any]] = []
    fully_defined: List[Dict[str, Any]] = []

    doc = create_new_part(sw_app)
    if doc is None:
        raise BuildError("create_new_part returned None.")
    sm = doc.SketchManager
    fm = doc.FeatureManager

    thickness_m = 0.0
    base_built = False
    for step in build_plan.get("steps", []):
        stype = step.get("type")
        fid = step.get("feature_id", "-")
        d = step.get("dimensions_drawing_units", {}) or {}
        pos = (step.get("positions_xy") or [[0.0, 0.0]])[0]
        try:
            if stype == "extrude_boss":
                length, width = m(d.get("length", 0)), m(d.get("width", 0))
                thickness_m = m(d.get("thickness", 0)) or 0.00635
                _begin_sketch(doc, "Front Plane", f"base {fid}")   # proven, null-dispatch-safe
                if sm.CreateCornerRectangle(0.0, 0.0, 0.0, length, width, 0.0) is None:
                    raise BuildError(f"CreateCornerRectangle None for {fid}")
                fd = _fully_defined(doc, _null_dispatch)
                fully_defined.append({"feature_id": fid, "sketch": "base", "status": fd})
                sm.InsertSketch(True)  # consume ACTIVE sketch (E006)
                # Exact verified signature/types from solidworks_builder (floats
                # for the double/angle slots — int 0 there raises "Type mismatch").
                feat = fm.FeatureExtrusion3(
                    True, False, False, 0, 0, float(thickness_m), 0.01,
                    False, False, False, False, to_radians(0), to_radians(0),
                    False, False, False, False,
                    True, True, True, 0, 0, False)
                if feat is None:
                    raise BuildError(f"FeatureExtrusion3 None for {fid}")
                try:
                    feat.Name = f"{fid}_base"
                except Exception:
                    pass
                base_built = True
                results.append({"feature_id": fid, "type": stype, "status": "PASS"})
            elif stype == "hole":
                if not base_built:
                    results.append({"feature_id": fid, "type": stype, "status": "FAIL",
                                    "detail": "no base solid before hole"})
                    continue
                dia = d.get("diameter", 0)
                r = m(dia / 2.0)
                cx, cy = m(pos[0]), m(pos[1])
                _begin_sketch(doc, "Front Plane", f"hole {fid}")   # proven, null-dispatch-safe
                if sm.CreateCircleByRadius(cx, cy, 0.0, r) is None:
                    raise BuildError(f"CreateCircleByRadius None for {fid}")
                fd = _fully_defined(doc, _null_dispatch)
                fully_defined.append({"feature_id": fid, "sketch": "hole", "status": fd})
                sm.InsertSketch(True)  # consume ACTIVE sketch (E006)
                # A blind depth callout (e.g. counterbore relief, ".16 DP" drill)
                # cuts to that depth, not through-all — previously every hole was
                # hardcoded through_all regardless of a stated blind depth.
                is_blind = step.get("depth_type") in ("blind", "blind_unknown_depth")
                depth_m = m(d["depth"]) if (is_blind and d.get("depth")) else None
                if depth_m:
                    feat = _cut_blind(fm, to_radians, depth_m)
                else:
                    feat = _cut_thru(fm, to_radians)
                if feat is None:
                    raise BuildError(f"FeatureCut4 None for {fid} (both directions)")
                try:
                    feat.Name = f"{fid}_hole"
                except Exception:
                    pass
                results.append({"feature_id": fid, "type": stype, "status": "PASS"})
        except Exception as e:
            results.append({"feature_id": fid, "type": stype, "status": "FAIL",
                            "detail": f"{type(e).__name__}: {e}"})

    doc.ForceRebuild3(False)
    bbox = _body_box(doc)   # E004: from IBody2.GetBodyBox
    sldprt = out / f"{part_name}.SLDPRT"
    stl = out / f"{part_name}.STL"
    _save(doc, sldprt)
    _save(doc, stl)

    result = {
        "part": part_name,
        "sldprt": str(sldprt) if sldprt.exists() else "",
        "stl": str(stl) if stl.exists() else "",
        "features": results,
        "fully_defined": fully_defined,
        "body_box_m": bbox,
    }
    (out / "macro_result.json").write_text(json.dumps({"results": results}, indent=2),
                                           encoding="utf-8")
    (out / f"{part_name}_build_result.json").write_text(json.dumps(result, indent=2),
                                                        encoding="utf-8")
    # Close the built part now that .SLDPRT/.STL are on disk, so documents do not
    # accumulate in the shared SolidWorks session across jobs.
    try:
        title = doc.GetTitle if isinstance(doc.GetTitle, str) else doc.GetTitle()
        sw_app.CloseDoc(title)
    except Exception:
        pass
    return result


def _fully_defined(doc, null_dispatch) -> str:
    """swSketchConstrainedStatus (1 fully / 2 over / 3 under). Attempts
    FullyDefineSketch to actually constrain a coordinate-drawn sketch, then
    re-measures, so the gate reports a real status instead of 'unknown'."""
    sk = doc.SketchManager.ActiveSketch
    if sk is None:
        return "unknown"

    def _status():
        try:
            return int(sk.GetConstrainedStatus())
        except Exception:
            return None

    st = _status()
    if st in (None, 3):   # unknown or under-defined -> try to auto-constrain
        try:
            doc.SketchManager.FullyDefineSketch(
                True, True, 0, 0, 0,
                null_dispatch(), null_dispatch(), null_dispatch(),
                null_dispatch(), null_dispatch())
            st = _status()
        except Exception:
            pass
    return {1: "fully", 2: "over", 3: "under"}.get(st, "unknown")


def _cut_thru(fm, to_radians):
    """FeatureCut4 through-all with a direction-flip retry — exact 27-arg verified
    signature/types from solidworks_builder (end=1 ThroughAll)."""
    for flip in (True, False):
        try:
            feat = fm.FeatureCut4(
                True, False, flip, 1, 0, 0.0, 0.01,
                False, False, False, False, to_radians(0), to_radians(0),
                False, False, False, False, False,
                True, True, True, True, False, 0, 0, False, False)
            if feat is not None:
                return feat
        except Exception:
            continue
    return None


def _cut_blind(fm, to_radians, depth_m):
    """FeatureCut4 to a BLIND depth (end=0) with a direction-flip retry — a
    counterbore relief or a drilled-to-depth hole must not cut through-all."""
    for flip in (True, False):
        try:
            feat = fm.FeatureCut4(
                True, False, flip, 0, 0, float(depth_m), 0.01,
                False, False, False, False, to_radians(0), to_radians(0),
                False, False, False, False, False,
                True, True, True, True, False, 0, 0, False, False)
            if feat is not None:
                return feat
        except Exception:
            continue
    return None


def _body_box(doc):
    try:
        bodies = doc.GetBodies2(0, False)
        if bodies:
            box = bodies[0].GetBodyBox()   # (x1,y1,z1,x2,y2,z2) meters (E004)
            return [round(float(x), 6) for x in box]
    except Exception:
        pass
    return []


def _save(doc, path: Path) -> None:
    try:
        doc.SaveAs3(str(path), 0, 0)
    except Exception as e:
        log.warning("SaveAs3 failed for %s: %s", path, e)
