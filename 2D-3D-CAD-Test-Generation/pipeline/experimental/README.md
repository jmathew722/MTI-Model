# pipeline/experimental — quarantine, and its decision log

Code lands here when it is unverified against the real target environment,
reachable only behind an explicit opt-in, and kept because there is a concrete
plan to revive it. **A module does not get to stay here indefinitely**: either
the verification happens and it is promoted, or it is removed and this file
records why.

---

## HoleWizard5 — REMOVED 2026-08-16 (verified, does not work)

**Status: deleted.** Resolves REFACTOR_ANALYSIS §2.1.

### What it was
`hole_wizard.py` + `hole_wizard_constants.py` built holes as real
diameter-driven legacy Hole Wizard features (`IFeatureManager::HoleWizard5`,
dispid 222) typed from the callout sub-type (simple / tapped / counterbore /
countersink / clearance), with named enum constants and the ANSI clearance
table. `solidworks_builder._try_hole_wizard` called it behind
`MTI_ENABLE_HOLE_WIZARD=1`; the default was always OFF.

### The verification (the four steps this file used to prescribe)
Run on **SolidWorks 2026, revision 34.3.2**, live, on 2026-08-16:

1. Scratch part created from a real template, `4.0 × 3.0 × 0.5` in base block
   built — **solid body present and verified**.
2. Point sketch placed at the hole centre — **`_place_points` returned True on
   every attempt**.
3. `HoleWizard5` invoked with four different parameter mappings:

| Attempt | Mapping | Result |
|---|---|---|
| A | exactly what the module built (legacy diameter-driven, `StandardIndex=0`) | **None** |
| B | `StandardIndex=1`, `Value1 = diameter` | **None** |
| C | `Value1..3 = diameter / depth / 118° drill angle` | **None** |
| D | explicit through-all end condition | **None** |

4. API presence probed: `HoleWizard`, `HoleWizard2..5`, `AdvancedHole` and
   `SimpleHole2` are all **present** on the FeatureManager. So the method exists
   and is callable — it simply returns `None` for this legacy diameter-driven
   configuration, with valid preconditions in place.

### The decision
This file's own rule was: verify and promote, or remove. The verification was
performed and is negative across four plausible mappings on the current target,
so the module was not "unverified" any more — it was **verified not to work**,
while still costing an import fix on every refactor. It is removed.

Removed with it: `solidworks_builder._try_hole_wizard`,
`solidworks_builder._wizard_hole_type`, the `hole_wizard5` entry in
`methods_config`, and `tests/test_hole_wizard.py`. The `MTI_ENABLE_HOLE_WIZARD`
env var is now inert (a test pins that a stale flag in someone's `.env` cannot
select a method that no longer exists).

**Nothing else changed.** Holes are built by the proven sketch-circle cut
(`_circular_cut_at`), which was always the default and is covered by the golden
suite.

### If someone wants to revive it
```
git show eb77b63:2D-3D-CAD-Test-Generation/pipeline/experimental/hole_wizard.py
git show eb77b63:2D-3D-CAD-Test-Generation/pipeline/experimental/hole_wizard_constants.py
git show eb77b63:2D-3D-CAD-Test-Generation/tests/test_hole_wizard.py
```
Two leads the probe surfaced, either of which would be a fresh implementation
rather than a revival:

* **`SimpleHole2`** is present and is the diameter-driven simple-hole API — far
  smaller than the 27-argument wizard, and enough for a plain drilled hole.
* **`AdvancedHole`** is present and is the modern replacement for the legacy
  wizard on recent versions.

Neither is worth doing while the sketch-circle cut builds correct geometry: the
only thing they would add is a more idiomatic feature tree.

### What was NOT removed
`reconcile_callout_count` lived in `hole_wizard.py` but is live pipeline logic
(the Stage 2.5 resolver calls it for the A050211E "callout says 6, five are
countable" conflict) and has nothing to do with the wizard COM call. It moved to
`pipeline/callout_qty.py` on 2026-08-15, which owns callout quantity language.
