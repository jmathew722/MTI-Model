"""DWG-native pipeline: DWG -> SolidWorks import -> exact geometry+text -> part.

A SECOND, PERMANENT PRODUCT alongside the vision pipeline — not a duplicate of
it (decision recorded 2026-08-15 in ``docs/DWG_PATHS.md``, resolving
REFACTOR_ANALYSIS §1.6/§2.4). The two differ in what they treat as truth: here
the DWG ENTITIES are truth and a part that fails a gate lands in ``failed/``;
the vision pipeline reads the DRAWING and always produces a complete approximate
model. Neither guarantee survives merging them.

Which path a given file takes is answered by ONE function —
``pipeline.dwg_routing.route_for`` — never by which entry point an operator
happened to open. Requires Windows + SolidWorks; entry points are
``run-dwg.ps1`` and the API on :8095.

Shared modules (pipeline/coordinate_normalize.py, pipeline/solidworks_builder.py)
are IMPORTED, never copied — one canonical coordinate resolver, one place to fix
a bug. Changes that must land in BOTH emitters are listed in the parity checklist
in ``docs/DWG_PATHS.md``. See also docs/dwg-native/architecture.md and
dwg_native/spike/FINDINGS.md (Phase 0).
"""
__all__ = ["session", "extract", "semantic", "build", "verify", "api"]
