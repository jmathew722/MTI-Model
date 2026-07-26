"""Import a DWG through the SolidWorks API and expose its converted geometry.

Phase-0-proven path: GetImportFileData -> configure via raw IDispatch.Invoke
(ImportToDrawing + ImportDimensions + AddSketchConstraints) -> LoadFile4 ->
SaveAs3 to a DXF. SolidWorks owns the DWG parse; the extractor then reads the
SolidWorks-converted DXF with ezdxf (exact geometry + exact MTEXT). This is the
"SolidWorks owns the authoritative parser" contract from the brief, made to work
around the Phase-0 finding that dimension OBJECTS do not survive import.

WINDOWS + SolidWorks ONLY. COM imports are lazy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

log = logging.getLogger("dwg_native.importer")

# swImportDxfDwg_ImportMethod_e (verified against swconst.tlb in Phase 0)
IMPORT_TO_DRAWING = 1
IMPORT_TO_PART_SKETCH = 2


class ImportError_(RuntimeError):
    """DWG import through SolidWorks failed."""


@dataclass
class ImportResult:
    doc: Any                 # the SolidWorks drawing document (COM object)
    doc_title: str
    dxf_path: Path           # SolidWorks-converted DXF (read by the extractor)
    load_errors: int
    notes: List[str]
    pdf_path: Path = None    # SolidWorks-rendered PDF of the sheet (for the UI preview)


def import_dwg(session, dwg_path: str | Path, work_dir: str | Path) -> ImportResult:
    """Import ``dwg_path`` and produce a SolidWorks-converted DXF in ``work_dir``.

    ``session`` is a dwg_native.session.ComSession (provides .app + .pput/.pget).
    """
    import pythoncom  # type: ignore
    from win32com.client import VARIANT  # type: ignore

    sw = session.ensure() if hasattr(session, "ensure") else session.app
    src = Path(dwg_path)
    if not src.is_file():
        raise ImportError_(f"DWG not found: {src}")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    notes: List[str] = []

    idata = sw.GetImportFileData(str(src))
    if idata is None:
        raise ImportError_("GetImportFileData returned None — unsupported file?")
    # Configure the import via low-level parameterized-property puts (win32com
    # late binding cannot set these; established in Phase 0).
    for prop, value in (("ImportMethod", IMPORT_TO_DRAWING),
                        ("ImportDimensions", True),
                        ("AddSketchConstraints", True)):
        try:
            session.pput(idata, prop, 0, value)
        except Exception as e:  # non-fatal; note it and continue with defaults
            notes.append(f"could not set {prop}: {type(e).__name__}")

    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.LoadFile4(str(src), "r", idata, errs)
    if doc is None:
        raise ImportError_(f"LoadFile4 returned None (errs={errs.value}).")

    title = _call(doc, "GetTitle") or src.stem
    # SolidWorks SaveAs3 requires an ABSOLUTE Windows path (native separators);
    # a relative/forward-slash path writes nothing and returns silently.
    dxf = (work.resolve() / f"{src.stem}_swconv.dxf")
    dxf_win = str(dxf).replace("/", "\\")
    try:                                   # make sure the imported doc is active
        sw.ActivateDoc3(title, False, 2, VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0))
    except Exception:
        pass
    ok = _call(doc, "SaveAs3", dxf_win, 0, 0)
    if not dxf.is_file():
        raise ImportError_(
            f"SolidWorks produced no DXF from the imported drawing (saveas={ok}, "
            f"path={dxf_win}).")
    notes.append(f"SolidWorks converted DWG -> DXF ({dxf.name}); saveas={ok}")

    # Also render the imported sheet to PDF straight from SolidWorks (it already
    # has the drawing open) — this is the drawing view the UI shows on the left.
    pdf = (work.resolve() / f"{src.stem}_sheet.pdf")
    try:
        _call(doc, "SaveAs3", str(pdf).replace("/", "\\"), 0, 0)
        if pdf.is_file():
            notes.append(f"rendered sheet PDF ({pdf.name})")
        else:
            pdf = None
            notes.append("sheet PDF export produced no file (preview falls back to geometry)")
    except Exception as e:
        pdf = None
        notes.append(f"sheet PDF export failed: {type(e).__name__}")

    return ImportResult(doc=doc, doc_title=title, dxf_path=dxf,
                        load_errors=int(errs.value or 0), notes=notes, pdf_path=pdf)


def _call(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    try:
        a = getattr(obj, name)
        return a(*args) if callable(a) else a
    except Exception:
        return default
