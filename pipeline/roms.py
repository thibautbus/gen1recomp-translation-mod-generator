"""Canonical ROM verification and gen1recomp import orchestration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import MappingProxyType
from typing import Any, Callable

from .project import is_frozen, project_config, resource_root, which_luajit
from .subprocess_run import run_streamed


# Product support is intentionally limited to the canonical US games; this
# allowlist is independent of whatever sections a config may contain.
SUPPORTED_VERSIONS = frozenset(("red", "blue", "yellow"))
CONFIGURED_VERSIONS = SUPPORTED_VERSIONS | {"gold"}
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_MANIFESTS = {
    "red": "rom_manifest.json",
    "blue": "rom_manifest_blue.json",
    "yellow": "rom_manifest_yellow.json",
}


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
    # Gold is validated if present but, unlike RBY, not required: a
    # deployment that never touches Gold can keep a pared-down
    # pipeline.toml without [rom.gold].
    missing = sorted(SUPPORTED_VERSIONS - keys)
    unsupported = sorted(keys - CONFIGURED_VERSIONS)
    if missing or unsupported:
        details = []
        if missing:
            details.append(f"missing {', '.join(f'[rom.{version}]' for version in missing)}")
        if unsupported:
            details.append(f"unsupported versions: {', '.join(unsupported)}")
        raise ValueError("invalid ROM configuration: " + "; ".join(details))

    hashes: dict[str, str] = {}
    for version in sorted(CONFIGURED_VERSIONS & keys):
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


def import_rom(version: str, rom: str | Path, gen1recomp: str | Path, out: str | Path, assets: str | Path, only: list[str] | None = None, log_fn: Callable[[str], None] | None = None) -> None:
    """Run the canonical gen1recomp extractor; outputs must be ignored paths."""
    verify_rom(rom, version)
    root = Path(gen1recomp).resolve()
    rom = Path(rom).resolve()
    out = Path(out).resolve()
    assets = Path(assets).resolve()
    manifest = root / "tools" / _MANIFESTS[version]
    unix_venv_python = root / ".venv" / "bin" / "python"
    windows_venv_python = root / ".venv" / "Scripts" / "python.exe"
    if unix_venv_python.is_file():
        python = str(unix_venv_python)
    elif windows_venv_python.is_file():
        python = str(windows_venv_python)
    else:
        python = sys.executable
    script = str(root / "tools" / "build_rom_data.py")
    if is_frozen():
        # The frozen executable is an app dispatcher, not a Python runtime.
        # Route the extractor through its internal-worker entry point instead
        # of recursively launching the interactive builder.
        command = [sys.executable, "--internal-worker", script]
    else:
        command = [python, script]
    command.extend(["--rom", str(rom), "--manifest", str(manifest), "--out", str(out), "--assets", str(assets), "--clean"])
    for dataset in only or []:
        command.extend(["--only", dataset])
    run_streamed(command, cwd=root / "tools", log_fn=log_fn)


# Gold shares the canonical fingerprint registry with RBY, while remaining
# outside SUPPORTED_VERSIONS because it has a different extractor contract.
# Unlike RBY, [rom.gold] is optional in pipeline.toml, so this is None
# rather than a KeyError at import time when a deployment omits it;
# verify_gold_rom() raises a clear error instead when Gold is actually used.
GOLD_SHA1 = CANONICAL.get("gold")

# Complete output contract of tools/gold_extract.lua.  Keeping the list next
# to the importer lets it validate a fresh extraction before publishing it;
# downstream builders import the same constant rather than maintaining a
# second, potentially divergent list.
GOLD_REQUIRED_TSV = (
    "gold_text.tsv", "gold_labels.tsv", "gold_stages.tsv", "gold_species.tsv",
    "gold_moves.tsv", "gold_items.tsv",
    "gold_trainer_classes.tsv", "gold_landmarks.tsv",
)


def verify_gold_rom(path: str | Path) -> dict[str, Any]:
    if GOLD_SHA1 is None:
        raise ValueError(
            "missing [rom.gold] configuration: Gold ROM verification requires "
            "a [rom.gold] section in config/pipeline.toml"
        )
    path = Path(path)
    actual = sha1(path)
    if actual != GOLD_SHA1:
        raise ValueError(f"gold ROM SHA-1 mismatch: {actual} (expected {GOLD_SHA1})")
    return {"version": "gold", "path": str(path.resolve()), "sha1": actual, "size": path.stat().st_size}


def import_gold_rom(rom: str | Path, gen1recomp: str | Path, out: str | Path, log_fn: Callable[[str], None] | None = None) -> None:
    """Extract and atomically publish Gold's required TSV catalogs."""
    verify_gold_rom(rom)
    root = Path(gen1recomp).resolve()
    rom = Path(rom).resolve()
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    luajit = which_luajit()
    if luajit is None:
        raise RuntimeError("LuaJIT is required to import a Gold ROM; see MODKIT_LUAJIT")
    script = resource_root() / "tools" / "gold_extract.lua"
    temporary = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    command = [luajit, str(script), str(root), str(rom), str(temporary)]
    try:
        run_streamed(command, log_fn=log_fn)

        missing = [
            name for name in GOLD_REQUIRED_TSV
            if not (temporary / name).is_file()
            or not (temporary / name).read_text(encoding="utf-8").strip()
        ]
        if missing:
            raise RuntimeError(
                "Gold extraction completed without required non-empty outputs: "
                + ", ".join(missing)
            )

        backup = temporary.with_name(f"{temporary.name}.old")
        had_output = out.exists()
        if had_output:
            out.replace(backup)
        try:
            temporary.replace(out)
        except Exception:
            if had_output and backup.exists() and not out.exists():
                backup.replace(out)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


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
