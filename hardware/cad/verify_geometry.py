"""Check nominal assembled solids and useful motion, rather than STL topology alone."""
import json
import itertools
from pathlib import Path
import generate as g

parts={name:fn() for name,(fn,qty,mat,orientation) in g.PARTS.items()}
def placed(name,xyz=(0,0,0),angles=(0,0,0)):
    return parts[name].rotate(angles).translate(xyz)

robot={
 'chassis':placed('10_chassis',(0,0,14)),
 'fixed_jaw':placed('21_fixed_jaw',(0,0,2)),
 'gripper_tower':placed('22_gripper_servo_tower',(0,0,18)),
 'gripper_envelope':g.box((40.2,20.2,48),(30,12.9,17)),
 'marker_tower':placed('30_marker_tower',(-45,0,g.P['marker_top_height']),(180,0,0)),
 'tether_mast':placed('31_tether_mast',(-95,0,18)),
 'caster_spacer':placed('14_caster_spacer',(-82,0,10.1)),
}
for sign in (-1,1):
    robot[f'cradle{sign}']=placed('11_drive_servo_cradle',(-10,sign*40,18))
    robot[f'drive_envelope{sign}']=g.box((40.2,47.3,20.2),(-30.2,sign*43.65-23.65,22.4))
    angle=-90 if sign==1 else 90
    robot[f'wheel{sign}']=placed('12_drive_wheel',(0,sign*72,32.5),(angle,0,0))

results=[]
def check(a,b,solid_a,solid_b):
    volume=(solid_a ^ solid_b).volume()
    results.append(dict(a=a,b=b,intersection_mm3=round(volume,6)))
    assert volume<0.01, f'{a} intersects {b} by{volume:.3f} mm3'

for (name_a,a),(name_b,b) in itertools.combinations(robot.items(),2):
    check(name_a,name_b,a,b)
for angle in range(-20,36):
    jaw=placed('20_moving_jaw',(60,23,2),(0,0,angle))
    for name,solid in robot.items():check(f'jaw{angle}',name,jaw,solid)

base=parts['50_gate_base']
length=g.P['gate_travel']+80
stops=[placed('52_gate_end_stop',(x,y,4)) for x in (-length/2+5,length/2-5) for y in (-50,50)]
half_travel=g.P['gate_travel']/2
positions=[float(x) for x in g.np.linspace(-half_travel,half_travel,int(g.P['gate_travel'])+1)]
for x in positions:
    slider=placed('51_gate_slider',(x,0,2))
    check(f'slider{x}','base',slider,base)
    for i,stop in enumerate(stops):check(f'slider{x}',f'stop{i}',slider,stop)

# Check the robot at a real grip pose against the entire gate and its raised tag.
# Robot axle at x=110, facing -x, places a38 mm target between the fingers.
grip_robot={name:solid.rotate((0,0,180)).translate((110,0,0)) for name,solid in robot.items()}
grip_robot['moving_jaw']=placed('20_moving_jaw',(60,23,2)).rotate((0,0,180)).translate((110,0,0))
object_tag=placed('41_object_marker_cap',(0,0,g.P['marker_top_height']),(180,0,0))
for position in (-half_travel,0,half_travel):
    grip_fixture={'base':base,'slider':placed('51_gate_slider',(position,0,2)),
                  'tag':object_tag.translate((position,0,0))}
    grip_fixture.update({f'stop{i}':stop for i,stop in enumerate(stops)})
    for name,a in grip_robot.items():
        for other,b in grip_fixture.items():
            check(f'grip_robot_{name}_at{position}',f'gate_{other}',a.translate((position,0,0)),b)
for name,a in grip_robot.items():
    check(f'grip_robot_{name}','puck',a,parts['40_puck'])
    check(f'grip_robot_{name}','puck_tag',a,object_tag)

summary=dict(robot_static_pair_checks=78,jaw_angles_checked=list(range(-20,36)),
    gate_positions_checked_mm=positions,total_intersection_checks=len(results),
    largest_intersection_mm3=max(r['intersection_mm3'] for r in results),
    grasp_pose_checked=True,grasp_slider_positions_mm=[-half_travel,0,half_travel],common_marker_plane_mm=g.P['marker_top_height'],
    limitation='Nominal CAD clearances only. No stock horns, wiring, fasteners, tires, loads, or physical hardware fit tested.')
(g.OUT/'geometry_verification.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
