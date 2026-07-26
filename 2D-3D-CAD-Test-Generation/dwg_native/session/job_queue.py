"""Single serialized SolidWorks worker + job queue.

FastAPI must NOT call SolidWorks from a request handler — the COM session is
single-instance and stateful, and interleaved requests corrupt each other's
document state. So the web layer enqueues a job and returns immediately; ONE
background worker thread owns the ComSession and drains the queue strictly one
job at a time. A per-job timeout guarantees a hung SolidWorks dialog cannot
wedge the queue forever.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("dwg_native.jobqueue")


class JobStatus(str, Enum):
    QUEUED = "queued"
    IMPORTING = "importing"
    EXTRACTING = "extracting"
    MAPPING = "mapping"
    BUILDING = "building"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"


# The status the UI shows as it moves through the pipeline, in order.
STAGE_ORDER = [JobStatus.QUEUED, JobStatus.IMPORTING, JobStatus.EXTRACTING,
               JobStatus.MAPPING, JobStatus.BUILDING, JobStatus.VERIFYING,
               JobStatus.DONE]


@dataclass
class Job:
    id: str
    dwg_path: str
    output_dir: str
    status: JobStatus = JobStatus.QUEUED
    stage_detail: str = ""
    failed_check: str = ""          # named failing verification gate, if any
    error: str = ""
    created_at: float = field(default_factory=lambda: 0.0)
    finished_at: float = 0.0
    artifacts: Dict[str, str] = field(default_factory=dict)   # name -> path
    result: Dict[str, Any] = field(default_factory=dict)      # summary payload

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# A job runner receives (job, session, progress_cb) and mutates the job in place.
JobRunner = Callable[[Job, Any, Callable[[JobStatus, str], None]], None]


class JobQueue:
    """One worker thread, one COM session, FIFO jobs. Thread-safe enqueue/read."""

    def __init__(self, runner: JobRunner, session_factory: Callable[[], Any],
                 per_job_timeout_s: float = 600.0) -> None:
        self._runner = runner
        self._session_factory = session_factory
        self._timeout = per_job_timeout_s
        self._q: "queue.Queue[str]" = queue.Queue()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._session: Optional[Any] = None
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- public API -------------------------------------------------------- #
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="sw-worker", daemon=True)
        self._worker.start()

    def submit(self, dwg_path: str, output_dir: str) -> Job:
        jid = uuid.uuid4().hex[:12]
        job = Job(id=jid, dwg_path=str(dwg_path), output_dir=str(output_dir),
                  created_at=time.time())
        with self._lock:
            self._jobs[jid] = job
        self._q.put(jid)
        self.start()
        return job

    def get(self, jid: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(jid)

    def all(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in
                    sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]

    def stop(self) -> None:
        self._stop.set()
        self._q.put("__stop__")

    # -- worker ------------------------------------------------------------ #
    def _loop(self) -> None:
        # COM must be initialised on the worker thread that touches SolidWorks.
        try:
            import pythoncom  # type: ignore
            pythoncom.CoInitialize()
        except Exception:
            pass
        while not self._stop.is_set():
            jid = self._q.get()
            if jid == "__stop__":
                break
            job = self.get(jid)
            if job is None:
                continue
            self._run_one(job)

    def _run_one(self, job: Job) -> None:
        def progress(status: JobStatus, detail: str = "") -> None:
            with self._lock:
                job.status = status
                job.stage_detail = detail
            log.info("[job %s] %s %s", job.id, status.value, detail)

        # A hung SolidWorks dialog is contained by running the job body in a
        # watchdog thread with a hard timeout; the session is rebuilt afterward.
        result_box: Dict[str, Any] = {}

        def body() -> None:
            try:
                session = self._ensure_session()
                self._runner(job, session, progress)
            except Exception as e:  # noqa: BLE001 — surface the real reason
                result_box["error"] = f"{type(e).__name__}: {e}"
                result_box["trace"] = traceback.format_exc()

        t = threading.Thread(target=body, name=f"job-{job.id}", daemon=True)
        t.start()
        t.join(self._timeout)
        with self._lock:
            if t.is_alive():
                job.status = JobStatus.FAILED
                job.error = (f"Job exceeded the {self._timeout:.0f}s timeout — a "
                             "SolidWorks dialog or operation hung. Session will be "
                             "rebuilt for the next job.")
                job.finished_at = time.time()
                # Force a fresh session next job; the hung one is unusable.
                self._session = None
            elif "error" in result_box:
                job.status = JobStatus.FAILED
                job.error = result_box["error"]
                job.finished_at = time.time()
            else:
                if job.status != JobStatus.FAILED:
                    job.status = JobStatus.DONE
                job.finished_at = time.time()
        # Clean up any leaked documents so the next job starts clean.
        try:
            if self._session is not None and hasattr(self._session, "close_all_documents"):
                self._session.close_all_documents()
        except Exception:
            pass

    def _ensure_session(self) -> Any:
        if self._session is None:
            self._session = self._session_factory()
        elif hasattr(self._session, "ensure"):
            self._session.ensure()
        return self._session
