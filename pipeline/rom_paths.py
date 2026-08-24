"""Optional private ROM path configuration for the interactive builder."""
from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Mapping


ROM_PATH_KEYS = frozenset(("red", "blue", "yellow", "gold", "silver"))
_SECTIONS = frozenset(("rom",))


def load_rom_paths(config_path: str | Path | None = None) -> dict[str, dict[str, Path]]:
    """Load and validate an optional private ROM path TOML file.

    Values are returned as absolute paths. Relative values are interpreted
    relative to the configuration file, so moving the repository does not
    change the meaning of a path in the file. Loading only parses TOML and
    resolves names; it never opens or hashes a ROM.
    """
    path = (
        Path(config_path).expanduser()
        if config_path is not None
        else Path(__file__).resolve().parents[1] / "config" / "rom_paths.toml"
    )
    if not path.is_file():
        return {"rom": {}}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Unable to load ROM path configuration: {error}") from None
    except UnicodeError as error:
        raise ValueError(f"Unable to load ROM path configuration: {error}") from None

    if not isinstance(data, dict):  # pragma: no cover - tomllib always returns a dict
        raise ValueError("ROM path configuration must contain TOML tables.")
    unknown_sections = sorted(set(data) - _SECTIONS)
    if unknown_sections:
        raise ValueError(
            "Unsupported ROM path configuration keys: " + ", ".join(unknown_sections)
        )

    resolved: dict[str, dict[str, Path]] = {"rom": {}}
    for section, allowed in (("rom", ROM_PATH_KEYS),):
        raw_table = data.get(section, {})
        if not isinstance(raw_table, dict):
            raise ValueError(f"[{section}] must be a TOML table.")
        unknown_keys = sorted(set(raw_table) - allowed)
        if unknown_keys:
            raise ValueError(
                f"Unsupported keys in [{section}]: " + ", ".join(unknown_keys)
            )
        for key, value in raw_table.items():
            if not isinstance(value, str):
                raise ValueError(f"[{section}].{key} must be a string path.")
            configured = Path(value).expanduser()
            if not configured.is_absolute():
                configured = path.parent / configured
            resolved[section][key] = configured.resolve()
    return resolved


def configured_path(
    paths: Mapping[str, Mapping[str, Path]], section: str, key: str
) -> Path | None:
    """Return one configured path without touching the filesystem."""
    table = paths.get(section, {})
    return table.get(key)
