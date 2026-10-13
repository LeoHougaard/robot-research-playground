"""Download a checksum-pinned portable FreeCAD into this project only."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import subprocess
import urllib.request

VERSION = "1.1.4"
URL = "https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Windows-x86_64-py311.7z"
SHA256 = "4828741fc91ee37fafcdb97a1abacb18b04ba451ac4372d9ff7a7349b36f4d6d"


def main():
    root = Path(__file__).resolve().parents[1]
    tools = root / ".tools"
    tools.mkdir(exist_ok=True)
    archive = tools / "FreeCAD-1.1.4.7z"
    if not archive.exists():
        temporary = archive.with_suffix(".part")
        request = urllib.request.Request(URL, headers={"User-Agent": "robot-playground-build"})
        total, next_report = 0, 32 * 1024**2
        with urllib.request.urlopen(request, timeout=60) as source, temporary.open("wb") as destination:
            while chunk := source.read(1024**2):
                destination.write(chunk)
                total += len(chunk)
                if total >= next_report:
                    print(f"Portable FreeCAD download: {total // 1024**2} MiB", flush=True)
                    next_report += 32 * 1024**2
        temporary.replace(archive)
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if digest != SHA256:
        raise RuntimeError("Portable FreeCAD checksum mismatch; archive will not run or extract")
    listing = subprocess.run(["tar", "-tf", str(archive)], capture_output=True, text=True, check=True)
    entries = listing.stdout.splitlines()
    for name in entries:
        normalized = PurePosixPath(name.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or ":" in name:
            raise RuntimeError("Archive path is outside the task-local extraction directory")
    destination = tools / "freecad"
    destination.mkdir(exist_ok=True)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Unexpected extraction destination")
    candidates = list(destination.rglob("FreeCADCmd.exe")) + list(destination.rglob("freecadcmd.exe"))
    if not candidates:
        print(f"Extracting {len(entries)} verified archive entries", flush=True)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(destination)], check=True)
        candidates = list(destination.rglob("FreeCADCmd.exe")) + list(destination.rglob("freecadcmd.exe"))
    if not candidates:
        raise RuntimeError("Portable archive lacks FreeCADCmd.exe")
    manifest = {"version": VERSION, "source": URL, "sha256": digest,
                "freecadcmd": str(candidates[0].resolve()), "installation": "task-local portable only"}
    (tools / "freecad-runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
