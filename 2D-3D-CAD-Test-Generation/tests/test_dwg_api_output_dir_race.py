"""2026-07-28 fix: dwg_native/api/app.py's /api/dwg/jobs endpoint used to call
queue.submit() (which enqueues the job and can start the worker pulling it
immediately) with a PLACEHOLDER output_dir, then mutate job.output_dir
AFTERWARD once the job's id was known — a race the worker could win, reading
the placeholder. Also, since the placeholder was always overwritten
unconditionally, a caller-supplied output_dir form field was silently
discarded every time. This test verifies the job's output_dir is CORRECT (the
real final path, honoring a caller-supplied root) the instant it is
observable — no placeholder value ever exists.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from dwg_native.api.app import create_app
from dwg_native.session.job_queue import JobQueue


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Prevent the real background worker from touching SolidWorks — the queue
    # under test never needs to actually run a job for THIS race-condition test.
    monkeypatch.setattr(JobQueue, "start", lambda self: None)
    app = create_app()
    return TestClient(app)


def test_submit_output_dir_has_no_race_window(client, tmp_path):
    dwg = tmp_path / "sample.dwg"
    dwg.write_bytes(b"not a real dwg, upload-path only")
    r = client.post("/api/dwg/jobs", files={"file": ("sample.dwg", dwg.read_bytes())})
    assert r.status_code == 202
    jid = r.json()["job_id"]

    job = client.get(f"/api/dwg/jobs/{jid}").json()
    # The output_dir must already be the real final path — never a "pending"
    # placeholder that a caller reading it at this instant could observe.
    assert "pending" not in job["output_dir"]
    assert job["output_dir"].endswith("sample")


def test_submit_honors_caller_supplied_output_dir(client, tmp_path):
    dwg = tmp_path / "sample2.dwg"
    dwg.write_bytes(b"not a real dwg")
    custom_root = str(tmp_path / "my_custom_root")
    r = client.post("/api/dwg/jobs",
                    files={"file": ("sample2.dwg", dwg.read_bytes())},
                    data={"output_dir": custom_root})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    job = client.get(f"/api/dwg/jobs/{jid}").json()
    # Previously ALWAYS discarded (overwritten by the post-hoc mutation) —
    # now genuinely honored.
    assert job["output_dir"].startswith(custom_root)
