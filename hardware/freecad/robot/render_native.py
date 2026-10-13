"""Render the tessellation exported by the real FreeCAD kernel, with no GUI GL."""
import ast
import json
import math
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'hardware/cad'))
import generate as g

g.OUT=HERE/'previews'
g.OUT.mkdir(exist_ok=True)
tree=ast.parse((ROOT/'hardware/cad/generate.py').read_text(encoding='utf-8'))
renderer=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='render')
class Footer(ast.NodeTransformer):
    def visit_Constant(self,node):
        if isinstance(node.value,str) and node.value.startswith('Actual exported meshes.'):
            node.value='Analytic FreeCAD geometry. Vendor references need physical fit measurements.'
        return node
renderer=Footer().visit(renderer)
exec(compile(ast.fix_missing_locations(ast.Module(body=[renderer],type_ignores=[])),str(__file__),'exec'),g.__dict__)
records=json.loads((HERE/'preview_geometry.json').read_text(encoding='utf-8'))

def items(selected,elevation,azimuth):
    az,el=math.radians(azimuth),math.radians(elevation)
    u=g.np.array([math.cos(az),math.sin(az),0])
    v=g.np.array([-math.sin(az)*math.sin(el),math.cos(az)*math.sin(el),math.cos(el)])
    depth=g.np.cross(u,v)
    out=[]
    for r in selected:
        mesh=g.trimesh.Trimesh(vertices=r['vertices_mm'],faces=r['faces'],process=False)
        mask=mesh.face_normals@depth>1e-10
        mesh.update_faces(mask)
        color='#738a98'
        if r['purchased_reference']:color='#178e9a'
        elif r['part'].startswith(('12_','20_','21_','40_','51_')):color='#d9963c'
        elif r['part'].startswith('13_'):color='#303943'
        out.append((mesh,color))
    return out

robot=[r for r in records if r['purchased_reference'] or (r['part'][:2].isdigit() and 10<=int(r['part'][:2])<32)]
print('Rendering actual native robot geometry',flush=True)
g.render(items(robot,30,-55),'native_robot.png','Native CAD. Robot with vendor servo and caster references',elevation=30,azimuth=-55)
print('Rendering actual native arena geometry',flush=True)
g.render(items(records,90,0),'native_arena_top.png','Native CAD. Robot, gate, objects and calibration mounts',elevation=90,azimuth=0)
print('Native previews complete',flush=True)
