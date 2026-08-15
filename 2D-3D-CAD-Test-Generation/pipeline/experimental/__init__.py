"""Quarantine for code that is NOT part of a production run.

A module lands here when it is (a) unverified against the real target
environment, (b) reachable only behind an explicit opt-in flag, and (c) kept
because there is a concrete plan to revive it — not because deleting it felt
risky. Nothing in ``pipeline/`` imports from here at module load; the one call
site that can reach ``hole_wizard`` does so lazily, inside a function, behind
``MTI_ENABLE_HOLE_WIZARD=1``.

See ``README.md`` in this directory for each module's status, the exact blocker,
and what would have to be verified on a live machine to promote it back.
"""
