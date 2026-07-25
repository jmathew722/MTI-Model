"""PHASE 0 FEASIBILITY SPIKE — disposable. Optimize for a fast, honest answer.

The whole DWG-native project rests on ONE unverified assumption: that when a DWG
is imported into SolidWorks and converted to SOLIDWORKS entities, its dimensions
arrive as real IDisplayDimension / IDimension objects whose numeric values are
queryable — not as flat text, not as loose sketch geometry with detached labels.

This script imports a batch of REAL DWGs via the SolidWorks COM API (the proven
GetImportFileData + LoadFile4 path already used in pipeline/vector_extract/
dwg_convert.py) and dumps a FULL, UNFILTERED inventory of everything reachable in
the resulting document to JSON, so FINDINGS.md can be written from measured fact.

It is deliberately defensive: every COM call is wrapped, every unrecognized object
is recorded (never silently skipped), and results are flushed per-file so a single
hung import cannot lose the batch. Throwaway code — not wired into any package.

Usage (from 2D-3D-CAD-Test-Generation/, Windows, SolidWorks installed):
    webapp\\.venv\\Scripts\\python.exe dwg_native\\spike\\dump_dwg_entities.py FILE1.dwg FILE2.dwg ...
    webapp\\.venv\\Scripts\\python.exe dwg_native\\spike\\dump_dwg_entities.py --dir "C:\\path\\with\\dwgs" --limit 10

Output: dwg_native/spike/out/<stem>.json (one per file) + out/_summary.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "out"

# ---- Best-effort enum decoders (raw int is always recorded too) ------------- #
# These are labels for readability only; the raw integer is authoritative.
SW_DOC_TYPE = {1: "PART", 2: "ASSEMBLY", 3: "DRAWING", 4: "DRAWING_TEMPLATE"}
SW_VIEW_TYPE = {  # swDrawingViewTypes_e (tentative labels, raw int authoritative)
    0: "auxiliary?", 1: "named/std", 2: "projected", 3: "section",
    4: "detail", 5: "relative", 6: "broken?", 7: "empty?",
}
SW_ANNOTATION_TYPE = {  # swAnnotationType_e (tentative labels)
    1: "displayDimension", 2: "note", 3: "balloon", 4: "surfaceFinish",
    5: "datumTag", 6: "gtol", 7: "weld", 8: "datumTargetSym",
    9: "centerMark", 10: "cThread", 11: "multiJog", 12: "dowelSym",
}
SW_SEG_TYPE = {  # swSketchSegments_e
    0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola",
}
SW_LENGTH_UNIT = {  # swLengthUnit_e (tentative)
    0: "mm", 1: "cm", 2: "m", 3: "inch", 4: "feet", 5: "feet_inch",
}


def _safe(fn, *args, default=None):
    """Call a COM method/attr, swallowing any failure — returns default."""
    try:
        v = fn(*args) if callable(fn) else fn
        return v
    except Exception:
        return default


def _getattr_call(obj, name, *args, default=None):
    """Best-effort: call obj.name(*args) if it exists, else default."""
    try:
        attr = getattr(obj, name)
    except Exception:
        return default
    try:
        return attr(*args) if callable(attr) else attr
    except Exception:
        return default


def _point_xyz(pt):
    """An ISketchPoint / IMathPoint-ish object -> [x, y, z] in meters."""
    if pt is None:
        return None
    x = _getattr_call(pt, "X")
    y = _getattr_call(pt, "Y")
    z = _getattr_call(pt, "Z")
    if x is None and y is None:
        # Some point objects expose an array via ArrayData / GetData.
        arr = _getattr_call(pt, "ArrayData") or _getattr_call(pt, "GetArrayData")
        if arr:
            try:
                return [float(arr[0]), float(arr[1]), float(arr[2])]
            except Exception:
                return {"unrecognized_point": str(type(pt))}
        return {"unrecognized_point": str(type(pt))}
    return [x, y, z]


# --------------------------------------------------------------------------- #
# Dimension / annotation probes — the crux of the spike
# --------------------------------------------------------------------------- #
def probe_display_dimension(disp, idx):
    """Everything we can learn about ONE IDisplayDimension. The central question:
    does GetDimension() return a live IDimension with a readable numeric value?"""
    rec = {"index": idx}

    # The live IDimension driving object — THE thing the project's premise needs.
    dim = _getattr_call(disp, "GetDimension")
    rec["get_dimension_returns_object"] = dim is not None
    if dim is not None:
        rec["dim_full_name"] = _getattr_call(dim, "FullName") or _getattr_call(dim, "GetNameForSelection")
        rec["dim_name"] = _getattr_call(dim, "Name")
        # GetSystemValue2("") is documented to return the value in METERS.
        rec["system_value_m"] = _getattr_call(dim, "GetSystemValue2", "")
        rec["value_property"] = _getattr_call(dim, "Value")  # user-unit value
        rec["dimension_type_raw"] = _getattr_call(dim, "GetType")

    # The annotation wrapper carries type, name, text, position, attachment.
    ann = _getattr_call(disp, "GetAnnotation")
    if ann is not None:
        atype = _getattr_call(ann, "GetType")
        rec["annotation_type_raw"] = atype
        rec["annotation_type"] = SW_ANNOTATION_TYPE.get(atype, f"raw:{atype}")
        rec["annotation_name"] = _getattr_call(ann, "GetName")
        rec["position_2d_m"] = _safe(lambda: list(_getattr_call(ann, "GetPosition") or []))
        # Geometry association: does the dimension know what it measures?
        assoc = _probe_association(ann)
        rec["association"] = assoc

    # The rendered text (what the user SEES) — separate from the numeric value.
    rec["displayed_text"] = (
        _getattr_call(disp, "GetText", 0)  # swDimensionTextParts: 0=all? try a few
        or _getattr_call(disp, "GetText", 1)
    )
    rec["text_override"] = None
    # A driving vs. reference (driven) flag if exposed.
    rec["driven"] = _getattr_call(disp, "IsReference")
    return rec


def _probe_association(ann):
    """Does this annotation carry a link to the geometry it measures?
    Tries several documented method names; records exactly what worked so the
    findings can state the real association signal (or its absence)."""
    for meth in ("GetAttachedEntities3", "GetAttachedEntities2", "GetAttachedEntities"):
        res = _getattr_call(ann, meth)
        if res is not None:
            try:
                n = len(res)
            except Exception:
                n = "non-countable"
            return {"method": meth, "attached_entity_count": n}
    # Attachment points are a weaker signal but still an association.
    pts = _getattr_call(ann, "GetAttachPointCount") or _getattr_call(ann, "GetAttachedPointCount")
    if pts is not None:
        return {"method": "attach_point_count", "count": pts}
    return {"method": None, "note": "no association method returned data"}


def probe_note(ann):
    """A note annotation -> its text string + position."""
    note = _getattr_call(ann, "GetSpecificAnnotation")
    text = _getattr_call(note, "GetText") if note is not None else None
    if text is None:
        text = _getattr_call(ann, "GetText")
    return {
        "type": "note",
        "type_raw": _getattr_call(ann, "GetType"),
        "name": _getattr_call(ann, "GetName"),
        "text": text,
        "position_2d_m": _safe(lambda: list(_getattr_call(ann, "GetPosition") or [])),
    }


def probe_generic_annotation(ann, unrecognized):
    """Any annotation that is not a note — record its type + text + position, and
    push genuinely unknown types into the unrecognized bucket."""
    atype = _getattr_call(ann, "GetType")
    rec = {
        "type_raw": atype,
        "type": SW_ANNOTATION_TYPE.get(atype, f"UNKNOWN:{atype}"),
        "name": _getattr_call(ann, "GetName"),
        "text": _getattr_call(ann, "GetText"),
        "position_2d_m": _safe(lambda: list(_getattr_call(ann, "GetPosition") or [])),
    }
    if atype not in SW_ANNOTATION_TYPE:
        unrecognized.append({"kind": "annotation", "type_raw": atype,
                             "name": rec["name"]})
    return rec


def probe_sketch_segment(seg, unrecognized):
    """A sketch segment -> type + endpoint/center/radius geometry (best-effort)."""
    st = _getattr_call(seg, "GetType")
    rec = {"type_raw": st, "type": SW_SEG_TYPE.get(st, f"UNKNOWN:{st}")}
    rec["construction"] = _getattr_call(seg, "ConstructionGeometry")
    if st == 0:  # line
        rec["start_2d_m"] = _point_xyz(_getattr_call(seg, "GetStartPoint2"))
        rec["end_2d_m"] = _point_xyz(_getattr_call(seg, "GetEndPoint2"))
    elif st == 1:  # arc / circle
        rec["center_2d_m"] = _point_xyz(_getattr_call(seg, "GetCenterPoint2"))
        rec["radius_m"] = _getattr_call(seg, "GetRadius")
        rec["is_circle"] = _getattr_call(seg, "IsCircle")
        rec["start_2d_m"] = _point_xyz(_getattr_call(seg, "GetStartPoint2"))
        rec["end_2d_m"] = _point_xyz(_getattr_call(seg, "GetEndPoint2"))
    elif st == 2:  # ellipse
        rec["center_2d_m"] = _point_xyz(_getattr_call(seg, "GetCenterPoint2"))
    else:
        unrecognized.append({"kind": "sketch_segment", "type_raw": st})
    return rec


# --------------------------------------------------------------------------- #
# View + document walk
# --------------------------------------------------------------------------- #
def walk_view(view, unrecognized):
    rec = {
        "name": _getattr_call(view, "GetName2") or _getattr_call(view, "Name"),
        "type_raw": _getattr_call(view, "Type"),
    }
    rec["type"] = SW_VIEW_TYPE.get(rec["type_raw"], f"raw:{rec['type_raw']}")

    # ---- display dimensions: THE central probe -----------------------------
    dd_count = _getattr_call(view, "GetDisplayDimensionCount", default=0) or 0
    rec["display_dimension_count"] = dd_count
    dds = []
    if dd_count:
        arr = _getattr_call(view, "GetDisplayDimensions")
        if arr:
            for j, disp in enumerate(arr):
                try:
                    dds.append(probe_display_dimension(disp, j))
                except Exception as e:  # never let one bad object kill the walk
                    dds.append({"index": j, "probe_error": f"{type(e).__name__}: {e}"})
                    unrecognized.append({"kind": "display_dimension_probe_error",
                                         "index": j, "error": str(e)})
    rec["display_dimensions"] = dds

    # ---- all annotations on the view (notes, GTOLs, datums, …) --------------
    annotations = []
    ann = _getattr_call(view, "GetFirstAnnotation2") or _getattr_call(view, "GetFirstAnnotation")
    guard = 0
    while ann is not None and guard < 5000:
        guard += 1
        atype = _getattr_call(ann, "GetType")
        try:
            if atype == 2:  # note
                annotations.append(probe_note(ann))
            elif atype == 1:  # displayDimension already covered above; note presence
                annotations.append({"type": "displayDimension(ref)", "type_raw": 1,
                                    "name": _getattr_call(ann, "GetName")})
            else:
                annotations.append(probe_generic_annotation(ann, unrecognized))
        except Exception as e:
            annotations.append({"annotation_probe_error": f"{type(e).__name__}: {e}"})
        ann = _getattr_call(ann, "GetNext3") or _getattr_call(ann, "GetNext2") \
            or _getattr_call(ann, "GetNext")
    rec["annotations"] = annotations
    rec["annotation_count_walked"] = len(annotations)

    # ---- sketch segments ----------------------------------------------------
    segs = []
    sketch = _getattr_call(view, "GetSketch")
    if sketch is not None:
        seg_arr = _getattr_call(sketch, "GetSketchSegments")
        if seg_arr:
            for seg in seg_arr:
                try:
                    segs.append(probe_sketch_segment(seg, unrecognized))
                except Exception as e:
                    segs.append({"segment_probe_error": f"{type(e).__name__}: {e}"})
    rec["sketch_segment_count"] = len(segs)
    rec["sketch_segments"] = segs
    return rec


def import_and_dump(sw, dwg_path: Path):
    """Import ONE DWG and return its full inventory dict."""
    import pythoncom
    from win32com.client import VARIANT

    result = {"source_file": dwg_path.name, "source_path": str(dwg_path),
              "unrecognized_objects": []}
    unrecognized = result["unrecognized_objects"]

    import_data = _getattr_call(sw, "GetImportFileData", str(dwg_path))
    result["import_data_type"] = str(type(import_data)) if import_data is not None else None
    # Record any import-data properties we can read (import method / view type).
    if import_data is not None:
        result["import_data_props"] = {
            k: _getattr_call(import_data, k)
            for k in ("ImportMethod", "ViewType", "ImportDimensions",
                      "ImportBlocksAsBlocks", "MergePoints", "UnitsSource")
        }

    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = _getattr_call(sw, "LoadFile4", str(dwg_path), "r", import_data, errs)
    result["load_errors_raw"] = getattr(errs, "value", None)
    if doc is None:
        result["import_ok"] = False
        result["note"] = "LoadFile4 returned None — DWG did not import as a document."
        return result

    result["import_ok"] = True
    dtype = _getattr_call(doc, "GetType")
    result["doc_type_raw"] = dtype
    result["doc_type"] = SW_DOC_TYPE.get(dtype, f"UNKNOWN:{dtype}")
    result["doc_title"] = _getattr_call(doc, "GetTitle")

    # Units
    unit_raw = _getattr_call(doc, "GetUserPreferenceIntegerValue", 51)  # swUnitsLinear=51 (tentative)
    result["units_detected_raw"] = unit_raw
    result["units_detected"] = SW_LENGTH_UNIT.get(unit_raw, f"raw:{unit_raw}")

    # A drawing exposes GetFirstView / GetNextView. If it imported as something
    # else (part/sketch), record that plainly — it is itself a finding.
    views = []
    view = _getattr_call(doc, "GetFirstView")
    if view is None:
        result["note"] = ("No GetFirstView — document is not a drawing or has no "
                          f"views (doc_type={result['doc_type']}).")
        unrecognized.append({"kind": "no_views", "doc_type": result["doc_type"]})
    guard = 0
    while view is not None and guard < 2000:
        guard += 1
        try:
            views.append(walk_view(view, unrecognized))
        except Exception as e:
            views.append({"view_walk_error": f"{type(e).__name__}: {e}",
                          "trace": traceback.format_exc()})
        view = _getattr_call(view, "GetNextView")
    result["views"] = views
    result["view_count"] = len(views)

    # ---- roll up the numbers that decide GREEN/YELLOW/RED ------------------
    total_dd = sum(v.get("display_dimension_count", 0) or 0 for v in views
                   if isinstance(v, dict))
    live_dims, valued_dims, associated = 0, 0, 0
    for v in views:
        if not isinstance(v, dict):
            continue
        for d in v.get("display_dimensions", []):
            if d.get("get_dimension_returns_object"):
                live_dims += 1
            if isinstance(d.get("system_value_m"), (int, float)):
                valued_dims += 1
            assoc = d.get("association") or {}
            if assoc.get("attached_entity_count") not in (None, 0, "non-countable") \
               or assoc.get("count") not in (None, 0):
                associated += 1
    result["rollup"] = {
        "total_display_dimensions": total_dd,
        "dimensions_with_live_object": live_dims,
        "dimensions_with_numeric_value_m": valued_dims,
        "dimensions_with_geometry_association": associated,
    }

    # Clean up so open docs don't accumulate across the batch.
    _getattr_call(sw, "CloseDoc", result.get("doc_title") or _getattr_call(doc, "GetTitle"))
    return result


def connect():
    """Late-bound connect (mirrors pipeline/solidworks_builder.connect_to_solidworks)."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        active = win32com.client.GetActiveObject("SldWorks.Application")
        sw = win32com.client.Dispatch(active)
    except Exception:
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True
    # Suppress interactive dialogs that would deadlock an unattended import.
    # swInputDimValOnCreate = 4 ; swSketchInference = ... ; keep minimal + safe.
    for toggle in (4,):  # swUserPreferenceToggle_e.swInputDimValOnCreate
        try:
            sw.SetUserPreferenceToggle(toggle, False)
        except Exception:
            pass
    try:
        sw.SetUserPreferenceIntegerValue(9, 2)  # swUnitSystem -> ignore failure
    except Exception:
        pass
    return sw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="DWG paths")
    ap.add_argument("--dir", help="import every *.dwg under this directory")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files: list[Path] = [Path(f) for f in args.files]
    if args.dir:
        files += sorted(Path(args.dir).glob("*.[dD][wW][gG]"))
    # De-dup, keep existing only.
    seen, uniq = set(), []
    for f in files:
        rp = f.resolve()
        if rp in seen or not f.exists():
            continue
        seen.add(rp)
        uniq.append(f)
    if args.limit:
        uniq = uniq[: args.limit]
    if not uniq:
        print("No DWG files given. Pass paths or --dir.", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Connecting to SolidWorks…", flush=True)
    sw = connect()
    try:
        print(f"SolidWorks revision: {_getattr_call(sw, 'RevisionNumber')}", flush=True)
    except Exception:
        pass

    summary = []
    for i, f in enumerate(uniq, 1):
        print(f"[{i}/{len(uniq)}] importing {f.name} …", flush=True)
        try:
            rec = import_and_dump(sw, f)
        except Exception as e:
            rec = {"source_file": f.name, "import_ok": False,
                   "fatal_error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc()}
        out = OUT_DIR / (f.stem + ".json")
        out.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
        roll = rec.get("rollup", {})
        summary.append({
            "file": f.name,
            "import_ok": rec.get("import_ok"),
            "doc_type": rec.get("doc_type"),
            "view_count": rec.get("view_count"),
            "units": rec.get("units_detected"),
            **roll,
            "unrecognized_count": len(rec.get("unrecognized_objects", [])),
        })
        r = summary[-1]
        print(f"    -> ok={r['import_ok']} type={r.get('doc_type')} views={r.get('view_count')} "
              f"dims={r.get('total_display_dimensions')} live={r.get('dimensions_with_live_object')} "
              f"valued={r.get('dimensions_with_numeric_value_m')} "
              f"assoc={r.get('dimensions_with_geometry_association')}", flush=True)
        # Flush the running summary after every file (crash-safe).
        (OUT_DIR / "_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print("\nDONE. Per-file dumps + _summary.json in", OUT_DIR, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
