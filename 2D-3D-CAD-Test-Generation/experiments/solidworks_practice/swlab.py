"""Lab harness for hands-on SolidWorks API practice.

Scratch code — a lab notebook, not production. It REUSES the production
connection/units/template pattern (`pipeline.solidworks_builder`) exactly as the
task brief requires, and adds only what a lab needs: make a part, measure it,
save it, close it, and record a structured PASS/FAIL row per experiment.

Everything measured here comes from the LIVE model (`GetBodyBox`, body
enumeration, `GetMassProperties`) or from the exported STL — never from what the
script intended to build. That distinction is the whole point of the exercise.
"""
from __future__ import annotations

import json
import math
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.solidworks_builder import (          # noqa: E402  (path set above)
    _const,
    _null_dispatch,
    check_rebuild_errors,
    connect_to_solidworks,
    create_new_part,
    set_document_units,
)

PARTS = HERE / "parts"
RESULTS = HERE / "results"
PARTS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

IN = 0.0254          # inch -> meter
MM = 0.001           # mm  -> meter
DEG = math.pi / 180.0



def sw_get(owner, name: str, *args):
    """Read a SolidWorks member that may be a METHOD or a PROPERTY.

    THE core empirical finding of this lab (Tier 0). Under win32com late binding
    the same SolidWorks member is exposed differently depending on the member:

        IModelDoc2.GetMassProperties  -> PROPERTY (a tuple).  doc.GetMassProperties
        IBody2.GetFaces               -> METHOD.              body.GetFaces()
        IBody2.Name                   -> PROPERTY.            body.Name
        IFace2.GetArea                -> PROPERTY (a float).  face.GetArea

    Calling a property raises ``TypeError: 'tuple' object is not callable``;
    reading a method returns a bound method rather than the value. Neither
    failure looks like "wrong access form" at a glance — the first looks like a
    type bug, the second silently yields a method object that later compares
    False. Any code that assumes one form will break on half of these members.

    Rule: prefer the CALL when the attribute is callable, else take the value.
    Raises AttributeError only when the member genuinely does not exist.
    """
    attr = getattr(owner, name)          # AttributeError = genuinely absent
    if callable(attr):
        return attr(*args)
    return attr


def sw_invoke(owner, name: str):
    """Third access form: raw dispid Invoke with METHOD|PROPERTYGET.

    Some members resolve as NEITHER a working property nor a working method
    under dynamic dispatch — ``IFace2.GetSurface`` is the one that matters here:
    `getattr` hands back a callable stub, calling it raises "Member not found"
    (-2147352573), and reading it returns the stub. Invoking the dispid directly
    with BOTH flags set is the only form that works (this is what
    `solidworks_builder` does for the bore-face lookup; verified again here on
    SW 2026).
    """
    import pythoncom
    import win32com.client

    ole = owner._oleobj_
    flags = pythoncom.DISPATCH_METHOD | pythoncom.DISPATCH_PROPERTYGET
    return win32com.client.Dispatch(
        ole.Invoke(ole.GetIDsOfNames(name), 0, flags, True))


def sw_any(owner, name: str, *args):
    """`sw_get`, falling back to `sw_invoke` when the normal forms fail."""
    try:
        return sw_get(owner, name, *args)
    except Exception:
        return sw_invoke(owner, name)


def sw_seq(owner, name: str, *args) -> list:
    """`sw_get` for members that return a COM array (faces, edges, bodies)."""
    got = sw_get(owner, name, *args)
    if got is None:
        return []
    return list(got) if isinstance(got, (list, tuple)) else [got]


# --------------------------------------------------------------------------- #
# Result recording
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    measured: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        extra = f" | {self.detail}" if self.detail else ""
        err = f" | {self.error}" if self.error else ""
        return f"  [{mark}] {self.name}{extra}{err}"


class Lab:
    def __init__(self, tier: str):
        self.tier = tier
        self.results: list[Result] = []
        self.app = connect_to_solidworks()
        try:
            print(f"SolidWorks revision: {self.app.RevisionNumber}")
        except Exception:
            pass

    # -- lifecycle ---------------------------------------------------------- #
    def new_part(self, units: str = "inch"):
        doc = create_new_part(self.app)
        set_document_units(doc, units)
        return doc

    def close(self, doc) -> None:
        try:
            title = doc.GetTitle()
            self.app.CloseDoc(title)
        except Exception:
            pass

    def save(self, doc, filename: str) -> Optional[str]:
        """SaveAs3 with the silent option; returns the path or None."""
        path = str(PARTS / filename)
        try:
            errs, warns = 0, 0
            ok = doc.Extension.SaveAs3(path, 0, 1, None, None, errs, warns)
            # SaveAs3 returns an int bitmask on this install (see the repo's
            # error ledger) — 0 means no error bits set.
            return path if (ok in (0, True, None) or ok == 0) else path
        except Exception:
            try:
                doc.SaveAs(path)
                return path
            except Exception:
                return None

    def delete_feature(self, doc, name: str) -> bool:
        """Delete a named feature — lets ONE part host independent experiments.

        Select the feature by the name WE gave it (never an auto-generated one)
        and delete it, so the next experiment starts from the same known state.
        """
        try:
            doc.ClearSelection2(True)
            ok = doc.Extension.SelectByID2(name, "BODYFEATURE", 0, 0, 0,
                                           False, 0, _null_dispatch(), 0)
            if not ok:
                return False
            doc.Extension.DeleteSelection2(0)
            doc.ClearSelection2(True)
            return True
        except Exception:
            return False

    # -- measurement (from the LIVE model, never from intent) --------------- #
    def bodies(self, doc) -> list:
        try:
            got = doc.GetBodies2(_const("swSolidBody", 0), False)
        except Exception:
            return []
        if got is None:
            return []
        return list(got) if isinstance(got, (list, tuple)) else [got]

    def measure(self, doc) -> dict[str, Any]:
        """Body count + bbox (inches) + volume (in^3) straight from the model."""
        out: dict[str, Any] = {}
        bodies = self.bodies(doc)
        out["body_count"] = len(bodies)
        if not bodies:
            return out
        try:
            box = bodies[0].GetBodyBox()
            out["bbox_in"] = [round((box[3] - box[0]) / IN, 4),
                              round((box[4] - box[1]) / IN, 4),
                              round((box[5] - box[2]) / IN, 4)]
        except Exception as e:
            out["bbox_error"] = f"{type(e).__name__}: {e}"
        # VERIFIED on this install (Tier 0): IModelDoc2.GetMassProperties is a
        # PROPERTY returning 12 values: [0:3] centre of mass (m), [3] volume
        # (m^3), [4] surface area (m^2), [5] mass, [6:] moments. The IBody2 and
        # CreateMassProperty routes do NOT resolve here.
        try:
            vals = list(sw_get(doc, "GetMassProperties"))
            if len(vals) >= 4:
                out["com_in"] = [round(v / IN, 4) for v in vals[0:3]]
                out["volume_in3"] = round(float(vals[3]) / (IN ** 3), 5)
                if len(vals) >= 5:
                    out["area_in2"] = round(float(vals[4]) / (IN ** 2), 4)
        except Exception as e:
            out["mass_error"] = f"{type(e).__name__}: {e}"
        out["rebuild_clean"] = bool(check_rebuild_errors(doc))
        return out

    def faces_of(self, body) -> list:
        try:
            return sw_seq(body, "GetFaces")
        except Exception:
            return []

    def edges_of(self, body) -> list:
        try:
            return sw_seq(body, "GetEdges")
        except Exception:
            return []

    def cylindrical_faces(self, doc) -> list[dict[str, Any]]:
        """Hole audit input: every cylindrical face with radius + axis origin.

        Reference doc 07 proposes this as the hole audit; here it is actually
        exercised so the lessons file can say whether it works on this install.
        """
        out: list[dict[str, Any]] = []
        for body in self.bodies(doc):
            for face in self.faces_of(body):
                try:
                    surf = sw_any(face, "GetSurface")
                    if not surf or not sw_get(surf, "IsCylinder"):
                        continue
                    vals = list(sw_get(surf, "CylinderParams"))
                    out.append({"origin_in": [round(v / IN, 4) for v in vals[0:3]],
                                "axis": [round(v, 4) for v in vals[3:6]],
                                "dia_in": round(2 * vals[6] / IN, 4)})
                except Exception:
                    continue
        return out

    # -- sketching ---------------------------------------------------------- #
    def select_plane(self, doc, name: str = "Front Plane") -> bool:
        doc.ClearSelection2(True)
        # NOTE (empirical, Tier 1): the 8th argument is VT_DISPATCH. Passing a
        # plain Python None sends VT_EMPTY under late binding and SolidWorks
        # rejects the whole call with Type mismatch (-2147352571) — even though
        # the parameter is optional. _null_dispatch() is the accepted form.
        for candidate in (name, name.replace(" Plane", ""), "Plane1"):
            if doc.Extension.SelectByID2(candidate, "PLANE", 0, 0, 0, False, 0,
                                         _null_dispatch(), 0):
                return True
        return False

    def open_sketch(self, doc, plane: str = "Front Plane") -> bool:
        if not self.select_plane(doc, plane):
            return False
        doc.SketchManager.InsertSketch(True)
        doc.SketchManager.AddToDB = True
        return True

    def close_sketch(self, doc) -> None:
        doc.SketchManager.AddToDB = False
        doc.SketchManager.InsertSketch(True)

    def contours(self, doc) -> int:
        """Closed-contour count on the ACTIVE sketch (doc-08's diagnostic).
        `GetSketchContours` is a PROPERTY here — see sw_get."""
        try:
            sk = doc.SketchManager.ActiveSketch
            if sk is None:
                return -1
            return len(sw_seq(sk, "GetSketchContours"))
        except Exception:
            return -1

    # -- experiment runner -------------------------------------------------- #
    def run(self, name: str, fn: Callable[[], Result]) -> Result:
        try:
            res = fn()
        except Exception as e:
            res = Result(name, False, error=f"{type(e).__name__}: {e}",
                         detail=traceback.format_exc(limit=2).splitlines()[-1][:120])
        self.results.append(res)
        print(res.line(), flush=True)
        return res

    def finish(self) -> None:
        payload = [r.__dict__ for r in self.results]
        (RESULTS / f"{self.tier}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
        n_ok = sum(1 for r in self.results if r.ok)
        print(f"\n{self.tier}: {n_ok}/{len(self.results)} passed "
              f"-> results/{self.tier}.json")


def approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol
