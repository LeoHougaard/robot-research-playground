"""Preview exported CAD geometry. Optional: pillow, numpy, trimesh; no GUI needed."""
from pathlib import Path
import json
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import trimesh

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "hardware/prints/accessories"
COLORS = [(42, 118, 157), (207, 146, 55), (79, 148, 120), (110, 110, 163)]
MANIFEST = json.loads((HERE / "cad_manifest.json").read_text())
PARTS = {entry["id"]: entry for entry in MANIFEST["parts"]}


def native_mesh(identifier):
    entry = PARTS[identifier]
    mesh = trimesh.load_mesh(ROOT / entry["stl_path"], process=True)
    assert mesh.is_watertight and mesh.is_volume, identifier
    angles = np.radians(entry["print_rotation_xyz_deg"])
    transform = trimesh.transformations.euler_matrix(*angles)
    transform[:3, 3] = entry["print_translation_mm"]
    mesh.apply_transform(np.linalg.inv(transform))
    assert np.allclose(mesh.bounds, entry["bounds_mm"], atol=.121), identifier
    return mesh


def font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def draw_geometry(image, rectangle, meshes, title, subtitle="", view=(1, -1, 1.2)):
    draw = ImageDraw.Draw(image)
    left, top, width, height = rectangle
    draw.rounded_rectangle((left, top, left+width, top+height), radius=18, fill=(249, 250, 251), outline=(222, 226, 232), width=2)
    draw.text((left+24, top+18), title, font=font(25), fill=(27, 39, 49))
    draw.text((left+24, top+52), subtitle, font=font(17), fill=(85, 96, 108))
    sight = np.asarray(view, dtype=float); sight /= np.linalg.norm(sight)
    horizontal = np.cross((0, 0, 1), sight); horizontal /= np.linalg.norm(horizontal)
    vertical = np.cross(sight, horizontal)
    all_vertices = np.concatenate([mesh.vertices for mesh, _ in meshes])
    projected = np.column_stack((all_vertices@horizontal, -all_vertices@vertical))
    low, high = projected.min(axis=0), projected.max(axis=0)
    scale = min((width-70)/max(high[0]-low[0], 1), (height-120)/max(high[1]-low[1], 1))
    centre = (high+low)/2
    origin = np.array((left+width/2, top+height/2+32))
    pixels = np.asarray(image).copy()
    zbuffer = np.full((image.height,image.width), -np.inf, dtype=np.float32)
    light = np.asarray((-.3, -.6, 1)); light /= np.linalg.norm(light)
    for mesh, color in meshes:
        vertices = np.column_stack((mesh.vertices@horizontal, -mesh.vertices@vertical))
        xy = (vertices-centre)*scale+origin
        depth = mesh.vertices@sight
        for face, normal in zip(mesh.faces, mesh.face_normals):
            brightness = .61+.32*max(0., normal@light)
            shade = tuple(int(channel*brightness) for channel in color)
            points=xy[face]
            x0,y0=points.min(axis=0); x1,y1=points.max(axis=0)
            x0=max(left,int(math.floor(x0)));y0=max(top+85,int(math.floor(y0)))
            x1=min(left+width,int(math.ceil(x1)));y1=min(top+height-40,int(math.ceil(y1)))
            if x1<=x0 or y1<=y0:continue
            a,b,c=points
            denominator=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(denominator)<1e-9:continue
            yy,xx=np.mgrid[y0:y1,x0:x1].astype(np.float32);xx+=.5;yy+=.5
            w0=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denominator
            w1=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denominator
            w2=1-w0-w1
            interpolated=w0*depth[face[0]]+w1*depth[face[1]]+w2*depth[face[2]]
            section=zbuffer[y0:y1,x0:x1]
            mask=(w0>=-1e-5)&(w1>=-1e-5)&(w2>=-1e-5)&(interpolated>section)
            section[mask]=interpolated[mask]
            pixels[y0:y1,x0:x1][mask]=shade
    image.paste(Image.fromarray(pixels))
    draw = ImageDraw.Draw(image)
    draw.text((left+24, top+height-30), "Geometry preview · dimensions in mm · physical fit unverified", font=font(14), fill=(96, 104, 115))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (1920, 2120), "white")
    for index, entry in enumerate(MANIFEST["parts"]):
        row, column = divmod(index, 3)
        dimensions = np.subtract(entry["bounds_mm"][1], entry["bounds_mm"][0])
        draw_geometry(canvas, (20+column*630, 20+row*416, 610, 396),
                      [(native_mesh(entry["id"]), COLORS[index%len(COLORS)])],
                      entry["id"].replace("_", " "), " × ".join(f"{x:g}" for x in dimensions)+f" mm · print ×{entry['quantity']}")
    canvas.save(OUT/"parts-overview.png")
    for assembly in MANIFEST["assemblies"]:
        meshes=[]
        for index, instance in enumerate(assembly["instances"]):
            mesh=native_mesh(instance["part"])
            mesh.apply_transform(np.asarray(instance["placement_matrix"]).reshape(4,4))
            meshes.append((mesh, COLORS[index%len(COLORS)]))
        canvas=Image.new("RGB", (1440, 1100), "white")
        draw_geometry(canvas, (20,20,1400,1060),meshes,assembly["assembly"].replace("_", " ").title(),
                      "Assembled printed parts · purchased hardware/supports excluded")
        canvas.save(OUT/(assembly["assembly"]+"-assembly.png"))
    meshes=[]
    for index, (identifier, offset) in enumerate([
        ("electronics_box", (0,0,0)), ("perfboard_carrier", (7,7,92)),
        ("terminal_guard", (220,0,60)), ("electronics_lid", (0,0,160))]):
        mesh=native_mesh(identifier);mesh.apply_translation(offset)
        meshes.append((mesh,COLORS[index%len(COLORS)]))
    canvas=Image.new("RGB", (1440,1400), "white")
    draw_geometry(canvas,(20,20,1400,1360),meshes,"Electronics enclosure · exploded", "Lid / terminal guard / adjustable carrier / vented box")
    canvas.save(OUT/"electronics-exploded.png")
    print("Rendered", len(MANIFEST["parts"]), "parts and", len(MANIFEST["assemblies"]), "assemblies; STL frames match native bounds")


if __name__ == "__main__":
    main()
