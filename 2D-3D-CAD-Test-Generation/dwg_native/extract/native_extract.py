"""Read the SolidWorks-converted DXF into raw_extraction.json.

SolidWorks did the DWG parse (importer.py); here we read the resulting DXF with
ezdxf and record exact geometry + exact MTEXT with 2D positions. NOTHING is
interpreted. Anything the walker cannot classify goes into
``unrecognized_objects`` — never silently dropped.

All coordinates are converted to METERS via the DXF $INSUNITS header (inch/mm),
so downstream can hand them straight to pipeline.coordinate_normalize.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional

from .schema import (DisplayDimension, Geometry, RawExtraction, TextToken, View)

log = logging.getLogger("dwg_native.extract")

# DXF $INSUNITS -> meters-per-unit. 1=inch, 4=mm, 5=cm, 6=m, 2=feet.
_INSUNITS_TO_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}
_KNOWN_GEOM = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "POINT", "INSERT"}
_TEXT_TYPES = {"MTEXT", "TEXT", "ATTRIB"}
# Recognised-but-cosmetic; counted in notes, not treated as features or unknowns.
_IGNORED = {"HATCH", "SOLID", "WIPEOUT", "LEADER", "DIMENSION", "TOLERANCE",
            "VIEWPORT", "SPLINE"}


def _p2(pt, k) -> List[float]:
    return [round(float(pt[0]) * k, 9), round(float(pt[1]) * k, 9)]


def extract_raw(source_file: str, dxf_path: str | Path,
                sw_doc: Any = None, session: Any = None,
                extra_notes: Optional[List[str]] = None) -> RawExtraction:
    """Build a RawExtraction from the SolidWorks-converted DXF.

    ``sw_doc``/``session`` are optional; if given, native IDisplayDimensions are
    recorded honestly (Phase 0: usually value-less shells) for provenance.
    """
    import ezdxf  # imported here so the module loads without ezdxf present

    doc = ezdxf.readfile(str(dxf_path))
    insunits = int(doc.header.get("$INSUNITS", 1) or 1)
    k = _INSUNITS_TO_M.get(insunits, 0.0254)
    units = {0.0254: "inch", 0.001: "mm", 0.01: "cm", 1.0: "m", 0.3048: "feet"}.get(k, "inch")

    msp = doc.modelspace()
    geometry: List[Geometry] = []
    text_tokens: List[TextToken] = []
    unrecognized: List[dict] = []
    ignored_hist: Counter = Counter()
    gid = tid = 0

    for e in msp:
        etype = e.dxftype()
        try:
            if etype == "LINE":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="line",
                    start_2d_m=_p2(e.dxf.start, k), end_2d_m=_p2(e.dxf.end, k),
                    layer=str(getattr(e.dxf, "layer", "")),
                    construction=_is_construction(e)))
            elif etype == "CIRCLE":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="circle",
                    center_2d_m=_p2(e.dxf.center, k), radius_m=round(float(e.dxf.radius) * k, 9),
                    layer=str(getattr(e.dxf, "layer", "")),
                    construction=_is_construction(e)))
            elif etype == "ARC":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="arc",
                    center_2d_m=_p2(e.dxf.center, k), radius_m=round(float(e.dxf.radius) * k, 9),
                    layer=str(getattr(e.dxf, "layer", "")),
                    construction=_is_construction(e)))
            elif etype == "ELLIPSE":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="ellipse", center_2d_m=_p2(e.dxf.center, k),
                    layer=str(getattr(e.dxf, "layer", ""))))
            elif etype == "POINT":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="point", center_2d_m=_p2(e.dxf.location, k),
                    layer=str(getattr(e.dxf, "layer", ""))))
            elif etype == "INSERT":
                gid += 1
                geometry.append(Geometry(
                    id=f"G{gid}", type="insert", center_2d_m=_p2(e.dxf.insert, k),
                    layer=str(getattr(e.dxf, "layer", ""))))
            elif etype in _TEXT_TYPES:
                txt = _text_of(e)
                pos = _text_pos(e, k)
                if txt is not None and pos is not None:
                    tid += 1
                    text_tokens.append(TextToken(
                        id=f"T{tid}", text=txt, position_2d_m=pos,
                        height_m=round(float(getattr(e.dxf, "char_height", 0) or
                                             getattr(e.dxf, "height", 0) or 0) * k, 9),
                        layer=str(getattr(e.dxf, "layer", ""))))
            elif etype in _IGNORED:
                ignored_hist[etype] += 1
            else:
                unrecognized.append({"kind": "dxf_entity", "type": etype,
                                     "layer": str(getattr(e.dxf, "layer", ""))})
        except Exception as ex:  # never let one bad entity abort the extraction
            unrecognized.append({"kind": "entity_read_error", "type": etype,
                                 "error": f"{type(ex).__name__}: {ex}"})

    # Sheet extents from geometry bounding box (meters).
    sheet = _sheet_extents(geometry)

    view = View(name=_call(sw_doc, "GetTitle") or "Model", type=1,
                geometry=geometry, text_tokens=text_tokens,
                display_dimensions=_native_display_dims(sw_doc, session))

    notes = list(extra_notes or [])
    if ignored_hist:
        notes.append("ignored cosmetic/exploded entities: "
                     + ", ".join(f"{t}×{n}" for t, n in ignored_hist.items()))
    notes.append(f"units detected from $INSUNITS={insunits} -> {units}")
    if not view.display_dimensions:
        notes.append("no native IDisplayDimension objects (Phase 0 RED expected); "
                     "dimension values read from MTEXT tokens")

    return RawExtraction(source_file=source_file, units_detected=units,
                         sheet=sheet, views=[view],
                         unrecognized_objects=unrecognized, import_notes=notes)


def _is_construction(e) -> bool:
    try:
        return str(getattr(e.dxf, "linetype", "")).upper() in ("CENTER", "PHANTOM", "HIDDEN")
    except Exception:
        return False


def _text_of(e) -> Optional[str]:
    try:
        if e.dxftype() == "MTEXT":
            return e.plain_text() if hasattr(e, "plain_text") else str(e.text)
        return str(e.dxf.text)
    except Exception:
        return None


def _text_pos(e, k) -> Optional[List[float]]:
    try:
        if e.dxftype() == "MTEXT":
            return _p2(e.dxf.insert, k)
        return _p2(e.dxf.insert, k)
    except Exception:
        try:
            return _p2(e.dxf.align_point, k)
        except Exception:
            return None


def _sheet_extents(geometry: List[Geometry]) -> dict:
    xs, ys = [], []
    for g in geometry:
        for pt in (g.start_2d_m, g.end_2d_m, g.center_2d_m):
            if pt:
                xs.append(pt[0]); ys.append(pt[1])
        if g.center_2d_m and g.radius_m:
            xs += [g.center_2d_m[0] - g.radius_m, g.center_2d_m[0] + g.radius_m]
            ys += [g.center_2d_m[1] - g.radius_m, g.center_2d_m[1] + g.radius_m]
    if not xs:
        return {}
    return {"width_m": round(max(xs) - min(xs), 6), "height_m": round(max(ys) - min(ys), 6),
            "min_x_m": round(min(xs), 6), "min_y_m": round(min(ys), 6)}


def _native_display_dims(sw_doc, session) -> List[DisplayDimension]:
    """Best-effort record of native display dims (Phase 0: usually value-less)."""
    if sw_doc is None:
        return []
    out: List[DisplayDimension] = []
    try:
        import win32com.client as win32  # type: ignore
        views = _call(sw_doc, "GetViews") or []
        idx = 0
        for grp in views:
            for v in grp:
                v = win32.Dispatch(v)
                arr = _call(v, "GetDisplayDimensions") or []
                for disp in arr:
                    idx += 1
                    disp = win32.Dispatch(disp)
                    dim = _call(disp, "GetDimension")
                    val = None
                    if dim is not None:
                        val = _call(win32.Dispatch(dim), "GetSystemValue2", "")
                    out.append(DisplayDimension(
                        id=f"DD{idx}", value_m=val,
                        name=_call(win32.Dispatch(dim), "FullName") if dim else None,
                        text=_call(disp, "GetText", 0),
                        has_live_idimension=dim is not None))
    except Exception:
        pass
    return out


def _call(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    try:
        a = getattr(obj, name)
        return a(*args) if callable(a) else a
    except Exception:
        return default
