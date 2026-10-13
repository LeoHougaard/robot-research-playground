# Regenerating the CAD

The delivered native, STEP and STL files can be used immediately. Regenerate only after changing source dimensions. Keep hand-edited FreeCAD files under different names: builders replace the generated files. Run these commands from the project root on Windows, using Python 3.14 for the pinned mesh dependencies. The core research runtime needs only Python 3.11 or newer.

First edit `hardware/cad/parameters.json` for the robot/arena or `hardware/freecad/accessories/parameters.json` for offboard parts. Measure purchased interfaces before changing them. Related dimensions and mounting patterns must agree.

```powershell
python -m pip install --target hardware/cad/_deps -r hardware/cad/requirements.txt
python hardware/cad/generate.py
python hardware/cad/verify_geometry.py
python scripts/bootstrap_freecad.py
python scripts/run_freecad.py hardware/freecad/robot/build_robot.py --profile robot-build
python scripts/run_freecad.py hardware/freecad/accessories/build_accessories.py --profile accessory-build
python scripts/cad_visibility.py --manifest hardware/freecad/robot/cad_manifest.json --manifest hardware/freecad/accessories/cad_manifest.json
python scripts/run_freecad.py scripts/verify_cad.py --profile cad-review
```

Check the reports, not only the process exit: `hardware/prints/geometry_verification.json` must show no unexpected intersections, and `hardware/freecad/validation/kernel_report.json` must have `passed: true`. Changed dimensions may require updating expected clearances, controller geometry and calibration. Do not weaken a failed check without understanding the interference.

Apply saved visibility only after all builders finish. It hides cutters and saves a fitted top view. Regeneration overwrites that display metadata. The builders use FreeCAD's bundled Python, independently of the mesh dependencies in `hardware/cad/_deps`.

Optional preview refresh:

```powershell
python hardware/freecad/robot/render_native.py
python -c "import sys,runpy; sys.path.insert(0,'hardware/cad/_deps'); runpy.run_path('hardware/freecad/accessories/render_accessories.py',run_name='__main__')"
```

Inspect the resulting PNGs and open representative native parts in FreeCAD. The supplied `scripts/verify_cad_gui.FCMacro` checks actual GUI opening and visible geometry without saving files; its scope excludes OpenGL rendering. Set `CAD_PROJECT_ROOT` to the absolute project path and open the macro with the task-local GUI executable and separate `-u`/`-s` settings. It exits that verification process and writes `hardware/freecad/validation/gui_report.json`.

After reviewing the new exports and reports, run `python scripts/package.py` to refresh the portable handoff. The FreeCAD download, caches, CAD backups, live state and large preview tessellation intermediate are excluded. Source, exported geometry, previews and evidence remain included.
