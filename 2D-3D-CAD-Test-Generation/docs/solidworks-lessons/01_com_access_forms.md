# COM access forms and constants (win32com late binding)

Tested on **SolidWorks 2026, rev 34.3.2**, `win32com` late binding, via
`experiments/solidworks_practice/t0_api_probe.py` and `t0b_property_vs_method.py`.

## Verified working calls

```python
# constants — AFTER connecting (the tlb loads on connect)
from pipeline.solidworks_builder import _const, connect_to_solidworks
connect_to_solidworks()
_const("swEndCondBlind")           # 0
_const("swEndCondThroughAll")      # 1
_const("swEndCondThroughNext")     # 2
_const("swEndCondMidPlane")        # 6      <- not 4
_const("swEndCondUpToBody")        # 7
_const("swEndCondThroughAllBoth")  # 9      <- not 8
_const("swSolidBody")              # 0

# the three access forms
def sw_get(owner, name, *args):
    attr = getattr(owner, name)
    return attr(*args) if callable(attr) else attr

def sw_invoke(owner, name):        # only form that resolves IFace2.GetSurface
    import pythoncom, win32com.client
    ole = owner._oleobj_
    flags = pythoncom.DISPATCH_METHOD | pythoncom.DISPATCH_PROPERTYGET
    return win32com.client.Dispatch(ole.Invoke(ole.GetIDsOfNames(name), 0, flags, True))
```

Measured per-member behaviour:

| Member | Property | Method | Raw Invoke |
|---|---|---|---|
| `IModelDoc2.GetMassProperties` | **OK** (12-tuple) | TypeError | — |
| `IModelDoc2.FirstFeature` | **OK** | Member not found | — |
| `IBody2.GetFaces` / `GetEdges` | callable stub | **OK** | — |
| `IBody2.Name` | **OK** | TypeError | — |
| `IFace2.GetArea` / `GetEdgeCount` | **OK** | TypeError | — |
| `IFace2.GetSurface` | stub | Member not found | **OK** |
| `ISurface.IsCylinder` / `IsPlane` / `CylinderParams` / `PlaneParams` | **OK** | TypeError | — |
| `IBody2.GetBodyBox`, `IPartDoc.GetPartBox` | — | **OK** | — |
| `ISketch.GetLineCount2(0)` | — | **OK** | — |
| `IModelDoc2.GetRebuildErrorCount` | AttributeError — **does not exist here** | | |
| `Extension.CreateMassProperty` | Member not found — **not available** | | |

`GetMassProperties` field order (measured on a 4×3×0.5 in block = 6.0 in³):
`[0:3]` centre of mass (m), `[3]` **volume** (m³), `[4]` surface area (m²),
`[5]` mass, `[6:]` moments.

## What the reference docs got right / missed

* Doc 02's "use `swconst` enums, never magic integers" is **confirmed the hard
  way** — see E012. Its C# examples cannot be transliterated directly because
  C# interop exposes everything as methods; Python late binding does not.
* Doc 07 proposes the cylindrical-face hole audit. It **does work here**, but
  only through the raw-Invoke form for `GetSurface` — a straight transliteration
  of the doc's C# fails with "Member not found".
* Doc 07's `IMassProperty` route is **not available** on this install; the
  `IModelDoc2.GetMassProperties` property replaces it.

## Gotchas found empirically

* `_const` before connecting silently returns your fallback default.
* Reading a *method* as a property yields a bound method — truthy, so
  `if body.GetFaces:` passes while giving you nothing.
* `SelectByID2`'s 8th argument must be `VT_DISPATCH` null; plain `None` fails the
  whole call with `Type mismatch (-2147352571)`. See E013.

## Failure modes tested
E012 (guessed enum destroyed the body), E013 (`None` as VT_DISPATCH). Both in
`docs/solidworks-macro-error-log.md`.

## Confidence
**HIGH** — every row re-observed across three separate sessions.
