"""What does the feature tree actually report? (settles the RefAxis lookup)"""
import math
from swlab import IN, Lab, _null_dispatch, sw_any, sw_get

lab = Lab("probe_tree")
DOC = lab.new_part()
lab.open_sketch(DOC, "Front Plane")
DOC.SketchManager.CreateCircleByRadius(0, 0, 0, 3 * IN)
DOC.SketchManager.AddToDB = False
DOC.SketchManager.InsertSketch(True)
DOC.FeatureManager.FeatureExtrusion3(True, False, False, 0, 0, 0.5 * IN, 0.01,
    False, False, False, False, 0, 0, False, False, False, False, True, True,
    True, 0, 0, False)

# make an axis from the outer cylindrical face
made = False
for body in lab.bodies(DOC):
    for face in lab.faces_of(body):
        try:
            surf = sw_any(face, "GetSurface")
            if surf and sw_get(surf, "IsCylinder"):
                DOC.ClearSelection2(True)
                if face.Select4(False, _null_dispatch()):
                    made = DOC.InsertAxis2(True)
                break
        except Exception:
            continue
    if made:
        break
print("InsertAxis2 returned:", made)

print("\nfeature tree (name | GetTypeName2):")
feat = sw_get(DOC, "FirstFeature")
n = 0
while feat is not None and n < 40:
    n += 1
    try:
        nm = sw_get(feat, "Name")
    except Exception as e:
        nm = f"<{type(e).__name__}>"
    try:
        tn = sw_get(feat, "GetTypeName2")
    except Exception as e:
        tn = f"<{type(e).__name__}: {str(e)[:40]}>"
    print(f"  {n:>2}. {str(nm)[:34]:<36}{tn}")
    try:
        feat = sw_get(feat, "GetNextFeature")
    except Exception as e:
        print("   walk stopped:", type(e).__name__, str(e)[:60])
        break
lab.close(DOC)
