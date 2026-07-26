"""Rules-first 2D->3D semantic mapping. LLM only for genuine ambiguity."""
from .numbers import ParsedNumber, parse_number, parse_tokens
from .rules import (Circle, Loop, classify_circles, classify_hole_callout,
                    detect_closed_loops, detect_rectangle_profile, exclude_furniture,
                    gauge_thickness, largest_profile_loop, parse_multipliers,
                    rectangle_candidates, select_rectangle_profile, attach_by_proximity)
from .conflicts import ConflictError, ProvenanceError, assert_provenance, find_conflicts
from .mapper import map_to_build_plan
from .ocr_correction import correct_ocr

__all__ = ["ParsedNumber", "parse_number", "parse_tokens", "Circle", "Loop",
           "classify_circles", "classify_hole_callout", "detect_closed_loops",
           "detect_rectangle_profile", "exclude_furniture", "gauge_thickness",
           "largest_profile_loop", "parse_multipliers", "rectangle_candidates",
           "select_rectangle_profile", "attach_by_proximity",
           "ConflictError", "ProvenanceError", "assert_provenance",
           "find_conflicts", "map_to_build_plan", "correct_ocr"]
