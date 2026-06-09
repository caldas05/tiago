# PyInstaller hook for music21 (used by score import: MusicXML/.mxl/MEI → MIDI).
#
# music21 does heavy dynamic importing, so its submodules must be collected as
# hidden imports, and it ships non-.py data files (schemas, etc.) that must be
# bundled. We drop the large built-in `corpus` scores — score import only ever
# runs converter.parse on user-supplied files, never the corpus — which trims
# ~60 MB off the frozen binary.
#
# Picked up automatically by every build path via `--additional-hooks-dir hooks`
# (build.bat, .github/workflows/release.yml) and `hookspath=['hooks']`
# (polytime.spec), so bundling stays defined in exactly one place.
import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("music21")
datas = [
    (src, dst)
    for (src, dst) in collect_data_files("music21")
    if "corpus" not in dst.replace(os.sep, "/").split("/")
]
