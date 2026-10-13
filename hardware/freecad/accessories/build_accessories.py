"""Run with task-local FreeCADCmd, not ordinary Python. Native editable CSG only."""
from pathlib import Path
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import sys
import FreeCAD as App
import Part
import MeshPart

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PARAMETERS = json.loads((HERE / "parameters.json").read_text(encoding="utf-8"))
OUT_NATIVE = ROOT / "hardware/native/accessories"
OUT_STEP = ROOT / "hardware/step/accessories"
OUT_PRINT = ROOT / "hardware/prints/accessories"


def expression(value):
    return value if isinstance(value, str) else f"{value:g} mm"


def combine(a, operation, b):
    return f"({expression(a)}) {operation} ({expression(b)})"


def D(name):
    return "Dimensions." + name


class Builder:
    def __init__(self, name, family):
        self.name = name
        self.doc = App.newDocument(name)
        self.dim = self.doc.addObject("App::FeaturePython", "Dimensions")
        self.dim.Label = "Editable dimensions in millimetres"
        for key, value in PARAMETERS[family].items():
            if not isinstance(value, (int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid positive accessory dimension: " + key)
            self.dim.addProperty("App::PropertyLength", key, "Dimensions")
            setattr(self.dim, key, value)
        self.inputs = dict(PARAMETERS[family])

    def value(self, obj, property_name, value):
        if isinstance(value, str): obj.setExpression(property_name, value)
        elif property_name.startswith("Placement.Base."):
            placement=obj.Placement
            vector=placement.Base
            setattr(vector,property_name.rsplit(".",1)[1],value)
            placement.Base=vector
            obj.Placement=placement
        else: setattr(obj, property_name, value)

    def place(self, obj, point):
        for axis, value in zip("xyz", point): self.value(obj, "Placement.Base." + axis, value)

    def hide(self, obj):
        if obj.ViewObject: obj.ViewObject.Visibility = False

    def box(self, name, length, width, height, point=(0,0,0)):
        obj = self.doc.addObject("Part::Box", name)
        for prop,value in zip(("Length","Width","Height"),(length,width,height)): self.value(obj,prop,value)
        self.place(obj,point)
        self.hide(obj)
        return obj

    def cylinder(self, name, radius, height, point=(0,0,0), axis=(0,0,1)):
        obj = self.doc.addObject("Part::Cylinder", name)
        self.value(obj,"Radius",radius)
        self.value(obj,"Height",height)
        self.place(obj,point)
        obj.Placement.Rotation = App.Rotation(App.Vector(0,0,1), App.Vector(*axis))
        self.hide(obj)
        return obj

    def union(self, name, objects):
        if len(objects) == 1: return objects[0]
        obj = self.doc.addObject("Part::MultiFuse", name)
        obj.Shapes = objects
        obj.Refine = True
        self.hide(obj)
        return obj

    def cut(self, name, base, tool):
        obj = self.doc.addObject("Part::Cut", name)
        obj.Base, obj.Tool, obj.Refine = base, tool, True
        self.hide(obj)
        return obj

    def slot(self, name, x, y, travel, diameter, height, z=-1):
        radius = f"({expression(diameter)}) / 2"
        a = self.cylinder(name+"Start",radius,height,(x,y,z))
        c = self.cylinder(name+"End",radius,height,(combine(x,"+",travel),y,z))
        middle = self.box(name+"Middle",travel,diameter,height,(x,combine(y,"-",radius),z))
        return self.union(name,[a,middle,c])

    def finish(self, base, tools, *, quantity=1, material="PETG", orientation="Largest flat face on bed"):
        final = self.cut("Final",base,self.union("AllCuttingTools",tools))
        self.doc.recompute()
        shape = final.Shape
        if shape.isNull() or not shape.isValid() or not shape.isClosed() or len(shape.Solids) != 1 or shape.Volume <= 0:
            raise RuntimeError(self.name+" is not one valid closed analytic solid")
        # A property edit must propagate through the actual native CSG tree.
        key = next(iter(self.inputs))
        previous = getattr(self.dim,key).Value
        original_volume = shape.Volume
        setattr(self.dim,key,previous + 0.5)
        self.doc.recompute()
        if abs(final.Shape.Volume-original_volume) < 1e-6:
            raise RuntimeError(self.name+" dimensions do not rebuild the final native solid")
        setattr(self.dim,key,previous)
        self.doc.recompute()
        shape = final.Shape
        native = OUT_NATIVE / (self.name+".FCStd")
        step = OUT_STEP / (self.name+".step")
        stl = OUT_PRINT / (self.name+".stl")
        self.doc.saveAs(str(native))
        Part.export([final],str(step))
        restored = Part.Shape()
        restored.read(str(step))
        if not restored.isValid() or len(restored.Solids) != 1 or abs(restored.Volume-shape.Volume) > max(1e-5,shape.Volume*1e-7):
            raise RuntimeError(self.name+" STEP round-trip differs from native solid")
        print_shape = shape.copy()
        print_rotation = [0,0,0]
        if self.name == "electronics_lid":
            print_shape.rotate(App.Vector(0,0,0),App.Vector(1,0,0),180)
            print_rotation = [180,0,0]
            orientation = "Outer flat face on bed; retaining lip upward"
        print_z = -print_shape.BoundBox.ZMin
        print_shape.translate(App.Vector(0,0,print_z))
        mesh = MeshPart.meshFromShape(Shape=print_shape,LinearDeflection=0.12,AngularDeflection=0.2,Relative=False)
        if not mesh.isSolid(): raise RuntimeError(self.name+" print mesh is not manifold and watertight")
        mesh.write(str(stl))
        bb = shape.BoundBox
        entry = {"id":self.name,"native_path":str(native.relative_to(ROOT)).replace('\\','/'),
                 "step_path":str(step.relative_to(ROOT)).replace('\\','/'),
                 "stl_path":str(stl.relative_to(ROOT)).replace('\\','/'),"final_object":"Final",
                 "print_rotation_xyz_deg":print_rotation,"print_translation_mm":[0,0,print_z],
                 "bounds_mm":[[bb.XMin,bb.YMin,bb.ZMin],[bb.XMax,bb.YMax,bb.ZMax]],
                 "volume_mm3":shape.Volume,"quantity":quantity,"material":material,"orientation":orientation,
                 "solid_count":1,"closed":True,"valid":True,"mesh_manifold_watertight":True,
                 "mesh_facets":mesh.CountFacets,"surface_types":sorted(set(type(face.Surface).__name__ for face in shape.Faces)),
                 "editable_native_object_types":sorted(set(obj.TypeId for obj in self.doc.Objects)),
                 "parameter_edit_rebuild_verified":True,"step_roundtrip_verified":True}
        saved = shape.copy()
        App.closeDocument(self.doc.Name)
        print(self.name,"volume",round(entry['volume_mm3'],2),"facets",entry['mesh_facets'])
        return entry,saved


def enclosure_box():
    b=Builder("electronics_box","enclosure")
    outer=b.box("OuterBox",D("BoxLength"),D("BoxWidth"),D("BoxHeight"))
    cavity=b.box("InnerCavity",f'{D("BoxLength")} - 2 * {D("Wall")}',f'{D("BoxWidth")} - 2 * {D("Wall")}',
                 f'{D("BoxHeight")} - {D("Floor")} + 1 mm',(D("Wall"),D("Wall"),D("Floor")))
    shell=b.cut("OpenShell",outer,cavity)
    posts=[]; tools=[]
    for x in (D("BossInset"),f'{D("BoxLength")} - {D("BossInset")}'):
        for y in (D("BossInset"),f'{D("BoxWidth")} - {D("BossInset")}'):
            posts.append(b.cylinder("LidScrewBoss",D("BossRadius"),f'{D("BoxHeight")} - 2.5 mm',(x,y,2.5)))
            tools.append(b.cylinder("InsertBore",f'{D("InsertDiameter")} / 2',f'{D("InsertDepth")} + 1 mm',
                                    (x,y,f'{D("BoxHeight")} - {D("InsertDepth")}')))
    base=b.union("ShellAndBosses",[shell]+posts)
    for x in (-1,f'{D("BoxLength")} - {D("Wall")} - 1 mm'):
        for y in (30,44,58,72,86,100):
            tools.append(b.box("SideVent",f'{D("Wall")} + 2 mm',D("VentWidth"),D("VentHeight"),(x,y,22)))
    for x in (20,140):
        tools.append(b.box("UniversalCablePort",D("CableOpeningWidth"),f'{D("Wall")} + 2 mm',D("CableOpeningHeight"),(x,-1,15)))
    for x in (16,f'{D("BoxLength")} - 16 mm'):
        for y in (16,f'{D("BoxWidth")} - 16 mm'):
            tools.append(b.cylinder("CarrierMount",f'{D("M3Clearance")} / 2',f'{D("Floor")} + 2 mm',(x,y,-1)))
    return b.finish(base,tools,orientation="Floor on bed; open box upward")


def enclosure_lid():
    b=Builder("electronics_lid","enclosure")
    plate=b.box("LidPlate",D("BoxLength"),D("BoxWidth"),D("LidThickness"))
    inset=f'{D("Wall")} + {D("LipClearance")}'
    outer=b.box("LipOuter",f'{D("BoxLength")} - 2 * ({inset})',f'{D("BoxWidth")} - 2 * ({inset})',
                f'{D("LipDepth")} + 0.5 mm',(inset,inset,f'-{D("LipDepth")}'))
    inner=b.box("LipInner",f'{D("BoxLength")} - 2 * ({inset} + {D("LipWall")})',
                f'{D("BoxWidth")} - 2 * ({inset} + {D("LipWall")})',f'{D("LipDepth")} + 2 mm',
                (f'{inset} + {D("LipWall")}',f'{inset} + {D("LipWall")}',f'-{D("LipDepth")} - 1 mm'))
    lip=b.cut("LipFrame",outer,inner)
    notches=[];tools=[]
    for x in (D("BossInset"),f'{D("BoxLength")} - {D("BossInset")}'):
        for y in (D("BossInset"),f'{D("BoxWidth")} - {D("BossInset")}'):
            notches.append(b.cylinder("BossClearance",f'{D("BossRadius")} + 0.5 mm',f'{D("LipDepth")} + 1 mm',
                                       (x,y,f'-{D("LipDepth")} - 0.5 mm')))
            tools.append(b.cylinder("LidBoltHole",f'{D("M3Clearance")} / 2',f'{D("LidThickness")} + 2 mm',(x,y,-1)))
    lip=b.cut("BossNotchedLip",lip,b.union("AllBossNotches",notches))
    return b.finish(b.union("PlateAndLip",[plate,lip]),tools,orientation="Outside lid face on bed; lip upward")


def carrier():
    b=Builder("perfboard_carrier","carrier")
    plate=b.box("CarrierPlate",D("CarrierLength"),D("CarrierWidth"),D("CarrierThickness"))
    tools=[]
    for i in range(1,7):
        tools.append(b.slot("AdjustableBoardSlot",D("SlotStart"),D("SlotY"+str(i)),
                            f'{D("CarrierLength")} - {D("SlotEndInset")} - {D("SlotStart")}',
                            D("M3Clearance"),f'{D("CarrierThickness")} + 2 mm'))
    for x in (9,f'{D("CarrierLength")} - 9 mm'):
        for y in (9,f'{D("CarrierWidth")} - 9 mm'):
            tools.append(b.cylinder("CarrierBoltHole",f'{D("M3Clearance")} / 2',f'{D("CarrierThickness")} + 2 mm',(x,y,-1)))
    # Tray corners pass the enclosure's full-height lid screw bosses.
    for x in (1,f'{D("CarrierLength")} - 1 mm'):
        for y in (1,f'{D("CarrierWidth")} - 1 mm'):
            tools.append(b.cylinder("EnclosureBossRelief",6.5,f'{D("CarrierThickness")} + 2 mm',(x,y,-1)))
    return b.finish(plate,tools)


def camera_plate():
    b=Builder("camera_plate","camera")
    plate=b.box("CameraAdapter",D("CameraLength"),D("CameraWidth"),D("CameraThickness"))
    tools=[b.slot("TripodBoltSlot",D("CameraSlotStart"),D("CameraSlotY"),D("CameraSlotTravel"),
                  D("TripodClearance"),f'{D("CameraThickness")} + 2 mm')]
    for x in (20,80):
        for y in (31,69):
            tools.append(b.cylinder("ClampBoltHole",f'{D("M4Clearance")} / 2',f'{D("CameraThickness")} + 2 mm',(x,y,-1)))
    return b.finish(plate,tools)


def beam_clamp(name):
    b=Builder(name,"beam")
    body=b.box("ClampHalf",D("ClampLength"),D("ClampWidth"),D("ClampHeight"))
    throat=f'{D("MeasuredBeamWidth")} + {D("BeamSideClearance")}'
    depth=f'({D("MeasuredBeamHeight")} - {D("MatingGap")}) / 2'
    tools=[b.box("MeasuredBeamGroove",f'{D("ClampLength")} + 2 mm',throat,f'{depth} + 1 mm',
                 (-1,f'({D("ClampWidth")} - ({throat})) / 2',f'{D("ClampHeight")} - ({depth})'))]
    for x in (10,f'{D("ClampLength")} - 10 mm'):
        for y in (8,f'{D("ClampWidth")} - 8 mm'):
            tools.append(b.cylinder("ClampBolt",f'{D("M4Clearance")} / 2',f'{D("ClampHeight")} + 2 mm',(x,y,-1)))
    for y in (8,f'{D("ClampWidth")} - 8 mm'):
        tools.append(b.cylinder("CordPassage",f'{D("CordEyeDiameter")} / 2',f'{D("ClampHeight")} + 2 mm',
                                (f'{D("ClampLength")} / 2',y,-1)))
    return b.finish(body,tools,quantity=2,orientation="Flat outer back on bed; groove upward")


def tether_anchor():
    b=Builder("tether_anchor_plate","anchor")
    plate=b.box("TetherPlate",D("AnchorLength"),D("AnchorWidth"),D("AnchorThickness"))
    tools=[b.cylinder("SoftCordEye",f'{D("CordEyeDiameter")} / 2',f'{D("AnchorThickness")} + 2 mm',
                      (f'{D("AnchorLength")} / 2',f'{D("AnchorWidth")} / 2',-1))]
    for x in (10,f'{D("AnchorLength")} - 10 mm'):
        tools.append(b.cylinder("AnchorMount",f'{D("M4Clearance")} / 2',f'{D("AnchorThickness")} + 2 mm',
                                (x,f'{D("AnchorWidth")} / 2',-1)))
    return b.finish(plate,tools,orientation="Flat plate on bed; cord is secondary restraint only")


def cable_clamp(name):
    b=Builder(name,"cable")
    body=b.box("CableClampHalf",D("CableClampLength"),D("CableClampWidth"),D("CableClampHeight"))
    tools=[b.cylinder("MeasuredCableGroove",f'({D("MeasuredCableDiameter")} - {D("CableCompression")}) / 2',
                      f'{D("CableClampLength")} + 2 mm',(-1,f'{D("CableClampWidth")} / 2',D("CableClampHeight")),(1,0,0))]
    for x in (12,f'{D("CableClampLength")} - 12 mm'):
        for y in (4.5,f'{D("CableClampWidth")} - 4.5 mm'):
            tools.append(b.cylinder("CableClampBolt",f'{D("M3Clearance")} / 2',f'{D("CableClampHeight")} + 2 mm',(x,y,-1)))
    return b.finish(body,tools,orientation="Flat back on bed; groove upward")


def estop_panel():
    b=Builder("estop_panel","panel")
    panel=b.box("PanelBlank",D("PanelLength"),D("PanelWidth"),D("PanelThickness"))
    tools=[b.cylinder("Omron22mmCutout",f'{D("EstopHoleDiameter")} / 2',f'{D("PanelThickness")} + 2 mm',
                      (D("EstopCentreX"),D("EstopCentreY"),-1)),
           b.box("OmronLockRingKey",D("EstopKeyWidth"),f'{D("EstopKeyOverallHeight")} - {D("EstopHoleDiameter")} / 2',
                 f'{D("PanelThickness")} + 2 mm',(f'{D("EstopCentreX")} - {D("EstopKeyWidth")} / 2',D("EstopCentreY"),-1)),
           b.cylinder("Adafruit1505ArmButton",f'{D("ArmHoleDiameter")} / 2',f'{D("PanelThickness")} + 2 mm',
                      (D("ArmCentreX"),D("ArmCentreY"),-1))]
    for x in (8,f'{D("PanelLength")} - 8 mm'):
        for y in (8,f'{D("PanelWidth")} - 8 mm'):
            tools.append(b.cylinder("StandBolt",f'{D("M4Clearance")} / 2',f'{D("PanelThickness")} + 2 mm',(x,y,-1)))
    for x in (f'{D("EstopCentreX")} - 43 mm',f'{D("EstopCentreX")} + 43 mm'):
        tools.append(b.cylinder("ClearanceRingMount",f'{D("M3Clearance")} / 2',f'{D("PanelThickness")} + 2 mm',
                                (x,D("EstopCentreY"),-1)))
    return b.finish(panel,tools,orientation="Flat rear on bed; use supplied Omron locking plate, no printed certification claim")


def estop_ring():
    b=Builder("estop_clearance_ring","guard")
    outer=b.cylinder("OuterClearanceRing",f'{D("GuardOuterDiameter")} / 2',D("GuardHeight"))
    ears=[b.cylinder("MountEar",D("GuardEarRadius"),D("GuardHeight"),(x,0,0))
          for x in (f'-{D("GuardEarOffset")}',D("GuardEarOffset"))]
    tools=[b.cylinder("FingerClearZone",f'{D("GuardInnerDiameter")} / 2',f'{D("GuardHeight")} + 2 mm',(0,0,-1))]
    for x in (f'-{D("GuardEarOffset")}',D("GuardEarOffset")):
        tools.append(b.cylinder("RingBolt",f'{D("M3Clearance")} / 2',f'{D("GuardHeight")} + 2 mm',(x,0,-1)))
    return b.finish(b.union("RingAndEars",[outer]+ears),tools,orientation="Flat ring on bed; optional low ring, check palm access and twist-release")


def estop_stand():
    b=Builder("estop_panel_stand","stand")
    floor=b.box("StandFloor",D("StandLength"),D("StandWidth"),D("StandFloor"))
    legs=[];tools=[]
    for x in (18,f'{D("StandLength")} - 18 mm'):
        for y in (18,f'{D("StandWidth")} - 18 mm'):
            legs.append(b.box("PanelPillar",D("PillarWidth"),D("PillarWidth"),f'{D("RearClearance")} + 0.5 mm',
                              (f'({expression(x)}) - {D("PillarWidth")} / 2',f'({expression(y)}) - {D("PillarWidth")} / 2',
                               f'{D("StandFloor")} - 0.5 mm')))
            tools.append(b.cylinder("PillarM4Pilot",f'{D("M4Clearance")} / 2',f'{D("RearClearance")} + {D("StandFloor")} + 2 mm',
                                    (x,y,-1)))
    for x in (8,f'{D("StandLength")} - 8 mm'):
        tools.append(b.cylinder("DeskFixing",f'{D("M5Clearance")} / 2',f'{D("StandFloor")} + 2 mm',
                                (x,f'{D("StandWidth")} / 2',-1)))
    return b.finish(b.union("FloorAndPillars",[floor]+legs),tools,orientation="Floor on bed; secure to desk/support; open sides give rear terminal access")


def terminal_guard():
    b=Builder("terminal_guard","terminal_guard")
    outer=b.box("TerminalShield",D("TerminalLength"),D("TerminalWidth"),D("TerminalHeight"))
    cavity=b.box("OpenTerminalLane",f'{D("TerminalLength")} + 2 mm',f'{D("TerminalWidth")} - 2 * {D("TerminalWall")}',
                 D("TerminalHeight"),(-1,D("TerminalWall"),D("TerminalWall")))
    tools=[cavity]
    for x in (10,f'{D("TerminalLength")} - 10 mm'):
        tools.append(b.cylinder("ShieldMount",f'{D("M3Clearance")} / 2',f'{D("TerminalWall")} + 2 mm',(x,16,-1)))
    return b.finish(outer,tools,orientation="Flat floor on bed; low-voltage terminal shielding only")


def positioned(shape,translation,rotate_x=False):
    copy=shape.copy()
    if rotate_x: copy.rotate(App.Vector(0,0,0),App.Vector(1,0,0),180)
    copy.translate(App.Vector(*translation))
    return copy


def assembly_check(name,shapes):
    intersections=[]
    for i,(a_name,a) in enumerate(shapes):
        for b_name,b in shapes[i+1:]:
            volume=a.common(b).Volume
            if volume>1e-5: intersections.append({"a":a_name,"b":b_name,"overlap_mm3":volume})
    if intersections: raise RuntimeError(name+" interference: "+str(intersections))
    doc=App.newDocument(name+"_assembly")
    links=[]
    for part_name,shape in shapes:
        existing={obj.Name for obj in doc.Objects}
        doc.mergeProject(str(OUT_NATIVE/(part_name+".FCStd")))
        imported=[obj for obj in doc.Objects if obj.Name not in existing]
        finals=[obj for obj in imported if obj.Label=="Final" and obj.TypeId=="Part::Cut"]
        if len(finals)!=1: raise RuntimeError(name+" merge did not retain one native final: "+part_name)
        link=finals[0]
        link.Label=part_name+"Instance"
        link.Placement=shape.Placement
        if link.ViewObject: link.ViewObject.Visibility=False
        links.append(link)
    final=doc.addObject("Part::Compound","FinalAssembly")
    final.Links=links
    final.Label=name.replace('_',' ').title()+" assembly"
    doc.recompute()
    compound=final.Shape
    if not compound.isValid() or not compound.isClosed() or len(compound.Solids)!=len(shapes):
        raise RuntimeError(name+" native link assembly invalid")
    native=OUT_NATIVE/(name+"_assembly.FCStd")
    step=OUT_STEP/(name+"_assembly.step")
    doc.saveAs(str(native))
    Part.export([final],str(step))
    restored=Part.Shape();restored.read(str(step))
    if not restored.isValid() or len(restored.Solids)!=len(shapes) or abs(restored.Volume-compound.Volume)>compound.Volume*1e-7:
        raise RuntimeError(name+" assembly STEP round-trip differs")
    bb=compound.BoundBox
    return {"assembly":name,"parts":[n for n,_ in shapes],"unexpected_intersections":intersections,
            "id":name+"_assembly","final_object":"FinalAssembly",
            "native_path":str(native.relative_to(ROOT)).replace('\\','/'),
            "step_path":str(step.relative_to(ROOT)).replace('\\','/'),
            "bounds_mm":[[bb.XMin,bb.YMin,bb.ZMin],[bb.XMax,bb.YMax,bb.ZMax]],
            "volume_mm3":compound.Volume,"solid_count":len(compound.Solids),"valid":True,"closed":True,
            "native_model_kind":"Self-contained merged editable Part primitive/boolean trees; Part::Compound final",
            "instances":[{"part":part_name,"placement_matrix":list(shape.Placement.toMatrix().A)} for part_name,shape in shapes],
            "step_roundtrip_verified":True}


def main():
    for folder in (OUT_NATIVE,OUT_STEP,OUT_PRINT): folder.mkdir(parents=True,exist_ok=True)
    makers=[enclosure_box,enclosure_lid,carrier,camera_plate,
            lambda:beam_clamp("beam_clamp_lower"),lambda:beam_clamp("beam_clamp_upper"),tether_anchor,
            lambda:cable_clamp("cable_clamp_lower"),lambda:cable_clamp("cable_clamp_upper"),
            estop_panel,estop_ring,estop_stand,terminal_guard]
    parts=[];shapes={}
    for maker in makers:
        entry,shape=maker();parts.append(entry);shapes[entry['id']]=shape
    assemblies=[]
    enclosure=PARAMETERS['enclosure']; beam=PARAMETERS['beam']; camera=PARAMETERS['camera']; stand=PARAMETERS['stand']; panel=PARAMETERS['panel']
    assemblies.append(assembly_check("electronics",[("electronics_box",shapes['electronics_box']),
         ("electronics_lid",positioned(shapes['electronics_lid'],(0,0,enclosure['BoxHeight']+enclosure['LipClearance']))),
         ("perfboard_carrier",positioned(shapes['perfboard_carrier'],(7,7,11))),
         ("terminal_guard",positioned(shapes['terminal_guard'],(17,93,15)))]))
    assemblies.append(assembly_check("camera",[("camera_plate",shapes['camera_plate']),
         ("beam_clamp_lower",positioned(shapes['beam_clamp_lower'],(10,(camera['CameraWidth']-beam['ClampWidth'])/2,camera['CameraThickness']))),
         ("beam_clamp_upper",positioned(shapes['beam_clamp_upper'],(10,(camera['CameraWidth']+beam['ClampWidth'])/2,camera['CameraThickness']+2*beam['ClampHeight']+beam['MatingGap']),True))]))
    assemblies.append(assembly_check("tether",[("beam_clamp_lower",shapes['beam_clamp_lower']),
         ("beam_clamp_upper",positioned(shapes['beam_clamp_upper'],(0,beam['ClampWidth'],2*beam['ClampHeight']+beam['MatingGap']),True)),
         ("tether_anchor_plate",positioned(shapes['tether_anchor_plate'],(0,beam['ClampWidth']-23,2*beam['ClampHeight']+beam['MatingGap'])))]))
    assemblies.append(assembly_check("cable",[("cable_clamp_lower",shapes['cable_clamp_lower']),
         ("cable_clamp_upper",positioned(shapes['cable_clamp_upper'],(0,22,16),True))]))
    assemblies.append(assembly_check("operator_panel",[("estop_panel_stand",shapes['estop_panel_stand']),
         ("estop_panel",positioned(shapes['estop_panel'],(10,10,stand['StandFloor']+stand['RearClearance']))),
         ("estop_clearance_ring",positioned(shapes['estop_clearance_ring'],(10+panel['EstopCentreX'],10+panel['EstopCentreY'],stand['StandFloor']+stand['RearClearance']+panel['PanelThickness'])))]))
    report={"schema_version":1,"freecad_version":".".join(App.Version()[:3]),"parts":parts,"assemblies":assemblies,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "parameters_sha256":hashlib.sha256((HERE/"parameters.json").read_bytes()).hexdigest(),
            "actual_hardware_fit_tested":False,"printed_load_rating_verified":False,
            "parameters_source":"hardware/freecad/accessories/parameters.json",
            "native_model_kind":"FreeCAD analytic native Part::Box/Cylinder/Cut/MultiFuse with live parameter expressions",
            "vendor_reference":"Omron A22E-M 40mm head; panel hole22.3+0.4/0, 1-5mm panel; choose22.5mm and3mm; locking-ring keywidth3.2+0.2/0 and overall24.1+0.4/0 -> choose3.3mm and24.3mm. 70mm free ring opening is design clearance, not vendor-certified guard. Adafruit1505 NO momentary armbutton: maxshaft15.56mm,totalheight29.4mm; nominalcutout16mm requires real fit check."}
    (HERE/"cad_manifest.json").write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    with (OUT_PRINT/"print_manifest.csv").open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=['part','quantity','material','orientation','stl_path','print_rotation_xyz_deg','print_translation_mm','native_bounds_mm'])
        writer.writeheader()
        for entry in parts:
            writer.writerow({'part':entry['id'],**{key:entry[key] for key in ('quantity','material','orientation','stl_path')},
                'print_rotation_xyz_deg':json.dumps(entry['print_rotation_xyz_deg']),
                'print_translation_mm':json.dumps(entry['print_translation_mm']),
                'native_bounds_mm':json.dumps(entry['bounds_mm'])})
    print("ACCESSORIES_SUCCESS",len(parts),"parts",len(assemblies),"assemblies")


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
