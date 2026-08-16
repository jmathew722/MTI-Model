"""The provider-comparison tool (tools/compare_providers.py).

It is the one gate between the OpenAI path's "live_plumbing_verified" status and
"production" (docs/PROVIDER_STATUS.md). It spends real money on two API keys, so
the parts that must be right are: it never calls anything in --dry-run, it never
calls anything without confirmation, and its diff actually distinguishes "read
differently" from "not read at all".
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "compare_providers.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_providers", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["compare_providers"] = mod
    spec.loader.exec_module(mod)
    return mod


cp = _load()


class TestDiff:
    def test_agreeing_dimensions_are_counted_as_agreed(self):
        left = {"dimensions": [{"id": "D001", "value": 4.0}]}
        right = {"dimensions": [{"id": "D001", "value": 4.001}]}
        d = cp.compare("P", left, right)["dimensions"]
        assert d["agreed"] == 1 and d["differed"] == 0

    def test_a_real_difference_is_reported_with_both_values(self):
        left = {"dimensions": [{"id": "D001", "value": 16.0}]}
        right = {"dimensions": [{"id": "D001", "value": 16.8}]}
        d = cp.compare("P", left, right)["dimensions"]
        assert d["differed"] == 1
        assert d["differences"][0] == {"id": "D001", "anthropic": 16.0, "openai": 16.8}

    def test_a_missed_dimension_is_distinguished_from_a_differing_one(self):
        """The column that matters most: one provider never read it at all."""
        left = {"dimensions": [{"id": "D001", "value": 1.0}, {"id": "D002", "value": 2.0}]}
        right = {"dimensions": [{"id": "D001", "value": 1.0}]}
        d = cp.compare("P", left, right)["dimensions"]
        assert d["only_anthropic"] == ["D002"] and d["only_openai"] == []
        assert d["differed"] == 0          # not a disagreement — an omission

    def test_hole_callout_counts_are_compared(self):
        rep = cp.compare("P", {"hole_callouts": [{}, {}]}, {"hole_callouts": [{}]})
        assert rep["hole_callouts"] == {"anthropic": 2, "openai": 1}

    def test_non_numeric_values_are_ignored_not_crashed_on(self):
        left = {"dimensions": [{"id": "D001", "value": "unreadable"}]}
        assert cp.compare("P", left, {})["dimensions"]["anthropic"] == 0


class TestCostSafety:
    def test_dry_run_calls_nothing(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "PartA").mkdir()
        called = []
        monkeypatch.setattr(cp, "_extract",
                            lambda *a, **k: called.append(a) or ({}, 0.0))
        monkeypatch.setattr(sys, "argv",
                            ["x", "--parts", str(tmp_path), "--dry-run"])
        assert cp.main() == 0
        assert called == []
        assert "nothing called" in capsys.readouterr().out

    def test_without_confirmation_nothing_is_spent(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "PartA").mkdir()
        called = []
        monkeypatch.setattr(cp, "_extract",
                            lambda *a, **k: called.append(a) or ({}, 0.0))
        monkeypatch.setattr("builtins.input", lambda _="": "n")
        monkeypatch.setattr(sys, "argv", ["x", "--parts", str(tmp_path)])
        assert cp.main() == 0
        assert called == []
        assert "Aborted" in capsys.readouterr().out

    def test_a_non_interactive_run_refuses_rather_than_assuming_yes(self, tmp_path, monkeypatch):
        (tmp_path / "PartA").mkdir()
        called = []
        monkeypatch.setattr(cp, "_extract",
                            lambda *a, **k: called.append(a) or ({}, 0.0))

        def _no_tty(_=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _no_tty)
        monkeypatch.setattr(sys, "argv", ["x", "--parts", str(tmp_path)])
        assert cp.main() == 2
        assert called == []

    def test_confirmed_run_writes_a_report_with_costs(self, tmp_path, monkeypatch):
        (tmp_path / "PartA").mkdir()
        out = tmp_path / "rep"
        monkeypatch.setattr(cp, "_extract", lambda part, root, provider, py: (
            {"dimensions": [{"id": "D001", "value": 1.0 if provider == "anthropic" else 2.0}]},
            0.25))
        monkeypatch.setattr(sys, "argv",
                            ["x", "--parts", str(tmp_path), "--out", str(out), "--yes"])
        assert cp.main() == 0
        data = json.loads((out / "provider_comparison.json").read_text(encoding="utf-8"))
        assert data["cost_usd"] == {"anthropic": 0.25, "openai": 0.25}
        assert data["parts"][0]["dimensions"]["differed"] == 1


class TestPartDiscovery:
    def test_part_folders_are_found_and_limited(self, tmp_path):
        for n in ("A", "B", "C"):
            (tmp_path / n).mkdir()
        (tmp_path / "output").mkdir()          # not a part
        found = [p.name for p in cp._parts(tmp_path, 2)]
        assert found == ["A", "B"]
        assert "output" not in [p.name for p in cp._parts(tmp_path, 0)]

    def test_a_single_drawing_file_is_accepted(self, tmp_path):
        f = tmp_path / "part.pdf"
        f.write_text("x", encoding="utf-8")
        assert cp._parts(f, 3) == [f]

    def test_loose_drawing_files_are_found_when_there_are_no_folders(self, tmp_path):
        (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in cp._parts(tmp_path, 0)] == ["a.pdf"]
