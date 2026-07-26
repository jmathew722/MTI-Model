"""Build layer: build_plan.json -> VBA macro (reviewable) + direct COM .SLDPRT."""
from .vba_emit import emit_vba
from .builder import build_part, BuildError

__all__ = ["emit_vba", "build_part", "BuildError"]
