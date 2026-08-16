# Tier 6 — turning the manual back on the pipeline's own macros

Iterations 16–17. Every earlier tier tested *SolidWorks*. This one tests **the
macros this pipeline emits**, by auditing the checked-in golden package
(`tests/golden/bracket/macros/`, 2356 lines across 10 files) against the rules
the lab established empirically.

The useful result is not a pile of defects. It is that most of the audit came
back clean for reasons worth writing down — and that the one real gap turned
into a measured threshold and a new plan-time check.

## What the audit checked, and what it found

| Rule | Emitted macros | Verdict |
|---|---|---|
| 2 — `Nothing` for every `SelectByID2` object arg | every call passes `Nothing` | clean |
| 4 — no `ClearSelection2` between closing a profile and consuming it | `ClearSelection2` appears only before selecting, never between | clean |
| 5 — name every feature immediately | `swFeat.Name = ...` right after each create, with a comment explaining the auto-numbering drift it avoids | clean |
| 10 — count instances after a pattern | `CreateCircularPatternSafe` checks `Is Nothing` and falls back `FeatureCircularPattern5` → `4` | clean |
| 14 — nothing between select and call | see below | clean, and non-obvious |

### The `MsgBox` question, and why it is not a defect

26 `MsgBox` calls are emitted, 18 in `RUN_ALL.vba` alone. A modal dialog in an
unattended run is a hang, so this looked like the headline finding — until the
package README settled it: these macros are **for a human at the SolidWorks
machine** ("paste into a new macro (Alt+F11) and press **F5**"). The COM path
(`solidworks_builder.py`) is the unattended one and emits no dialogs. For a
human-run macro a modal error is the right call. **No change.**

Worth stating because the opposite conclusion was one grep away, and "the
generated macros hang in automation" would have been a confident, wrong bug
report.

### Rule 11 vs rule 14: a genuine conflict in the interactive fillet macro

`04_fillets_chamfers.vba` is the one place a **human owns the selection** — the
README says select the edges in the graphics area, then run. That puts two rules
in direct opposition:

* **rule 11** — compare the volume before and after every fillet
* **rule 14** — nothing between select and call, because a rebuild clears the
  selection and disconnects held pointers (E022)

The macro cannot obey 11 without breaking 14: measuring first would destroy the
selection the human just made. It obeys 14, and its only signal is
`If swFeatF003 Is Nothing`.

That is safe **only if** "returned a Feature" and "geometry actually changed" are
the same thing — which is exactly what the (now retracted) E020 denied. So it
needed measuring, not assuming. Ten radii on a 4.0 × 3.0 × **0.5** plate, all 12
edges, each from an independent clean state:

| radius | returned | volume | | radius | returned | volume |
|---|---|---|---|---|---|---|
| R0.01 | feature | 6.0 → 5.99936 | | R0.26 | `None` | unchanged |
| R0.0625 | feature | 6.0 → 5.97518 | | R0.30 | `None` | unchanged |
| R0.125 | feature | 6.0 → 5.90202 | | R0.50 | `None` | unchanged |
| R0.20 | feature | 6.0 → 5.75319 | | R1.00 | `None` | unchanged |
| R0.24 | feature | 6.0 → 5.64768 | | R5.00 | `None` | unchanged |

**Perfect correspondence, 10/10.** A returned Feature always means the geometry
changed; `None` always means it did not. **The emitted macro's `Is Nothing` check
is sufficient**, and where a human owns the selection rule 14 wins over rule 11
without losing any signal. No change to the macro.

## The finding that came out of it: R < thickness / 2

The sweep was aimed at the return value and landed on a sharp geometric limit as
a by-product. On a 0.5 in plate the last radius that builds is **0.24** and the
first that fails is **0.26** — the limit is **R < thickness / 2**, because the two
opposite edges of a through edge each consume the radius. Past it the call
returns `None`, raises nothing, and nothing changes.

The COM builder already reports this, but only *after* the failure and only if
the build runs at all. So `pipeline/validator.py` gained
`_check_edge_treatment_radius`: at plan time, any fillet radius or chamfer
distance not strictly less than half the material thickness gets a warning naming
both numbers. It is **advisory** — the drawing is the authority and the callout
may be meant for a thicker edge — so it flags and the build proceeds, per the
guiding principle. `tests/test_edge_treatment_radius.py` pins all ten measured
radii: the five that built must pass silently, the five that did nothing must
warn.

### A first-pass sweep that measured nothing

The first run of that sweep guessed the auto-generated feature name `"Fillet1"`
for its rollback. The delete silently did nothing, the plate went from 12 edges
to 48, and every radius after the second ran against an already-filleted part —
so eight "failures" were one accumulated state, not eight independent tests. The
correspondence result survived; the radius threshold would have been pure
fiction. **MANUAL rule 5 — name every feature immediately — caught out the person
who wrote it**, in a lab whose whole subject is that this API fails silently.

## Confidence

**HIGH.** The rule conformance is a static read of checked-in macros; the
threshold and the correspondence are 10 measured before/after pairs from
independent clean states, with the flawed first attempt discarded and rerun
rather than reinterpreted.
