"""PHASE 0 SPIKE v2 — exhaustive single-file probe, NON-SWALLOWING.

The batch walk (dump_dwg_entities.py) reported 0 views for every imported DWG.
That is either the truth or an artifact of looking in the wrong place (imported
DWG content commonly lands as loose sketch/annotation objects ON THE SHEET, not
inside model-linked IViews). Because the batch swallowed exceptions, it cannot
tell those two cases apart. This script hammers ONE file through every documented
access path and prints the REAL exception text for each, so the finding is
conclusive either way.

Usage:
    webapp\\.venv\\Scripts\\python.exe dwg_native\\spike\\probe_one.py "C:\\path\\to\\file.dwg"
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def try_call(label, fn, *args):
    """Call fn(*args); print the outcome with the REAL error if it fails."""
    try:
        v = fn(*args) if callable(fn) else fn
        print(f"  [ok ] {label}: {v!r}"[:300])
        return v, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  [ERR] {label}: {msg}"[:300])
        return None, msg


def main():
    path = Path(sys.argv[1])
    import pythoncom
    import win32com.client
    from win32com.client import VARIANT

    pythoncom.CoInitialize()
    try:
        sw = win32com.client.Dispatch(win32com.client.GetActiveObject("SldWorks.Application"))
    except Exception:
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True

    print(f"== IMPORT {path.name} ==")
    import_data = sw.GetImportFileData(str(path))
    print(f"  import_data = {import_data!r}")
    # Dump EVERY attribute we can read off the import-data object.
    for name in dir(import_data):
        if name.startswith("_"):
            continue
        try:
            val = getattr(import_data, name)
            if not callable(val):
                print(f"    prop {name} = {val!r}"[:200])
        except Exception:
            pass

    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(path), "r", import_data, errs)
    print(f"  LoadFile4 -> doc={doc!r} errs={errs.value}")

    active = try_call("sw.ActiveDoc", lambda: sw.ActiveDoc)[0]
    for tag, d in (("LoadFile4-doc", doc), ("ActiveDoc", active)):
        if d is None:
            continue
        print(f"\n== DOC via {tag} ==")
        try_call("GetType", d.GetType)
        try_call("GetTitle", d.GetTitle)
        try_call("GetPathName", d.GetPathName)
        # ---- drawing-view access paths ------------------------------------
        try_call("GetFirstView", d.GetFirstView)
        views, _ = try_call("GetViews", d.GetViews)
        if views:
            try:
                print(f"    GetViews returned {len(views)} sheet-group(s)")
                for gi, grp in enumerate(views):
                    try:
                        print(f"      sheet[{gi}] has {len(grp)} view(s): "
                              + ", ".join(str(getattr(v, 'GetName2', lambda: '?')()) for v in grp))
                    except Exception as e:
                        print(f"      sheet[{gi}] enum error: {e}")
            except Exception as e:
                print(f"    GetViews enum error: {e}")
        try_call("GetSheetCount", d.GetSheetCount)
        try_call("GetSheetNames", d.GetSheetNames)
        cur, _ = try_call("GetCurrentSheet", d.GetCurrentSheet)
        # ---- document-level annotation walk (view-independent) ------------
        n_ann = 0
        ann, err = try_call("GetFirstAnnotation2", d.GetFirstAnnotation2)
        if ann is None and err:
            ann = try_call("GetFirstAnnotation", d.GetFirstAnnotation)[0]
        types = {}
        guard = 0
        while ann is not None and guard < 20000:
            guard += 1
            n_ann += 1
            try:
                t = ann.GetType()
            except Exception:
                t = "?"
            types[t] = types.get(t, 0) + 1
            try:
                ann = ann.GetNext3()
            except Exception:
                try:
                    ann = ann.GetNext2()
                except Exception:
                    ann = None
        print(f"    document-level annotations walked: {n_ann}, type histogram: {types}")
        # ---- feature-tree walk (imported entities often appear as features)
        feat, _ = try_call("FirstFeature", d.FirstFeature)
        feats = []
        guard = 0
        while feat is not None and guard < 5000:
            guard += 1
            try:
                feats.append((feat.Name, feat.GetTypeName2()))
            except Exception:
                try:
                    feats.append((feat.Name, feat.GetTypeName()))
                except Exception:
                    feats.append(("?", "?"))
            try:
                feat = feat.GetNextFeature()
            except Exception:
                feat = None
        print(f"    feature-tree features: {len(feats)}")
        for nm, tp in feats[:40]:
            print(f"       - {nm}  [{tp}]")

    print("\n== DONE ==")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
