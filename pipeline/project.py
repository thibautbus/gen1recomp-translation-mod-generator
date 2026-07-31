"""Project metadata shared by generated mods and distribution tooling."""
from __future__ import annotations

from pathlib import Path
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
