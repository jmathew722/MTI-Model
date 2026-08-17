"""Validation scorecard + projection angle + delivery report (reference docs
04, 05, 07, 09) — Phases A–D of docs/reference/INTEGRATION_PLAN.md.

The scorecard is the one place a human learns whether a part is good, so the
tests that matter are: each layer FAILS when it should, the verdict rule is the
doc's, a missing input is SKIPPED (never silently passed), and nothing here can
raise — a scorecard that crashes tells you nothing.
"""
import json
from pathlib import Path

import pytest

from pipeline.engineering_review import format_delivery_report, write_delivery_report
from pipeline.schema import DrawingData, infer_projection_angle
from pipeline.validation import (
    FAIL,
    PASS,
    PASS_WITH_ASSUMPTIONS,
    SKIPPED,
    build_scorecard,
    write_scorecard,
)


def _write(d: Path, name: str, payload) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def _layer(card, name):
    return next(x for x in card.layers if x.name == name)


# --------------------------------------------------------------------------- #
# Phase A — projection angle (doc 04)
# --------------------------------------------------------------------------- #
class TestProjectionAngle:
    def test_the_field_round_trips(self):
        m = DrawingData.model_validate({
            "units": "inch", "confidence": 0.9, "dimensions": [], "features": [],
            "projection_angle": "first_angle",
            "projection_angle_source": "title_block_symbol"})
        assert m.projection_angle == "first_angle"

    def test_old_extractions_without_the_field_still_load(self):
        """Additive-schema rule: every saved extraction predates this field."""
        m = DrawingData.model_validate(
            {"units": "inch", "confidence": 0.9, "dimensions": [], "features": []})
        assert m.projection_angle == "unknown"
        assert m.projection_angle_source == ""

    def test_a_read_symbol_always_wins(self):
        angle, source, _ = infer_projection_angle("first_angle", "ASME Y14.5", "inch")
        assert (angle, source) == ("first_angle", "title_block_symbol")

    def test_ansi_infers_third_angle(self):
        angle, source, basis = infer_projection_angle("unknown", "ASME Y14.5", "mm")
        assert angle == "third_angle" and source == "inferred_from_standard"
        assert "US standard" in basis

    def test_iso_infers_first_angle(self):
        angle, source, _ = infer_projection_angle("unknown", "ISO 128", "mm")
        assert angle == "first_angle" and source == "inferred_from_standard"

    def test_inch_units_infer_third_angle_when_no_standard(self):
        angle, source, _ = infer_projection_angle("unknown", "", "inch")
        assert angle == "third_angle" and source == "inferred_from_units"

    def test_metric_default_says_verify_because_it_mirrors_the_part(self):
        angle, _, basis = infer_projection_angle("unknown", "", "mm")
        assert angle == "first_angle"
        assert "mirrors the part" in basis

    def test_an_inferred_angle_is_an_assumption_not_a_silent_default(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {"units": "inch", "dimensions": []})
        card = build_scorecard(tmp_path, "P")
        layer = _layer(card, "projection_angle")
        assert layer.status == PASS                     # not a measured failure
        assert "MIRRORED" in layer.detail               # but impossible to miss
        assert any(a["id"] == "projection_angle" for a in card.assumptions)
        assert any("mirrors the part" in a for a in card.advisories)

    def test_a_read_symbol_produces_no_advisory(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {
            "units": "inch", "dimensions": [],
            "projection_angle": "third_angle",
            "projection_angle_source": "title_block_symbol"})
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "projection_angle").status == PASS
        assert not any("projection" in a for a in card.advisories)


# --------------------------------------------------------------------------- #
# Phase B — the scorecard layers and verdict rule (doc 07)
# --------------------------------------------------------------------------- #
class TestLayers:
    def test_a_failed_feature_build_is_a_layer_1_failure(self, tmp_path):
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "macro_result.json").write_text(
            '{"feature_id": "F002", "status": "failed"}\n', encoding="utf-8")
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "build_health").status == FAIL
        assert card.overall == FAIL

    def test_excluded_features_FAIL_the_layer_and_stay_an_advisory(self, tmp_path):
        """CHANGED 2026-08-17 (E027). This test used to assert PASS.

        That encoded the bug: a feature excluded as incomplete is NOT in the
        model, and build_health exists to answer "did every planned feature
        build". Asserting PASS meant TEST3 parts shipped a green build_health
        while missing a chamfer and a cut. The advisory text is still expected —
        it was never the problem; routing the signal ONLY to the advisory was.
        """
        _write(tmp_path, "P_build_dispositions.json",
               [{"feature_id": "F003", "state": "EXCLUDED_INCOMPLETE"}])
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "build_health").status == FAIL
        assert "F003" in _layer(card, "build_health").detail
        assert any("excluded as incomplete" in a for a in card.advisories)

    def test_a_deferred_open_feature_fails_the_layer(self, tmp_path):
        """The other way a feature goes missing without ever being 'failed'."""
        _write(tmp_path, "_deferred_log.json",
               {"total": 1, "recovered": 0, "open": 1,
                "items": [{"feature_id": "F007", "feature_type": "extrude_cut",
                           "recovered": False}]})
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "build_health").status == FAIL
        assert "F007" in _layer(card, "build_health").detail

    def test_a_recovered_deferred_feature_does_not_fail(self, tmp_path):
        """The retry ladder getting it built is a success, not a failure."""
        _write(tmp_path, "_deferred_log.json",
               {"total": 1, "recovered": 1, "open": 0,
                "items": [{"feature_id": "F007", "recovered": True}]})
        _write(tmp_path, "P_build_dispositions.json",
               [{"feature_id": "F007", "state": "BUILT"}])
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "build_health").status == PASS

    def test_pretty_printed_macro_result_is_read(self, tmp_path):
        """E027 root cause: the COM builder writes pretty-printed JSON with a
        results array, and this layer parsed it ONLY as JSONL — so every line
        failed to parse, results came out empty, and a recorded FAIL was
        invisible. Real case: 4088-A-RevA reported PASS while macro_result.json
        in the same folder said its chamfer FAILED."""
        (tmp_path / "logs").mkdir()
        _write(tmp_path / "logs", "macro_result.json", {"results": [
            {"feature": "F001", "feature_id": "F001", "status": "PASS", "detail": ""},
            {"feature": "F003", "feature_id": "F003", "status": "FAIL",
             "detail": "InsertFeatureChamfer returned None"},
        ]})
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "build_health").status == FAIL
        assert "F003" in _layer(card, "build_health").detail

    def test_a_bbox_failure_is_carried_through(self, tmp_path):
        (tmp_path / "P_model_check.txt").write_text(
            "MODEL CHECK\n[PASS] Solid body exists.\n"
            "[FAIL] height: drawing value(s) [488.95] mm not found\n", encoding="utf-8")
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "bounding_box").status == FAIL

    def test_a_mismeasured_feature_fails_the_audit(self, tmp_path):
        _write(tmp_path, "P_feature_verification.json", {"features": [
            {"feature_id": "F001", "classification": "OK"},
            {"feature_id": "F002", "classification": "MISPLACED"}]})
        card = build_scorecard(tmp_path, "P")
        layer = _layer(card, "feature_audit")
        assert layer.status == FAIL and "F002=MISPLACED" in layer.detail

    def test_a_failed_must_meet_constraint_fails(self, tmp_path):
        _write(tmp_path, "constraint_verification.json", {"constraints": [
            {"id": "MM-001", "status": "FAIL", "required": 4, "measured": 3}]})
        card = build_scorecard(tmp_path, "P")
        assert _layer(card, "must_meet").status == FAIL

    def test_missing_inputs_are_skipped_never_silently_passed(self, tmp_path):
        card = build_scorecard(tmp_path, "P")
        for name in ("solid_body", "volume_ratio", "feature_audit", "must_meet"):
            layer = _layer(card, name)
            assert layer.status == SKIPPED
            assert layer.detail, f"{name} must state WHY it was skipped"

    def test_the_scorecard_never_raises(self, tmp_path):
        (tmp_path / "P_build_plan.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "P_feature_verification.json").write_text("[[[", encoding="utf-8")
        card = build_scorecard(tmp_path, "P")
        assert card.overall in (PASS, PASS_WITH_ASSUMPTIONS, FAIL, SKIPPED)


class TestVerdictRule:
    def test_any_failure_means_fail(self, tmp_path):
        _write(tmp_path, "P_feature_verification.json",
               {"features": [{"feature_id": "F1", "classification": "MISSING"}]})
        assert build_scorecard(tmp_path, "P").overall == FAIL

    def test_clean_with_assumptions_is_pass_with_assumptions(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {"units": "inch", "dimensions": []})
        _write(tmp_path, "P_feature_verification.json",
               {"features": [{"feature_id": "F1", "classification": "OK"}]})
        card = build_scorecard(tmp_path, "P")
        assert card.overall == PASS_WITH_ASSUMPTIONS      # inferred projection angle

    def test_clean_with_a_read_symbol_and_no_assumptions_is_pass(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {
            "units": "inch", "dimensions": [],
            "projection_angle": "third_angle",
            "projection_angle_source": "title_block_symbol"})
        _write(tmp_path, "P_feature_verification.json",
               {"features": [{"feature_id": "F1", "classification": "OK"}]})
        assert build_scorecard(tmp_path, "P").overall == PASS

    def test_assumptions_are_ranked_lowest_confidence_first(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {"units": "inch", "dimensions": [
            {"id": "D001", "value": 1.0, "assumption_made": True,
             "assumption_basis": "typ_sibling", "assumption_confidence": 0.9}]})
        card = build_scorecard(tmp_path, "P")
        confidences = [a["confidence"] for a in card.assumptions]
        assert confidences == sorted(confidences)

    def test_the_scorecard_is_written_to_disk(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {"units": "inch", "dimensions": []})
        path = write_scorecard(tmp_path, "P")
        assert path is not None and path.name == "validation.json"
        assert json.loads(path.read_text(encoding="utf-8"))["overall"]


# --------------------------------------------------------------------------- #
# Phase C — dimension coverage (doc 05)
# --------------------------------------------------------------------------- #
class TestDimensionCoverage:
    def test_an_unconsumed_dimension_is_reported(self, tmp_path):
        _write(tmp_path, "P_extraction.json", {"units": "inch", "dimensions": [
            {"id": "D001", "value": 4.0}, {"id": "D002", "value": 2.0}]})
        _write(tmp_path, "P_build_plan.json", {"steps": [
            {"feature_id": "F001", "type": "extrude_boss",
             "evidence": {"dimension_ids": ["D001"]}}]})
        card = build_scorecard(tmp_path, "P")
        layer = _layer(card, "dimension_coverage")
        assert layer.status == PASS               # advisory, never a FAIL
        assert layer.data["unused"] == ["D002"]
        assert any("consumed by no feature" in a for a in card.advisories)

    def test_full_coverage_raises_no_advisory(self, tmp_path):
        _write(tmp_path, "P_extraction.json",
               {"units": "inch", "dimensions": [{"id": "D001", "value": 4.0}]})
        _write(tmp_path, "P_build_plan.json", {"steps": [
            {"feature_id": "F001", "evidence": {"dimension_ids": ["D001"]}}]})
        card = build_scorecard(tmp_path, "P")
        assert "drive a build step" in _layer(card, "dimension_coverage").detail

    def test_a_plan_without_evidence_is_skipped_not_reported_as_all_unused(self, tmp_path):
        _write(tmp_path, "P_extraction.json",
               {"units": "inch", "dimensions": [{"id": "D001", "value": 4.0}]})
        _write(tmp_path, "P_build_plan.json", {"steps": [{"feature_id": "F001"}]})
        assert _layer(build_scorecard(tmp_path, "P"), "dimension_coverage").status == SKIPPED


class TestEvidenceOnSteps:
    def test_generated_steps_carry_their_evidence(self, tmp_path):
        from pipeline.macro_generator import generate_macro_package
        from pipeline.validator import format_verification_report, run_verification
        from tests.test_golden_macros import _golden_drawing

        data = _golden_drawing()
        model, report = run_verification(data)
        pkg = generate_macro_package(model, data,
                                     format_verification_report(model, report), tmp_path)
        plan = json.loads(Path(pkg.build_plan_json).read_text(encoding="utf-8"))
        solid = [s for s in plan["steps"]
                 if s.get("type") in ("extrude_boss", "extrude_cut", "hole")]
        assert solid, "golden package must contain solid steps"
        for step in solid:
            ev = step.get("evidence") or {}
            assert ev.get("dimension_ids"), f"{step['feature_id']} cites no dimensions"
            assert ev.get("values"), f"{step['feature_id']} cites no values"


# --------------------------------------------------------------------------- #
# Phase D — delivery report (doc 09)
# --------------------------------------------------------------------------- #
class TestDeliveryReport:
    SCORECARD = {
        "overall": "PASS_WITH_ASSUMPTIONS",
        "layers": [{"name": "solid_body", "status": "PASS", "detail": "one body"},
                   {"name": "bounding_box", "status": "FAIL", "detail": "height off"}],
        "assumptions": [
            {"id": "D004", "what": "depth = .28", "basis": "typ", "confidence": 0.9},
            {"id": "F003", "what": "corner radius?", "basis": "open_question",
             "confidence": 0.0, "default_if_unanswered": "ships sharp"}],
    }

    def test_the_four_sections_are_present_and_ordered(self):
        text = format_delivery_report("P", self.SCORECARD, [])
        for i, head in enumerate(["1. RESULT", "2. ASSUMPTIONS", "3. UNRESOLVED",
                                  "4. THE ASK"]):
            assert head in text
        positions = [text.index(h) for h in
                     ["1. RESULT", "2. ASSUMPTIONS", "3. UNRESOLVED", "4. THE ASK"]]
        assert positions == sorted(positions)

    def test_assumptions_are_lowest_confidence_first(self):
        text = format_delivery_report("P", self.SCORECARD, [])
        assert text.index("F003") < text.index("D004")

    def test_a_shipping_default_is_stated(self):
        assert "ships as: ships sharp" in format_delivery_report("P", self.SCORECARD, [])

    def test_exactly_one_ask(self):
        text = format_delivery_report("P", self.SCORECARD, [])
        assert text.count("Reply 'approved'") == 1

    def test_critical_items_are_the_unresolved_section(self):
        text = format_delivery_report("P", self.SCORECARD, [
            {"severity": "CRITICAL", "id": "F004", "what": "no radius",
             "decision": "shipped sharp"},
            {"severity": "LOW", "id": "F005", "what": "cosmetic note"}])
        assert "F004" in text and "F005" not in text

    def test_no_assumptions_still_produces_a_clean_ask(self):
        text = format_delivery_report("P", {"overall": "PASS", "layers": [],
                                            "assumptions": []}, [])
        assert "None: every value was read from the drawing." in text
        assert "Nothing needs a decision." in text

    def test_it_is_written_to_disk_and_never_raises(self, tmp_path):
        path = write_delivery_report(tmp_path, "P", self.SCORECARD, [])
        assert path is not None and path.name == "P_delivery_report.txt"
        assert write_delivery_report(tmp_path / "nope" / "deeper", "P") is None
