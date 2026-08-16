# SolidWorks lessons — empirically verified

Hands-on results from driving live **SolidWorks 2026 rev 34.3.2** over
`win32com`. Everything here was executed and measured; nothing is quoted from
documentation. Lab notebook: `experiments/solidworks_practice/`.

| File | One-line summary |
|---|---|
| [MANUAL.md](MANUAL.md) | **Start here.** Five rules for writing SolidWorks COM code that works first time, plus the measured placement reference and a generation checklist. |
| [01_com_access_forms.md](01_com_access_forms.md) | Late binding exposes members as method, property, or raw-Invoke-only — per member; plus the enum values that differ from the obvious guess. |
| [02_cut_and_hole_placement.md](02_cut_and_hole_placement.md) | The verified cut recipe, and measured hole/edge-notch placement on all four edges of one block. |

## What was measured

* One part, iterated: `experiments/solidworks_practice/parts/practice_master.sldprt`
  — a 6.0 × 4.0 × 0.5 in block, 5 holes, 4 edge notches, all added to the SAME
  file and measured after each step.
* **10 of 12 placements verified** to 3 dp against the live model. The 2
  non-verifications are the Top/Right-plane probes, which is itself the finding
  recorded as E015.

## New entries added to the error ledger
E012–E015 in [`../solidworks-macro-error-log.md`](../solidworks-macro-error-log.md).
All four fail **silently** — a feature object comes back and the model reports
clean.
