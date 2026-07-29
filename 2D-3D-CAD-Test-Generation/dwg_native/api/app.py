"""FastAPI app for the DWG-native pipeline.

The web layer NEVER calls SolidWorks directly. An upload/enqueue returns a job id
immediately; the single serialized JobQueue worker owns the COM session. The UI
polls GET /api/dwg/jobs/{id}. Static frontend is served from ../../frontend-dwg.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..session import JobQueue
from ..session.com_session import ComSession
from ..pipeline_run import run_job

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parents[1]                       # 2D-3D-CAD-Test-Generation/
_FRONTEND = _PROJECT / "frontend-dwg"
_JOBS_ROOT = _PROJECT / "dwg_native" / "_jobs"
_SAMPLE_DIR = Path.home() / "Downloads" / "EDrawings"   # where the test DWGs live


def create_app() -> FastAPI:
    app = FastAPI(title="DWG-native pipeline")
    _JOBS_ROOT.mkdir(parents=True, exist_ok=True)

    # Never let the browser serve a stale app.js/styles.css/index — a cached old
    # bundle is exactly how "the button stopped working after an update" happens.
    @app.middleware("http")
    async def _no_cache(request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    queue = JobQueue(runner=run_job, session_factory=ComSession, per_job_timeout_s=600.0)
    app.state.queue = queue

    # -- job submission ---------------------------------------------------- #
    @app.post("/api/dwg/jobs")
    async def submit(file: Optional[UploadFile] = File(None),
                     server_path: Optional[str] = Form(None),
                     output_dir: Optional[str] = Form(None)):
        if not file and not server_path:
            raise HTTPException(400, "Provide a DWG upload or a server_path.")
        if file:
            dest = _JOBS_ROOT / "uploads"
            dest.mkdir(parents=True, exist_ok=True)
            dwg_path = dest / file.filename
            with dwg_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            dwg_path = Path(server_path)
            if not dwg_path.is_file():
                raise HTTPException(404, f"server_path not found: {dwg_path}")
        # Compute the FINAL output_dir BEFORE enqueueing (2026-07-28 fix): the
        # worker thread can start pulling this job off the queue the instant
        # queue.submit() puts it there — a mutation of job.output_dir AFTER
        # submit() returns races the worker, which may already have read the
        # placeholder value (the previous code always lost this race in
        # practice, silently discarding a caller-supplied output_dir every
        # time). A directory token generated here (not job.id, which only
        # exists after submit() returns) needs no post-hoc correction.
        dir_token = uuid.uuid4().hex[:12]
        job_root = Path(output_dir) / dir_token if output_dir else (_JOBS_ROOT / dir_token)
        final_output_dir = str(job_root / dwg_path.stem)
        job = queue.submit(str(dwg_path), final_output_dir)
        return JSONResponse({"job_id": job.id, "status": job.status.value}, status_code=202)

    @app.get("/api/dwg/jobs")
    async def list_jobs():
        return {"jobs": queue.all()}

    @app.get("/api/dwg/jobs/{jid}")
    async def get_job(jid: str):
        job = queue.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.to_dict()

    @app.get("/api/dwg/jobs/{jid}/artifact/{name}")
    async def get_artifact(jid: str, name: str):
        job = queue.get(jid)
        if job is None or name not in job.artifacts:
            raise HTTPException(404, "no such artifact")
        p = Path(job.artifacts[name])
        if not p.is_file():
            raise HTTPException(404, "artifact file missing")
        return FileResponse(str(p), filename=p.name)

    @app.get("/api/dwg/jobs/{jid}/sheet.pdf")
    async def sheet_pdf(jid: str):
        """The SolidWorks-rendered PDF of the imported drawing, served INLINE so
        the UI can show it as a PDF view on the left."""
        job = queue.get(jid)
        if job is None or "sheet_pdf" not in job.artifacts:
            raise HTTPException(404, "no sheet pdf")
        p = Path(job.artifacts["sheet_pdf"])
        if not p.is_file():
            raise HTTPException(404, "sheet pdf missing")
        return FileResponse(str(p), media_type="application/pdf",
                            content_disposition_type="inline")

    @app.get("/api/dwg/jobs/{jid}/dimensions")
    async def dimensions(jid: str):
        """Every dimension the DWG import produced: the exact MTEXT tokens (parsed)
        and the geometry-derived build dimensions, each with provenance."""
        job = queue.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        import json
        from dwg_native.semantic.numbers import parse_number
        raw_p = Path(job.artifacts.get("raw_extraction", ""))
        bp_p = Path(job.artifacts.get("build_plan", ""))
        extracted, geom = [], []
        if raw_p.is_file():
            raw = json.loads(raw_p.read_text(encoding="utf-8"))
            factor = {"inch": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0}.get(
                raw.get("units_detected", "inch"), 0.0254)
            for v in raw.get("views", []):
                for t in v.get("text_tokens", []):
                    pn = parse_number(t.get("text", ""))
                    pos = t.get("position_2d_m") or [0, 0]
                    extracted.append({
                        "id": t.get("id"), "text": t.get("text"),
                        "value": pn.value, "kind": pn.kind, "count": pn.count,
                        "typ": pn.is_typical, "thru": pn.is_through, "deep": pn.is_depth,
                        "x_in": round(pos[0] / factor, 3), "y_in": round(pos[1] / factor, 3),
                    })
        if bp_p.is_file():
            bp = json.loads(bp_p.read_text(encoding="utf-8"))
            for s in bp.get("steps", []):
                pmap = s.get("provenance_map", {}) or {}
                for k, val in (s.get("dimensions_drawing_units") or {}).items():
                    geom.append({"feature": s.get("feature_id"), "type": s.get("type"),
                                 "dimension": k, "value": val, "units": bp.get("units", "inch"),
                                 "provenance": pmap.get(k, "")})
                for pos in (s.get("positions_xy") or []):
                    if s.get("type") == "hole":
                        geom.append({"feature": s.get("feature_id"), "type": "hole",
                                     "dimension": "position (x,y)", "value": pos,
                                     "units": bp.get("units", "inch"),
                                     "provenance": pmap.get("position", "")})
        return {"extracted_text": extracted, "geometry_dimensions": geom,
                "n_extracted": len(extracted), "n_geometry": len(geom)}

    @app.get("/api/dwg/jobs/{jid}/geometry")
    async def geometry(jid: str):
        """Exact extracted geometry + text for the 2D preview canvas — visual proof
        the extraction landed. Reads raw_extraction.json from the job output."""
        job = queue.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        raw = Path(job.artifacts.get("raw_extraction", ""))
        if not raw.is_file():
            return {"views": []}
        import json
        return json.loads(raw.read_text(encoding="utf-8"))

    @app.get("/api/dwg/samples")
    async def samples():
        """List the on-disk test DWGs (A050381E, A050401E, and any siblings)."""
        out = []
        if _SAMPLE_DIR.exists():
            for p in sorted(_SAMPLE_DIR.glob("*.[dD][wW][gG]")):
                out.append({"name": p.name, "path": str(p)})
        return {"samples": out, "dir": str(_SAMPLE_DIR)}

    @app.get("/api/dwg/health")
    async def health():
        return {"ok": True, "frontend": _FRONTEND.exists()}

    # -- static frontend --------------------------------------------------- #
    @app.get("/")
    async def index():
        idx = _FRONTEND / "index.html"
        if idx.is_file():
            return HTMLResponse(idx.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>frontend-dwg missing</h1>", status_code=500)

    if _FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    queue.start()
    return app


app = create_app()
