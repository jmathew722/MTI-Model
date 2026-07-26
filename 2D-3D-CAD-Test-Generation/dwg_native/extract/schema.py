"""raw_extraction.json schema — ONLY what SolidWorks/ezdxf reported, zero
interpretation. Every value carries provenance (view, object type, 2D coords).

Plain dataclasses (no pydantic dependency) so the semantic layer + tests load
without the pipeline's heavier deps. Coordinates are in METERS (SolidWorks system
units); the one inch<->meter conversion lives in pipeline.coordinate_normalize.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Geometry:
    """One sketch/geometry entity as SolidWorks converted it."""
    id: str
    type: str                       # line | arc | circle | ellipse | point | insert
    start_2d_m: Optional[List[float]] = None
    end_2d_m: Optional[List[float]] = None
    center_2d_m: Optional[List[float]] = None
    radius_m: Optional[float] = None
    layer: str = ""
    construction: bool = False


@dataclass
class TextToken:
    """One MTEXT/text string with its sheet position. The dimension NUMBERS live
    here (Phase 0 RED: they do not survive as dimension objects)."""
    id: str
    text: str
    position_2d_m: List[float]
    height_m: float = 0.0
    layer: str = ""


@dataclass
class DisplayDimension:
    """A native IDisplayDimension IF one survived import. Recorded honestly even
    when value is None (Phase 0: imported DWG display dims are value-less shells)."""
    id: str
    value_m: Optional[float]
    name: Optional[str]
    text: Optional[str]
    position_2d_m: Optional[List[float]] = None
    has_live_idimension: bool = False


@dataclass
class View:
    name: str
    type: int = 0
    geometry: List[Geometry] = field(default_factory=list)
    text_tokens: List[TextToken] = field(default_factory=list)
    display_dimensions: List[DisplayDimension] = field(default_factory=list)


@dataclass
class RawExtraction:
    source_file: str
    units_detected: str = "inch"
    sheet: Dict[str, float] = field(default_factory=dict)
    views: List[View] = field(default_factory=list)
    unrecognized_objects: List[Dict[str, Any]] = field(default_factory=list)
    import_notes: List[str] = field(default_factory=list)

    # -- convenience rollups used by the semantic layer -------------------- #
    def all_geometry(self) -> List[Geometry]:
        return [g for v in self.views for g in v.geometry]

    def all_text(self) -> List[TextToken]:
        return [t for v in self.views for t in v.text_tokens]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file": self.source_file,
            "units_detected": self.units_detected,
            "sheet": self.sheet,
            "views": [
                {
                    "name": v.name, "type": v.type,
                    "geometry": [asdict(g) for g in v.geometry],
                    "text_tokens": [asdict(t) for t in v.text_tokens],
                    "display_dimensions": [asdict(d) for d in v.display_dimensions],
                }
                for v in self.views
            ],
            "unrecognized_objects": self.unrecognized_objects,
            "import_notes": self.import_notes,
        }


def save_raw(raw: RawExtraction, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw.to_dict(), indent=2), encoding="utf-8")
    return p


def load_raw(path: str | Path) -> RawExtraction:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    views = []
    for v in d.get("views", []):
        views.append(View(
            name=v.get("name", ""), type=v.get("type", 0),
            geometry=[Geometry(**g) for g in v.get("geometry", [])],
            text_tokens=[TextToken(**t) for t in v.get("text_tokens", [])],
            display_dimensions=[DisplayDimension(**dd)
                                for dd in v.get("display_dimensions", [])],
        ))
    return RawExtraction(
        source_file=d.get("source_file", ""),
        units_detected=d.get("units_detected", "inch"),
        sheet=d.get("sheet", {}),
        views=views,
        unrecognized_objects=d.get("unrecognized_objects", []),
        import_notes=d.get("import_notes", []),
    )
