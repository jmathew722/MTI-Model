"""The canonical per-feature ledger (REFACTOR_ANALYSIS §1.2 / §2.3).

Six artifacts tracked feature state in six shapes; the ledger is the one place
that history is assembled. These tests pin the contract that matters: nothing is
overwritten, the order is the pipeline's own stage order, every source artifact
is optional, and a partial run still produces a valid ledger.
"""
import json

import pytest

from pipeline.feature_ledger import (
    BUILT,
    EXCLUDED_INCOMPLETE,
    LEDGER_SUFFIX,
    NEEDS_HUMAN_INPUT,
    STAGE_ASSIST,
    STAGE_DEFERRED,
    STAGE_FEATURE_VERIFY,
    STAGE_MACRO,
    STAGE_ORDER,
    STAGE_RECONCILE,
    STAGE_SEQUENCER,
    FeatureLedger,
    build_ledger,
    write_ledger,
)


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def part_dir(tmp_path):
    _write(tmp_path, "P1_build_dispositions.json", [
        {"feature_id": "F001", "feature_type": "extrude_boss", "state": BUILT,
         "stage_name": "base_solid", "values_used": {"length": 4.0}},
        {"feature_id": "F002", "feature_type": "hole", "state": "BUILT_WITH_DERIVED_VALUE",
         "derivation_source": "sibling_diameter",
         "flags": [{"flag_tier": "CRITICAL"}]},
        {"feature_id": "F003", "feature_type": "fillet", "state": EXCLUDED_INCOMPLETE,
         "reason": "no radius given"},
    ])
    _write(tmp_path, "P1_build_plan.json", {"steps": [
        {"seq": 1, "feature_id": "F001", "type": "extrude_boss", "status": "generated",
         "macro_file": "01_F001_base.vba", "positions_xy": [[0, 0]]},
        {"seq": 2, "feature_id": "F002", "type": "hole", "status": "generated",
         "macro_file": "02_F002_hole.vba"},
        {"seq": 0, "feature_id": "-", "type": "setup", "status": "generated"},
    ]})
    _write(tmp_path, "P1_feature_verification.json", {"features": [
        {"feature_id": "F001", "classification": "OK"},
        {"feature_id": "F002", "classification": "MISPLACED",
         "measured": [1.1, 2.2], "expected": [1.0, 2.0], "reason": "off by .1"},
    ]})
    _write(tmp_path, "_deferred_log.json", {"items": [
        {"feature_id": "F004", "feature_type": "chamfer", "recovered": False,
         "error_class": "selection_failure", "error_text": "SelectByID2 failed",
         "attempts": [{"pass": 2, "strategy": "reselect_by_enumerated_topology"}]},
    ]})
    _write(tmp_path, "P1_reconciliation_report.json", {
        "unresolved": [{"feature_id": "F003", "status": "unresolved_after_pass_1",
                        "reason": "no radius anywhere in the extraction"}],
        "splices_applied": ["F005"]})
    _write(tmp_path, "P1_assist_queue.json", {"questions": [
        {"feature_id": "F003", "status": "pending",
         "question_text": "What corner radius?", "default_if_unanswered": "ships sharp"},
    ]})
    return tmp_path


class TestAssembly:
    def test_every_source_feature_appears_once(self, part_dir):
        led = build_ledger(part_dir, part="P1")
        assert led.ids() == ["F001", "F002", "F003", "F004", "F005"]
        assert len(led) == 5

    def test_history_is_append_only_and_in_stage_order(self, part_dir):
        rec = build_ledger(part_dir).get("F002")
        stages = [e.stage for e in rec.history]
        assert stages == [STAGE_SEQUENCER, STAGE_MACRO, STAGE_FEATURE_VERIFY]
        assert stages == sorted(stages, key=STAGE_ORDER.index)
        # the earlier words are still there, not replaced by the latest
        assert rec.status_at(STAGE_SEQUENCER) == "BUILT_WITH_DERIVED_VALUE"
        assert rec.status_at(STAGE_MACRO) == "generated"
        assert rec.verification_verdict == "MISPLACED"
        assert rec.status == "MISPLACED"          # current = last stage to speak

    def test_disposition_and_verification_are_both_reachable(self, part_dir):
        rec = build_ledger(part_dir).get("F001")
        assert rec.disposition_state == BUILT
        assert rec.verification_verdict == "OK"
        assert rec.feature_type == "extrude_boss"

    def test_scaffold_steps_without_a_feature_id_are_not_features(self, part_dir):
        assert "-" not in build_ledger(part_dir)

    def test_deferred_open_feature_is_recorded_with_its_diagnosis(self, part_dir):
        rec = build_ledger(part_dir).get("F004")
        entry = rec.at(STAGE_DEFERRED)
        assert entry.status == "deferred_open"
        assert entry.basis == "selection_failure"
        assert entry.data["attempts"]

    def test_assist_question_marks_the_feature(self, part_dir):
        rec = build_ledger(part_dir).get("F003")
        assert rec.needs_human_input
        assert rec.at(STAGE_ASSIST).detail == "What corner radius?"
        assert rec.at(STAGE_RECONCILE).status == "unresolved_after_pass_1"

    def test_open_items_names_everything_a_human_should_look_at(self, part_dir):
        ids = [r.feature_id for r in build_ledger(part_dir).open_items()]
        assert ids == ["F002", "F003", "F004"]     # F001 OK, F005 spliced clean

    def test_sources_are_recorded(self, part_dir):
        led = build_ledger(part_dir)
        assert "build_dispositions" in led.sources
        assert "feature_verification" in led.sources


class TestTolerance:
    def test_an_empty_directory_yields_an_empty_ledger_not_an_error(self, tmp_path):
        led = build_ledger(tmp_path)
        assert len(led) == 0
        assert led.to_dict()["feature_count"] == 0

    def test_a_partial_run_still_produces_a_valid_ledger(self, tmp_path):
        _write(tmp_path, "P_build_dispositions.json",
               [{"feature_id": "F001", "state": BUILT}])
        led = build_ledger(tmp_path)
        assert led.get("F001").disposition_state == BUILT
        assert led.get("F001").verification_verdict == ""

    def test_unreadable_artifact_is_skipped_not_fatal(self, tmp_path):
        (tmp_path / "P_build_plan.json").write_text("{not json", encoding="utf-8")
        _write(tmp_path, "P_build_dispositions.json",
               [{"feature_id": "F001", "state": BUILT}])
        assert build_ledger(tmp_path).get("F001").disposition_state == BUILT

    def test_dispositions_fall_back_to_the_copy_in_the_build_plan(self, tmp_path):
        _write(tmp_path, "P_build_plan.json", {
            "steps": [], "dispositions": [{"feature_id": "F009", "state": BUILT}]})
        assert build_ledger(tmp_path).get("F009").disposition_state == BUILT

    def test_macro_result_json_lines_are_ingested(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "macro_result.json").write_text(
            '{"feature_id": "F001", "status": "success"}\n'
            'not json at all\n'
            '{"feature_id": "F002", "status": "failed", "error": "zero thickness"}\n',
            encoding="utf-8")
        led = build_ledger(tmp_path)
        assert led.get("F001").status == "success"
        assert led.get("F002").status == "failed"      # malformed line skipped, rest kept


class TestWriting:
    def test_ledger_is_written_as_json(self, part_dir):
        path = write_ledger(part_dir, "P1")
        assert path is not None and path.name == f"P1{LEDGER_SUFFIX}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["feature_count"] == 5
        assert data["stage_order"] == list(STAGE_ORDER)
        f002 = next(f for f in data["features"] if f["feature_id"] == "F002")
        assert [h["stage"] for h in f002["history"]] == [
            STAGE_SEQUENCER, STAGE_MACRO, STAGE_FEATURE_VERIFY]

    def test_writing_never_raises_on_a_bad_path(self, tmp_path):
        assert write_ledger(tmp_path / "does" / "not" / "exist", "P") is None


class TestDirectRecording:
    def test_record_requires_a_feature_id(self):
        with pytest.raises(ValueError):
            FeatureLedger().record("", STAGE_SEQUENCER, BUILT)

    def test_repeated_records_accumulate(self):
        led = FeatureLedger()
        led.record("F001", STAGE_SEQUENCER, BUILT)
        led.record("F001", STAGE_FEATURE_VERIFY, "OK")
        rec = led.get("F001")
        assert len(rec.history) == 2
        assert rec.status == "OK"

    def test_with_status_filters(self):
        led = FeatureLedger()
        led.record("F001", STAGE_SEQUENCER, BUILT)
        led.record("F002", STAGE_SEQUENCER, EXCLUDED_INCOMPLETE)
        assert [r.feature_id for r in led.with_status(EXCLUDED_INCOMPLETE)] == ["F002"]


class TestSummaryViewUsesTheLedger:
    """The first migrated consumer (REFACTOR_ANALYSIS §1.2) — it must actually
    read the ledger, not keep its own private reconciliation."""

    def test_summary_view_builds_the_ledger(self):
        import inspect

        from pipeline import summary_view

        assert "build_ledger" in inspect.getsource(summary_view.build_summary)
