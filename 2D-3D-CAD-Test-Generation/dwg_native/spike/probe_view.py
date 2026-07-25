"""PHASE 0 SPIKE v3 — walk INTO the view via GetViews(), the path that works.

v2 established that GetFirstView() is unreachable through win32com late binding
(IDrawingDoc methods raise "Member not found" on the IModelDoc2 IDispatch), but
GetViews() DOES work and returns real view objects. This script wraps each view
and asks the question the whole project depends on, per view:

  - GetDisplayDimensionCount / GetDisplayDimensions
  - per display dimension: does GetDimension() return a live object, and does
    GetSystemValue2("") yield a numeric value (meters)?
  - the view's annotations (notes / GTOLs / datums) via GetFirstAnnotation2
  - the view's sketch segments (lines / circles) as a geometry sanity check

Prints a blunt per-view + per-file tally.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out_v3"


def g(obj, name, *args):
    """Best-effort call; returns (value, error_str_or_None)."""
    try:
        a = getattr(obj, name)
    except Exception as e:
        return None, f"attr:{type(e).__name__}"
    try:
        return (a(*args) if callable(a) else a), None
    except Exception as e:
        return None, f"{type(e).__name__}:{e}"[:120]


def probe_dim(disp):
    import win32com.client
    disp = win32com.client.Dispatch(disp)
    rec = {}
    dim, err = g(disp, "GetDimension")
    rec["get_dimension_ok"] = dim is not None
    if err:
        rec["get_dimension_err"] = err
    if dim is not None:
        dim = win32com.client.Dispatch(dim)
        rec["value_m"] = g(dim, "GetSystemValue2", "")[0]
        rec["value_user_units"] = g(dim, "Value")[0]
        rec["name"] = g(dim, "FullName")[0] or g(dim, "Name")[0]
    ann, _ = g(disp, "GetAnnotation")
    if ann is not None:
        ann = win32com.client.Dispatch(ann)
        rec["ann_type"] = g(ann, "GetType")[0]
        pos, _ = g(ann, "GetPosition")
        rec["pos_2d_m"] = list(pos) if pos else None
        # geometry association
        for m in ("GetAttachedEntities3", "GetAttachedEntities2", "GetAttachedEntities"):
            res, _ = g(ann, m)
            if res is not None:
                try:
                    rec["attached_entities"] = len(res)
                except Exception:
                    rec["attached_entities"] = "non-countable"
                rec["attached_via"] = m
                break
    txt, _ = g(disp, "GetText", 0)
    rec["displayed_text"] = txt
    return rec


def probe_view(v):
    import win32com.client
    v = win32com.client.Dispatch(v)
    rec = {"name": g(v, "GetName2")[0] or g(v, "Name")[0],
           "type": g(v, "Type")[0]}
    dd_count, err = g(v, "GetDisplayDimensionCount")
    rec["display_dimension_count"] = dd_count
    if err:
        rec["dd_count_err"] = err
    dims = []
    if dd_count:
        arr, _ = g(v, "GetDisplayDimensions")
        if arr:
            for disp in arr:
                dims.append(probe_dim(disp))
    rec["display_dimensions"] = dims

    # annotations on the view
    types = {}
    notes = []
    ann, _ = g(v, "GetFirstAnnotation2")
    guard = 0
    while ann is not None and guard < 20000:
        guard += 1
        ann = win32com.client.Dispatch(ann)
        t = g(ann, "GetType")[0]
        types[t] = types.get(t, 0) + 1
        if t == 2:  # note
            spec, _ = g(ann, "GetSpecificAnnotation")
            txt = g(win32com.client.Dispatch(spec), "GetText")[0] if spec is not None else None
            notes.append(txt)
        ann = g(ann, "GetNext3")[0] or g(ann, "GetNext2")[0]
    rec["annotation_type_histogram"] = types
    rec["notes"] = notes

    # sketch segments
    seg_types = {}
    sk, _ = g(v, "GetSketch")
    if sk is not None:
        sk = win32com.client.Dispatch(sk)
        segs, _ = g(sk, "GetSketchSegments")
        if segs:
            for s in segs:
                st = g(win32com.client.Dispatch(s), "GetType")[0]
                seg_types[st] = seg_types.get(st, 0) + 1
    rec["sketch_segment_type_histogram"] = seg_types
    return rec


def main():
    import pythoncom
    import win32com.client
    from win32com.client import VARIANT

    OUT.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    try:
        sw = win32com.client.Dispatch(win32com.client.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True

    files = [Path(a) for a in sys.argv[1:]]
    summary = []
    for f in files:
        print(f"\n=== {f.name} ===", flush=True)
        import_data = sw.GetImportFileData(str(f))
        errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.LoadFile4(str(f), "r", import_data, errs)
        if doc is None:
            print("  LoadFile4 returned None"); continue
        title = g(doc, "GetTitle")[0]
        views_raw, _ = g(doc, "GetViews")
        views = []
        if views_raw:
            for grp in views_raw:
                for v in grp:
                    views.append(probe_view(v))
        rec = {"file": f.name, "title": title, "view_count": len(views), "views": views}
        (OUT / (f.stem + ".json")).write_text(json.dumps(rec, indent=2, default=str),
                                              encoding="utf-8")
        tot_dd = sum(v["display_dimension_count"] or 0 for v in views)
        valued = sum(1 for v in views for d in v["display_dimensions"]
                     if isinstance(d.get("value_m"), (int, float)))
        assoc = sum(1 for v in views for d in v["display_dimensions"]
                    if isinstance(d.get("attached_entities"), int) and d["attached_entities"] > 0)
        tot_notes = sum(len(v["notes"]) for v in views)
        tot_segs = sum(sum(v["sketch_segment_type_histogram"].values()) for v in views)
        print(f"  views={len(views)} display_dims={tot_dd} valued_numeric={valued} "
              f"geom_assoc={assoc} notes={tot_notes} sketch_segs={tot_segs}", flush=True)
        summary.append({"file": f.name, "views": len(views), "display_dims": tot_dd,
                        "valued_numeric": valued, "geom_assoc": assoc,
                        "notes": tot_notes, "sketch_segs": tot_segs})
        try:
            sw.CloseDoc(title)
        except Exception:
            pass
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n== SUMMARY ==")
    for s in summary:
        print(" ", s)


if __name__ == "__main__":
    main()
