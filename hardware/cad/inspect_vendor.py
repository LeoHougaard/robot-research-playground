"""Read the original vendor STEP using the existing OpenCascade Python package."""
import json
from pathlib import Path
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder

root = Path(__file__).parent
reader = STEPControl_Reader()
assert reader.ReadFile(str(root / 'vendor/st3020 servo.stp')) == IFSelect_RetDone
reader.TransferRoots()
shape = reader.OneShape()
solids = []
ex = TopExp_Explorer(shape, TopAbs_SOLID)
while ex.More():
    solid = ex.Current()
    bbox = Bnd_Box()
    BRepBndLib.Add_s(solid, bbox)
    cylinders = []
    faces = TopExp_Explorer(solid, TopAbs_FACE)
    while faces.More():
        surf = BRepAdaptor_Surface(TopoDS.Face(faces.Current()))
        if surf.GetType() == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            p, d = cyl.Location(), cyl.Axis().Direction()
            cylinders.append(dict(radius_mm=round(cyl.Radius(), 5),
                center_mm=[round(v, 5) for v in (p.X(),p.Y(),p.Z())],
                axis=[round(v, 5) for v in (d.X(),d.Y(),d.Z())]))
        faces.Next()
    lo, hi = bbox.CornerMin(), bbox.CornerMax()
    solids.append(dict(bounds_mm=[lo.X(),lo.Y(),lo.Z(),hi.X(),hi.Y(),hi.Z()], cylinders=cylinders))
    ex.Next()
out = root / 'vendor/st3020_step_measurements.json'
out.write_text(json.dumps(solids, indent=2))
print(f'{len(solids)} solids written to {out}')
for i, s in enumerate(solids):
    b=s['bounds_mm']
    print(i, [round(b[k+3]-b[k],3) for k in range(3)],
        'cylinder radii', sorted(set(c['radius_mm'] for c in s['cylinders'])))
