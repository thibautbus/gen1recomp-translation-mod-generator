# PyInstaller one-file Windows x64 build.  Runtime assets are assembled by
# packaging/build_windows_executable.ps1; ROMs, private config, and caches are
# intentionally absent.
from pathlib import Path

spec_path = Path(SPECPATH).resolve()
# PyInstaller sets SPECPATH to the spec directory.  Accept a file path too so
# this spec remains easy to exercise directly, without depending on cwd.
spec_dir = spec_path if spec_path.is_dir() else spec_path.parent
ROOT = spec_dir.parent
runtime = ROOT / "packaging" / "runtime" / "luajit"
datas = []
for relative in (
    "config/pipeline.toml", "config/engine_scope.json", "config/semantic_anchors.json",
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
            datas.append((str(source), str(Path("luajit") / source.parent.relative_to(runtime))))

a = Analysis(
    [str(ROOT / "build_translation.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL", "PIL.Image", "PIL.ImageFile", "PIL.PngImagePlugin"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="gen1recomp-translation-builder",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
)
