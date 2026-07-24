"""Unconditional fixed-region extraction pass (pipeline/region_extraction.py).

Exercises the whole subsystem WITHOUT the API (a fake extract_fn) and WITHOUT a
PDF (a synthetic PNG master): the region pass runs on every drawing regardless
of confidence (call-count > 0), the merge accounts for every overview field
(the Stage-E assertion), the merge rules resolve/override/escalate correctly
(incl. the A050211E 5-vs-6 hole-count case), and the output writer + ledger +
overlay produce the specified artifacts. All pure/local.
"""
import json

import pytest
from PIL import Image

from pipeline.region_extraction import (
    R_AGREED,
    R_CONFLICT,
    R_COVERAGE_GAP,
    R_OVERVIEW_KEPT,
    R_REGION_ADDED,
    R_REGION_OVERRIDE,
    RegionMergeError,
    append_ledger,
    default_region_extract_fn,
    flatten_overview,
    merge_fields,
    run_region_extraction,
)


def _master_png(tmp_path, w=3000, h=2000):
    p = tmp_path / "src.png"
    Image.new("RGB", (w, h), "white").save(p)
    return p


def _overview(confidence=0.95, hole_qty=5, dims=None):
    return {
        "part_number": "A050211E", "units": "inch", "confidence": confidence,
        "dimensions": dims if dims is not None else [
            {"id": "D001", "value": 4.0, "applies_to": "length"},
            {"id": "D002", "value": 2.0, "applies_to": "width"},
        ],
        "hole_callouts": [{"id": "H001", "qty": hole_qty, "diameter": 0.25}],
        "features": [], "build_order": [],
    }


# --------------------------------------------------------------------------- #
# flatten_overview
# --------------------------------------------------------------------------- #
def test_flatten_overview_emits_field_paths():
    fields = {f["field_path"]: f for f in flatten_overview(_overview())}
    assert fields["dimensions.D001.value"]["value"] == 4.0
    assert fields["hole_callouts.H001.qty"]["value"] == 5
    assert fields["hole_callouts.H001.diameter"]["value"] == 0.25
    # High overall confidence + clear dims -> HIGH per-field confidence.
    assert fields["dimensions.D001.value"]["confidence"] == "HIGH"


def test_flatten_overview_unclear_dim_is_low():
    ov = _overview(dims=[{"id": "D009", "value": 1.12, "value_unclear": True}])
    f = flatten_overview(ov)[0]
    assert f["confidence"] == "LOW"


# --------------------------------------------------------------------------- #
# Stage E merge rules
# --------------------------------------------------------------------------- #
def _ovf(fp, v, c):
    return {"field_path": fp, "value": v, "confidence": c}


def _rgf(fp, v, c, rid="p01_r01"):
    return {"field_path": fp, "value": v, "confidence": c, "region_id": rid}


def test_merge_agreement():
    m = merge_fields([_ovf("a.b", 3.0, "MEDIUM")], [_rgf("a.b", 3.0, "HIGH")])
    e = m.merge_log[0]
    assert e["resolution"] == R_AGREED and e["final_value"] == 3.0
    assert not m.needs_review


def test_merge_region_override_high_over_low():
    m = merge_fields([_ovf("hole_callouts.H001.qty", 5, "LOW")],
                     [_rgf("hole_callouts.H001.qty", 6, "HIGH")])
    e = m.merge_log[0]
    assert e["resolution"] == R_REGION_OVERRIDE
    assert e["overview_value"] == 5 and e["region_value"] == 6 and e["final_value"] == 6


def test_merge_overview_kept_when_overview_more_confident():
    m = merge_fields([_ovf("a.b", 4.0, "HIGH")], [_rgf("a.b", 9.9, "LOW")])
    assert m.merge_log[0]["resolution"] == R_OVERVIEW_KEPT
    assert m.merge_log[0]["final_value"] == 4.0


def test_merge_both_high_disagree_escalates_no_autotiebreak():
    m = merge_fields([_ovf("a.b", 4.0, "HIGH")], [_rgf("a.b", 5.0, "HIGH")])
    e = m.merge_log[0]
    assert e["resolution"] == R_CONFLICT
    assert e["final_value"] is None            # NOT auto-resolved
    assert m.needs_review
    assert any(q["reason"] == R_CONFLICT for q in m.review_queue)


def test_merge_coverage_gap_only_when_no_region_attempted():
    # regions_attempted=0 means the region pass didn't run over the area -> the
    # silent-skip guard fires: coverage_gap, routed to review.
    m = merge_fields([_ovf("a.b", 4.0, "HIGH")], [], regions_attempted=0)
    assert m.merge_log[0]["resolution"] == R_COVERAGE_GAP and m.needs_review


def test_merge_attempted_but_unread_is_overview_kept_not_review():
    # Regions were attempted (attempted>0) but none re-read this field -> keep
    # the overview value, uncorroborated, NOT a review item.
    m = merge_fields([_ovf("a.b", 4.0, "HIGH")], [], regions_attempted=6)
    assert m.merge_log[0]["resolution"] == R_OVERVIEW_KEPT
    assert not m.needs_review


def test_merge_region_added_field_new():
    m = merge_fields([], [_rgf("features.new_hole", 1, "HIGH")])
    assert m.merge_log[0]["resolution"] == R_REGION_ADDED
    assert m.resolved["features.new_hole"] == 1


def test_merge_accounts_for_every_overview_field():
    """The Stage-E acceptance assertion: every overview field_path has a
    merge-log entry with a resolution status."""
    ov = [_ovf("a", 1, "HIGH"), _ovf("b", 2, "LOW"), _ovf("c", 3, "MEDIUM")]
    rg = [_rgf("b", 9, "HIGH"), _rgf("z_extra", 7, "HIGH")]
    m = merge_fields(ov, rg)
    logged = {e["field_path"] for e in m.merge_log}
    for o in ov:
        assert o["field_path"] in logged
        assert next(e for e in m.merge_log if e["field_path"] == o["field_path"])["resolution"] \
            in (R_AGREED, R_REGION_OVERRIDE, R_OVERVIEW_KEPT, R_CONFLICT, R_COVERAGE_GAP)


# --------------------------------------------------------------------------- #
# Unconditional region pass — runs on every drawing (criterion 4)
# --------------------------------------------------------------------------- #
def test_region_pass_runs_even_when_overview_all_high_confidence(tmp_path):
    src = _master_png(tmp_path)
    calls = {"n": 0}

    def fake(image_b64, media_type, region_id, overview):
        calls["n"] += 1
        return []                                   # region reads nothing new

    res = run_region_extraction(src, _overview(confidence=0.99), tmp_path, "A050211E",
                                extract_fn=fake, keep_regions="none")
    # No confidence gate: EVERY region got a real call, count > 0.
    assert res.api_calls == len(res.regions) > 0
    assert calls["n"] == res.api_calls
    # Coverage is complete (0 gap), so no field is a coverage_gap due to tiling.
    assert all(e["resolution"] != R_COVERAGE_GAP for e in res.merge.merge_log)


def test_a050211e_hole_count_disagreement_recorded(tmp_path):
    """Overview reads 5 holes (LOW), a region reads 6 (HIGH): the merge log shows
    the disagreement with an explicit resolution, not a dropped/averaged field."""
    src = _master_png(tmp_path)

    def fake(image_b64, media_type, region_id, overview):
        # One region confidently reads 6 holes.
        if region_id.endswith("r01"):
            return [{"field_path": "hole_callouts.H001.qty", "value": 6,
                     "confidence": "HIGH", "agrees_with_overview": False,
                     "overview_value": 5}]
        return []

    res = run_region_extraction(src, _overview(confidence=0.6, hole_qty=5), tmp_path,
                                "A050211E", extract_fn=fake, keep_regions="all")
    hole = next(e for e in res.merge.merge_log
                if e["field_path"] == "hole_callouts.H001.qty")
    assert hole["overview_value"] == 5 and hole["region_value"] == 6
    assert hole["resolution"] == R_REGION_OVERRIDE and hole["final_value"] == 6


# --------------------------------------------------------------------------- #
# Output writer + ledger + overlay
# --------------------------------------------------------------------------- #
def test_outputs_written_manifest_mergelog_regions(tmp_path):
    src = _master_png(tmp_path)
    res = run_region_extraction(src, _overview(), tmp_path, "A050211E",
                                extract_fn=lambda *a: [], keep_regions="all",
                                overlay=True)
    regions_dir = tmp_path / "regions"
    manifest = json.loads((regions_dir / "manifest.json").read_text())
    assert manifest["region_count"] == len(res.regions)
    assert manifest["master_dims"] == [3000, 2000]
    assert len(manifest["regions"]) == len(res.regions)
    # merge_log.json present with an entry per overview field.
    ml = json.loads((tmp_path / "merge_log.json").read_text())
    ov_paths = {f["field_path"] for f in flatten_overview(_overview())}
    assert ov_paths <= {e["field_path"] for e in ml}
    # keep_regions=all -> a crop PNG + sidecar per region.
    for rg in res.regions:
        assert (regions_dir / f"{rg.id}.png").is_file()
        assert (regions_dir / f"{rg.id}_extraction.json").is_file()
    # overlay written.
    assert (tmp_path / "A050211E_page01_overlay.png").is_file()
    # master + sent retained.
    assert (tmp_path / "A050211E_page01_master.png").is_file()
    assert (tmp_path / "A050211E_page01_sent.png").is_file()


def test_keep_regions_none_drops_crops_keeps_sidecars(tmp_path):
    src = _master_png(tmp_path)
    res = run_region_extraction(src, _overview(), tmp_path, "P",
                                extract_fn=lambda *a: [], keep_regions="none")
    regions_dir = tmp_path / "regions"
    for rg in res.regions:
        assert not (regions_dir / f"{rg.id}.png").is_file()       # crop dropped
        assert (regions_dir / f"{rg.id}_extraction.json").is_file()  # sidecar kept


def test_ledger_appends_per_field_and_summary(tmp_path):
    src = _master_png(tmp_path)
    lessons = tmp_path / "lessons_learned.jsonl"

    def fake(image_b64, media_type, region_id, overview):
        if region_id.endswith("r01"):
            return [{"field_path": "hole_callouts.H001.qty", "value": 6, "confidence": "HIGH"}]
        return []

    run_region_extraction(src, _overview(confidence=0.6, hole_qty=5), tmp_path, "P",
                          extract_fn=fake, keep_regions="none", lessons_path=lessons)
    rows = [json.loads(x) for x in lessons.read_text().splitlines() if x.strip()]
    kinds = {r["kind"] for r in rows}
    assert "region_field" in kinds and "region_pass_summary" in kinds
    summary = next(r for r in rows if r["kind"] == "region_pass_summary")
    assert summary["api_calls"] > 0 and summary["region_count"] > 0
    assert summary["overrides"] >= 1                       # the 5->6 override


def test_default_extract_fn_is_callable_without_network(tmp_path):
    fn = default_region_extract_fn(model="claude-sonnet-5", cache_dir=tmp_path, usage_out={})
    assert callable(fn)


def test_apply_resolved_writes_overrides_back_and_noop_on_agreement():
    from pipeline.region_extraction import apply_resolved

    ov = _overview(hole_qty=5, dims=[{"id": "D001", "value": 4.0, "applies_to": "length"}])
    # A region_override corrected the hole count 5 -> 6 and confirmed D001 == 4.0.
    resolved = {"hole_callouts.H001.qty": 6, "dimensions.D001.value": 4.0,
                "features.new_thing": 9}   # unknown path -> not applied
    changed = apply_resolved(ov, resolved)
    assert changed == 1                                   # only the qty differed
    assert ov["hole_callouts"][0]["qty"] == 6
    assert ov["dimensions"][0]["value"] == 4.0
    # A fully-agreeing resolution changes nothing (criterion: agreement leaves the
    # extraction — and thus the build plan — untouched).
    assert apply_resolved(ov, {"dimensions.D001.value": 4.0,
                               "hole_callouts.H001.qty": 6}) == 0
