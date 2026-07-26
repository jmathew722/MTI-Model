"""DWG-native pipeline: DWG -> SolidWorks import -> exact geometry+text -> part.

New top-level package (a branch of the MTI repo). Shared modules
(pipeline/coordinate_normalize.py, pipeline/solidworks_builder.py) are IMPORTED,
never copied — one canonical coordinate resolver, one place to fix a bug. See
docs/dwg-native/architecture.md and dwg_native/spike/FINDINGS.md (Phase 0).
"""
__all__ = ["session", "extract", "semantic", "build", "verify", "api"]
