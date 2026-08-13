"""Canonical ROM verification and gen1recomp import orchestration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Callable

from .project import is_frozen, project_config, resource_root, which_luajit


# Product support is intentionally limited to the canonical US games; this
# allowlist is independent of whatever sections a config may contain.
SUPPORTED_VERSIONS = frozenset(("red", "blue", "yellow"))
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
    if log_fn is None:
        subprocess.run(command, cwd=root / "tools", check=True)
        return
    # Mirror pipeline.builder._run(): stream combined stdout/stderr live
    # instead of letting the child inherit the parent's own streams, which
    # are invalid in the frozen GUI's console-less window and would
    # otherwise leave a failure here as an opaque exit code with no output.
    printable = " ".join(command)
    line = f"\n> {printable}"
    print(line)
    log_fn(line)
    process = subprocess.Popen(
        command, cwd=root / "tools", text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    for output_line in process.stdout:
        output_line = output_line.rstrip("\r\n")
        print(output_line)
        log_fn(output_line)
    returncode = process.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


# Gold's fingerprint is intentionally kept out of SUPPORTED_VERSIONS/[rom.*]:
# _canonical_hashes() asserts that table's keys are EXACTLY
# SUPPORTED_VERSIONS, so adding [rom.gold] there would force every RBY call
# site to learn about a fourth, differently-shaped import path -- one with
# no build_rom_data.py equivalent (its VERSION_MANIFESTS only knows
# red/blue/yellow) and no _MANIFESTS entry -- before that unification is
# due.
GOLD_SHA1 = "d8b8a3600a465308c9953dfa04f0081c05bdcb94"


def verify_gold_rom(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    actual = sha1(path)
    if actual != GOLD_SHA1:
        raise ValueError(f"gold ROM SHA-1 mismatch: {actual} (expected {GOLD_SHA1})")
    return {"version": "gold", "path": str(path.resolve()), "sha1": actual, "size": path.stat().st_size}


def import_gold_rom(rom: str | Path, gen1recomp: str | Path, out: str | Path, log_fn: Callable[[str], None] | None = None) -> None:
    """Extract Gold's text catalog under plain LuaJIT; no LÖVE, no ROM in git.

    Mirrors import_rom's subprocess shape (verify hash -> resolve
    interpreter -> build command -> stream log -> raise on nonzero exit),
    but drives tools/gold_extract.lua against RomExtractorGen2 instead of
    build_rom_data.py: Gold's import has no Python-side equivalent, and the
    underlying extraction pilot runs under LuaJIT alone, with
    tests/love_stub standing in for LÖVE.

    Writes gold_text.tsv, gold_labels.tsv and gold_stages.tsv into ``out``.
    """
    verify_gold_rom(rom)
    root = Path(gen1recomp).resolve()
    rom = Path(rom).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    luajit = which_luajit()
    if luajit is None:
        raise RuntimeError("LuaJIT is required to import a Gold ROM; see MODKIT_LUAJIT")
    script = resource_root() / "tools" / "gold_extract.lua"
    command = [luajit, str(script), str(root), str(rom), str(out)]
    if log_fn is None:
        subprocess.run(command, check=True)
        return
    # Mirror pipeline.builder._run() / import_rom(): stream combined
    # stdout/stderr live instead of letting the child inherit the parent's
    # own streams, which are invalid in the frozen GUI's console-less window.
    printable = " ".join(command)
    line = f"\n> {printable}"
    print(line)
    log_fn(line)
    process = subprocess.Popen(
        command, text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    for output_line in process.stdout:
        output_line = output_line.rstrip("\r\n")
        print(output_line)
        log_fn(output_line)
    returncode = process.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


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
