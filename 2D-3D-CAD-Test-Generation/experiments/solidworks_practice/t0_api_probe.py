"""Probe: which measurement / inspection APIs actually resolve on THIS install?

Late-bound win32com either resolves a method or raises AttributeError/com_error.
Rather than trust any document, call each candidate on a real body and record the
outcome. Everything downstream (validation, hole audits, volume checks) depends
on knowing which of these exist.
"""
import math

from swlab import IN, Lab, Result, _null_dispatch

lab = Lab("tier0_probe")
EC_BLIND = 0


def _make_block(doc, w=4.0, h=3.0, d=0.5):
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, w * IN, h * IN, 0)
    doc.FeatureManager.FeatureExtrusion3(
        True, False, False, EC_BLIND, EC_BLIND, d * IN, 0.01,
        False, False, False, False, 0, 0, False, False, False, False,
        True, True, True, 0, 0, False)
    return doc


def _try(label, fn):
    try:
        val = fn()
        return (label, "OK", repr(val)[:110])
    except Exception as e:
        return (label, type(e).__name__, str(e)[:110])


def probe():
    doc = lab.new_part()
    _make_block(doc)
    body = lab.bodies(doc)[0]
    rows = []

    # --- volume / mass properties: five documented routes ------------------ #
    rows.append(_try("IBody2.GetMassProperties()", lambda: list(body.GetMassProperties())))
    rows.append(_try("IBody2.GetMassProperties2(1)",
                     lambda: list(body.GetMassProperties2(1))))
    rows.append(_try("Extension.CreateMassProperty()",
                     lambda: doc.Extension.CreateMassProperty()))
    rows.append(_try("Extension.GetMassProperties2(1,null,0)",
                     lambda: list(doc.Extension.GetMassProperties2(1, _null_dispatch(), 0))))
    rows.append(_try("IModelDoc2.GetMassProperties()",
                     lambda: list(doc.GetMassProperties())))

    # --- bounding box ------------------------------------------------------ #
    rows.append(_try("IBody2.GetBodyBox()", lambda: [round(v, 5) for v in body.GetBodyBox()]))
    rows.append(_try("IPartDoc.GetPartBox(True)",
                     lambda: [round(v, 5) for v in doc.GetPartBox(True)]))

    # --- sketch contour probe (doc 08's diagnostic) ------------------------ #
    lab.open_sketch(doc, "Top Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 1 * IN, 1 * IN, 0)
    sk = doc.SketchManager.ActiveSketch
    rows.append(_try("ISketch.GetSketchContours()", lambda: len(list(sk.GetSketchContours()))))
    rows.append(_try("ISketch.GetSketchSegments()", lambda: len(list(sk.GetSketchSegments()))))
    rows.append(_try("ISketch.GetLineCount2(0)", lambda: sk.GetLineCount2(0)))
    lab.close_sketch(doc)

    # --- face / surface enumeration (doc 07's hole audit) ------------------ #
    faces = lab.faces_of(body)
    rows.append(_try("IBody2.GetFaces()", lambda: len(faces)))
    if faces:
        rows.append(_try("IFace2.GetSurface()", lambda: faces[0].GetSurface() is not None))
        rows.append(_try("ISurface.IsCylinder()",
                         lambda: faces[0].GetSurface().IsCylinder()))
        rows.append(_try("ISurface.IsPlane()", lambda: faces[0].GetSurface().IsPlane()))
        rows.append(_try("IFace2.GetArea()", lambda: round(faces[0].GetArea(), 8)))
    rows.append(_try("IBody2.GetEdges()", lambda: len(list(body.GetEdges()))))

    # --- rebuild / error reporting ----------------------------------------- #
    rows.append(_try("IModelDoc2.ForceRebuild3(True)", lambda: doc.ForceRebuild3(True)))
    rows.append(_try("IModelDoc2.GetRebuildErrorCount()",
                     lambda: doc.GetRebuildErrorCount()))
    # FirstFeature is a METHOD in the API docs but comes back as a PROPERTY
    # under late binding on this install — probe both forms.
    rows.append(_try("IModelDoc2.FirstFeature() [method]",
                     lambda: doc.FirstFeature().GetTypeName2()))
    rows.append(_try("IModelDoc2.FirstFeature [property]",
                     lambda: doc.FirstFeature.GetTypeName2()))
    feat = None
    for getter in (lambda: doc.FirstFeature, lambda: doc.FirstFeature()):
        try:
            feat = getter()
            break
        except Exception:
            continue
    if feat is not None:
        rows.append(_try("IFeature.GetErrorCode2()", lambda: feat.GetErrorCode2()))
        rows.append(_try("IFeature.GetTypeName2()", lambda: feat.GetTypeName2()))
        rows.append(_try("IFeature.GetNextFeature()", lambda: feat.GetNextFeature() is not None))

    lab.save(doc, "t0_probe_block.sldprt")
    lab.close(doc)

    print(f"\n{'API':<42}{'RESULT':<18}VALUE")
    for label, status, value in rows:
        print(f"  {label:<40}{status:<18}{value}")
    ok = [r for r in rows if r[1] == "OK"]
    return Result("0. API availability probe", True,
                  detail=f"{len(ok)}/{len(rows)} APIs resolve on this install",
                  measured={"rows": rows})


if __name__ == "__main__":
    lab.run("API availability probe", probe)
    lab.finish()
