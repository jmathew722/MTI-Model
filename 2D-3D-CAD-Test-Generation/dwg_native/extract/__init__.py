"""Native extraction: SolidWorks import -> exact geometry + MTEXT -> raw_extraction.json."""
from .schema import (Geometry, RawExtraction, TextToken, View, load_raw, save_raw)
from .importer import import_dwg, ImportError_ as DwgImportError
from .native_extract import extract_raw

__all__ = ["Geometry", "RawExtraction", "TextToken", "View", "load_raw",
           "save_raw", "import_dwg", "DwgImportError", "extract_raw"]
