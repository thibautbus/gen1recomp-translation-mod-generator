"""Canonical ROM verification and gen1recomp import orchestration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import MappingProxyType
from typing import Any

from .project import project_config


# Product support is intentionally limited to the canonical US Red/Blue pair;
# this allowlist is independent of whatever sections a config may contain.
SUPPORTED_VERSIONS = frozenset(("red", "blue"))
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


def _canonical_hashes(root: str | Path | None = None) -> dict[str, str]:
    """Load and validate canonical ROM fingerprints from ``pipeline.toml``."""
    try:
        config = project_config() if root is None else project_config(root)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ValueError(f"unable to load ROM configuration: {exc}") from exc

    rom_config = config.get("rom") if isinstance(config, dict) else None
    if not isinstance(rom_config, dict):
        raise ValueError("invalid ROM configuration: missing [rom] section")

    keys = set(rom_config)
    missing = sorted(SUPPORTED_VERSIONS - keys)
    unsupported = sorted(keys - SUPPORTED_VERSIONS)
    if missing or unsupported:
        details = []
        if missing:
            details.append(f"missing {', '.join(f'[rom.{version}]' for version in missing)}")
        if unsupported:
            details.append(f"unsupported versions: {', '.join(unsupported)}")
        raise ValueError("invalid ROM configuration: " + "; ".join(details))

    hashes: dict[str, str] = {}
    for version in sorted(SUPPORTED_VERSIONS):
        section = rom_config[version]
        if not isinstance(section, dict):
            raise ValueError(f"invalid [rom.{version}] configuration: expected a table")
        # ROM sections intentionally contain only the fingerprint.  Reject
        # stale path fields and private metadata so the TOML remains the sole
        # source of canonical values rather than an accidental path registry.
        fields = set(section)
        unknown_fields = sorted(fields - {"sha1"})
        if unknown_fields:
            raise ValueError(
                f"invalid [rom.{version}] configuration: unsupported keys: "
                + ", ".join(unknown_fields)
            )
        digest = section.get("sha1")
        if not isinstance(digest, str) or _SHA1.fullmatch(digest) is None:
            raise ValueError(
                f"invalid [rom.{version}].sha1: expected a nonempty 40-character "
                "lowercase hexadecimal SHA-1"
            )
        hashes[version] = digest
    return hashes


# Backward-compatible read-only view for callers that used CANONICAL.  The
# values are loaded from config/pipeline.toml; they are not duplicated here.
CANONICAL = MappingProxyType(_canonical_hashes())


def sha1(path: str | Path) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_rom(path: str | Path, version: str, *, config_root: str | Path | None = None) -> dict[str, Any]:
    path = Path(path)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported version {version!r}")
    expected = (CANONICAL if config_root is None else _canonical_hashes(config_root))[version]
    actual = sha1(path)
    if actual != expected:
        raise ValueError(f"{version} ROM SHA-1 mismatch: {actual} (expected {expected})")
    return {"version": version, "path": str(path.resolve()), "sha1": actual, "size": path.stat().st_size}


def catalog_roms(roms: dict[str, str | Path], output: str | Path) -> dict[str, Any]:
    catalog = {version: verify_rom(path, version) for version, path in roms.items()}
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"schema": 1, "roms": catalog}, indent=2) + "\n", encoding="utf-8")
    return catalog


def import_rom(version: str, rom: str | Path, gen1recomp: str | Path, out: str | Path, assets: str | Path, only: list[str] | None = None) -> None:
    """Run the canonical gen1recomp extractor; outputs must be ignored paths."""
    verify_rom(rom, version)
    root = Path(gen1recomp).resolve()
    rom = Path(rom).resolve()
    out = Path(out).resolve()
    assets = Path(assets).resolve()
    manifest = root / "tools" / ("rom_manifest_blue.json" if version == "blue" else "rom_manifest.json")
    unix_venv_python = root / ".venv" / "bin" / "python"
    windows_venv_python = root / ".venv" / "Scripts" / "python.exe"
    if unix_venv_python.is_file():
        python = str(unix_venv_python)
    elif windows_venv_python.is_file():
        python = str(windows_venv_python)
    else:
        python = sys.executable
    command = [python, str(root / "tools" / "build_rom_data.py"), "--rom", str(rom), "--manifest", str(manifest), "--out", str(out), "--assets", str(assets), "--clean"]
    for dataset in only or []:
        command.extend(["--only", dataset])
    subprocess.run(command, cwd=root / "tools", check=True)


def import_all(roms: dict[str, str | Path], gen1recomp: str | Path, cache_root: str | Path) -> dict[str, dict[str, Any]]:
    """Import both versions into disjoint ``GameVersion/<version>`` caches."""
    cache_root = Path(cache_root)
    catalog: dict[str, dict[str, Any]] = {}
    for version, rom in roms.items():
        info = verify_rom(rom, version)
        version_root = cache_root / "GameVersion" / version
        out = version_root / "data" / "generated"
        assets = version_root / "assets" / "generated"
        import_rom(version, rom, gen1recomp, out, assets)
        info.update({"game_version": version, "data": str(out), "assets": str(assets)})
        catalog[version] = info
    (cache_root / "catalog.json").write_text(json.dumps({"schema": 1, "roms": catalog}, indent=2) + "\n", encoding="utf-8")
    return catalog
