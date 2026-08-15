"""Which path does a DWG take? (REFACTOR_ANALYSIS §1.6 / §2.4)

There are two DWG code paths in this repo, and until now nothing in the code
said which one handles a given file — the answer depended on which entry point
the operator happened to start from. That ambiguity is the costly part of the
redundancy: two pipelines that can silently diverge, with no single place to
look up the routing rule.

**The decision (2026-08-15): both paths stay, with explicit, non-overlapping
jobs.** ``dwg_native/`` is a real second product, not a duplicate — it is
geometry-first (SolidWorks imports the DWG and the EXACT entities drive the
build; no vision model is involved), it is Windows+SolidWorks-only, and it has
its own UI, job queue and gates. The vision pipeline is drawing-first and runs
anywhere. Neither can do the other's job. Full rationale, guiding principles and
the parity checklist: ``docs/DWG_PATHS.md``.

This module is that decision expressed as code — ONE function, so "which path"
is answered the same way by the CLI, the webapp and any future caller:

    route_for(path, ...) -> DwgRoute

The routing rule, in one sentence: **a DWG entering the vision pipeline stays in
the vision pipeline** (Stage 2.4 ``dwg_crosscheck`` gives it the DWG's exact
dimension text, which is the geometry-first benefit that is cheap to share);
**the DWG-native pipeline is entered deliberately**, by its own entry point,
when the operator wants the entities themselves to be the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# The two products.
VISION_PIPELINE = "vision_pipeline"     # main.py / webapp — drawing-first, any OS
DWG_NATIVE = "dwg_native"               # run-dwg.ps1 / :8095 — geometry-first, Windows

DWG_SUFFIXES = (".dwg",)
DXF_SUFFIXES = (".dxf",)
VECTOR_SUFFIXES = DWG_SUFFIXES + DXF_SUFFIXES + (".pdf",)


@dataclass(frozen=True)
class DwgRoute:
    """Which product handles this input, and what it will do with it."""

    route: str
    reason: str
    crosscheck: bool = False        # Stage 2.4 exact-text cross-check applies
    requires_solidworks: bool = False

    @property
    def is_native(self) -> bool:
        return self.route == DWG_NATIVE

    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route, "reason": self.reason,
                "crosscheck": self.crosscheck,
                "requires_solidworks": self.requires_solidworks}


def is_dwg(path: Path | str) -> bool:
    return Path(path).suffix.lower() in DWG_SUFFIXES


def is_vector_source(path: Path | str) -> bool:
    """Whether this file can supply exact vector geometry (Stage 3 / Stage 2.4)."""
    return Path(path).suffix.lower() in VECTOR_SUFFIXES


def route_for(path: Path | str, *, entry_point: str = VISION_PIPELINE,
              dwg_crosscheck: bool = True,
              solidworks_available: Optional[bool] = None) -> DwgRoute:
    """The single answer to "which DWG path handles this file".

    ``entry_point`` is which product the caller IS (``main.py``/webapp pass the
    default; ``dwg_native``'s API passes :data:`DWG_NATIVE`). A file never
    silently hops products: the entry point decides, and this function only
    reports what that entry point will actually do — including whether the
    geometry-first benefit (Stage 2.4's exact dimension text) is available on
    the vision path for this input.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if entry_point == DWG_NATIVE:
        if suffix not in DWG_SUFFIXES:
            return DwgRoute(DWG_NATIVE,
                            f"{suffix or 'this input'} is not a DWG — the DWG-native "
                            "pipeline imports DWG entities only",
                            requires_solidworks=True)
        return DwgRoute(DWG_NATIVE,
                        "entered through the DWG-native pipeline: SolidWorks imports "
                        "the DWG and its exact entities drive the build (no vision "
                        "model involved)",
                        requires_solidworks=True)

    if suffix in DWG_SUFFIXES:
        can_crosscheck = bool(dwg_crosscheck and (solidworks_available is not False))
        return DwgRoute(
            VISION_PIPELINE,
            ("DWG on the vision pipeline: converted for the vision read, and "
             "Stage 2.4 corrects OCR digits against the DWG's own exact dimension "
             "text" if can_crosscheck else
             "DWG on the vision pipeline: Stage 2.4 exact-text cross-check is "
             "unavailable (disabled, or no SolidWorks) — the vision reading stands "
             "on its own and is flagged as such"),
            crosscheck=can_crosscheck)

    return DwgRoute(VISION_PIPELINE,
                    f"{suffix or 'input'} is handled by the vision pipeline"
                    + (" (exact vector geometry available for hole positions)"
                       if suffix in VECTOR_SUFFIXES else ""))


def describe() -> str:
    """The routing rule as text, for reports and the explainer."""
    return (
        "DWG routing (docs/DWG_PATHS.md): a DWG entering the VISION pipeline "
        "(main.py / webapp :8092) stays there and gains Stage 2.4's exact "
        "dimension-text cross-check. The DWG-NATIVE pipeline (run-dwg.ps1 / "
        ":8095) is entered deliberately when the DWG's own entities should be the "
        "source of truth; it requires Windows + SolidWorks. Neither path silently "
        "hands a file to the other."
    )
