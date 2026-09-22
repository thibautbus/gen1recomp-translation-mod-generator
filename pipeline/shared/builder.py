"""Interactive, end-to-end translation mod builder."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Callable
import zipfile

from .corpus import canonical_language
from .mod_assets import font_profile_warning, validate_font_profile
from .project import (
    is_frozen,
    resource_root,
    which_luajit as _which_luajit,
    luajit_install_hint as _luajit_install_hint,
)
from .dependencies import DependencyError, fetch_archive, fetch_files
from .roms import verify_crystal_rom, verify_firered_rom, verify_gs_rom, verify_rb_rom, verify_rom
from .subprocess_run import run_streamed
from .rom_paths import configured_path, load_rom_paths
from .specs import game_spec, languages_for_collection, release_profile, release_profile_for_generation
from .specs import BuildRequest


def languages_for_generation(generation: int) -> tuple[tuple[str, str], ...]:
    """Return the union of languages published by a release's collections."""
    profile = release_profile_for_generation(generation)
    languages: dict[str, str] = {}
    for game in profile.games:
        for code, name in languages_for_collection(game_spec(game).corpus_collection):
            languages.setdefault(code, name)
    return tuple(languages.items())


FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".gb", ".gbc", ".rom", ".ips", ".bps", ".ups", ".patch", ".diff",
}
FORBIDDEN_ARCHIVE_PARTS = {
    "data/generated", "assets/generated", "gameversion", "worksheet",
}


class BuildError(RuntimeError):
    """An expected failure that can be presented directly to the user."""


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
    # The GUI surfaces a raised BuildError in a single error dialog; the
    # full transcript only lives in its separate, easy-to-miss "Show log"
    # panel, so a bare exit code left a bug report with nothing else to go
    # on -- run_streamed includes the command's own tail in the message too.
    run_streamed(command, cwd=cwd, env=env, log_fn=log_fn, error_cls=BuildError)


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
            return fetch_archive(url, digest, destination, revision=str(config.get("revision", "")), selective_prefix=single, immutable_prefixes=("src", "tools"), trusted_tree_sha256=str(config.get("archive_tree_sha256", "")))
        except DependencyError as error:
            raise BuildError(f"Unable to download pinned dependency: {error}") from error
    ensure_checkout(config["source"], config["revision"], destination, sparse_paths=prefixes)
    return destination


def _font_source(workspace: Path, config: dict, font_profile: str = "fusion", language: str = "fr") -> Path:
    """Download only the selected pinned font dependency."""
    profile = validate_font_profile(language, font_profile)
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
        elif canonical_language(language) == "ko":
            # Korean is part of the pinned Fusion archive, unlike Japanese
            # which is fetched from its companion archive.
            korean_font = source / "fusion-pixel-10px-proportional-ko.ttf"
            if not korean_font.is_file():
                raise BuildError("Pinned Fusion Pixel dependency has no Korean font variant.")
        return source
    except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
        raise BuildError(f"Unable to download pinned font dependency: {error}") from error


def prepare_dependencies(
    workspace: Path,
    config: dict,
    *,
    corpus_collection: str | tuple[str, ...],
    font_profile: str | None,
    language: str,
    engine_source: str | Path | None = None,
) -> tuple[Path, Path, Path | None]:
    """Prepare the common engine/corpus/font inputs for any release profile.

    ``font_profile=None`` skips the font download for a release that cannot
    register one (FireRed: Schemas.GEN3 gates the ``font`` registry).
    """
    dependency_root = workspace / "dependencies"
    if engine_source is None:
        gen1recomp = dependency_root / "gen1recomp"
    else:
        from .engine_profile import validate_upstream_checkout
        try:
            gen1recomp = validate_upstream_checkout(engine_source)
        except (OSError, ValueError) as error:
            raise BuildError(f"Unable to validate upstream Gen1Recomp checkout: {error}") from error
    corpus = dependency_root / "poke-corpus"
    if engine_source is None:
        _ensure_dependency(config["gen1recomp"], gen1recomp)
        # The next phases execute both src-owned extractors and tools/modkit.
        # Do this immediately after checkout/cache publication so neither can
        # run against a dirty or tampered engine checkout.
        from .engine_manifest import load_manifest, verified_source
        try:
            verified_source(gen1recomp, load_manifest())
        except (OSError, ValueError) as error:
            raise BuildError(f"Unable to verify prepared Gen1Recomp dependency: {error}") from error
    # A profile declares its collection; callers cannot accidentally fetch a
    # second generation's corpus just because the other flow did so first.
    collections = (corpus_collection,) if isinstance(corpus_collection, str) else corpus_collection
    prefixes = [f"corpus/{collection}" for collection in collections]
    _ensure_dependency(config["corpus"], corpus, selective_prefix=prefixes)
    font_source = None if font_profile is None else _font_source(workspace, config, font_profile, language)
    return gen1recomp, corpus, font_source


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
        # destination can exist without being a git checkout: a prior run
        # that used the archive path instead (frozen build, or a switch
        # between the two), an interrupted clone, or a corrupted directory.
        # `git clone` refuses to run into a non-empty directory, so replace
        # it rather than crash -- this is disposable cache, never user data.
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
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


def _prompt_generation(input_fn: Callable[[str], str]) -> int:
    """Ask which games to translate; the answer decides which other prompts
    follow (ROM count, later the language list). Named by game, since
    "generation" is engine vocabulary the user does not need.
    """
    print("\nWhich games do you want to translate?")
    print("  1 - Red, Blue and Yellow      (generation 1)")
    print("  2 - Gold, Silver and Crystal  (generation 2)")
    print("  3 - FireRed                   (generation 3)")
    raw = input_fn("Games number [1]: ").strip()
    if raw in {"", "1"}:
        return 1
    if raw == "2":
        return 2
    if raw == "3":
        return 3
    raise BuildError(f"Invalid games selection: {raw!r}")


def _prompt_language(
    input_fn: Callable[[str], str], collection: str | None = "RedBlue", generation: int | None = None,
) -> tuple[str, str]:
    languages = languages_for_generation(generation) if generation is not None else languages_for_collection(collection or "RedBlue")
    print("\nPlease specify the output language:")
    for index, (code, name) in enumerate(languages, 1):
        print(f"  {index} - {name} ({code})")
    raw = input_fn("Language number: ").strip()
    try:
        return languages[int(raw) - 1]
    except (ValueError, IndexError):
        raise BuildError(f"Invalid language selection: {raw!r}") from None


def _prompt_font_profile(language: str, input_fn: Callable[[str], str]) -> str:
    """Choose a font profile after language selection."""
    language = canonical_language(language)
    if language == "ja-Hrkt":
        print("\nJapanese uses Fusion Pixel by TakWolf, proportional 8px.")
        return "fusion"
    if language == "ko":
        print("\nKorean uses Fusion Pixel's Hangul variant; Pokemon Font is unavailable.")
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


def _confirm(input_fn: Callable[[str], str]) -> bool:
    action = "downloaded" if is_frozen() else "cloned"
    answer = input_fn(
        f"\nGen1Recomp and poke-corpus are about to be {action} locally into "
        "the private .cache directory.\nDo you wish to continue? [Y/n]: "
    )
    return answer.strip().lower() in {"", "y", "yes"}


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


def main(
    input_fn: Callable[[str], str] = input,
    font_profile: str | None = None,
    generation: int | None = None,
) -> int:
    print("Gen1Recomp translation mod builder\n")
    try:
        luajit = check_prerequisites()
        rom_paths = load_rom_paths(resource_root() / "config" / "rom_paths.toml")
        if generation is None:
            generation = _prompt_generation(input_fn)
        elif generation not in (1, 2, 3):
            raise BuildError(f"Invalid games selection: {generation!r}")
        if generation == 3:
            firered_prompt = (
                "Please specify the location of your Pokemon FireRed ROM "
                "(full path, e.g. C:\\Games\\PokemonFireRed.gba): "
            )
            firered_rom = _prompt_configured_path(
                firered_prompt, configured_path(rom_paths, "rom", "firered"), input_fn
            )
            language, language_name = _prompt_language(input_fn, generation=generation)
            if font_profile:
                print("Note: FireRed prints every string with the cart's own font; "
                      "--font-profile is ignored.")
            verify_firered_rom(firered_rom)
            if not _confirm(input_fn):
                if is_frozen():
                    print("\nBuild cancelled. No dependency downloads were performed.")
                else:
                    print("\nBuild cancelled. No repositories were cloned.")
                return 0
            from .orchestration import build_request
            output = build_request(
                BuildRequest({"firered": firered_rom}, release_profile("frlg"), language, None),
                language_name=language_name, luajit=luajit,
            )
        elif generation == 1:
            rb_prompt = (
                "Please specify the location of your Pokemon Red or Blue ROM "
                "(full path, e.g. C:\\Games\\PokemonRed.gb): "
            )
            rb_rom = _prompt_configured_path(
                rb_prompt, configured_path(rom_paths, "rom", "red"), input_fn
            )
            yellow_prompt = (
                "Please specify the location of your Pokemon Yellow ROM "
                "(full path, e.g. C:\\Games\\PokemonYellow.gb): "
            )
            yellow = _prompt_configured_path(
                yellow_prompt, configured_path(rom_paths, "rom", "yellow"), input_fn
            )
            language, language_name = _prompt_language(input_fn, generation=generation)
            selected_profile = font_profile or _prompt_font_profile(language, input_fn)
            selected_profile = validate_font_profile(language, selected_profile)
            if font_profile:
                warning = font_profile_warning(selected_profile)
                if warning:
                    print(f"Warning: {warning}")
            verify_rb_rom(rb_rom)
            verify_rom(yellow, "yellow")
            if not _confirm(input_fn):
                if is_frozen():
                    print("\nBuild cancelled. No dependency downloads were performed.")
                else:
                    print("\nBuild cancelled. No repositories were cloned.")
                return 0
            from .orchestration import build_request
            output = build_request(
                BuildRequest({"rb": rb_rom, "yellow": yellow}, release_profile("rby"), language, None, selected_profile),
                language_name=language_name, luajit=luajit,
            )
        else:
            gs_prompt = (
                "Please specify the location of your Pokemon Gold or Silver ROM "
                "(full path, e.g. C:\\Games\\PokemonGold.gbc): "
            )
            gs_rom = _prompt_configured_path(
                gs_prompt, configured_path(rom_paths, "rom", "gold"), input_fn
            )
            crystal_prompt = (
                "Please specify the location of your Pokemon Crystal ROM "
                "(full path, e.g. C:\\Games\\PokemonCrystal.gbc): "
            )
            crystal_rom = _prompt_configured_path(
                crystal_prompt, configured_path(rom_paths, "rom", "crystal"), input_fn
            )
            language, language_name = _prompt_language(input_fn, generation=generation)
            selected_profile = font_profile or _prompt_font_profile(language, input_fn)
            selected_profile = validate_font_profile(language, selected_profile)
            if font_profile:
                warning = font_profile_warning(selected_profile)
                if warning:
                    print(f"Warning: {warning}")
            verify_gs_rom(gs_rom)
            verify_crystal_rom(crystal_rom)
            if not _confirm(input_fn):
                if is_frozen():
                    print("\nBuild cancelled. No dependency downloads were performed.")
                else:
                    print("\nBuild cancelled. No repositories were cloned.")
                return 0
            from .orchestration import build_request
            output = build_request(
                BuildRequest({"gs": gs_rom, "crystal": crystal_rom}, release_profile("gsc"), language, None, selected_profile),
                language_name=language_name, luajit=luajit,
            )
    except (RuntimeError, ValueError, OSError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    print(f"\nFile generated at {output}")
    return 0
