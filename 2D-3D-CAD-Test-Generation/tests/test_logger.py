"""Shared logger: handler wiring and once-per-fact warnings (2026-08-16).

Two problems, both found by reading real run output rather than by a test:

* one collision produced FOUR identical warnings, because the pure function that
  discovers it is called several times per feature (base choice, sort key,
  disposition, COM build). A warning repeated four times teaches an operator to
  skim warnings, which is when the one that matters gets missed.
* ``get_logger`` used a single global "configured" flag, so whichever NAME was
  requested first claimed it — a first call for any other name would have left
  the main "pipeline" logger permanently without a handler, and silent.
"""
import logging

from utils.logger import (
    get_logger,
    reset_warn_once,
    set_level,
    warn_once,
)


def _own_handlers(logger):
    """The handlers THIS module installed — pytest attaches its own capture
    handlers to the same logger, which are not ours to count."""
    return [h for h in logger.handlers
            if type(h).__name__ in ("RichHandler", "StreamHandler")]


class TestHandlerWiring:
    def test_the_pipeline_logger_has_exactly_one_handler(self):
        log = get_logger()
        assert len(_own_handlers(log)) == 1
        get_logger(), get_logger()          # repeated calls must not stack
        assert len(_own_handlers(log)) == 1

    def test_a_second_named_logger_is_configured_too(self):
        """The old single global flag meant only the first name ever got one."""
        other = get_logger("pipeline_test_secondary")
        assert len(_own_handlers(other)) == 1
        assert _own_handlers(get_logger()), "the main pipeline logger stays wired"

    def test_child_loggers_reach_the_shared_handler_once(self):
        get_logger()
        child = logging.getLogger("pipeline.some_stage")
        assert child.handlers == []         # no handler of its own -> no duplicate
        assert child.propagate is True      # ...it reaches the parent's handler

    def test_set_level_targets_the_named_logger(self):
        try:
            set_level(logging.DEBUG)
            assert get_logger().level == logging.DEBUG
        finally:
            set_level(logging.INFO)


class TestWarnOnce:
    def setup_method(self):
        reset_warn_once()

    def test_the_same_fact_is_reported_once(self, caplog):
        log = get_logger()
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            first = warn_once(log, "k", "collision on %s", "width")
            second = warn_once(log, "k", "collision on %s", "width")
        assert first is True and second is False
        assert len(caplog.records) == 1
        assert "collision on width" in caplog.text

    def test_different_facts_are_each_reported(self, caplog):
        log = get_logger()
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            warn_once(log, "a", "first")
            warn_once(log, "b", "second")
        assert len(caplog.records) == 2

    def test_reset_allows_reporting_again(self, caplog):
        log = get_logger()
        with caplog.at_level(logging.WARNING, logger="pipeline"):
            warn_once(log, "k", "msg")
            reset_warn_once()
            warn_once(log, "k", "msg")
        assert len(caplog.records) == 2


class TestCollisionWarningIsDeduped:
    """The 16247 case end-to-end: one collision, one warning — even though the
    resolving function runs several times."""

    def setup_method(self):
        reset_warn_once()

    def test_repeated_resolution_warns_once(self, caplog):
        from pipeline.build_sequencer import _feature_dim_values
        from pipeline.schema import DrawingData, Feature

        model = DrawingData.model_validate({
            "units": "inch", "confidence": 0.9,
            "dimensions": [
                {"id": "D003", "type": "linear", "value": 1.0, "unit": "inch",
                 "applies_to": "flange_width"},
                {"id": "D005", "type": "linear", "value": 2.0, "unit": "inch",
                 "applies_to": "total_flange_width"},
            ],
            "features": [],
        })
        feat = Feature(id="F001", type="extrude_boss", description="base",
                       related_dimensions=["D003", "D005"])
        with caplog.at_level(logging.WARNING):
            for _ in range(4):              # what a real run does
                assert _feature_dim_values(model, feat)["width"] == 2.0
        collisions = [r for r in caplog.records if "colliding dimensions" in r.getMessage()]
        assert len(collisions) == 1
