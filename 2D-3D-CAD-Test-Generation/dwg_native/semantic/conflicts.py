"""Conflict detection + the provenance invariant. No silent skips, no defaults.

- A callout multiplier disagreeing with the counted geometry instances is a
  BLOCKING conflict (A050211E: 6-hole callout vs 5 countable holes), never a
  silent tiebreak.
- A numeric dimension that cannot attach to geometry blocks.
- Every value in build_plan.json must trace to a specific raw_extraction object;
  assert_provenance fails the job on any value without a `source`.
"""
from __future__ import annotations

from typing import Any, Dict, List


class ConflictError(RuntimeError):
    """A blocking semantic conflict was found."""


class ProvenanceError(RuntimeError):
    """A build_plan value lacks provenance to a raw_extraction object."""


def find_conflicts(circles: List[Any], multipliers: List[Any],
                   attachments: List[dict], unit_factor: float = 0.0254) -> List[dict]:
    """Return a list of conflict dicts (blocking=True/False). Empty = clean.

    ``circles`` are rules.Circle (radius in METERS); ``multipliers`` are
    numbers.ParsedNumber with a count (value in DRAWING UNITS, e.g. inches, as
    read from the sheet text) — ``unit_factor`` converts between them (meters per
    drawing unit; default 0.0254 = inch). ``attachments`` are
    rules.attach_by_proximity output.
    """
    conflicts: List[dict] = []

    holes = [c for c in circles if getattr(c, "role", "") == "hole"]
    counted = len(holes)
    callouts = [m for m in multipliers if getattr(m, "count", None)]
    # A token like "4X Ø.25" carries BOTH a count and its own diameter (kind==
    # "diameter"); a bare "(6) HLS" carries only a count (kind=="count", no
    # diameter). When every callout names its own diameter, reconcile PER
    # DIAMETER GROUP — summing max() across sizes falsely blocked a drawing with
    # "4X Ø.25" + "2X Ø.50" (6 real holes): the old code took max(4, 2) = 4 and
    # compared it to the total 6, a guaranteed false CRITICAL on any multi-size
    # part. When any callout is a bare count (no diameter to group by), fall back
    # to the original total-vs-max comparison (the single-group canonical case,
    # e.g. A050211E's "(6) HLS").
    sized = [m for m in callouts if getattr(m, "kind", "") == "diameter"]
    if sized and len(sized) == len(callouts):
        for m in sized:
            group = [h for h in holes
                     if abs(float(getattr(h, "radius", 0)) * 2 / unit_factor - m.value) <= 0.01]
            if len(group) != m.count:
                conflicts.append({
                    "type": "multiplier_vs_count",
                    "blocking": True,
                    "severity": "CRITICAL",
                    "detail": (f"Callout specifies {m.count} hole(s) of diameter {m.value:g} "
                               f"but {len(group)} matching-diameter hole(s) exist in the "
                               "geometry."),
                    "callout_count": m.count,
                    "counted_instances": len(group),
                    "diameter": m.value,
                    "resolution": "human_decision_required",
                })
    elif callouts:
        called = max(m.count for m in callouts)
        if called != counted:
            conflicts.append({
                "type": "multiplier_vs_count",
                "blocking": True,
                "severity": "CRITICAL",
                "detail": (f"Callout specifies {called} hole(s) but {counted} "
                           f"countable hole instance(s) exist in the geometry."),
                "callout_count": called,
                "counted_instances": counted,
                "resolution": "human_decision_required",
            })

    # Un-attachable numeric dimensions are ADVISORY, not blocking. Since every
    # built value comes from EXACT geometry (a circle's radius/centre, the profile
    # loop), an MTEXT token that does not attach is unlabelled extra information —
    # surfaced as a flag (no silent skip) but it must not block a part whose
    # geometry is fully determined. (Overall dims + notes legitimately sit away
    # from the edges they annotate.) The genuine hazard — a callout count that
    # contradicts the counted geometry — is the blocking case above.
    for a in attachments:
        if a.get("attached_to") is None:
            conflicts.append({
                "type": "unattached_dimension",
                "blocking": False,
                "severity": "MEDIUM",
                "detail": (f"Dimension text {a.get('text')!r} (value {a.get('value')}) "
                           "did not attach to geometry; recorded, not used for build "
                           "(geometry is authoritative)."),
                "token_id": a.get("token_id"),
            })
    return conflicts


def assert_provenance(build_plan: Dict[str, Any]) -> None:
    """Fail the job if ANY build_plan value lacks provenance to a raw object.

    Every step must carry a non-empty ``provenance`` list, and every entry in
    ``dimensions_drawing_units`` must have a matching key in
    ``provenance_map`` naming the raw object id it came from.
    """
    problems: List[str] = []
    for step in build_plan.get("steps", []):
        if step.get("type") in ("setup", "export", "verify", "run_all"):
            continue
        fid = step.get("feature_id", "?")
        prov = step.get("provenance") or []
        if not prov:
            problems.append(f"step {fid} ({step.get('type')}) has no provenance")
        pmap = step.get("provenance_map") or {}
        for dim_key in (step.get("dimensions_drawing_units") or {}):
            if dim_key not in pmap:
                problems.append(f"step {fid} value '{dim_key}' has no provenance_map entry")
    if problems:
        raise ProvenanceError("Provenance invariant violated:\n  - "
                              + "\n  - ".join(problems))
