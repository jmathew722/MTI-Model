"""SolidWorks Hole Wizard enum constants — named, version-resolved (2026-07-24).

Phase 3b of the full-feature-coverage task. The Hole Wizard builder must never
carry inline magic integers for SolidWorks enums (they are version/locale
specific). This module is the single source: every enum member is named, and
:func:`resolve` reads its value from the INSTALLED swConst type library at call
time (via ``solidworks_builder._const``) so the build machine's own value wins,
falling back to the integer verified against swConst **v32 (SolidWorks 2024)**
recorded here — see ``docs/phase2-research-notes.md`` for how these were pulled.

The fallbacks let the module (and its tests) work off-Windows / without a live
SolidWorks; on a real build the live typelib value is used.
"""
from __future__ import annotations

# Verified live against the installed swConst v32 (SolidWorks 2024), 2026-07-24.
# swWzdGeneralHoleTypes_e
_FALLBACK: dict[str, int] = {
    "swWzdCounterBore": 0,
    "swWzdCounterSink": 1,
    "swWzdHole": 2,            # simple drilled hole
    "swWzdPipeTap": 3,
    "swWzdTap": 4,             # tapped
    "swWzdLegacy": 5,          # diameter-driven; no fastener-standard table lookup
    "swWzdCounterBoreSlot": 6,
    "swWzdCounterSinkSlot": 7,
    "swWzdHoleSlot": 8,
    # swEndConditions_e
    "swEndCondBlind": 0,
    "swEndCondThroughAll": 1,
    "swEndCondUpToSurface": 4,
    "swEndCondThroughAllBoth": 9,
    # swWzdHoleStandards_e
    "swStandardAnsiInch": 0,
    "swStandardAnsiMetric": 1,
}


def resolve(name: str) -> int:
    """The enum integer for ``name`` — the installed typelib's value when a live
    SolidWorks is present, else the recorded v32 fallback. Never raises for a
    known name; unknown names raise KeyError (a typo, caught in tests)."""
    fallback = _FALLBACK[name]
    try:  # live typelib wins (Windows + SolidWorks)
        from pipeline.solidworks_builder import _const
        return int(_const(name, fallback))
    except Exception:
        return fallback


# --- Named accessors (no caller ever writes a bare integer) ----------------- #
def general_hole_type(name: str) -> int:
    return resolve(name)


def end_condition(*, through_all: bool, up_to_surface: bool = False) -> int:
    if up_to_surface:
        return resolve("swEndCondUpToSurface")
    return resolve("swEndCondThroughAll") if through_all else resolve("swEndCondBlind")


def hole_standard(metric: bool) -> int:
    return resolve("swStandardAnsiMetric") if metric else resolve("swStandardAnsiInch")


# Map the schema HoleType sub-types to the swWzdGeneralHoleTypes_e MEMBER NAME.
# LEGACY placement (diameter-driven) is used for the drilled base of every hole
# so no fastener data-pack table is needed; the sub-type geometry (cbore/csk/tap)
# is added through the HoleWizard5 Value slots. See docs/phase2-research-notes.md.
_HOLE_TYPE_TO_WZD_NAME: dict[str, str] = {
    "thru": "swWzdLegacy",
    "blind": "swWzdLegacy",
    "counterbore": "swWzdCounterBore",
    "countersink": "swWzdCounterSink",
    "spotface": "swWzdCounterBore",     # a spotface is a shallow counterbore
    "tapped": "swWzdTap",
    "clearance": "swWzdLegacy",         # clearance dia comes from the ANSI table
    "simple": "swWzdHole",
}


def wizard_type_name_for(hole_subtype: str) -> str:
    """swWzdGeneralHoleTypes_e MEMBER NAME for a schema HoleType value."""
    return _HOLE_TYPE_TO_WZD_NAME.get((hole_subtype or "").lower(), "swWzdLegacy")


# --- ANSI standard-fastener CLEARANCE hole diameters (inches) --------------- #
# Nominal fastener size -> (close, normal, loose) clearance drill diameter, from
# the ANSI B18.2 / machinery-standard clearance tables. Computed here so a
# standard-fastener clearance hole is standards-correct WITHOUT the live Toolbox
# data pack (the fastener-index lookups that make HoleWizard5 fragile).
CLEARANCE_INCH: dict[str, tuple[float, float, float]] = {
    "#4":  (0.116, 0.120, 0.128),
    "#6":  (0.144, 0.150, 0.170),
    "#8":  (0.170, 0.177, 0.196),
    "#10": (0.196, 0.201, 0.228),
    "1/4": (0.257, 0.266, 0.281),
    "5/16": (0.323, 0.332, 0.348),
    "3/8": (0.386, 0.397, 0.414),
    "1/2": (0.514, 0.531, 0.563),
    "5/8": (0.641, 0.656, 0.688),
    "3/4": (0.766, 0.781, 0.813),
}
# Metric nominal (mm) -> (close, normal, loose) clearance hole diameter (mm),
# ISO 273 medium series and neighbours.
CLEARANCE_METRIC_MM: dict[str, tuple[float, float, float]] = {
    "M3":  (3.2, 3.4, 3.6),
    "M4":  (4.3, 4.5, 4.8),
    "M5":  (5.3, 5.5, 5.8),
    "M6":  (6.4, 6.6, 7.0),
    "M8":  (8.4, 9.0, 10.0),
    "M10": (10.5, 11.0, 12.0),
    "M12": (13.0, 13.5, 14.5),
    "M16": (17.0, 17.5, 18.5),
    "M20": (21.0, 22.0, 24.0),
}


def clearance_diameter_in(fastener: str, *, fit: str = "normal") -> float | None:
    """Clearance-hole diameter in INCHES for a fastener size, or None if the size
    isn't tabled. Metric sizes are converted mm->in. ``fit`` = close|normal|loose."""
    idx = {"close": 0, "normal": 1, "loose": 2}.get(fit, 1)
    key = (fastener or "").strip().upper().replace(" ", "")
    for table, to_in in ((CLEARANCE_INCH, 1.0), (CLEARANCE_METRIC_MM, 1.0 / 25.4)):
        # case-insensitive match on the table keys
        for k, vals in table.items():
            if k.upper() == key:
                return round(vals[idx] * to_in, 4)
    return None
