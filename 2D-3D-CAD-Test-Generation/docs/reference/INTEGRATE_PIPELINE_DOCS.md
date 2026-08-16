# Task: Triage, Plan, and Integrate the 11 Pipeline Reference Docs

## Context

The repo already has a working 2D-to-3D pipeline: FastAPI app, Claude Vision extraction, Stage 2.5
ambiguity resolution, a mature direct-COM build engine (`pipeline/solidworks_builder.py`, ~1885
lines), a secondary VBA generator (`pipeline/macro_generator.py`), a canonical coordinate resolver
(`pipeline/coordinate_normalize.py`), and a dated error log (`docs/solidworks-macro-error-log.md`,
entries E001-E011).

Eleven new reference docs have been provided (paths below). They describe best practices for an
AI-driven 2D-to-3D SolidWorks pipeline written independently of this repo. They are NOT a spec to
follow blindly — they are a second, more rigorous pass on problems this repo has already partly
solved. Your job is to figure out what is genuinely missing or weak here, plan the integration, then
build it.

## Reference docs (place in `docs/reference/` if not already there)

```
00_README_pipeline_overview.md
01_language_choice_csharp_over_vba.md
02_solidworks_api_fundamentals.md
03_macro_writing_best_practices.md
04_reading_engineering_drawings.md
05_2d_to_3d_interpretation_strategy.md
06_geometry_construction_playbook.md
07_validation_and_self_checking.md
08_autonomous_error_handling.md
09_user_interaction_protocol.md
10_common_failure_modes.md
```

## Phase 0 — Read everything before touching code

Read all 11 docs in full. Then read, in full:
- `pipeline/solidworks_builder.py`
- `pipeline/macro_generator.py`
- `pipeline/coordinate_normalize.py`
- `pipeline/batch.py`
- `main.py`
- `docs/solidworks-macro-error-log.md`
- the extraction/Stage-2.5 prompt code (wherever the drawing→JSON and JSON→plan prompts live)

Do not write any plan or code until both passes are done.

## Phase 1 — Triage

Produce `docs/reference/TRIAGE.md`. For each of the 11 docs, score it against the existing repo in
one row of a table:

| Doc | Verdict | Why |
|---|---|---|
| 01 language choice | CONFLICT — flag, do not act | repo is Python + win32com + VBA, not C#/.NET |
| 02 API fundamentals | ADAPT | principles apply, examples are C# and need Python/win32com translation |
| ... | ... | ... |

Verdict must be one of:
- **ALREADY IMPLEMENTED** — repo already does this; note where, cite the file/function.
- **ADAPT** — the principle is right, the code examples are C# and must be translated to the repo's
  Python/win32com idiom before use.
- **NEW CAPABILITY** — repo has nothing like this; genuinely new work.
- **CONFLICT — flag, do not act** — the doc recommends something that contradicts an existing,
  working architectural decision in this repo. Do not implement. Write the tradeoff up for Joe to
  decide, and default to leaving current architecture in place.

Rank the NEW CAPABILITY and ADAPT items by leverage (impact on part-build correctness /
effort). This ranking drives Phase 2.

**Known conflict to handle exactly this way:** doc 01 recommends a standalone C# app instead of
VBA/COM. This repo already has a working direct-COM Python build engine as the PRIMARY path (per
`main.py --engine com`), with VBA as a secondary portable/review deliverable. Do not rewrite the
build engine in C#. Extract the underlying principles that transfer regardless of language (explicit
unit-conversion boundary, checked return values instead of silent failures, no `SendKeys`/screen-
coordinate automation, deterministic selection over recorded-macro name reuse, structured plan JSON
as the interpreter input) and apply those to the Python code. Write the C# question up as a single
flagged item in the final report — do not decide it yourself.

## Phase 2 — Project plan

Produce `docs/reference/INTEGRATION_PLAN.md`: a phased, dependency-ordered plan covering every item
triaged as NEW CAPABILITY or ADAPT. Suggested phase order (adjust based on what Phase 1 actually
finds):

1. **Validation & self-checking layer** (doc 07) — this is almost certainly net-new and highest
   leverage: rebuild-health check, single-solid-body assertion, bounding-box check against expected
   dims, mass-properties sanity (volume ratio, center-of-mass symmetry), a hole audit via cylindrical
   face enumeration, and a `validation.json` scorecard with a PASS / PASS_WITH_ASSUMPTIONS / FAIL
   verdict. Build as `pipeline/validation.py`, called at the end of every build in `batch.py`.
2. **Autonomous error-handling ladder** (doc 08) — formalize the escalation order (deterministic fix
   → alternative construction → logged default → defer to user at the end), a per-step retry budget
   (max 3 attempts, no identical retries), and the failure-symptom-to-cause lookup table. Check
   whether Stage 2.5's ambiguity resolution already covers part of this — if so, extend it rather than
   duplicating it.
3. **Drawing/plan JSON schema alignment** (docs 04, 05) — diff the existing extraction JSON schema
   and build-plan schema against `drawing.json` / `plan.json` in these docs. Adopt any fields the
   repo is missing that materially improve traceability (per-step `evidence` field tying each
   feature to the source dimension/view, an `assumptions[]` list with confidence levels, explicit
   part-archetype classification, projection-angle handling). Do not break existing consumers of the
   current schema; add fields, don't rename working ones without updating every call site.
4. **Macro/COM code audit against the anti-pattern table** (doc 03) — grep `solidworks_builder.py`
   and `macro_generator.py` for: `SendKeys`, magic integers where `swconst` enums should be used,
   any bare/broad exception swallowing, hardcoded selection by default feature names (`Boss-
   Extrude1` etc.), and unchecked return values on feature-creation calls. Fix what's found, or if
   the audit finds the existing code is already clean on a given anti-pattern, say so in the report
   instead of manufacturing busywork.
5. **Common-failure-mode merge** (doc 10) — reconcile with `docs/solidworks-macro-error-log.md`
   (E001-E011). Merge doc 10's environment/COM/geometry failure tables into that log rather than
   creating a second competing failure-reference file. Keep one canonical troubleshooting doc.
6. **End-of-run delivery report** (doc 09) — a single structured report emitted after validation:
   result summary, ranked assumption list (lowest confidence first), unresolved failures, one
   compact ask. Check whether the existing "verification reports" UI tab already renders something
   like this — if so, extend its data contract rather than building a parallel format.

For each phase, the plan must state: files touched, new files created, how it will be tested, and
what "done" looks like. Do not proceed to Phase 3 until this plan exists as a committed file.

## Phase 3 — Implement, in the order the plan sets

For each phase:
1. Implement.
2. Test against a real sample drawing already in the repo (use the flange drawing A050211E work
   already done, or the most recently tested sample if that's stale) — run it through the actual
   pipeline end to end, not a synthetic unit test only.
3. Confirm the phase's "done" criteria from the plan are met before moving to the next phase.
4. Update `docs/reference/INTEGRATION_PLAN.md` to check off the completed phase with a one-line
   result note (pass/fail, what broke, what got fixed).

If a phase's implementation surfaces a design question that materially changes part output (not a
code bug — those you fix yourself, always), log it the way doc 08/09 describe: a defaulted,
confidence-tagged assumption, not a mid-run question. Collect every such item for the final report.

## Phase 4 — Final report

At the end, produce one summary (in the PR description / final chat message, not a new file) covering:
- What was implemented, phase by phase, with pass/fail per phase.
- The single flagged architectural decision (C# vs current Python/COM/VBA) with a one-paragraph
  recommendation, clearly marked as Joe's call, not applied.
- Any other CONFLICT-verdict items from Phase 1 that were not auto-resolved.
- Any assumptions logged during implementation testing.

## Working rules

- Everything through Phase 3 runs autonomously. Do not stop to ask permission between phases.
- Do not touch build-engine language/architecture (Phase "1 CONFLICT" item) without explicit sign-off.
- Prefer extending existing modules over creating parallel ones; the repo already has real,
  battle-tested code — the goal is closing gaps, not a rewrite.
- Every new module gets tested against a real drawing before being marked done.
- Commit at the end of each phase with a clear message, and push to GitHub at the end of the full run.
