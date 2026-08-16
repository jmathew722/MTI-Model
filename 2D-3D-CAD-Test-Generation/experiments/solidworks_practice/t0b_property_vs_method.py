"""Probe 2 — the property-vs-method rule.

Tier-0 probe 1 produced a cluster of "TypeError: 'tuple' object is not callable"
and "'str' object is not callable". That is not a missing API: it means the
member RESOLVED and returned its value, and then Python tried to CALL the
result. Under win32com late binding many zero-argument SolidWorks "methods" are
surfaced as PROPERTIES.

This probe settles, per member, which access form works — the answer decides how
every measurement in this repo has to be written.
"""
from swlab import IN, Lab, Result, _null_dispatch, sw_any, sw_get, sw_seq

lab = Lab("tier0_property_vs_method")


def _forms(owner, name, args=()):
    """Try member as property and as method; report which form works."""
    out = {}
    try:
        val = getattr(owner, name)
        if callable(val):
            out["property"] = "callable (is a method)"
        else:
            out["property"] = f"OK -> {repr(val)[:70]}"
    except Exception as e:
        out["property"] = f"{type(e).__name__}: {str(e)[:60]}"
    try:
        val = getattr(owner, name)(*args)
        out["method"] = f"OK -> {repr(val)[:70]}"
    except Exception as e:
        out["method"] = f"{type(e).__name__}: {str(e)[:60]}"
    return out


def probe():
    doc = lab.new_part()
    lab.open_sketch(doc, "Front Plane")
    doc.SketchManager.CreateCornerRectangle(0, 0, 0, 4 * IN, 3 * IN, 0)
    doc.FeatureManager.FeatureExtrusion3(
        True, False, False, 0, 0, 0.5 * IN, 0.01, False, False, False, False,
        0, 0, False, False, False, False, True, True, True, 0, 0, False)
    body = lab.bodies(doc)[0]

    checks = [
        ("IModelDoc2", doc, "GetMassProperties", ()),
        ("IModelDoc2", doc, "FirstFeature", ()),
        ("IBody2", body, "GetFaces", ()),
        ("IBody2", body, "GetEdges", ()),
        ("IBody2", body, "Name", ()),
    ]
    print(f"\n{'owner.member':<34}{'AS PROPERTY':<46}AS METHOD")
    rows = []
    for owner_name, owner, member, args in checks:
        r = _forms(owner, member, args)
        rows.append((f"{owner_name}.{member}", r["property"], r["method"]))
        print(f"  {owner_name + '.' + member:<32}{r['property']:<46}{r['method']}")

    # Faces/surfaces: the doc-07 hole audit chain, member by member.
    faces = sw_seq(body, "GetFaces")
    print(f"\n  body.GetFaces (property) -> {len(faces)} faces")
    f0 = faces[0]
    for member in ("GetSurface", "GetArea", "GetEdgeCount"):
        r = _forms(f0, member, ())
        rows.append((f"IFace2.{member}", r["property"], r["method"]))
        print(f"  {'IFace2.' + member:<32}{r['property']:<46}{r['method']}")

    surf = sw_any(f0, "GetSurface")
    for member in ("IsCylinder", "IsPlane", "CylinderParams", "PlaneParams"):
        r = _forms(surf, member, ())
        rows.append((f"ISurface.{member}", r["property"], r["method"]))
        print(f"  {'ISurface.' + member:<32}{r['property']:<46}{r['method']}")

    # The mass-property tuple: what are the fields, actually?
    mp = list(sw_get(doc, "GetMassProperties"))
    print(f"\n  IModelDoc2.GetMassProperties -> {len(mp)} values:")
    for i, v in enumerate(mp):
        print(f"      [{i}] {v!r}")

    lab.save(doc, "t0b_property_probe.sldprt")
    lab.close(doc)
    return Result("0b. property-vs-method rule", True,
                  detail=f"{len(rows)} members probed; mass-property tuple has {len(mp)} fields",
                  measured={"rows": rows, "mass_properties": mp})


if __name__ == "__main__":
    lab.run("property vs method", probe)
    lab.finish()
