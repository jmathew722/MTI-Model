"""Quarantine for code that is NOT part of a production run.

A module lands here when it is (a) unverified against the real target
environment, (b) reachable only behind an explicit opt-in flag, and (c) kept
because there is a concrete plan to revive it — not because deleting it felt
risky. Nothing in ``pipeline/`` imports from here at module load.

**A module does not get to stay indefinitely.** Either the verification happens
and it is promoted back to ``pipeline/``, or it is removed and ``README.md``
records the evidence. That file is the decision log, and it is the point of this
package: the HoleWizard5 path lived here for one day, was verified against a live
SolidWorks 2026 session, failed across four parameter mappings, and was deleted —
with the evidence and the git refs to recover it written down.

The package is currently EMPTY of modules. That is the intended steady state.
"""
