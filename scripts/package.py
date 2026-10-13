"""Create a portable source/print/firmware handoff, excluding caches and secrets."""
from pathlib import Path
import hashlib
import json
import os
import zipfile


EXCLUDED = {".git", ".venv", ".build-venv", ".mechanical-venv", ".pio", ".pio-core", "_deps", "__pycache__", "state", "artifacts", ".tools"}
EXCLUDED_FILES = {"hardware/freecad/robot/preview_geometry.json"}


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "artifacts"
    destination.mkdir(exist_ok=True)
    files = []
    for folder, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in EXCLUDED and not name.endswith(".egg-info") and not (Path(folder) / name).is_symlink()]
        for filename in filenames:
            path = Path(folder) / filename
            relative = path.relative_to(root)
            if (not path.is_file() or path.is_symlink() or filename.endswith(".env")
                    or path.suffix.lower() in {".pyc", ".pyo", ".fcbak", ".fcstd1"}
                    or relative.as_posix() in EXCLUDED_FILES):
                continue
            files.append((path, relative))
    files.sort(key=lambda item: item[1].as_posix())
    archive = destination / "robot-playground-v1.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path, relative in files:
            output.write(path, "robot-playground/" + relative.as_posix())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {"archive": archive.name, "sha256": checksum, "bytes": archive.stat().st_size, "files": len(files),
                "excluded": sorted(EXCLUDED), "excluded_files": sorted(EXCLUDED_FILES),
                "excluded_suffixes": [".pyc", ".pyo", ".FCBak", ".FCStd1"],
                "physical_hardware": "unverified and disarmed"}
    (destination / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
