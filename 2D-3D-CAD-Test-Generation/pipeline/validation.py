"""The validation SCORECARD — one verdict for one part (reference doc 07).

This pipeline measures a great deal and, until now, concluded nothing in one
place: rebuild health, body count, bbox, per-feature verdicts, must-meet
constraints and reconciliation each land in their own artifact, and a human has
to open six files to learn whether the part is good. Doc 07's central demand is
a single machine-readable scorecard ending in one verdict, and its golden rule
is *never report success without a passing scorecard*.

This module is that scorecard. It is a PRESENTATION-AND-MEASUREMENT layer, not a
new pipeline stage:

* most layers are ASSEMBLED from artifacts the pipeline already wrote — it does
  not re-derive what `feature_verify`/`constraint_verify`/`model_validator`
  already decided, and it never contradicts the READY gate (it explains it);
* two layers are genuinely NEW measurements that nothing else performed —
  **volume ratio** (solid ÷ bounding box, doc 07's "fingerprint") and
  **centre-of-mass symmetry** (a one-sided feature on a part the drawing says is
  symmetric);
* one layer is the **mirror risk** from doc 04: a part whose projection angle was
  never established could be mirrored, and no other check in the pipeline can
  see that.

**Measurement idiom.** Doc 07 reaches for `IMassProperty` over COM. This repo
cannot: `solidworks_builder.check_rebuild_errors` records the live finding that
the mass-property objects do not resolve under its late-bound dispatch. The
equivalent measurements are taken from the exported STL with `trimesh` — the
same idiom `constraint_verify` and `feature_verify` already use, and one that
works with no SolidWorks running at all.

Verdict rule (doc 07):
  * any Layer-1 (rebuild/build) failure → ``FAIL``;
  * any measured geometric failure → ``FAIL``;
  * everything passes but confidence-low assumptions or advisories exist →
    ``PASS_WITH_ASSUMPTIONS``;
  * otherwise → ``PASS``.

Never raises: a scorecard that crashes tells you nothing. A layer whose input is
missing is reported ``skipped`` with the reason, never silently passed.

Public: :func:`build_scorecard`, :func:`write_scorecard`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

log = get_logger()

SCORECARD_NAME = "validation.json"

PASS = "PASS"
PASS_WITH_ASSUMPTIONS = "PASS_WITH_ASSUMPTIONS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

# A symmetric part's centre of mass must sit on the symmetry plane. 1% of the
# span is loose enough for mesh tessellation, tight enough to catch a feature
# built on one side only.
COM_SYMMETRY_TOL_FRAC = 0.01
# Below this, a "solid" is mostly air — usually a cut that severed the part or a
# base built at the wrong scale. Above 1.0 is impossible (bbox bounds the solid).
MIN_PLAUSIBLE_VOLUME_RATIO = 0.02


@dataclass
class Layer:
    """One graded check."""

    name: str
    status: str                     # PASS | FAIL | SKIPPED
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class Scorecard:
    part: str = ""
    overall: str = SKIPPED
    layers: list[Layer] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)

    def add(self, layer: Layer) -> Layer:
        self.layers.append(layer)
        return layer

    @property
    def failures(self) -> list[Layer]:
        return [x for x in self.layers if x.status == FAIL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "overall": self.overall,
            "failures": [x.name for x in self.failures],
            "layers": [x.to_dict() for x in self.layers],
            "assumptions": self.assumptions,
            "advisories": self.advisories,
        }


# --------------------------------------------------------------------------- #
# Artifact loading (every input optional)
# --------------------------------------------------------------------------- #
def _load(path: Optional[Path]) -> Any:
    if path is None or not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first(out: Path, *patterns: str) -> Optional[Path]:
    for pat in patterns:
        for p in sorted(out.glob(pat)):
            if p.is_file():
                return p
    return None


def _mesh(part_dir: Path):
    """The built solid as a trimesh, or (None, reason)."""
    stl = _first(part_dir, "*.STL", "*.stl")
    if stl is None:
        return None, "no STL exported (the part was not built)"
    try:
        import trimesh
    except Exception:
        return None, "trimesh not installed"
    try:
        mesh = trimesh.load(str(stl), force="mesh")
    except Exception as e:
        return None, f"STL unreadable: {type(e).__name__}: {e}"
    if getattr(mesh, "is_empty", False) or not len(getattr(mesh, "faces", [])):
        return None, "STL contains no geometry"
    return mesh, ""


# --------------------------------------------------------------------------- #
# Layers
# --------------------------------------------------------------------------- #
def _layer_build_health(card: Scorecard, part_dir: Path) -> None:
    """Layer 1 — did every planned feature actually build?"""
    results = []
    for candidate in (part_dir / "logs" / "macro_result.json",
                      part_dir / "macro_result.json"):
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        # E027 (2026-08-17): this used to parse the file ONLY line-by-line as
        # JSONL. The COM builder writes a pretty-printed JSON object —
        # {"results": [ {...}, ... ]} — so every line failed to parse, `results`
        # came out EMPTY, and a recorded "status": "FAIL" was never seen. TEST3
        # part 4088-A-RevA reported build_health PASS while macro_result.json
        # said its chamfer FAILED, in the same directory. Parse as JSON first;
        # keep the JSONL path for the streaming writer that also exists.
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            # {"results": [...]} from the COM builder, or a single record when
            # the streaming writer emitted exactly one line.
            results = (list(parsed["results"]) if "results" in parsed
                       else [parsed] if ("feature_id" in parsed or "feature" in parsed)
                       else [])
        elif isinstance(parsed, list):
            results = list(parsed)
        else:
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if not line or line in ("[", "]"):
                    continue
                try:
                    results.append(json.loads(line))
                except ValueError:
                    continue
        break
    dispositions = _load(_first(part_dir, "*_build_dispositions.json")) or []
    excluded = [d.get("feature_id") for d in dispositions
                if d.get("state") == "EXCLUDED_INCOMPLETE"]
    failed = [str(r.get("feature_id") or r.get("feature") or "?") for r in results
              if str(r.get("status") or r.get("result") or "").lower()
              in ("fail", "failed", "error")]

    # E027 (2026-08-17): a feature can go MISSING without ever appearing in
    # `failed`. `macro_result.json` records build-call failures; a feature that
    # ends `deferred_open` (the retry ladder gave up) or `skipped` is absent from
    # the model but absent from that list too. This layer used to compute
    # `excluded` and then route it to an ADVISORY without touching the status —
    # so TEST3 parts 4088-A and 4092-B reported PASS while missing a chamfer and
    # a cut. Planned geometry that is not in the model is a build-health failure
    # by definition; that is the whole question this layer exists to answer.
    deferred = _load(_first(part_dir, "_deferred_log.json")) or {}
    open_items = [str(i.get("feature_id") or "?")
                  for i in (deferred.get("items") or [])
                  if not i.get("recovered")]
    missing = [fid for fid in (failed + excluded + open_items) if fid]
    # de-duplicate, preserving order: one feature can be in several ledgers
    seen: set[str] = set()
    missing = [f for f in missing if not (f in seen or seen.add(f))]

    if missing:
        why = []
        if failed:
            why.append(f"{len(failed)} failed")
        if open_items:
            why.append(f"{len(open_items)} deferred open")
        if excluded:
            why.append(f"{len(excluded)} excluded as incomplete")
        card.add(Layer("build_health", FAIL,
                       f"{len(missing)} planned feature(s) are NOT in the model "
                       f"({', '.join(why)}): {', '.join(missing[:6])}",
                       {"failed": failed, "deferred_open": open_items,
                        "excluded": excluded}))
    elif not results and not dispositions:
        card.add(Layer("build_health", SKIPPED, "no build results on disk"))
    else:
        card.add(Layer("build_health", PASS,
                       f"{len(dispositions)} planned feature(s), all present in the model"))
    if excluded:
        card.advisories.append(
            f"{len(excluded)} feature(s) excluded as incomplete: {', '.join(excluded[:6])}")


def _layer_solid(card: Scorecard, mesh, reason: str) -> None:
    """Layer 1b — exactly one watertight solid body."""
    if mesh is None:
        card.add(Layer("solid_body", SKIPPED, reason))
        return
    try:
        bodies = mesh.split(only_watertight=False)
        n = max(1, len(bodies))
    except Exception:
        n = 1
    watertight = bool(getattr(mesh, "is_watertight", False))
    if n != 1:
        card.add(Layer("solid_body", FAIL,
                       f"expected 1 solid body, found {n} — a boss did not merge or a "
                       f"cut severed the part", {"body_count": n}))
    elif not watertight:
        card.add(Layer("solid_body", FAIL,
                       "the built solid is not watertight — the mesh has holes, so no "
                       "volume measurement can be trusted", {"watertight": False}))
    else:
        card.add(Layer("solid_body", PASS, "one watertight solid body",
                       {"body_count": 1, "watertight": True}))


def _layer_volume_ratio(card: Scorecard, mesh, reason: str) -> None:
    """Layer 3a — NEW: volume ÷ bounding-box volume, doc 07's fingerprint."""
    if mesh is None:
        card.add(Layer("volume_ratio", SKIPPED, reason))
        return
    if not getattr(mesh, "is_watertight", False):
        card.add(Layer("volume_ratio", SKIPPED,
                       "mesh is not watertight — volume is undefined"))
        return
    extents = [float(v) for v in mesh.extents]
    bbox_vol = extents[0] * extents[1] * extents[2]
    vol = float(mesh.volume)
    if bbox_vol <= 0:
        card.add(Layer("volume_ratio", SKIPPED, "degenerate bounding box"))
        return
    ratio = vol / bbox_vol
    data = {"volume_mm3": round(vol, 4), "bbox_volume_mm3": round(bbox_vol, 4),
            "ratio": round(ratio, 4)}
    if ratio <= MIN_PLAUSIBLE_VOLUME_RATIO:
        card.add(Layer("volume_ratio", FAIL,
                       f"solid fills only {ratio * 100:.1f}% of its bounding box — the "
                       f"part is mostly air, which means a cut severed it or the base "
                       f"was built at the wrong scale", data))
    elif ratio > 1.0:
        card.add(Layer("volume_ratio", FAIL,
                       f"volume exceeds the bounding box ({ratio:.3f}) — the mesh is "
                       f"self-intersecting or inverted", data))
    else:
        card.add(Layer("volume_ratio", PASS,
                       f"solid fills {ratio * 100:.1f}% of its bounding box", data))


def _layer_com_symmetry(card: Scorecard, mesh, reason: str,
                        symmetry: Optional[dict]) -> None:
    """Layer 3b — NEW: centre of mass on the declared symmetry plane."""
    if mesh is None:
        card.add(Layer("com_symmetry", SKIPPED, reason))
        return
    sym_type = str((symmetry or {}).get("type") or "").lower()
    if sym_type not in ("mirror", "bilateral", "both", "rotational"):
        card.add(Layer("com_symmetry", SKIPPED,
                       "the drawing declares no symmetry to check against"))
        return
    if not getattr(mesh, "is_watertight", False):
        card.add(Layer("com_symmetry", SKIPPED, "mesh is not watertight"))
        return
    com = [float(v) for v in mesh.center_of_mass]
    bounds_min = [float(v) for v in mesh.bounds[0]]
    bounds_max = [float(v) for v in mesh.bounds[1]]
    # Worst in-plane axis: Z is the extrusion direction, where an asymmetric
    # centre of mass is normal (a blind pocket) rather than a defect.
    offsets = []
    for axis in (0, 1):
        span = bounds_max[axis] - bounds_min[axis]
        if span <= 0:
            continue
        centre = (bounds_max[axis] + bounds_min[axis]) / 2.0
        offsets.append(("XY"[axis], abs(com[axis] - centre), span))
    if not offsets:
        card.add(Layer("com_symmetry", SKIPPED, "degenerate bounds"))
        return
    worst_axis, worst_off, worst_span = max(offsets, key=lambda t: t[1] / t[2])
    frac = worst_off / worst_span
    data = {"axis": worst_axis, "offset_mm": round(worst_off, 4),
            "span_mm": round(worst_span, 4), "offset_fraction": round(frac, 5),
            "declared_symmetry": sym_type}
    if frac > COM_SYMMETRY_TOL_FRAC:
        card.add(Layer("com_symmetry", FAIL,
                       f"the drawing declares {sym_type} symmetry but the centre of mass "
                       f"sits {worst_off:.3f} mm off centre in {worst_axis} "
                       f"({frac * 100:.1f}% of the span) — a feature was built on one "
                       f"side only", data))
    else:
        card.add(Layer("com_symmetry", PASS,
                       f"centre of mass is on the {worst_axis} centre within "
                       f"{frac * 100:.2f}%", data))


def _layer_bbox(card: Scorecard, part_dir: Path) -> None:
    """Layer 2 — bbox vs the drawing's overall dimensions (model_validator's work)."""
    check = _first(part_dir, "*_model_check.txt")
    if check is None:
        card.add(Layer("bounding_box", SKIPPED, "no model check on disk (part not built)"))
        return
    text = check.read_text(encoding="utf-8", errors="replace")
    fails = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("[FAIL]")]
    if fails:
        card.add(Layer("bounding_box", FAIL,
                       "; ".join(f.replace("[FAIL] ", "") for f in fails[:3]),
                       {"failures": fails}))
    else:
        card.add(Layer("bounding_box", PASS, "every checked envelope dimension matched"))


def _layer_features(card: Scorecard, part_dir: Path) -> None:
    """Layer 4 — the per-feature audit (feature_verify already did the measuring)."""
    fv = _load(_first(part_dir, "*_feature_verification.json"))
    if not fv:
        card.add(Layer("feature_audit", SKIPPED, "no per-feature verification on disk"))
        return
    bad = [f for f in (fv.get("features") or [])
           if str(f.get("classification") or "").upper()
           in ("MISSING", "MISPLACED", "WRONG_SIZE", "EXTRA")]
    total = len(fv.get("features") or [])
    if bad:
        card.add(Layer("feature_audit", FAIL,
                       f"{len(bad)} of {total} feature(s) measured wrong: "
                       + ", ".join(f"{f.get('feature_id')}={f.get('classification')}"
                                   for f in bad[:6]),
                       {"bad": [f.get("feature_id") for f in bad], "total": total}))
    else:
        card.add(Layer("feature_audit", PASS, f"all {total} measured feature(s) OK"))


def _layer_constraints(card: Scorecard, part_dir: Path) -> None:
    """Layer 4b — operator must-meet constraints."""
    cv = _load(part_dir / "constraint_verification.json")
    if not cv:
        card.add(Layer("must_meet", SKIPPED, "no must-meet constraints for this part"))
        return
    failed = [c for c in (cv.get("constraints") or [])
              if str(c.get("status") or "").upper() == "FAIL"]
    if failed:
        card.add(Layer("must_meet", FAIL,
                       "; ".join(f"{c.get('id')}: required {c.get('required')}, "
                                 f"measured {c.get('measured')}" for c in failed[:4]),
                       {"failed": [c.get("id") for c in failed]}))
    else:
        card.add(Layer("must_meet", PASS,
                       f"{len(cv.get('constraints') or [])} constraint(s) met"))


def _layer_projection(card: Scorecard, extraction: dict) -> None:
    """Mirror risk (doc 04) — nothing else in the pipeline can see this.

    An unknown angle is NOT a measured failure: the part may be perfectly built.
    It is the doc-04 case of an ambiguity that must be defaulted and DECLARED,
    so the angle is inferred here from the drawing's own evidence (standard,
    then units) and recorded as a low-confidence assumption. That keeps the
    verdict honest — PASS_WITH_ASSUMPTIONS, not FAIL — while making the mirror
    risk impossible to miss, which is the whole point of capturing it.
    """
    from pipeline.schema import infer_projection_angle

    declared = str(extraction.get("projection_angle") or "unknown")
    source = str(extraction.get("projection_angle_source") or "")
    angle, inferred_source, basis = infer_projection_angle(
        declared, str(extraction.get("drawing_standard") or ""),
        str(extraction.get("units") or ""))
    source = source or inferred_source
    data = {"projection_angle": angle, "source": source, "basis": basis}

    if source == "title_block_symbol":
        card.add(Layer("projection_angle", PASS,
                       f"{angle.replace('_', ' ')} read from the title-block symbol", data))
        return
    card.add(Layer("projection_angle", PASS,
                   f"built as {angle.replace('_', ' ')} — {basis}. If the drawing uses "
                   f"the other convention the part is MIRRORED, and no geometric check "
                   f"can detect that", data))
    card.advisories.append(
        f"projection angle {angle.replace('_', ' ')} was {source.replace('_', ' ')}, "
        f"not read from a symbol — verify before manufacture (a wrong convention "
        f"mirrors the part)")
    card.assumptions.append({
        "id": "projection_angle",
        "what": f"projection convention = {angle.replace('_', ' ')}",
        "basis": basis,
        "confidence": 0.5,
        "default_if_unanswered": f"built as {angle.replace('_', ' ')}",
    })


def _layer_dimension_coverage(card: Scorecard, extraction: dict,
                              build_plan: Optional[dict]) -> None:
    """Doc 05's self-check: every dimension should be consumed by some step.

    Advisory, never a FAIL — a reference dimension or a general note legitimately
    drives nothing. An UNUSED dimension is the doc's cheap missed-feature signal,
    so it is reported rather than ignored.
    """
    if not build_plan:
        card.add(Layer("dimension_coverage", SKIPPED, "no build plan on disk"))
        return
    all_ids = {str(d.get("id")) for d in (extraction.get("dimensions") or [])
               if d.get("id")}
    if not all_ids:
        card.add(Layer("dimension_coverage", SKIPPED, "extraction has no dimension ids"))
        return
    used: set[str] = set()
    for step in (build_plan.get("steps") or []):
        for did in ((step.get("evidence") or {}).get("dimension_ids") or []):
            used.add(str(did))
        for did in (step.get("dimension_ids") or []):
            used.add(str(did))
    unused = sorted(all_ids - used)
    data = {"total": len(all_ids), "used": len(all_ids) - len(unused), "unused": unused}
    if not used:
        card.add(Layer("dimension_coverage", SKIPPED,
                       "no step records the dimensions it consumed"))
        return
    if unused:
        card.add(Layer("dimension_coverage", PASS,
                       f"{len(unused)} of {len(all_ids)} dimension(s) drive no build step: "
                       + ", ".join(unused[:8]), data))
        card.advisories.append(
            f"dimensions consumed by no feature ({', '.join(unused[:8])}) — either "
            f"reference dimensions, or a feature was missed")
    else:
        card.add(Layer("dimension_coverage", PASS,
                       f"all {len(all_ids)} dimension(s) drive a build step", data))


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def build_scorecard(part_dir: Path | str, part: str = "",
                    extraction: Optional[dict] = None) -> Scorecard:
    """Grade one part's output directory. Never raises."""
    part_dir = Path(part_dir)
    card = Scorecard(part=part or part_dir.name)
    try:
        if extraction is None:
            extraction = (_load(_first(part_dir, "*_resolved_extraction.json"))
                          or _load(_first(part_dir, "*_extraction.json")) or {})
        build_plan = _load(_first(part_dir, "*_build_plan.json"))
        mesh, reason = _mesh(part_dir)

        _layer_build_health(card, part_dir)
        _layer_solid(card, mesh, reason)
        _layer_bbox(card, part_dir)
        _layer_volume_ratio(card, mesh, reason)
        _layer_com_symmetry(card, mesh, reason, extraction.get("symmetry"))
        _layer_features(card, part_dir)
        _layer_constraints(card, part_dir)
        # Assumptions are collected BEFORE the projection layer, which appends
        # its own inferred-convention assumption to the same list.
        card.assumptions = _collect_assumptions(part_dir, extraction)
        _layer_projection(card, extraction)
        _layer_dimension_coverage(card, extraction, build_plan)
        card.assumptions.sort(key=lambda a: float(a.get("confidence") or 0.0))
        card.overall = _verdict(card)
    except Exception as e:  # a scorecard that crashes tells you nothing
        card.add(Layer("scorecard", SKIPPED, f"{type(e).__name__}: {e}"))
        card.overall = SKIPPED
    return card


def _collect_assumptions(part_dir: Path, extraction: dict) -> list[dict[str, Any]]:
    """Low-confidence resolver assumptions + open human questions, for the
    PASS_WITH_ASSUMPTIONS verdict and the delivery report."""
    out: list[dict[str, Any]] = []
    for d in (extraction.get("dimensions") or []):
        if not d.get("assumption_made"):
            continue
        out.append({
            "id": str(d.get("id") or ""),
            "what": f"{d.get('applies_to') or 'dimension'} = {d.get('value')}",
            "basis": str(d.get("assumption_basis") or "unspecified_basis"),
            "confidence": float(d.get("assumption_confidence") or 0.0),
        })
    assist = _load(_first(part_dir, "*_assist_queue.json")) or {}
    for q in (assist.get("questions") or []):
        if q.get("status") != "pending":
            continue
        out.append({
            "id": str(q.get("feature_id") or q.get("question_id") or ""),
            "what": str(q.get("question_text") or ""),
            "basis": "open_question",
            "confidence": 0.0,
            "default_if_unanswered": q.get("default_if_unanswered"),
        })
    out.sort(key=lambda a: a["confidence"])          # lowest confidence first
    return out


def _verdict(card: Scorecard) -> str:
    if card.failures:
        return FAIL
    graded = [x for x in card.layers if x.status != SKIPPED]
    if not graded:
        return SKIPPED
    if card.assumptions or card.advisories:
        return PASS_WITH_ASSUMPTIONS
    return PASS


def write_scorecard(part_dir: Path | str, part: str = "",
                    extraction: Optional[dict] = None) -> Optional[Path]:
    """Grade and persist ``validation.json``. Never raises."""
    part_dir = Path(part_dir)
    try:
        card = build_scorecard(part_dir, part, extraction)
        path = part_dir / SCORECARD_NAME
        path.write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
        level = log.warning if card.overall == FAIL else log.info
        level("validation scorecard: %s (%s)", card.overall,
              ", ".join(f"{x.name}={x.status}" for x in card.layers))
        return path
    except Exception as e:  # pragma: no cover - defensive
        log.warning("validation scorecard not written: %s", e)
        return None
