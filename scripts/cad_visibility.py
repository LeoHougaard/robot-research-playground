"""Preserve native CAD feature trees while saving a clean default visibility state.

FreeCADCmd saves geometry without GUI metadata. This adds only the documented
Visibility property using the schema produced by the project's FreeCAD GUI probe.
Validate by reopening in FreeCAD and by the independent CAD kernel script.
"""
from pathlib import Path
import argparse
import json
import xml.etree.ElementTree as ET
import zipfile


def set_visibility(path, final_object, bounds=None):
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    document = ET.fromstring(next(data for info, data in entries if info.filename == "Document.xml"))
    names = [obj.attrib["name"] for obj in document.findall("./Objects/Object")]
    if final_object not in names:
        raise ValueError(f"Missing final object in {path}: {final_object}")
    gui = ET.Element("Document", SchemaVersion="1", HasExpansion="1")
    ET.SubElement(gui, "Expand")
    providers = ET.SubElement(gui, "ViewProviderData", Count=str(len(names)))
    for index, name in enumerate(names):
        provider = ET.SubElement(providers, "ViewProvider", name=name, expanded="0", treeRank=str(index))
        properties = ET.SubElement(provider, "Properties", Count="1", TransientCount="0")
        prop = ET.SubElement(properties, "Property", name="Visibility", type="App::PropertyBool", status="1")
        ET.SubElement(prop, "Bool", value="true" if name == final_object else "false")
    if bounds:
        low, high = bounds
        span = max(10, *(high[i] - low[i] for i in range(3)))
        x, y = (low[0] + high[0]) / 2, (low[1] + high[1]) / 2
        z = high[2] + 2 * span
    else:
        span, x, y, z = 100, 0, 0, 200
    camera = (f"OrthographicCamera {{\n viewportMapping ADJUST_CAMERA\n position {x} {y} {z}\n"
              f" orientation 0 0 1 0\n nearDistance 0\n farDistance {5 * span}\n"
              f" aspectRatio 1\n focalDistance {2 * span}\n height {1.5 * span}\n}}\n")
    ET.SubElement(gui, "Camera", settings=camera)
    content = ET.tostring(gui, encoding="utf-8", xml_declaration=True)
    temporary = path.with_suffix(".visibility.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for info, data in entries:
            if info.filename != "GuiDocument.xml":
                output.writestr(info, data)
        output.writestr("GuiDocument.xml", content)
    temporary.replace(path)
    return {"path": str(path), "final_object": final_object, "view_providers": len(names)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = []
    for manifest_name in args.manifest:
        manifest = json.loads(Path(manifest_name).read_text())
        for item in manifest.get("parts", []) + manifest.get("assemblies", []):
            path = (root / item["native_path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Native file is outside the project")
            report.append(set_visibility(path, item["final_object"], item.get("bounds_mm")))
    print(json.dumps({"native_files": len(report), "visibility": report}, indent=2))


if __name__ == "__main__":
    main()
