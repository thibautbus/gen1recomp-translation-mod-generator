"""Interactive, end-to-end translation mod builder."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Callable
import zipfile

from .align import align, apply_corpus_overrides
from .corpus import canonical_language, parse_redblue, parse_yellow
from .mod import (
    FONT_PROFILES,
    YELLOW_CATALOG_HOOKS,
    font_profile_warning,
    generate_mod,
    ttf_registration,
    validate_font_profile,
    yellow_isyellow_guard_lines,
)
from .project import ROOT, is_frozen, project_config, project_version, resource_root, work_root
from .dependencies import DependencyError, fetch_archive, fetch_files
from .roms import import_rom, verify_rom
from .rom_paths import configured_path, load_rom_paths


LANGUAGES = (
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("ja-Hrkt", "Japanese"),
)

def load_yellow_coverage_exceptions(path: str | Path) -> dict[str, frozenset[str]]:
    """Load ``config/yellow_coverage_exceptions.json`` reviewed exceptions.

    Mirrors the review discipline of ``config/semantic_anchor_decisions.json``:
    each entry is a human-reviewed exception, not a blind override.  See that
    file's ``description`` for why this stays a separate, smaller schema.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        language: frozenset(labels)
        for language, labels in (data.get("entries") or {}).items()
    }

FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".gb", ".gbc", ".rom", ".ips", ".bps", ".ups", ".patch", ".diff",
}
FORBIDDEN_ARCHIVE_PARTS = {
    "data/generated", "assets/generated", "gameversion", "worksheet",
}


class BuildError(RuntimeError):
    """An expected failure that can be presented directly to the user."""


def _which_luajit() -> str | None:
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


def _luajit_install_hint() -> str:
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


def _pillow_install_hint() -> str:
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        environment = Path(virtual_env).absolute()
        interpreter = Path(sys.executable).absolute()
        try:
            interpreter.relative_to(environment)
        except ValueError:
            expected = (
                environment / "Scripts" / "python.exe"
                if platform.system() == "Windows"
                else environment / "bin" / "python"
            )
            return (
                f"the active virtual environment is {environment}, but this "
                f"script is running with {interpreter}; run: "
                f'"{expected}" build_translation.py'
            )
    return f'run: "{sys.executable}" -m pip install Pillow'


def check_prerequisites() -> str:
    """Fail with actionable installation guidance and return the LuaJIT path."""
    missing: list[str] = []
    if not is_frozen() and sys.version_info < (3, 11):
        missing.append("Python 3.11 or newer (https://www.python.org/downloads/)")
    if not is_frozen() and not shutil.which("git"):
        missing.append("Git (https://git-scm.com/downloads)")
    luajit = _which_luajit()
    if not luajit:
        missing.append(f"LuaJIT ({_luajit_install_hint()})")
    if not is_frozen() and importlib.util.find_spec("PIL") is None:
        missing.append(f"Pillow ({_pillow_install_hint()})")
    if missing:
        details = "\n".join(f"  - {item}" for item in missing)
        raise BuildError(
            "Missing build prerequisites:\n"
            f"{details}\n\nInstall them, then run this command again."
        )
    return luajit


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    printable = " ".join(command)
    line = f"\n> {printable}"
    print(line)
    if log_fn:
        log_fn(line)
    try:
        if log_fn is None:
            subprocess.run(command, cwd=cwd, env=env, check=True)
        else:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, text=True, errors="replace",
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
    except subprocess.CalledProcessError as error:
        raise BuildError(
            f"Command failed with exit code {error.returncode}: {printable}"
        ) from error


def _modkit_command(modkit: Path, *args: str) -> list[str]:
    """Dispatch Modkit through the embedded interpreter when frozen."""
    if is_frozen():
        return [sys.executable, "--internal-worker", str(modkit), *args]
    return [sys.executable, str(modkit), *args]


def _ensure_dependency(config: dict, destination: Path, *, selective_prefix: str | list[str] | None = None) -> Path:
    prefixes = (selective_prefix,) if isinstance(selective_prefix, str) else tuple(selective_prefix or ())
    if is_frozen():
        if config.get("archive_files"):
            try:
                return fetch_files(
                    str(config.get("archive_base_url", "")),
                    dict(config["archive_files"]),
                    destination,
                    revision=str(config.get("revision", "")),
                )
            except (DependencyError, TypeError, ValueError) as error:
                raise BuildError(f"Unable to download pinned dependency files: {error}") from error
        url = str(config.get("archive_url", ""))
        digest = str(config.get("archive_sha256", ""))
        if not url or not digest:
            raise BuildError("Pinned archive URL and SHA-256 are required in config/pipeline.toml for standalone mode.")
        try:
            # fetch_archive extracts a single subtree; a multi-prefix request
            # only makes sense for the git sparse-checkout path below. This
            # is a real BuildError, not an assert: an assert is silently
            # stripped under `python -O`, which would make a frozen build
            # extract only the first prefix (e.g. corpus/RedBlue) and drop
            # the rest (corpus/Yellow) without any error at all. Callers with
            # more than one prefix (the corpus) must instead be listed in
            # config.toml's [*.archive_files] table for standalone mode.
            if len(prefixes) > 1:
                raise BuildError(
                    "Standalone archive extraction supports a single selective "
                    f"prefix; got {list(prefixes)!r}. Multi-prefix dependencies "
                    "must use the archive_files table in config/pipeline.toml "
                    "for standalone mode instead of subtree extraction."
                )
            single = prefixes[0] if prefixes else None
            return fetch_archive(url, digest, destination, revision=str(config.get("revision", "")), selective_prefix=single, immutable_prefixes=("src",), trusted_tree_sha256=str(config.get("archive_tree_sha256", "")))
        except DependencyError as error:
            raise BuildError(f"Unable to download pinned dependency: {error}") from error
    ensure_checkout(config["source"], config["revision"], destination, sparse_paths=prefixes)
    return destination


def _font_source(workspace: Path, config: dict, font_profile: str = "fusion", language: str = "fr") -> Path:
    """Download only the selected pinned font dependency."""
    profile = validate_font_profile("fr", font_profile)
    fonts = config.get("fonts", {})
    selected = fonts.get(profile, {})
    if not selected:
        raise BuildError(f"Pinned {profile} font dependency is missing from config/pipeline.toml.")
    root = workspace / "dependencies"
    try:
        if profile == "pokemon":
            return fetch_files(
                str(selected["archive_base_url"]),
                dict(selected["archive_files"]),
                root / "pokemon-font",
                revision=str(selected.get("revision", "")),
            )
        source = fetch_archive(
            str(selected["archive_url"]),
            str(selected["archive_sha256"]),
            root / "fusion-pixel-font",
            revision=str(selected.get("revision", "")),
        )
        if canonical_language(language) == "ja-Hrkt":
            japanese = config.get("fonts", {}).get("fusion_japanese", {})
            japanese_source = fetch_archive(
                str(japanese["archive_url"]),
                str(japanese["archive_sha256"]),
                root / "fusion-pixel-font-japanese",
                revision=str(japanese.get("revision", "")),
            )
            shutil.copy2(
                japanese_source / "fusion-pixel-8px-proportional-ja.ttf",
                source / "fusion-pixel-8px-proportional-ja.ttf",
            )
        return source
    except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
        raise BuildError(f"Unable to download pinned font dependency: {error}") from error


def ensure_checkout(
    url: str,
    revision: str,
    destination: Path,
    *,
    sparse_paths: tuple[str, ...] = (),
    runner: Callable[..., None] = _run,
) -> None:
    """Create or refresh a private checkout at an immutable revision."""
    git_dir = destination / ".git"
    if not git_dir.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone = ["git", "clone", "--no-checkout"]
        if sparse_paths:
            clone.extend(["--filter=blob:none", "--sparse"])
        clone.extend([url, str(destination)])
        runner(clone)
    fetch = ["git", "fetch", "--depth", "1"]
    if sparse_paths:
        fetch.append("--filter=blob:none")
    fetch.extend(["origin", revision])
    runner(fetch, cwd=destination)
    runner(["git", "checkout", "--detach", revision], cwd=destination)
    if sparse_paths:
        runner(
            ["git", "sparse-checkout", "set", *sparse_paths],
            cwd=destination,
        )


def _prompt_path(prompt: str, input_fn: Callable[[str], str]) -> Path:
    raw = input_fn(prompt).strip().strip("\"'")
    if not raw:
        raise BuildError("A ROM path is required.")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise BuildError(f"File not found: {path}")
    return path.resolve()


def _prompt_configured_path(
    prompt: str,
    configured: Path | None,
    input_fn: Callable[[str], str],
) -> Path:
    """Offer a configured path, falling back to the regular path prompt."""
    if configured is None:
        return _prompt_path(prompt, input_fn)
    while True:
        answer = input_fn(
            f"Path found in config: {configured}. Use this path? [Y/n]: "
        ).strip().lower()
        if answer in {"", "y", "yes"}:
            break
        if answer in {"n", "no"}:
            return _prompt_path(prompt, input_fn)
        print("Please answer y/yes or n/no.")
    try:
        # Keep the same existence/type validation as a directly entered path.
        if not configured.is_file():
            raise BuildError(f"File not found: {configured}")
        return configured.resolve()
    except (OSError, BuildError) as error:
        print(f"Configured path is not usable: {error}")
        return _prompt_path(prompt, input_fn)


def _prompt_language(input_fn: Callable[[str], str]) -> tuple[str, str]:
    print("\nPlease specify the output language:")
    for index, (code, name) in enumerate(LANGUAGES, 1):
        print(f"  {index} - {name} ({code})")
    raw = input_fn("Language number: ").strip()
    try:
        return LANGUAGES[int(raw) - 1]
    except (ValueError, IndexError):
        raise BuildError(f"Invalid language selection: {raw!r}") from None


def _prompt_font_profile(language: str, input_fn: Callable[[str], str]) -> str:
    """Choose a font profile after language selection; Japanese is Fusion-only."""
    language = canonical_language(language)
    if language == "ja-Hrkt":
        print("\nJapanese uses Fusion Pixel by TakWolf, proportional 8px.")
        return "fusion"
    print("\nPlease select a font profile:")
    print("  1 - Fusion Pixel by TakWolf, proportional 10px (recommended)")
    print("  2 - Pokemon Font clone by Superpencil, 8px (some text may overflow)")
    raw = input_fn("Font profile number [1]: ").strip()
    if raw in {"", "1", "fusion"}:
        return "fusion"
    if raw in {"2", "pokemon"}:
        warning = font_profile_warning("pokemon")
        if warning:
            print(f"Warning: {warning}")
        return "pokemon"
    raise BuildError(f"Invalid font profile selection: {raw!r}")


def _prompt_line_break_mode(input_fn: Callable[[str], str]) -> bool:
    """Choose whether dialogue keeps the ROM-original mid-text line breaks
    and CONT pauses, or reflows to the text box's full pixel width."""
    print("\nPlease choose a line-break style:")
    print("  1 - Faithful: keep the original ROM's line breaks and CONT pauses (recommended)")
    print("  2 - Optimized: reflow text to the full box width, fewer forced pauses")
    raw = input_fn("Line-break style [1]: ").strip()
    if raw in {"", "1", "faithful"}:
        return False
    if raw in {"2", "optimized"}:
        return True
    raise BuildError(f"Invalid line-break style selection: {raw!r}")


def _confirm(input_fn: Callable[[str], str]) -> bool:
    action = "downloaded" if is_frozen() else "cloned"
    answer = input_fn(
        f"\nGen1Recomp and poke-corpus are about to be {action} locally into "
        "the private .cache directory.\nDo you wish to continue? [Y/n]: "
    )
    return answer.strip().lower() in {"", "y", "yes"}


def _language_override_path(language: str, filename: str) -> Path | None:
    path = resource_root() / "overrides" / language / filename
    return path if path.is_file() else None


def _corpus_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "corpus_overrides.json")


def _engine_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "shared_engine_overrides.json")


def _yellow_engine_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "yellow_engine_overrides.json")


def _merge_engine_overrides(*paths: Path | None, destination_dir: Path | None = None, name: str = "merged_engine_overrides.json") -> Path | None:
    """Merge shared engine override files into one temporary JSON."""
    from .engine import ENGINE_SCHEMA, load_engine_overrides
    merged: dict = {}
    for path in paths:
        if path is None:
            continue
        merged.update(load_engine_overrides(path))
    if not merged:
        return None
    destination = (destination_dir or resource_root() / ".cache" / "tmp") / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"schema": ENGINE_SCHEMA, "version": 1, "entries": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def assemble_worksheet(scaffold: Path, destination: Path) -> Path:
    """Copy private Modkit references into a single ignored build directory."""
    source = Path(str(scaffold) + "-worksheet")
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "dialogue", "species_names", "move_names", "item_names",
        "trainer_names", "status_labels",
    ):
        worksheet = source / f"{name}.txt"
        if not worksheet.is_file():
            raise BuildError(f"Modkit did not generate {worksheet}")
        shutil.copy2(worksheet, destination / worksheet.name)
    strings = scaffold / "lang" / "strings.lua"
    if not strings.is_file():
        raise BuildError(f"Modkit did not generate {strings}")
    shutil.copy2(strings, destination / "strings.lua")
    return destination


def preserve_scaffold_support(
    scaffold: Path,
    mod: Path,
    language: str = "fr",
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> None:
    """Keep Modkit's runtime hooks while selecting the bundled TTF profile."""
    main = scaffold / "main.lua"
    if not main.is_file():
        raise BuildError(f"Modkit did not generate {main}")
    # The scaffold owns the catalog/font runtime. Keep it intact and append
    # the optional qid-driven handlers only when all branch translations are
    # proven; without the runtime file vanilla behavior remains untouched.
    runtime = mod / "lang" / "literal_handlers.lua"
    scaffold_main = main.read_text(encoding="utf-8")
    # Keep an existing scaffold registration during refreshes without a font
    # dependency; only a real source may select the custom TTF profile.
    ttf_line = ttf_registration(language, font_source, font_profile)
    registration_lines = [
        line for line in scaffold_main.splitlines()
        if line.strip().startswith('mod.content.font:register("ttf"')
    ]
    if registration_lines and font_source is not None:
        scaffold_main = "\n".join(
            ttf_line if line.strip().startswith('mod.content.font:register("ttf"') else line
            for line in scaffold_main.splitlines()
        ) + ("\n" if scaffold_main.endswith("\n") else "")
    elif not registration_lines:
        # Refreshes without a dependency source must not point at an absent
        # asset; a new scaffold uses the engine's Plain Pixel registration.
        lines = scaffold_main.splitlines()
        placeholder = next(
            (index for index, line in enumerate(lines)
             if line.strip().startswith('-- mod.content.font:register("ttf"')),
            None,
        )
        if placeholder is not None:
            lines[placeholder] = ttf_line
            scaffold_main = "\n".join(lines) + ("\n" if scaffold_main.endswith("\n") else "")
        else:
            marker = "return function(mod)"
            if marker not in scaffold_main:
                raise BuildError(f"Modkit scaffold main has no translation entry point: {main}")
            scaffold_main = scaffold_main.replace(marker, marker + "\n" + ttf_line, 1)
    # Type names are translated at draw time (Font.draw/Font.split) while the
    # type_chart registry keeps the English names, so third-party mods that
    # key colors/UI off TypeChart.displayName keep resolving them.  An empty
    # generated catalog (no corpus TypeNames rows) has nothing to apply and
    # leaves the scaffold untouched; when values exist but the scaffold
    # drifts, the failure is loud so a generated type_names.lua can never be
    # packed without its runtime hook.
    type_catalog = mod / "lang" / "type_names.lua"
    if type_catalog.is_file():
        type_body = type_catalog.read_text(encoding="utf-8")
        has_type_values = any(
            line.lstrip().startswith("[") and '= "' in line
            and not line.rstrip().endswith('"",')
            for line in type_body.splitlines()
        )
        if has_type_values:
            type_injection = (
                "\n  -- Injected: localized type display names from generated lang/type_names.lua\n"
                "  -- Type names stay English in the type_chart registry so third-party\n"
                "  -- mods that key colors/UI off TypeChart.displayName keep resolving,\n"
                "  -- and are localized at draw time instead: every engine site renders\n"
                "  -- the type name as a standalone Font.draw string, which is substituted\n"
                "  -- below.\n"
                '  local okType, TypeChart = pcall(require, "src.battle.TypeChart")\n'
                "  local by_english = {}\n"
                '  counts.type_names = each("type_names", function(typeId, localized)\n'
                "    if okType and TypeChart and type(TypeChart.displayName) == \"function\" then\n"
                "      local canonical = TypeChart.displayName(typeId)\n"
                "      if type(canonical) == \"string\" and canonical ~= \"\" and canonical ~= localized then\n"
                "        by_english[canonical] = localized\n"
                "      end\n"
                "    end\n"
                "  end)\n"
                "  if next(by_english) then\n"
                '    local okFont, Font = pcall(require, "src.render.Font")\n'
                "    if okFont and type(Font) == \"table\" then\n"
                "      local function localize(text)\n"
                "        if type(text) ~= \"string\" then return text end\n"
                "        local localized = by_english[text]\n"
                "        return type(localized) == \"string\" and localized or text\n"
                "      end\n"
                "      if type(Font.split) == \"function\" then\n"
                "        local original_split = Font.split\n"
                "        Font.split = function(text)\n"
                "          return original_split(localize(text))\n"
                "        end\n"
                "      end\n"
                "      if type(Font.draw) == \"function\" then\n"
                "        local original_draw = Font.draw\n"
                "        Font.draw = function(text, x, y, ...)\n"
                "          return original_draw(localize(text), x, y, ...)\n"
                "        end\n"
                "      end\n"
                "    end\n"
                "  end\n"
            )
            if "counts.type_names" not in scaffold_main:
                type_marker = '  counts.statuses = each("status_labels", function(id, value)\n    mod.content.statuses:patch(id, { label = value })\n  end)'
                if type_marker in scaffold_main:
                    scaffold_main = scaffold_main.replace(type_marker, type_marker + type_injection, 1)
                elif 'each("status_labels"' in scaffold_main:
                    # The statuses block drifted from the exact scaffold shape;
                    # fall back to the closing function boundary like the
                    # literal-handler injection below (counts and each are in
                    # scope for the whole function body).
                    end = scaffold_main.rfind("\nend")
                    if end < 0:
                        raise BuildError(f"Modkit scaffold main has no closing function: {main}")
                    scaffold_main = scaffold_main[:end] + type_injection + scaffold_main[end:]
                else:
                    raise BuildError(f"Modkit scaffold main has no statuses block to extend: {main}")
    # Yellow layers are applied only after the shared catalogs and only for
    # the Yellow game.  Keep the hook in the final scaffold-owned main.lua;
    # generate_mod's standalone main remains useful for unit tests.
    yellow_names = [
        name for name in ("dialogue", "strings", "species_names", "move_names",
                          "item_names", "trainer_names", "status_labels")
        if (mod / "lang" / f"{name}_yellow.lua").is_file()
    ]
    if yellow_names and "local yellow_game_version" not in scaffold_main:
        yellow_injection = (
            "\n  -- Injected: versioned catalogs for Pokémon Yellow.\n"
            + yellow_isyellow_guard_lines()
            + "  if yellow_game_version then\n"
            + "\n".join(f"    {YELLOW_CATALOG_HOOKS[name]}" for name in yellow_names)
            + "\n  end\n"
        )
        end = scaffold_main.rfind("\nend")
        if end < 0:
            raise BuildError(f"Modkit scaffold main has no closing function: {main}")
        scaffold_main = scaffold_main[:end] + yellow_injection + scaffold_main[end:]

    # A few in-game Options values are raw Font strings in v0.1.69 instead
    # of Strings lookups. Keep this allowlist explicit and reuse the generated
    # strings catalog; do not patch the renderer/Kit itself.
    if "local raw_option_keys" not in scaffold_main and 'each("strings"' in scaffold_main:
        raw_options_injection = (
            "\n  -- Injected: localize raw values only while OptionsMenu draws\n"
            "  local raw_option_keys = {\n"
            '    ["OG RED"] = true, ["OG BLUE"] = true, ["OG YELLOW"] = true,\n'
            '    ["SGB"] = true, ["ADVANCED"] = true, ["OG INV"] = true,\n'
            '    ["SGB INV"] = true, ["CLASSIC"] = true, ["GBC"] = true,\n'
            '    ["WINDOWED"] = true, ["BORDERLESS"] = true,\n'
            '    ["TREES"] = true, ["WATER"] = true, ["BLACK"] = true,\n'
            '    ["OFF"] = true, ["1X"] = true, ["2X"] = true, ["3X"] = true,\n'
            '    ["NORMAL"] = true,\n'
            "  }\n"
            "  local by_raw_option = {}\n"
            '  each("strings", function(id, localized)\n'
            "    if raw_option_keys[id] and localized ~= id then\n"
            "      by_raw_option[id] = localized\n"
            "    end\n"
            "  end)\n"
            "  if next(by_raw_option) then\n"
            '    local okOptions, OptionsMenu = pcall(require, "src.ui.OptionsMenu")\n'
            '    local okFont, Font = pcall(require, "src.render.Font")\n'
            "    if okOptions and type(OptionsMenu) == \"table\" and type(OptionsMenu.draw) == \"function\"\n"
            "        and okFont and type(Font) == \"table\" then\n"
            "      local original_options_draw = OptionsMenu.draw\n"
            "      local function localizeRawOption(text)\n"
            "        if type(text) ~= \"string\" then return text end\n"
            "        return by_raw_option[text] or text\n"
            "      end\n"
            "      OptionsMenu.draw = function(self, ...)\n"
            "        local original_split, original_draw = Font.split, Font.draw\n"
            "        if type(original_split) == \"function\" then\n"
            "          Font.split = function(text) return original_split(localizeRawOption(text)) end\n"
            "        end\n"
            "        if type(original_draw) == \"function\" then\n"
            "          Font.draw = function(text, x, y, ...)\n"
            "            return original_draw(localizeRawOption(text), x, y, ...)\n"
            "          end\n"
            "        end\n"
            "        local ok, result = pcall(original_options_draw, self, ...)\n"
            "        Font.split, Font.draw = original_split, original_draw\n"
            "        if ok then return result end\n"
            "        error(result, 0)\n"
            "      end\n"
            "    end\n"
            "  end\n"
        )
        end = scaffold_main.rfind("\nend")
        if end < 0:
            raise BuildError(f"Modkit scaffold main has no closing function: {main}")
        scaffold_main = scaffold_main[:end] + raw_options_injection + scaffold_main[end:]
    # Yellow's Pallet-intro catch demo and the old-man tutorial show the
    # thrower name in the translated "%s used POKé BALL!" template
    # (BattleState.oldManThrow).  demoName must stay the canonical English
    # literal -- the engine keys Yellow's Pallet-intro sprite selection off
    # demoName == "PROF.OAK" -- so the translation happens only at the
    # render site and is reverted right after.
    if "oldManThrow" not in scaffold_main:
        demo_injection = (
            "\n  -- Injected: localize hard-coded demo-battle thrower names\n"
            '  local demo_names = catalog("demo_names")\n'
            "  local function localizedDemoName(self, name)\n"
            "    if type(name) == \"string\" then\n"
            "      local localized = demo_names and demo_names[name]\n"
            "      if type(localized) == \"string\" and localized ~= \"\" then\n"
            "        return localized\n"
            "      end\n"
            "      if name == \"PROF.OAK\" then\n"
            "        local trainers = self and self.game and self.game.data and self.game.data.trainers\n"
            "        local oak = trainers and trainers.OPP_PROF_OAK\n"
            "        if oak and type(oak.name) == \"string\" and oak.name ~= \"\" then\n"
            "          return oak.name\n"
            "        end\n"
            "      end\n"
            "    end\n"
            "    return nil\n"
            "  end\n"
            '  local okDemo, BS = pcall(require, "src.battle.BattleState")\n'
            "  if okDemo and type(BS) == \"table\" and type(BS.oldManThrow) == \"function\" then\n"
            "    local original_oldManThrow = BS.oldManThrow\n"
            "    BS.oldManThrow = function(self, ...)\n"
            "      if type(self) == \"table\" then\n"
            "        local canonical = self.demoName\n"
            "        local localized = localizedDemoName(self, canonical)\n"
            "        if type(localized) == \"string\" and localized ~= \"\" then\n"
            "          self.demoName = localized\n"
            "          local ok, result = pcall(original_oldManThrow, self, ...)\n"
            "          self.demoName = canonical\n"
            "          if ok then return result end\n"
            "          error(result, 0)\n"
            "        end\n"
            "      end\n"
            "      return original_oldManThrow(self, ...)\n"
            "    end\n"
            "  end\n"
            "  -- Injected: the Pallet-intro thrower sprite is NOT overridden; with\n"
            "  -- demoName kept canonical, the engine itself selects Prof. Oak's back\n"
            "  -- pic for that demo (vanilla behavior).\n"
        )
        end = scaffold_main.rfind("\nend")
        if end >= 0:
            scaffold_main = scaffold_main[:end] + demo_injection + scaffold_main[end:]
    if runtime.is_file():
        marker = '  local literal_body = mod:read("lang/literal_handlers.lua")'
        if marker not in scaffold_main:
            injection = (
                '\n  local literal_body = mod:read("lang/literal_handlers.lua")\n'
                '  if literal_body then\n'
                '    local chunk, err = loadstring(literal_body, "lang/literal_handlers.lua")\n'
                '    if not chunk then error(err) end\n'
                '    local setup = chunk()\n'
                '    if type(setup) ~= "function" then error("literal_handlers.lua must return a function") end\n'
                '    setup(mod)\n'
                '  end\n'
            )
            end = scaffold_main.rfind("\nend")
            if end < 0:
                raise BuildError(f"Modkit scaffold main has no closing function: {main}")
            scaffold_main = scaffold_main[:end] + injection + scaffold_main[end:]
    (mod / "main.lua").write_text(scaffold_main, encoding="utf-8")
    # TTF mode supplies ordinary Unicode glyphs; ROM-derived font/charmap
    # catalogs and images are intentionally not copied into the mod.
    naming = scaffold / "lang" / "naming.lua"
    if naming.is_file():
        shutil.copy2(naming, mod / "lang" / "naming.lua")


def remove_legacy_font_artifacts(mod: Path) -> None:
    """Drop stale ROM-derived font files from an incremental build."""
    for relative in (
        Path("lang/font.lua"),
        Path("lang/charmap.lua"),
        Path("assets/font/localized.png"),
    ):
        (mod / relative).unlink(missing_ok=True)


def inspect_archive(path: Path) -> None:
    """Refuse a distribution containing ROMs, extracts, or worksheets."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise BuildError(f"Invalid output archive: {path}") from error
    seen: set[str] = set()
    for entry in entries:
        name = entry.filename
        normalized = name.replace("\\", "/").lower()
        parts = [part for part in normalized.split("/") if part]
        if normalized.startswith("/") or ".." in parts:
            raise BuildError(f"Unsafe archive path: {name}")
        if normalized in seen:
            raise BuildError(f"Duplicate archive entry: {name}")
        seen.add(normalized)
        unix_mode = (entry.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise BuildError(f"Symbolic links are not allowed in the archive: {name}")
        if Path(normalized).suffix in FORBIDDEN_ARCHIVE_SUFFIXES:
            raise BuildError(f"Unsafe archive entry: {name}")
        if any(part in normalized for part in FORBIDDEN_ARCHIVE_PARTS):
            raise BuildError(f"Private build data found in archive: {name}")
        allowed = (
            normalized in {"manifest.json", "main.lua"}
            or normalized.startswith("lang/")
            or normalized.startswith("fonts/")
            or normalized.startswith("assets/font/")
            or normalized == ".modkit/pack.json"
        )
        if not entry.is_dir() and not allowed:
            raise BuildError(f"Unexpected archive entry: {name}")
    if "manifest.json" not in {name.rstrip("/") for name in seen}:
        raise BuildError("The generated archive has no manifest.json.")


def publish_archive(candidate: Path, output: Path) -> Path:
    """Inspect a private candidate, then atomically replace the public output."""
    inspect_archive(candidate)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, output)
    return output.resolve()


def print_coverage(
    path: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    """Print ROM-gated and informational engine match percentages."""
    report = json.loads(path.read_text(encoding="utf-8"))
    lines = ["\nTranslation coverage:"]
    for key, label in (("rom", "ROM catalog"), ("engine", "All engine strings")):
        section = report.get(key) or {}
        translated = int(section.get("translated", 0))
        total = int(section.get("total", 0))
        percent = float(section.get("percent", 0.0))
        lines.append(f"  {label}: {translated}/{total} ({percent:.2f}%)")
    section = report.get("engine_rby") or {}
    if section.get("available", True) and section.get("total"):
        lines.append(f"  RBY-related engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    elif report.get("engine_rby_warning"):
        lines.append(f"  RBY-related engine strings: unavailable ({report['engine_rby_warning']})")
    yellow = (report.get("yellow") or {}).get("coverage", {}).get("rom") or {}
    if yellow.get("total"):
        lines.append(f"  Yellow ROM catalogs: {int(yellow.get('translated', 0))}/{int(yellow.get('total', 0))} ({float(yellow.get('percent', 0.0)):.2f}%)")
    for line in lines:
        print(line)
        if log_fn:
            log_fn(line)


def build(
    red_rom: Path,
    blue_rom: Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: Path | None = None,
    output_dir: Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
    font_profile: str = "fusion",
    yellow_rom: Path | None = None,
    reflow_line_breaks: bool = False,
) -> Path:
    """Execute the complete private extraction, translation, and pack flow.

    With ``yellow_rom`` the result is the universal Red/Blue/Yellow mod: the
    Yellow import stays in a separate cache directory and versioned catalog
    layers are applied at runtime when ``GameVersion.isYellow()``.
    """
    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    font_profile = validate_font_profile(language, font_profile)
    workspace = Path(workspace_root) if workspace_root is not None else work_root() / ".cache"
    destination = Path(output_dir) if output_dir is not None else (
        work_root() if is_frozen() else work_root() / "dist"
    )
    workspace = workspace.resolve()
    destination = destination.resolve()
    status("Validating ROMs")
    verify_rom(red_rom, "red")
    verify_rom(blue_rom, "blue")
    if yellow_rom is not None:
        verify_rom(yellow_rom, "yellow")

    dependency_root = workspace / "dependencies"
    gen1recomp = dependency_root / "gen1recomp"
    corpus = dependency_root / "poke-corpus"
    config = project_config()
    engine_source = config["gen1recomp"]
    corpus_source = config["corpus"]
    status("Preparing dependencies")
    _ensure_dependency(engine_source, gen1recomp)
    _ensure_dependency(corpus_source, corpus, selective_prefix=["corpus/RedBlue", "corpus/Yellow"])
    font_source = _font_source(workspace, config, font_profile, language)

    log("\nExtracting private ROM data...")
    status("Extracting private ROM data")
    import_rom(
        "red", red_rom, gen1recomp,
        gen1recomp / "data" / "generated",
        gen1recomp / "assets" / "generated",
        log_fn=log_fn,
    )
    import_rom(
        "blue", blue_rom, gen1recomp,
        gen1recomp / "blue" / "data" / "generated",
        gen1recomp / "blue" / "assets" / "generated",
        log_fn=log_fn,
    )
    if yellow_rom is not None:
        import_rom(
            "yellow", yellow_rom, gen1recomp,
            gen1recomp / "yellow" / "data" / "generated",
            gen1recomp / "yellow" / "assets" / "generated",
            log_fn=log_fn,
        )

    build_root = workspace / "interactive" / language
    scaffold = build_root / "translation_source"
    modkit = gen1recomp / "tools" / "modkit.py"
    env = dict(os.environ)
    env["MODKIT_LUAJIT"] = luajit
    env["LUA"] = luajit
    # Modkit's dump_dataset() decodes the LuaJIT dump with subprocess
    # text=True and no explicit encoding, which falls back to the OS locale
    # codepage (e.g. cp1252 on Windows). Some dumped text (observed in the
    # Yellow-layer dataset, across target languages) isn't representable in
    # that codepage and crashes the internal reader thread with a
    # UnicodeDecodeError, leaving proc.stdout as None. Force Python-wide
    # UTF-8 mode (PEP 540) for this worker so that fallback decodes as UTF-8
    # instead, matching the UTF-8 the Lua source files are read/written as.
    env["PYTHONUTF8"] = "1"
    # v0.1.69+'s modkit pack/validate drives the real loader headlessly.
    # Data.loadModule supports POKEPORT_DATA_DIR, which loadfiles the
    # imported dataset directly and skips the love.filesystem-dependent
    # CacheFs path (a bare loader run would crash on CacheFs.read).
    env["POKEPORT_DATA_DIR"] = str(gen1recomp / "data" / "generated")
    if is_frozen():
        lua_dir = str(Path(luajit).resolve().parent)
        env["PATH"] = lua_dir + os.pathsep + env.get("PATH", "")
    _run(_modkit_command(modkit, "--repo", str(gen1recomp),
            "translation", "translation_source", "--language", language_name,
            "--base", "imported", "--dest", str(build_root), "--pixel-font", "--force"),
        cwd=gen1recomp,
        env=env,
        log_fn=log_fn,
    )
    worksheet = assemble_worksheet(scaffold, build_root / "complete-modkit-worksheet")
    yellow_worksheet = None
    if yellow_rom is not None:
        yellow_root = build_root / "yellow_source"
        yellow_scaffold = yellow_root / "translation_source_yellow"
        yellow_env = dict(env)
        yellow_env["POKEPORT_DATA_DIR"] = str(gen1recomp / "yellow" / "data" / "generated")
        _run(_modkit_command(modkit, "--repo", str(gen1recomp),
                "translation", "translation_source_yellow", "--language", language_name,
                "--base", "imported", "--dest", str(yellow_root), "--pixel-font", "--force"),
            cwd=gen1recomp, env=yellow_env, log_fn=log_fn)
        yellow_worksheet = assemble_worksheet(
            yellow_scaffold, yellow_root / "complete-modkit-worksheet"
        )

    log("\nMatching poke-corpus translations...")
    status("Matching poke-corpus translations")
    records = parse_redblue(corpus, language)
    rows = align(records, target_lang=language)
    corpus_overrides = _corpus_overrides_path(language)
    if corpus_overrides:
        rows = apply_corpus_overrides(rows, corpus_overrides)
    mod = build_root / "mod"
    coverage = build_root / "coverage.json"
    remove_legacy_font_artifacts(mod)
    yellow_dialogue = None
    yellow_stats = None
    yellow_catalogs: dict[str, dict[str, str]] = {}
    yellow_engine_values: dict[str, str] = {}
    red_joined = None
    red_join_report = None
    if yellow_rom is not None:
        log("\nBuilding Yellow dialogue layer...")
        status("Building Yellow dialogue layer")
        from .yellow import parse_text_catalog, yellow_dialogue_layer
        from .join import join_catalogs, read_worksheets
        red_text = parse_text_catalog(gen1recomp / "data" / "generated" / "text.lua")
        yellow_text = parse_text_catalog(gen1recomp / "yellow" / "data" / "generated" / "text.lua")
        yellow_rows = align(parse_yellow(corpus, language), target_lang=language)
        red_worksheets = read_worksheets(worksheet)
        red_joined, red_join_report = join_catalogs(rows, red_worksheets, language)
        yellow_worksheets = read_worksheets(yellow_worksheet)
        yellow_joined, yellow_join_report = join_catalogs(
            yellow_rows, yellow_worksheets, language
        )
        yellow_dialogue, yellow_stats = yellow_dialogue_layer(
            red_text, yellow_text,
            yellow_rows,
            language,
            red_translation=red_joined.get("dialogue", {}),
        )
        for catalog_name, values in yellow_joined.items():
            if catalog_name == "dialogue":
                continue
            common = red_joined.get(catalog_name, {})
            layer = {
                key: value for key, value in values.items()
                if value and value != common.get(key)
            }
            if layer:
                yellow_catalogs[catalog_name] = layer
        yellow_stats["catalogs"] = {}
        for name in yellow_worksheets:
            if name == "dialogue":
                continue
            yellow_stats["catalogs"][name] = {
                "translated": len(yellow_catalogs.get(name, {})),
                "total": len(yellow_worksheets[name]),
                "matched": yellow_join_report.get("matched", {}).get(name, 0),
            }
        common_dialogue = red_joined.get("dialogue", {})
        yellow_dialogue_joined = yellow_joined.get("dialogue", {})
        unmatched_labels = set(yellow_stats.get("unmatched_labels", ()))
        coverage_exceptions = load_yellow_coverage_exceptions(
            resource_root() / "config" / "yellow_coverage_exceptions.json"
        )
        composition_covered = coverage_exceptions.get(language, frozenset())
        yellow_stats["effective_dialogue_translated"] = sum(
            label not in unmatched_labels
            and (
                label in yellow_dialogue_joined
                or (
                    label in common_dialogue
                    and red_text.get(label) == yellow_text.get(label)
                )
            )
            or label in composition_covered
            for label in yellow_text
        )
        yellow_stats["composition_covered_labels"] = sorted(
            composition_covered & set(yellow_text)
        )
        # A composition-covered label is credited unconditionally in the
        # numerator above regardless of its own (possibly empty) ROM
        # content, so it must be credited unconditionally in the
        # denominator too — otherwise the numerator could exceed the
        # denominator and report >100% coverage.
        yellow_stats["effective_dialogue_total"] = sum(
            bool(content) or label in composition_covered
            for label, content in yellow_text.items()
        )
        effective_named = 0
        for name, entries in yellow_worksheets.items():
            if name == "dialogue":
                continue
            common_entries = {entry.key: entry.english for entry in red_worksheets.get(name, ())}
            common_values = red_joined.get(name, {})
            yellow_values = yellow_joined.get(name, {})
            effective_named += sum(
                bool(yellow_values.get(key))
                or (bool(common_values.get(key)) and common_entries.get(key) == entry.english)
                for entry in entries
                for key in (entry.key,)
            )
        yellow_stats["effective_named_catalog_translated"] = effective_named
        override_path = _yellow_engine_overrides_path(language)
        if override_path:
            from .engine import check_printf_directives, load_engine_overrides, read_engine_catalog
            overrides = load_engine_overrides(override_path)
            engine_catalog_values = read_engine_catalog(yellow_worksheet / "strings.lua")
            for source, row in overrides.items():
                if source not in engine_catalog_values:
                    raise BuildError(f"Yellow engine override contains unknown key: {source!r}")
                value = row["override"]
                errors = check_printf_directives(source, value)
                if errors:
                    raise BuildError(f"Invalid Yellow engine override {source!r}: {errors[0]}")
                yellow_engine_values[source] = value
            yellow_catalogs.setdefault("strings", {}).update(yellow_engine_values)
        log(f"  Yellow layer: {yellow_stats['layer_entries']} entries "
            f"({yellow_stats['versioned_required']} versioned, "
            f"{yellow_stats['yellow_only']} Yellow-only, "
            f"{yellow_stats['shared_safe']} shared-safe skipped, "
            f"{yellow_stats['unmatched']} unmatched)")
        # Independent Yellow audit: the versioned dialogue matrix, written
        # under .cache/audit/yellow/ next to the coverage report.
        from .yellow_audit import write_yellow_audit
        audit_path = write_yellow_audit(
            gen1recomp / "data" / "generated" / "text.lua",
            gen1recomp / "yellow" / "data" / "generated" / "text.lua",
            align(parse_yellow(corpus, language), target_lang=language),
            language,
            build_root / ".." / ".." / "audit" / "yellow",
            red_text=red_text,
            yellow_text=yellow_text,
            red_translation=red_joined.get("dialogue", {}),
            layer=yellow_dialogue,
            stats=yellow_stats,
        )
        log(f"  Yellow audit: {audit_path}")
    generate_mod(
        rows,
        mod,
        mod_id=f"translation-{language.lower()}",
        language=language,
        target_name=f"{language_name} translation",
        modkit_worksheet=worksheet,
        report_path=coverage,
        engine_overrides=_merge_engine_overrides(
            _engine_overrides_path(language),
            destination_dir=workspace / "tmp",
            name=f"merged_engine_overrides_{language}.json",
        ),
        semantic_anchors=resource_root() / "config" / "semantic_anchors.json",
        semantic_anchor_decisions=resource_root() / "config" / "semantic_anchor_decisions.json",
        strict_engine=True,
        engine_source=gen1recomp / "src",
        engine_scope=resource_root() / "config" / "engine_scope.json",
        font_source=font_source,
        font_profile=font_profile,
        yellow_dialogue=yellow_dialogue,
        yellow_stats=yellow_stats,
        yellow_catalogs=yellow_catalogs,
        yellow_engine_overrides=yellow_engine_values,
        precomputed_join=(red_joined, red_join_report) if red_joined is not None else None,
        reflow_line_breaks=reflow_line_breaks,
    )
    preserve_scaffold_support(scaffold, mod, language, font_source, font_profile)

    version = project_version()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"translation-{language.lower()}-{version}.zip"
    candidate = build_root / f"translation-{language.lower()}-{version}.candidate.zip"
    candidate.unlink(missing_ok=True)
    status("Packaging translation mod")
    _run(_modkit_command(modkit, "--repo", str(gen1recomp),
            "pack", str(mod), "-o", str(candidate), "--base", "imported"),
        cwd=gen1recomp,
        env=env,
        log_fn=log_fn,
    )
    published = publish_archive(candidate, output)
    status("Build complete")
    print_coverage(coverage, log_fn=log_fn)
    return published


def main(input_fn: Callable[[str], str] = input, font_profile: str | None = None, reflow_line_breaks: bool | None = None) -> int:
    print("Gen1Recomp translation mod builder\n")
    try:
        luajit = check_prerequisites()
        rom_paths = load_rom_paths(resource_root() / "config" / "rom_paths.toml")
        red_prompt = (
            "Please specify the location of your Pokemon Red ROM "
            "(full path, e.g. C:\\Games\\PokemonRed.gb): "
        )
        blue_prompt = (
            "Please specify the location of your Pokemon Blue ROM "
            "(full path, e.g. C:\\Games\\PokemonBlue.gb): "
        )
        red = _prompt_configured_path(
            red_prompt, configured_path(rom_paths, "rom", "red"), input_fn
        )
        blue = _prompt_configured_path(
            blue_prompt, configured_path(rom_paths, "rom", "blue"), input_fn
        )
        yellow_prompt = (
            "Please specify the location of your Pokemon Yellow ROM "
            "(full path, e.g. C:\\Games\\PokemonYellow.gb): "
        )
        yellow = _prompt_configured_path(
            yellow_prompt, configured_path(rom_paths, "rom", "yellow"), input_fn
        )
        language, language_name = _prompt_language(input_fn)
        selected_profile = font_profile or _prompt_font_profile(language, input_fn)
        selected_profile = validate_font_profile(language, selected_profile)
        if font_profile:
            warning = font_profile_warning(selected_profile)
            if warning:
                print(f"Warning: {warning}")
        selected_reflow = reflow_line_breaks if reflow_line_breaks is not None else _prompt_line_break_mode(input_fn)
        verify_rom(red, "red")
        verify_rom(blue, "blue")
        verify_rom(yellow, "yellow")
        if not _confirm(input_fn):
            if is_frozen():
                print("\nBuild cancelled. No dependency downloads were performed.")
            else:
                print("\nBuild cancelled. No repositories were cloned.")
            return 0
        output = build(
            red,
            blue,
            language,
            language_name,
            luajit,
            font_profile=selected_profile,
            yellow_rom=yellow,
            reflow_line_breaks=selected_reflow,
        )
    except (BuildError, ValueError, OSError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    print(f"\nFile generated at {output}")
    return 0
