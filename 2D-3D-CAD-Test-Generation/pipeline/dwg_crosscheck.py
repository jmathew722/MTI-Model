"""Stage 2.4 - DWG native cross-check (2026-07-26, MTI_OCRDWG).

When the pipeline input is a DWG, the vision/OCR extraction can misread a digit
(16.00 vs 16.80). SolidWorks, however, parses the DWG's own annotation text
exactly. This stage imports the DWG through the SolidWorks API (reusing the
proven ``dwg_native`` importer/extractor), pulls every EXACT dimension text value
(MTEXT) plus every circle diameter, and cross-checks them against the OCR
extraction: a match CONFIRMS the reading, a near-miss CORRECTS it to the exact
value (recorded, ``value_unclear`` cleared), and a value with no counterpart is
surfaced. The corrected extraction then commits into the resolver -> build ->
verify path unchanged.

Runs after OCR extraction and hole-augment, before Stage 2.5 (resolver). It is
purely additive and gracefully no-ops when the input is not a DWG or SolidWorks/
ezdxf are unavailable (never blocks a run). A single SolidWorks import is shared
between the OCR-render (PDF for the vision model) and this cross-check via an
in-process cache.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("pipeline.dwg_crosscheck")

_UNIT_FACTOR = {"inch": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0, "feet": 0.3048}
CONFIRM_TOL = 0.01        # within this (drawing units) the OCR value is confirmed
_MIN_CORRECT_WINDOW = 0.15  # a nearest exact value within this snaps (misread), else unverified

# In-process cache so the OCR-render import and the cross-check import are ONE
# SolidWorks import per DWG per run. Keyed by (resolved path, mtime).
_GT_CACHE: Dict[Tuple[str, float], "DwgGroundTruth"] = {}


@dataclass
class DwgGroundTruth:
    dwg_path: str
    units: str = "inch"
    pdf_path: Optional[str] = None            # SolidWorks-rendered sheet (OCR input)
    exact_values: List[float] = field(default_factory=list)   # drawing units
    text_values: List[Tuple[str, float]] = field(default_factory=list)
    circle_diameters: List[float] = field(default_factory=list)
    n_text: int = 0
    n_circles: int = 0
    available: bool = False
    note: str = ""


def is_dwg(path: Any) -> bool:
    try:
        return Path(str(path)).suffix.lower() == ".dwg"
    except Exception:
        return False


def get_ground_truth(dwg_path: str | Path, work_dir: str | Path,
                     session: Any = None) -> DwgGroundTruth:
    """Import the DWG once (cached) and return exact dimension values + a PDF.

    Reuses dwg_native's SolidWorks importer + ezdxf extractor. Never raises: on
    any failure (not Windows, no SolidWorks, no ezdxf, bad file) it returns a
    DwgGroundTruth with ``available=False`` and a note explaining why."""
    p = Path(dwg_path)
    try:
        key = (str(p.resolve()), p.stat().st_mtime)
    except Exception:
        key = (str(p), 0.0)
    if key in _GT_CACHE:
        return _GT_CACHE[key]

    gt = DwgGroundTruth(dwg_path=str(p))
    try:
        from dwg_native.session.com_session import ComSession
        from dwg_native.extract import import_dwg, extract_raw
        from dwg_native.semantic.numbers import parse_number
    except Exception as e:  # dwg_native / deps missing
        gt.note = f"dwg_native unavailable: {type(e).__name__}: {e}"
        _GT_CACHE[key] = gt
        return gt

    own_session = False
    try:
        if session is None:
            session = ComSession()
            session.connect()
            own_session = True
        imp = import_dwg(session, p, work_dir)
        raw = extract_raw(p.name, imp.dxf_path, sw_doc=imp.doc, session=session,
                          extra_notes=imp.notes)
        session.close_doc(imp.doc_title)
    except Exception as e:
        gt.note = f"SolidWorks import failed: {type(e).__name__}: {e}"
        _GT_CACHE[key] = gt
        return gt

    factor = _UNIT_FACTOR.get(raw.units_detected, 0.0254)
    gt.units = raw.units_detected
    gt.pdf_path = str(imp.pdf_path) if getattr(imp, "pdf_path", None) else None
    for t in raw.all_text():
        pn = parse_number(t.text)
        if pn.numeric and pn.value is not None and 0 < abs(pn.value) < 500:
            gt.text_values.append((t.text, round(pn.value, 4)))
    for g in raw.all_geometry():
        if g.type == "circle" and g.radius_m:
            gt.circle_diameters.append(round(2 * g.radius_m / factor, 4))
    gt.exact_values = sorted({v for _, v in gt.text_values} | set(gt.circle_diameters))
    gt.n_text = len(gt.text_values)
    gt.n_circles = len(gt.circle_diameters)
    gt.available = bool(gt.exact_values)
    gt.note = f"imported OK: {gt.n_text} text value(s), {gt.n_circles} circle(s)"
    if own_session:
        try:
            session.close_all_documents()
        except Exception:
            pass
    _GT_CACHE[key] = gt
    return gt


def _nearest(value: float, exact: List[float]) -> Optional[Tuple[float, float]]:
    best = None
    for e in exact:
        dist = abs(e - value)
        if best is None or dist < best[0]:
            best = (dist, e)
    return best


def crosscheck_and_correct(drawing_data: Dict[str, Any], gt: DwgGroundTruth) -> Dict[str, Any]:
    """Cross-check every OCR dimension + hole diameter against the exact DWG
    values, correcting misreads in place. Returns a report dict. Mutates
    ``drawing_data`` (values snapped to exact, notes + provenance appended)."""
    report: Dict[str, Any] = {
        "source": "dwg_crosscheck", "units": gt.units,
        "exact_value_count": len(gt.exact_values),
        "confirmed": [], "corrected": [], "unverified": [], "dwg_only": [],
    }
    matched_exact: set = set()

    def _check(kind: str, ident: str, holder: Dict[str, Any], field_name: str) -> None:
        v = holder.get(field_name)
        if not isinstance(v, (int, float)) or v <= 0:
            return
        near = _nearest(float(v), gt.exact_values)
        if near is None:
            report["unverified"].append({"kind": kind, "id": ident, "value": v,
                                         "reason": "no exact DWG values"})
            return
        dist, e = near
        window = max(_MIN_CORRECT_WINDOW, 0.05 * abs(v))
        if dist <= CONFIRM_TOL:
            matched_exact.add(e)
            report["confirmed"].append({"kind": kind, "id": ident, "value": round(v, 4),
                                        "dwg": e})
            if holder.get("value_unclear"):
                holder["value_unclear"] = False
        elif dist <= window:
            matched_exact.add(e)
            old = round(float(v), 4)
            holder[field_name] = e
            holder["value_unclear"] = False
            note = (f"[DWG-verified] OCR read {old}; SolidWorks import shows {e}; "
                    "corrected to the exact DWG value.")
            holder["notes"] = (str(holder.get("notes", "")) + " " + note).strip()
            report["corrected"].append({"kind": kind, "id": ident, "ocr": old,
                                        "dwg": e, "delta": round(e - old, 4)})
        else:
            report["unverified"].append({"kind": kind, "id": ident, "value": round(v, 4),
                                         "nearest_dwg": e, "distance": round(dist, 4)})

    for d in drawing_data.get("dimensions", []) or []:
        _check("dimension", d.get("id", "?"), d, "value")
    for h in drawing_data.get("hole_callouts", []) or []:
        _check("hole_diameter", h.get("id", "?"), h, "diameter")

    # Exact values that never matched any OCR reading -> possible OCR misses.
    tol = CONFIRM_TOL
    for e in gt.exact_values:
        if all(abs(e - m) > tol for m in matched_exact):
            report["dwg_only"].append(e)

    report["summary"] = (
        f"{len(report['confirmed'])} confirmed, {len(report['corrected'])} corrected, "
        f"{len(report['unverified'])} unverified vs {len(gt.exact_values)} exact DWG value(s)")
    return report


def run_dwg_crosscheck(dwg_path: str | Path, drawing_data: Dict[str, Any],
                       output_dir: str | Path, part_base: str, session: Any = None,
                       write_report: bool = True) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Orchestrate the stage: ground-truth import -> cross-check/correct -> (opt.)
    write ``<part>_dwg_crosscheck.json`` + log corrections. Returns
    ``(possibly_corrected_drawing_data, report_or_None)``. Never raises.

    ``output_dir`` is the run OUTPUT ROOT (holds ``lessons_learned.jsonl``). Pass
    ``write_report=False`` to skip the file write when the caller will persist the
    returned report into the final per-part folder itself."""
    if not is_dwg(dwg_path):
        return drawing_data, None
    root = Path(output_dir)
    try:
        gt = get_ground_truth(dwg_path, root, session=session)
    except Exception as e:
        log.warning("DWG cross-check skipped (ground-truth error): %s", e)
        return drawing_data, {"source": "dwg_crosscheck", "skipped": str(e)}
    if not gt.available:
        return drawing_data, {"source": "dwg_crosscheck", "skipped": gt.note}

    report = crosscheck_and_correct(drawing_data, gt)
    report["ground_truth_note"] = gt.note
    report["sheet_pdf"] = gt.pdf_path
    if write_report:
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / f"{part_base}_dwg_crosscheck.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("could not write dwg_crosscheck report: %s", e)
    # Append corrections to the cross-run lessons ledger (best-effort).
    if report.get("corrected"):
        try:
            ledger = root / "lessons_learned.jsonl"
            with ledger.open("a", encoding="utf-8") as f:
                for c in report["corrected"]:
                    f.write(json.dumps({"stage": "dwg_crosscheck", "part": part_base,
                                        "resolution": "dwg_corrected", **c}) + "\n")
        except Exception:
            pass
    return drawing_data, report


def write_crosscheck_report(report: Optional[Dict[str, Any]], part_dir: Path, part_base: str) -> None:
    """Persist a cross-check report into the final per-part folder (used by callers
    that know the part dir only after resolve/verify)."""
    if not report or report.get("skipped"):
        return
    try:
        Path(part_dir).mkdir(parents=True, exist_ok=True)
        (Path(part_dir) / f"{part_base}_dwg_crosscheck.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("could not write dwg_crosscheck report to part dir: %s", e)
