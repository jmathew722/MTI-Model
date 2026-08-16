# The SolidWorks COM Manual — write it right the first time

**Audience:** Claude (or any agent) generating SolidWorks automation for this
repo. **Status:** every rule below was produced by running code against a live
SolidWorks 2026 (rev 34.3.2) via `win32com` and measuring the result. Nothing
here is quoted from documentation.

Read this before writing any COM or macro code. The five rules cost one lab
session each to learn, and four of them fail **silently** — the call returns an
object, the part looks built, and the geometry is wrong or missing.

---

## Rule 1 — Never guess an enum. Resolve it from the type library.

```python
EC_THROUGH_ALL      = _const("swEndCondThroughAll")      # 1
EC_MIDPLANE         = _const("swEndCondMidPlane")        # 6   (NOT 4)
EC_THROUGH_ALL_BOTH = _const("swEndCondThroughAllBoth")  # 9   (NOT 8)
```

Measured on this install. Two values differ from what a reasonable person would
guess, and the failure is catastrophic and silent:

> I passed `7` for "through all both", reasoning from the enum order. `7` is
> `swEndCondUpToBody`. With no body reference supplied, the cut **deleted the
> entire solid**, `FeatureCut4` returned a valid feature object, and
> `check_rebuild_errors` reported the model clean. Volume went 12.0 in³ → 0.

`_const(name)` needs a live connection first — it loads the installed
`sldworks.tlb`. Calling it before `connect_to_solidworks()` silently returns your
default.

---

## Rule 2 — Three access forms exist. Use `sw_get`, never a bare call.

Under late binding, a SolidWorks member is exposed as a method, a property, or
neither — and it is **per member**, not per interface:

| Member | Form that works | What the wrong form does |
|---|---|---|
| `IModelDoc2.GetMassProperties` | **property** → 12-tuple | `TypeError: 'tuple' object is not callable` |
| `IBody2.GetFaces` | **method** | returns a bound method, silently truthy |
| `IBody2.Name` | **property** | `TypeError: 'str' object is not callable` |
| `IFace2.GetArea` | **property** → float | `TypeError: 'float' object is not callable` |
| `IFace2.GetSurface` | **raw dispid Invoke** | `com_error -2147352573 Member not found` from BOTH normal forms |
| `ISurface.IsCylinder` / `CylinderParams` | **property** | `TypeError: ... not callable` |
| `IModelDoc2.FirstFeature` / `IFeature.GetNextFeature` | **property** | `Member not found` when called |

```python
def sw_get(owner, name, *args):
    attr = getattr(owner, name)        # AttributeError = genuinely absent
    return attr(*args) if callable(attr) else attr

def sw_invoke(owner, name):            # the third form, for GetSurface
    import pythoncom, win32com.client
    ole = owner._oleobj_
    flags = pythoncom.DISPATCH_METHOD | pythoncom.DISPATCH_PROPERTYGET
    return win32com.client.Dispatch(ole.Invoke(ole.GetIDsOfNames(name), 0, flags, True))
```

The dangerous case is reading a *method* as a property: you get a bound method
object, which is truthy, so `if body.GetFaces:` passes and you carry on with
nothing.

---

## Rule 3 — `SelectByID2`'s 8th argument must be a VT_DISPATCH null.

```python
from pipeline.solidworks_builder import _null_dispatch
doc.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0,
                          _null_dispatch(), 0)     # NOT None
```

Passing Python `None` sends `VT_EMPTY` and SolidWorks rejects the **entire call**
with `Type mismatch (-2147352571)` — even though the parameter is optional. This
took out all 8 experiments in my first Tier-1 run before a single feature was
built.

---

## Rule 4 — THE cut recipe. Three load-bearing steps, each learned the hard way.

```python
# 1. open a sketch on the plane
doc.Extension.SelectByID2(plane, "PLANE", 0, 0, 0, False, 0, _null_dispatch(), 0)
doc.SketchManager.InsertSketch(True)
doc.SketchManager.AddToDB = True

# 2. draw the profile (absolute model coordinates, METERS)
doc.SketchManager.CreateCircleByRadius(x_m, y_m, 0, radius_m)

# 3. CLOSE the sketch — this leaves it SELECTED, and that selection is the profile
doc.SketchManager.AddToDB = False
doc.SketchManager.InsertSketch(True)

# 4. cut, retrying with the direction flipped
def _try(flip):
    return doc.FeatureManager.FeatureCut4(
        True, False, flip, EC_THROUGH_ALL, EC_BLIND, 0.0, 0.01,
        False, False, False, False, 0, 0, False, False, False, False, False,
        True, True, True, True, False, 0, 0, False, False)
feat = _try(True) or _try(False)
```

What breaks it:

* **`ClearSelection2(True)` between step 3 and step 4.** This is the one that
  cost the most time. Clearing the selection deselects the profile; the cut then
  removes **nothing** (or, with a bad end condition, everything) and returns a
  feature object with no error. My holes silently produced zero cylindrical faces
  for three runs.
* **Not closing the sketch.** Leaving it open is what the *VBA recorder* pattern
  does, and the repo's macro generator relies on it there — but on the COM path
  the profile must be closed and selected. The two paths genuinely differ.
* **No flip retry.** A cut aimed at the empty side of the sketch plane removes
  nothing and returns `None`. Always try both directions before concluding
  failure.

And do **not** re-find the sketch by name after closing it (`SelectByID2(name,
"SKETCH", …)`) — that is error **E006** in the ledger, confirmed again here:
selecting the closed sketch by an empty/derived name selected nothing and the cut
removed 0 in³.

---

## Rule 5 — Measure the result. Never trust the call's return value.

A feature object is not evidence. Verified measurement chain on this install:

```python
vals   = sw_get(doc, "GetMassProperties")   # PROPERTY, 12 values
com    = vals[0:3]                          # centre of mass, metres
volume = vals[3]                            # m^3   <- [3], not [4]
area   = vals[4]                            # m^2
box    = body.GetBodyBox()                  # (x1,y1,z1,x2,y2,z2) metres
```

`IBody2.GetMassProperties` needs a parameter, `Extension.CreateMassProperty` does
not resolve at all, and `GetRebuildErrorCount` does not exist here — the
`IModelDoc2.GetMassProperties` property is the one that works.

**Hole audit** (verified working, and stronger than a body count):

```python
for face in sw_get(body, "GetFaces"):
    surf = sw_invoke(face, "GetSurface")
    if sw_get(surf, "IsCylinder"):
        x, y, z, ax, ay, az, r = sw_get(surf, "CylinderParams")
```

---

## Placement — what the geometry actually does

Measured on one 6.0 × 4.0 × 0.5 in block, Front-Plane sketches, absolute
coordinates, corner of the block at the origin.

**Holes: sketch (x, y) lands at model (x, y), exactly.** 5/5 verified to 3 dp.

| Intended | Measured (cylindrical face) |
|---|---|
| (0.75, 0.75) ⌀.375 | (0.75, 0.75) ⌀.375 |
| (5.25, 0.75) ⌀.375 | (5.25, 0.75) ⌀.375 |
| (0.75, 3.25) ⌀.375 | (0.75, 3.25) ⌀.375 |
| (5.25, 3.25) ⌀.375 | (5.25, 3.25) ⌀.375 |
| (3.00, 2.00) ⌀.75 | (3.00, 2.00) ⌀.75 |

**Edge cuts: the corner-origin frame holds on all four sides.** 4/4 verified —
each notch removed 0.245 in³ against an intended 0.25 (the 0.005 difference is
the deliberate 0.01 overshoot past the edge).

| Notch | Sketch corner | Removed |
|---|---|---|
| LEFT | (−0.01, 1.5) 0.5 × 1.0 | 0.245 in³ |
| RIGHT | (5.51, 1.5) 0.5 × 1.0 | 0.245 in³ |
| BOTTOM | (2.5, −0.01) 1.0 × 0.5 | 0.245 in³ |
| TOP | (2.5, 3.51) 1.0 × 0.5 | 0.245 in³ |

So for a Front-Plane part built corner-at-origin:
`left → x0 = 0`, `right → x0 = W − depth`, `bottom → y0 = 0`,
`top → y0 = H − depth`. **Y is UP.** A top notch computed as `y = depth` lands on
the bottom edge, builds perfectly cleanly, and is the wrong part — this is the
158-C failure class, and the only thing that catches it is measuring which side
lost material.

**Overshoot the open side.** Every notch here used a 0.01 in overshoot past the
edge. A cut whose boundary is exactly coincident with the part face is a
zero-thickness condition; the repo's `slot_cut.EDGE_OVERSHOOT_EPS` exists for
this and the measurements confirm the need.

**Non-Front planes do NOT share the coordinate frame.** A hole sketched at the
same (1.25, 0.5) on the Top and Right planes produced **no new cylindrical face
and removed no material** — the profile lands somewhere that misses the block.
Sketch-plane → model-axis mapping must be resolved explicitly per plane; do not
reuse Front-Plane coordinates on another plane.

---

## Rule 6 — "It did not throw" is not evidence. Ever.

Across two lab sessions, **six of six** discovered failure modes were silent
(E012–E017). The API's habit is to return a feature object, or `None`, and let
the model report clean:

| What was asked for | What came back | What was actually built |
|---|---|---|
| through-all-both cut (enum guessed) | valid feature, rebuild clean | **the whole solid deleted** |
| hole, selection cleared first | valid feature | nothing |
| linear / circular pattern | `None`, no exception | nothing |
| mirror about the wrong plane | valid feature | nothing (copy landed off the part) |
| revolve with two centerlines | valid feature | a revolve (doc says this should fail) |
| hole on a non-Front plane | `None` from both flips | nothing |

So every feature call is followed by a measurement of the RESULT, not a check of
the return:

* additive feature → volume increased, and **body count is still 1** (a boss or
  revolve clear of the base silently makes a second body);
* cut → volume decreased by the expected amount;
* hole → a cylindrical face exists at the expected (x, y, ⌀);
* pattern → the instance COUNT increased by the expected number.

## The checklist (use this when generating a build)

1. Connect, then resolve every enum with `_const`.
2. `_null_dispatch()` for every `SelectByID2` object argument.
3. Sketch → draw in metres → **close** the sketch → cut with a **flip retry**.
4. Never `ClearSelection2` between closing a profile and consuming it.
5. Name every feature immediately (`feat.Name = ...`); never select by an
   auto-generated name.
6. After every feature: body count, volume, and — for holes — the cylindrical
   face audit. A feature object proves nothing.
7. Overshoot open-edge cuts; never end a cut coincident with a face.
8. Compute edge-referenced positions with Y up from the corner origin, and
   verify which side actually lost material.
9. Emit exactly ONE centerline per revolve sketch and assert it — SolidWorks
   will not reject an ambiguous axis (E016).
10. After a pattern or mirror, count the resulting instances. Both fail silently
    (E017), and a wrong `Mark` produces `None`, not an error.
