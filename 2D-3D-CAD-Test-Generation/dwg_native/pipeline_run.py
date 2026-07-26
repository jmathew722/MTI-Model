"""The DWG-native job runner: import -> extract -> map -> build -> verify.

Wired into the JobQueue (single serialized SolidWorks worker). Writes every
artifact to the job's output dir with provenance; a part that fails a
verification gate lands in ``failed/`` with the failing gate named. Also runs the
OCR-correction step against a sibling vision *_extraction.json when one exists.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .session.job_queue import Job, JobStatus
from .extract import import_dwg, extract_raw, save_raw
from .semantic import (map_to_build_plan, assert_provenance, correct_ocr)
from .build import emit_vba, build_part
from .verify import verify_build

log = logging.getLogger("dwg_native.run")


def _find_vision_extraction(part_stem: str) -> Optional[dict]:
    """Best-effort: locate a vision pipeline *_extraction.json for this part to
    correct. Searches the usual output roots. Returns the parsed dict or None."""
    roots = [Path("../test_drawings"), Path("UI_Output"), Path("webapp/parts")]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(f"*{part_stem}*_extraction.json"):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def run_job(job: Job, session: Any, progress: Callable[[JobStatus, str], None]) -> None:
    out = Path(job.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    dwg = Path(job.dwg_path)
    part_stem = dwg.stem
    artifacts = job.artifacts

    # 1. import ------------------------------------------------------------ #
    progress(JobStatus.IMPORTING, dwg.name)
    imp = import_dwg(session, dwg, out)

    # 2. extract ----------------------------------------------------------- #
    progress(JobStatus.EXTRACTING, "reading geometry + text")
    raw = extract_raw(dwg.name, imp.dxf_path, sw_doc=imp.doc, session=session,
                      extra_notes=imp.notes)
    raw_path = save_raw(raw, out / "raw_extraction.json")
    artifacts["raw_extraction"] = str(raw_path)
    session.close_doc(imp.doc_title)

    # 3. map (rules first) ------------------------------------------------- #
    progress(JobStatus.MAPPING, "2D -> 3D feature intents")
    bp = map_to_build_plan(raw)
    assert_provenance(bp)   # fails the job on any unsourced value
    (out / "build_plan.json").write_text(json.dumps(bp, indent=2), encoding="utf-8")
    artifacts["build_plan"] = str(out / "build_plan.json")

    # OCR correction (the stated purpose): DWG-exact values correct vision.
    vision = _find_vision_extraction(part_stem)
    corr = correct_ocr(bp, vision)
    (out / "ocr_correction.json").write_text(json.dumps(corr, indent=2), encoding="utf-8")
    artifacts["ocr_correction"] = str(out / "ocr_correction.json")

    # reviewable VBA artifact (always emitted)
    macros = out / "macros"
    macros.mkdir(exist_ok=True)
    vba = emit_vba(bp, str(out / f"{bp['part']}.SLDPRT"))
    (macros / "build.vba").write_text(vba, encoding="utf-8")
    artifacts["macro_vba"] = str(macros / "build.vba")

    # A blocking conflict blocks the build (no silent skip): surface + stop.
    if bp.get("blocking"):
        blockers = [c for c in bp.get("conflicts", []) if c.get("blocking")]
        job.status = JobStatus.FAILED
        job.failed_check = "semantic_conflict"
        job.error = "; ".join(c.get("detail", "") for c in blockers) or "blocking conflict"
        job.result = {"conflicts": bp.get("conflicts", []), "hole_count": bp.get("hole_count"),
                      "ocr_correction": corr, "blocked": True}
        return

    # 4. build ------------------------------------------------------------- #
    progress(JobStatus.BUILDING, "COM build .SLDPRT + .STL")
    sw_app = session.ensure()
    build_result = build_part(bp, out, sw_app)
    artifacts["sldprt"] = build_result.get("sldprt", "")
    artifacts["stl"] = build_result.get("stl", "")

    # 5. verify ------------------------------------------------------------ #
    progress(JobStatus.VERIFYING, "5 gates")
    report = verify_build(bp, build_result)
    (out / "verification_report.json").write_text(json.dumps(report.to_dict(), indent=2),
                                                  encoding="utf-8")
    artifacts["verification"] = str(out / "verification_report.json")

    job.result = {
        "part": bp["part"],
        "hole_count": bp.get("hole_count"),
        "profile_found": bp.get("profile_found"),
        "conflicts": bp.get("conflicts", []),
        "ocr_correction": corr,
        "verification": report.to_dict(),
        "body_box_m": build_result.get("body_box_m"),
    }
    if not report.passed:
        # A failing part goes to failed/ with the failing gate named.
        job.status = JobStatus.FAILED
        job.failed_check = report.failing_check
        job.error = f"verification gate failed: {report.failing_check}"
        failed_dir = out.parent / "failed" / job.id
        try:
            failed_dir.mkdir(parents=True, exist_ok=True)
            (failed_dir / "verification_report.json").write_text(
                json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass
        return
    progress(JobStatus.DONE, "READY")
