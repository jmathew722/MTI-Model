"""2026-07-28 fix: pipeline.solidworks_builder.save_model's SaveAs3 result check.

LIVE-VERIFIED against a real SolidWorks session: SaveAs3 returns an INTEGER
swFileSaveError_e bitmask, not a bool — 0 (swFileSaveWithoutError) on a genuine
success, and a real forced failure (an invalid drive path) returned int 1 with
no file written. The OLD check `result in (False, None)` only matched 0 by
Python's `0 == False` coincidence and NEVER matched any other nonzero error
code (1 != False, 2 != False, ...) — so an ACTUAL SaveAs3 failure (any
documented nonzero code) silently passed the check entirely, and save_model
returned claiming success with nothing written to disk.
"""
from __future__ import annotations

import pytest

from pipeline.solidworks_builder import SolidWorksError, save_model


class _Doc:
    def __init__(self, save_result, write_file_to=None):
        self._save_result = save_result
        self._write_file_to = write_file_to

    def SaveAs3(self, path, version, options):
        if self._write_file_to:
            from pathlib import Path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("fake sldprt", encoding="utf-8")
        return self._save_result


def test_success_code_zero_is_not_an_error(tmp_path):
    doc = _Doc(save_result=0, write_file_to=True)
    path = save_model(doc, "part", tmp_path)
    assert path  # did not raise


def test_nonzero_code_with_no_file_written_is_a_hard_failure(tmp_path):
    # The exact bug: OLD code let this pass silently (1 is never `in (False, None)`).
    doc = _Doc(save_result=1, write_file_to=False)
    with pytest.raises(SolidWorksError, match="SaveAs3 failed"):
        save_model(doc, "part", tmp_path)


def test_nonzero_code_with_file_written_is_advisory_not_blocking(tmp_path, caplog):
    # Some bits in the mask may be warnings coexisting with a real save.
    doc = _Doc(save_result=2, write_file_to=True)
    path = save_model(doc, "part", tmp_path)
    assert path  # did not raise — advisory only


def test_none_return_with_no_file_still_fails(tmp_path):
    doc = _Doc(save_result=None, write_file_to=False)
    with pytest.raises(SolidWorksError):
        save_model(doc, "part", tmp_path)
