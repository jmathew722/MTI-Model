"""PHASE 0 SPIKE v4 — find the import setting that yields NATIVE entities.

Default LoadFile4 import gives a drawing with one EMPTY view (no sketch segments,
no display dimensions). Either the DWG content came in as a non-native block, or
the import method must be set explicitly. This probes the IImportDwgDxfData object
returned by GetImportFileData: which property names exist, and which ImportMethod
value produces reachable native geometry.

For each candidate config it imports, then counts native content three ways:
  1. drawing views (GetViews) -> per-view sketch segs + display dims
  2. if it came in as a PART: the part's sketches + their segments
  3. document-level annotations

Prints which config, if any, yields non-zero native geometry.
"""
from __future__ import annotations

import sys
from pathlib import Path


def g(obj, name, *args):
    try:
        a = getattr(obj, name)
    except Exception as e:
        return None, f"attr-missing:{type(e).__name__}"
    try:
        return (a(*args) if callable(a) else a), None
    except Exception as e:
        return None, f"{type(e).__name__}:{str(e)[:80]}"


CANDIDATE_PROPS = [
    "ImportMethod", "ViewType", "ImportDimensions", "ImportAnnotations",
    "ImportPolylines", "ImportBlocksAsBlocks", "ImportSketchTogether",
    "MergePoints", "UnitsSource", "Units", "SelectAllLayers", "SheetNumber",
    "Import3DDrawing", "ImportToNewPartAsSketch", "DocTemplateName",
    "AddConstraints", "MergeEntities", "OpenAsSheet",
]


def introspect(import_data):
    print("  -- import_data property probe --")
    found = {}
    for p in CANDIDATE_PROPS:
        val, err = g(import_data, p)
        if err and "attr-missing" in err:
            continue
        found[p] = val
        print(f"     {p} = {val!r} {'(err '+err+')' if err else ''}")
    if not found:
        print("     (no candidate properties resolved by name)")
    return found


def count_native(sw, doc):
    """Return (doc_type, views, seg_count, dim_count, part_seg_count)."""
    import win32com.client
    dtype = g(doc, "GetType")[0]
    seg = dim = part_seg = 0
    views_raw, _ = g(doc, "GetViews")
    nviews = 0
    if views_raw:
        for grp in views_raw:
            for v in grp:
                nviews += 1
                v = win32com.client.Dispatch(v)
                dim += g(v, "GetDisplayDimensionCount")[0] or 0
                sk, _ = g(v, "GetSketch")
                if sk is not None:
                    segs, _ = g(win32com.client.Dispatch(sk), "GetSketchSegments")
                    if segs:
                        seg += len(segs)
    # If it imported as a PART (type 1), walk its feature sketches.
    if dtype == 1:
        feat, _ = g(doc, "FirstFeature")
        guard = 0
        while feat is not None and guard < 3000:
            guard += 1
            feat = win32com.client.Dispatch(feat)
            tn = g(feat, "GetTypeName2")[0] or g(feat, "GetTypeName")[0]
            if tn in ("ProfileFeature", "Sketch", "3DProfileFeature"):
                sk, _ = g(feat, "GetSpecificFeature2")
                if sk is not None:
                    segs, _ = g(win32com.client.Dispatch(sk), "GetSketchSegments")
                    if segs:
                        part_seg += len(segs)
            feat = g(feat, "GetNextFeature")[0]
    return dtype, nviews, seg, dim, part_seg


def run_config(sw, path, method, extra):
    import pythoncom
    import win32com.client
    from win32com.client import VARIANT

    import_data = sw.GetImportFileData(str(path))
    if import_data is None:
        return f"method={method}: GetImportFileData returned None"
    # Try to set the method + extras; record what stuck.
    set_log = []
    if method is not None:
        _, err = g(import_data, "ImportMethod")  # existence
        try:
            import_data.ImportMethod = method
            set_log.append(f"ImportMethod:={method}")
        except Exception as e:
            set_log.append(f"ImportMethod set-fail:{type(e).__name__}")
    for k, v in (extra or {}).items():
        try:
            setattr(import_data, k, v)
            set_log.append(f"{k}:={v}")
        except Exception as e:
            set_log.append(f"{k} set-fail:{type(e).__name__}")

    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(path), "r", import_data, errs)
    if doc is None:
        return f"cfg[m={method} {set_log}] -> LoadFile4 None (errs={errs.value})"
    dtype, nviews, seg, dim, part_seg = count_native(sw, doc)
    title = g(doc, "GetTitle")[0]
    try:
        sw.CloseDoc(title)
    except Exception:
        pass
    return (f"cfg[m={method} {set_log}] -> type={dtype} views={nviews} "
            f"draw_segs={seg} draw_dims={dim} part_segs={part_seg}")


def main():
    import pythoncom
    import win32com.client

    path = Path(sys.argv[1])
    pythoncom.CoInitialize()
    try:
        sw = win32com.client.Dispatch(win32com.client.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True

    print(f"=== {path.name} ===")
    print("Introspecting default import_data object:")
    introspect(sw.GetImportFileData(str(path)))

    print("\nTrying import configurations:")
    configs = [
        (None, None),                      # default
        (0, None),                         # -> new part 2D sketch (common)
        (1, None),                         # -> new part 3D
        (2, None),                         # -> new drawing
        (None, {"ImportDimensions": True, "ImportAnnotations": True}),
        (0, {"ImportDimensions": True, "ImportAnnotations": True,
             "AddConstraints": True, "MergeEntities": True}),
        (2, {"ImportDimensions": True, "ImportAnnotations": True}),
    ]
    for method, extra in configs:
        try:
            print(" ", run_config(sw, path, method, extra), flush=True)
        except Exception as e:
            print(f"  cfg[m={method}] EXCEPTION {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
