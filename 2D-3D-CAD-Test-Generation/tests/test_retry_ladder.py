"""The shared bounded-retry contract (REFACTOR_ANALYSIS §1.5 / §2.7).

Three stages used to verify cap enforcement, escalating-strategy ordering and
oscillation detection separately, with three subtly different implementations of
the same guarantee. This suite is the one place that guarantee is tested; the
three callers' own suites then only have to test their domain logic.
"""
import pytest

from pipeline.retry_ladder import (
    DEFAULT_CAP,
    STOP_CAP,
    STOP_COMPLETED,
    STOP_ERROR,
    STOP_EXHAUSTED,
    STOP_NO_PROGRESS,
    STOP_NOTHING_TO_DO,
    STOP_OSCILLATION,
    LadderContext,
    PassResult,
    run_ladder,
)


class TestCapEnforcement:
    def test_never_runs_more_than_the_cap(self):
        seen = []

        def attempt(n):
            seen.append(n)
            return PassResult(progressed=True)

        result = run_ladder(attempt, cap=3)
        assert seen == [1, 2, 3]
        assert result.passes_used == 3
        assert result.stop_class == STOP_CAP
        assert "cap (3)" in result.stopped_reason

    def test_default_cap_is_three(self):
        assert DEFAULT_CAP == 3
        calls = []
        run_ladder(lambda n: (calls.append(n), PassResult(progressed=True))[1])
        assert len(calls) == 3

    def test_zero_cap_runs_nothing_and_says_so(self):
        called = []
        result = run_ladder(lambda n: called.append(n), cap=0)
        assert called == []
        assert result.passes_used == 0
        assert result.stop_class == STOP_NOTHING_TO_DO

    def test_first_pass_number_is_configurable(self):
        """The deferred-feature queue numbers retries from 2 — pass 1 was the
        original build attempt."""
        seen = []
        run_ladder(lambda n: (seen.append(n), PassResult(progressed=True))[1],
                   cap=2, first_pass=2)
        assert seen == [2, 3]


class TestTermination:
    def test_done_stops_immediately(self):
        seen = []

        def attempt(n):
            seen.append(n)
            return PassResult(done=True, detail="finished")

        result = run_ladder(attempt, cap=3)
        assert seen == [1]
        assert result.completed and result.stop_class == STOP_COMPLETED
        assert result.stopped_reason == "finished"

    def test_no_progress_stops_rather_than_burning_the_cap(self):
        seen = []

        def attempt(n):
            seen.append(n)
            return PassResult(progressed=False)

        result = run_ladder(attempt, cap=3)
        assert seen == [1]
        assert result.stop_class == STOP_NO_PROGRESS

    def test_no_progress_may_be_allowed_when_passes_escalate_strategy(self):
        """The deferred-retry ladder keeps going on a fruitless pass because the
        NEXT pass tries a genuinely different strategy."""
        seen = []

        def attempt(n):
            seen.append(n)
            return PassResult(progressed=False)

        result = run_ladder(attempt, cap=3, stop_on_no_progress=False)
        assert seen == [1, 2, 3]
        assert result.stop_class == STOP_CAP

    def test_exhaustion_always_stops_even_without_the_no_progress_rule(self):
        def attempt(n):
            return PassResult(progressed=False, exhausted=True)

        result = run_ladder(attempt, cap=3, stop_on_no_progress=False)
        assert result.passes_used == 1
        assert result.stop_class == STOP_EXHAUSTED

    def test_progress_plus_exhaustion_still_gets_another_pass(self):
        seen = []

        def attempt(n):
            seen.append(n)
            return PassResult(progressed=n == 1, exhausted=True)

        result = run_ladder(attempt, cap=3, stop_on_no_progress=False)
        assert seen == [1, 2]
        assert result.stop_class == STOP_EXHAUSTED

    def test_an_error_stops_the_ladder_and_is_recorded(self):
        result = run_ladder(lambda n: PassResult(error="COM died"), cap=3)
        assert result.stop_class == STOP_ERROR
        assert "COM died" in result.stopped_reason
        assert result.passes_used == 1

    def test_a_raising_attempt_becomes_an_error_stop_not_a_crash(self):
        def attempt(n):
            raise RuntimeError("boom")

        result = run_ladder(attempt, cap=3)
        assert result.stop_class == STOP_ERROR
        assert "RuntimeError: boom" in result.stopped_reason

    def test_an_attempt_returning_none_counts_as_no_progress(self):
        result = run_ladder(lambda n: None, cap=3)
        assert result.passes_used == 1
        assert result.stop_class == STOP_NO_PROGRESS


class TestOscillation:
    def test_a_regressed_unit_stops_the_ladder(self):
        passes = [PassResult(progressed=True, ok_ids={"F001", "F002"}),
                  PassResult(progressed=True, ok_ids={"F002"})]

        result = run_ladder(lambda n: passes[n - 1], cap=3)
        assert result.stop_class == STOP_OSCILLATION
        assert result.regressed == ["F001"]
        assert "F001" in result.stopped_reason

    def test_no_history_means_no_oscillation_on_the_first_pass(self):
        result = run_ladder(lambda n: PassResult(progressed=False, ok_ids=set()), cap=2)
        assert result.stop_class == STOP_NO_PROGRESS

    def test_growing_the_passing_set_is_not_oscillation(self):
        passes = [PassResult(progressed=True, ok_ids={"F001"}),
                  PassResult(done=True, ok_ids={"F001", "F002"})]
        result = run_ladder(lambda n: passes[n - 1], cap=3)
        assert result.completed

    def test_oscillation_is_checked_before_completion(self):
        """A pass that fixes everything else but regresses one unit still stops —
        a regression is never masked by an otherwise-good pass."""
        passes = [PassResult(progressed=True, ok_ids={"F001", "F002"}),
                  PassResult(done=True, ok_ids={"F002", "F003"})]
        result = run_ladder(lambda n: passes[n - 1], cap=3)
        assert result.stop_class == STOP_OSCILLATION
        assert result.regressed == ["F001"]

    def test_oscillation_detection_can_be_disabled(self):
        passes = [PassResult(progressed=True, ok_ids={"F001"}),
                  PassResult(progressed=True, ok_ids=set()),
                  PassResult(progressed=True, ok_ids={"F001"})]
        result = run_ladder(lambda n: passes[n - 1], cap=3,
                            stop_on_oscillation=False)
        assert result.stop_class == STOP_CAP

    def test_an_attempt_can_report_the_regression_itself(self):
        """The geometric loop asks mid-pass so it can skip correction work it
        would only throw away — same rule, consulted earlier."""
        result = run_ladder(lambda n: PassResult(regressed=["F007"], ok_ids=set()),
                            cap=3)
        assert result.stop_class == STOP_OSCILLATION
        assert result.regressed == ["F007"]


class TestLadderContext:
    def test_context_is_passed_to_two_argument_attempts(self):
        seen = []

        def attempt(n, ctx):
            seen.append((n, ctx.pass_num, ctx.has_history, set(ctx.prev_ok)))
            return PassResult(progressed=True, ok_ids={"F001"})

        run_ladder(attempt, cap=2)
        assert seen == [(1, 1, False, set()), (2, 2, True, {"F001"})]

    def test_context_regressed_uses_the_same_rule_as_the_ladder(self):
        ctx = LadderContext(pass_num=2, prev_ok=frozenset({"A", "B"}), has_history=True)
        assert ctx.regressed({"B"}) == ["A"]
        assert ctx.regressed({"A", "B", "C"}) == []

    def test_first_pass_context_never_reports_a_regression(self):
        ctx = LadderContext(pass_num=1)
        assert ctx.regressed(set()) == []


class TestLedger:
    def test_every_pass_appends_one_entry(self):
        def attempt(n):
            return PassResult(progressed=n < 3, entry={"recovered": [f"F00{n}"]})

        result = run_ladder(attempt, cap=4)
        assert [e["pass"] for e in result.ledger] == [1, 2, 3]
        assert result.ledger[0]["recovered"] == ["F001"]
        assert result.ledger[-1]["stop"] == STOP_NO_PROGRESS

    def test_as_dict_is_json_shaped(self):
        import json

        result = run_ladder(lambda n: PassResult(done=True), cap=2)
        json.dumps(result.as_dict())          # must not raise
        assert result.as_dict()["stop_class"] == STOP_COMPLETED

    def test_custom_stop_wording_is_used(self):
        result = run_ladder(lambda n: PassResult(progressed=True), cap=1,
                            reasons={STOP_CAP: "ran out after {cap}"})
        assert result.stopped_reason == "ran out after 1"


class TestCallersShareTheContract:
    """The three ladders in the pipeline must actually go through this module —
    a caller that quietly reimplements the loop is the regression this whole
    consolidation exists to prevent."""

    def test_deferred_retry_uses_the_ladder(self):
        import inspect

        from pipeline import deferred_retry

        src = inspect.getsource(deferred_retry.run_retry_passes)
        assert "run_ladder" in src

    def test_reconciliation_loops_use_the_ladder(self):
        import inspect

        from pipeline import reconciliation

        assert "run_ladder" in inspect.getsource(reconciliation.reconcile_part)
        assert "run_ladder" in inspect.getsource(reconciliation.geometric_correction_loop)

    def test_deferred_queue_records_how_its_ladder_ended(self):
        from pipeline.deferred_retry import DeferredQueue, run_retry_passes

        q = DeferredQueue()
        q.add("F004", "hole", "SelectByID2 could not select the target face")
        run_retry_passes(q, retry_one=lambda item, strategy, ctx: (False, strategy))
        assert q.ladder["stop_class"] in (STOP_EXHAUSTED, STOP_CAP)
        assert q.ladder["passes_used"] >= 1
        assert q.open_items()          # still open -> goes to the clarification gate
