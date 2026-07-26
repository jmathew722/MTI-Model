# DWG-native — architecture (Phase 2)

## What this pipeline is
Takes a DWG, imports it through the SolidWorks API, reads the drawing's **exact
geometry and text** out of the imported document, and uses those exact values to
build a parametric part **and to correct the OCR/vision extraction findings**.
SolidWorks owns the DWG parse; nothing guesses a digit from a pixel.

## The two hard problems (stated up front)

**Problem 1 — reading the numbers.** SOLVED differently than the brief assumed.
Phase 0 (RED) proved numeric dimension *objects* do not survive import. But the
exact numbers DO survive as **MTEXT strings** and the exact geometry survives as
**LINE/ARC/CIRCLE** entities. So we read exact text + exact coordinates, not
pixels. This is strictly better than vision OCR for the digits themselves.

**Problem 2 — what does a number dimension?** UNSOLVED by any API, and honestly
labelled. A DWG dimension is a 2D measurement between two points. Whether `.280`
is a plate thickness or a hole depth is *semantic*, and the DWG does not store
it. There is still a reasoning stage; its input is exact numbers with known 2D
positions instead of uncertain pixels — a much better starting position, not an
escape. The semantic layer is rules-first; the LLM is used only where rules
genuinely cannot decide.

## System shape
```
DWG upload
  -> SolidWorks COM: import (GetImportFileData + LoadFile4, ImportToDrawing)
  -> SaveAs DXF (SolidWorks' own conversion) + read with ezdxf
  -> Native extraction: geometry (line/arc/circle) + MTEXT tokens + positions
  -> raw_extraction.json        exact values, exact 2D coords, zero interpretation
  -> Semantic mapping           rules-first: loops, circles, multipliers, proximity
  -> build_plan.json            same schema shape as the existing pipeline
  -> OCR correction             DWG-exact values override uncertain vision findings
  -> VBA macro emission + direct COM build
  -> .SLDPRT + .STL
  -> Verification: rebuild, fully-defined, dimension round-trip, count, bbox
```

## Process model (not optional)
SolidWorks COM automation is single-instance and stateful; two requests
interleaving corrupt each other's document state. Therefore:
- **A single serialized worker owns the COM session.** FastAPI accepts an upload,
  writes a job record, returns a job id immediately (`202`); the worker processes
  jobs strictly one at a time (`dwg_native/session/job_queue.py`).
- **Session lifecycle** (`dwg_native/session/com_session.py`): attach to a running
  SolidWorks or launch one; if a job crashes the session, detect it, mark the job
  failed with a real reason, and rebuild the session for the next job — never hang.
- **Hard per-job timeout** so a hung modal dialog cannot block the queue.
- **Dialog suppression** during automation via
  `ISldWorks::SetUserPreferenceToggle(swInputDimValOnCreate=False)` and
  `EnableBackgroundProcessing`/`CommandInProgress`, plus closing any leaked
  documents between jobs.
- **Document cleanup** between jobs (`CloseAllDocuments`) so leaked docs do not
  accumulate.

## Extraction layer (`dwg_native/extract/`)
Produces `raw_extraction.json` = only what SolidWorks reported, zero
interpretation, every value carrying provenance (which view, object type, 2D
coordinates). Schema (`schema.py`):
```jsonc
{
  "source_file": "A050381E.dwg",
  "units_detected": "inch",
  "sheet": {"width_m": 0.8636, "height_m": 0.5588},
  "views": [{
    "name": "Model", "type": 1,
    "display_dimensions": [],            // Phase-0 RED: usually empty, recorded honestly
    "geometry": [
      {"type": "line",   "start_2d_m": [0,0], "end_2d_m": [0.4064,0], "layer": "0"},
      {"type": "circle", "center_2d_m": [0.1,0.1], "radius_m": 0.0095, "layer": "0"}
    ],
    "text_tokens": [
      {"text": "1.000", "position_2d_m": [0.21,0.35], "height_m": 0.003}
    ]
  }],
  "unrecognized_objects": []             // MANDATORY, never empty by omission
}
```
`unrecognized_objects` is mandatory: anything the walker cannot classify is dumped
there with whatever type info exists. Silent drops are the failure mode this
project exists to avoid.

## Semantic mapping layer (`dwg_native/semantic/`) — rules first
Turns `raw_extraction.json` into `build_plan.json`. Deterministic rules:
- **Closed-loop detection** — chain line/arc segments; the largest closed loop
  that is not the sheet border or title block is the base profile.
- **Circle classification** — circles inside the profile below a diameter
  threshold are hole candidates; concentric duplicates collapse to one hole.
- **Number parsing** — MTEXT → value + tolerance + qualifier (`Ø`, `R`, `2X`,
  `TYP`, `+.000/-.001`).
- **Dimension→geometry attachment** — no attached-entity refs survive import
  (Phase 0), so attach a numeric token to the nearest geometry by proximity, with
  a recorded match confidence.
- **Title-block / border exclusion** by position (and layer where it survives).
- **Multiplier parsing** (`2 HOLES`, `6X`, `TYP`) reconciled against counted
  circle instances.

LLM (structured JSON in, never an image) only for: which view is front/top/section
when unlabelled, free-text manufacturing notes (`DRILL & C'BORE .16 DP FOR #6 SOC
HD SCR` → structured hole spec), and rule-flagged ambiguities.

## OCR correction (the stated purpose)
`dwg_native/semantic/ocr_correction.py`: given a vision `*_extraction.json` for
the same part (if present), each DWG-exact value that matches a vision field
within tolerance **confirms** it; a mismatch **overrides** the vision value with
the DWG value and records the correction with both readings. Standalone (no prior
vision run) it simply emits the exact values. Every correction is logged.

## Conflict handling — no silent skips, no silent defaults
- A callout multiplier disagreeing with counted instances is a **blocking
  conflict** (the A050211E 5-vs-6 case is the canonical test), never a tiebreak.
- A dimension that cannot attach to geometry with acceptable confidence blocks.
- Anything in `unrecognized_objects` the semantic layer cannot account for is
  surfaced.
- **Provenance invariant:** every field in `build_plan.json` traces to a specific
  object in `raw_extraction.json`. `assert_provenance()` fails the job on any
  value without a `source` — enforced in tests and at job time.

## Build layer (`dwg_native/build/`)
Reuses `solidworks_builder.py` patterns. Fully-defined sketches are a **hard
gate** (query state after creating each sketch; fail the job if under-defined).
Every applied dimension is a driving dimension. Holes route through
`HoleWizard5` with named enum constants (opt-in, verified fallback to sketch-cut,
matching the existing pipeline's default-off posture). Bolt circles / repeated
holes use a pattern feature. VBA macros are emitted as a reviewable artifact and
the part is built directly over COM (the existing dual-path arrangement).

## Verification layer (`dwg_native/verify/`)
Five gates, all machine-checkable:
1. **Rebuild** — force rebuild, no errors/warnings in the tree.
2. **Fully-defined** — every sketch reports fully defined.
3. **Dimension round-trip** — read each built feature dimension back over the API
   and compare numerically against `raw_extraction.json`; any mismatch beyond
   float tolerance is a hard failure. This is the strongest gate and exists only
   because extraction was numeric to begin with.
4. **Feature count** — holes built == holes resolved == reconciled callout count.
5. **Bounding-box sanity** vs stated overall dimensions.
A part failing any gate lands in `failed/` with the failing gate named, never in
the success output directory.

## Coexistence with the PDF pipeline
New code lives only under `dwg_native/` and `frontend-dwg/`; shared modules are
imported, not copied. `main.py --engine vba|com` runs the PDF pipeline unchanged.
`tests/` confirms the PDF pipeline still passes on this branch.
