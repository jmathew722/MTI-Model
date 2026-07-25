"""PHASE 0 SPIKE v6 — DEFINITIVE. Early-bound, correctly-configured import.

Now that the SldWorks v32 type library is generated, IImportDxfDwgData exposes
real setters: SetImportMethod(sheet, value), SetImportDimensions(sheet, bool),
SetAddSketchConstraints(sheet, bool). This configures the import to "new drawing
+ convert to SW entities + import dimensions", imports, then walks the result
via the early-bound IDrawingDoc (GetFirstView now reachable) and reports, per
import method, how many native display dimensions carry numeric values.

This is the script whose numbers decide GREEN / YELLOW / RED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

GUID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
OUT = Path(__file__).resolve().parent / "out_final"


def wrap(mod, obj, iface):
    """Wrap a raw SW IDispatch in its early-bound generated class WITHOUT CastTo
    (CastTo calls GetTypeInfo, which SW objects refuse). Instantiating the
    generated DispatchBaseClass subclass around the dispatch is the documented
    win32com workaround."""
    if obj is None:
        return None
    try:
        return getattr(mod, iface)(obj)
    except Exception:
        return obj


def walk_drawing(mod, doc):
    """Early-bound walk of a drawing doc -> per-view dims/annotations/segments."""
    ddoc = wrap(mod, doc, "IDrawingDoc")
    views = []
    try:
        v = ddoc.GetFirstView()  # the sheet, then real views via GetNextView
    except Exception as e:
        return {"first_view_error": f"{type(e).__name__}: {str(e)[:80]}", "views": []}
    guard = 0
    while v is not None and guard < 2000:
        guard += 1
        vrec = {}
        try:
            vrec["name"] = v.GetName2()
        except Exception:
            vrec["name"] = None
        # display dimensions
        dims = []
        try:
            n = v.GetDisplayDimensionCount()
        except Exception:
            n = 0
        vrec["display_dimension_count"] = n
        if n:
            try:
                arr = v.GetDisplayDimensions()
            except Exception:
                arr = None
            for disp in (arr or []):
                d = {}
                try:
                    dd = wrap(mod, disp, "IDisplayDimension")
                    dim = dd.GetDimension2(0) if hasattr(dd, "GetDimension2") else dd.GetDimension()
                except Exception as e:
                    dim = None
                    d["get_dimension_error"] = f"{type(e).__name__}: {str(e)[:60]}"
                if dim is not None:
                    try:
                        d["value_m"] = dim.GetSystemValue2("")
                    except Exception as e:
                        d["value_error"] = str(e)[:60]
                    try:
                        d["name"] = dim.FullName
                    except Exception:
                        pass
                dims.append(d)
        vrec["display_dimensions"] = dims
        # sketch segments
        segcount = 0
        try:
            sk = v.GetSketch()
            segs = sk.GetSketchSegments() if sk else None
            segcount = len(segs) if segs else 0
        except Exception:
            pass
        vrec["sketch_segment_count"] = segcount
        # annotation count on the view
        try:
            vrec["annotation_count"] = v.GetAnnotationCount()
        except Exception:
            vrec["annotation_count"] = None
        views.append(vrec)
        try:
            v = v.GetNextView()
        except Exception:
            v = None
    return {"views": views}


def try_import(sw, mod, pythoncom, VARIANT, path, method, import_dims, constraints):
    idata = sw.GetImportFileData(str(path))
    cfg = {"requested_method": method}
    c = wrap(mod, idata, "IImportDxfDwgData")
    if c is idata:
        return {"cast_error": "could not wrap IImportDxfDwgData"}
    try:
        cfg["sheet_count"] = c.GetSheetCount()
    except Exception as e:
        cfg["sheet_count_error"] = str(e)[:60]
    try:
        cfg["default_method"] = c.ImportMethod(0)
    except Exception as e:
        cfg["default_method_error"] = str(e)[:60]
    if method is not None:
        try:
            c.SetImportMethod(0, method)
            cfg["set_method_ok"] = True
        except Exception as e:
            cfg["set_method_error"] = f"{type(e).__name__}: {str(e)[:60]}"
    if import_dims:
        try:
            c.SetImportDimensions(0, True)
            cfg["set_dims_ok"] = True
        except Exception as e:
            cfg["set_dims_error"] = str(e)[:60]
    if constraints:
        try:
            c.SetAddSketchConstraints(0, True)
        except Exception:
            pass

    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(path), "r", idata, errs)
    cfg["load_errs"] = errs.value
    if doc is None:
        cfg["result"] = "LoadFile4 None"
        return cfg
    try:
        cfg["doc_type"] = doc.GetType()
    except Exception:
        cfg["doc_type"] = None
    dtype = cfg["doc_type"]
    if dtype == 3:  # drawing
        cfg.update(walk_drawing(mod, doc))
    elif dtype == 1:  # part (2D sketch import)
        cfg["result"] = "imported as PART (2D sketch) — walking features"
        seg = 0
        try:
            f = doc.FirstFeature()
            guard = 0
            while f is not None and guard < 3000:
                guard += 1
                try:
                    tn = f.GetTypeName2()
                except Exception:
                    tn = ""
                if tn in ("ProfileFeature", "Sketch"):
                    try:
                        sk = f.GetSpecificFeature2()
                        segs = sk.GetSketchSegments()
                        seg += len(segs) if segs else 0
                    except Exception:
                        pass
                f = f.GetNextFeature()
        except Exception as e:
            cfg["part_walk_error"] = str(e)[:60]
        cfg["part_sketch_segment_count"] = seg
    # rollup
    views = cfg.get("views", [])
    cfg["total_display_dims"] = sum(v.get("display_dimension_count", 0) or 0 for v in views)
    cfg["dims_with_numeric_value"] = sum(
        1 for v in views for d in v.get("display_dimensions", [])
        if isinstance(d.get("value_m"), (int, float)))
    cfg["total_sketch_segs"] = (sum(v.get("sketch_segment_count", 0) or 0 for v in views)
                                or cfg.get("part_sketch_segment_count", 0))
    try:
        sw.CloseDoc(doc.GetTitle())
    except Exception:
        try:
            sw.CloseDoc(doc.GetTitle)
        except Exception:
            pass
    return cfg


def main():
    import pythoncom
    import win32com.client as win32
    from win32com.client import gencache, VARIANT

    mod = gencache.EnsureModule(GUID, 0, 32, 0)
    pythoncom.CoInitialize()
    try:
        sw = win32.Dispatch(win32.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32.Dispatch("SldWorks.Application")
        sw.Visible = True

    OUT.mkdir(parents=True, exist_ok=True)
    files = [Path(a) for a in sys.argv[1:]]
    all_results = {}
    for f in files:
        print(f"\n=== {f.name} ===", flush=True)
        per_file = []
        # method None=default; 0,1,2 candidate ImportMethods; with dims+constraints
        for method in (None, 0, 1, 2):
            r = try_import(sw, mod, pythoncom, VARIANT, f, method,
                           import_dims=True, constraints=True)
            per_file.append(r)
            print(f"  method={method}: default={r.get('default_method')} "
                  f"doc_type={r.get('doc_type')} views={len(r.get('views', []))} "
                  f"dims={r.get('total_display_dims')} "
                  f"numeric={r.get('dims_with_numeric_value')} "
                  f"segs={r.get('total_sketch_segs')} "
                  f"{r.get('set_method_error','')}", flush=True)
        all_results[f.name] = per_file
        (OUT / (f.stem + ".json")).write_text(json.dumps(per_file, indent=2, default=str),
                                              encoding="utf-8")
    (OUT / "_all.json").write_text(json.dumps(all_results, indent=2, default=str),
                                   encoding="utf-8")


if __name__ == "__main__":
    main()
