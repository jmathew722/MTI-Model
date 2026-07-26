# DWG-native — inherited patterns (Phase 1)

What the existing PDF pipeline already solved that the DWG-native pipeline
**imports and reuses** (never copies), and what it deliberately does **not**
carry over. Written before any new code, per the brief.

## Reused as-is (imported, one canonical source)

### `pipeline/coordinate_normalize.py` — the coordinate authority
The one place semantic drawing anchors become global CAD coordinates. Origin is
the **lower-left corner**, **+X right / +Y up / +Z extrusion**; `INCH_TO_M =
0.0254` and `to_meters()` are the only inch→meter conversion, applied only at the
CAD boundary. The DWG-native semantic + build layers call
`resolve_point_anchor`, `resolve_notch_anchor`, `validate_bounds`, and the
`Anchor` enum from here. **Coordinate conventions are not re-derived** — a bug
fixed here fixes both pipelines. (`dwg_native/semantic/mapper.py` and
`dwg_native/build/builder.py` import it directly.)

### `pipeline/solidworks_builder.py` — the COM build patterns
The most valuable inherited asset. Reused:
- `connect_to_solidworks()` — late-bound `Dispatch` connect (SW's IDispatch
  refuses `GetTypeInfo`, so `gencache.EnsureDispatch` is avoided). The DWG-native
  session manager wraps this, adding a serialized single-worker queue on top.
- `_ensure_sw_constants()` / `_const()` — resolve swConst enums by generated
  typelib with literal fallbacks.
- The verified feature calls: `FeatureExtrusion3`, `FeatureCut4` (flip-retry),
  `FeatureCircularPattern5` (axis Mark 1 / seed Mark 4), and the
  rename-after-create + `macro_result.json` logging contract.
- `create_new_part()` template resolution (`SOLIDWORKS_TEMPLATE_PATH`).

### `pipeline/macro_audit.py` — the static build guard
`BANNED_APIS` and the error-ledger enforcement are reused to audit the
DWG-native build path so the same class of macro bug cannot ship on a new path.

### `pipeline/vector_extract/dwg_convert.py` — the DWG import primitive
Already drives `GetImportFileData` + `LoadFile4` over COM (engine chain
ezdwg → SolidWorks translator → ODA). The DWG-native importer builds on the same
COM import; the extractor additionally rounds the imported doc to DXF and reads
it with `ezdxf`.

## The `docs/solidworks-macro-error-log.md` regression checklist
Every E-entry is a bug paid for once. Treated as a **pre-existing regression
checklist for the new build path**, not history:
- **E004** — no invented `GetModelBoundingBox`; read the box from `IBody2.GetBodyBox`.
- **E006** — never re-select a closed sketch by name (auto-numbering drift);
  consume the active sketch or re-find by `ProfileFeature`.
`tests/test_dwg_build_regressions.py` asserts the DWG-native emitted/attempted
build never reintroduces these.

## What the current PDF pipeline does that is NOT carried over

| Not carried over | Why |
|---|---|
| Claude **Vision extraction** (`extractor.py`) | The DWG carries exact geometry + exact text. Vision is the thing we are *correcting*, not repeating. |
| **Stage 2.5 resolver** as written | Its whole job is choosing among *uncertain vision candidates*. DWG values are exact; a much narrower reconciliation replaces it (rules-first, `dwg_native/semantic/`). |
| **Crop / region-markup logic** (`region_extraction.py`, tiling) | Those exist to squeeze pixels out of a raster. There are no pixels here. |
| **Vision-uncertainty confidence layer** | A DWG token is exact text at an exact coordinate; confidence collapses to "did it parse". The remaining uncertainty is *semantic* (what a number dimensions), handled explicitly. |
| **The entire current frontend** | Phase 3 is a redesign in `frontend-dwg/`, not a restyle. |

## The Phase-0 fact that shapes everything downstream
Numeric dimension **objects do not survive DWG import** (see
`dwg_native/spike/FINDINGS.md`, verdict RED): `IDisplayDimension.GetDimension()`
returns `None`, values come through as free-floating **MTEXT** + exploded
geometry. So the extraction layer reads **exact geometry (LINE/ARC/CIRCLE with
coordinates) + exact MTEXT tokens with 2D positions**, and the reasoning stage
maps those exact-but-unlabelled numbers to 3D feature intents. The DWG replaces
*digit uncertainty*, not the *semantic* problem — that is stated plainly in the
architecture doc and is the core design constraint.
