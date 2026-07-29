"""Tests for the hardened static macro auditor (2026-07-28): control-flow block
balance (For/Do/With) and SwConst enum-member validation."""
from __future__ import annotations

from pipeline.macro_audit import audit_text


def _ids(findings):
    return [f.rule_id for f in findings]


def test_unbalanced_for_next_is_error():
    vba = ('Option Explicit\nSub main()\n'
           '    For i = 1 To 3\n        x = i\n'   # For with no Next
           'End Sub\n')
    findings = audit_text("01_F001_x.vba", vba)
    assert any(f.rule_id == "STRUCT" and "For/Next" in f.message and f.severity == "error"
               for f in findings)


def test_balanced_blocks_ok():
    vba = ('Option Explicit\nSub main()\n'
           '    For i = 1 To 3\n        x = i\n    Next i\n'
           '    LogResult "PASS", "s", "ok"\n'
           'End Sub\n')
    findings = audit_text("01_F001_x.vba", vba)
    assert not any(f.severity == "error" for f in findings)


def test_unknown_enum_member_flagged():
    vba = ('Option Explicit\nSub main()\n'
           '    x = swEndConditions_e.swEndCondTypoDoesNotExist\n'
           '    LogResult "PASS", "s", "ok"\n'
           'End Sub\n')
    findings = audit_text("01_F001_x.vba", vba)
    assert any(f.rule_id == "ENUM" for f in findings)


def test_known_enum_member_not_flagged():
    vba = ('Option Explicit\nSub main()\n'
           '    x = swEndConditions_e.swEndCondThroughAll\n'
           '    LogResult "PASS", "s", "ok"\n'
           'End Sub\n')
    findings = audit_text("01_F001_x.vba", vba)
    assert not any(f.rule_id == "ENUM" for f in findings)
