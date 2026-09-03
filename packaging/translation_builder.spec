# PyInstaller one-file build. Runtime assets are assembled by the platform
# build scripts; ROMs, private config, and caches are intentionally absent.
from pathlib import Path
import os

spec_path = Path(SPECPATH).resolve()
# PyInstaller sets SPECPATH to the spec directory.  Accept a file path too so
# this spec remains easy to exercise directly, without depending on cwd.
spec_dir = spec_path if spec_path.is_dir() else spec_path.parent
ROOT = spec_dir.parent
variant = os.environ.get("GEN1RECOMP_VARIANT", "cli").strip().lower()
if variant not in {"cli", "gui"}:
    raise SystemExit(f"GEN1RECOMP_VARIANT must be cli or gui, got {variant!r}")
entrypoint = ROOT / ("build_translation_gui.py" if variant == "gui" else "build_translation.py")
runtime = ROOT / "packaging" / "runtime" / "luajit"
datas = []
binaries = []
for relative in ("pyproject.toml",):
    source = ROOT / relative
    datas.append((str(source), str(Path(relative).parent)))
# Naming every config/{rby,gsc,shared} file individually has already fallen
# out of sync with real releases more than once as new engine/Crystal config
# files landed. Bundle the whole config/ tree instead -- except
# config/rom_paths.toml, the gitignored file holding the packager's own
# private local ROM paths -- so a future addition here does not silently
# repeat the same gap, the same reasoning already applied to overrides/ and
# tools/ below.
for source in (ROOT / "config").rglob("*"):
    if source.is_file() and source.name != "rom_paths.toml":
        datas.append((str(source), str(source.parent.relative_to(ROOT))))
for source in (ROOT / "overrides").rglob("*"):
    if source.is_file() and source.name != "rom_paths.toml":
        datas.append((str(source), str(source.parent.relative_to(ROOT))))
# gs_extract.lua and the gate_*.lua scripts are read from resource_root()
# at runtime (pipeline/roms.py, pipeline/gs_mod.py); missing from datas
# meant a frozen Gold build failed with "cannot open ... gs_extract.lua:
# No such file or directory" past the first ROM extraction step. Bundle the
# whole tools/ tree (including gen2_gate_fixtures/, the release gate's
# fixture mods) rather than naming each script, so a future addition here
# does not silently repeat the same gap.
for source in (ROOT / "tools").rglob("*"):
    if source.is_file() and "__pycache__" not in source.parts:
        datas.append((str(source), str(source.parent.relative_to(ROOT))))
if runtime.is_dir():
    for source in runtime.rglob("*"):
        if source.is_file():
            # Linux needs the ELF entry point in binaries so one-file
            # extraction preserves its executable mode. Windows keeps its
            # existing data layout for compatibility with the EXE build.
            if source.name == "luajit":
                binaries.append((str(source), "luajit"))
                continue
            datas.append((str(source), str(Path("luajit") / source.parent.relative_to(runtime))))

a = Analysis(
    [str(entrypoint)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PIL", "PIL.Image", "PIL.ImageFile", "PIL.PngImagePlugin", "certifi"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=f"gen1recomp-translation-mod-generator-{variant}",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False if variant == "gui" else True,
)
