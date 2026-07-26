"""FastAPI app for the DWG-native pipeline.

The web layer NEVER calls SolidWorks directly. An upload/enqueue returns a job id
immediately; the single serialized JobQueue worker owns the COM session. The UI
polls GET /api/dwg/jobs/{id}. Static frontend is served from ../../frontend-dwg.
"""
from __future__ import annotations

import shutil
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

    queue = JobQueue(runner=run_job, session_factory=ComSession, per_job_timeout_s=600.0)
    app.state.queue = queue

    # -- job submission ---------------------------------------------------- #
    @app.post("/api/dwg/jobs")
    async def submit(file: Optional[UploadFile] = File(None),
                     server_path: Optional[str] = Form(None),
                     output_dir: Optional[str] = Form(None)):
        if not file and not server_path:
            raise HTTPException(400, "Provide a DWG upload or a server_path.")
        job_out = Path(output_dir) if output_dir else (_JOBS_ROOT / "pending")
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
        job = queue.submit(str(dwg_path), str(job_out / dwg_path.stem))
        # give the job its own output dir keyed by id
        job.output_dir = str(_JOBS_ROOT / job.id / dwg_path.stem)
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
