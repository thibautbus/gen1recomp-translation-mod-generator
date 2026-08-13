"""Project metadata shared by generated mods and distribution tooling."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import sys
import tomllib


def is_frozen() -> bool:
    """Whether this process is a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Read-only bundled files (or the repository while developing)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parents[1]


def work_root() -> Path:
    """Writable user data root; never points into PyInstaller's extraction dir."""
    return Path.cwd().resolve() if is_frozen() else resource_root()


# Compatibility for callers and developer workflows.
ROOT = resource_root()
RESOURCE_ROOT = ROOT
WORK_ROOT = work_root()


def project_config(root: str | Path = ROOT) -> dict:
    """Load the checked-in pipeline configuration."""
    return tomllib.loads((Path(root) / "config" / "pipeline.toml").read_text(encoding="utf-8"))


def project_version(root: str | Path = ROOT) -> str:
    """Return the package version from the repository's single source of truth."""
    data = tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def which_luajit() -> str | None:
    """Locate the LuaJIT binary the same way for every subprocess caller."""
    configured = os.environ.get("MODKIT_LUAJIT")
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else None
    if is_frozen():
        root = resource_root()
        if platform.system() == "Windows":
            candidates = (root / "luajit" / "luajit.exe", root / "luajit" / "bin" / "luajit.exe", root / "luajit.exe")
        else:
            candidates = (root / "luajit" / "luajit", root / "luajit" / "bin" / "luajit", root / "luajit")
        for candidate in candidates:
            runtime_dir = candidate.parent
            runtime_ok = (runtime_dir / "lua51.dll").is_file() if platform.system() == "Windows" else True
            if candidate.is_file() and runtime_ok and (runtime_dir / "jit").is_dir():
                return str(candidate)
    return shutil.which("luajit") or shutil.which("luajit.exe")


def luajit_install_hint() -> str:
    if is_frozen():
        return "the bundled LuaJIT runtime is missing or damaged; re-download the standalone EXE"
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        return "run: brew install luajit"
    if system == "Linux":
        for manager, command in (
            ("apt-get", "sudo apt install luajit"),
            ("dnf", "sudo dnf install luajit"),
            ("pacman", "sudo pacman -S luajit"),
        ):
            if shutil.which(manager):
                return f"run: {command}"
    return (
        "install the native executable from https://luajit.org/download.html, "
        "then put it on PATH or set MODKIT_LUAJIT to its full path"
    )
