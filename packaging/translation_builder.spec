# PyInstaller one-file build. Runtime assets are assembled by the platform
# build scripts; ROMs, private config, and caches are intentionally absent.
from pathlib import Path

spec_path = Path(SPECPATH).resolve()
# PyInstaller sets SPECPATH to the spec directory.  Accept a file path too so
# this spec remains easy to exercise directly, without depending on cwd.
spec_dir = spec_path if spec_path.is_dir() else spec_path.parent
ROOT = spec_dir.parent
runtime = ROOT / "packaging" / "runtime" / "luajit"
datas = []
binaries = []
for relative in (
    "config/pipeline.toml", "config/engine_scope.json", "config/semantic_anchors.json", "config/semantic_anchor_decisions.json",
    "config/terminology_anchors.json", "config/literal_handlers.json",
    "config/rom_paths.example.toml",
    "pyproject.toml",
):
    source = ROOT / relative
    datas.append((str(source), str(Path(relative).parent)))
for source in (ROOT / "overrides").rglob("*"):
    if source.is_file() and source.name != "rom_paths.toml":
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
    [str(ROOT / "build_translation.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PIL", "PIL.Image", "PIL.ImageFile", "PIL.PngImagePlugin"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="gen1recomp-translation-mod-generator",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
)
