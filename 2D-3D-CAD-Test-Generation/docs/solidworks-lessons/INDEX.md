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
| [04_deliberate_failures.md](04_deliberate_failures.md) | Tier 5, 5/5: what a unit slip, an open sketch, an oversized fillet, a detached boss and a stale name actually do. |

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

## New entries added to the error ledger
E012–E019 in [`../solidworks-macro-error-log.md`](../solidworks-macro-error-log.md).
**Every one of the six fails silently** — a feature object comes back, or `None`
comes back with no exception, and the model reports clean. That is the single
most important pattern this lab established: on this API, "it did not throw" is
not evidence of anything.
