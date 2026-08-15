"""C# macro output (pipeline/csharp_macro.py).

The macros_csharp/ companion package is generated from the SAME BuildStep data
as the VBA, deterministic, self-echo-checked, and never touches macros/ (the
golden snapshot stays byte-identical). No SolidWorks needed — these tests only
inspect the emitted source text.

Emission is OPT-IN since 2026-08-15 (REFACTOR_ANALYSIS §1.7) — every test here
asks for it explicitly, and :class:`TestOptIn` pins the default OFF.
"""
import pytest

from pipeline.csharp_macro import (
    CSharpEmitError,
    _resolve_plane,
    _self_echo_check,
    generate_csharp_package,
)
from pipeline.macro_generator import generate_macro_package
from pipeline.validator import format_verification_report, run_verification
from tests.test_golden_macros import _golden_drawing


@pytest.fixture()
def built(tmp_path):
    data = _golden_drawing()
    model, report = run_verification(data)
    assert report.ok, str(report)
    pkg = generate_macro_package(model, data, format_verification_report(model, report),
                                 tmp_path, emit_csharp=True)
    return model, pkg


def test_package_files_written(built):
    model, pkg = built
    cs_dir = pkg.root / "macros_csharp"
    assert cs_dir.is_dir()
    names = sorted(p.name for p in cs_dir.iterdir())
    assert names == ["BuildPart.csproj", "Program.cs", "README.md", "SwBuildHelpers.cs"]
    # The VBA macros folder is untouched by the C# emission (golden safety).
    assert not list(pkg.macros_dir.glob("*.cs"))


def test_program_carries_the_same_geometry_literals(built):
    """Every hole center and the base-plate profile from the build plan must be
    emitted into Program.cs (dual-output agreement)."""
    model, pkg = built
    text = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
    # Golden mounting holes: 4 centers, dia 0.25, at y=1 spaced 1.0 in x.
    for cx in ("0.5", "1.5", "2.5", "3.5"):
        assert f"sw.CreateCircle({cx}, 1, 0.25);" in text
    # Base plate: 4.0 x 2.0 rectangle, corner at the origin.
    assert "sw.CreateCornerRect(0, 0, 4, 2);" in text
    # Holes are thru → ThroughAll cut; boss carries its 0.5 depth.
    assert "true, 0)" in text or "true, 0);" in text  # CutFeature(..., thru, 0)
    assert "sw.BossFeature(" in text and "0.5" in text


def test_manual_steps_stay_manual_never_silent(built):
    model, pkg = built
    text = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
    # The golden shell is prohibited → logged MANUAL step, and the fillet step
    # is interactive → logged WARN; neither disappears from the C# output.
    assert text.count("MANUAL STEP") >= 1
    assert 'sw.Log("WARN"' in text
    assert "F004" in text  # the shell feature is named, not dropped


def test_program_structure_and_helpers(built):
    model, pkg = built
    cs_dir = pkg.root / "macros_csharp"
    program = (cs_dir / "Program.cs").read_text(encoding="utf-8")
    helpers = (cs_dir / "SwBuildHelpers.cs").read_text(encoding="utf-8")
    # Balanced braces in both files (cheap structural sanity for generated C#).
    assert program.count("{") == program.count("}")
    assert helpers.count("{") == helpers.count("}")
    # Late binding, not interop references.
    assert 'Type.GetTypeFromProgID("SldWorks.Application")' in helpers
    assert "SolidWorks.Interop" not in helpers
    # The verified feature calls and their enum conventions are present.
    assert "FeatureExtrusion3" in helpers
    assert "FeatureCut4" in helpers
    assert "FeatureCircularPattern5" in helpers
    assert "throughAll ? 1 : 0" in helpers  # swEndCondThroughAll : swEndCondBlind
    # Inch part → UNIT_FACTOR 0.0254 baked into the helpers.
    assert "UNIT_FACTOR = 0.0254" in helpers
    # csproj targets .NET Framework 4.8 with the dynamic-binder reference.
    csproj = (cs_dir / "BuildPart.csproj").read_text(encoding="utf-8")
    assert "<TargetFramework>net48</TargetFramework>" in csproj
    assert '<Reference Include="Microsoft.CSharp" />' in csproj
    # README names the canonical path.
    readme = (cs_dir / "README.md").read_text(encoding="utf-8")
    assert "canonical" in readme


def test_emission_is_deterministic(built, tmp_path):
    model, pkg = built
    first = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
    generate_csharp_package(model, pkg, 0.0254)
    second = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
    assert first == second


def test_self_echo_rejects_dropped_position(built):
    model, pkg = built
    program = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
    # Remove one hole circle line → the echo check must name the drop.
    broken = program.replace("sw.CreateCircle(2.5, 1, 0.25);", "")
    with pytest.raises(CSharpEmitError, match="F002"):
        _self_echo_check(pkg, broken)
    # And the intact program passes with all positions checked.
    assert _self_echo_check(pkg, program) >= 4


class TestPlaneResolution:
    """2026-07-28 fix: _emit_solid_step/_emit_slot_rect used to pass the RAW
    unmapped sketch_plane label (e.g. "top") straight to SelectPlane with a
    HARDCODED index of 1 — SelectByID2 never matches a bare lowercase label
    (it needs the real plane name "Top Plane"), so every non-front-plane C#
    build silently fell back to the 1st reference plane in the tree (Front
    Plane) regardless of which plane was intended."""

    def test_top_resolves_to_top_plane_index_2(self):
        assert _resolve_plane("top") == ("Top Plane", 2)
        assert _resolve_plane("Top") == ("Top Plane", 2)   # case-insensitive

    def test_right_and_side_aliases_resolve_to_right_plane_index_3(self):
        assert _resolve_plane("right") == ("Right Plane", 3)
        assert _resolve_plane("side") == ("Right Plane", 3)
        assert _resolve_plane("left") == ("Right Plane", 3)

    def test_front_resolves_to_front_plane_index_1(self):
        assert _resolve_plane("front") == ("Front Plane", 1)
        assert _resolve_plane("") == ("Front Plane", 1)   # default

    def test_unrecognized_label_falls_back_to_front_not_a_wrong_plane(self):
        # e.g. "REF_DATUM_A" (the slot_rect_cut sentinel) — REF_DATUM_A IS the
        # base datum coincident with Front Plane, so this fallback is correct.
        assert _resolve_plane("REF_DATUM_A") == ("Front Plane", 1)

    def test_generated_program_selects_the_correct_plane_for_a_top_feature(self, built):
        """End-to-end: the golden fixture's F001 is sketch_plane="Top" — the
        emitted Program.cs must select "Top Plane" at index 2, never index 1
        (which — before the fix — silently built it on Front Plane instead)."""
        model, pkg = built
        program = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
        assert 'SelectPlane("Top Plane", 2)' in program
        assert 'SelectPlane("top", 1)' not in program   # the old broken call


class TestHoleTypeParity:
    """2026-07-28 fix: a counterbore/countersink/tapped hole used to route
    through the generic single-cut emitter (_emit_solid_step), silently
    building a PLAIN through/blind hole with the cbore/csk relief or thread
    entirely missing — no error, no log, wrong geometry. Now routes to an
    honest MANUAL step (matching the module's own stated design rule)."""

    def _cbore_drawing(self):
        d = _golden_drawing()
        d["hole_callouts"] = [
            {"id": "H001", "type": "counterbore", "diameter": 0.25, "qty": 1,
             "cbore_diameter": 0.5, "cbore_depth": 0.1, "feature_ref": "F002"},
        ]
        return d

    def _build(self, data, tmp_path):
        model, report = run_verification(data)
        assert report.ok, str(report)
        return generate_macro_package(model, data, format_verification_report(model, report),
                                      tmp_path, emit_csharp=True)

    def test_counterbore_routes_to_manual_not_a_plain_cut(self, tmp_path):
        pkg = self._build(self._cbore_drawing(), tmp_path)
        program = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
        # The F002 step must be a logged MANUAL step naming the counterbore —
        # never a plain sw.CutFeature/sw.CreateCircle for the wrong diameter.
        assert 'counterbore' in program.lower()
        assert 'sw.Log("WARN"' in program

    def test_simple_hole_still_builds_normally(self, built):
        # A plain THRU hole (the golden fixture's F002) must NOT be affected —
        # still a real scripted cut, not demoted to manual.
        model, pkg = built
        program = (pkg.root / "macros_csharp" / "Program.cs").read_text(encoding="utf-8")
        assert "sw.CreateCircle(2.5, 1, 0.25)" in program
        assert "sw.CutFeature(" in program


class TestOptIn:
    """C# emission is opt-in (REFACTOR_ANALYSIS §1.7): the VBA package is the
    canonical build path, nothing builds from the C# output, and emitting it on
    every run made every macro_generator change a two-emitter change."""

    def _generate(self, tmp_path, **kw):
        data = _golden_drawing()
        model, report = run_verification(data)
        return generate_macro_package(model, data,
                                      format_verification_report(model, report),
                                      tmp_path, **kw)

    def test_not_emitted_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MTI_EMIT_CSHARP", raising=False)
        pkg = self._generate(tmp_path)
        assert not (pkg.root / "macros_csharp").exists()
        # the VBA package is untouched by the opt-out
        assert (pkg.root / "macros").is_dir()
        assert any(pkg.root.glob("macros/*.vba"))

    def test_flag_enables_it(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MTI_EMIT_CSHARP", raising=False)
        pkg = self._generate(tmp_path, emit_csharp=True)
        assert (pkg.root / "macros_csharp" / "Program.cs").is_file()

    def test_env_var_enables_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTI_EMIT_CSHARP", "1")
        pkg = self._generate(tmp_path)
        assert (pkg.root / "macros_csharp" / "Program.cs").is_file()

    def test_explicit_false_beats_the_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTI_EMIT_CSHARP", "1")
        pkg = self._generate(tmp_path, emit_csharp=False)
        assert not (pkg.root / "macros_csharp").exists()

    def test_cli_exposes_the_flag(self):
        import subprocess
        import sys

        out = subprocess.run([sys.executable, "main.py", "--help"],
                             capture_output=True, text=True, timeout=180)
        assert "--emit-csharp" in out.stdout
