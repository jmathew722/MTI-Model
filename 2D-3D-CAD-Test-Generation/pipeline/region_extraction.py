"""Unconditional fixed-region high-resolution extraction pass (2026-07-24).

Every drawing gets a SECOND extraction pass over fixed, overlapping regions of
the page — ALWAYS, on every drawing, with no confidence trigger. The overview
pass (the existing full-page extraction) resizes a D-size sheet ~7x down, so a
3 mm callout becomes ~5 px in what the model sees; the region pass re-reads
each tile at near-native resolution and reconciles it against the overview. The
earlier "confidence-triggered zoom" design was rejected: gating the high-res
read on Claude's own uncertainty means a confidently-wrong field never gets a
second look — a silent-skip failure. Here the region pass runs regardless;
confidence is used only AFTER, to decide whether a merged field needs a human.

Stages (see docs + the task spec):
  A  render_master        — retain the high-DPI master raster on disk.
  B  write_sent_copy       — the downsampled copy the overview pass sees.
  C  regions_for_master    — compute_regions(): overlapping tiles, count scales
                             with sheet size.
  D  run_region_pass       — crop + resize + extract EVERY region (no skip);
                             the API call is INJECTED (``extract_fn``) so the
                             machinery is unit-tested without paid calls.
  E  merge_fields          — reconcile every field; assert every overview field
                             is accounted for (agreed / region_override /
                             overview_kept / conflict_escalated / coverage_gap /
                             region_added). A missing account HALTS the drawing.

Image geometry lives in :mod:`pipeline.image_coordinates` (pixel space). This
module never touches :mod:`pipeline.coordinate_normalize` (3D model space).

Public entry point: :func:`run_region_extraction`.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.highres_pass import (
    AGREED,
    ALWAYS,
    A_WINS,
    B_WINS,
    CONFLICT,
    best_by_rank,
    evaluate_trigger,
    rank_confidence,
    reconcile_pair,
)
from pipeline.highres_pass import values_agree as _hp_values_agree
from pipeline.image_coordinates import (
    DEFAULT_OVERLAP_FRAC,
    DEFAULT_TARGET_EDGE_PX,
    Region,
    compute_regions,
    coverage_gap_pixels,
    master_to_sent_scale,
    render_region_overlay,
    resized_size,
    tier_limits,
)
from utils.logger import get_logger

log = get_logger()

MASTER_DPI = 300              # Stage A minimum; 400 for thin line weights
MASTER_DPI_THIN = 400
REGIONS_DIRNAME = "regions"
MANIFEST_NAME = "manifest.json"
MERGE_LOG_NAME = "merge_log.json"

# Merge resolutions — every overview field ends as exactly one of these.
R_AGREED = "agreed"
R_REGION_OVERRIDE = "region_override"
R_OVERVIEW_KEPT = "overview_kept"
R_CONFLICT = "conflict_escalated"
R_COVERAGE_GAP = "coverage_gap"
R_REGION_ADDED = "region_added"

_CONF_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class RegionMergeError(Exception):
    """Stage-E integrity failure: an overview field has no merge-log entry.
    This is a bug in the tiling/merge, not a data issue — halt the drawing."""


# --------------------------------------------------------------------------- #
# Stage A — high-DPI master raster (retained on disk)
# --------------------------------------------------------------------------- #
@dataclass
class MasterRaster:
    image: Any                 # PIL.Image (RGB)
    width_px: int
    height_px: int
    page: int
    dpi: int
    master_png_path: Optional[Path] = None


def render_master(source_path: Path, page: int, out_dir: Path, part: str,
                  dpi: int = MASTER_DPI) -> MasterRaster:
    """Rasterize one page of a PDF (or load an image) at high DPI and RETAIN it
    on disk as ``<part>_page{NN}_master.png``. Every region crop comes from this
    master, never from the downsampled sent copy."""
    from PIL import Image, ImageOps

    source_path = Path(source_path)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        img = _render_pdf_page(source_path, page, dpi)
    else:
        img = ImageOps.exif_transpose(Image.open(source_path))
        page = 1
    img = img.convert("RGB")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    master_path = out_dir / f"{part}_page{page:02d}_master.png"
    img.save(master_path, format="PNG")
    log.info("region-pass Stage A: master raster %dx%d px @ %d DPI -> %s",
             img.width, img.height, dpi, master_path.name)
    return MasterRaster(image=img, width_px=img.width, height_px=img.height,
                        page=page, dpi=dpi, master_png_path=master_path)


def _render_pdf_page(pdf_path: Path, page: int, dpi: int):
    from PIL import Image
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        try:
            if page < 1 or page > doc.page_count:
                raise ValueError(f"page {page} out of range (doc has {doc.page_count})")
            zoom = dpi / 72.0
            pix = doc.load_page(page - 1).get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            return Image.open(io.BytesIO(pix.tobytes("png")))
        finally:
            doc.close()
    except Exception:
        from pdf2image import convert_from_path
        pages = convert_from_path(str(pdf_path), dpi=dpi, first_page=page, last_page=page)
        if not pages:
            raise
        return pages[0]


# --------------------------------------------------------------------------- #
# Stage B — the downsampled copy the overview pass sees (records the scale)
# --------------------------------------------------------------------------- #
@dataclass
class SentCopy:
    sent_png_path: Path
    width_px: int
    height_px: int
    scale_master_to_sent: float   # sent_px = master_px * scale


def write_sent_copy(master: MasterRaster, out_dir: Path, part: str,
                    tier: str = "standard") -> SentCopy:
    max_edge, max_tokens = tier_limits(tier)
    sent_w, sent_h = resized_size(master.width_px, master.height_px, max_edge, max_tokens)
    sent_img = master.image.resize((sent_w, sent_h))
    out_dir = Path(out_dir)
    sent_path = out_dir / f"{part}_page{master.page:02d}_sent.png"
    sent_img.save(sent_path, format="PNG")
    scale = master_to_sent_scale(master.width_px, sent_w)
    log.info("region-pass Stage B: sent copy %dx%d px (scale %.4f) -> %s",
             sent_w, sent_h, scale, sent_path.name)
    return SentCopy(sent_png_path=sent_path, width_px=sent_w, height_px=sent_h,
                    scale_master_to_sent=scale)


# --------------------------------------------------------------------------- #
# Stage C — regions
# --------------------------------------------------------------------------- #
def regions_for_master(master: MasterRaster,
                       target_edge_px: int = DEFAULT_TARGET_EDGE_PX,
                       overlap_frac: float = DEFAULT_OVERLAP_FRAC) -> list[Region]:
    regions = compute_regions(master.width_px, master.height_px,
                              target_edge_px=target_edge_px,
                              overlap_frac=overlap_frac, page=master.page)
    gap = coverage_gap_pixels(master.width_px, master.height_px, regions)
    if gap != 0:  # a bug in compute_regions, not a tuning question
        raise RegionMergeError(
            f"region tiling left {gap} master px uncovered — compute_regions bug.")
    log.info("region-pass Stage C: %d region(s) cover %dx%d master (0 gap px)",
             len(regions), master.width_px, master.height_px)
    return regions


def crop_region_b64(master: MasterRaster, region: Region,
                    tier: str = "standard") -> tuple[str, Any]:
    """Crop the region from the MASTER (native res), resize it for the API, and
    return (base64_png, PIL crop). The crop is near-native because the tile is
    already ~target_edge_px."""
    crop = master.image.crop(tuple(region.bbox_master_px))
    max_edge, max_tokens = tier_limits(tier)
    rw, rh = resized_size(crop.width, crop.height, max_edge, max_tokens)
    if (rw, rh) != (crop.width, crop.height):
        crop_sent = crop.resize((rw, rh))
    else:
        crop_sent = crop
    buf = io.BytesIO()
    crop_sent.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8"), crop


# --------------------------------------------------------------------------- #
# Overview flattening — DrawingData -> comparable field records
# --------------------------------------------------------------------------- #
def _overview_confidence(overall: float, unclear: bool) -> str:
    if unclear:
        return "LOW"
    if overall >= 0.85:
        return "HIGH"
    if overall >= 0.70:
        return "MEDIUM"
    return "LOW"


def apply_resolved(overview: dict, resolved: dict[str, Any]) -> int:
    """Write the merge's resolved values back onto the overview DrawingData dict,
    IN PLACE, so region-pass corrections flow into Stage 2.5 and the build. The
    inverse of :func:`flatten_overview`: only the known, safely-mappable paths
    are applied (``dimensions.<id>.value``, ``hole_callouts.<id>.qty|diameter``);
    region-only additions to unknown paths are left for review rather than
    forced into the strict schema. Returns the number of fields changed — 0 when
    every field agreed, so an all-agree drawing's extraction (and its build
    plan) is untouched."""
    dims = {d.get("id"): d for d in overview.get("dimensions", []) or []}
    holes = {h.get("id"): h for h in overview.get("hole_callouts", []) or []}
    changed = 0
    for fp, val in resolved.items():
        parts = fp.split(".")
        if len(parts) == 3 and parts[0] == "dimensions" and parts[2] == "value":
            d = dims.get(parts[1])
            if d is not None and d.get("value") != val:
                d["value"] = val
                changed += 1
        elif len(parts) == 3 and parts[0] == "hole_callouts" and parts[2] in ("qty", "diameter"):
            h = holes.get(parts[1])
            if h is not None and h.get(parts[2]) != val:
                h[parts[2]] = val
                changed += 1
    return changed


def flatten_overview(overview: dict) -> list[dict]:
    """DrawingData dict -> a flat list of comparable field records
    ``{field_path, value, confidence}`` (the space the region pass reconciles
    against). Dimensions, hole diameters, and hole counts are the fields the
    high-res pass most often corrects; the same field_path convention is asked
    of the region extractor so the merge matches by string."""
    if not overview:
        return []
    overall = float(overview.get("confidence", 0.0) or 0.0)
    out: list[dict] = []
    for d in overview.get("dimensions", []) or []:
        did = d.get("id")
        if not did:
            continue
        unclear = bool(d.get("value_unclear") or d.get("resolution_required"))
        out.append({"field_path": f"dimensions.{did}.value",
                    "value": d.get("value"),
                    "confidence": _overview_confidence(overall, unclear)})
    for h in overview.get("hole_callouts", []) or []:
        hid = h.get("id")
        if not hid:
            continue
        if h.get("qty"):
            out.append({"field_path": f"hole_callouts.{hid}.qty", "value": h.get("qty"),
                        "confidence": _overview_confidence(overall, False)})
        if h.get("diameter"):
            out.append({"field_path": f"hole_callouts.{hid}.diameter",
                        "value": h.get("diameter"),
                        "confidence": _overview_confidence(overall, False)})
    return out


# --------------------------------------------------------------------------- #
# Stage D — extract EVERY region, unconditionally (API call injected)
# --------------------------------------------------------------------------- #
RegionExtractFn = Callable[[str, str, str, dict], list[dict]]
"""(image_b64, media_type, region_id, overview_json) -> list of field records."""


@dataclass
class RegionPassResult:
    fields: list[dict] = field(default_factory=list)   # merged field records
    per_region: dict[str, list[dict]] = field(default_factory=dict)
    crops: dict[str, Any] = field(default_factory=dict)   # region_id -> PIL crop
    api_calls: int = 0


def run_region_pass(master: MasterRaster, regions: list[Region], overview: dict,
                    extract_fn: RegionExtractFn, tier: str = "standard",
                    usage_out: Optional[dict] = None) -> RegionPassResult:
    """Stage D: extract EVERY region. No confidence gate, no density heuristic,
    no skip — every region gets a real ``extract_fn`` call. Returns the collected
    field records, per-region records, the crops, and the API-call count (which
    callers assert is > 0 for every drawing — the no-silent-skip guarantee)."""
    result = RegionPassResult()
    for region in regions:
        b64, crop = crop_region_b64(master, region, tier)
        result.crops[region.id] = crop
        recs = extract_fn(b64, "image/png", region.id, overview) or []
        result.api_calls += 1                       # counted even if recs == []
        for rec in recs:
            rec.setdefault("source", "region_pass")
            rec["region_id"] = region.id
        result.per_region[region.id] = recs
        result.fields.extend(recs)
    log.info("region-pass Stage D: %d region(s) extracted, %d API call(s), %d field(s)",
             len(regions), result.api_calls, len(result.fields))
    return result


# --------------------------------------------------------------------------- #
# Stage E — merge every field; account for every overview field or HALT
# --------------------------------------------------------------------------- #
# The value-comparison and winner rules are the shared ones in
# pipeline.highres_pass (REFACTOR_ANALYSIS §1.4) — the same decisions the tiled
# zoom pass makes. Only what to DO with each outcome is this module's own.
_values_agree = _hp_values_agree


def _best_conf(*confs: str) -> str:
    return max(confs, key=lambda c: _CONF_RANK.get(str(c).upper(), 0))


@dataclass
class MergeOutcome:
    merge_log: list[dict] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)   # field_path -> final value
    review_queue: list[dict] = field(default_factory=list)
    needs_review: bool = False


def merge_fields(overview_fields: list[dict], region_fields: list[dict],
                 regions_attempted: int = 1) -> MergeOutcome:
    """Reconcile the overview and region readings field-by-field, logging EVERY
    field regardless of outcome, then assert every overview field is accounted
    for. Rules:

      * agree -> keep either (confidence = max).
      * disagree, one side strictly higher confidence -> that side wins
        (region_override / overview_kept), logged with both values.
      * disagree, equal confidence (incl. both HIGH) -> conflict_escalated to
        review with both readings; NEVER auto-tie-broken.
      * overview field a region ATTEMPTED but did not re-read -> overview_kept
        (uncorroborated; keep the overview value, NOT a review item — the
        region simply had nothing to add there).
      * overview field NO region even attempted (``regions_attempted == 0`` —
        the region pass didn't run over its area) -> coverage_gap -> review.
        Given full-coverage tiling this cannot happen unless the pass was
        skipped, which is the exact silent-skip failure this guard catches.
      * region-only field -> region_added (the low-res overview missed it).

    Raises :class:`RegionMergeError` if any overview field_path lacks a
    merge-log entry (a tiling/merge bug — the drawing halts rather than letting
    an unaccounted field slip into the build plan)."""
    out = MergeOutcome()
    # Best region reading per field_path (highest confidence wins the slot).
    region_by_path: dict[str, dict] = best_by_rank(
        region_fields,
        key_of=lambda r: r.get("field_path"),
        rank_of=lambda r: rank_confidence(r.get("confidence")),
    )
    ov_by_path = {o["field_path"]: o for o in overview_fields if o.get("field_path")}

    for fp, ov in ov_by_path.items():
        rg = region_by_path.get(fp)
        entry: dict[str, Any] = {"field_path": fp, "overview_value": ov.get("value"),
                                 "overview_confidence": ov.get("confidence")}
        if rg is None:
            if regions_attempted <= 0:
                # The region pass never ran over this field's area — the exact
                # silent-skip this design forbids. Route to review, unresolved.
                entry.update(resolution=R_COVERAGE_GAP, final_value=ov.get("value"))
                out.review_queue.append({"field_path": fp, "reason": R_COVERAGE_GAP,
                                         "overview_value": ov.get("value")})
                out.needs_review = True
            else:
                # Attempted but not re-read: uncorroborated — keep the overview
                # value, do not force review (the region had nothing to add).
                entry.update(resolution=R_OVERVIEW_KEPT, final_value=ov.get("value"))
                out.resolved[fp] = ov.get("value")
            out.merge_log.append(entry)
            continue
        entry.update(region_id=rg.get("region_id"), region_value=rg.get("value"),
                     region_confidence=rg.get("confidence"))
        verdict = reconcile_pair(
            ov.get("value"), rg.get("value"),
            a_rank=rank_confidence(ov.get("confidence")),
            b_rank=rank_confidence(rg.get("confidence")),
        )
        if verdict == AGREED:
            entry.update(resolution=R_AGREED, final_value=ov.get("value"))
            out.resolved[fp] = ov.get("value")
        elif verdict == B_WINS:
            entry.update(resolution=R_REGION_OVERRIDE, final_value=rg.get("value"))
            out.resolved[fp] = rg.get("value")
        elif verdict == A_WINS:
            entry.update(resolution=R_OVERVIEW_KEPT, final_value=ov.get("value"))
            out.resolved[fp] = ov.get("value")
        else:
            # Equal confidence disagreement (incl. both HIGH): genuine conflict
            # (highres_pass.CONFLICT) — never auto-tie-broken, always reviewed.
            assert verdict == CONFLICT
            entry.update(resolution=R_CONFLICT, final_value=None)
            out.review_queue.append({
                "field_path": fp, "reason": R_CONFLICT,
                "overview_value": ov.get("value"), "region_value": rg.get("value"),
                "region_id": rg.get("region_id")})
            out.needs_review = True
        out.merge_log.append(entry)

    # Region-only additions (the overview missed them entirely).
    for fp, rg in region_by_path.items():
        if fp in ov_by_path:
            continue
        out.merge_log.append({"field_path": fp, "overview_value": None,
                              "region_id": rg.get("region_id"),
                              "region_value": rg.get("value"),
                              "region_confidence": rg.get("confidence"),
                              "resolution": R_REGION_ADDED, "final_value": rg.get("value")})
        out.resolved[fp] = rg.get("value")

    # THE assertion: every overview field must be accounted for in the log.
    logged = {e["field_path"] for e in out.merge_log}
    missing = [fp for fp in ov_by_path if fp not in logged]
    if missing:
        raise RegionMergeError(
            f"{len(missing)} overview field(s) have no merge-log entry: "
            f"{missing[:8]} — halting the drawing to review rather than passing "
            f"unaccounted fields into the build plan.")
    return out


# --------------------------------------------------------------------------- #
# Output writer + manifest + merge log (--keep-regions)
# --------------------------------------------------------------------------- #
def write_region_outputs(out_dir: Path, part: str, master: MasterRaster,
                         regions: list[Region], overlap_frac: float,
                         rp: RegionPassResult, merge: MergeOutcome,
                         keep_regions: str = "all") -> Path:
    """Write regions/ (crops + sidecar extractions), manifest.json, and
    merge_log.json. ``keep_regions``: all | conflicts-only | none."""
    out_dir = Path(out_dir)
    regions_dir = out_dir / REGIONS_DIRNAME
    regions_dir.mkdir(parents=True, exist_ok=True)

    conflict_ids = {q.get("region_id") for q in merge.review_queue if q.get("region_id")}
    manifest = {
        "page": master.page,
        "master_dims": [master.width_px, master.height_px],
        "region_count": len(regions),
        "overlap_frac": overlap_frac,
        "regions": [{"id": r.id, "bbox_master_px": r.bbox_master_px} for r in regions],
    }
    (regions_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for region in regions:
        keep_crop = (keep_regions == "all"
                     or (keep_regions == "conflicts-only" and region.id in conflict_ids))
        if keep_crop and region.id in rp.crops:
            rp.crops[region.id].save(regions_dir / f"{region.id}.png", format="PNG")
        # The sidecar extraction is always written (it is the audit trail tying a
        # build-plan field back to a region), even when the crop image is dropped.
        (regions_dir / f"{region.id}_extraction.json").write_text(
            json.dumps(rp.per_region.get(region.id, []), indent=2), encoding="utf-8")

    (out_dir / MERGE_LOG_NAME).write_text(json.dumps(merge.merge_log, indent=2),
                                          encoding="utf-8")
    log.info("region-pass outputs: %d region(s), keep=%s, merge_log=%d field(s)",
             len(regions), keep_regions, len(merge.merge_log))
    return out_dir / MERGE_LOG_NAME


# --------------------------------------------------------------------------- #
# Lessons-learned ledger (per-region + per-drawing summary)
# --------------------------------------------------------------------------- #
def append_ledger(lessons_path: Optional[Path], part: str, master: MasterRaster,
                  regions: list[Region], merge: MergeOutcome, api_calls: int) -> None:
    if lessons_path is None:
        return
    lessons_path = Path(lessons_path)
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overrides = sum(1 for e in merge.merge_log if e["resolution"] == R_REGION_OVERRIDE)
    conflicts = sum(1 for e in merge.merge_log if e["resolution"] == R_CONFLICT)
    gaps = sum(1 for e in merge.merge_log if e["resolution"] == R_COVERAGE_GAP)
    added = sum(1 for e in merge.merge_log if e["resolution"] == R_REGION_ADDED)
    rows: list[dict] = []
    for e in merge.merge_log:
        rows.append({"ts": ts, "kind": "region_field", "part": part,
                     "field_path": e["field_path"], "region_id": e.get("region_id"),
                     "overview_value": e.get("overview_value"),
                     "region_value": e.get("region_value"),
                     "overview_confidence": e.get("overview_confidence"),
                     "region_confidence": e.get("region_confidence"),
                     "resolution": e["resolution"], "final_value": e.get("final_value")})
    rows.append({"ts": ts, "kind": "region_pass_summary", "part": part,
                 "page": master.page, "master_dims": [master.width_px, master.height_px],
                 "region_count": len(regions), "api_calls": api_calls,
                 "overrides": overrides, "conflicts_escalated": conflicts,
                 "coverage_gaps": gaps, "region_added": added,
                 "needs_review": merge.needs_review})
    with lessons_path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_overlay(master: MasterRaster, regions: list[Region], out_path: Path) -> Path:
    """Debug: draw region boundaries + overlap zones on the master raster."""
    overlay = render_region_overlay(master.image, regions)
    out_path = Path(out_path)
    overlay.save(out_path, format="PNG")
    return out_path


# --------------------------------------------------------------------------- #
# Top-level orchestrator: Stage A -> E + outputs + ledger
# --------------------------------------------------------------------------- #
@dataclass
class RegionExtractionResult:
    merge: MergeOutcome
    regions: list[Region]
    api_calls: int
    master: MasterRaster
    sent: Optional[SentCopy]
    merge_log_path: Optional[Path]
    trigger: Optional[dict] = None      # the trigger decision that let this run
    skipped: bool = False               # True when the policy declined to fire


def run_region_extraction(source_path: Path, overview: dict, out_dir: Path, part: str,
                          *, page: int = 1, extract_fn: Optional[RegionExtractFn] = None,
                          tier: str = "standard",
                          target_edge_px: int = DEFAULT_TARGET_EDGE_PX,
                          overlap_frac: float = DEFAULT_OVERLAP_FRAC,
                          keep_regions: str = "all", dpi: int = MASTER_DPI,
                          lessons_path: Optional[Path] = None,
                          usage_out: Optional[dict] = None,
                          overlay: bool = False,
                          trigger_policy: str = ALWAYS) -> RegionExtractionResult:
    """Run the region pass on one page and reconcile it with the overview
    extraction. ``extract_fn`` defaults to the real field-level region extractor
    (:func:`default_region_extract_fn`); inject a fake for tests. Returns the
    merged outcome + artifacts (also written to ``out_dir``).

    ``trigger_policy`` is the pluggable policy from
    :mod:`pipeline.highres_pass` (REFACTOR_ANALYSIS §1.4) and defaults to
    ``ALWAYS`` — the unconditional design is deliberate and unchanged: a
    confidently-wrong field never trips a confidence heuristic, so gating this
    pass on confidence would reintroduce the silent-skip failure it exists to
    prevent. ``ON_CONFIDENCE_HEURISTIC`` (the escalation policy the tiled zoom
    pass uses) and ``NEVER`` are available for callers that want cost over
    coverage; a declined pass returns a result with ``skipped=True`` and the
    reasons recorded, never a silent no-op.
    """
    decision = evaluate_trigger(trigger_policy, extraction=overview)
    if not decision.fire:
        log.info("region pass skipped by trigger policy %s: %s",
                 decision.policy, "; ".join(decision.reasons))
        master = render_master(source_path, page, out_dir, part, dpi=dpi)
        return RegionExtractionResult(
            merge=MergeOutcome(), regions=[], api_calls=0, master=master,
            sent=None, merge_log_path=None, trigger=decision.as_dict(), skipped=True)
    if extract_fn is None:
        extract_fn = default_region_extract_fn(model=None, cache_dir=out_dir /
                                               ".extraction_cache", usage_out=usage_out)
    master = render_master(source_path, page, out_dir, part, dpi=dpi)
    sent = write_sent_copy(master, out_dir, part, tier=tier)
    regions = regions_for_master(master, target_edge_px=target_edge_px,
                                 overlap_frac=overlap_frac)
    rp = run_region_pass(master, regions, overview, extract_fn, tier=tier,
                         usage_out=usage_out)
    merge = merge_fields(flatten_overview(overview), rp.fields,
                         regions_attempted=rp.api_calls)
    merge_log_path = write_region_outputs(out_dir, part, master, regions,
                                          overlap_frac, rp, merge, keep_regions)
    append_ledger(lessons_path, part, master, regions, merge, rp.api_calls)
    if overlay:
        write_overlay(master, regions, out_dir / f"{part}_page{master.page:02d}_overlay.png")
    return RegionExtractionResult(merge=merge, regions=regions, api_calls=rp.api_calls,
                                  master=master, sent=sent, merge_log_path=merge_log_path,
                                  trigger=decision.as_dict())


# --------------------------------------------------------------------------- #
# Default region extractor (real API, field-level schema) — injected in prod
# --------------------------------------------------------------------------- #
_REGION_TOOL = "report_region_fields"
_REGION_SYSTEM = """\
You are re-reading ONE high-resolution REGION crop of an engineering drawing, at
near-native resolution. A separate low-resolution overview pass already read the
whole sheet; its JSON is given to you as context. Report EVERY dimension,
tolerance, callout, and feature mark FULLY visible in THIS region. For anything
the overview pass already read for this area, state agreement or disagreement
explicitly. Use the SAME field_path names the overview uses
(e.g. "dimensions.D004.value", "hole_callouts.H001.qty"). Report by calling the
required tool; never invent a value you cannot read in this crop."""

_REGION_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {},
                    "unit": {"type": "string"},
                    "field_path": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    "agrees_with_overview": {"type": "boolean"},
                    "overview_value": {},
                },
                "required": ["value", "field_path", "confidence"],
            },
        }
    },
    "required": ["fields"],
}


def default_region_extract_fn(model: Optional[str], cache_dir: Optional[Path],
                              usage_out: Optional[dict]) -> RegionExtractFn:
    """A real field-level region extractor (forced tool call, provider-agnostic
    via the shared client). Cached per (region image, model) so re-runs are
    free. Returns a callable matching :data:`RegionExtractFn`."""
    import hashlib
    import os

    from pipeline.extractor import (
        SDK_MAX_RETRIES,
        _accumulate_usage,
        _build_client,
        _cache_lookup,
        _cache_store,
        _image_block,
    )

    def _fn(image_b64: str, media_type: str, region_id: str, overview: dict) -> list[dict]:
        mdl = model or os.getenv("EXTRACTION_MODEL")
        if not mdl:
            from pipeline.ai_provider import default_model
            mdl = default_model()
        key = hashlib.sha256(("region\0" + mdl + "\0" + region_id + "\0"
                              + image_b64).encode("utf-8")).hexdigest()
        cached = _cache_lookup(cache_dir, key)
        if cached is not None:
            return cached.get("fields", [])
        client = _build_client(SDK_MAX_RETRIES)
        ov_ctx = json.dumps({"dimensions": overview.get("dimensions", []),
                             "hole_callouts": overview.get("hole_callouts", [])})[:6000]
        resp = client.messages.create(
            model=mdl, max_tokens=4000, system=_REGION_SYSTEM,
            messages=[{"role": "user", "content": [
                _image_block(image_b64, media_type),
                {"type": "text", "text": f"Region {region_id}. Overview context (JSON):\n"
                                         f"{ov_ctx}\n\nRead this region and call the tool."},
            ]}],
            tools=[{"name": _REGION_TOOL,
                    "description": "Report the fields read in this region.",
                    "input_schema": _REGION_TOOL_SCHEMA}],
            tool_choice={"type": "tool", "name": _REGION_TOOL})
        if usage_out is not None:
            _accumulate_usage(usage_out, resp)
        fields: list[dict] = []
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use" and b.name == _REGION_TOOL:
                fields = list((b.input or {}).get("fields") or [])
                break
        _cache_store(cache_dir, key, {"fields": fields})
        return fields

    return _fn
