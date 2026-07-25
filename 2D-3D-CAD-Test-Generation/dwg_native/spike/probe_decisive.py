"""PHASE 0 SPIKE v8 — decisive: does content import AT ALL, and in what form?

Enum (swImportDxfDwg_ImportMethod_e): 0=DoNotImportSheet, 1=ImportToDrawing,
2=ImportToPartSketch, 3=ImportToExistingDrawing, 4=ImportToExistingPart.

For method 1 (drawing) and method 2 (part sketch), this:
  * reports doc type of BOTH the LoadFile4 return and sw.ActiveDoc,
  * lists the FULL feature tree of a part result (so a converted sketch shows),
  * counts sketch segments in every sketch feature,
  * round-trips the import to DXF and counts entities with ezdxf,
so we know definitively whether geometry landed and where.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pythoncom
import win32com.client as win32
from win32com.client import VARIANT


def pget(obj, name, *a):
    oo = obj._oleobj_
    return oo.Invoke(oo.GetIDsOfNames(name), 0, pythoncom.DISPATCH_PROPERTYGET, True, *a)


def pput(obj, name, *a):
    oo = obj._oleobj_
    return oo.Invoke(oo.GetIDsOfNames(name), 0, pythoncom.DISPATCH_PROPERTYPUT, False, *a)


def call(obj, name, *a, default=None):
    try:
        x = getattr(obj, name)
        return x(*a) if callable(x) else x
    except Exception:
        return default


def import_configured(sw, path, method):
    idata = sw.GetImportFileData(str(path))
    try:
        pput(idata, "ImportMethod", 0, method)
        pput(idata, "ImportDimensions", 0, True)
        pput(idata, "AddSketchConstraints", 0, True)
    except Exception as e:
        print(f"    config err: {e}")
    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(path), "r", idata, errs)
    return doc, errs.value


def walk_features(doc, limit=60):
    feats = []
    f = call(doc, "FirstFeature")
    guard = 0
    while f is not None and guard < 5000:
        guard += 1
        f = win32.Dispatch(f)
        nm = call(f, "Name")
        tn = call(f, "GetTypeName2") or call(f, "GetTypeName")
        seg = None
        if tn in ("ProfileFeature", "Sketch", "DetailCircle", "3DProfileFeature"):
            sk = call(f, "GetSpecificFeature2")
            if sk is not None:
                segs = call(win32.Dispatch(sk), "GetSketchSegments")
                seg = len(segs) if segs else 0
        feats.append((nm, tn, seg))
        f = call(f, "GetNextFeature")
    return feats


def count_dxf_entities(path):
    try:
        import ezdxf
    except Exception:
        return "ezdxf-not-installed"
    try:
        doc = ezdxf.readfile(str(path))
        msp = doc.modelspace()
        from collections import Counter
        c = Counter(e.dxftype() for e in msp)
        return dict(c)
    except Exception as e:
        return f"read-err:{type(e).__name__}:{str(e)[:60]}"


def main():
    pythoncom.CoInitialize()
    try:
        sw = win32.Dispatch(win32.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32.Dispatch("SldWorks.Application"); sw.Visible = True

    path = Path(sys.argv[1])
    print(f"=== {path.name} ===")
    for method, label in ((1, "ImportToDrawing"), (2, "ImportToPartSketch")):
        print(f"\n-- method {method} ({label}) --")
        doc, errs = import_configured(sw, path, method)
        print(f"  LoadFile4 errs={errs} return_doc={doc is not None}")
        active = call(sw, "ActiveDoc")
        for tag, d in (("return", doc), ("active", active)):
            if d is None:
                print(f"  [{tag}] None"); continue
            dt = call(d, "GetType")
            title = call(d, "GetTitle")
            print(f"  [{tag}] type={dt} title={title!r}")
            if dt == 1:  # part -> list features
                feats = walk_features(d)
                nseg = sum(s for _, _, s in feats if isinstance(s, int))
                print(f"       part features={len(feats)} total_sketch_segs={nseg}")
                for nm, tn, s in feats[:25]:
                    print(f"         - {nm} [{tn}] segs={s}")
            elif dt == 3:  # drawing -> view count + sheet sketch
                views = call(d, "GetViews")
                nv = sum(len(g) for g in views) if views else 0
                print(f"       drawing views={nv}")
        # round-trip to DXF to see if geometry actually imported
        if doc is not None:
            out = Path(tempfile.gettempdir()) / (path.stem + f"_m{method}.dxf")
            ok = call(doc, "SaveAs3", str(out), 0, 0)
            ents = count_dxf_entities(out) if out.exists() else "no-file"
            print(f"  DXF round-trip saveas={ok} -> entities={ents}")
            try:
                sw.CloseDoc(call(doc, "GetTitle"))
            except Exception:
                pass


if __name__ == "__main__":
    main()
