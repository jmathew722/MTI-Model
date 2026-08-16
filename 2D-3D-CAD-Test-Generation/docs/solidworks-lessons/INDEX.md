# SolidWorks lessons — empirically verified

Hands-on results from driving live **SolidWorks 2026 rev 34.3.2** over
`win32com`. Everything here was executed and measured; nothing is quoted from
documentation. Lab notebook: `experiments/solidworks_practice/`.

| File | One-line summary |
|---|---|
| [MANUAL.md](MANUAL.md) | **Start here.** Five rules for writing SolidWorks COM code that works first time, plus the measured placement reference and a generation checklist. |
| [01_com_access_forms.md](01_com_access_forms.md) | Late binding exposes members as method, property, or raw-Invoke-only — per member; plus the enum values that differ from the obvious guess. |
| [02_cut_and_hole_placement.md](02_cut_and_hole_placement.md) | The verified cut recipe, and measured hole/edge-notch placement on all four edges of one block. |
| [03_revolve_patterns_mirror_shell.md](03_revolve_patterns_mirror_shell.md) | Revolve verified (360°/270°, +second body); circular pattern RESOLVED with the Mark answer; two centerlines do not fail a revolve. |
| [05_multifeature_part_and_validation.md](05_multifeature_part_and_validation.md) | Tier 4: a bracket built in plan order, graded PASS by `pipeline.validation`, and the hole audit catching a 2x diameter. |
| [06_bolt_circles_and_remaining_gaps.md](06_bolt_circles_and_remaining_gaps.md) | Bolt circle two ways — one sketch beats a pattern; shell confirmed absent; linear pattern still unexplained. |
| [04_deliberate_failures.md](04_deliberate_failures.md) | Tier 5, 5/5: what a unit slip, an open sketch, an oversized fillet, a detached boss and a stale name actually do. |
| [07_the_selection_is_fragile.md](07_the_selection_is_fragile.md) | A rebuild between select and call clears the selection and disconnects held pointers — it faked two published findings, and E020 is retracted. |
| [08_auditing_the_emitted_macros.md](08_auditing_the_emitted_macros.md) | Tier 6: the manual turned back on this pipeline's own macros; the return value is trustworthy, and an edge treatment needs R < thickness/2. |
| [09_linear_patterns_solved.md](09_linear_patterns_solved.md) | Tier 7: linear patterns build — it was the selection form, not the API; two stacked defects fixed in `build_pattern`; every enum fallback audited. |

## What was measured

* One part, iterated: `experiments/solidworks_practice/parts/practice_master.sldprt`
  — a 6.0 × 4.0 × 0.5 in block, 5 holes, 4 edge notches, all added to the SAME
  file and measured after each step.
* **10 of 12 placements verified** to 3 dp against the live model. The 2
  non-verifications are the Top/Right-plane probes, which is itself the finding
  recorded as E015.
* Tier 2 (`t2_revolve_pattern_mirror_shell.py`): **5 of 10 verified**, using
  add → measure → **delete** so the same part hosts every experiment. Revolve
  measured exactly (270° = 0.75 × the 360° volume); patterns and shell did NOT
  build in the lab and the gaps are recorded rather than glossed.
* Iteration 6 narrowed the pattern gap to reference-axis creation; **iteration 7
  closed it** — two stacked bugs (bore-face search matching the outer wall, and
  `callable()` mis-detecting a property). Circular patterns now build, and the
  correct-vs-wrong **Mark comparison is measured**: Mark 4 → +3 instances,
  Mark 1 → `None` and nothing, neither raising.
* Tier 5 complete: **5/5** deliberate failures reproduced and characterised.
* Tier 4 complete: a multi-feature bracket (base -> boss on a reference plane
  -> notch -> 4 holes -> fillets last), graded **PASS** by the real
  `pipeline.validation` scorecard, with the hole audit **catching** a
  deliberately doubled hole diameter.
* Iteration 9 closed the Tier-3 bolt-circle comparison: **four circles in one
  sketch + one cut** places all four holes exactly, with one feature and no
  prerequisites — independently confirming the construction this pipeline
  already uses. Shell is confirmed absent (no dispid, five name variants);
  linear patterns remain unexplained **at this point** (closed in iteration 19 — see below).

* Iterations 10–15 ([lesson 07](07_the_selection_is_fragile.md)) chased a
  contradiction — the same fillet call building in one script and returning
  `None` in another — down to **a rebuild between "select" and "call" silently
  clearing the selection and disconnecting held edge pointers** (E022). That bug
  was in this lab's own harness, and it had manufactured two published findings:
  **E020 is retracted** (all-edges fillets work, including over hole edges) and
  iteration 9's single-edge fillet gap is closed. A third near-miss is recorded
  as E023 — a sweep that stopped at its first success almost got working
  pipeline code "fixed". The pipeline itself was audited and is unaffected.

* Iterations 16–17 ([lesson 08](08_auditing_the_emitted_macros.md)) turned the
  manual back on **this pipeline's own emitted macros**, auditing the checked-in
  golden package rule by rule. Most of it came back clean, including the
  `MsgBox` question (those macros are human-run by design, so a modal error is
  correct) and the interactive fillet macro's bare `Is Nothing` check — which a
  10-radius sweep proved sufficient, since "returned a Feature" and "the volume
  changed" agreed every time. That sweep also produced **E024**: an edge
  treatment must be strictly less than half the material thickness (R0.24 builds
  on a 0.5 plate, R0.26 silently does not), now checked at plan time by
  `validator._check_edge_treatment_radius`.

* Iterations 18–21 ([lesson 09](09_linear_patterns_solved.md)) closed the last
  open capability gap. **Linear patterns build** — the API was never missing, the
  SELECTION FORM was wrong: the direction edge needs
  `Extension.SelectByID2(…, "EDGE", x, y, z, …, Mark 1)`, where `IEdge::Select4`
  returns True and is unusable. `build_pattern` had TWO stacked defects (seed at
  Mark 0 with no direction reference; an 18-argument call that needs 20), the
  second hidden behind the first. Fixed and verified through the shipping code
  path, holes 1 → 3. A tidy explanation — "the Mark is the mechanism" — was
  predicted, tested and DISPROVED; the asymmetry stays recorded as unexplained.
  The same tier audited every hardcoded enum fallback against the live library
  (all ten agree, E025) and pinned them with a CI-safe test.

## New entries added to the error ledger
E012–E025 in [`../solidworks-macro-error-log.md`](../solidworks-macro-error-log.md),
one of them (E020) retracted and kept as a worked example of a false finding.
**Every live failure mode is silent** — a feature object comes back, or `None`
comes back with no exception, and the model reports clean. That is the single
most important pattern this lab established: on this API, "it did not throw" is
not evidence of anything. Its corollary, learned the hard way in lesson 07, is
that `None` means "this did not work" and never says *why* — so a harness bug
and a genuine API limit look identical until you run a controlled comparison.
