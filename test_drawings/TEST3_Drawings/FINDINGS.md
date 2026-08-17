# TEST3 — what 15 real drawings taught the pipeline

Run 2026-08-17 against live **SolidWorks 2026 rev 34.3.2**, full pipeline
(`main.py --views-folder ../test_drawings/TEST3_Batch`). These are scanned MTI /
Electro-Motive drawings from 1976–1988 — pencil on vellum, photocopied, folded.
They are much harder input than the synthetic lab plates of Tiers 0–7, and they
found things no synthetic part could.

## Folder layout

| Path | What it is |
|---|---|
| `TEST3_Drawings/` | the 15 source PDFs, as delivered |
| `TEST3_Batch/` | the same sheets as 14 per-part folders (`--views-folder` layout); **SB10009's two sheets merged into one multiview part** |
| `TEST3_Batch/output/<part>/` | every artifact per part — extraction, resolved extraction, build plan, macros, `.SLDPRT`, `.STL`, reports |

Parts are named from the **title block**, not the filename: `A040791E.PDF` →
`4079-D`. Worth knowing when looking for output.

## Three defects found, all fixed, all pinned by tests

### 1. A dimension labelled "length" was driving an extrude depth

**Part 4079-D** (BRACKET ASSY). The base plate is BOM item 3, `.250 × 4.500 ×
15.25` — a quarter-inch plate. The extraction set:

```
F001.depth_dimension_id = D013
D013 = { value: 15.25, applies_to: 'length' }
```

SolidWorks extruded the plate **15.25 inches thick**. Measured result
17.25 × 14.75 × 7.00 in against a drawing whose third extent is 6.00.

**One bad field, three wrong answers.** Everything downstream that asks "how
thick is this part" calls `_model_thickness`, which returned 15.25. That included
the E024 edge-treatment check added the day before — it computed a limit of
7.6 in, so a **1.76 in chamfer** passed unflagged and then failed in SolidWorks
with 49 edges selected. A check written for exactly this case stayed silent
because it trusted an upstream number without asking whether it could be a
thickness at all.

**Fixed** — `validator._check_extrude_depth_semantics` flags any depth dimension
whose own `applies_to` says `length` / `width` / `height` / `overall_*` / `span`,
naming the feature, dimension id, value and label. On 4079-D it catches **F001**
(15.25 as 'length') and **F002** (1.0 as 'width'). And
`_check_edge_treatment_radius` now **declines** when handed a "thickness" ≥ the
part's smaller in-plane extent — warning off an impossible number is worse than
silence.

Neither invents the missing `0.250`: it was never linked to the feature, so it
cannot be recovered without guessing. They state the contradiction and let the
build ship a model.

### 2. An unclassifiable sheet deleted an entire part

**Part SB10009** produced **nothing** — no model, no macros, no report.

`classify_view` is filename-based. The two sheets are `SB10009 SHT 3.pdf` and
`SB10009 SHT 4.pdf`: no view keyword, no leading digit, and neither stem equals
the folder name. Both were skipped, `views` came out empty, and extraction died
with *"No view images supplied for multi-view extraction"*.

The single-sheet parts in this same batch survived **by luck** — a folder
`A040791E/` holding `A040791E.PDF` matches the "stem equals folder name" rule.

Dropping every sheet is the one outcome the guiding principle forbids. **Fixed**
— when nothing classifies, the sheets are adopted as overview context and the
guess is stated in the warnings, including how to remove it (name a sheet
`<part>_front_view`).

### 3. Chamfers fail as a group where fillets do not — *under investigation*

Three chamfer failures in the first four parts, **every one with scope
"all edges"**:

| Part | Feature | Size | Edges | Result |
|---|---|---|---|---|
| 4079-D | F005 | 1.76 in | 49 | `None` |
| 4079-D | F006 | 0.12 in | 49 | `None` |
| 4088-A | F003 | 0.06 in | 26 | `None` |

The last one is the informative one: **0.06 on a 0.500 thick key** is nowhere
near the E024 limit (`R < t/2 = 0.25`), so "too big" does not explain it.

E020 once claimed exactly this about *fillets* and was **retracted** — the
all-edges fillet works, and the apparent failure was the lab's own harness
clearing the selection. So the honest question is whether chamfers genuinely
differ, or whether this is E020 repeating. `t17_all_edges_chamfer.py` is written
to settle it: all-edges fillet (control) vs all-edges chamfer vs edge-by-edge
chamfer, on one clean plate. **Not yet run** — SolidWorks was occupied by the
batch. No claim either way until it is.

## What the drawings themselves demand

* **Every sheet is a faint scan.** Every part logged `Image appears nearly blank
  (mean pixel ~250)`. There is **no vector line work**, so hole positions fall
  back to Hough estimates and carry a not-vector-exact flag. Any position from
  this set is an estimate by construction.
* **Three are assembly drawings**, not parts — 4079-D and 4080-D are BRACKET
  ASSY with 3-item BOMs, SB10009 SHT 3 is a plate plus a support. The extraction
  handles them by combining items into one part and says so in its warnings,
  which is the right call for a single-`.sldprt` pipeline but means the model is
  a weldment approximation, not the drawing.
* **Dimensional closure fails on 4079-D**: `D002 = D001 + D003 + D004` →
  7.82 ≠ 10.12. Consistent with a hand-dimensioned assembly sheet where the
  chain crosses items.

## Results so far

| Part | Built | Envelope check |
|---|---|---|
| 4079-D | yes | **FAIL** — width 17.25 in vs 6.00 (defect 1) |
| 4080-D-RevB | yes | PASS on all three extents |
| 4086-A-RevA | yes | PASS, **no skipped features** |
| 4088-A-RevA | yes | PASS; one chamfer skipped (defect 3) |
| 4092-B | yes | — |
| SB10009 | **no output** | defect 2 |
| A040871E | no output | tool-input validation failure, repair attempted |

The batch was still running when this was written; the remaining parts
(SB10011, SB10024, SB10025, SB10046, SB10047, SB11842, SB12273) are pending.

**4086-A-RevA is the current best result** — a KEY, built clean with every
feature applied and every extent matching.

## Where the rest of the knowledge lives

Nothing here duplicates the SolidWorks API lessons. Those are canonical in
[`docs/solidworks-lessons/`](../../2D-3D-CAD-Test-Generation/docs/solidworks-lessons/)
(MANUAL + 9 lessons) and
[`docs/solidworks-macro-error-log.md`](../../2D-3D-CAD-Test-Generation/docs/solidworks-macro-error-log.md)
(E012–E025). This file records only what the **real drawings** added.
