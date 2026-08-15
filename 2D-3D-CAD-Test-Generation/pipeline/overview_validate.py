"""Stage 11 — overview validation: ONE module, named sub-checks.

Merged 2026-08-15 from ``overview_check.py`` + ``overview_macro_validate.py``
(REFACTOR_ANALYSIS §1.3). Both files did the same category of work — diff what
the pipeline produced against what the OVERVIEW says the drawing shows — split
across two modules with no boundary between them, so a reader had to know which
file a given overview question lived in. There is now one module, one import,
and one place to add the next overview-derived check.

Two INPUTS, one job. The overview evidence arrives in two forms, and each has
its own sub-checks:

  **A. the overview IMAGE** (the ``full`` view: the whole sheet / isometric).
  Re-examined ALONE by a focused vision pass that lists every discrete feature
  it shows; that list is diffed against what the pipeline captured and built.
      * :func:`check_missing_features` (alias :func:`cross_check`) — a feature
        clearly visible in the overview but absent from the build is CRITICAL; a
        count shortfall is HIGH; a possibly-shown feature is MEDIUM; an envelope
        axis matching no extracted dimension is HIGH. Features in the build but
        not visible in the overview are FINE and never flagged — an overview
        cannot show every hidden feature.
      * Entry point: :func:`run_overview_check` (exception-safe wrapper).

  **B. the Stage 1.5 overview ANALYSIS words** (``overview_analysis.json``).
  Checked against the GENERATED MACRO PACKAGE — where :mod:`pipeline.macro_echo`
  proves every emitted literal round-trips to the build plan, these prove the
  package as a whole agrees with what the overview pass said the drawing shows:
      * :func:`check_hole_counts` — a global note stating a feature count
        ("(6) HLS" → ``resolved_count: 6``) must equal the hole instances the
        macros actually drill (baked circles + circular-pattern copies);
      * :func:`check_correspondences` — every cross-view feature the overview
        saw must map to ≥1 generated build step (canonicalized words);
      * :func:`check_through_vs_blind` — a relation confirming THROUGH (or
        blind) must not meet a step built the other way;
      * :func:`check_conflict_carryover` — unreconciled CRITICAL/HIGH overview
        conflicts re-surfaced next to the macros they affect;
      * :func:`check_symmetry_advisory` — declared rotational symmetry with no
        pattern step (informational).
      * Entry points: :func:`validate_macros_against_overview` (pure),
        :func:`run_overview_macro_validation` (load + validate + persist).

**One advisory/strict toggle.** Both halves are ADVISORY by default — they
produce findings, never exceptions: the pipeline principle is *resolve and flag,
never block*. Strict mode is opt-in per half and does the same thing in both:
raise instead of returning findings — :func:`assert_overview_macro_validation`
for B, and for A the caller's existing gate (a CRITICAL finding gates READY
unless ``--skip-overview-check``).

Report artifacts are unchanged by the merge: A's findings flow into the
engineering review + verification report; B writes
``<Part>_macro_overview_validation.json`` and adds ``overview_macro_validation``
to ``build_plan.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

log = get_logger()


# =========================================================================== #
# PART A — the overview IMAGE vs the extraction/build
# =========================================================================== #
OVERVIEW_TOOL_NAME = "report_overview_features"
# Mixed into the cache key — bump when the prompt below changes meaningfully.
OVERVIEW_PROMPT_VERSION = "1"
MAX_TOKENS = 4000

# Feature kinds the overview pass may report. Kept deliberately coarse — this is
# a cross-check, not a second extraction.
_KINDS = ("hole", "counterbore", "countersink", "thread", "slot", "cutout",
          "fillet", "chamfer", "rib", "boss", "pattern", "shell", "other")

_SYSTEM = """\
You are a senior inspection engineer looking at the OVERVIEW sheet of a 2D
engineering drawing. Your only job is to list every DISCRETE FEATURE the
drawing visibly shows, with honest counts, so a reviewer can confirm nothing
was missed by an earlier extraction. Do NOT extract dimensions in detail —
report features, counts, and the overall envelope only. Report a feature as
clearly_visible=false when it is small, partially hidden, or you are unsure it
exists. Call the required tool exactly once."""

_USER_TEXT = """\
List every discrete feature visible in this drawing (holes, counterbores,
countersinks, threads, slots, cutouts, fillets, chamfers, ribs, bosses,
patterns, shells). Give the count of each repeated feature (e.g. a 4-hole bolt
pattern -> kind "hole", count 4). Also report the overall part envelope
(width/height and depth if readable) with its units. Be honest: mark anything
uncertain with clearly_visible=false rather than omitting or inventing it."""


def _overview_tool() -> dict[str, Any]:
    return {
        "name": OVERVIEW_TOOL_NAME,
        "description": "Report every discrete feature visible in the overview drawing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "features": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(_KINDS)},
                            "count": {"type": "integer", "minimum": 1},
                            "description": {"type": "string"},
                            "location": {"type": "string"},
                            "clearly_visible": {"type": "boolean"},
                        },
                        "required": ["kind", "count", "description"],
                    },
                },
                "envelope": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "depth": {"type": "number"},
                        "units": {"type": "string"},
                    },
                },
                "notes": {"type": "string"},
            },
            "required": ["features"],
        },
    }


def _cache_key(image_b64: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(b"overview\0")
    h.update(OVERVIEW_PROMPT_VERSION.encode("utf-8"))
    h.update(b"\0")
    h.update(model.encode("utf-8"))
    h.update(b"\0")
    h.update(image_b64.encode("utf-8"))
    return h.hexdigest()


def extract_overview_features(
    image_b64: str,
    media_type: str = "image/png",
    model: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    usage_out: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """One focused vision call on the overview image; returns the tool input dict
    ``{"features": [...], "envelope": {...}, "notes": ...}``. Cached like the main
    extraction so re-runs are free."""
    from pipeline.extractor import (
        DEFAULT_MODEL,
        _accumulate_usage,
        _build_client,
        _cache_lookup,
        _cache_store,
        _image_block,
        SDK_MAX_RETRIES,
    )

    model = model or os.getenv("EXTRACTION_MODEL") or DEFAULT_MODEL
    key = _cache_key(image_b64, model)
    cached = _cache_lookup(cache_dir, key)
    if cached is not None:
        log.info("Overview-check cache hit (%s...)", key[:12])
        return cached

    client = _build_client(max_retries=SDK_MAX_RETRIES)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_b64, media_type),
                {"type": "text", "text": _USER_TEXT},
            ],
        }],
        tools=[_overview_tool()],
        tool_choice={"type": "tool", "name": OVERVIEW_TOOL_NAME},
    )
    if usage_out is not None:
        _accumulate_usage(usage_out, response)
    tool_use = next(
        (b for b in response.content
         if getattr(b, "type", None) == "tool_use" and b.name == OVERVIEW_TOOL_NAME),
        None,
    )
    if tool_use is None:
        raise RuntimeError(
            f"Overview pass did not call {OVERVIEW_TOOL_NAME!r} "
            f"(stop_reason={getattr(response, 'stop_reason', None)})"
        )
    data = dict(tool_use.input or {})
    data.setdefault("features", [])
    _cache_store(cache_dir, key, data)
    return data


# --------------------------------------------------------------------------- #
# Diff logic (pure, unit-testable — no API)
# --------------------------------------------------------------------------- #

def _build_inventory(extraction: dict) -> dict[str, int]:
    """Count what the pipeline captured, per overview kind. Conservative and
    generous on the build side: a kind is matched by feature types AND hole
    callout types AND description keywords, so an overview finding is only
    raised when the build genuinely has nothing that could account for it."""
    inv = {k: 0 for k in _KINDS}
    callouts = extraction.get("hole_callouts") or []
    for h in callouts:
        qty = max(int(h.get("qty") or 1), 1)
        htype = str(h.get("type") or "").lower()
        inv["hole"] += qty
        if "counterbore" in htype or "spotface" in htype:
            inv["counterbore"] += qty
        if "countersink" in htype:
            inv["countersink"] += qty
        if "tap" in htype or h.get("thread_spec"):
            inv["thread"] += qty
        if str(h.get("pattern") or "").strip():
            inv["pattern"] += 1

    text_kinds = {
        "slot": ("slot", "keyway", "groove"),
        "cutout": ("cutout", "cut-out", "window", "notch", "opening"),
        "rib": ("rib", "web", "gusset"),
        "boss": ("boss", "standoff", "pad"),
    }
    for f in extraction.get("features") or []:
        ftype = str(f.get("type") or "").lower()
        desc = f"{f.get('description') or ''} {f.get('notes') or ''}".lower()
        if ftype == "hole":
            # counted via callouts when present; count the feature only if no callouts
            if not callouts:
                inv["hole"] += 1
        elif ftype in ("fillet", "chamfer", "pattern", "shell", "thread"):
            inv[ftype] += 1
        elif ftype == "extrude_boss":
            inv["boss"] += 1
        elif ftype == "extrude_cut":
            inv["cutout"] += 1
        for kind, needles in text_kinds.items():
            if any(n in desc for n in needles):
                inv[kind] += 1
    return inv


def _envelope_items(envelope: dict, extraction: dict) -> list[dict[str, Any]]:
    """Check the overview's overall envelope numbers against the extracted
    dimensions (with inch<->mm conversion). An axis value with no matching
    dimension anywhere is HIGH — the build may be the wrong overall size."""
    items: list[dict[str, Any]] = []
    if not envelope:
        return items
    dims = extraction.get("dimensions") or []
    values: list[float] = []
    for d in dims:
        for k in ("resolved_value", "value"):
            v = d.get(k)
            if isinstance(v, (int, float)) and v > 0:
                values.append(float(v))
    if not values:
        return items

    def _matched(v: float) -> bool:
        for cand in (v, v * 25.4, v / 25.4):
            for known in values:
                if known > 0 and abs(cand - known) / max(known, 1e-9) <= 0.02:
                    return True
        return False

    for axis in ("width", "height", "depth"):
        v = envelope.get(axis)
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if not _matched(float(v)):
            units = envelope.get("units") or "drawing units"
            items.append(_item(
                "HIGH", f"OV-ENV-{axis.upper()}",
                what=f"The overview shows an overall {axis} of {v:g} {units}, but no "
                     f"extracted dimension matches it (±2%, inch/mm checked).",
                decision="build proceeded with the extracted dimensions",
                why="overall envelope read from the overview drawing disagrees with "
                    "or is missing from the extraction",
                affects="overall part envelope",
            ))
    return items


def _item(severity: str, item_id: str, what: str, decision: str, why: str,
          affects: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "source": "overview",
        "id": item_id,
        "what": what,
        "decision": decision,
        "why": why,
        "affects": affects,
    }


# Kinds where the overview's count is reliable enough to flag a mismatch.
_COUNT_CHECKED = {"hole", "counterbore", "countersink", "thread"}

# Fix 4.1 (learning-loop 2026-07-09: 15 recurring "cannot auto-match" noise
# flags). Legitimate drawing content that isn't a machinable feature — classify
# it and give each a reconciliation rule instead of the generic noise flag.
_NONFEATURE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stock_thickness_view", ("thick", "thickness", "stock", "gauge", "gage", "material thick")),
    ("surface_finish_note",  ("finish", "coat", "plat", "anodiz", "cfs", "paint", "powder", "zinc")),
    ("hardware_reference_note", ("hardware", "screw", "nut", "rivet", "standoff", "insert",
                                 "pem", "washer", "bolt ", "fastener")),
    ("reference_boundary", ("dashed", "phantom", "hidden line", "reference", "ref only",
                            "envelope", "boundary")),
    ("formed_profile", ("form", "bend", "bent", "flange", "tab", "brake", "developed")),
)
_NUM_RE = re.compile(r"(\d*\.\d+|\d+)")  # captures ".105", "0.105", "105"


def _classify_nonfeature(text: str) -> str:
    t = (text or "").lower()
    for kind, needles in _NONFEATURE_KINDS:
        if any(k in t for k in needles):
            return kind
    return "unknown"


def _extraction_thickness_in(extraction: dict) -> Optional[float]:
    """Part thickness/extrude depth from the extraction (drawing units), if any."""
    for d in extraction.get("dimensions", []) or []:
        applies = str(d.get("applies_to", "")).lower()
        if any(t in applies for t in ("thick", "depth", "height")) and (d.get("value") or 0) > 0:
            return float(d["value"])
    return None


def _reconcile_nonfeature(fid: str, desc: str, where: str, clearly: bool,
                          extraction: dict) -> Optional[dict[str, Any]]:
    """Reconcile a non-machinable overview item by kind. Returns a review item,
    or None when it reconciles cleanly (no noise flag)."""
    kind = _classify_nonfeature(f"{desc} {where}")
    loc = f" ({where})" if where else ""

    if kind == "stock_thickness_view":
        # P2 (2026-07-10): parse gauge callouts gauge-aware. "12 GA. (.105)" must
        # reconcile as .105 (the decimal), NOT 12 (the gauge) — a naive first-number
        # grab produced four false 12.0-vs-.105 mismatches this cycle. A gauge-only
        # callout with unknown material is ambiguous, so it is recorded (not
        # compared), never guessed. The comparison is always decimal-to-decimal.
        from pipeline.gauge import parse_thickness_callout

        thk_build = _extraction_thickness_in(extraction)
        reading = parse_thickness_callout(desc, material=extraction.get("material"))
        gauge_note = f" [gauge {reading.gauge}]" if reading.gauge else ""

        if reading.needs_material:
            return _item("LOW", fid,
                         what=f"Gauge thickness noted but material unknown: {desc}{loc}{gauge_note}. "
                              f"Cannot convert a gauge number to a decimal without the material — "
                              f"not compared to the build.",
                         decision="recorded as the part's gauge reference; not reconciled",
                         why="a gauge number is ambiguous across materials (no decimal given)",
                         affects="metadata: stock gauge")
        shown = reading.thickness_in
        if shown is not None and thk_build:
            # P8 (2026-07-10): plausibility band. A "thickness" that is an
            # order of magnitude off the build in EITHER direction (A001551E:
            # 21.0 vs .50 — 42x) is almost certainly an unrelated number the
            # thickness-view extractor captured, not a real disagreement. Reject
            # it as out-of-band instead of raising a false HIGH conflict.
            ratio = max(shown, thk_build) / max(min(shown, thk_build), 1e-9)
            if ratio > 5.0:
                return _item("LOW", fid,
                             what=f"Thickness-view value {shown} is out of plausible range vs the "
                                  f"build thickness {thk_build} ({ratio:.0f}x){loc}{gauge_note}; "
                                  f"treated as a non-thickness number, not a conflict.",
                             decision="ignored as an out-of-band capture, build thickness kept",
                             why="an edge-view thickness cannot differ from the build by an order "
                                 "of magnitude — the extractor captured an unrelated dimension",
                             affects="metadata: rejected thickness candidate")
            if abs(shown - thk_build) <= max(0.01, 0.03 * thk_build):
                return None  # thickness view agrees with the built extrude depth
            return _item("HIGH", fid,
                         what=f"Thickness mismatch: the overview's thickness view shows {shown} but "
                              f"the build's extrude depth is {thk_build}{loc}{gauge_note}.",
                         decision="build kept its extracted thickness",
                         why="stock-thickness view (decimal) disagrees with the extrude depth",
                         affects="part thickness / extrude depth")
        return _item("LOW", fid,
                     what=f"Stock/thickness view noted: {desc}{loc}{gauge_note}.",
                     decision="recorded as the part's thickness reference",
                     why="a thickness edge view is not a machinable feature",
                     affects="metadata: stock thickness")

    if kind in ("surface_finish_note", "hardware_reference_note"):
        label = "surface finish" if kind == "surface_finish_note" else "hardware reference"
        return _item("LOW", fid,
                     what=f"{label.title()} note: {desc}{loc}.",
                     decision=f"attached to part metadata as a {label} note",
                     why="a note, not part geometry — no build counterpart expected",
                     affects=f"metadata: {label}")

    if kind == "reference_boundary":
        return _item("LOW", fid,
                     what=f"Reference/phantom geometry noted: {desc}{loc}.",
                     decision="treated as a reference boundary, not built geometry",
                     why="dashed/phantom/reference linework is not a solid feature",
                     affects="metadata: reference boundary")

    if kind == "formed_profile":
        return _item("MEDIUM" if clearly else "LOW", fid,
                     what=f"Formed/bent profile shown: {desc}{loc}.",
                     decision="not built — the pipeline models machined prismatic parts, "
                              "not sheet-metal forming",
                     why="a bend/flange is an unsupported feature kind (escalate if the part "
                         "is truly sheet metal)",
                     affects="unsupported feature kind: formed profile")

    # Genuinely unknown content keeps the honest generic flag (now rare, so it
    # regains signal value).
    return _item("MEDIUM" if clearly else "LOW", fid,
                 what=f"The overview shows content the checker cannot auto-match: {desc}{loc}",
                 decision="not automatically verified against the build",
                 why="no direct counterpart in the build inventory or the non-feature taxonomy",
                 affects="manual visual comparison recommended")


def cross_check(overview: dict, extraction: dict) -> list[dict[str, Any]]:
    """Diff the overview feature list against the consolidated extraction.
    Returns engineering-review item dicts (source="overview"), worst first.
    Only overview->build gaps are flagged; extra build features are fine."""
    items: list[dict[str, Any]] = []
    inv = _build_inventory(extraction)
    # Count checking is AGGREGATE, not per-callout: the overview lists holes per
    # callout GROUP (e.g. ".406 DIA (2) HL'S" -> 2, ".422 6-HOLES" -> 6) while the
    # build inventory is the TOTAL of every group. Comparing one group's count to
    # the grand total is apples-to-oranges and fired false "2 vs 5" HIGH flags
    # (learning-loop 2026-07-09: A001211E, A001271E, A001621E, A001821M). Instead,
    # sum the overview's per-group counts for a kind and compare that TOTAL to the
    # build total — flag only a genuine shortfall (overview total > build total),
    # which is the only direction that means a feature is missing.
    ov_counts: dict[str, int] = {}
    n = 0
    for f in overview.get("features") or []:
        kind = str(f.get("kind") or "other").lower()
        if kind not in _KINDS:
            kind = "other"
        count = max(int(f.get("count") or 1), 1)
        clearly = bool(f.get("clearly_visible", True))
        desc = str(f.get("description") or kind)
        where = str(f.get("location") or "").strip()
        n += 1
        fid = f"OV{n:03d}"
        have = inv.get(kind, 0)
        if kind in _COUNT_CHECKED:
            ov_counts[kind] = ov_counts.get(kind, 0) + count

        if kind == "other":
            item = _reconcile_nonfeature(fid, desc, where, clearly, extraction)
            if item is not None:
                items.append(item)
            # a reconciled non-feature that checks out (None) raises no noise flag
        elif have == 0:
            items.append(_item(
                "CRITICAL" if clearly else "MEDIUM", fid,
                what=(f"The overview clearly shows {count}x {kind} ({desc}"
                      + (f", {where}" if where else "") + ") but the build contains "
                      "no matching feature.") if clearly else
                     (f"The overview POSSIBLY shows {count}x {kind} ({desc}"
                      + (f", {where}" if where else "") + ") with no matching "
                      "feature in the build."),
                decision="feature is absent from the final part",
                why="present in the overview drawing, missing from the extraction/build",
                affects=f"{kind} feature(s) — verify against the drawing",
            ))
        # have>0: per-group count is NOT compared to the total here (see the
        # aggregate check below), so a valid multi-group callout raises no flag.

    # Aggregate shortfall check: only when the overview's TOTAL for a kind exceeds
    # the build's total (build is genuinely missing some), and only for kinds that
    # still have at least one built feature (a total absence was already flagged
    # CRITICAL per-callout above).
    for kind, ov_total in sorted(ov_counts.items()):
        have = inv.get(kind, 0)
        if have > 0 and ov_total > have:
            n += 1
            items.append(_item(
                "HIGH", f"OV{n:03d}",
                what=f"Count shortfall for {kind}: the overview's callouts total "
                     f"{ov_total}, but the build contains {have}.",
                decision=f"build kept its extracted total of {have}",
                why="the summed overview callout counts exceed the built total — a "
                    "group of this feature may be missing (per-group counts that sum "
                    "to the build total are treated as consistent and NOT flagged)",
                affects=f"{kind} total count",
            ))
    items.extend(_envelope_items(overview.get("envelope") or {}, extraction))
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    items.sort(key=lambda it: order.get(it["severity"], 4))
    return items


def run_overview_check(
    overview_image: Path,
    extraction: dict,
    cache_dir: Optional[Path] = None,
    usage_out: Optional[dict[str, int]] = None,
    page: int = 1,
) -> tuple[list[dict[str, Any]], str]:
    """The exception-safe wrapper the pipeline calls.

    Returns ``(items, note)``. ``items`` is empty and ``note`` explains why when
    the check could not run (no image / no key / API failure) — the pipeline
    proceeds either way; only a successful check with CRITICAL findings gates.
    """
    try:
        if overview_image is None or not Path(overview_image).is_file():
            return [], "skipped: no overview image for this part"
        from utils.image_prep import prepare_image

        prepared = prepare_image(str(overview_image), page=page, return_details=True)
        overview = extract_overview_features(
            prepared.base64, media_type=prepared.media_type,
            cache_dir=cache_dir, usage_out=usage_out,
        )
        items = cross_check(overview, extraction)
        n_feat = len(overview.get("features") or [])
        return items, (f"checked {n_feat} overview feature(s): "
                       f"{len(items)} finding(s)")
    except EnvironmentError as e:
        return [], f"skipped: {e}"
    except Exception as e:  # never sink a build over the cross-check
        log.warning("Overview check failed (non-fatal): %s", e)
        return [], f"skipped: {type(e).__name__}: {e}"

# =========================================================================== #
# PART B — the Stage 1.5 overview WORDS vs the generated macro package
# =========================================================================== #
REPORT_SUFFIX = "_macro_overview_validation.json"

# Canonical-word map: overview prose and step descriptions meet on these stems.
# Values are the canonical token; keys are the drawing/extraction spellings.
_SYNONYMS: dict[str, str] = {
    "bore": "hole", "bores": "hole", "holes": "hole", "hls": "hole",
    "drill": "hole", "drilled": "hole", "drilling": "hole",
    "tap": "thread", "taps": "thread", "tapped": "thread", "thd": "thread",
    "threads": "thread", "threaded": "thread",
    "cbore": "counterbore", "counterbored": "counterbore",
    "csk": "countersink", "countersunk": "countersink",
    "notch": "slot", "notches": "slot", "slots": "slot", "cutout": "slot",
    "cutouts": "slot", "keyway": "slot",
    "bosses": "boss",
    "chamfers": "chamfer", "fillets": "fillet", "radii": "fillet",
    "thru": "through",
    "patterns": "pattern", "bolts": "bolt",
    "tabs": "tab",
}

# Words that carry no feature identity (view names, articles, drafting boilerplate).
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "for", "with", "and", "or", "to",
    "is", "are", "as", "by", "its", "it", "this", "that", "vs", "not", "same",
    "view", "views", "front", "side", "top", "bottom", "left", "right",
    "section", "detail", "profile", "drawing", "sheet", "line", "lines",
    "hidden", "visible", "shown", "seen", "corresponds", "corresponding",
    "confirms", "confirmed", "matching", "matches", "feature", "features",
    "all", "one", "two", "three", "four", "five", "six", "dia", "diameter",
})

# Overview correspondences that describe sheet furniture, not part geometry —
# there is legitimately no build step for these.
_NON_GEOMETRY_TOKENS = frozenset({
    "title", "block", "border", "note", "notes", "finish", "inspection",
    "balloon", "balloons", "revision", "tolerance", "tolerances",
})

# Step types that create (or pattern) part geometry — the match pool.
_GEOMETRY_STEP_TYPES = frozenset({
    "extrude_boss", "extrude_cut", "hole", "thread", "revolve", "mirror",
    "pattern", "slot_rect_cut", "slot_corner_fillet", "circular_pattern",
    "fillet", "chamfer", "fillet/chamfer",
})

# Note words that mean the count refers to drilled/tapped features.
_HOLE_COUNT_TOKENS = frozenset({"hole", "thread", "counterbore", "countersink"})


class OverviewMacroValidationError(Exception):
    """Strict mode: the macro package contradicts the overview analysis."""


@dataclass
class ValidationEntry:
    check: str          # note_count | correspondence_coverage | through_blind |
                        # conflict_carryover | symmetry_advisory
    status: str         # PASS | WARN | FAIL
    severity: str       # CRITICAL | HIGH | MEDIUM | LOW
    subject: str        # the overview words being checked (note text / feature name)
    detail: str
    matched_feature_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check, "status": self.status, "severity": self.severity,
            "subject": self.subject, "detail": self.detail,
            "matched_feature_ids": self.matched_feature_ids,
        }


@dataclass
class OverviewMacroReport:
    entries: list[ValidationEntry] = field(default_factory=list)
    planned_hole_instances: int = 0

    @property
    def ok(self) -> bool:
        return not any(e.status == "FAIL" for e in self.entries)

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for e in self.entries:
            out[e.status] = out.get(e.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "planned_hole_instances": self.planned_hole_instances,
            "entries": [e.to_dict() for e in self.entries],
        }

    def review_items(self) -> list[dict[str, Any]]:
        """FAIL findings as engineering-review items (same dict shape as
        :func:`pipeline.engineering_review.build_review_items` emits)."""
        items: list[dict[str, Any]] = []
        for e in self.entries:
            if e.status != "FAIL":
                continue
            items.append({
                "severity": e.severity,
                "source": "overview_macro_validation",
                "id": ",".join(e.matched_feature_ids) or "-",
                "what": f"Macro package disagrees with the overview analysis "
                        f"({e.check}): {e.subject}",
                "decision": "macros were generated as planned; the disagreement is "
                            "flagged, not silently reconciled",
                "why": e.detail,
                "affects": ", ".join(e.matched_feature_ids) or "whole part",
            })
        return items


# --------------------------------------------------------------------------- #
# Word canonicalization
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> set[str]:
    """Lowercased canonical word set of a phrase (synonyms applied, stopwords
    and bare numbers dropped)."""
    out: set[str] = set()
    for raw in re.split(r"[^a-z0-9']+", (text or "").lower()):
        if not raw or raw.isdigit():
            continue
        word = _SYNONYMS.get(raw, raw)
        if word in _STOPWORDS or len(word) < 2:
            continue
        out.add(word)
    return out


def _step_tokens(step) -> set[str]:
    toks = _tokens(f"{step.description} {step.feature_type} {step.notes}")
    toks |= _tokens(step.feature_id.replace("_", " "))
    toks.add(_SYNONYMS.get(step.feature_type, step.feature_type))
    return toks


def _geometry_steps(pkg) -> list[Any]:
    return [s for s in pkg.steps
            if s.feature_type in _GEOMETRY_STEP_TYPES
            and s.status in ("generated", "needs_review")]


def _planned_hole_instances(pkg) -> int:
    """Hole instances the package actually drills: baked circles on hole/thread
    steps plus circular-pattern copies (total_instances INCLUDES the seed, and
    the seed is its own hole step — count total-1 for the pattern)."""
    total = 0
    for s in pkg.steps:
        if s.status not in ("generated", "needs_review"):
            continue
        if s.feature_type in ("hole", "thread"):
            total += max(1, len(s.positions_xy))
        elif s.feature_type == "circular_pattern":
            n = int((s.circular_pattern or {}).get("total_instances", 0) or 0)
            total += max(0, n - 1)
    return total


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_hole_counts(overview: dict, pkg, report: OverviewMacroReport) -> None:
    planned = report.planned_hole_instances
    for note in overview.get("global_notes") or []:
        count = note.get("resolved_count")
        if not count:
            continue
        if "inspection" in (note.get("applies_to") or "").lower():
            continue
        note_toks = _tokens(f"{note.get('note', '')} {note.get('applies_to', '')}")
        if not (note_toks & _HOLE_COUNT_TOKENS):
            continue  # a count of something the macros don't enumerate (e.g. views)
        subject = note.get("note", "")
        if planned == count:
            report.entries.append(ValidationEntry(
                "note_count", "PASS", "LOW", subject,
                f"drawing note states {count} hole feature(s); the macros drill "
                f"exactly {planned} instance(s)."))
        elif planned < count:
            report.entries.append(ValidationEntry(
                "note_count", "FAIL", "CRITICAL", subject,
                f"drawing note states {count} hole feature(s) but the macros only "
                f"drill {planned} instance(s) — {count - planned} instance(s) "
                f"missing from the build."))
        else:
            report.entries.append(ValidationEntry(
                "note_count", "WARN", "MEDIUM", subject,
                f"the macros drill {planned} hole instance(s), more than the "
                f"note's {count} — the note may govern only a subset "
                f"(one diameter/thread) of the part's holes."))


def check_correspondences(overview: dict, pkg,
                           report: OverviewMacroReport) -> list[tuple[dict, list]]:
    """Coverage check. Returns (correspondence, matched_steps) pairs for the
    through/blind check to reuse."""
    steps = _geometry_steps(pkg)
    step_tok = [(s, _step_tokens(s)) for s in steps]
    matched_pairs: list[tuple[dict, list]] = []

    for corr in overview.get("cross_view_correspondences") or []:
        name = corr.get("feature", "")
        toks = _tokens(name.replace("_", " "))
        if not toks or toks <= _NON_GEOMETRY_TOKENS:
            continue
        hits = [s for s, st in step_tok if toks & st]
        confidence = (corr.get("confidence") or "medium").lower()
        if hits:
            matched_pairs.append((corr, hits))
            report.entries.append(ValidationEntry(
                "correspondence_coverage", "PASS", "LOW", name,
                f"overview feature matches {len(hits)} build step(s).",
                matched_feature_ids=sorted({s.feature_id for s in hits})))
        elif confidence == "high":
            report.entries.append(ValidationEntry(
                "correspondence_coverage", "FAIL", "HIGH", name,
                f"the overview analysis saw this feature across views "
                f"({', '.join(corr.get('seen_in') or []) or 'unspecified'}; "
                f"relation: {corr.get('relation', '')[:160]}) with HIGH confidence, "
                f"but no generated build step matches its words."))
        else:
            report.entries.append(ValidationEntry(
                "correspondence_coverage", "WARN",
                "MEDIUM" if confidence == "medium" else "LOW", name,
                f"no build step matches this {confidence}-confidence overview "
                f"feature — verify it is either built under another name or "
                f"genuinely not part geometry."))
    return matched_pairs


def check_through_vs_blind(matched_pairs: list[tuple[dict, list]],
                         report: OverviewMacroReport) -> None:
    for corr, hits in matched_pairs:
        # Overview prose confirms one reading by negating the other ("a THROUGH
        # bore, not a blind hole") — drop the negated mention before deciding.
        rel_text = re.sub(r"\bnot\s+(?:a\s+|an\s+)?(?:blind|through|thru)\b", " ",
                          (corr.get("relation") or "").lower())
        relation = _tokens(rel_text)
        says_through = "through" in relation
        says_blind = "blind" in relation
        if says_through == says_blind:  # neither, or contradictory prose — skip
            continue
        expected = "through_all" if says_through else "blind"
        typed = [s for s in hits if s.depth_type in ("blind", "through_all")]
        if not typed:
            continue
        wrong = [s for s in typed if s.depth_type != expected]
        name = corr.get("feature", "")
        if not wrong:
            report.entries.append(ValidationEntry(
                "through_blind", "PASS", "LOW", name,
                f"overview confirms {expected.replace('_all', '')}; every matched "
                f"step agrees.",
                matched_feature_ids=sorted({s.feature_id for s in typed})))
        elif len(wrong) == len(typed):
            report.entries.append(ValidationEntry(
                "through_blind", "FAIL", "CRITICAL", name,
                f"the overview's cross-view read confirms a "
                f"{expected.replace('_all', '')} feature "
                f"({corr.get('relation', '')[:160]}), but the matched step(s) are "
                f"built {wrong[0].depth_type} — building from one view alone "
                f"would produce a wrong part.",
                matched_feature_ids=sorted({s.feature_id for s in wrong})))
        else:
            report.entries.append(ValidationEntry(
                "through_blind", "WARN", "MEDIUM", name,
                f"overview confirms {expected.replace('_all', '')}; the matched "
                f"steps disagree among themselves — the words may span several "
                f"distinct features.",
                matched_feature_ids=sorted({s.feature_id for s in wrong})))


def check_conflict_carryover(overview: dict, report: OverviewMacroReport) -> None:
    for c in overview.get("cross_view_conflicts") or []:
        sev = (c.get("severity") or "MEDIUM").upper()
        if sev not in ("CRITICAL", "HIGH"):
            continue
        report.entries.append(ValidationEntry(
            "conflict_carryover", "WARN", sev, c.get("description", "")[:160],
            "unreconciled overview conflict still open at macro time — "
            + (c.get("recommendation") or "verify against the drawing.")))


def check_symmetry_advisory(overview: dict, pkg, report: OverviewMacroReport) -> None:
    sym = (overview.get("symmetry") or {})
    if sym.get("type") not in ("rotational", "both"):
        return
    has_pattern = any(
        s.feature_type in ("circular_pattern", "pattern") or s.placement == "pattern"
        for s in pkg.steps)
    multi_hole = any(s.feature_type in ("hole", "thread") and len(s.positions_xy) >= 3
                     for s in pkg.steps)
    if not has_pattern and multi_hole:
        report.entries.append(ValidationEntry(
            "symmetry_advisory", "WARN", "LOW", f"symmetry: {sym.get('type')}",
            "the overview reports rotational symmetry but no pattern step exists — "
            "individually-placed holes are the safe default (X/Y dimensioning is "
            "evidence against a polar pattern); flagged for awareness only. "
            + (sym.get("notes") or "")))


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def validate_macros_against_overview(model, pkg, overview: dict) -> OverviewMacroReport:
    """Pure validation of a generated :class:`MacroPackage` against the Stage 1.5
    ``overview_analysis`` dict. Deterministic; no I/O."""
    report = OverviewMacroReport(planned_hole_instances=_planned_hole_instances(pkg))
    check_hole_counts(overview, pkg, report)
    matched = check_correspondences(overview, pkg, report)
    check_through_vs_blind(matched, report)
    check_conflict_carryover(overview, report)
    check_symmetry_advisory(overview, pkg, report)
    return report


def run_overview_macro_validation(
    model, pkg, overview: Optional[dict] = None, write: bool = True,
) -> Optional[OverviewMacroReport]:
    """Load ``overview_analysis.json`` from the package root (unless the dict is
    passed in), validate, persist ``<Part>_macro_overview_validation.json``, and
    log findings. Returns ``None`` when no overview analysis exists — the stage
    is additive and never breaks a run."""
    if overview is None:
        path = Path(pkg.root) / "overview_analysis.json"
        if not path.is_file():
            return None
        try:
            overview = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("overview macro validation: unreadable %s (%s) — skipped.",
                        path, e)
            return None
    report = validate_macros_against_overview(model, pkg, overview)
    if write:
        name = Path(pkg.build_plan_json).name.replace("_build_plan.json", "")
        out = Path(pkg.root) / f"{name}{REPORT_SUFFIX}"
        out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    counts = report.counts()
    for e in report.entries:
        if e.status == "FAIL":
            log.warning("overview macro validation FAIL [%s] %s: %s",
                        e.check, e.subject, e.detail)
        elif e.status == "WARN":
            log.info("overview macro validation WARN [%s] %s", e.check, e.subject)
    log.info("overview macro validation: %d PASS / %d WARN / %d FAIL "
             "(%d planned hole instance(s))",
             counts["PASS"], counts["WARN"], counts["FAIL"],
             report.planned_hole_instances)
    return report


def assert_overview_macro_validation(model, pkg,
                                     overview: Optional[dict] = None) -> OverviewMacroReport:
    """Strict variant: raise :class:`OverviewMacroValidationError` on any FAIL."""
    report = run_overview_macro_validation(model, pkg, overview)
    if report is not None and not report.ok:
        fails = [e for e in report.entries if e.status == "FAIL"]
        detail = "; ".join(f"[{e.check}] {e.subject}: {e.detail}" for e in fails[:10])
        raise OverviewMacroValidationError(
            f"Macro package contradicts the overview analysis "
            f"({len(fails)} failure(s)): {detail}")
    return report

# =========================================================================== #
# Compatibility aliases
# =========================================================================== #
# `cross_check` is the name every existing caller/test uses for Part A's diff;
# `check_missing_features` is the descriptive name in the merged module's
# sub-check family. Same function, both names supported.
check_missing_features = cross_check
