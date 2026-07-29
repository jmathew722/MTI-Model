"""A multi-line or quote-carrying LLM-supplied feature description must never
break VBA comment syntax (2026-07-28 hardening). Newlines in a raw f-string
interpolation would end the comment mid-line and desyncronize the rest of the
generated Sub — these targeted skeleton emitters previously interpolated
feature.description raw."""
from __future__ import annotations

from pipeline.macro_generator import (Feature, FeatureType,
                                      _macro_coverage_skeleton,
                                      _macro_revolve_skeleton)
from pipeline.macro_audit import audit_text


def _feature(desc: str, ftype=FeatureType.REVOLVE) -> Feature:
    return Feature(id="F009", type=ftype, description=desc, related_dimensions=[])


def test_revolve_skeleton_escapes_multiline_description():
    vba = _macro_revolve_skeleton(_feature('two-line\ndescription with "quotes"'),
                                  "09_F009_revolve")
    # No raw newline may appear inside what was a single comment line.
    for line in vba.splitlines():
        assert not line.strip().startswith("'") or "\n" not in line
    findings = audit_text("09_F009_revolve.vba", "Option Explicit\nSub main()\n" + vba + "End Sub\n")
    assert not any(f.severity == "error" for f in findings)


def test_coverage_skeleton_escapes_multiline_description():
    vba = _macro_coverage_skeleton(_feature('line one\nline two', FeatureType.SWEEP),
                                   "09_F009_sweep", "a swept profile + a 3D path sketch")
    assert "\n    ' Build manually" in vba or "line one" in vba
    for line in vba.splitlines():
        if line.strip().startswith("'"):
            assert "\n" not in line.strip()
