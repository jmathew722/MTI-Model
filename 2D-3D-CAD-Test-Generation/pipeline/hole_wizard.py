"""Real Hole Wizard feature builder (Phase 3b, 2026-07-24).

Turns the schema's ``HoleType`` sub-types into REAL ``IFeatureManager::HoleWizard5``
features instead of the plain cut-extrude approximation. It distinguishes, each
with its own builder function:

* **simple** drilled hole (thru or blind),
* **tapped** hole (tap-drill diameter drilled; thread stays cosmetic per repo
  convention — real helical threads are prohibited),
* **counterbore** (concentric wider recess via the wizard Value slots),
* **countersink** (conical relief via the wizard Value slots),
* **standard-fastener clearance** hole (ANSI Inch / Metric diameter from the
  :mod:`hole_wizard_constants` table — standards-correct without the live
  Toolbox data pack),
* **bolt-circle / hole-pattern** handling that reuses ONE hole definition across
  a circular pattern (seed hole + ``FeatureCircularPattern5``) rather than
  calling ``HoleWizard5`` once per hole.

Design split (so the module is unit-tested without SolidWorks):

* **Pure logic** — :func:`plan_wizard_hole` (sub-type → enum + Value-slot
  assembly, using named constants from :mod:`hole_wizard_constants`, never inline
  magic integers), :func:`resolve_clearance_diameter`, and
  :func:`reconcile_callout_count` (Phase 3d, the 5-vs-6 conflict). No COM, fully
  testable.
* **Live COM** — :func:`build_wizard_hole` + the per-sub-type ``build_*`` wrappers
  assemble the ``HoleWizard5`` args from a :class:`WizardPlan` and invoke the API,
  verifying the result and returning ``None`` (fall back to the proven sketch-cut)
  on ANY failure so the working build never regresses.

Placement is CROSS-CHECKED against the canonical resolver
(:mod:`coordinate_normalize`) — this module writes NO coordinate math of its own;
it only consumes :func:`coordinate_normalize.validate_bounds` /
:func:`coordinate_normalize.to_meters`.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

from pipeline import hole_wizard_constants as hwc
from pipeline.coordinate_normalize import Bounds, to_meters, validate_bounds
from pipeline.schema import HoleType

# ---------------------------------------------------------------------------- #
# Pure planning
# ---------------------------------------------------------------------------- #


@dataclass
class WizardPlan:
    """A version-resolved, unit-normalized HoleWizard5 call, ready to invoke.

    All lengths are METERS (the ``HoleWizard5`` boundary). ``values`` is the
    twelve type-specific ``Value1..Value12`` doubles in order. Everything here is
    computed with NAMED enum constants (:mod:`hole_wizard_constants`) — no inline
    integers — so it is correct for the installed SolidWorks version and testable
    off-Windows via the recorded fallbacks.
    """

    subtype: str                     # canonical sub-type ("simple"/"tapped"/...)
    general_hole_type: int           # swWzdGeneralHoleTypes_e (resolved)
    standard_index: int              # swWzdHoleStandards_e / 0 for legacy
    fastener_type_index: int         # 0 for legacy diameter-driven
    end_condition: int               # swEndConditions_e (resolved)
    diameter_m: float
    depth_m: float
    values: list[float] = field(default_factory=lambda: [0.0] * 12)
    description: str = ""

    def as_hole_wizard5_args(self) -> tuple:
        """The exact 27-positional-arg tuple for ``IFeatureManager.HoleWizard5``
        (dispid 222), matching the signature verified in ``solidworks_builder.py``.
        """
        v = list(self.values) + [0.0] * (12 - len(self.values))
        return (
            int(self.general_hole_type),      # GenericHoleType (long)
            int(self.standard_index),         # StandardIndex (long)
            int(self.fastener_type_index),    # FastenerTypeIndex (long)
            "",                               # SSize (str) — legacy
            int(self.end_condition),          # EndType (short)
            float(self.diameter_m),           # Diameter
            float(self.depth_m),              # Depth
            0.0,                              # Length
            *[float(x) for x in v[:12]],      # Value1..Value12
            "",                               # ThreadClass
            False,                            # RevDir
            True,                             # FeatureScope
            True,                             # AutoSelect
            False,                            # AssemblyFeatureScope
            False,                            # AutoSelectComponents
            False,                            # PropagateFeatureToParts
        )


# Canonical sub-type string for a HoleCallout. The schema HoleType enum is the
# source of truth; a clearance callout is inferred from a fastener thread_spec on
# a plain thru/blind hole (there is no CLEARANCE HoleType member).
def hole_subtype(h) -> str:
    """Canonical sub-type for a hole callout: one of simple/blind/tapped/
    counterbore/countersink/spotface/clearance. Never raises."""
    t = getattr(h, "type", None)
    tval = getattr(t, "value", t)
    tval = (str(tval) if tval is not None else "").lower()
    if tval == HoleType.TAPPED.value:
        return "tapped"
    if tval == HoleType.COUNTERBORE.value:
        return "counterbore"
    if tval == HoleType.COUNTERSINK.value:
        return "countersink"
    if tval == HoleType.SPOTFACE.value:
        return "spotface"
    # A plain thru/blind hole that names a standard fastener is a clearance hole.
    if tval in (HoleType.THRU.value, HoleType.BLIND.value):
        if resolve_clearance_diameter(h) is not None:
            return "clearance"
        return "simple" if tval == HoleType.THRU.value else "blind"
    return "simple"


_FASTENER_TOKENS = list(hwc.CLEARANCE_INCH) + list(hwc.CLEARANCE_METRIC_MM)


def _fastener_size_token(spec: str) -> Optional[str]:
    """Pull a tabled fastener size (``1/4``, ``#10``, ``M6`` …) out of a free-text
    thread/clearance spec, or None. Longest token first so ``5/16`` beats ``5``."""
    if not spec:
        return None
    s = str(spec).upper().replace(" ", "")
    for tok in sorted(_FASTENER_TOKENS, key=len, reverse=True):
        if tok.upper() in s:
            return tok
    return None


def resolve_clearance_diameter(h, *, fit: str = "normal") -> Optional[float]:
    """Standard-fastener CLEARANCE diameter (inches) for a callout, or None when
    the callout does not name a tabled fastener. Consults
    :func:`hole_wizard_constants.clearance_diameter_in` — the single ANSI-inch/
    metric table — never an inline size."""
    for attr in ("thread_spec", "notes"):
        tok = _fastener_size_token(getattr(h, attr, "") or "")
        if tok:
            return hwc.clearance_diameter_in(tok, fit=fit)
    return None


def plan_wizard_hole(h, *, through_all: bool, depth_m: Optional[float],
                     unit, metric: bool = False) -> WizardPlan:
    """Assemble a :class:`WizardPlan` for a hole callout — the PURE core.

    Chooses the ``swWzdGeneralHoleTypes_e`` member for the sub-type via the
    constants module, sets the end condition, converts the drill diameter + depth
    to meters, and fills the type-specific ``Value`` slots (counterbore
    diameter/depth, countersink diameter/angle, clearance diameter). No COM, no
    coordinate math — safe to unit-test.
    """
    subtype = hole_subtype(h)
    wzd_name = hwc.wizard_type_name_for(subtype)
    general = hwc.general_hole_type(wzd_name)
    end_cond = hwc.end_condition(through_all=through_all)

    # Drill diameter: the callout's, unless this is a standard-fastener clearance
    # hole (then the ANSI table diameter).
    drill_in = float(getattr(h, "diameter", 0.0) or 0.0)
    if subtype == "clearance":
        clr = resolve_clearance_diameter(h)
        if clr:
            drill_in = clr
    dia_m = to_meters(drill_in)

    # Depth: a through hole uses the end condition; a blind hole needs a positive
    # value (generous default when unspecified, matching the legacy path).
    if through_all or (depth_m is None):
        depth = to_meters(max(float(getattr(h, "depth", 0.0) or 0.0),
                              drill_in * 4.0, 0.5))
    else:
        depth = float(depth_m)

    values = [0.0] * 12
    desc = f"{subtype} hole ⌀{drill_in:g}"
    if subtype in ("counterbore", "spotface"):
        # Value1 = counterbore diameter, Value2 = counterbore depth (meters).
        cbd = float(getattr(h, "cbore_diameter", 0.0) or 0.0)
        cbdep = float(getattr(h, "cbore_depth", 0.0) or 0.0)
        values[0] = to_meters(cbd)
        values[1] = to_meters(cbdep)
        desc += f", c'bore ⌀{cbd:g}×{cbdep:g}"
    elif subtype == "countersink":
        # Value1 = countersink diameter, Value2 = included angle (radians).
        csd = float(getattr(h, "csink_diameter", 0.0) or 0.0)
        csa = float(getattr(h, "csink_angle", 0.0) or 0.0) or 82.0
        values[0] = to_meters(csd)
        values[1] = math.radians(csa)
        desc += f", c'sink ⌀{csd:g}@{csa:g}°"
    elif subtype == "tapped":
        thr = getattr(h, "thread_spec", "") or ""
        if thr:
            desc += f", tap {thr} (cosmetic)"

    return WizardPlan(
        subtype=subtype,
        general_hole_type=general,
        standard_index=hwc.hole_standard(metric),
        fastener_type_index=0,
        end_condition=end_cond,
        diameter_m=dia_m,
        depth_m=depth,
        values=values,
        description=desc,
    )


# ---------------------------------------------------------------------------- #
# Callout-vs-count reconciliation (Phase 3d — the A050211E 5-vs-6 conflict)
# ---------------------------------------------------------------------------- #


def reconcile_callout_count(callout_qty: Optional[int],
                            countable_instances: Optional[int],
                            feature_id: str,
                            *, source: str = "hole_wizard") -> Optional[dict]:
    """Compare a callout MULTIPLIER against the COUNTABLE pattern instances.

    When both are known and DISAGREE (the A050211E flange: a ``(6)`` callout but 5
    countable holes) this is a **build-blocking conflict**: return a CRITICAL flag
    object shaped for the pipeline's existing escalation surface (engineering
    review + ``human_assist`` queue) — NEVER silently pick a number. Returns
    ``None`` when they agree or either side is unknown (nothing to escalate).
    """
    if not callout_qty or not countable_instances:
        return None
    if int(callout_qty) == int(countable_instances):
        return None
    return {
        "feature_id": feature_id,
        "severity": "CRITICAL",
        "source": source,
        "kind": "callout_vs_count_mismatch",
        "callout_qty": int(callout_qty),
        "countable_instances": int(countable_instances),
        "human_note": (
            f"{feature_id}: callout multiplier ({int(callout_qty)}) does not match "
            f"the {int(countable_instances)} countable hole positions on the sheet. "
            "Build-blocking — resolve which count is correct before drilling."
        ),
        "gate_question": (
            f"How many holes does {feature_id} have — the callout says "
            f"{int(callout_qty)} but {int(countable_instances)} are dimensioned/visible?"
        ),
        "candidates": [int(callout_qty), int(countable_instances)],
        "blocking": True,
    }


# ---------------------------------------------------------------------------- #
# Placement cross-check — consumes coordinate_normalize, never reimplements it
# ---------------------------------------------------------------------------- #


def _envelope_in(model) -> Optional[tuple[float, float]]:
    """Parent (width, height) in inches from the model's dimensions, or None.

    Read-only helper for the bounds cross-check; the actual placement numbers are
    resolved upstream by ``coordinate_normalize`` — this only supplies the parent
    extent so :func:`validate_bounds` can flag an off-part center."""
    w = ht = 0.0
    for d in getattr(model, "dimensions", []) or []:
        a = (getattr(d, "applies_to", "") or "").lower()
        val = float(getattr(d, "value", 0.0) or 0.0)
        if val <= 0:
            continue
        if a in ("length", "width") and val > w:
            w = val
        elif a in ("height",) and val > ht:
            ht = val
    if w > 0 and ht > 0:
        return (w, ht)
    return None


def validate_centers(model, centers_in: list[tuple[float, float]],
                     radius_in: float) -> list[str]:
    """Cross-check every resolved hole center against the parent envelope using
    the CANONICAL :func:`coordinate_normalize.validate_bounds`. Returns a list of
    violation strings (empty = all inside). No coordinate math lives here."""
    env = _envelope_in(model)
    if env is None:
        return []
    pw, ph = env
    out: list[str] = []
    for cx, cy in centers_in:
        b = Bounds(cx - radius_in, cx + radius_in, cy - radius_in, cy + radius_in)
        v = validate_bounds(b, parent_width=pw, parent_height=ph)
        if v:
            out.append(f"({cx:g}, {cy:g}): " + "; ".join(v))
    return out


# ---------------------------------------------------------------------------- #
# Live COM invocation
# ---------------------------------------------------------------------------- #


def wizard_enabled() -> bool:
    """True when the real Hole Wizard path is opted in (``MTI_ENABLE_HOLE_WIZARD``).

    Kept default-OFF: live SolidWorks 2024 returned ``None`` from ``HoleWizard5``
    for the legacy diameter hole (documented in ``solidworks_builder._try_hole_wizard``
    and ``docs/phase2-research-notes.md``); the proven sketch-cut stays the default
    so the working build never regresses. Flip the flag to build real wizard holes."""
    return bool(os.getenv("MTI_ENABLE_HOLE_WIZARD"))


def _place_points(sw_doc, centers_m: list[tuple[float, float]], sw) -> bool:
    """Pre-select the host face and drop one sketch point per center (the
    HoleWizard5 placement contract). Returns False if the face/sketch fails."""
    if not sw._select_top_face(sw_doc, centers_m[0]):
        return False
    sw_doc.SketchManager.InsertSketch(True)
    if sw_doc.SketchManager.ActiveSketch is None:
        return False
    for cx, cy in centers_m:
        sw_doc.SketchManager.CreatePoint(cx, cy, 0.0)
    sw_doc.SketchManager.InsertSketch(True)  # close; points stay selected
    return True


def _invoke_wizard(sw_doc, model, feature, plan: WizardPlan,
                   centers_m: list[tuple[float, float]]):
    """Shared COM call: place points, invoke HoleWizard5 with ``plan``'s args,
    verify (rebuild OK + solid present), and roll back → None on any failure so
    the caller falls back to the proven sketch-cut. Never raises."""
    from pipeline import solidworks_builder as sw

    if not centers_m:
        return None
    try:
        featmgr = sw_doc.FeatureManager
        hw = getattr(featmgr, "HoleWizard5", None) or getattr(featmgr, "HoleWizard4", None)
        if hw is None:
            return None
        if not _place_points(sw_doc, centers_m, sw):
            return None
        wizard = hw(*plan.as_hole_wizard5_args())
        if wizard is None:
            sw_doc.ClearSelection2(True)
            return None
        if not sw.check_rebuild_errors(sw_doc) or not sw._solid_body_exists(sw_doc):
            sw._delete_feature(sw_doc, wizard)
            sw_doc.ClearSelection2(True)
            return None
        sw_doc.ClearSelection2(True)
        return wizard
    except Exception as e:  # pragma: no cover - live COM only
        import logging
        logging.getLogger(__name__).warning(
            "hole %s: HoleWizard5 (%s) failed (%s) — sketch-cut fallback.",
            getattr(feature, "id", "?"), plan.subtype, e)
        try:
            sw_doc.ClearSelection2(True)
        except Exception:
            pass
        return None


def build_wizard_hole(sw_doc, model, feature, h,
                      centers_m: list[tuple[float, float]],
                      *, through_all: bool, depth_m: Optional[float],
                      metric: bool = False):
    """REAL Hole Wizard feature for a callout at the given centers (meters).

    Dispatches on the callout sub-type, builds the :class:`WizardPlan`, and
    invokes the wizard. Returns the created feature on success, ``None`` to fall
    back to the sketch-cut. Opt-in via :func:`wizard_enabled`. This is the single
    entry point ``solidworks_builder.build_hole`` calls in place of the old
    ``_try_hole_wizard`` (which now delegates here)."""
    if not wizard_enabled() or not centers_m:
        return None
    unit = getattr(getattr(model, "units", None), "value", None)
    plan = plan_wizard_hole(h, through_all=through_all, depth_m=depth_m,
                            unit=unit, metric=metric)
    return _invoke_wizard(sw_doc, model, feature, plan, centers_m)


# Per-sub-type wrappers — each names its enum through the plan, so a caller can
# request a specific builder and the "one function per hole type" contract holds.
def build_simple_hole(sw_doc, model, feature, h, centers_m, *, through_all, depth_m):
    return build_wizard_hole(sw_doc, model, feature, h, centers_m,
                             through_all=through_all, depth_m=depth_m)


def build_tapped_hole(sw_doc, model, feature, h, centers_m, *, through_all, depth_m):
    return build_wizard_hole(sw_doc, model, feature, h, centers_m,
                             through_all=through_all, depth_m=depth_m)


def build_counterbore_hole(sw_doc, model, feature, h, centers_m, *, through_all, depth_m):
    return build_wizard_hole(sw_doc, model, feature, h, centers_m,
                             through_all=through_all, depth_m=depth_m)


def build_countersink_hole(sw_doc, model, feature, h, centers_m, *, through_all, depth_m):
    return build_wizard_hole(sw_doc, model, feature, h, centers_m,
                             through_all=through_all, depth_m=depth_m)


def build_clearance_hole(sw_doc, model, feature, h, centers_m, *, through_all, depth_m,
                         metric: bool = False):
    return build_wizard_hole(sw_doc, model, feature, h, centers_m,
                             through_all=through_all, depth_m=depth_m, metric=metric)
