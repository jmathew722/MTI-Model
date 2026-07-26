"""Rules-first 2D->3D semantic mapping. LLM only for genuine ambiguity."""
from .numbers import ParsedNumber, parse_number, parse_tokens
from .rules import (Circle, Loop, classify_circles, detect_closed_loops,
                    exclude_furniture, largest_profile_loop, parse_multipliers,
                    attach_by_proximity)
from .conflicts import ConflictError, ProvenanceError, assert_provenance, find_conflicts
from .mapper import map_to_build_plan
from .ocr_correction import correct_ocr

__all__ = ["ParsedNumber", "parse_number", "parse_tokens", "Circle", "Loop",
           "classify_circles", "detect_closed_loops", "exclude_furniture",
           "largest_profile_loop", "parse_multipliers", "attach_by_proximity",
           "ConflictError", "ProvenanceError", "assert_provenance",
           "find_conflicts", "map_to_build_plan", "correct_ocr"]
