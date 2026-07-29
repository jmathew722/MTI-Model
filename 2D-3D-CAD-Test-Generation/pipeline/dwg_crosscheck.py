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
# Three tiers, because numeric proximity ALONE cannot distinguish a digit-misread
# of value X from a genuinely different dimension Y (the reason the old flat 0.15"
# snap window corrupted correct readings, e.g. .25 -> .38):
#   * CONFIRM  (<= 0.005"): the OCR read matches a DWG value -> mark certain.
#   * AUTO-CORRECT (<= 0.03"): a tiny discrepancy (rounding / last-digit noise) is
#     snapped to the exact DWG value, but only if kind-matched, unambiguous, and
#     one-to-one.
#   * DISCREPANCY (0.03"..0.75"): a larger mismatch is FLAGGED for human review and
#     the DWG value is added to possible_values — the OCR value is NOT silently
#     changed (that would risk corrupting a correct reading).
#   * UNVERIFIED: no exact value of the right kind is near.
CONFIRM_TOL = 0.005
AUTO_CORRECT_TOL = 0.03
DISCREPANCY_TOL = 0.75
# The nearest exact value must be this many times closer than the 2nd-nearest to be
# treated as unambiguous — otherwise the reading is left UNVERIFIED, never snapped.
_UNIQUENESS_RATIO = 2.0

# In-process cache so the OCR-render import and the cross-check import are ONE
# SolidWorks import per DWG per run. Keyed by (resolved path, mtime).
_GT_CACHE: Dict[Tuple[str, float], "DwgGroundTruth"] = {}


@dataclass
class DwgGroundTruth:
    dwg_path: str
    units: str = "inch"
    pdf_path: Optional[str] = None            # SolidWorks-rendered sheet (OCR input)
    exact_values: List[float] = field(default_factory=list)   # drawing units (all)
    length_values: List[float] = field(default_factory=list)  # kind-separated pools
    diameter_values: List[float] = field(default_factory=list)
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
    # Bucket exact values BY KIND so a length can never snap to a diameter, and
    # exclude counts/tolerances/notes (which polluted the flat snap pool).
    for t in raw.all_text():
        pn = parse_number(t.text)
        if not (pn.numeric and pn.value is not None):
            continue
        v = round(pn.value, 4)
        if not (0 < abs(v) < 500) or pn.count is not None:
            continue
        gt.text_values.append((t.text, v))
        if pn.kind == "diameter":
            gt.diameter_values.append(v)
        elif pn.kind == "length":
            gt.length_values.append(v)
        # radius/tolerance/note kinds are recorded in text_values but not as snap targets
    for g in raw.all_geometry():
        if g.type == "circle" and g.radius_m:
            d = round(2 * g.radius_m / factor, 4)
            gt.circle_diameters.append(d)
            gt.diameter_values.append(d)
    gt.length_values = sorted(set(gt.length_values))
    gt.diameter_values = sorted(set(gt.diameter_values))
    gt.exact_values = sorted(set(gt.length_values) | set(gt.diameter_values))
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


def _two_nearest(value: float, pool: List[float]):
    """Return the two smallest distances (nearest, second) to values in ``pool``."""
    ds = sorted((abs(e - value), e) for e in pool)
    nearest = ds[0] if ds else None
    second = ds[1][0] if len(ds) > 1 else None
    return nearest, second


def crosscheck_and_correct(drawing_data: Dict[str, Any], gt: DwgGroundTruth) -> Dict[str, Any]:
    """Cross-check OCR dimensions + hole diameters against KIND-MATCHED exact DWG
    values and correct clear misreads in place. Mutates ``drawing_data``. Safe by
    construction:
      * a dimension only matches LENGTH values, a hole only matches DIAMETER values
        (a length can never snap to a diameter);
      * a CONFIRM needs a tight (<=0.005") match; a CORRECTION needs the nearest
        exact value to be within a modest window AND unambiguously closer than the
        2nd-nearest (uniqueness ratio), AND not already claimed by another
        correction (one-to-one) — otherwise the reading is left UNVERIFIED;
      * ``value_unclear`` is cleared ONLY on a confirm, never on a correction
        (a corrected value is flagged for review, not asserted certain).
    Never fabricates and never blocks."""
    report: Dict[str, Any] = {
        "source": "dwg_crosscheck", "units": gt.units,
        "exact_value_count": len(gt.exact_values),
        "length_pool": len(gt.length_values), "diameter_pool": len(gt.diameter_values),
        "confirmed": [], "corrected": [], "discrepancies": [], "unverified": [], "dwg_only": [],
    }
    matched_exact: set = set()

    items = []
    for d in drawing_data.get("dimensions", []) or []:
        items.append(("dimension", d.get("id", "?"), d, "value", gt.length_values))
    for h in drawing_data.get("hole_callouts", []) or []:
        items.append(("hole_diameter", h.get("id", "?"), h, "diameter", gt.diameter_values))

    # Pass 1 — confirmations (tight, kind-matched). Many readings may confirm the
    # same exact value (e.g. two identical holes), so confirms do not consume.
    pending = []
    for kind, ident, holder, fld, pool in items:
        v = holder.get(fld)
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if not pool:
            report["unverified"].append({"kind": kind, "id": ident, "value": round(float(v), 4),
                                         "reason": f"no exact {kind} values in the DWG"})
            continue
        near, second = _two_nearest(float(v), pool)
        if near and near[0] <= CONFIRM_TOL:
            matched_exact.add(near[1])
            report["confirmed"].append({"kind": kind, "id": ident, "value": round(float(v), 4),
                                        "dwg": near[1]})
            if holder.get("value_unclear"):
                holder["value_unclear"] = False   # OCR verified correct -> now certain
        else:
            pending.append((kind, ident, holder, fld, float(v), near, second))

    # Pass 2 — corrections + discrepancies (unambiguous, one-to-one). Closest first.
    pending.sort(key=lambda p: p[5][0] if p[5] else 1e9)
    used_for_correction: set = set()
    for kind, ident, holder, fld, v, near, second in pending:
        if near is None:
            report["unverified"].append({"kind": kind, "id": ident, "value": round(v, 4),
                                         "reason": "no exact value of this kind"})
            continue
        dist, e = near
        unambiguous = second is None or second > _UNIQUENESS_RATIO * dist
        old = round(v, 4)
        if not unambiguous:
            report["unverified"].append({"kind": kind, "id": ident, "value": old,
                                         "nearest_dwg": e, "distance": round(dist, 4),
                                         "reason": "ambiguous (two exact values equally near)"})
        elif dist <= AUTO_CORRECT_TOL and e not in used_for_correction:
            # Tiny discrepancy -> snap to exact (rounding / last-digit noise).
            holder[fld] = e
            note = (f"[DWG-verified] OCR read {old}; DWG exact {e}; auto-corrected "
                    f"(within {AUTO_CORRECT_TOL} in).")
            holder["notes"] = (str(holder.get("notes", "")) + " " + note).strip()
            # value_unclear is intentionally NOT cleared on a correction.
            used_for_correction.add(e)
            matched_exact.add(e)
            report["corrected"].append({"kind": kind, "id": ident, "ocr": old, "dwg": e,
                                        "delta": round(e - old, 4)})
        elif dist <= DISCREPANCY_TOL:
            # Larger mismatch -> FLAG for review, do NOT silently change the value.
            # Offer the DWG value to the resolver/human via possible_values.
            pv = holder.get("possible_values") or []
            if e not in pv:
                pv = [old, e] if not pv else pv + [e]
            holder["possible_values"] = pv
            holder["value_unclear"] = True
            note = (f"[DWG-conflict] OCR read {old} but the SolidWorks DWG import shows "
                    f"{e} ({round(abs(e - old), 4)}\" apart) — needs human review; value NOT "
                    "auto-changed.")
            holder["notes"] = (str(holder.get("notes", "")) + " " + note).strip()
            matched_exact.add(e)
            report["discrepancies"].append({"kind": kind, "id": ident, "ocr": old, "dwg": e,
                                            "delta": round(e - old, 4)})
        else:
            report["unverified"].append({"kind": kind, "id": ident, "value": old,
                                         "nearest_dwg": e, "distance": round(dist, 4),
                                         "reason": "no exact value within review range"})

    # Exact values that never matched any OCR reading -> possible OCR misses.
    for e in gt.exact_values:
        if all(abs(e - m) > CONFIRM_TOL for m in matched_exact):
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
