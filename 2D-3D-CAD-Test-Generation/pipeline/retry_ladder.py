"""The ONE bounded-retry contract (2026-08-15 refactor, REFACTOR_ANALYSIS §1.5).

Three stages of this pipeline independently re-implemented the same loop shape —
Stage 10.5 reconciliation (re-run the resolver, cap 3), Stage 10.7 geometric
correction (rebuild via COM, cap 3), and Workstream 1 deferred retry (re-attempt
failed features, cap 3). Each one had its own cap check, its own "made no
progress, stop deterministically" rule, and its own idea of what oscillation
means. Same guarantee, three subtly different implementations, three places for
it to rot.

This module owns that guarantee. Callers keep their domain logic (what an
attempt IS is completely different in each case) and hand it to :func:`run_ladder`
as a callback; the ladder owns ONLY the termination policy:

    * **cap** — never more than ``cap`` passes, ever;
    * **completion** — an attempt that reports ``done`` stops immediately;
    * **oscillation** — a unit that PASSED on an earlier pass and fails now stops
      the loop rather than thrashing (opt-out with ``stop_on_oscillation=False``);
    * **no progress** — an attempt that changes nothing stops the loop instead of
      burning the remaining cap (deterministic work cannot get luckier by
      repeating; opt out with ``stop_on_no_progress=False`` for ladders whose
      passes escalate STRATEGY rather than re-run the same computation);
    * **exhaustion** — an attempt that reports it has no untried strategy left
      always stops;
    * **error** — a raising/erroring attempt stops the loop and is recorded, never
      swallowed.

Every pass appends one structured entry to a ledger, so each caller's report
file keeps its existing per-pass audit trail.

The pipeline invariant is unchanged and enforced here: a ladder that runs out of
passes STOPS LOUDLY — the result always names why it stopped, and never reports
success it did not achieve.

Public: :class:`PassResult`, :class:`LadderResult`, :func:`run_ladder`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from utils.logger import get_logger

log = get_logger()

DEFAULT_CAP = 3

# Stop classes (the complete set — a ladder always ends in exactly one).
STOP_COMPLETED = "completed"
STOP_CAP = "cap"
STOP_NO_PROGRESS = "no_progress"
STOP_OSCILLATION = "oscillation"
STOP_EXHAUSTED = "exhausted"
STOP_ERROR = "error"
STOP_NOTHING_TO_DO = "nothing_to_do"


@dataclass
class PassResult:
    """What one attempt reports back to the ladder.

    ``progressed``  the attempt changed something real (a feature recovered, a
                    correction applied, a gap closed). No progress ends the loop
                    under the default policy.
    ``done``        the work is complete; stop with :data:`STOP_COMPLETED`.
    ``exhausted``   no untried strategy remains; stop once nothing progressed.
    ``ok_ids``      the units currently passing. Supplying it on every pass turns
                    on oscillation detection (a unit leaving this set regresses).
    ``error``       a failure that must stop the ladder (recorded, never hidden).
    ``entry``       caller-owned fields merged into this pass's ledger entry.
    """

    progressed: bool = False
    done: bool = False
    exhausted: bool = False
    ok_ids: Optional[Iterable[str]] = None
    regressed: Optional[list[str]] = None
    detail: str = ""
    error: str = ""
    entry: dict[str, Any] = field(default_factory=dict)


@dataclass
class LadderContext:
    """Handed to each attempt so it can consult the ladder's policy MID-pass.

    Oscillation is the one termination rule an attempt may need to know about
    before it finishes: once a regression exists, everything the pass would do
    next (apply a correction, rebuild, log it as progress) is work whose results
    get thrown away. :meth:`regressed` answers "did anything that passed before
    fail now?" using the ladder's own definition — the policy stays in one place;
    only the moment of asking belongs to the caller.
    """

    pass_num: int
    prev_ok: frozenset[str] = frozenset()
    has_history: bool = False

    def regressed(self, ok_ids: Iterable[str]) -> list[str]:
        """Units that passed on an earlier pass and do not pass now (sorted).
        Always empty on the first pass — there is no history to regress from."""
        if not self.has_history:
            return []
        now = {i for i in ok_ids if i is not None}
        return sorted(i for i in self.prev_ok - now if i is not None)


@dataclass
class LadderResult:
    """How the ladder ended. ``stop_class`` is one of the ``STOP_*`` constants."""

    passes_used: int = 0
    stop_class: str = STOP_NOTHING_TO_DO
    stopped_reason: str = ""
    ledger: list[dict[str, Any]] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return self.stop_class == STOP_COMPLETED

    def as_dict(self) -> dict[str, Any]:
        return {
            "passes_used": self.passes_used,
            "stop_class": self.stop_class,
            "stopped_reason": self.stopped_reason,
            "regressed": self.regressed,
            "ledger": self.ledger,
        }


_DEFAULT_REASONS: dict[str, str] = {
    STOP_COMPLETED: "all work completed",
    STOP_CAP: "iteration cap ({cap}) reached",
    STOP_NO_PROGRESS: (
        "pass {pass_num} changed nothing — stopping rather than repeating "
        "identical work (no new information is available)"
    ),
    STOP_OSCILLATION: (
        "oscillation — {regressed} regressed after a correction; stopped to "
        "avoid thrashing"
    ),
    STOP_EXHAUSTED: "every available strategy has been tried",
    STOP_ERROR: "attempt failed: {error}",
    STOP_NOTHING_TO_DO: "nothing to retry",
}


def _accepts_context(attempt: Callable[..., PassResult]) -> bool:
    """Whether ``attempt`` wants the :class:`LadderContext` second argument.
    Signature-inspected once per ladder so both call shapes stay supported
    without every caller having to accept an argument it ignores."""
    import inspect

    try:
        params = list(inspect.signature(attempt).parameters.values())
    except (TypeError, ValueError):  # builtins / C callables
        return False
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return True
    return len(positional) >= 2


def run_ladder(
    attempt: Callable[..., PassResult],
    *,
    cap: int = DEFAULT_CAP,
    first_pass: int = 1,
    name: str = "retry",
    stop_on_oscillation: bool = True,
    stop_on_no_progress: bool = True,
    reasons: Optional[dict[str, str]] = None,
) -> LadderResult:
    """Drive ``attempt(pass_num[, ctx]) -> PassResult`` under the bounded-retry
    contract. An attempt may take a second argument to receive a
    :class:`LadderContext` (see its docstring); one-argument attempts are called
    unchanged.

    ``cap`` passes maximum (``cap <= 0`` runs none and reports
    :data:`STOP_NOTHING_TO_DO`). ``first_pass`` is the number given to the first
    pass — the deferred-feature queue numbers its retries from 2 because pass 1
    was the original build attempt. ``reasons`` overrides the human-readable stop
    text per class (each caller keeps the wording its report already uses);
    ``{cap}``/``{pass_num}``/``{regressed}``/``{error}`` are substituted.

    Termination order within a pass is fixed and deliberate: error, then
    oscillation, then completion, then no-progress/exhaustion. Oscillation is
    checked BEFORE completion so a pass that both regresses one unit and fixes
    the rest still stops — a regression is never masked by an otherwise-good
    pass.
    """
    texts = {**_DEFAULT_REASONS, **(reasons or {})}
    result = LadderResult()
    prev_ok: set[str] = set()
    have_prev_ok = False

    if cap <= 0:
        result.stopped_reason = texts[STOP_NOTHING_TO_DO]
        return result

    takes_ctx = _accepts_context(attempt)
    for pass_num in range(first_pass, first_pass + cap):
        result.passes_used = pass_num - first_pass + 1
        ctx = LadderContext(pass_num=pass_num, prev_ok=frozenset(prev_ok),
                            has_history=have_prev_ok)
        try:
            outcome = attempt(pass_num, ctx) if takes_ctx else attempt(pass_num)
        except Exception as e:  # an attempt must never take the run down with it
            outcome = PassResult(error=f"{type(e).__name__}: {e}")
        if outcome is None:  # a caller that returns nothing made no progress
            outcome = PassResult()

        entry: dict[str, Any] = {"pass": pass_num, **(outcome.entry or {})}
        if outcome.detail:
            entry.setdefault("detail", outcome.detail)
        result.ledger.append(entry)

        if outcome.error:
            result.stop_class = STOP_ERROR
            result.stopped_reason = texts[STOP_ERROR].format(
                error=outcome.error, cap=cap, pass_num=pass_num, regressed="")
            entry["stop"] = STOP_ERROR
            log.warning("%s ladder: %s", name, result.stopped_reason)
            return result

        ok_ids = set(outcome.ok_ids) if outcome.ok_ids is not None else None
        if stop_on_oscillation:
            # Either the ladder finds the regression itself, or the attempt
            # already found it through LadderContext.regressed and reported it.
            regressed = list(outcome.regressed or [])
            if not regressed and ok_ids is not None and have_prev_ok:
                regressed = sorted(i for i in prev_ok - ok_ids if i is not None)
            if regressed:
                result.stop_class = STOP_OSCILLATION
                result.regressed = regressed
                result.stopped_reason = texts[STOP_OSCILLATION].format(
                    regressed=regressed, cap=cap, pass_num=pass_num, error="")
                entry["stop"] = STOP_OSCILLATION
                entry["oscillation"] = regressed
                log.warning("%s ladder: %s", name, result.stopped_reason)
                return result
        if ok_ids is not None:
            prev_ok, have_prev_ok = ok_ids, True

        if outcome.done:
            result.stop_class = STOP_COMPLETED
            result.stopped_reason = outcome.detail or texts[STOP_COMPLETED]
            entry["stop"] = STOP_COMPLETED
            return result

        if not outcome.progressed and (outcome.exhausted or stop_on_no_progress):
            stop_class = STOP_EXHAUSTED if outcome.exhausted else STOP_NO_PROGRESS
            result.stop_class = stop_class
            result.stopped_reason = outcome.detail or texts[stop_class].format(
                cap=cap, pass_num=pass_num, regressed="", error="")
            entry["stop"] = stop_class
            log.info("%s ladder: %s", name, result.stopped_reason)
            return result

    result.stop_class = STOP_CAP
    result.stopped_reason = texts[STOP_CAP].format(
        cap=cap, pass_num=result.passes_used, regressed="", error="")
    log.info("%s ladder: %s", name, result.stopped_reason)
    return result
