"""The shared high-resolution second-look subsystem (REFACTOR_ANALYSIS §1.4).

Tiled extraction (escalation-triggered) and region extraction (unconditional)
now share window planning, the coverage guarantee, and the reading-reconciliation
decision core — differing only in trigger POLICY and in the key their readings
match on. These tests pin the shared half, and check that both callers really go
through it.
"""
import pytest

from pipeline.highres_pass import (
    AGREED,
    ALWAYS,
    A_WINS,
    B_WINS,
    CONFLICT,
    NEVER,
    ON_CONFIDENCE_HEURISTIC,
    assert_full_coverage,
    best_by_rank,
    evaluate_trigger,
    group_by_proximity,
    numeric_key,
    rank_confidence,
    reconcile_pair,
    values_agree,
    window_starts,
    windows_by_division,
    windows_fixed_size,
)


class TestTriggerPolicy:
    def test_always_fires_and_says_why(self):
        d = evaluate_trigger(ALWAYS)
        assert d.fire and bool(d) and d.policy == ALWAYS
        assert "confidently-wrong" in d.reasons[0]

    def test_never_is_recorded_as_a_decision_not_an_absence(self):
        d = evaluate_trigger(NEVER)
        assert not d.fire and d.policy == NEVER and d.reasons

    def test_heuristic_fires_on_low_confidence(self):
        d = evaluate_trigger(ON_CONFIDENCE_HEURISTIC,
                             extraction={"confidence": 0.4, "dimensions": []})
        assert d.fire and any("confidence" in r for r in d.reasons)

    def test_heuristic_does_not_fire_on_a_clean_extraction(self):
        d = evaluate_trigger(ON_CONFIDENCE_HEURISTIC,
                             extraction={"confidence": 0.95, "dimensions": [
                                 {"value": 1.0}, {"value": 2.0}]})
        assert not d.fire

    def test_heuristic_fires_on_a_large_sheet_at_the_raster_cap(self):
        d = evaluate_trigger(ON_CONFIDENCE_HEURISTIC, page_area_sqin=34 * 44,
                             raster_long_edge=2576)
        assert d.fire and any("large sheet" in r for r in d.reasons)

    def test_unknown_policy_is_a_loud_error(self):
        with pytest.raises(ValueError, match="unknown trigger policy"):
            evaluate_trigger("sometimes")

    def test_decision_is_serializable(self):
        assert set(evaluate_trigger(ALWAYS).as_dict()) == {"fire", "policy", "reasons"}


class TestWindowPlanning:
    def test_fixed_size_windows_overlap_by_the_requested_fraction(self):
        ws = windows_fixed_size(4000, 1000, 1000, 0.2)
        xs = sorted({w.x0 for w in ws})
        assert xs[1] - xs[0] == 800            # step = size * (1 - overlap)

    def test_last_window_is_clamped_to_the_far_edge(self):
        starts = window_starts(2500, 1000, 800)
        assert starts[-1] == 1500               # 1500 + 1000 == 2500, no overhang
        assert windows_fixed_size(2500, 500, 1000, 0.2)[-1].x1 == 2500

    def test_a_raster_smaller_than_one_window_is_a_single_window(self):
        assert len(windows_fixed_size(500, 400, 1000, 0.2)) == 1

    def test_division_windows_scale_their_count_with_sheet_size(self):
        assert len(windows_by_division(1400, 1400, 1400, 0.2)) == 1
        assert len(windows_by_division(4200, 2800, 1400, 0.2)) == 3 * 2

    def test_division_windows_pad_interior_sides_only(self):
        ws = windows_by_division(2800, 1400, 1400, 0.2)
        assert ws[0].x0 == 0                    # no padding outside the sheet
        assert ws[-1].x1 == 2800
        assert ws[0].x1 > 1400                  # interior side padded

    @pytest.mark.parametrize("w,h", [(4000, 1000), (2500, 2500), (999, 3001)])
    def test_both_planners_cover_the_whole_raster(self, w, h):
        assert_full_coverage(windows_fixed_size(w, h, 1000, 0.22), w, h)
        assert_full_coverage(windows_by_division(w, h, 1400, 0.2), w, h)

    def test_coverage_guard_rejects_a_gap(self):
        from pipeline.highres_pass import Window

        with pytest.raises(ValueError, match="horizontal gap"):
            assert_full_coverage([Window(0, 0, 0, 0, 100, 500)], 1000, 500)

    def test_coverage_guard_rejects_no_windows(self):
        with pytest.raises(ValueError, match="unread"):
            assert_full_coverage([], 100, 100)


class TestReconciliationCore:
    def test_numeric_agreement_is_tolerant(self):
        assert values_agree(1.0, 1.0000001)
        assert not values_agree(1.0, 1.5)

    def test_text_agreement_is_case_and_space_insensitive(self):
        assert values_agree(" THRU ", "thru")
        assert not values_agree("thru", "blind")

    def test_none_agrees_only_with_none(self):
        assert values_agree(None, None)
        assert not values_agree(None, 1.0)
        assert not values_agree(1.0, None)

    def test_confidence_ranking(self):
        assert rank_confidence("high") > rank_confidence("MEDIUM") > rank_confidence("low")
        assert rank_confidence(None) == 0        # unknown ranks lowest, never highest

    def test_agreeing_readings_need_no_winner(self):
        assert reconcile_pair(1.0, 1.0, 2, 0) == AGREED

    def test_higher_confidence_wins_a_disagreement(self):
        assert reconcile_pair(1.0, 2.0, 2, 1) == A_WINS
        assert reconcile_pair(1.0, 2.0, 0, 2) == B_WINS

    def test_equal_confidence_disagreement_is_never_auto_tie_broken(self):
        assert reconcile_pair(1.0, 2.0, 2, 2) == CONFLICT
        assert reconcile_pair(1.0, 2.0, 0, 0) == CONFLICT

    def test_numeric_key_groups_equal_numbers_and_passes_text_through(self):
        assert numeric_key(1.00001) == numeric_key(1.0)
        assert numeric_key("thru") == "thru"

    def test_group_by_proximity_groups_within_tolerance(self):
        items = [{"p": (0, 0)}, {"p": (10, 0)}, {"p": (500, 500)}]
        groups = group_by_proximity(items, lambda d: d["p"], tol=25)
        assert groups == [[0, 1], [2]]

    def test_items_without_a_position_are_left_to_the_caller(self):
        items = [{"p": (0, 0)}, {"p": None}]
        assert group_by_proximity(items, lambda d: d["p"], tol=5) == [[0]]

    def test_best_by_rank_is_deterministic_on_ties(self):
        items = [{"k": "a", "c": "HIGH", "v": 1}, {"k": "a", "c": "HIGH", "v": 2}]
        best = best_by_rank(items, lambda d: d["k"], lambda d: rank_confidence(d["c"]))
        assert best["a"]["v"] == 1              # first wins a tie, input order decides


class TestBothCallersShareTheCore:
    def test_tiled_extraction_plans_windows_through_the_shared_module(self):
        import inspect

        from utils import tiled_extraction

        assert "windows_fixed_size" in inspect.getsource(tiled_extraction.make_tiles)

    def test_region_planning_goes_through_the_shared_module(self):
        import inspect

        from pipeline import image_coordinates

        assert "windows_by_division" in inspect.getsource(image_coordinates.compute_regions)

    def test_region_merge_uses_the_shared_decision_core(self):
        import inspect

        from pipeline import region_extraction

        assert "reconcile_pair" in inspect.getsource(region_extraction.merge_fields)

    def test_tile_stitch_uses_the_shared_grouping(self):
        import inspect

        from utils import tiled_extraction

        assert "group_by_proximity" in inspect.getsource(tiled_extraction.stitch)

    def test_the_two_systems_still_differ_only_in_policy(self):
        """Tiled = escalation, region = unconditional. Same machinery, different
        trigger — which is the whole point of the consolidation."""
        from pipeline.region_extraction import run_region_extraction
        import inspect

        sig = inspect.signature(run_region_extraction)
        assert sig.parameters["trigger_policy"].default == ALWAYS
