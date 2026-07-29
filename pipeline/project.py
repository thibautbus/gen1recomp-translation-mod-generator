"""Project metadata shared by generated mods and distribution tooling."""
from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def project_config(root: str | Path = ROOT) -> dict:
    """Load the checked-in pipeline configuration."""
    return tomllib.loads(
        (Path(root) / "config" / "pipeline.toml").read_text(encoding="utf-8")
    )


def project_version(root: str | Path = ROOT) -> str:
    """Return the package version from the repository's single source of truth."""
    data = tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])
