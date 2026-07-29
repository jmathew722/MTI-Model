"""Unit tests for the rewritten pipeline.solidworks_builder.check_rebuild_errors
(2026-07-28). Live-verified against a real SolidWorks session during development:
GetRebuildErrorCount/GetRebuildErrors/GetRebuildWarningCount do NOT resolve at
all under this file's late-bound COM dispatch on the actual target install
(every one raises AttributeError) — the OLD implementation silently swallowed
that into errors=0 and reported "clean" on every single call, so the check was
completely dead on the real environment. ForceRebuild3(True)'s own boolean
return (already used elsewhere in this module) is the proven-working signal;
these tests cover the new fail-closed-on-genuine-failure behavior with mocks.
"""
from __future__ import annotations

from pipeline.solidworks_builder import check_rebuild_errors


class _Doc:
    """A minimal mock matching the real late-bound behavior: the legacy getters
    are simply ABSENT (AttributeError), exactly as verified live."""
    def __init__(self, force_rebuild_result=True, force_rebuild_raises=None):
        self._result = force_rebuild_result
        self._raises = force_rebuild_raises

    def ForceRebuild3(self, top_only):
        if self._raises:
            raise self._raises
        return self._result


def test_clean_rebuild_reports_true_via_force_rebuild():
    # No legacy getters at all (matches the real install) — ForceRebuild3 is the
    # only signal, and it reports success.
    assert check_rebuild_errors(_Doc(force_rebuild_result=True)) is True


def test_failed_rebuild_reports_false():
    assert check_rebuild_errors(_Doc(force_rebuild_result=False)) is False


def test_force_rebuild_raising_fails_closed_not_open():
    # The old bug: any probe exception silently became "clean" (fail-open). The
    # rebuild call itself failing must now report NOT clean (fail-closed) —
    # unknown must never masquerade as success.
    assert check_rebuild_errors(_Doc(force_rebuild_raises=RuntimeError("COM error"))) is False


def test_legacy_error_count_still_used_when_it_resolves():
    class _DocWithLegacyErrors(_Doc):
        def GetRebuildErrorCount(self):
            return 3
    # A legacy getter reporting errors > 0 short-circuits to False without even
    # needing ForceRebuild3 to be consulted.
    assert check_rebuild_errors(_DocWithLegacyErrors(force_rebuild_result=True)) is False


def test_legacy_error_count_zero_falls_through_to_force_rebuild():
    class _DocWithLegacyErrors(_Doc):
        def GetRebuildErrorCount(self):
            return 0
    assert check_rebuild_errors(_DocWithLegacyErrors(force_rebuild_result=True)) is True
    assert check_rebuild_errors(_DocWithLegacyErrors(force_rebuild_result=False)) is False
