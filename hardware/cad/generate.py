"""Parametric solid CAD, STL export, independent mesh validation, and assembly renders.

Run from any directory: python hardware/cad/generate.py
All lengths are millimetres. STL files lie on Z=0 in their documented print frame.
Stock servo horns handle the spline. Body clamps use straps and allow axial adjustment.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '_deps'))
import manifold3d as m
import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

P = json.loads((ROOT / 'parameters.json').read_text())
OUT = ROOT.parent / 'prints'
OUT.mkdir(parents=True, exist_ok=True)
N = 96

def box(size, at=(0,0,0)):
    return m.Manifold.cube(size).translate(at)

def cyl(r, h, at=(0,0,0)):
    return m.Manifold.cylinder(h, r, circular_segments=N).translate(at)

def hole(x,y,r,h=100,z=-1):
    return cyl(r,h,(x,y,z))

def slot(x,y,length,width,h=100,z=-1,axis='x'):
    straight = length-width
    if axis == 'x':
        return box((straight,width,h),(x-straight/2,y-width/2,z)) + hole(x-straight/2,y,width/2,h,z) + hole(x+straight/2,y,width/2,h,z)
    return box((width,straight,h),(x-width/2,y-straight/2,z)) + hole(x,y-straight/2,width/2,h,z) + hole(x,y+straight/2,width/2,h,z)

def horn_holes(part, height):
    part -= hole(0,0,P['horn_center_access']/2,height+2)
    for angle in (0,90,180,270):
        a=math.radians(angle)
        x,y=P['horn_bolt_radius']*math.cos(a),P['horn_bolt_radius']*math.sin(a)
        part -= hole(x,y,P['horn_hole_clearance']/2,height+2)
    return part

def chassis():
    part=box((P['base_length'],P['base_width'],P['base_thickness']),
        (P['base_center_x']-P['base_length']/2,-P['base_width']/2,0))
    part -= box((41,21,8),(29.5,12.5,-1))
    for sign in (-1,1):
        for x in (-37,17):
            for y in (26,54): part -= slot(x,sign*y,10,3.4,8,axis='y')
    for x in (40,48): part -= hole(x,-23,1.7,8)
    for x in (30,45):
        # Front servo tower uses a short extension over the base front.
        if x <= 50:
            for y in (7,39): part -= hole(x,y,1.7,8)
    for x in (-103,-87):
        for y in (-8,8): part -= hole(x,y,1.7,8)
    for y in (-P['caster_hole_spacing']/2,P['caster_hole_spacing']/2):
        part -= hole(P['caster_x'],y,1.25,8)
    # Marker zip-tie holes, cable ties, and rear ballast ties.
    for x in (-70,-20):
        for y in (-6,6):
            part -= slot(x-7,y,6,3,8,axis='y')
            part -= slot(x+7,y,6,3,8,axis='y')
    for x in (-98,-66):
        for y in (-44,-24,24,44): part -= slot(x,y,8,3,8,axis='y')
    return part

def drive_cradle():
    # Frame centre is x=-10, y=0. The body can slide along its shaft axis.
    part=box((60,56,4.4),(-30,-28,0))
    for x in (-24,21): part += box((3,56,8), (x,-28,4.4))
    for x in (-27,27):
        for y in (-14,14): part -= slot(x,y,12,3.4,20,axis='y')
    # Straps pass through the bottom, across the narrow body dimension.
    for y in (-17,17):
        for x in (-18,18): part -= slot(x,y,8,3.2,20,axis='y')
    return part

def wheel():
    part=cyl(P['wheel_core_radius'],10)
    part += cyl(31.5,1.5)
    part += cyl(31.5,1.5,(0,0,8.5))
    part=horn_holes(part,10)
    # Stock horn rests against the flat wheel side. Four M2.5 bolts transmit load.
    return part

def tire():
    # TPU ring fits the 59.5 mm core between the retaining rims.
    return cyl(P['wheel_radius'],7)-cyl(29.55,9,(0,0,-1))

def horn_coupon():
    return horn_holes(cyl(12,3),3)

def clearance_coupon():
    part=box((70,25,4))
    for i,d in enumerate((2.5,2.8,3.0,3.2,3.4)):
        part -= hole(7+14*i,12.5,d/2,6)
    return part

def body_coupon():
    # Open U gauges share the chassis clamp gap; they do not assert a servo fit.
    part=box((P['servo_long']+6,14,3))
    part += box((3,14,10),(0,0,3))
    part += box((3,14,10),(P['servo_long']+3,0,3))
    return part

def caster_spacer():
    h=P['base_bottom']-P['caster_height']
    part=box((24,18,h),(-12,-9,0))
    for y in (-6.75,6.75): part -= hole(0,y,1.25,h+2)
    return part

def caster_shim(h):
    part=box((24,18,h),(-12,-9,0))
    for y in (-6.75,6.75): part -= hole(0,y,1.25,h+2)
    return part

def jaw():
    part=cyl(12,10)+box((P['jaw_length'],8,10),(0,-4,0))
    part=horn_holes(part,10)
    for angle in (0,90,180,270):
        a=math.radians(angle)
        part -= hole(P['horn_bolt_radius']*math.cos(a),P['horn_bolt_radius']*math.sin(a),3.15,2.2,z=0)
    return part

def fixed_jaw():
    # Print frame equals assembled frame except the 2 mm floor gap.
    part=box((94,8,10),(36,-27,0))
    part += box((18,8,12),(36,-27,0))
    for x in (40,48):
        part -= hole(x,-23,1.7,18)
        part -= hole(x,-23,3.15,2.2,z=0)
    return part

def gripper_tower():
    # The servo hangs through a body opening. Its horn face is adjusted to Z=12.
    part=box((70,40,4),(20,3,0))
    part -= box((41,21,40),(29.5,12.5,-1))
    for y in (9.5,33.5): part += box((47,3,30),(26.5,y,4))
    for x in (26.5,70.5): part += box((3,27,30),(x,9.5,4))
    # Cable window at the rear long edge, above the clamping band.
    part -= box((30,6,16),(35,31.5,17))
    for x in (30,45):
        for y in (7,39): part -= hole(x,y,1.7,6)
    # Two straps through each wall. No screw touches the servo case.
    for x in (26,70):
        for y in (18,28): part -= box((5,4,3),(x,y,10))
    return part

def marker_tower():
    # Print the broad marker surface on the bed. Turn over during assembly.
    s=P['marker_plate_size']; part=box((s,s,2),(-s/2,-s/2,0))
    for x in (-25,25):
        for y in (-6,6):
            legs=P['marker_top_height']-P['base_bottom']-P['base_thickness']-2
            part += box((8,8,legs),(x-4,y-4,2))
            cross=cyl(1.8,12).rotate((0,90,0)).translate((x-6,y,legs-2))
            part -= cross
    return part

def tether_mast():
    part=box((24,24,4),(-12,-12,0)) + box((10,10,106),(-5,-5,4))
    ring=(cyl(10,5)-cyl(5,7,(0,0,-1))).rotate((90,0,0)).translate((0,2.5,110))
    part += ring
    for x in (-8,8):
        for y in (-8,8): part -= hole(x,y,1.7,6)
    return part

def overhead_tie_plate():
    part=box((70,30,4),(-35,-15,0))
    for x in (-25,25):
        for y in (-8,8): part -= slot(x,y,8,3.2,6,axis='y')
    part += (cyl(10,6)-cyl(5,8,(0,0,-1))).rotate((90,0,0)).translate((0,3,12))
    part += box((10,6,8),(-5,-3,4))
    return part

def puck():
    part=cyl(P['puck_diameter']/2,P['puck_height'])
    return part-hole(0,0,4.2,8,z=12)

def object_marker_cap():
    # Print on the broad plate, invert, and glue its stem into a puck socket.
    # Mounted plate top is74 mm for robot, puck, and gate tags alike.
    return box((28,28,2),(-14,-14,0))+cyl(4,P['marker_top_height']-14,(0,0,2))

def calibration_marker_base():
    part=box((44,44,4),(-22,-22,0))+cyl(9,16,(0,0,4))
    return part-hole(0,0,4.2,8,z=12)

def gate_base():
    length=P['gate_travel']+80
    part=box((length,116,1.2),(-length/2,-58,0))
    part -= box((length-20,76,3),(-length/2+10,-38,-1))
    for sign in (-1,1):
        y=sign*50
        part += box((length,16,4),(-length/2,y-8,0))
        # Open-ended C channel. Sliding feet are retained under its 4 mm top lip.
        wall_y=45.5 if sign==1 else -54
        part += box((length-20,8.5,9),(-length/2+10,wall_y,4))
        lip_y=39 if sign==1 else -54
        part += box((length-20,15,4),(-length/2+10,lip_y,9))
        for x in (-length/2+5,length/2-5):
            part -= hole(x,y,1.7,15)
            part -= hole(x,y,3.15,2.2,z=0)
    return part

def gate_slider():
    part=cyl(19,20)
    for y in (-41,41):
        part += box((60,8,4),(-30,y-4,2.4))
        inner=34 if y>0 else -34
        part += box((8,8,12),(-30,inner-4,4))
    # Raised rear bridge leaves two floor-height jaws a path to the puck.
    part += box((8,76,4),(-30,-38,12))
    part += box((30,12,4),(-25,-6,12))
    return part-hole(0,0,4.2,10,z=10)

def gate_stop():
    # One block per rail at each end. No crossbar obstructs the finger corridor.
    return box((10,20,5),(-5,-10,0))-hole(0,0,1.7,7)

def gate_rail_coupon():
    return (gate_base() ^ box((40,20,20),(-20,38,-1))).translate((20,-38,0))

def gate_runner_coupon():
    return box((40,8,4))

def wall_segment(length=200):
    part=box((length,20,4),(-length/2,0,0)) + box((length,5,26),(-length/2,0,4))
    for x in (-length/2+10,length/2-10):
        part -= hole(x,15,1.7,6)
        part -= hole(x,15,3.15,2.2,z=0)
    return part

def wall_joiner():
    part=box((40,12,3),(-20,-6,0))
    for x in (-10,10): part -= hole(x,0,1.7,5)
    return part

def arena_corner():
    # Bottom-left frame joiner, with its two mounting holes outside the arena.
    part=box((50,12,3),(-25,-21,0)) + box((12,50,3),(-21,-25,0))
    for x,y in ((10,-15),(-15,10)): part -= hole(x,y,1.7,5)
    return part

PARTS={
 '01_horn_fit_coupon':(horn_coupon,1,'PETG','flat; compare all four holes to the stock horn'),
 '02_hole_clearance_coupon':(clearance_coupon,1,'PETG','flat; hole sequence left to right 2.5,2.8,3.0,3.2,3.4 mm'),
 '03_servo_body_coupon':(body_coupon,1,'PETG','flat; open gap is40.2 mm'),
 '04_gate_rail_coupon':(gate_rail_coupon,1,'PETG','flat; same retaining lip as full gate, supports under lip'),
 '05_gate_runner_coupon':(gate_runner_coupon,1,'PETG','flat; slide through coupon channel before printing full gate'),
 '10_chassis':(chassis,1,'PETG','flat bottom; no supports'),
 '11_drive_servo_cradle':(drive_cradle,2,'PETG','flat bottom; no supports'),
 '12_drive_wheel':(wheel,2,'PETG','flat side; no supports'),
 '13_tpu_tire':(tire,2,'TPU95A','flat ring; no supports'),
 '14_caster_spacer':(caster_spacer,1,'PETG','flat; nominal3.9 mm'),
 '15_caster_shim_05':(lambda:caster_shim(.5),1,'PETG','flat; optional leveling'),
 '16_caster_shim_10':(lambda:caster_shim(1),1,'PETG','flat; optional leveling'),
 '20_moving_jaw':(jaw,1,'PETG','flat; no supports'),
 '21_fixed_jaw':(fixed_jaw,1,'PETG','flat jaw bottom; no supports'),
 '22_gripper_servo_tower':(gripper_tower,1,'PETG','flat bottom; cable-window bridge and strap slots may need supports'),
 '30_marker_tower':(marker_tower,1,'PETG','marker plate on bed; turn over for assembly'),
 '31_tether_mast':(tether_mast,1,'PETG','upright with brim; support inside eye or print on side with supports'),
 '32_overhead_tie_plate':(overhead_tie_plate,1,'PETG','flat plate; eye needs support'),
 '40_puck':(puck,3,'PETG','flat; vary color by object, keep shape unchanged'),
 '41_object_marker_cap':(object_marker_cap,8,'PETG','plate on bed, stem upward; invert and glue into puck, gate, or calibration socket'),
 '42_calibration_marker_base':(calibration_marker_base,4,'PETG','flat; same74 mm tagplane as robot and objects'),
 '50_gate_base':(gate_base,1,'PETG','flat bottom; rail lips need support, remove support from channels'),
 '51_gate_slider':(gate_slider,1,'PETG','supports under runners and rear bridge; ensure all support is removable'),
 '52_gate_end_stop':(gate_stop,4,'PETG','flat; one block per rail at each end, no supports'),
 '60_arena_wall_200':(wall_segment,20,'PETG','flat foot; optional printed alternative to wooden walls'),
 '61_wall_joiner':(wall_joiner,16,'PETG','flat; use on top of adjacent feet'),
 '62_arena_corner':(arena_corner,4,'PETG','flat; use on top of feet at frame corners')}

AW,AH=P['arena_inside']
NX,NY=math.ceil(AW/P['arena_segment_max_length']),math.ceil(AH/P['arena_segment_max_length'])
LX,LY=AW/NX,AH/NY
assert min(LX,LY)>=40, 'Arena segments must leave room for joint bolts.'
PARTS['60_arena_wall_200']=(lambda:wall_segment(LX),2*NX,'PETG','flat foot; inside face at localY=0, foot points outside')
if abs(LX-LY)<1e-6:
    PARTS['60_arena_wall_200']=(lambda:wall_segment(LX),2*(NX+NY),'PETG','flat foot; inside face at localY=0, foot points outside')
else:
    PARTS['63_arena_wall_y']=(lambda:wall_segment(LY),2*NY,'PETG','flat foot; shorter rectangular-arena side')
PARTS['61_wall_joiner']=(wall_joiner,2*(NX+NY)-4,'PETG','flat; use on top of adjacent feet')

def mesh(solid):
    raw=solid.to_mesh()
    return trimesh.Trimesh(vertices=np.array(raw.vert_properties[:,:3]),faces=np.array(raw.tri_verts),process=True)

def connected_count(a):
    parent=list(range(len(a.faces)))
    def find(n):
        while parent[n]!=n:
            parent[n]=parent[parent[n]]; n=parent[n]
        return n
    for u,v in a.face_adjacency:
        parent[find(int(u))]=find(int(v))
    return len({find(i) for i in range(len(parent))})

def render(items, filename, title, elevation=30, azimuth=-55):
    """Rasterise actual STL triangles with a per-pixel depth buffer."""
    W,H=1400,1000
    im=Image.new('RGB',(W,H),'#f5f5f1'); draw=ImageDraw.Draw(im)
    az,el=math.radians(azimuth),math.radians(elevation)
    u=np.array([math.cos(az),math.sin(az),0]); v=np.array([-math.sin(az)*math.sin(el),math.cos(az)*math.sin(el),math.cos(el)])
    depth=np.cross(u,v)
    verts=np.vstack([a.vertices for a,c in items]); proj=np.column_stack((verts@u,verts@v))
    lo,hi=proj.min(0),proj.max(0); scale=min((W-160)/(hi[0]-lo[0]),(H-200)/(hi[1]-lo[1]))
    centre=(lo+hi)/2
    pixels=np.array(im)
    zbuffer=np.full((H,W),-np.inf)
    light=np.array([-.4,-.6,1.]);light/=np.linalg.norm(light)
    for a,color in items:
        rgb=np.array(tuple(int(color[i:i+2],16) for i in (1,3,5)))
        for face,normal in zip(a.faces,a.face_normals):
            tri=a.vertices[face]
            xy=np.column_stack((tri@u,tri@v)); xy=(xy-centre)*scale
            xy[:,0]+=W/2;xy[:,1]=H/2+45-xy[:,1]
            shade=.65+.35*abs(float(normal@light))
            col=(rgb*shade).astype(np.uint8)
            x0=max(0,int(np.floor(xy[:,0].min())));x1=min(W-1,int(np.ceil(xy[:,0].max())))
            y0=max(0,int(np.floor(xy[:,1].min())));y1=min(H-1,int(np.ceil(xy[:,1].max())))
            if x1<x0 or y1<y0: continue
            a0,a1,a2=xy
            den=(a1[1]-a2[1])*(a0[0]-a2[0])+(a2[0]-a1[0])*(a0[1]-a2[1])
            if abs(den)<1e-8:continue
            xx,yy=np.meshgrid(np.arange(x0,x1+1)+.5,np.arange(y0,y1+1)+.5)
            w0=((a1[1]-a2[1])*(xx-a2[0])+(a2[0]-a1[0])*(yy-a2[1]))/den
            w1=((a2[1]-a0[1])*(xx-a2[0])+(a0[0]-a2[0])*(yy-a2[1]))/den
            w2=1-w0-w1
            z=tri@depth;zz=w0*z[0]+w1*z[1]+w2*z[2]
            region=zbuffer[y0:y1+1,x0:x1+1]
            mask=(w0>=-1e-6)&(w1>=-1e-6)&(w2>=-1e-6)&(zz>region)
            region[mask]=zz[mask]
            pixels[y0:y1+1,x0:x1+1][mask]=col
    im=Image.fromarray(pixels);draw=ImageDraw.Draw(im)
    try: font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',32); small=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',22)
    except OSError: font=small=ImageFont.load_default()
    draw.text((35,25),title,fill='#182d38',font=font)
    draw.text((35,H-45),'Actual exported meshes. Servo blocks are envelopes; hardware fit remains unverified.',fill='#364a54',font=small)
    im.save(OUT/filename)

def transformed(solid, translate=(0,0,0), rotate=(0,0,0)):
    return mesh(solid.rotate(rotate).translate(translate))

def calibration_crosshair_sheet():
    dpi=300
    px=lambda mm:round(mm*dpi/25.4)
    im=Image.new('RGB',(px(210),px(297)),'white');d=ImageDraw.Draw(im)
    try:font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',48)
    except OSError:font=ImageFont.load_default()
    d.text((px(15),px(15)),'Calibration references. Print at 100 percent.',fill='black',font=font)
    for name,x,y in (('A',55,70),('B',155,70),('C',55,170),('D',155,170)):
        d.rectangle((px(x-14),px(y-14),px(x+14),px(y+14)),outline='#b0b0b0',width=2)
        d.line((px(x-5),px(y),px(x+5),px(y)),fill='black',width=px(.4))
        d.line((px(x),px(y-5),px(x),px(y+5)),fill='black',width=px(.4))
        d.text((px(x-5),px(y+20)),f'Ref {name}',fill='black',font=font)
    d.line((px(50),px(250),px(150),px(250)),fill='black',width=3)
    for x in (50,150):d.line((px(x),px(247),px(x),px(253)),fill='black',width=3)
    d.text((px(80),px(256)),'100 mm ruler',fill='black',font=font)
    im.save(OUT/'calibration-crosshairs-a4-300dpi.png',dpi=(dpi,dpi))

def assembly(solids, open_jaw=False):
    gray='#738a98';teal='#31a9b0';orange='#d9963c';dark='#343a45'
    a=[(transformed(solids['10_chassis'],(0,0,14)),gray)]
    for sign in (-1,1):
        a.append((transformed(solids['11_drive_servo_cradle'],(-10,sign*40,18)),gray))
        wheel_at=(0,sign*72,32.5)
        angle=-90 if sign==1 else 90
        a.append((transformed(solids['12_drive_wheel'],wheel_at,(angle,0,0)),orange))
        tire_at=(0,sign*73.5,32.5)
        a.append((transformed(solids['13_tpu_tire'],tire_at,(angle,0,0)),dark))
        # Explicit envelope only, dimensions cover the disagreement in vendor files.
        a.append((transformed(box((40.2,47.3,20.2),(-30.2,-23.65,0)),(0,sign*43.65,22.4)),teal))
    a.append((transformed(solids['14_caster_spacer'],(-82,0,10.1)),gray))
    a.append((transformed(cyl(5,10.1),(-82,0,0)),dark))
    a.append((transformed(solids['21_fixed_jaw'],(0,0,2)),orange))
    angle=P['jaw_open_angle_deg'] if open_jaw else P['jaw_closed_angle_deg']
    a.append((transformed(solids['20_moving_jaw'],(60,23,2),(0,0,angle)),orange))
    a.append((transformed(solids['22_gripper_servo_tower'],(0,0,18)),gray))
    a.append((transformed(box((40.2,20.2,48),(30,12.9,0)),(0,0,17)),teal))
    a.append((transformed(solids['30_marker_tower'],(-45,0,P['marker_top_height']),(180,0,0)),gray))
    a.append((transformed(solids['31_tether_mast'],(-95,0,18)),gray))
    return a

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--skip-render',action='store_true');args=ap.parse_args()
    calibration_crosshair_sheet()
    report=[];solids={}
    for name,(fn,qty,material,orientation) in PARTS.items():
        solid=fn(); solids[name]=solid
        assert solid.status()==m.Error.NoError, (name,solid.status())
        a=mesh(solid)
        assert a.is_watertight and a.is_winding_consistent and a.volume>0, name
        # All export files contain exactly one connected solid.
        assert connected_count(a)==1, name
        a.export(OUT/(name+'.stl'))
        loaded=trimesh.load_mesh(OUT/(name+'.stl'),process=True)
        assert loaded.is_watertight and loaded.is_winding_consistent and loaded.volume>0, name
        assert connected_count(loaded)==1, name
        report.append(dict(part=name,quantity=qty,material=material,orientation=orientation,
            triangles=int(len(a.faces)),watertight=bool(loaded.is_watertight),connected_solids=1,
            dimensions_mm=np.round(a.extents,3).tolist(),volume_mm3=round(a.volume,3),minimum_z_mm=round(float(a.bounds[0,2]),4)))
    (OUT/'mesh_report.json').write_text(json.dumps(report,indent=2))
    (OUT/'print_manifest.csv').write_text('part,quantity,material,orientation\n'+''.join(f'{r["part"]},{r["quantity"]},{r["material"]},"{r["orientation"]}"\n' for r in report))
    layout=[]
    for i in range(NX):
        layout += [dict(part='60_arena_wall_200',x=(i+.5)*LX,y=0,rotation_deg=180),
                   dict(part='60_arena_wall_200',x=(i+.5)*LX,y=AH,rotation_deg=0)]
    yname='63_arena_wall_y' if '63_arena_wall_y' in solids else '60_arena_wall_200'
    for i in range(NY):
        layout += [dict(part=yname,x=0,y=(i+.5)*LY,rotation_deg=90),
                   dict(part=yname,x=AW,y=(i+.5)*LY,rotation_deg=-90)]
    (OUT/'arena_layout.json').write_text(json.dumps(dict(inside_mm=[AW,AH],
        wall_height_mm=30,minimum_floor_footprint_mm=[AW+40,AH+40],segments=layout,
        straight_joiners=2*(NX+NY)-4,corner_joiners=4),indent=2))
    if not args.skip_render:
        render(assembly(solids),'robot_assembly.png','Robot V1. Two wheels, caster, one moving jaw')
        render(assembly(solids,True),'robot_open_jaw.png','Robot V1. Jaw open at 35 degrees',elevation=65)
        render(assembly(solids),'robot_top.png','Robot V1. Top view',elevation=90,azimuth=0)
        gate=[(mesh(solids['50_gate_base']),'#738a98'),(transformed(solids['51_gate_slider'],(0,0,2)),'#d9963c')]
        length=P['gate_travel']+80
        gate += [(transformed(solids['52_gate_end_stop'],(x,y,4)),'#31a9b0') for x in (-length/2+5,length/2-5) for y in (-50,50)]
        gate.append((transformed(solids['41_object_marker_cap'],(0,0,P['marker_top_height']),(180,0,0)),'#738a98'))
        render(gate,'gate_assembly.png','Captive slider. 100 mm nominal travel',elevation=40)
        grasp=[]
        for a,col in assembly(solids):
            a.apply_transform(trimesh.transformations.rotation_matrix(math.pi,(0,0,1)))
            a.apply_translation((110,0,0));grasp.append((a,col))
        grasp += [(a.copy(),col) for a,col in gate]
        render(grasp,'robot_gate_grasp.png','Checked grasp pose. Robot and gate tags on 74 mm plane',elevation=35,azimuth=-30)
        plate=[]
        for i,name in enumerate(solids):
            a=mesh(solids[name]);a.apply_translation((-a.bounds[0,0]+(i%5)*220,-a.bounds[0,1]+(i//5)*140,0))
            plate.append((a,'#738a98' if i%2 else '#d9963c'))
        render(plate,'parts_inventory.png','Print inventory. Dimensions and quantities in manifest',elevation=70,azimuth=0)
        arena=[(transformed(solids[r['part']],(r['x'],r['y'],0),(0,0,r['rotation_deg'])),'#738a98') for r in layout]
        for a,col in assembly(solids):
            a.apply_translation((AW*.3,AH*.3,0));arena.append((a,col))
        for a,col in gate:
            a=a.copy();a.apply_translation((AW*.7,AH*.7,0));arena.append((a,col))
        for x,y in ((.65,.3),(.75,.45),(.45,.75)):
            arena.append((transformed(solids['40_puck'],(AW*x,AH*y,0)),'#d9963c'))
            arena.append((transformed(solids['41_object_marker_cap'],(AW*x,AH*y,P['marker_top_height']),(180,0,0)),'#738a98'))
        render(arena,'arena_layout.png',f'Arena. {AW:g} x {AH:g} mm inside. Object placement is illustrative.',elevation=90,azimuth=0)
    print(f'Exported and reloaded {len(report)} watertight, connected STL files to {OUT}')

if __name__=='__main__': main()
