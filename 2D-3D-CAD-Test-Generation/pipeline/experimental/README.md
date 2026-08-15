# pipeline/experimental — quarantined code

Code here is **not part of a production run**. It is unverified against the real
target environment (Windows + SolidWorks 2024), reachable only behind an explicit
opt-in flag, and kept only because there is a concrete plan to revive it.

Resolution of REFACTOR_ANALYSIS §2.1 ("resolve HoleWizard5's fate"): the path is
neither promoted nor deleted — it is **quarantined**, which makes its status
visible in the tree instead of implied by a default-off env var buried in a
2 400-line builder. Promotion requires the live verification below; if that
verification is never scheduled, the honest next step is deletion, and this file
is where that decision gets recorded.

---

## `hole_wizard.py` + `hole_wizard_constants.py`

**Status:** QUARANTINED — default OFF, never reached unless
`MTI_ENABLE_HOLE_WIZARD=1`.

**What it does:** builds holes as real diameter-driven legacy Hole Wizard
features (`IFeatureManager::HoleWizard5`) at the resolved centers, typed from the
callout sub-type (simple / tapped / counterbore / countersink / clearance), with
named enum constants and the ANSI clearance table, instead of the proven
sketch-circle cut.

**Why it is not on:** the 27-argument signature was verified against the
installed `sldworks.tlb` (dispid 222 — the old "Type mismatch" is gone), but on
SolidWorks 2024 the call **returned `None` even on a clean part with a valid face
and point sketch**. The parameter/`Value`-slot mapping for the legacy wizard is
version- and locale-specific, and it has not been nailed down on a live machine.

**What the pipeline uses instead:** `solidworks_builder._circular_cut_at` — the
sketch-circle cut, which is regression-free and covered by the golden suite. This
is unaffected by anything in this directory.

**To promote it (the concrete plan):**

1. On a machine with SolidWorks 2024 open, build a scratch part with a flat top
   face and one point sketch (`pipeline/construction_experiment.py` already does
   this for method-library seeding — reuse it).
2. Call `HoleWizard5` with the plan from `hole_wizard.plan_wizard_hole` and dump
   the returned object plus `swFeatureManager.GetLastError`.
3. Bisect the `Value`/`ValueTypes` slot mapping against the SW 2024 API help for
   the *legacy* (not Advanced) wizard until a feature is actually created.
4. Record the verified mapping in `docs/sw_api_reference/`, add a live-verified
   note next to the signature, and only then move the module back to `pipeline/`
   and flip the default.

**If step 1 is never scheduled:** delete both modules and
`solidworks_builder._try_hole_wizard`. A permanently-unverified branch that is
maintained on every refactor is a cost with no matching benefit.

**What was NOT quarantined:** `reconcile_callout_count` used to live in
`hole_wizard.py` but is live pipeline logic (the Stage 2.5 resolver calls it for
the A050211E "callout says 6, five are countable" conflict) and has nothing to do
with the wizard COM call. It now lives in `pipeline/callout_qty.py`, the module
that owns callout quantity language. `hole_wizard.py` re-exports it so any
external caller keeps working.

**Tests:** `tests/test_hole_wizard.py` still runs — it covers the pure planning
logic (sub-type routing, clearance table, argument shape), which is worth keeping
green so the module does not rot while it waits for its live verification.
