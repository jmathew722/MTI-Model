# Tier 7 — the last gap closes: linear patterns build

Iterations 18–21. Linear patterns had been the one open capability gap since
Tier 2: `FeatureLinearPattern4` returned `None`, raised nothing and created
nothing, while *circular* patterns built correctly under what looked like
identical discipline. It is now closed, and it turned out to be **two** defects
stacked, the second hidden behind the first.

## First, an audit that came back clean

E012 — a guessed enum value that deleted an entire solid while reporting a clean
rebuild — is the worst failure in this ledger. `_const(name, fallback)` falls back
to a hardcoded number when the type library cannot be read, which is exactly when
nothing else can catch a wrong one. So every fallback in `pipeline/` was checked
against the live library:

| | fallback | live |
|---|---|---|
| `swCM` | 1 | 1 |
| `swDefaultTemplatePart` | 8 | 8 |
| `swINCHES` | 3 | 3 |
| `swMM` | 0 | 0 |
| `swSolidBody` | 0 | 0 |

and the E012 family re-checked: `swEndCondBlind` 0, `swEndCondThroughAll` 1,
`swEndCondMidPlane` **6** (not 4), `swEndCondUpToBody` **7** (the value that
deleted the solid), `swEndCondThroughAllBoth` **9** (not 7). All ten agree.

The emitted VBA is immune to this class by construction — it uses named
constants (`swEndConditions_e.swEndCondBlind`), never numeric literals.
`tests/test_enum_fallbacks.py` is the CI-safe half: it re-reads the fallbacks out
of the pipeline source and fails if one is edited away from a measured value, no
SolidWorks required.

## Then the gap itself: it was the selection form

Two things had never been varied — *how* the direction is selected, and *which*
overload is called. Four combinations, on an 8 × 3 × 0.375 plate with one seed
hole:

| | `IEdge::Select4` | `Extension.SelectByID2(…, "EDGE", x, y, z, …, Mark 1)` |
|---|---|---|
| `FeatureLinearPattern4` | `None`, holes 1→1 | **BUILT, holes 1→3** |
| `FeatureLinearPattern5` | Type mismatch | Type mismatch |

**The API was never missing. The selection form was wrong.** This is the E018
pattern again: on this install *how* you reach a member matters more than which
member you reach.

### A prediction that was wrong, kept because it was wrong

`Select4(Append, Callout)` takes no Mark parameter, so it selects at Mark 0,
while a direction reference needs Mark 1. That is a tidy mechanism, and it makes
a falsifiable prediction: `IEntity::Select2(Append, Mark)` — same object, same
access route, but able to carry a Mark — should build at Mark 1.

It does not.

```
Select2(append, Mark=1)   ->  nothing built     PREDICTION WRONG
Select2(append, Mark=0)   ->  nothing built     as predicted
Select4 (Mark 0 implied)  ->  nothing built     as predicted
```

So the Mark is *necessary* and not *sufficient*, and `SelectByID2` remains the
only route measured to work. **The mechanism is unexplained** — recorded that way
rather than dressed up, because a wrong explanation is worse than an honest gap.

## The pipeline had both defects

`build_pattern` selected the seed with `Select4(False, …)` — no Mark — and
selected **no direction reference at all**. That is precisely the configuration
measured to fail every time. The code even said so: *"this is NOT verified live."*

Fixing the selection surfaced the second defect immediately: the call carried
**18 arguments** and raised `Parameter not optional` (`-2147352561`). The working
form takes **20**. That error had never been seen because the selection failed
first — one defect masking another, both silent in their own way.

### Verified in the code that ships, not just in the lab

A recipe working in a lab script and the pipeline's encoding of it working are
different claims. Iteration 21 runs `_select_pattern_direction_edge` — the actual
shipping function, deriving its point from a *plan* rather than from the model —
against a live part:

```
plan envelope matches the built part      length=8.0, thickness=0.375
_select_pattern_direction_edge            edge at (4, 0, 0.375) at Mark 1
pattern builds                            holes 1 -> 3, returned a feature
```

The direction point comes from numbers the plan already holds (envelope midpoint,
top face), so nothing is invented. When no edge is found there the build is not
blocked — the pattern proceeds and is flagged for direction verification, per the
guiding principle.

## Confidence

**HIGH** for the capability (measured 1→3 through the shipping code path) and for
the enum audit (ten values read from the live library). **The mechanism behind the
selection-form asymmetry is unknown**, and the fix is verified on a plate-with-
holes geometry — not yet across the full range of real drawings.
