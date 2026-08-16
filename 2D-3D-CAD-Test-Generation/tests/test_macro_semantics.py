"""Operation-semantics check (pipeline/macro_semantics.py, 2026-08-16).

The echo check proves the emitted NUMBERS match the plan. This proves the
emitted OPERATIONS do. Every test below corrupts one thing that leaves all the
literals intact — which is exactly why the echo check cannot see it — and
asserts this guard does.

A guard is only worth having if it fails when it should, so the negative cases
carry the weight here; the positive case is the golden package.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.macro_generator import generate_macro_package
from pipeline.macro_semantics import (
    MacroSemanticsError,
    assert_macro_semantics,
    check_macro_semantics,
    parse_macro_operations,
)
from pipeline.validator import format_verification_report, run_verification
from tests.test_golden_macros import _golden_drawing


@pytest.fixture()
def built(tmp_path):
    data = _golden_drawing()
    model, report = run_verification(data)
    assert report.ok, str(report)
    pkg = generate_macro_package(model, data, format_verification_report(model, report),
                                 tmp_path)
    plan = json.loads(Path(pkg.build_plan_json).read_text(encoding="utf-8"))
    return pkg, plan


def _corrupt_macro(pkg, feature_id: str, old: str, new: str) -> None:
    """Edit the emitted macro for one feature — simulating a generator defect."""
    for path in Path(pkg.macros_dir).glob("*.vba"):
        if f"_{feature_id}_" in path.name:
            text = path.read_text(encoding="utf-8")
            assert old in text, f"{path.name} does not contain {old!r}"
            path.write_text(text.replace(old, new), encoding="utf-8")
            return
    raise AssertionError(f"no macro found for {feature_id}")


class TestCleanPackagePasses:
    def test_the_golden_package_is_semantically_consistent(self, built):
        pkg, plan = built
        rep = check_macro_semantics(pkg, plan)
        assert rep.ok, rep.issues
        assert rep.checked >= 2

    def test_generation_enforces_it(self, built):
        pkg, plan = built
        assert_macro_semantics(pkg, plan)      # must not raise

    def test_operations_are_recovered_from_the_macros(self, built):
        pkg, _ = built
        ops = parse_macro_operations(pkg.macros_dir)
        kinds = {o.feature_id: o.kind for o in ops}
        assert kinds.get("F001") == "boss"     # the base plate
        assert kinds.get("F002") == "cut"      # the mounting holes drill
        assert all(o.plane for o in ops)       # each names the plane it sketches on


class TestCatchesWhatTheEchoCheckCannot:
    """Each corruption keeps every literal valid — only the verb changes."""

    def test_a_cut_emitted_as_a_boss(self, built):
        pkg, plan = built
        _corrupt_macro(pkg, "F002", "FeatureManager.FeatureCut4(",
                       "FeatureManager.FeatureExtrusion3(")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("emits a boss" in i and "F002" in i for i in rep.issues), rep.issues

    def test_a_macro_that_both_adds_and_removes_material(self, built):
        """The hole macro emits its cut twice (flip-direction retry). Turning
        ONE of them into an extrusion leaves a macro that both adds and removes
        material — meaningless whichever the plan intended, so it is named."""
        pkg, plan = built
        path = next(p for p in Path(pkg.macros_dir).glob("*.vba") if "_F002_" in p.name)
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("FeatureManager.FeatureCut4(",
                                     "FeatureManager.FeatureExtrusion3(", 1),
                        encoding="utf-8")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("BOTH an extrusion and a cut" in i for i in rep.issues), rep.issues

    def test_a_through_hole_emitted_blind(self, built):
        pkg, plan = built
        _corrupt_macro(pkg, "F002", "swEndCondThroughAllBoth", "swEndCondBlind")
        _corrupt_macro(pkg, "F002", "swEndCondThroughAll", "swEndCondBlind")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("through_all" in i and "blind" in i for i in rep.issues), rep.issues

    def test_a_wrong_extrude_depth(self, built):
        pkg, plan = built
        base = next(s for s in plan["steps"] if s.get("feature_id") == "F001")
        depth = (base.get("dimensions_drawing_units") or {}).get("depth")
        assert depth, "golden base must carry a depth"
        _corrupt_macro(pkg, "F001", f"{depth} * UNIT_FACTOR", f"{depth + 1} * UNIT_FACTOR")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("plan depth" in i for i in rep.issues), rep.issues

    def test_a_planned_step_whose_macro_does_nothing(self, built):
        pkg, plan = built
        _corrupt_macro(pkg, "F002", "FeatureManager.FeatureCut4(",
                       "' removed: FeatureManager_FeatureCut4(")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("no macro performs a solid operation" in i for i in rep.issues)

    def test_missing_hole_instances(self, built):
        pkg, plan = built
        hole = next((s for s in plan["steps"] if s.get("type") == "hole"), None)
        assert hole and len(hole.get("positions_xy") or []) >= 2
        path = next(p for p in Path(pkg.macros_dir).glob("*.vba")
                    if f"_{hole['feature_id']}_" in p.name)
        text = path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if "CreateCircleByRadius" in ln]
        path.write_text(text.replace(lines[-1], "    ' dropped circle"), encoding="utf-8")
        rep = check_macro_semantics(pkg, plan)
        assert not rep.ok
        assert any("sketches only" in i for i in rep.issues), rep.issues

    def test_strict_mode_raises_with_the_feature_named(self, built):
        pkg, plan = built
        _corrupt_macro(pkg, "F002", "FeatureManager.FeatureCut4(",
                       "FeatureManager.FeatureExtrusion3(")
        with pytest.raises(MacroSemanticsError, match="F002"):
            assert_macro_semantics(pkg, plan)


class TestDoesNotMisfire:
    """The calibration cases — each of these is CORRECT and must stay quiet.
    Both were false positives in the first version of this check."""

    def test_a_cosmetic_thread_step_needs_no_solid_operation(self, built):
        pkg, plan = built
        plan["steps"].append({"seq": 90, "feature_id": "F900", "type": "thread",
                              "status": "needs_review", "macro_file": "90_F900_thread.vba"})
        assert check_macro_semantics(pkg, plan).ok

    def test_a_tapped_hole_that_drills_is_a_cut_not_an_extra(self, built):
        """A `thread` step WITH a callout drills its tap hole; that operation is
        planned, not unplanned (the A001211E false positive)."""
        pkg, plan = built
        hole = next(s for s in plan["steps"] if s.get("type") == "hole")
        hole["type"] = "thread"
        hole["status"] = "needs_review"
        rep = check_macro_semantics(pkg, plan)
        assert rep.ok, rep.issues

    def test_scaffold_macros_are_not_operations(self, built):
        pkg, _ = built
        files = {o.macro_file for o in parse_macro_operations(pkg.macros_dir)}
        assert "00_setup.vba" not in files
        assert "RUN_ALL.vba" not in files
        assert "ZZZ_export_stl.vba" not in files

    def test_an_unreadable_plan_is_reported_not_crashed_on(self, tmp_path):
        pkg = SimpleNamespace(root=tmp_path, macros_dir=tmp_path,
                              build_plan_json=tmp_path / "missing.json")
        rep = check_macro_semantics(pkg)
        assert not rep.ok and "unreadable" in rep.issues[0]


class TestWiredIntoGeneration:
    def test_generate_macro_package_runs_the_check(self):
        import inspect

        from pipeline import macro_generator

        src = inspect.getsource(macro_generator.generate_macro_package)
        assert "assert_macro_semantics" in src
