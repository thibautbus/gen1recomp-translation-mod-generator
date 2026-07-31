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

from .align import align, apply_overrides
from .corpus import canonical_language, parse_redblue
from .mod import generate_mod
from .localized_font import extract_localized_font, validate_localized_rom
from .project import ROOT, project_config, project_version
from .roms import import_rom, verify_rom
from .rom_paths import configured_path, load_rom_paths


LANGUAGES = (
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("ja-Hrkt", "Japanese"),
)
WESTERN_FONT_LANGUAGES = {"fr", "de", "es", "it"}

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
    return shutil.which("luajit") or shutil.which("luajit.exe")


def _luajit_install_hint() -> str:
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
    if sys.version_info < (3, 11):
        missing.append("Python 3.11 or newer (https://www.python.org/downloads/)")
    if not shutil.which("git"):
        missing.append("Git (https://git-scm.com/downloads)")
    luajit = _which_luajit()
    if not luajit:
        missing.append(f"LuaJIT ({_luajit_install_hint()})")
    if importlib.util.find_spec("PIL") is None:
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
) -> None:
    printable = " ".join(command)
    print(f"\n> {printable}")
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except subprocess.CalledProcessError as error:
        raise BuildError(
            f"Command failed with exit code {error.returncode}: {printable}"
        ) from error


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


def _print_language_warning(language: str) -> None:
    if canonical_language(language) == "ja-Hrkt":
        print(
            "\nWarning: Japanese localized-font extraction is not supported, "
            "and the Japanese translation does not currently display "
            "correctly in game. No Japanese ROM will be requested."
        )


def _confirm(input_fn: Callable[[str], str]) -> bool:
    answer = input_fn(
        "\nGen1Recomp and poke-corpus are about to be cloned locally into "
        "the private .cache directory.\nDo you wish to continue? [Y/n]: "
    )
    return answer.strip().lower() in {"", "y", "yes"}


def _override_path(language: str, filename: str) -> Path | None:
    path = (
        ROOT / "overrides" / filename
        if language == "fr"
        else ROOT / "overrides" / language / filename
    )
    return path if path.is_file() else None


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


def preserve_scaffold_support(scaffold: Path, mod: Path) -> None:
    """Keep Modkit's font, charmap, and naming runtime integration."""
    main = scaffold / "main.lua"
    if not main.is_file():
        raise BuildError(f"Modkit did not generate {main}")
    # The scaffold owns the catalog/font runtime. Keep it intact and append
    # the optional qid-driven handlers only when all branch translations are
    # proven; without the runtime file vanilla behavior remains untouched.
    runtime = mod / "lang" / "literal_handlers.lua"
    scaffold_main = main.read_text(encoding="utf-8")
    # Font images live inside the mod archive. LOVE resolves a plain relative
    # path against the game root, so turn mod-owned paths into asset paths
    # before registering the page. Without this, every localized glyph is
    # registered but renders as a blank cell.
    font_registration = "    mod.content.font:register(id, page)"
    font_path_fix = (
        '    if type(page) == "table" and type(page.image) == "string"\n'
        "        and mod:read(page.image) then\n"
        "      page.image = mod.assets:path(page.image)\n"
        "    end\n"
    )
    if font_registration in scaffold_main and "mod.assets:path(page.image)" not in scaffold_main:
        scaffold_main = scaffold_main.replace(
            font_registration,
            font_path_fix + font_registration,
            1,
        )
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
    lang = mod / "lang"
    for name in ("font.lua", "charmap.lua", "naming.lua"):
        source = scaffold / "lang" / name
        if not source.is_file():
            raise BuildError(f"Modkit did not generate {source}")
        shutil.copy2(source, lang / name)
    font_source = scaffold / "assets" / "font"
    font_destination = mod / "assets" / "font"
    for source in font_source.glob("*"):
        if source.is_file() and source.suffix.lower() == ".png":
            font_destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, font_destination / source.name)


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


def print_coverage(path: Path) -> None:
    """Print ROM-gated and informational engine match percentages."""
    report = json.loads(path.read_text(encoding="utf-8"))
    print("\nTranslation coverage:")
    for key, label in (("rom", "ROM catalog"), ("engine", "All engine strings")):
        section = report.get(key) or {}
        translated = int(section.get("translated", 0))
        total = int(section.get("total", 0))
        percent = float(section.get("percent", 0.0))
        print(f"  {label}: {translated}/{total} ({percent:.2f}%)")
    section = report.get("engine_rby") or {}
    if section.get("available", True) and section.get("total"):
        print(f"  RBY-related engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    elif report.get("engine_rby_warning"):
        print(f"  RBY-related engine strings: unavailable ({report['engine_rby_warning']})")


def build(
    red_rom: Path,
    blue_rom: Path,
    language: str,
    language_name: str,
    luajit: str,
    localized_rom: Path | None = None,
) -> Path:
    """Execute the complete private extraction, translation, and pack flow."""
    language = canonical_language(language)
    verify_rom(red_rom, "red")
    verify_rom(blue_rom, "blue")
    if language in WESTERN_FONT_LANGUAGES and localized_rom is None:
        raise BuildError(
            f"A {language_name} Pokémon Red or Blue ROM is required as the font source."
        )
    if language == "ja-Hrkt" and localized_rom is not None:
        raise BuildError(
            "Localized font extraction is not yet supported for Japanese."
        )
    if localized_rom is not None:
        # Localized dumps are user-owned inputs.  We validate their basic
        # cartridge structure but deliberately do not enforce a SHA-1.
        validate_localized_rom(localized_rom)

    dependency_root = ROOT / ".cache" / "dependencies"
    gen1recomp = dependency_root / "gen1recomp"
    corpus = dependency_root / "poke-corpus"
    config = project_config()
    engine_source = config["gen1recomp"]
    corpus_source = config["corpus"]
    ensure_checkout(
        engine_source["source"], engine_source["revision"], gen1recomp
    )
    ensure_checkout(
        corpus_source["source"],
        corpus_source["revision"],
        corpus,
        sparse_paths=("corpus/RedBlue",),
    )

    print("\nExtracting private ROM data...")
    import_rom(
        "red", red_rom, gen1recomp,
        gen1recomp / "data" / "generated",
        gen1recomp / "assets" / "generated",
    )
    import_rom(
        "blue", blue_rom, gen1recomp,
        gen1recomp / "blue" / "data" / "generated",
        gen1recomp / "blue" / "assets" / "generated",
    )

    build_root = ROOT / ".cache" / "interactive" / language
    scaffold = build_root / "translation_source"
    modkit = gen1recomp / "tools" / "modkit.py"
    env = dict(os.environ)
    env["MODKIT_LUAJIT"] = luajit
    env["LUA"] = luajit
    _run(
        [
            sys.executable, str(modkit), "--repo", str(gen1recomp),
            "translation", "translation_source", "--language", language_name,
            "--base", "imported", "--dest", str(build_root), "--force",
        ],
        cwd=gen1recomp,
        env=env,
    )
    worksheet = assemble_worksheet(
        scaffold, build_root / "complete-modkit-worksheet"
    )

    print("\nMatching poke-corpus translations...")
    records = parse_redblue(corpus, language)
    rows = align(records, target_lang=language)
    overrides = _override_path(language, "overrides.json")
    if overrides:
        rows = apply_overrides(rows, overrides)
    mod = build_root / "mod"
    coverage = build_root / "coverage.json"
    generate_mod(
        rows,
        mod,
        mod_id=f"translation-{language.lower()}",
        language=language,
        target_name=f"{language_name} translation",
        modkit_worksheet=worksheet,
        report_path=coverage,
        engine_overrides=_override_path(language, "engine_overrides.json"),
        semantic_anchors=ROOT / "config" / "semantic_anchors.json",
        strict_engine=True,
        engine_source=gen1recomp / "src",
        engine_scope=ROOT / "config" / "engine_scope.json",
    )
    preserve_scaffold_support(scaffold, mod)
    if localized_rom is not None:
        extract_localized_font(
            localized_rom,
            mod,
            language=language,
            manifest=gen1recomp / "tools" / "rom_manifest.json",
        )

    version = project_version()
    output = ROOT / "dist" / f"translation-{language.lower()}-{version}.zip"
    candidate = build_root / f"translation-{language.lower()}-{version}.candidate.zip"
    candidate.unlink(missing_ok=True)
    _run(
        [
            sys.executable, str(modkit), "--repo", str(gen1recomp),
            "pack", str(mod), "-o", str(candidate), "--base", "imported",
        ],
        cwd=gen1recomp,
        env=env,
    )
    published = publish_archive(candidate, output)
    print_coverage(coverage)
    return published


def main(input_fn: Callable[[str], str] = input) -> int:
    print("Gen1Recomp translation mod builder\n")
    try:
        luajit = check_prerequisites()
        rom_paths = load_rom_paths(ROOT / "config" / "rom_paths.toml")
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
        language, language_name = _prompt_language(input_fn)
        localized = None
        if canonical_language(language) in WESTERN_FONT_LANGUAGES:
            language_name = dict(LANGUAGES)[language]
            localized = _prompt_configured_path(
                f"Please specify one localized {language_name} Pokémon Red or Blue ROM for its font "
                "(full path, e.g. /Games/PokemonLocalized.gb): ",
                configured_path(rom_paths, "localized", language),
                input_fn,
            )
        _print_language_warning(language)
        verify_rom(red, "red")
        verify_rom(blue, "blue")
        if localized is not None:
            validate_localized_rom(localized)
        if not _confirm(input_fn):
            print("\nBuild cancelled. No repositories were cloned.")
            return 0
        output = build(
            red,
            blue,
            language,
            language_name,
            luajit,
            localized_rom=localized,
        )
    except (BuildError, ValueError, OSError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    print(f"\nFile generated at {output}")
    return 0
