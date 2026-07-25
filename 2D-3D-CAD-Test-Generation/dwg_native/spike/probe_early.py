"""PHASE 0 SPIKE v5 — EARLY-BOUND import-data configuration.

Late binding could not SET IImportDwgDxfData properties. Generate the SldWorks
2024 type library wrappers and CastTo the import-data object so its real, typed
property setters are available (the same power VBA has). Then find the import
config that yields native SW entities, and walk the result via early-bound
IDrawingDoc / IView (GetFirstView now reachable).
"""
from __future__ import annotations

import sys
from pathlib import Path

SLDWORKS_TLB = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"


def main():
    import pythoncom
    import win32com.client
    from win32com.client import gencache, VARIANT

    pythoncom.CoInitialize()
    # Generate early-bound wrappers for the SldWorks 2024 (v20.0) type library.
    try:
        mod = gencache.EnsureModule(SLDWORKS_TLB, 0, 20, 0)
        print(f"EnsureModule OK: {mod}")
    except Exception as e:
        print(f"EnsureModule FAILED: {type(e).__name__}: {e}")
        mod = None

    try:
        sw = win32com.client.Dispatch(win32com.client.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True

    path = Path(sys.argv[1])
    print(f"\n=== {path.name} ===")
    import_data = sw.GetImportFileData(str(path))

    # CastTo the early-bound interface so property SETTERS exist.
    cast = None
    for iface in ("IImportDwgDxfData", "IImportDxfDwgData", "ImportDwgDxfData"):
        try:
            cast = win32com.client.CastTo(import_data, iface)
            if cast is not None:
                print(f"CastTo({iface}) OK -> {cast}")
                break
        except Exception as e:
            print(f"CastTo({iface}) failed: {type(e).__name__}: {str(e)[:80]}")
    target = cast if cast is not None else import_data

    # Show real properties now visible on the early-bound object.
    print("\nEarly-bound import_data members (non-callable, readable):")
    shown = 0
    for name in sorted(dir(target)):
        if name.startswith("_"):
            continue
        try:
            val = getattr(target, name)
            if not callable(val):
                print(f"   {name} = {val!r}"[:160])
                shown += 1
        except Exception:
            pass
    if not shown:
        print("   (none readable)")

    # Try to set import-to-drawing + convert entities + import dims/annotations.
    def attempt(**kw):
        idata = sw.GetImportFileData(str(path))
        try:
            c = win32com.client.CastTo(idata, "IImportDwgDxfData")
        except Exception:
            c = idata
        applied = []
        for k, v in kw.items():
            try:
                setattr(c, k, v)
                applied.append(f"{k}={v}")
            except Exception as e:
                applied.append(f"{k}!set({type(e).__name__})")
        errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.LoadFile4(str(path), "r", idata, errs)
        if doc is None:
            return f"applied[{applied}] -> LoadFile4 None errs={errs.value}"
        # early-bound drawing walk
        try:
            ddoc = win32com.client.CastTo(doc, "IDrawingDoc")
        except Exception:
            ddoc = doc
        dtype = doc.GetType
        seg = dim = nviews = 0
        try:
            v = ddoc.GetFirstView
        except Exception:
            v = None
        # GetFirstView may be a property in early binding; handle both
        if callable(getattr(ddoc, "GetFirstView", None)):
            v = ddoc.GetFirstView()
        guard = 0
        while v is not None and guard < 2000:
            guard += 1
            nviews += 1
            try:
                dim += v.GetDisplayDimensionCount()
            except Exception:
                pass
            try:
                sk = v.GetSketch()
                segs = sk.GetSketchSegments() if sk else None
                if segs:
                    seg += len(segs)
            except Exception:
                pass
            try:
                v = v.GetNextView()
            except Exception:
                v = None
        title = doc.GetTitle
        try:
            sw.CloseDoc(title if isinstance(title, str) else doc.GetTitle())
        except Exception:
            pass
        return f"applied[{applied}] -> type={dtype} views={nviews} segs={seg} dims={dim}"

    print("\nConfiguration attempts (early-bound):")
    for kw in (
        {},
        {"ImportMethod": 2},                       # to new drawing
        {"ImportMethod": 0},                       # to new part 2D sketch
        {"ImportMethod": 2, "ImportDimensions": True, "ImportAnnotations": True},
        {"ImportMethod": 0, "ImportDimensions": True, "ImportAnnotations": True,
         "AddConstraints": True},
    ):
        try:
            print("  ", attempt(**kw), flush=True)
        except Exception as e:
            print(f"   attempt {kw} EXCEPTION {type(e).__name__}: {str(e)[:100]}", flush=True)


if __name__ == "__main__":
    main()
