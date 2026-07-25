# Phase 0 Feasibility Spike — FINDINGS

**Date:** 2026-07-25
**Machine:** Windows 11, SolidWorks 2024 (revision 32.5.0), live COM session.
**Corpus:** 10 real user DWGs (9 from `Downloads/EDrawings/`, 1 from
`Downloads/A050041M.DWG`, 1 from `SolidWorksModel_Parts/150291/`), a mix of
single-part detail drawings (`A050001E`, `A050511E`, `150291`, …) and larger
sheets. Imported through the SolidWorks API import path
(`GetImportFileData` → `LoadFile4`), configured for **ImportToDrawing +
ImportDimensions + AddSketchConstraints**.

---

## VERDICT: 🔴 RED

**Numeric dimension values do NOT come through as queryable objects.** The
documented pattern `view.GetDisplayDimensions()` → `IDisplayDimension.GetDimension()`
→ `IDimension.GetSystemValue2("")` returns **no values** for imported DWGs,
because the imported dimensions have no associated model dimension and arrive as
**hollow annotation shells** (or, more often, as fully exploded geometry + text).

The premise the whole project rests on does not hold for this corpus. Do not
proceed to build the app as written without a decision from the user (see
"Fallbacks" below).

The user's visual confirmation that "dimensions render correctly after import" is
**true and consistent with this finding** — the numbers render because they are
MTEXT strings and line/arrow geometry on the sheet. Rendered text and a
structured numeric object are different things, exactly as the Phase 0 brief
anticipated. This spike measured the difference.

---

## The measured answer to each Phase 0 question

### 1. Do numeric dimension values come through as objects? — **No.**

| File | doc type | views | native `GetDisplayDimensionCount` | numeric values recovered | DXF round-trip content |
|------|----------|-------|-----------------------------------|--------------------------|------------------------|
| A050001E | drawing | 1 | **0** | 0 | 412 LINE, 76 CIRCLE, 19 ARC, 238 MTEXT, 34 HATCH |
| A050021E | drawing | 1 | **0** | 0 | 609 LINE, 23 CIRCLE, 124 MTEXT, 32 INSERT |
| A050061E | drawing | 1 | **0** | 0 | 304 LINE, 51 MTEXT, 18 INSERT |
| A050181E | drawing | 1 | **0** | 0 | 78 LINE, 12 MTEXT |
| A050401E | drawing | 1 | **0** | 0 | 150 LINE, 20 MTEXT |
| A050511E | drawing | 1 | 4 | **0** (all 4 shells empty) | 31 LINE, **4 DIMENSION**, 10 MTEXT |
| 1722901E | drawing | 1 | **0** | 0 | 204 LINE, 40 MTEXT, 15 INSERT |
| A090451M | drawing | 1 | **0** | 0 | 707 LINE, 41 MTEXT, 2 TOLERANCE |
| A050041M | drawing | 1 | **0** | 0 | 206 LINE, 21 MTEXT |
| 150291   | drawing | 1 | **0** | 0 | 283 LINE, 196 ARC, 48 MTEXT |

**0 of 10 files yielded a single readable numeric dimension value as an object.**

### 2. What fraction of visible dimensions are recoverable as objects? — **~0%.**

9/10 files expose **zero** display-dimension objects. The 10th (`A050511E`)
exposes 4 `IDisplayDimension` objects — but every one is empty:

```
dim1..4: GetDimension() -> None
         GetSystemValue2("") -> None
         GetText(0..6) -> '' (all seven text parts empty)
         GetAnnotation().GetText() -> None
```

The actual values for that same part live in **MTEXT**, not the dimension
objects: `'1.000'`, `'+.000'`, `'-.001'`, `"DRILL & C'BORE .16 DP. FOR"`,
`'#6 SOC. HD. SCR.'`, `'4140'`, `'R/C 40-45'`, `'FINISH ALL OVER'`.

### 3. If not objects, what do they come through as? — **Exploded geometry + MTEXT.**

Exact object types after import (verified by round-tripping the imported SW
document back to DXF): `LINE`, `ARC`, `CIRCLE` (the geometry and the dimension
arrows/extension lines), `HATCH`, `INSERT` (blocks), and **`MTEXT`** (the numbers
and notes as free-floating text strings). Only `A050511E` retained any
`DIMENSION` entities — and SolidWorks discarded their values on import anyway.

### 4. Do dimension objects carry geometry association? — **N/A / No.**

There are essentially no dimension objects to associate. Where four exist
(`A050511E`), `GetDimension()` is `None`, so there is no `IDimension` to carry
any association. The value and even its tolerance are *separate* MTEXT entities
at separate sheet positions, with no structural link to each other or to the
geometry they annotate.

### 5. Units / `GetSystemValue2` returning meters? — **Unverifiable here.**

`GetSystemValue2("")` is documented to return meters, but since no dimension
object returned a value, this could not be cross-checked against a rendered
number. The MTEXT values are in inches (`1.000`, `.16`), matching the drawings.

---

## Why this happens (root cause)

An imported DWG has **no source 3D model**. SolidWorks display dimensions are
designed to be *associative* to model geometry — `IDisplayDimension.GetDimension()`
returns the driving `IDimension` only when the display dimension is linked to a
model feature. Imported DWG annotations have nothing to link to, so:

- most AutoCAD `DIMENSION` entities are **exploded** into lines + arrows + MTEXT
  on import (9/10 files had **no** `DIMENSION` entities survive even to DXF), and
- the few that survive as `IDisplayDimension` objects are **value-less shells**
  (`A050511E`).

This is inherent to importing a drawing with no backing model, not a
configuration miss. It was reproduced across every `ImportMethod`
(`DoNotImportSheet`, `ImportToDrawing`, `ImportToPartSketch`,
`ImportToExistingDrawing`) with `ImportDimensions=True`.

---

## A second, independent finding: SolidWorks is not the right parser here

Driving `IImportDxfDwgData` and `IDrawingDoc` from Python required fighting
win32com the entire way (documented in the scripts): late binding cannot set the
parameterized `ImportMethod(Sheet)` property; `CastTo` fails because SW objects
refuse `GetTypeInfo`; generated early-bound wrappers fail on `InvokeTypes`;
`IDrawingDoc.GetFirstView` is unreachable late-bound. Only raw
`IDispatch.Invoke(..., DISPATCH_PROPERTYPUT, ...)` and `IModelDoc2.GetViews()`
worked.

More importantly: **everything SolidWorks gave us, `ezdxf` gives us directly from
the DWG, and more.** The imported SW document round-tripped to DXF is *exactly*
the same MTEXT + LINE/ARC/CIRCLE content that a direct DWG→DXF conversion
produces — but the direct path also preserves the `DIMENSION` entities
(`A050511E`: 4 of them, with their measurement values) that SolidWorks throws
away. So routing through a SolidWorks COM session buys complexity (single-instance
serialization, dialog suppression, crash recovery) while delivering *strictly
less* structured data than reading the file directly.

---

## Fallbacks (for the user to choose — per the Phase 0 gate)

The premise is RED, so the decision is the user's. The realistic options:

**(A) Parse the MTEXT strings ("OCR without the camera").**
The digits are *exact text* (`'1.000'`, not a pixel guess), which is genuinely
better than vision OCR for reading the numerals. But every hard problem the
project wanted to remove remains: values and tolerances are separate entities
that must be spatially grouped; there is no link between a value and the geometry
it dimensions (must be inferred by proximity); and the semantic problem (which
number is a thickness vs. a diameter vs. a depth) is completely unsolved. This is
the same reasoning burden as today's vision pipeline, minus only the digit-reading
uncertainty.

**(B) `ezdxf` on a DWG→DXF conversion (no SolidWorks in the read path).**
`ezdxf` reads `LINE`/`ARC`/`CIRCLE`/`MTEXT` with exact coordinates, and reads any
surviving `DIMENSION` entity *as structured data with a measurement value* — the
one thing SolidWorks discarded. For this corpus that means: structured dims from
~10% of files, exact geometry + exact text from 100%. It needs no COM session, no
job queue, no dialog suppression, and it is unit-testable without SolidWorks. The
DWG→DXF step already exists in the repo (`pipeline/vector_extract/dwg_convert.py`,
engine chain ezdwg → SolidWorks translator → ODA).

**(C) Hybrid.** Use `ezdxf` for exact geometry + text + any real DIMENSION
entities, and keep a small reasoning layer (rules first, LLM only for genuine
ambiguity) over that structured-but-unlabelled data. This is the honest version
of the project's ambition: exact numbers with known 2D positions feeding a
semantic mapper — achieved without the SolidWorks-native extraction that does not
work.

**What is NOT viable:** the project as written in the brief — "read the
dimensions as structured `IDimension` numeric objects out of the imported
SolidWorks document." That path returned zero values on every file tested.

Note on corpus: these are eDrawings-exported DWGs. Native AutoCAD-authored DWGs
with live associative dimensions could contain more `DIMENSION` entities — but
`A050511E` shows that even when they survive to DXF, **SolidWorks import strips
their values**, so better source files would help option (B)/ezdxf, not the
SolidWorks-native premise.

---

## How to reproduce

From `2D-3D-CAD-Test-Generation/` (Windows, SolidWorks running):

```powershell
# The definitive batch collector (per-file table above):
webapp\.venv\Scripts\python.exe dwg_native\spike\collect_findings.py `
  "C:\Users\joeka\Downloads\EDrawings\A050001E.DWG" `
  "C:\Users\joeka\Downloads\EDrawings\A050511E.DWG"   # ... etc

# Evidence dumps: dwg_native/spike/out_findings/*.json
```

### Script inventory (all disposable spike code)
- `dump_dwg_entities.py` — first broad walk (found "0 views" — a false negative
  from swallowed `GetFirstView` errors; kept as the audit trail).
- `probe_one.py` — non-swallowing single-file probe; established `GetFirstView`
  is unreachable late-bound but `GetViews()` works.
- `probe_view.py` — walk via `GetViews()`; views empty on default import.
- `probe_import_opts.py`, `probe_early.py`, `probe_configured_import.py` —
  the win32com binding fight (late/early/CastTo all fail to configure import).
- `probe_lowlevel.py` — the raw-`Invoke` configuration that finally works.
- `probe_decisive.py` — DXF round-trip proving content imports as MTEXT+geometry.
- **`collect_findings.py`** — the definitive batch collector behind the table.
