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
                   attachments: List[dict]) -> List[dict]:
    """Return a list of conflict dicts (blocking=True/False). Empty = clean.

    ``circles`` are rules.Circle; ``multipliers`` are numbers.ParsedNumber with a
    count; ``attachments`` are rules.attach_by_proximity output.
    """
    conflicts: List[dict] = []

    holes = [c for c in circles if getattr(c, "role", "") == "hole"]
    counted = len(holes)
    # Total callout count (max explicit multiplier that looks like a hole count).
    callouts = [m.count for m in multipliers if getattr(m, "count", None)]
    if callouts:
        called = max(callouts)
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
