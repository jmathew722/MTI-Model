"""The canonical per-feature ledger (REFACTOR_ANALYSIS §1.2 / §2.3).

Six artifacts independently tracked feature-level state through the back half of
the pipeline — ``_build_dispositions.json`` (duplicated AGAIN inside
``build_plan.json``'s ``dispositions`` key), ``_reconciliation_report.json``,
``_feature_verification.json``, ``_geometric_loop_report.json``,
``_assist_queue.json`` and ``_deferred_log.json``. Each was written by the stage
that produced it, in that stage's own shape, and anything wanting to answer the
obvious question — *what happened to F004?* — had to open all six and reconcile
them itself (which is exactly what :mod:`pipeline.summary_view` was doing).

This module is the one place that question is answered. One record per feature
id, holding an **append-only, stage-tagged history**:

    {stage, status, basis, timestamp, detail, source}

Ordering is the pipeline's own stage order (:data:`STAGE_ORDER`), so
``record.current`` is the latest word on a feature and ``record.history`` is how
it got there. Nothing is overwritten: a feature BUILT by the sequencer, measured
MISPLACED by Stage 10.6 and then corrected by Stage 10.7 has all three entries,
in order.

**Migration posture (deliberately incremental).** The six artifacts are still
written by their stages exactly as before — this ledger is *built from* them by
:func:`build_ledger`, which makes it a pure, side-effect-free view that can be
adopted one consumer at a time and verified against frozen goldens. Consumers
migrate to the ledger (``summary_view`` first, since it was already doing this
reconciliation by hand); each source file then becomes a filtered view of the
ledger, or is retired, once nothing reads it directly. The ledger can also be
persisted (:func:`write_ledger` → ``<Part>_feature_ledger.json``) as the single
artifact a human or an agent should open first.

Public: :class:`LedgerEntry`, :class:`FeatureRecord`, :class:`FeatureLedger`,
:func:`build_ledger`, :func:`write_ledger`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Pipeline stage order — the ledger's sort key, so a feature's history reads in
# the order the pipeline actually touched it.
STAGE_SEQUENCER = "build_sequencer"          # 6.5  disposition table
STAGE_MACRO = "macro_generation"             # 7    emitted build step
STAGE_PREVALIDATE = "cq_prevalidation"       # 8    headless CadQuery build
STAGE_COM_BUILD = "com_build"                # 9    SolidWorks COM outcome
STAGE_DEFERRED = "deferred_retry"            # 9    quarantine + retry ladder
STAGE_RECONCILE = "reconciliation"           # 10.5 checklist vs build
STAGE_FEATURE_VERIFY = "feature_verification"  # 10.6 measured against the plan
STAGE_GEOMETRIC_LOOP = "geometric_loop"      # 10.7 correction iterations
STAGE_ASSIST = "human_assist"                # 10.8 escalated question

STAGE_ORDER: tuple[str, ...] = (
    STAGE_SEQUENCER, STAGE_MACRO, STAGE_PREVALIDATE, STAGE_COM_BUILD,
    STAGE_DEFERRED, STAGE_RECONCILE, STAGE_FEATURE_VERIFY, STAGE_GEOMETRIC_LOOP,
    STAGE_ASSIST,
)

LEDGER_SUFFIX = "_feature_ledger.json"

# Terminal-ish statuses carried over from the disposition table, kept verbatim so
# the ledger never invents a vocabulary the rest of the pipeline doesn't use.
BUILT = "BUILT"
BUILT_WITH_DERIVED_VALUE = "BUILT_WITH_DERIVED_VALUE"
EXCLUDED_INCOMPLETE = "EXCLUDED_INCOMPLETE"
NEEDS_HUMAN_INPUT = "NEEDS_HUMAN_INPUT"


def _stage_rank(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER)


@dataclass
class LedgerEntry:
    """One stage's word on one feature. Never mutated after it is appended."""

    stage: str
    status: str
    basis: str = ""
    detail: str = ""
    timestamp: str = ""
    source: str = ""                    # artifact this entry was read from
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"stage": self.stage, "status": self.status}
        if self.basis:
            out["basis"] = self.basis
        if self.detail:
            out["detail"] = self.detail
        if self.timestamp:
            out["timestamp"] = self.timestamp
        if self.source:
            out["source"] = self.source
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class FeatureRecord:
    """Everything the pipeline knows about one feature, in stage order."""

    feature_id: str
    feature_type: str = ""
    history: list[LedgerEntry] = field(default_factory=list)

    def append(self, entry: LedgerEntry) -> None:
        self.history.append(entry)

    @property
    def current(self) -> Optional[LedgerEntry]:
        """The latest entry in stage order (the feature's current state)."""
        return self.history[-1] if self.history else None

    @property
    def status(self) -> str:
        cur = self.current
        return cur.status if cur else ""

    def at(self, stage: str) -> Optional[LedgerEntry]:
        """This feature's entry for one stage (the last, if a stage spoke twice)."""
        found = [e for e in self.history if e.stage == stage]
        return found[-1] if found else None

    def status_at(self, stage: str, default: str = "") -> str:
        entry = self.at(stage)
        return entry.status if entry else default

    @property
    def disposition_state(self) -> str:
        """The build-sequencer disposition — the state the rest of the pipeline
        means by "BUILT / derived / excluded"."""
        return self.status_at(STAGE_SEQUENCER)

    @property
    def verification_verdict(self) -> str:
        """The Stage 10.6 measured verdict (OK / MISSING / MISPLACED / …)."""
        return self.status_at(STAGE_FEATURE_VERIFY)

    @property
    def needs_human_input(self) -> bool:
        entry = self.at(STAGE_ASSIST)
        return bool(entry and entry.status == NEEDS_HUMAN_INPUT)

    def sort_history(self) -> None:
        self.history.sort(key=lambda e: _stage_rank(e.stage))

    def merge_duplicates(self) -> None:
        """Collapse the same fact reported by two artifacts into one entry.

        Several stages write the SAME state to more than one file — a pending
        assist question appears both as the ``NEEDS_HUMAN_INPUT`` overlay on the
        disposition table and as the question in the assist queue. That is one
        fact with two sources, not two events, and listing it twice makes a
        feature's history read as if something happened twice.

        Rule: within one stage, one entry per distinct status. The RICHEST entry
        wins (longest detail — the assist queue's actual question beats the
        overlay's bare marker) and every contributing artifact is preserved in
        ``source`` so nothing about provenance is lost. Genuinely repeated events
        keep their own entries, because they differ in status or stage (a retry
        that fails then recovers, for example).
        """
        best: dict[tuple[str, str], LedgerEntry] = {}
        order: list[tuple[str, str]] = []
        for e in self.history:
            key = (e.stage, e.status)
            cur = best.get(key)
            if cur is None:
                best[key] = e
                order.append(key)
                continue
            winner, loser = (e, cur) if len(e.detail) > len(cur.detail) else (cur, e)
            sources = [s for s in (winner.source, loser.source) if s]
            winner.source = " + ".join(dict.fromkeys(sources))
            if not winner.basis and loser.basis:
                winner.basis = loser.basis
            for k, v in (loser.data or {}).items():
                winner.data.setdefault(k, v)
            best[key] = winner
        self.history = [best[k] for k in order]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "status": self.status,
            "stage": (self.current.stage if self.current else ""),
            "history": [e.to_dict() for e in self.history],
        }


@dataclass
class FeatureLedger:
    """Every feature of one part, keyed by feature id."""

    part: str = ""
    features: dict[str, FeatureRecord] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    # -- writing ---------------------------------------------------------- #
    def record(self, feature_id: str, stage: str, status: str, *, basis: str = "",
               detail: str = "", feature_type: str = "", source: str = "",
               data: Optional[dict[str, Any]] = None,
               timestamp: Optional[str] = None) -> LedgerEntry:
        """Append one stage's word on one feature. Never overwrites."""
        fid = str(feature_id or "").strip()
        if not fid:
            raise ValueError("a ledger entry needs a feature id")
        rec = self.features.get(fid)
        if rec is None:
            rec = FeatureRecord(feature_id=fid, feature_type=feature_type)
            self.features[fid] = rec
        elif feature_type and not rec.feature_type:
            rec.feature_type = feature_type
        entry = LedgerEntry(stage=stage, status=str(status or ""), basis=basis,
                            detail=detail, source=source, data=dict(data or {}),
                            timestamp=timestamp or "")
        rec.append(entry)
        return entry

    # -- reading ---------------------------------------------------------- #
    def get(self, feature_id: str) -> Optional[FeatureRecord]:
        return self.features.get(str(feature_id))

    def __contains__(self, feature_id: object) -> bool:
        return str(feature_id) in self.features

    def __len__(self) -> int:
        return len(self.features)

    def ids(self) -> list[str]:
        return sorted(self.features)

    def with_status(self, status: str, stage: Optional[str] = None) -> list[FeatureRecord]:
        """Every feature whose current status (or status at ``stage``) matches."""
        out = []
        for rec in self.features.values():
            got = rec.status_at(stage) if stage else rec.status
            if got == status:
                out.append(rec)
        return sorted(out, key=lambda r: r.feature_id)

    def open_items(self) -> list[FeatureRecord]:
        """Features that ended in a state a human should look at — excluded,
        unresolved, mismeasured, deferred open, or awaiting an answer."""
        bad = {EXCLUDED_INCOMPLETE, NEEDS_HUMAN_INPUT, "MISSING", "MISPLACED",
               "WRONG_SIZE", "EXTRA", "deferred_open", "unresolved", "FAILED"}
        return [r for r in sorted(self.features.values(), key=lambda r: r.feature_id)
                if r.status in bad or r.needs_human_input]

    def finalize(self) -> "FeatureLedger":
        for rec in self.features.values():
            rec.sort_history()
            rec.merge_duplicates()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stage_order": list(STAGE_ORDER),
            "sources": self.sources,
            "feature_count": len(self.features),
            "open_count": len(self.open_items()),
            "features": [self.features[f].to_dict() for f in self.ids()],
        }


# --------------------------------------------------------------------------- #
# Building the ledger from the artifacts each stage already writes
# --------------------------------------------------------------------------- #
def _load(path: Optional[Path]) -> Any:
    if path is None or not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _first(out: Path, pattern: str, exclude: tuple[str, ...] = ()) -> Optional[Path]:
    for p in sorted(out.glob(pattern)):
        if any(x in p.name for x in exclude):
            continue
        return p
    return None


def _artifact(out: Path, *patterns: str) -> Optional[Path]:
    """First matching artifact across several naming conventions.

    The vision pipeline prefixes artifacts with the part name
    (``158-C_build_plan.json``); the DWG-native pipeline writes them bare
    (``build_plan.json``). Both are first-class products (docs/DWG_PATHS.md), so
    the ledger reads both rather than being a vision-only view.
    """
    for pat in patterns:
        hit = _first(out, pat)
        if hit is not None:
            return hit
    return None


def build_ledger(output_dir: Path | str, part: str = "") -> FeatureLedger:
    """Assemble the ledger for one part's output directory.

    Pure and total: every artifact is optional, an unreadable one is skipped, and
    a part that only got as far as the build sequencer still produces a valid
    ledger. Nothing here recomputes pipeline decisions — it only re-keys what the
    stages already recorded onto the feature they are about.
    """
    out = Path(output_dir)
    ledger = FeatureLedger(part=part or out.name)

    plan = _load(_artifact(out, "*_build_plan.json", "build_plan.json")) or {}
    dispositions = _load(_artifact(out, "*_build_dispositions.json",
                                   "build_dispositions.json"))
    if not isinstance(dispositions, list):
        dispositions = plan.get("dispositions") if isinstance(plan, dict) else None
    if not isinstance(dispositions, list):
        dispositions = []

    # 6.5 — the disposition table: the state everything downstream starts from.
    if dispositions:
        ledger.sources.append("build_dispositions")
    for d in dispositions:
        fid = d.get("feature_id")
        if not fid:
            continue
        ledger.record(
            fid, STAGE_SEQUENCER, d.get("state") or "",
            basis=d.get("derivation_source") or "",
            detail=d.get("reason") or d.get("missing") or "",
            feature_type=d.get("feature_type") or d.get("type") or "",
            source="build_dispositions.json",
            data={k: v for k, v in (
                ("stage_name", d.get("stage_name")),
                ("flags", d.get("flags")),
                ("position_xy", d.get("position_xy")),
                ("values_used", d.get("values_used")),
            ) if v not in (None, [], {})},
        )
        if d.get("human_input_state") == NEEDS_HUMAN_INPUT:
            ledger.record(fid, STAGE_ASSIST, NEEDS_HUMAN_INPUT,
                          detail="disposition overlay", source="build_dispositions.json")

    # 7 — the emitted macro step.
    for step in (plan.get("steps") or []):
        fid = step.get("feature_id")
        if not fid or fid == "-":
            continue
        ledger.record(
            fid, STAGE_MACRO, str(step.get("status") or "generated"),
            detail=str(step.get("description") or ""),
            feature_type=str(step.get("type") or ""),
            source="build_plan.json",
            data={k: v for k, v in (
                ("macro_file", step.get("macro_file")),
                ("seq", step.get("seq")),
                ("construction_method", step.get("construction_method")),
                ("positions_xy", step.get("positions_xy")),
            ) if v not in (None, [], {})},
        )
    if plan.get("steps"):
        ledger.sources.append("build_plan")

    # 9 — the COM build's per-feature outcome (macro_result.json, JSON Lines).
    _ingest_macro_result(out, ledger)

    # 9 — deferred-feature quarantine + retry ladder.
    deferred = _load(out / "_deferred_log.json") or {}
    for item in (deferred.get("items") or []):
        fid = item.get("feature_id")
        if not fid:
            continue
        ledger.record(
            fid, STAGE_DEFERRED,
            "recovered" if item.get("recovered") else "deferred_open",
            basis=item.get("error_class") or "",
            detail=(item.get("error_text") or "")[:300],
            feature_type=item.get("feature_type") or "",
            source="_deferred_log.json",
            data={"attempts": item.get("attempts") or []},
        )
    if deferred:
        ledger.sources.append("deferred_log")

    # 10.5 — reconciliation: only the unresolved items are per-feature.
    recon = _load(_artifact(out, "*_reconciliation_report.json")) or {}
    for u in (recon.get("unresolved") or []):
        fid = u.get("feature_id")
        if not fid:
            continue
        # `issue` is the field the reconciler actually writes (verified against
        # real reports, 2026-08-16) — it carries the finding ("extraction
        # describes 4 instance(s) but only 1 made it into the build plan"), which
        # is the useful half; `resolution_attempted` is the fallback narrative.
        ledger.record(fid, STAGE_RECONCILE, u.get("status") or "unresolved",
                      detail=u.get("issue") or u.get("reason")
                      or u.get("resolution_attempted") or "",
                      feature_type=u.get("feature_type") or "",
                      source="reconciliation_report.json",
                      data={"resolution_attempted": u.get("resolution_attempted")}
                      if u.get("issue") and u.get("resolution_attempted") else {})
    for fid in (recon.get("splices_applied") or []):
        ledger.record(fid, STAGE_RECONCILE, "spliced",
                      detail="recovered by re-resolution and spliced into the build plan",
                      source="reconciliation_report.json")
    if recon:
        ledger.sources.append("reconciliation_report")

    # 10.6 — per-feature geometric verification.
    fverify = _load(_artifact(out, "*_feature_verification.json")) or {}
    for fv in (fverify.get("features") or []):
        fid = fv.get("feature_id") or fv.get("id")
        if not fid:
            continue
        ledger.record(
            fid, STAGE_FEATURE_VERIFY,
            str(fv.get("classification") or fv.get("status") or ""),
            detail=str(fv.get("reason") or ""),
            source="feature_verification.json",
            data={k: v for k, v in (("measured", fv.get("measured")),
                                    ("expected", fv.get("expected"))) if v is not None},
        )
    if fverify:
        ledger.sources.append("feature_verification")

    # 10.7 — geometric correction loop.
    loop = _load(_artifact(out, "*_geometric_loop_report.json")) or {}
    for u in (loop.get("unresolved") or []):
        fid = u.get("feature_id")
        if not fid:
            continue
        ledger.record(fid, STAGE_GEOMETRIC_LOOP, u.get("class") or "unresolved",
                      detail=loop.get("stopped_reason") or "",
                      source="geometric_loop_report.json")
    if loop:
        ledger.sources.append("geometric_loop_report")

    # 10.8 — human-assist questions.
    assist = _load(_artifact(out, "*_assist_queue.json")) or {}
    for q in (assist.get("questions") or []):
        fid = q.get("feature_id")
        if not fid:
            continue
        answered = q.get("status") == "answered"
        ledger.record(
            fid, STAGE_ASSIST, "answered" if answered else NEEDS_HUMAN_INPUT,
            detail=str(q.get("question_text") or ""),
            source="assist_queue.json",
            data={k: v for k, v in (
                ("default_if_unanswered", q.get("default_if_unanswered")),
                ("answer", q.get("answer")),
            ) if v not in (None, "")},
        )
    if assist:
        ledger.sources.append("assist_queue")

    return ledger.finalize()


def _ingest_macro_result(out: Path, ledger: FeatureLedger) -> None:
    """``logs/macro_result.json`` is JSON Lines written BY the macros as they
    run — one line per feature outcome. Tolerates both the logs/ location and a
    flat copy, and skips malformed lines rather than losing the whole file."""
    for candidate in (out / "logs" / "macro_result.json", out / "macro_result.json"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        found = False
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            fid = rec.get("feature_id") or rec.get("feature") or rec.get("id")
            if not fid:
                continue
            status = rec.get("status") or rec.get("result") or (
                "success" if rec.get("success") else "failed")
            ledger.record(str(fid), STAGE_COM_BUILD, str(status),
                          detail=str(rec.get("error") or rec.get("detail") or ""),
                          source="macro_result.json")
            found = True
        if found:
            ledger.sources.append("macro_result")
        return


def write_ledger(output_dir: Path | str, prefix: str,
                 ledger: Optional[FeatureLedger] = None) -> Optional[Path]:
    """Persist ``<prefix>_feature_ledger.json``. Never raises — the ledger is a
    view over artifacts that are already on disk, so failing to write it must
    never cost a run its outputs."""
    out = Path(output_dir)
    try:
        led = ledger or build_ledger(out, part=prefix)
        path = out / f"{prefix}{LEDGER_SUFFIX}"
        path.write_text(json.dumps(led.to_dict(), indent=2), encoding="utf-8")
        return path
    except Exception:  # pragma: no cover - defensive by design
        return None
