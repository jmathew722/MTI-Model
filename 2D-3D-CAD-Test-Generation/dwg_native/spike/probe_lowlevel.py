"""PHASE 0 SPIKE v7 — WORKING probe. Low-level Invoke config + GetViews walk.

Established facts from v1-v6:
  * win32com late binding CANNOT set the parameterized IImportDxfDwgData props;
    CastTo fails (SW objects refuse GetTypeInfo); generated wrappers fail on
    InvokeTypes. But RAW IDispatch.Invoke with DISPATCH_PROPERTYPUT DOES work.
  * IDrawingDoc.GetFirstView is unreachable late-bound ("Member not found"),
    but IModelDoc2.GetViews() works and returns view objects.

So: configure the import via raw Invoke (ImportMethod / ImportDimensions /
AddSketchConstraints per sheet), import, and walk via GetViews(). For each
ImportMethod value, count how many native display dimensions carry a numeric
GetSystemValue2 value. The winning method (if any) answers the premise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32

OUT = Path(__file__).resolve().parent / "out_lowlevel"


def pget(obj, name, *args):
    """Property GET via raw Invoke (handles parameterized properties)."""
    oo = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
    did = oo.GetIDsOfNames(name)
    return oo.Invoke(did, 0, pythoncom.DISPATCH_PROPERTYGET, True, *args)


def pput(obj, name, *args):
    """Property PUT via raw Invoke; args = (indexArgs..., value)."""
    oo = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
    did = oo.GetIDsOfNames(name)
    return oo.Invoke(did, 0, pythoncom.DISPATCH_PROPERTYPUT, False, *args)


def call(obj, name, *args, default=None):
    try:
        a = getattr(obj, name)
        return a(*args) if callable(a) else a
    except Exception:
        return default


def walk_views(doc):
    """Late-bound GetViews walk -> per-view native display dims + segs + notes."""
    out = {"views": []}
    views_raw = call(doc, "GetViews")
    if not views_raw:
        return out
    for grp in views_raw:
        for v in grp:
            v = win32.Dispatch(v)
            rec = {"name": call(v, "GetName2") or call(v, "Name"),
                   "type": call(v, "Type")}
            n = call(v, "GetDisplayDimensionCount", default=0) or 0
            rec["display_dimension_count"] = n
            dims = []
            if n:
                arr = call(v, "GetDisplayDimensions")
                for disp in (arr or []):
                    disp = win32.Dispatch(disp)
                    d = {}
                    dim = call(disp, "GetDimension")
                    if dim is not None:
                        dim = win32.Dispatch(dim)
                        d["value_m"] = call(dim, "GetSystemValue2", "")
                        d["value_uu"] = call(dim, "Value")
                        d["name"] = call(dim, "FullName") or call(dim, "Name")
                    txt = call(disp, "GetText", 0)
                    d["text"] = txt
                    dims.append(d)
            rec["display_dimensions"] = dims
            # sketch segments
            seg = 0
            sk = call(v, "GetSketch")
            if sk is not None:
                segs = call(win32.Dispatch(sk), "GetSketchSegments")
                seg = len(segs) if segs else 0
            rec["sketch_segment_count"] = seg
            # notes
            notes = []
            ann = call(v, "GetFirstAnnotation2")
            guard = 0
            while ann is not None and guard < 10000:
                guard += 1
                ann = win32.Dispatch(ann)
                if call(ann, "GetType") == 2:
                    spec = call(ann, "GetSpecificAnnotation")
                    if spec is not None:
                        notes.append(call(win32.Dispatch(spec), "GetText"))
                ann = call(ann, "GetNext3") or call(ann, "GetNext2")
            rec["notes"] = notes
            out["views"].append(rec)
    return out


def walk_part_sketches(doc):
    """If the DWG imported as a PART (2D sketch), count sketch segments + dims."""
    seg = dim = 0
    f = call(doc, "FirstFeature")
    guard = 0
    while f is not None and guard < 5000:
        guard += 1
        f = win32.Dispatch(f)
        tn = call(f, "GetTypeName2") or call(f, "GetTypeName") or ""
        if tn in ("ProfileFeature", "Sketch"):
            sk = call(f, "GetSpecificFeature2")
            if sk is not None:
                sk = win32.Dispatch(sk)
                segs = call(sk, "GetSketchSegments")
                seg += len(segs) if segs else 0
                dims = call(sk, "GetDisplayDimensions") or call(sk, "GetDimensionCount")
                if isinstance(dims, int):
                    dim += dims
                elif dims:
                    dim += len(dims)
        f = call(f, "GetNextFeature")
    return {"part_sketch_segments": seg, "part_sketch_dims": dim}


def one_config(sw, path, method, import_dims=True, constraints=True):
    idata = sw.GetImportFileData(str(path))
    cfg = {"requested_method": method}
    try:
        cfg["default_method"] = pget(idata, "ImportMethod", 0)
        cfg["sheet_count"] = call(idata, "GetSheetCount")
    except Exception as e:
        cfg["read_err"] = f"{type(e).__name__}: {str(e)[:80]}"
    if method is not None:
        try:
            pput(idata, "ImportMethod", 0, method)
            cfg["set_method"] = pget(idata, "ImportMethod", 0)
        except Exception as e:
            cfg["set_method_err"] = f"{type(e).__name__}: {str(e)[:80]}"
    for prop, on in (("ImportDimensions", import_dims),
                     ("AddSketchConstraints", constraints),
                     ("ImportHatch", False)):
        if on is None:
            continue
        try:
            pput(idata, prop, 0, bool(on))
        except Exception:
            pass

    from win32com.client import VARIANT
    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(path), "r", idata, errs)
    cfg["load_errs"] = errs.value
    if doc is None:
        cfg["result"] = "LoadFile4 None"
        return cfg, None
    dtype = call(doc, "GetType")
    cfg["doc_type"] = dtype
    if dtype == 3:
        cfg.update(walk_views(doc))
    elif dtype == 1:
        cfg.update(walk_part_sketches(doc))
    views = cfg.get("views", [])
    cfg["total_display_dims"] = sum(v["display_dimension_count"] for v in views)
    cfg["dims_numeric"] = sum(1 for v in views for d in v["display_dimensions"]
                              if isinstance(d.get("value_m"), (int, float)))
    cfg["total_segs"] = (sum(v["sketch_segment_count"] for v in views)
                         or cfg.get("part_sketch_segments", 0))
    cfg["total_notes"] = sum(len(v.get("notes", [])) for v in views)
    title = call(doc, "GetTitle")
    return cfg, title


def main():
    pythoncom.CoInitialize()
    try:
        sw = win32.Dispatch(win32.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32.Dispatch("SldWorks.Application")
        sw.Visible = True
    OUT.mkdir(parents=True, exist_ok=True)

    files = [Path(a) for a in sys.argv[1:]]
    everything = {}
    for f in files:
        print(f"\n=== {f.name} ===", flush=True)
        per = []
        for method in (None, 0, 1, 2, 3):
            try:
                cfg, title = one_config(sw, f, method)
            except Exception as e:
                cfg, title = {"requested_method": method,
                              "EXCEPTION": f"{type(e).__name__}: {str(e)[:120]}"}, None
            per.append(cfg)
            print(f"  m={method} default={cfg.get('default_method')} "
                  f"set={cfg.get('set_method')} type={cfg.get('doc_type')} "
                  f"dims={cfg.get('total_display_dims')} numeric={cfg.get('dims_numeric')} "
                  f"segs={cfg.get('total_segs')} notes={cfg.get('total_notes')} "
                  f"{cfg.get('set_method_err','')}{cfg.get('EXCEPTION','')}", flush=True)
            if title:
                try:
                    sw.CloseDoc(title)
                except Exception:
                    pass
        everything[f.name] = per
        (OUT / (f.stem + ".json")).write_text(json.dumps(per, indent=2, default=str),
                                              encoding="utf-8")
    (OUT / "_all.json").write_text(json.dumps(everything, indent=2, default=str),
                                   encoding="utf-8")


if __name__ == "__main__":
    main()
