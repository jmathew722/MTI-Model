"""Phase D — construction-method dispatch config (the machine-readable half of
``pipeline/METHODS.md``).

The method library (``METHODS.md``) is the human-readable, evidence-backed record
of which SolidWorks/CadQuery construction recipe is proven for each feature class.
This module is the small config the pipeline actually reads at dispatch time, so a
session that discovers a better method (via ``construction_experiment.py``,
Phase D) turns that finding into permanent pipeline behavior instead of a one-off
fix. Defaults below reflect what the golden set + live SolidWorks 2024 have
actually verified — see METHODS.md for the evidence per entry.

The config is intentionally tiny and override-friendly:
  * defaults live here (verified);
  * ``methods.json`` next to this file, if present, overrides per key;
  * an env var ``MTI_METHOD_<CLASS>`` overrides a single class for a run.
Unknown/absent keys fall back to the safe default, never an error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Verified defaults (feature class -> construction method id).
#   hole:  sketch_circle_cut  — the ONLY hole method. The HoleWizard5 route was
#          removed 2026-08-16: live verification on SolidWorks 2026 (rev 34.3.2)
#          returned None from the API for four different parameter mappings on a
#          clean part with the points placed (pipeline/experimental/README.md).
#   slot:  slot2d (CadQuery) / create_sketch_slot (SolidWorks) — one API call
#          yields a closed obround; verified headless via CadQuery.
#   cut:   sketch_rect_cut with ORIGIN-ANCHORED coordinates (each cut sketch is
#          dimensioned to the part origin, never chained to a prior sketch, so an
#          upstream correction never cascades). Verified on the golden set.
_DEFAULTS: dict[str, str] = {
    "hole": "sketch_circle_cut",
    "hole_cbore": "sketch_circle_cut",   # + second concentric blind cut
    "hole_csk": "sketch_circle_cut",
    "hole_tapped": "sketch_circle_cut",  # drill + cosmetic thread
    "slot": "slot2d",
    "cut": "sketch_rect_cut",
}

_KNOWN_METHODS = {
    "hole": {"sketch_circle_cut"},
    "slot": {"slot2d", "create_sketch_slot", "capsule_profile"},
    "cut": {"sketch_rect_cut"},
}


def _config_path() -> Path:
    return Path(__file__).with_name("methods.json")


def load_methods() -> dict[str, str]:
    """Effective method map: defaults <- methods.json <- env overrides."""
    methods = dict(_DEFAULTS)
    p = _config_path()
    if p.is_file():
        try:
            override = json.loads(p.read_text(encoding="utf-8"))
            for k, v in (override.get("methods") or override).items():
                if isinstance(v, str):
                    methods[k] = v
        except Exception:
            pass  # a malformed override never breaks dispatch
    for k in list(methods):
        env = os.getenv(f"MTI_METHOD_{k.upper()}")
        if env:
            methods[k] = env
    return methods


def method_for(feature_class: str) -> str:
    """Preferred construction method id for a feature class (safe default if
    unknown)."""
    methods = load_methods()
    return methods.get(feature_class, _DEFAULTS.get(feature_class, ""))


def is_known(feature_class: str, method: str) -> bool:
    return method in _KNOWN_METHODS.get(feature_class.split("_")[0], set())
