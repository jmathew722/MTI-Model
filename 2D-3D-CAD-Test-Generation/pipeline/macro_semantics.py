"""Stage 7.4 — do the emitted macros perform the OPERATIONS the plan specifies?

The macro package is the canonical build path, and three guards already cover
parts of it:

* :mod:`pipeline.macro_audit` — no banned APIs, balanced blocks, a PASS/FAIL
  trail. Structural; says nothing about geometry.
* :mod:`pipeline.macro_echo` — every emitted geometry LITERAL round-trips to the
  build plan, and every planned position is emitted. **Per-number.**
* :mod:`pipeline.cq_prevalidate` — builds the BUILD PLAN headlessly. Checks the
  plan, not the macros generated from it.

Between them sits a real gap: every literal can be correct while the OPERATION
around it is wrong. A cut emitted as a boss, a through hole emitted blind, a
depth attached to the wrong feature, a sketch on the wrong plane, an operation
dropped entirely — each keeps every number intact, so the echo check passes, and
each builds the wrong part. This module checks the verbs, where the echo check
checks the nouns.

**Why this is not another geometry builder.** The first attempt rebuilt the
emitted VBA into a solid and compared it to the CadQuery build of the plan. It
was abandoned: reproducing counterbores, slots, patterns and the workplane frame
exactly enough to compare volumes means re-implementing
:mod:`pipeline.cq_prevalidate` against macro text — a THIRD parallel geometry
implementation, and its inevitable modelling gaps show up as differences that
look like generator bugs. Comparing the operation semantics needs no geometry
engine, has no approximation, and catches the class that actually escapes today.

Parsing is anchored to the exact call signatures the generator emits (the same
discipline, and the same regexes, as :mod:`pipeline.macro_echo`) — never a loose
scan of arbitrary text.

Advisory by default: :func:`check_macro_semantics` reports;
:func:`assert_macro_semantics` is the strict form used at generation time.

Public: :class:`MacroOperation`, :func:`parse_macro_operations`,
:func:`check_macro_semantics`, :func:`assert_macro_semantics`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pipeline.macro_echo import _CIRCLE_RE, _LINE_RE, _RECT_RE
from utils.logger import get_logger

log = get_logger()

# Depth literals are printed at 6 dp by the generator; compare well inside that.
DEPTH_TOL = 1e-4

# Plan step types whose macro performs a solid operation.
#
# ``thread`` is included because a TAPPED callout still drills its tap hole (the
# thread itself stays cosmetic, per the repo convention) — but a thread step with
# no callout emits an annotation-only macro, so a missing operation is allowed
# for it and only for it. Getting this wrong is how the first version of this
# check reported A001211E's tapped-hole macro as "an operation nothing planned".
# ``slot_rect_cut`` is the rectangular through-cut half of the canonical slot
# decomposition — a real, planned solid cut. Its sibling ``slot_corner_fillet``
# rounds the corners and performs no extrusion/cut, so it is not a solid step.
_SOLID_STEP_TYPES = frozenset({
    "extrude_boss", "extrude_cut", "hole", "thread", "slot_rect_cut",
})
_OPTIONAL_OPERATION_TYPES = frozenset({"thread"})

# Statuses whose macro is still emitted and therefore still checkable. A
# ``needs_review`` step (a tapped hole, a skeleton revolve) ships a real macro;
# excluding it from the planned set made its macro look unplanned.
_EMITTED_STATUSES = frozenset({"generated", "built", "needs_review", ""})
# Steps that legitimately emit no sketch geometry of their own.
_NON_SKETCH_STEP_TYPES = frozenset({
    "setup", "export_stl", "final_verify", "run_all", "export", "verify",
    "reference_geometry", "thread", "fillet/chamfer", "pattern",
    "circular_pattern", "linear_pattern", "mirror", "reference_axis",
})

_EXTRUDE_RE = re.compile(r"FeatureManager\.FeatureExtrusion3\(", re.I)
_CUT_RE = re.compile(r"FeatureManager\.FeatureCut4\(", re.I)
_THROUGH_RE = re.compile(r"swEndCond(?:ThroughAll|ThroughAllBoth)\b", re.I)
_DEPTH_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*\*\s*UNIT_FACTOR,\s*0\.01,", re.M)
_PLANE_RE = re.compile(r'SelectRefPlane\(\s*"([^"]+)"')

_SCAFFOLD_MACROS = frozenset({
    "RUN_ALL.vba", "00_setup.vba", "01a_reference_geometry.vba",
    "ZZ_final_verify.vba", "ZZZ_export_stl.vba",
})


class MacroSemanticsError(Exception):
    """The emitted macros perform different operations than the plan specifies."""


@dataclass
class MacroOperation:
    """One solid-changing operation recovered from a generated macro."""

    macro_file: str
    feature_id: str
    kind: str                       # boss | cut
    plane: str = ""
    through: bool = False
    depth_in: Optional[float] = None
    n_circles: int = 0
    n_rects: int = 0
    n_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"macro_file": self.macro_file, "feature_id": self.feature_id,
                "kind": self.kind, "plane": self.plane, "through": self.through,
                "depth_in": self.depth_in, "n_circles": self.n_circles,
                "n_rects": self.n_rects, "n_lines": self.n_lines}


@dataclass
class SemanticsReport:
    operations: list[MacroOperation] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checked": self.checked, "issues": self.issues,
                "operations": [o.to_dict() for o in self.operations]}


def _feature_id_of(filename: str) -> str:
    m = re.match(r"\d+[a-z]?_(F\d+)", filename)
    return m.group(1) if m else ""


def parse_macro_operations(macros_dir: Path | str) -> list[MacroOperation]:
    """Recover each feature macro's operation semantics, in file (build) order."""
    macros_dir = Path(macros_dir)
    ops: list[MacroOperation] = []
    for path in sorted(macros_dir.glob("*.vba")):
        if path.name in _SCAFFOLD_MACROS or path.name.startswith("RECONCILE_"):
            continue
        text = path.read_text(encoding="utf-8")
        body = text[text.index("Sub main()"):] if "Sub main()" in text else text

        is_boss = bool(_EXTRUDE_RE.search(body))
        is_cut = bool(_CUT_RE.search(body))
        if not (is_boss or is_cut):
            continue
        # A macro that both adds and removes material is not something the
        # generator ever intends: one feature macro performs ONE operation (a
        # hole macro's two FeatureCut4 calls are the flip-direction retry of the
        # SAME cut, which is why "cut" is decided on the call TYPE, not a count).
        # Reported as its own kind so it can never be silently read as either.
        kind = "mixed" if (is_boss and is_cut) else ("cut" if is_cut else "boss")
        depth_m = _DEPTH_RE.search(body)
        plane_m = _PLANE_RE.search(body)
        ops.append(MacroOperation(
            macro_file=path.name,
            feature_id=_feature_id_of(path.name),
            kind=kind,
            plane=plane_m.group(1) if plane_m else "",
            through=bool(_THROUGH_RE.search(body)),
            depth_in=float(depth_m.group(1)) if depth_m else None,
            n_circles=len(_CIRCLE_RE.findall(body)),
            n_rects=len(_RECT_RE.findall(body)),
            n_lines=len(_LINE_RE.findall(body)),
        ))
    return ops


def _expected_kind(step_type: str) -> str:
    """The solid operation a plan step type must perform. Everything that is not
    an additive boss removes material — including a tapped hole, which drills."""
    return "boss" if step_type == "extrude_boss" else "cut"


def check_macro_semantics(pkg, build_plan: Optional[dict] = None,
                          macros_dir: Optional[Path] = None) -> SemanticsReport:
    """Compare each planned solid step to the operation its macro performs."""
    import json

    report = SemanticsReport()
    macros_dir = Path(macros_dir or pkg.macros_dir)
    if build_plan is None:
        try:
            build_plan = json.loads(Path(pkg.build_plan_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            report.issues.append(f"build plan unreadable: {e}")
            return report

    ops = parse_macro_operations(macros_dir)
    report.operations = ops
    by_feature: dict[str, list[MacroOperation]] = {}
    for op in ops:
        by_feature.setdefault(op.feature_id, []).append(op)

    planned_ids: set[str] = set()
    for step in (build_plan.get("steps") or []):
        stype = str(step.get("type") or "").lower()
        fid = str(step.get("feature_id") or "")
        if stype not in _SOLID_STEP_TYPES or not fid or fid == "-":
            continue
        if str(step.get("status") or "generated") not in _EMITTED_STATUSES:
            continue
        planned_ids.add(fid)
        found = by_feature.get(fid)
        if not found:
            if stype in _OPTIONAL_OPERATION_TYPES:
                continue      # cosmetic thread: annotation-only macro is correct
            report.issues.append(
                f"{fid}: the plan specifies a {stype} but no macro performs a "
                f"solid operation for it — the step is not built")
            continue
        op = found[0]
        report.checked += 1

        expected = _expected_kind(stype)
        if op.kind == "mixed":
            report.issues.append(
                f"{fid}: {op.macro_file} emits BOTH an extrusion and a cut — one "
                f"feature macro performs one operation; this builds an unpredictable "
                f"shape whichever the plan intended")
        elif op.kind != expected:
            report.issues.append(
                f"{fid}: plan says {stype} ({expected}) but {op.macro_file} emits a "
                f"{op.kind} — every literal can be right and the part still wrong")

        depth_type = str(step.get("depth_type") or "").lower()
        if depth_type == "through_all" and not op.through:
            report.issues.append(
                f"{fid}: plan says through_all but {op.macro_file} emits a blind "
                f"end condition")
        elif depth_type == "blind" and op.through:
            report.issues.append(
                f"{fid}: plan says blind but {op.macro_file} emits a through-all "
                f"end condition")

        dims = step.get("dimensions_drawing_units") or {}
        planned_depth = dims.get("depth") or dims.get("thickness") or dims.get("height")
        if (planned_depth and op.depth_in is not None and not op.through
                and abs(float(planned_depth) - op.depth_in) > DEPTH_TOL):
            report.issues.append(
                f"{fid}: plan depth {planned_depth} but {op.macro_file} extrudes "
                f"{op.depth_in}")

        if stype in ("hole", "thread"):
            n_pos = len(step.get("positions_xy") or [])
            if n_pos and op.n_circles < n_pos:
                report.issues.append(
                    f"{fid}: plan places {n_pos} hole instance(s) but "
                    f"{op.macro_file} sketches only {op.n_circles} circle(s)")

    for fid, found in by_feature.items():
        if fid and fid not in planned_ids:
            report.issues.append(
                f"{fid}: {found[0].macro_file} performs a solid {found[0].kind} that "
                f"no plan step specifies")
    return report


def assert_macro_semantics(pkg, build_plan: Optional[dict] = None,
                           macros_dir: Optional[Path] = None) -> SemanticsReport:
    """Strict form: raise when the macros' operations contradict the plan."""
    report = check_macro_semantics(pkg, build_plan, macros_dir)
    if not report.ok:
        raise MacroSemanticsError(
            f"Generated macros contradict the build plan "
            f"({len(report.issues)} issue(s)): " + "; ".join(report.issues[:10]))
    log.info("macro semantics OK: %d planned operation(s) match the emitted macros",
             report.checked)
    return report
