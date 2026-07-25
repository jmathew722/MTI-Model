"""PHASE 0 — findings collector across a batch. Answers the verdict questions.

Per DWG, with the import CONFIGURED (ImportToDrawing + ImportDimensions +
AddSketchConstraints):
  1. view count + native IDisplayDimension count (are dims queryable objects?)
  2. doc-level annotation histogram (any dimension annotations at all?)
  3. DXF round-trip entity histogram (what FORM did content take: DIMENSION vs
     MTEXT + LINE/ARC), i.e. exact object types dimensions arrive as
Writes out_findings/<stem>.json + _summary.json with a per-file yes/no on
"numeric dimension objects recovered".
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pythoncom
import win32com.client as win32
from win32com.client import VARIANT

OUT = Path(__file__).resolve().parent / "out_findings"


def pput(o, n, *a):
    oo = o._oleobj_
    return oo.Invoke(oo.GetIDsOfNames(n), 0, pythoncom.DISPATCH_PROPERTYPUT, False, *a)


def call(o, n, *a, default=None):
    try:
        x = getattr(o, n)
        return x(*a) if callable(x) else x
    except Exception:
        return default


ANN_TYPE = {1: "displayDim", 2: "note", 3: "balloon", 4: "surfFinish", 5: "datumTag",
            6: "gtol", 9: "centerMark", 10: "cThread"}


def import_cfg(sw, path, method=1):
    idata = sw.GetImportFileData(str(path))
    for prop, val in (("ImportMethod", method), ("ImportDimensions", True),
                      ("AddSketchConstraints", True)):
        try:
            pput(idata, prop, 0, val)
        except Exception:
            pass
    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    return sw.LoadFile4(str(path), "r", idata, errs), errs.value


def native_dims(doc):
    views = call(doc, "GetViews")
    vcount = ddcount = 0
    sample = []
    if views:
        for grp in views:
            for v in grp:
                vcount += 1
                v = win32.Dispatch(v)
                n = call(v, "GetDisplayDimensionCount", default=0) or 0
                ddcount += n
                if n:
                    arr = call(v, "GetDisplayDimensions") or []
                    for disp in arr[:5]:
                        disp = win32.Dispatch(disp)
                        dim = call(disp, "GetDimension")
                        if dim is not None:
                            sample.append(call(win32.Dispatch(dim), "GetSystemValue2", ""))
    return vcount, ddcount, sample


def doc_annotation_hist(doc):
    hist = Counter()
    ann = call(doc, "GetFirstAnnotation2")
    guard = 0
    while ann is not None and guard < 20000:
        guard += 1
        ann = win32.Dispatch(ann)
        t = call(ann, "GetType")
        hist[ANN_TYPE.get(t, f"type{t}")] += 1
        ann = call(ann, "GetNext3") or call(ann, "GetNext2")
    return dict(hist)


def dxf_hist(path):
    try:
        import ezdxf
        d = ezdxf.readfile(str(path))
        return dict(Counter(e.dxftype() for e in d.modelspace()))
    except Exception as e:
        return f"err:{type(e).__name__}:{str(e)[:50]}"


def main():
    pythoncom.CoInitialize()
    try:
        sw = win32.Dispatch(win32.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32.Dispatch("SldWorks.Application"); sw.Visible = True
    OUT.mkdir(parents=True, exist_ok=True)

    files = [Path(a) for a in sys.argv[1:]]
    summary = []
    for f in files:
        rec = {"file": f.name}
        doc, errs = import_cfg(sw, f, method=1)
        rec["load_errs"] = errs
        if doc is None:
            rec["import_ok"] = False
            summary.append(rec); print(f"{f.name}: LoadFile4 None"); continue
        rec["import_ok"] = True
        rec["doc_type"] = call(doc, "GetType")
        vc, dd, sample = native_dims(doc)
        rec["view_count"] = vc
        rec["native_display_dimension_count"] = dd
        rec["native_dim_value_samples_m"] = sample
        rec["doc_annotation_histogram"] = doc_annotation_hist(doc)
        # DXF round-trip
        out = Path(tempfile.gettempdir()) / (f.stem + "_findings.dxf")
        call(doc, "SaveAs3", str(out), 0, 0)
        rec["dxf_entity_histogram"] = dxf_hist(out) if out.exists() else "no-file"
        rec["numeric_dimension_objects_recovered"] = dd > 0
        summary.append(rec)
        (OUT / (f.stem + ".json")).write_text(json.dumps(rec, indent=2, default=str),
                                              encoding="utf-8")
        print(f"{f.name}: type={rec['doc_type']} views={vc} native_dims={dd} "
              f"doc_ann={rec['doc_annotation_histogram']} "
              f"dxf={rec['dxf_entity_histogram']}", flush=True)
        try:
            sw.CloseDoc(call(doc, "GetTitle"))
        except Exception:
            pass
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                       encoding="utf-8")
    n = len(summary)
    rec_yes = sum(1 for s in summary if s.get("numeric_dimension_objects_recovered"))
    print(f"\n==> {rec_yes}/{n} files recovered ANY native numeric dimension object.")


if __name__ == "__main__":
    main()
