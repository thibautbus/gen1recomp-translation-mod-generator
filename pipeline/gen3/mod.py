"""Generation-3 translation mod helpers both families build with.

FireRed/LeafGreen (pipeline.frlg) and Emerald (pipeline.rse) ship the same
kind of mod to the same game3 runtime: dialogue as text IR, name catalogs
through the public content registries and engine strings through
``Strings()``.  This module holds what their builds share: the Lua
rendering, the registry hooks, the ROM-label migration of the strings
catalog, the private extract link Modkit reads, the coverage summary and the
packaging.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping

from ..shared.builder import BuildError
from ..shared.generate import lua_string
from ..shared.project import is_frozen, which_luajit
from .join import SHIPPED
from .text import ir_plain

# Catalog name -> the registry call applying one value.
CATALOG_HOOKS: Mapping[str, str] = {
    "species_names": "mod.content.pokemon:patch(id, { name = value })",
    "move_names": "mod.content.moves:patch(id, { name = value })",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "item_descriptions": "mod.content.items:patch(id, { description = value })",
    "trainer_names": "mod.content.trainers:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { className = value })",
    "strings": "mod.content.strings:override(id, value)",
    # The same registry: only the file is separate (ENGLISH_LOOKUP_SITES).
    "strings_by_english": "mod.content.strings:override(id, value)",
}


def _lua_value(value) -> str:
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "{ " + ", ".join(_lua_value(item) for item in value) + " }"
    raise TypeError(f"unsupported IR value {value!r}")


def lua_ir(segments: list[Mapping]) -> str:
    """One IR list as a Lua table literal, fields in a fixed order.

    Every field the runtime reads back is written: a tag segment is drawn
    from its ``tag`` (``{PKMN}``, ``{A_BUTTON}``; text_ir.lua expand_seg), an
    ``ext`` carries the arguments the Easy Chat keyboard and the Battle
    Records screen take their column from, and Emerald's dialect names its
    placeholders and fonts (``name``, ``font``).
    """
    parts = []
    for segment in segments:
        fields = [f"{key} = {_lua_value(segment[key])}"
                  for key in ("t", "s", "n", "code", "name", "cmd", "font", "tag", "args") if key in segment]
        parts.append("{ " + ", ".join(fields) + " }")
    return "{ " + ", ".join(parts) + " }"


# ---------------------------------------------------------------- ROM labels

def _modkit(gen1recomp: Path):
    """The pinned engine's own Modkit, imported for its ROM text harvest."""
    import importlib.util

    path = Path(gen1recomp) / "tools" / "modkit.py"
    spec = importlib.util.spec_from_file_location("gen1recomp_modkit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rom_label_strings(values: Mapping[str, str], gen1recomp: str | Path,
                      scope: Mapping[str, Mapping] | None = None,
                      luajit: str | None = None,
                      english_lookup_sites: tuple[str, ...] = ()) -> tuple[dict[str, str], dict[str, str]]:
    """Key every cart string of the engine catalog by its ROM label.

    Since gen1recomp v0.3.0 the runtime looks a cart string up by its label
    first (``Strings.translateLabel``), and ``modkit pack`` refuses a
    ``lang/strings.lua`` that still keys one by its English (MK306).  This is
    Modkit's own migration (``migrate_rom_text``), run on the catalog before
    it is written: an entry whose key is the English of a ROM label moves
    onto that label, and only the literals the engine passes to ``Strings()``
    itself stay keyed by their English.

    Returns the migrated catalog and, separately, the entries of
    ``english_lookup_sites`` (the family's) the migration took away: the runtime reads those
    by their English, so they are written to a catalog of their own and
    registered exactly the same way (``mod.content.strings:override``), which
    is the only key the screens that draw them will find.
    """
    gen1recomp = Path(gen1recomp)
    modkit = _modkit(gen1recomp)
    repo = str(gen1recomp.resolve())
    # rom_text_caches also looks in the LOVE user data root, where this
    # machine may hold its own game3 import: a build reads the extract it
    # was given and nothing else.
    caches = [row for row in modkit.rom_text_caches(repo)
              if os.path.abspath(row[0]).startswith(repo + os.sep)]
    if not caches:
        raise BuildError(
            "no imported game3 cache to read the cart's ROM labels from; "
            "the extract must be mounted before the catalogs are written"
        )
    # harvest_rom_text runs $LUA, or else the luajit on PATH, and a standalone
    # build carries its own LuaJIT where no PATH looks: hand Modkit that one.
    luajit = luajit or which_luajit()
    if luajit is None:
        raise BuildError("LuaJIT is required to read the cart's ROM labels; see MODKIT_LUAJIT")
    previous = os.environ.get("LUA")
    os.environ["LUA"] = str(luajit)
    try:
        rom_text = modkit.harvest_rom_text(repo, caches)
    finally:
        if previous is None:
            os.environ.pop("LUA", None)
        else:
            os.environ["LUA"] = previous
    engine_literals = {literal for literal, _site in modkit.harvest_engine_strings(repo)}
    done = {lua_string(key): lua_string(value) for key, value in values.items()}
    modkit.migrate_rom_text(done, rom_text, engine_literals)
    # A key whose label already carried a translation is left behind by the
    # migration, and pack refuses it all the same: the cart's own text is
    # translated on the label, so the English key goes.
    for key in modkit.rom_english_keys(rom_text) - engine_literals:
        done.pop(key, None)
    catalog = {_unlua(key): _unlua(value) for key, value in done.items()}
    scope = scope or {}
    by_english = {
        key: value for key, value in values.items()
        if key not in catalog
        and str(scope.get(key, {}).get("callsite", "")).startswith(english_lookup_sites)
    }
    return catalog, by_english


_LUA_ESCAPE = re.compile(r"\\(\d{1,3}|.)", re.S)
_LUA_CONTROL = {"n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _unlua(literal: str) -> str:
    """The plain string a Lua literal Modkit handed back stands for."""
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token.isdigit():
            return chr(int(token))
        return _LUA_CONTROL.get(token, token)

    return _LUA_ESCAPE.sub(replace, literal[1:-1])


# ---------------------------------------------------------------- coverage

def gen3_coverage(joined: dict) -> dict:
    stats = joined["dialogue_stats"]
    catalog_stats = joined["catalog_stats"]
    named_total = sum(row["total"] for row in catalog_stats.values())
    named_covered = sum(row["translated"] + row["same_as_english"] for row in catalog_stats.values())
    total = stats["total"] + named_total
    covered = stats["covered"] + named_covered
    engine = joined["engine_stats"]
    return {
        "rom": {
            "translated": covered, "total": total,
            "percent": round(100.0 * covered / total, 2) if total else 100.0,
            "ignored_markup_only": stats["ignored_markup_only"],
            "ignored_undecodable": stats["ignored_undecodable"],
            "ignored_japanese_source": stats["ignored_japanese_source"],
        },
        "dialogue": {key: stats[key] for key in
                     ("total", "covered", "shipped", "percent", "by_status",
                      "ignored_markup_only", "ignored_undecodable", "ignored_japanese_source")},
        "named_catalogs": catalog_stats,
        "engine_gen3": {key: engine[key] for key in ("translated", "total", "percent", "fallback_english")},
        "policy": "english-fallback",
    }


def unresolved_entries(joined: dict) -> list[dict]:
    return [
        {"key": entry.key, "status": entry.status, "qid": entry.qid,
         "english": ir_plain(entry.english), "detail": entry.detail}
        for entry in joined["entries"] if entry.status not in SHIPPED and entry.status != "same_as_english"
    ]


# ---------------------------------------------------------------- build

def _is_link(path: Path) -> bool:
    """A symlink, or the directory junction a Windows build links with."""
    if path.is_symlink():
        return True
    if hasattr(os.path, "isjunction"):  # Python 3.12
        return os.path.isjunction(path)
    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
    except OSError:
        return False
    return tag == 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT


def _link_directory(link: Path, target: Path) -> None:
    """Point ``link`` at the directory ``target``.

    Windows only lets an elevated prompt or Developer Mode make a symlink, so
    a build there makes a directory junction instead, which needs neither and
    which Modkit reads through the same way.
    """
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_directory(link: Path) -> None:
    """Remove a link made by _link_directory, never what it points at."""
    try:
        link.unlink()
    except OSError:
        if not _is_link(link):
            raise
        os.rmdir(link)  # a junction, where unlink refuses a directory


@contextmanager
def rom_text_cache(gen1recomp: Path, extracted: Path, edition: str = "firered"):
    """Let Modkit read the imported cart text while the mod is built.

    ``modkit pack`` checks that a strings catalog never keys a ROM English
    text (MK306), and both that check and the ROM labels the catalogs are
    keyed by come from the imported cache Modkit looks for under
    ``<repo>/<edition>/data/generated/gba`` (``rom_text_caches``: firered,
    leafgreen or emerald).  The extract itself stays private, in the build
    workspace; only a link points at it, and only for this build: a Red/Blue
    or Gold build that shares the engine checkout must not find a game3
    cache, or its own scaffold would list the cart's text too.
    """
    link = gen1recomp / edition
    target = (extracted / "cache").resolve()
    if not (target / "data" / "generated" / "gba" / "scripts" / "text.lua").is_file():
        raise BuildError(f"the {edition} extract has no script text cache: {target}")
    if _is_link(link):
        _unlink_directory(link)
    elif link.is_file():
        link.unlink()
    elif link.is_dir():
        # A real import lives there (Modkit documents <repo>/firered as the
        # place it reads the cart's text from): the build never removes one.
        raise BuildError(f"{link} is a directory, not this build's link to its extract")
    _link_directory(link, target)
    try:
        yield link
    finally:
        if _is_link(link):
            _unlink_directory(link)


def package_gen3_mod(
    mod_dir: Path, gen1recomp: Path, build_root: Path, destination: Path, archive_name: str,
    luajit: str | None = None, log_fn: Callable[[str], None] | None = None,
) -> Path:
    from ..shared.orchestration import package_release

    env = None
    if luajit is not None:
        env = dict(os.environ)
        env["MODKIT_LUAJIT"] = str(luajit)
        env["LUA"] = str(luajit)
        env["PYTHONUTF8"] = "1"
        if is_frozen():
            env["PATH"] = str(Path(luajit).resolve().parent) + os.pathsep + env.get("PATH", "")
    return package_release(
        mod_dir, gen1recomp, gen1recomp / "tools" / "modkit.py", build_root, destination,
        archive_name, env=env, log_fn=log_fn,
    )
