"""FireRed (generation 3) translation mod: generation, release gate, build.

The mod targets gen1recomp's game3 runtime through the public generation-3
content registries (src/mods/Schemas.lua, ``Schemas.GEN3``):

* ``text``: every dialogue key is overridden with its IR segment list, the
  only value form that keeps the runtime's placeholders and page breaks
  (pipeline.frlg.text);
* ``pokemon`` (name), ``moves`` (name), ``items`` (name, description) and
  ``trainers`` (name, class name), keyed the way ``G3.idOf`` and
  ``G3.trainerIds`` key them;
* ``strings`` for the few game3 interface strings that go through
  ``Strings()``.

No font is registered: ``Schemas.GEN3`` gates the ``font`` registry and
game3 draws every string with the cart's own Latin font.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable, Mapping

from ..shared.builder import BuildError, _run
from ..shared.corpus import canonical_language
from ..shared.dependencies import DependencyError, fetch_files
from .join import (
    SHIPPED,
    CatalogResult,
    dialogue_catalog,
    join_frlg_dialogue,
    join_frlg_engine_strings,
    join_indexed_catalog,
    join_item_descriptions,
    join_start_menu,
    load_frlg_corpus,
    load_frlg_dialogue_decisions,
    load_frlg_dialogue_overrides,
    load_frlg_engine_scope,
    registry_ids,
)
from .text import ir_plain, load_charmap, load_symbols
from ..shared.generate import lua_string
from ..shared.mod import TRANSLATION_MOD_PRIORITY
from ..shared.project import is_frozen, project_config, project_version, resource_root
from ..shared.roms import import_frlg_rom, verify_firered_rom
from ..shared.specs import game_spec, languages_for_collection, release_profile

MAIN = '''-- Generated FireRed translation mod.
return function(mod)
  local function catalog(name)
    local body = mod:read("lang/" .. name .. ".lua")
    if not body then return {} end
    local chunk = loadstring(body)
    if not chunk then return {} end
    local ok, value = pcall(chunk)
    return ok and type(value) == "table" and value or {}
  end
  -- Dialogue values are text IR segment lists (src/core/game3/scripting/
  -- text_ir.lua), keyed by the extractor's ROM-pointer text keys.
  for key, ir in pairs(catalog("dialogue")) do
    if type(ir) == "table" and #ir > 0 then mod.content.text:override(key, ir) end
  end
  local function each(name, apply)
    for id, value in pairs(catalog(name)) do
      if type(value) == "string" and value ~= "" then apply(id, value) end
    end
  end
__CATALOG_REGISTRATION____START_MENU_REGISTRATION__end
'''

# The start menu prints its entry labels without Strings(); the public
# ui.start_menu.items hook (src/ui/game3/start_menu.lua) hands the entry list
# over first, keyed by entry id.
_START_MENU_REGISTRATION = '''  local startMenu = catalog("start_menu")
  if next(startMenu) ~= nil then
    mod.hooks:wrap("ui.start_menu.items", function(nextFn, game, items)
      for _, item in ipairs(type(items) == "table" and items or {}) do
        local value = type(item) == "table" and startMenu[item.id]
        if type(value) == "string" and value ~= "" then item.label = value end
      end
      return nextFn(game, items)
    end)
  end
'''

# Catalog name -> the registry call applying one value.
FRLG_CATALOG_HOOKS: Mapping[str, str] = {
    "species_names": "mod.content.pokemon:patch(id, { name = value })",
    "move_names": "mod.content.moves:patch(id, { name = value })",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "item_descriptions": "mod.content.items:patch(id, { description = value })",
    "trainer_names": "mod.content.trainers:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { className = value })",
    "strings": "mod.content.strings:override(id, value)",
}

GATE_REPORT_NAME = "gate_report.json"


def frlg_mod_id(language: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3"


def frlg_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3-{version}.zip"


def _lua_value(value) -> str:
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"unsupported IR value {value!r}")


def lua_ir(segments: list[Mapping]) -> str:
    """One IR list as a Lua table literal, fields in a fixed order."""
    parts = []
    for segment in segments:
        fields = [f"{key} = {_lua_value(segment[key])}"
                  for key in ("t", "s", "n", "code", "cmd") if key in segment]
        parts.append("{ " + ", ".join(fields) + " }")
    return "{ " + ", ".join(parts) + " }"


def generate_frlg_mod(
    destination: str | Path,
    *,
    language: str,
    target_name: str,
    dialogue: Mapping[str, list[dict]],
    catalogs: Mapping[str, Mapping[str, str]],
    mod_id: str | None = None,
) -> Path:
    """Write a deterministic manifest, entry point and catalogs."""
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    unknown = set(catalogs) - set(FRLG_CATALOG_HOOKS) - {"start_menu"}
    if unknown:
        raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")
    lang_dir = destination / "lang"
    if lang_dir.exists():
        shutil.rmtree(lang_dir)
    lang_dir.mkdir(parents=True)

    lines = [f"-- Generated by the FireRed pipeline ({language}): dialogue", "return {"]
    lines.extend(f"  [{lua_string(key)}] = {lua_ir(ir)}," for key, ir in sorted(dialogue.items()))
    lines.append("}")
    (lang_dir / "dialogue.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")

    registration = ""
    start_menu = catalogs.get("start_menu") or {}
    if start_menu:
        lines = [f"-- Generated by the FireRed pipeline ({language}): start_menu", "return {"]
        lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(start_menu.items()))
        lines.append("}")
        (lang_dir / "start_menu.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name in FRLG_CATALOG_HOOKS:
        values = catalogs.get(name) or {}
        if not values:
            continue
        lines = [f"-- Generated by the FireRed pipeline ({language}): {name}", "return {"]
        lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items()))
        lines.append("}")
        (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        registration += f'  each("{name}", function(id, value) {FRLG_CATALOG_HOOKS[name]} end)\n'
    main = MAIN.replace("__CATALOG_REGISTRATION__", registration).replace(
        "__START_MENU_REGISTRATION__", _START_MENU_REGISTRATION if start_menu else "")
    (destination / "main.lua").write_text(main, encoding="utf-8")

    manifest = {
        "id": mod_id or frlg_mod_id(language), "name": target_name, "version": project_version(),
        "api": 2, "entry": "main.lua", "profile": "content", "games": ["firered"],
        "game_version": ">=0.0.0-dev <1.0.0", "category": "LANGUAGE",
        "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [],
        "conflicts": [], "permissions": [],
        "description": f"{target_name}, based mostly on PokeCorpus.",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def attach_frlg_validation(mod_dir: str | Path, validation: dict) -> None:
    path = Path(mod_dir) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["validation"] = validation
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- inputs

def prepare_pret_inputs(workspace: str | Path, config: Mapping) -> tuple[Path, Path]:
    """Download the pinned pret symbol table and charmap."""
    root = Path(workspace) / "dependencies" / "pret"
    pret = config.get("pret") or {}
    paths = []
    for name, filename in (("symbols", "pokefirered.sym"), ("charmap", "charmap.txt")):
        section = pret.get(name) or {}
        try:
            folder = fetch_files(
                str(section["archive_base_url"]), dict(section["archive_files"]),
                root / name, revision=str(section.get("revision", "")),
            )
        except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
            raise BuildError(f"Unable to download pinned pret {name}: {error}") from error
        paths.append(folder / filename)
    return paths[0], paths[1]


def _numbered(path: Path) -> dict[int, object]:
    return {int(key): value for key, value in json.loads(path.read_text(encoding="utf-8")).items()}


# ---------------------------------------------------------------- join

def join_frlg(
    extracted: str | Path,
    corpus_dir: str | Path,
    language: str,
    symbols_path: str | Path,
    charmap_path: str | Path,
) -> dict:
    """Run every FireRed join for one language; return catalogs and stats."""
    extracted = Path(extracted)
    language = canonical_language(language)
    charmap = load_charmap(charmap_path)
    symbols = load_symbols(symbols_path)
    corpus = load_frlg_corpus(corpus_dir, language)

    text = json.loads((extracted / "frlg_text.json").read_text(encoding="utf-8"))
    entries, dialogue_stats = join_frlg_dialogue(
        text, corpus, symbols, charmap,
        overrides=load_frlg_dialogue_overrides(language),
        decisions=load_frlg_dialogue_decisions(),
    )

    species = _numbered(extracted / "frlg_species.json")
    moves = _numbered(extracted / "frlg_moves.json")
    items = _numbered(extracted / "frlg_items.json")
    trainers = _numbered(extracted / "frlg_trainers.json")

    species_ids = registry_ids(species)
    item_names = {number: row.get("name") for number, row in items.items()}
    item_ids = registry_ids(item_names)
    trainer_ids = {number: str(number) for number in trainers}
    results: dict[str, CatalogResult] = {
        "species_names": join_indexed_catalog(
            species, species_ids, corpus, "frlg.common.species_names.gSpeciesNames.", charmap),
        "move_names": join_indexed_catalog(
            moves, registry_ids(moves), corpus, "frlg.common.move_names.gMoveNames.", charmap),
        "item_names": join_indexed_catalog(
            item_names, item_ids, corpus, "frlg.common.items.gItems.", charmap),
        "item_descriptions": join_item_descriptions(items, item_ids, corpus, charmap),
        "trainer_names": join_indexed_catalog(
            {number: row.get("name") for number, row in trainers.items()}, trainer_ids, corpus,
            "frlg.common.trainers.gTrainers.", charmap),
        # A trainer's class name is patched on the trainer row itself
        # (Trainers.get reads row.className first), keyed by trainer id.
        "trainer_class_names": _trainer_class_names(trainers, trainer_ids, corpus, charmap),
        "start_menu": join_start_menu(corpus, charmap),
    }
    strings, engine_stats = join_frlg_engine_strings(load_frlg_engine_scope(), corpus, charmap)
    catalogs = {name: result.values for name, result in results.items()}
    catalogs["strings"] = strings
    return {
        "entries": entries,
        "dialogue": dialogue_catalog(entries),
        "dialogue_stats": dialogue_stats,
        "catalogs": catalogs,
        "catalog_stats": {name: result.summary() for name, result in results.items()},
        "catalog_issues": {name: result.issues for name, result in results.items()},
        "engine_stats": engine_stats,
        "numbers": {"species": species_ids, "items": item_ids, "moves": registry_ids(moves)},
    }


# Class names game3 compares as English strings, kept in English until the
# engine compares class ids (docs/upstream-fixes.md, FireRed):
# - Trainers.info (src/core/game3/scripting/trainers.lua:277) only
#   substitutes the player's chosen rival name when className == "RIVAL"
#   (de/it RIVALE would print the ROM's placeholder name TERRY);
# - the quest log (src/core/game3/quest_log_recorder.lua:94-100) records gym
#   leader, Elite Four and champion wins from className == "LEADER",
#   "ELITE FOUR" and "CHAMPION" (French LEADER reads CHAMPION, so every gym
#   win would be logged as a champion win).
ENGINE_KEYED_CLASS_NAMES = frozenset({"RIVAL", "LEADER", "ELITE FOUR", "CHAMPION"})


def _trainer_class_names(trainers, trainer_ids, corpus, charmap) -> CatalogResult:
    """Join each trainer class once, then fan it out to its trainers."""
    per_class = join_indexed_catalog(
        {row["class"]: row["className"] for row in trainers.values()
         if isinstance(row.get("class"), int) and row.get("className")
         and row["className"] not in ENGINE_KEYED_CLASS_NAMES},
        {row["class"]: str(row["class"]) for row in trainers.values() if isinstance(row.get("class"), int)},
        corpus, "frlg.common.trainer_class_names.gTrainerClassNames.", charmap,
    )
    result = CatalogResult()
    result.stats.update(per_class.stats)
    result.issues = per_class.issues
    kept = sorted({row["class"] for row in trainers.values()
                   if row.get("className") in ENGINE_KEYED_CLASS_NAMES and isinstance(row.get("class"), int)})
    result.stats["total"] += len(kept)
    result.stats["engine_keyed"] += len(kept)
    result.issues.extend(f"class {number}: kept in English (engine compares its name)" for number in kept)
    for number, row in trainers.items():
        value = per_class.values.get(str(row.get("class")))
        if row.get("className") and value:
            result.values[trainer_ids[number]] = value
    return result


# ---------------------------------------------------------------- gate

def _sample(values: Mapping[str, str], prefer: str | None = None) -> tuple[str, str] | None:
    if not values:
        return None
    key = prefer if prefer in values else sorted(values)[0]
    return key, values[key]


def write_gate_expectations(path: Path, joined: dict) -> dict:
    """One sample per shipped catalog, read back by tools/frlg/gate.lua."""
    catalogs = joined["catalogs"]
    numbers = joined["numbers"]
    expectations: dict[str, dict] = {}

    for entry in joined["entries"]:
        if entry.status in SHIPPED and entry.translation and any(
                segment.get("t") == "player" for segment in entry.translation):
            probe = max((segment.get("s", "") for segment in entry.translation if segment.get("t") == "text"),
                        key=len)
            expectations["dialogue"] = {"key": entry.key, "ir": entry.translation, "probe": probe.strip()}
            break

    def number_of(kind: str, id_: str) -> int:
        return next(number for number, value in numbers[kind].items() if value == id_)

    sample = _sample(catalogs.get("species_names", {}), "BULBASAUR")
    if sample:
        expectations["species_names"] = {"id": sample[0], "number": number_of("species", sample[0]), "value": sample[1]}
    sample = _sample(catalogs.get("move_names", {}), "POUND")
    if sample:
        expectations["move_names"] = {"id": sample[0], "number": number_of("moves", sample[0]), "value": sample[1]}
    for name in ("item_names", "item_descriptions"):
        sample = _sample(catalogs.get(name, {}), "POTION")
        if sample:
            expectations[name] = {"id": sample[0], "number": number_of("items", sample[0]), "value": sample[1]}
    for name in ("trainer_names", "trainer_class_names"):
        sample = _sample(catalogs.get(name, {}), "288")
        if sample:
            expectations[name] = {"id": sample[0], "value": sample[1]}
    if catalogs.get("start_menu"):
        expectations["start_menu"] = dict(catalogs["start_menu"])
    sample = _sample(catalogs.get("strings", {}), "YES")
    if sample:
        expectations["strings"] = {"id": sample[0], "value": sample[1]}
    path.write_text(json.dumps(expectations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expectations


def run_frlg_gate(
    mod_dir: Path, extracted: Path, joined: dict, gen1recomp: Path, luajit: str,
    *, log_fn: Callable[[str], None] | None = None,
) -> dict:
    expectation_path = mod_dir.parent / f".{mod_dir.name}.gate.json"
    report_path = mod_dir.parent / f".{mod_dir.name}.{GATE_REPORT_NAME}"
    write_gate_expectations(expectation_path, joined)
    script = resource_root() / "tools" / "frlg" / "gate.lua"
    try:
        _run([luajit, str(script), str(gen1recomp), str(extracted / "cache"), str(mod_dir),
              str(expectation_path), str(report_path)], cwd=gen1recomp, log_fn=log_fn)
        return json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        expectation_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- coverage

def frlg_coverage(joined: dict) -> dict:
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
        },
        "dialogue": {key: stats[key] for key in ("total", "covered", "shipped", "percent", "by_status")},
        "named_catalogs": catalog_stats,
        "engine_gen3": {key: engine[key] for key in ("translated", "total", "percent", "fallback_english")},
        "policy": "english-fallback",
    }


def write_frlg_report(path: Path, joined: dict, coverage: dict, gate: dict) -> None:
    """Private per-key report (it quotes ROM text: never packaged)."""
    unresolved = [
        {"key": entry.key, "status": entry.status, "qid": entry.qid,
         "english": ir_plain(entry.english), "detail": entry.detail}
        for entry in joined["entries"] if entry.status not in SHIPPED and entry.status != "same_as_english"
    ]
    path.write_text(json.dumps({
        "coverage": coverage,
        "dialogue_unresolved": unresolved,
        "catalog_issues": joined["catalog_issues"],
        "engine_details": joined["engine_stats"]["details"],
        "gate": gate,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- build

def package_frlg_mod(
    mod_dir: Path, gen1recomp: Path, build_root: Path, destination: Path, language: str,
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
        frlg_archive_name(language, project_version()), env=env, log_fn=log_fn,
    )


def build_frlg(
    firered_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> Path:
    """Extract, join, gate and package the FireRed translation mod."""
    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    profile = release_profile("frlg")
    spec = game_spec("firered")
    supported = {code for code, _name in languages_for_collection(spec.corpus_collection)}
    if language not in supported:
        raise BuildError(
            f"FireRed has no {language} release: gen1recomp's game3 runtime prints every "
            "string with the US cart's Latin font (see docs/upstream-fixes.md, FireRed)."
        )
    status("Validating ROMs")
    verify_firered_rom(firered_rom)

    from ..shared.orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language, font_profile=None,
    )
    workspace, destination, gen1recomp = context.workspace, context.destination, context.gen1recomp
    corpus_dir = context.corpus / "corpus" / spec.corpus_collection
    status("Preparing dependencies")
    symbols, charmap = prepare_pret_inputs(workspace, project_config())

    log("\nExtracting private FireRed ROM data...")
    status("Extracting private FireRed ROM data")
    extracted = workspace / "firered" / "extracted"
    import_frlg_rom(firered_rom, gen1recomp, extracted, log_fn=log_fn)

    log("\nJoining corpus and generating the mod...")
    status("Joining corpus and generating the mod")
    joined = join_frlg(extracted, corpus_dir, language, symbols, charmap)
    build_root = workspace / "interactive-gen3" / language
    mod_dir = build_root / frlg_mod_id(language)
    generate_frlg_mod(
        mod_dir, language=language, target_name=f"{language_name} translation for FireRed",
        dialogue=joined["dialogue"], catalogs=joined["catalogs"],
    )
    coverage = frlg_coverage(joined)

    status("Running FireRed release gate")
    gate = run_frlg_gate(mod_dir, extracted, joined, gen1recomp, luajit, log_fn=log_fn)
    attach_frlg_validation(mod_dir, {
        "schema": 1, "policy": "english-fallback", "coverage": coverage,
        "runtime_limits": {
            "blank_glyphs": gate.get("blank_glyphs", {}).get("total", 0),
            "names_survive_field_entry": all(
                row.get("survives") for row in (gate.get("persistence") or {}).values()),
            "strings_resolve_in_game": bool((gate.get("strings_live") or {}).get("resolves")),
        },
    })
    write_frlg_report(build_root / "coverage.json", joined, coverage, gate)
    for key, label in (("rom", "FireRed ROM aggregate"), ("engine_gen3", "FireRed engine strings")):
        section = coverage[key]
        log(f"  {label}: {section['translated']}/{section['total']} ({section['percent']:.2f}%)")
    blank = gate.get("blank_glyphs", {})
    if blank.get("total"):
        log(f"  runtime limit: {blank['total']} shipped characters have no glyph in FrlgFont yet"
            " (docs/upstream-fixes.md, FireRed)")

    status("Packaging translation mod")
    published = package_frlg_mod(mod_dir, gen1recomp, build_root, destination, language, luajit, log_fn)
    status("Build complete")
    return published
