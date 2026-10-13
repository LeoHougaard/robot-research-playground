"""Build analytic, editable FreeCAD CSG documents and STEP from the V1 source.

Run with portable FreeCADCmd, not the workspace Python. No mesh is converted to CAD.
The existing manifold constructor functions are reused through an analytic adapter.
"""
import ast
import collections
import hashlib
import json
import math
import os
import sys
import traceback
from pathlib import Path

import FreeCAD as App
import Part
import Import

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'hardware/cad/generate.py'
PARAMS=ROOT/'hardware/cad/parameters.json'
NATIVE=ROOT/'hardware/native/robot'
STEP=ROOT/'hardware/step/robot'
HERE=Path(__file__).resolve().parent
for folder in (NATIVE,STEP): folder.mkdir(parents=True,exist_ok=True)
P=json.loads(PARAMS.read_text(encoding='utf-8'))

def visibility(obj,value):
    view=getattr(obj,'ViewObject',None)
    if view is not None: view.Visibility=value

class Solid:
    def __init__(self,builder,obj):self.b,self.obj=builder,obj
    def __add__(self,other):return self.b.boolean('Part::Fuse',self,other,'Union')
    def __sub__(self,other):return self.b.boolean('Part::Cut',self,other,'Cut')
    def __xor__(self,other):return self.b.boolean('Part::Common',self,other,'Intersection')
    def translate(self,xyz):
        obj=self.b.new('Part::Compound','Translate')
        obj.Links=[self.obj]
        obj.Placement=App.Placement(App.Vector(*[float(v) for v in xyz]),App.Rotation())
        visibility(self.obj,False)
        return Solid(self.b,obj)
    def rotate(self,angles):
        rx,ry,rz=[float(v) for v in angles]
        rotation=App.Rotation(App.Vector(0,0,1),rz).multiply(
            App.Rotation(App.Vector(0,1,0),ry)).multiply(App.Rotation(App.Vector(1,0,0),rx))
        obj=self.b.new('Part::Compound','Rotate')
        obj.Links=[self.obj];obj.Placement=App.Placement(App.Vector(),rotation)
        visibility(self.obj,False)
        return Solid(self.b,obj)

class Builder:
    def __init__(self,doc,prefix):
        self.doc,self.prefix,self.counter=doc,prefix,0
        self.group=doc.addObject('App::DocumentObjectGroup',prefix+'_Construction')
        self.group.Label=prefix+' editable construction'
    def new(self,kind,label):
        self.counter+=1
        obj=self.doc.addObject(kind,self.prefix+'_'+label+str(self.counter))
        obj.Label=label
        self.group.addObject(obj)
        return obj
    def cube(self,size,center=False):
        obj=self.new('Part::Box','Box')
        obj.Length,obj.Width,obj.Height=[float(v) for v in size]
        obj.Label='Box %.4g x %.4g x %.4g mm'%tuple(float(v) for v in size)
        if center:obj.Placement.Base=App.Vector(*[-float(v)/2 for v in size])
        return Solid(self,obj)
    def cylinder(self,height,radius_low,radius_high=-1,circular_segments=0,center=False):
        assert radius_high in (-1,radius_low),'V1 uses exact cylinders only'
        obj=self.new('Part::Cylinder','Cylinder')
        obj.Radius=float(radius_low);obj.Height=float(height)
        obj.Label='Cylinder radius %.4g, height %.4g mm'%(radius_low,height)
        if center:obj.Placement.Base=App.Vector(0,0,-float(height)/2)
        return Solid(self,obj)
    def boolean(self,kind,left,right,label):
        assert left.b is self and right.b is self
        obj=self.new(kind,label)
        obj.Base=left.obj;obj.Tool=right.obj
        if 'Refine' in obj.PropertiesList:obj.Refine=True
        visibility(left.obj,False);visibility(right.obj,False)
        return Solid(self,obj)

class ManifoldNamespace:
    def __init__(self,builder):self.Manifold=builder

def load_constructors():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes=[];in_parts=False
    for node in tree.body:
        if isinstance(node,ast.FunctionDef):
            if node.name=='mesh':break
            nodes.append(node)
        elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PARTS' for t in node.targets):
            in_parts=True;nodes.append(node)
        elif in_parts:nodes.append(node)
    env={'P':P,'math':math,'N':96,'m':None}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),env)
    return env

ENV=load_constructors()

def shape_stats(shape):
    bounds=shape.BoundBox
    surfaces=collections.Counter(type(f.Surface).__name__ for f in shape.Faces)
    return dict(bounds_mm=[[bounds.XMin,bounds.YMin,bounds.ZMin],[bounds.XMax,bounds.YMax,bounds.ZMax]],
                volume_mm3=shape.Volume,solid_count=len(shape.Solids),face_count=len(shape.Faces),
                surface_types=dict(surfaces),valid=shape.isValid(),closed=shape.isClosed())

def final_part(doc,name,constructor):
    builder=Builder(doc,name)
    ENV['m']=ManifoldNamespace(builder)
    solid=constructor()
    final=doc.addObject('Part::Compound','FinalPart' if doc.Name.startswith('Part_') else name+'_FinalPart')
    final.Label=name
    final.Links=[solid.obj]
    final.addProperty('App::PropertyString','SourceConstructor','Generation').SourceConstructor=constructor.__name__
    final.addProperty('App::PropertyString','SourceParametersJSON','Generation').SourceParametersJSON=json.dumps(P,sort_keys=True)
    final.addProperty('App::PropertyString','SourceSHA256','Generation').SourceSHA256=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    final.addProperty('App::PropertyString','EditInstructions','Generation').EditInstructions='Edit Box/Cylinder dimensions or placements in the native CSG tree, then recompute. JSON changes require regenerating with this script.'
    doc.recompute()
    visibility(builder.group,False);visibility(final,True)
    stats=shape_stats(final.Shape)
    assert stats['valid'] and stats['closed'] and stats['solid_count']==1 and stats['volume_mm3']>0,(name,stats)
    return final,builder,stats

def relative(path):return str(path.relative_to(ROOT)).replace('\\','/')

def save_part(name,fn,quantity,material,orientation,mesh_records):
    doc=App.newDocument('Part_'+name)
    final,builder,stats=final_part(doc,name,fn)
    assert final.Name=='FinalPart',final.Name
    native=NATIVE/(name+'.FCStd');step=STEP/(name+'.step')
    doc.saveAs(str(native));Part.export([final],str(step))
    imported=Part.read(str(step))
    assert imported.isValid() and len(imported.Solids)==1 and imported.isClosed(),name
    assert abs(imported.Volume-stats['volume_mm3'])<max(1e-5,stats['volume_mm3']*1e-7),name
    mesh=mesh_records[name]
    delta=(stats['volume_mm3']-mesh['volume_mm3'])/stats['volume_mm3']
    extents=[stats['bounds_mm'][1][i]-stats['bounds_mm'][0][i] for i in range(3)]
    bounds_delta=max(abs(extents[i]-mesh['dimensions_mm'][i]) for i in range(3))
    assert abs(delta)<.01,(name,'analytic versus mesh volume',delta)
    assert bounds_delta<.03,(name,'analytic versus mesh bounds',bounds_delta)
    entry=dict(id=name,quantity=quantity,material=material,print_orientation=orientation,
        native_path=relative(native),step_path=relative(step),final_object=final.Name,
        feature_count=builder.counter,stl_path='hardware/prints/'+name+'.stl',
        mesh_volume_mm3=mesh['volume_mm3'],mesh_volume_relative_error=delta,
        mesh_bounds_max_error_mm=bounds_delta,**stats)
    print('EXPORTED',name,'features',builder.counter,'volume',round(stats['volume_mm3'],3),flush=True)
    App.closeDocument(doc.Name)
    return entry

def placement(xyz,angles=(0,0,0)):
    rx,ry,rz=angles
    r=App.Rotation(App.Vector(0,0,1),rz).multiply(App.Rotation(App.Vector(0,1,0),ry)).multiply(App.Rotation(App.Vector(1,0,0),rx))
    return App.Placement(App.Vector(*xyz),r)

def assembly():
    doc=App.newDocument('RobotPlaygroundAssembly')
    masters={}
    for name,(fn,qty,material,orientation) in ENV['PARTS'].items():
        if int(name[:2])<10:continue
        final,builder,stats=final_part(doc,name,fn);visibility(final,False)
        masters[name]=final
    assembly_group=doc.addObject('App::DocumentObjectGroup','AssembledComponents')
    assembly_group.Label='Assembled components, editable placements'
    links=[];instances=[]
    def add(name,xyz,angles=(0,0,0),label=None):
        obj=doc.addObject('App::Link','Instance_'+str(len(links)))
        obj.setLink(masters[name]);obj.LinkPlacement=placement(xyz,angles)
        obj.Label=label or name;assembly_group.addObject(obj)
        visibility(obj,True);links.append(obj)
        instances.append(dict(part=name,label=obj.Label,translation_mm=list(xyz),rotation_deg=list(angles)))
    def add_reference(name,shape,place,source_url,label):
        obj=doc.addObject('Part::Feature',name)
        obj.Label=label;obj.Shape=shape;obj.Placement=place
        obj.addProperty('App::PropertyString','SourceURL','Purchased reference').SourceURL=source_url
        obj.addProperty('App::PropertyString','FitStatus','Purchased reference').FitStatus='Vendor model reference. Actual delivered revision, fit, and bearing load unverified.'
        assembly_group.addObject(obj);links.append(obj);visibility(obj,True)
        instances.append(dict(part=name,label=label,purchased_reference=True,source_url=source_url))
    # Robot in the arena. Axle origin is300,300 mm; all part sources remain unshifted.
    ox,oy=300,300
    def robot(name,xyz,angles=(0,0,0),label=None):add(name,(xyz[0]+ox,xyz[1]+oy,xyz[2]),angles,label)
    robot('10_chassis',(0,0,14))
    for sign in (-1,1):
        robot('11_drive_servo_cradle',(-10,sign*40,18),label='Drive cradle '+str(sign))
        angle=-90 if sign==1 else 90
        robot('12_drive_wheel',(0,sign*72,32.5),(angle,0,0))
        robot('13_tpu_tire',(0,sign*73.5,32.5),(angle,0,0))
    robot('14_caster_spacer',(-82,0,10.1))
    robot('20_moving_jaw',(60,23,2))
    robot('21_fixed_jaw',(0,0,2))
    robot('22_gripper_servo_tower',(0,0,18))
    robot('30_marker_tower',(-45,0,P['marker_top_height']),(180,0,0))
    robot('31_tether_mast',(-95,0,18))
    vendor_servo_path=ROOT/'hardware/cad/vendor/st3020 servo.stp'
    vendor_servo=Part.read(str(vendor_servo_path))
    assert len(vendor_servo.Solids)==17,'Original servo STEP structure changed'
    # Original STEP solids0..8 and15..16 are the servo;9..14 are supplied brackets/screws.
    servo_shape=Part.makeCompound([vendor_servo.Solids[i] for i in list(range(9))+[15,16]])
    shaft_x,shaft_y,face_z=-3.098899,10.1264857,65.8620719
    def axes_rotation(columns):
        matrix=App.Matrix()
        for col,values in enumerate(columns,1):
            for row,value in enumerate(values,1):setattr(matrix,'A%d%d'%(row,col),float(value))
        return App.Rotation(matrix)
    servo_url='https://files.waveshare.com/upload/6/61/St3020_servo.rar'
    for sign in (-1,1):
        rotation=axes_rotation(((0,0,sign),(1,0,0),(0,sign,0)))
        origin=App.Vector(ox-shaft_y,oy+sign*(72-face_z),32.5-sign*shaft_x)
        add_reference('VendorDriveServo'+str(sign),servo_shape,App.Placement(origin,rotation),servo_url,'ST3020 drive servo vendor reference '+str(sign))
    grip_rotation=axes_rotation(((0,1,0),(1,0,0),(0,0,-1)))
    grip_origin=App.Vector(ox+60-shaft_y,oy+23-shaft_x,12+face_z)
    add_reference('VendorGripperServo',servo_shape,App.Placement(grip_origin,grip_rotation),servo_url,'ST3020 gripper vendor reference')
    caster_shape=Part.read(str(HERE/'pololu950.step'))
    caster_rotation=App.Rotation(App.Vector(0,0,1),90).multiply(App.Rotation(App.Vector(1,0,0),180))
    caster_origin=App.Vector(ox-82,oy,caster_shape.BoundBox.ZMax)
    add_reference('VendorBallCaster',caster_shape,App.Placement(caster_origin,caster_rotation),
                  'https://www.pololu.com/file/0J1637/pololu-ball-caster-with-0-375in-ball.step',
                  'Pololu950 caster vendor reference')
    # Three loose pucks and one captive slider, with common-height tags.
    for i,(x,y) in enumerate(((650,300),(750,450),(450,750))):
        add('40_puck',(x,y,0),label='Puck '+str(i+1))
        add('41_object_marker_cap',(x,y,P['marker_top_height']),(180,0,0))
    gx,gy=700,700
    add('50_gate_base',(gx,gy,0));add('51_gate_slider',(gx,gy,2))
    length=P['gate_travel']+80
    for x in (-length/2+5,length/2-5):
        for y in (-50,50):add('52_gate_end_stop',(gx+x,gy+y,4))
    add('41_object_marker_cap',(gx,gy,P['marker_top_height']),(180,0,0))
    layout=json.loads((ROOT/'hardware/prints/arena_layout.json').read_text())
    for row in layout['segments']:add(row['part'],(row['x'],row['y'],0),(0,0,row['rotation_deg']))
    aw,ah=P['arena_inside'];nx=ENV['NX'];ny=ENV['NY'];lx=ENV['LX'];ly=ENV['LY']
    for i in range(1,nx):
        add('61_wall_joiner',(i*lx,-15,4));add('61_wall_joiner',(i*lx,ah+15,4))
    for i in range(1,ny):
        add('61_wall_joiner',(-15,i*ly,4),(0,0,90));add('61_wall_joiner',(aw+15,i*ly,4),(0,0,90))
    for x,y,a in ((0,0,0),(aw,0,90),(aw,ah,180),(0,ah,270)):
        add('62_arena_corner',(x,y,4),(0,0,a))
    for x,y in ((70,70),(aw-70,70),(70,ah-70),(aw-70,ah-70)):
        add('42_calibration_marker_base',(x,y,0))
        add('41_object_marker_cap',(x,y,P['marker_top_height']),(180,0,0))
    # The overhead tie plate is delivered separately. Its physical boom support
    # belongs to the accessories package, not a floating component in this scene.
    final=doc.addObject('Part::Compound','FinalAssembly');final.Links=links
    final.Label='Robot playground, analytic printed parts'
    final.addProperty('App::PropertyString','AssemblyStatus','Documentation').AssemblyStatus='Illustrative placement. No purchased fasteners or measured servo fit; see mechanical.md.'
    doc.recompute();visibility(final,True)
    stats=shape_stats(final.Shape)
    assert stats['valid'] and stats['closed'] and stats['volume_mm3']>0,stats
    native=NATIVE/'robot_playground_assembly.FCStd';step=STEP/'robot_playground_assembly.step'
    doc.saveAs(str(native));Import.export(links,str(step))
    imported=Part.read(str(step))
    assert imported.isValid() and imported.isClosed() and len(imported.Solids)==stats['solid_count'],('assembly STEP solid count',len(imported.Solids),stats)
    assert abs(imported.Volume-stats['volume_mm3'])<stats['volume_mm3']*1e-6,('assembly STEP volume',imported.Volume,stats)
    entry=dict(id='robot_playground_assembly',native_path=relative(native),step_path=relative(step),
               final_object=final.Name,feature_count=len(doc.Objects),instance_count=len(links),
               step_volume_mm3=imported.Volume,step_volume_relative_error=abs(imported.Volume-stats['volume_mm3'])/stats['volume_mm3'],**stats)
    preview=[]
    for obj,row in zip(links,instances):
        vertices,faces=obj.Shape.tessellate(.12)
        preview.append(dict(label=obj.Label,part=row['part'],purchased_reference=row.get('purchased_reference',False),
                            vertices_mm=[[v.x,v.y,v.z] for v in vertices],faces=[list(f) for f in faces]))
    (HERE/'preview_geometry.json').write_text(json.dumps(preview,separators=(',',':')),encoding='utf-8')
    (HERE/'assembly_instances.json').write_text(json.dumps(instances,indent=2),encoding='utf-8')
    App.closeDocument(doc.Name)
    return entry

def main():
    mesh_records={r['part']:r for r in json.loads((ROOT/'hardware/prints/mesh_report.json').read_text())}
    if os.environ.get('ROBOT_CAD_ASSEMBLY_ONLY')=='1':
        entries=json.loads((HERE/'cad_manifest.json').read_text())['parts']
    else:
        entries=[save_part(name,fn,qty,material,orientation,mesh_records) for name,(fn,qty,material,orientation) in ENV['PARTS'].items()]
    assemblies=[assembly()]
    report=dict(schema_version=1,freecad_version=list(App.Version()),
        source_path=relative(SOURCE),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        parameters_path=relative(PARAMS),parameters_sha256=hashlib.sha256(PARAMS.read_bytes()).hexdigest(),
        step_geometry='Analytic native CSG solids, not triangle meshes',
        native_editing='Native Box/Cylinder dimensions, booleans, and placements recompute through the feature tree.',
        onshape_editing='STEP imports solid geometry but does not carry the FreeCAD feature history.',
        physical_fit_verified=False,parts=entries,assemblies=assemblies)
    (HERE/'cad_manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('COMPLETE',len(entries),'native parts and',len(assemblies),'assembly',flush=True)

# FreeCADCmd executes scripts in its embedded namespace, rather than __main__.
try:main()
except BaseException:
    traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(1)
sys.stdout.flush();sys.stderr.flush();os._exit(0)
