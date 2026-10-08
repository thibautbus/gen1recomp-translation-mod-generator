"""FireRed/LeafGreen (generation 3) translation mod: generation, release gate,
build.

The mod targets gen1recomp's game3 runtime through the public generation-3
content registries (src/mods/Schemas.lua, ``Schemas.GEN3``), for both
editions at once:

* ``text``: every dialogue key is overridden with its IR segment list, the
  only value form that keeps the runtime's placeholders and page breaks
  (pipeline.gen3.text).  The named text both carts share is one layer; the
  script text, keyed by ROM address, is one layer per edition, since the two
  carts lay it out differently;
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
import shutil
from pathlib import Path
from typing import Callable, Mapping

from ..shared.builder import BuildError, _run
from ..shared.corpus import canonical_language
from ..shared.dependencies import DependencyError, fetch_files
from ..gen3.family import FRLG
from ..gen3.join import (
    SHIPPED,
    CatalogResult,
    dialogue_catalog,
    join_gen3_dialogue,
    join_gen3_engine_strings,
    join_indexed_catalog,
    join_item_descriptions,
    join_trainer_class_names,
    load_dialogue_decisions,
    load_dialogue_overrides,
    load_engine_scope,
    load_gen3_corpus,
    registry_ids,
)
from ..gen3.mod import (
    CATALOG_HOOKS,
    dialogue_label_rows,
    gen3_coverage,
    lua_ir,
    package_gen3_mod,
    rom_label_strings,
    rom_text_cache,
    unresolved_entries,
)
from ..gen3.text import load_charmap, load_symbols, text_key_address
from .start_menu import join_start_menu
from ..shared.generate import lua_string
from ..shared.pokedex_metrics import GEN3_SPECIES_METRICS_HOOK, lua_catalog, prepare_pokedex_metrics, species_metrics
from ..shared.mod_assets import TRANSLATION_MOD_PRIORITY
from ..shared.project import project_config, project_version, resource_root
from ..shared.roms import import_frlg_rom, verify_firered_rom, verify_leafgreen_rom
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
  -- text_ir.lua), keyed by the extractor's ROM-pointer text keys.  FireRed
  -- and LeafGreen lay their script text out at different addresses, so each
  -- edition reads its own layer on top of the named text both share.
  local okGame, GameVersion = pcall(require, "src.core.GameVersion")
  local edition = okGame and type(GameVersion) == "table"
    and type(GameVersion.get) == "function" and GameVersion.get() == "leafgreen"
    and "leafgreen" or "firered"
  for _, name in ipairs({ "dialogue", "dialogue_" .. edition }) do
    for key, ir in pairs(catalog(name)) do
      if type(ir) == "table" and #ir > 0 then mod.content.text:override(key, ir) end
    end
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

GATE_REPORT_NAME = "gate_report.json"


def frlg_mod_id(language: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3"


def frlg_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3-{version}.zip"


def split_frlg_dialogue(
    firered: Mapping[str, list[dict]], leafgreen: Mapping[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    """The dialogue both editions share, then FireRed's own, then LeafGreen's.

    Script text is keyed by ROM address and the two carts lay it out
    differently -- the few addresses both use hold different lines -- so an
    address key always belongs to its edition.  A named text is shared when
    both editions ship the same IR under its label; the naming screen's
    choices (RED/FIRE against GREEN/LEAF) are the ones that are not.
    """
    shared = {key: ir for key, ir in firered.items()
              if text_key_address(key) is None and leafgreen.get(key) == ir}
    return (shared,
            {key: ir for key, ir in firered.items() if key not in shared},
            {key: ir for key, ir in leafgreen.items() if key not in shared})


def generate_frlg_mod(
    destination: str | Path,
    *,
    language: str,
    target_name: str,
    dialogue: Mapping[str, list[dict]],
    catalogs: Mapping[str, Mapping[str, str]],
    mod_id: str | None = None,
    leafgreen_dialogue: Mapping[str, list[dict]] | None = None,
    species_metrics: Mapping[str, tuple[float, float]] | None = None,
) -> Path:
    """Write a deterministic manifest, entry point and catalogs.

    ``dialogue`` is FireRed's; with ``leafgreen_dialogue`` the mod covers both
    editions, its dialogue split by split_frlg_dialogue().  ``species_metrics``
    is every species' height and weight in metres and kilograms
    (pipeline.shared.pokedex_metrics).
    """
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    unknown = set(catalogs) - set(CATALOG_HOOKS) - {"start_menu"}
    if unknown:
        raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")
    lang_dir = destination / "lang"
    if lang_dir.exists():
        shutil.rmtree(lang_dir)
    lang_dir.mkdir(parents=True)

    games = ["firered"]
    layers = {"dialogue": dialogue}
    if leafgreen_dialogue is not None:
        games.append("leafgreen")
        shared, firered, leafgreen = split_frlg_dialogue(dialogue, leafgreen_dialogue)
        layers = {"dialogue": shared, "dialogue_firered": firered, "dialogue_leafgreen": leafgreen}
    for name, entries in layers.items():
        lines = [f"-- Generated by the FireRed pipeline ({language}): {name}", "return {"]
        lines.extend(f"  [{lua_string(key)}] = {lua_ir(ir)}," for key, ir in sorted(entries.items()))
        lines.append("}")
        (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")

    registration = ""
    start_menu = catalogs.get("start_menu") or {}
    if start_menu:
        lines = [f"-- Generated by the FireRed pipeline ({language}): start_menu", "return {"]
        lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(start_menu.items()))
        lines.append("}")
        (lang_dir / "start_menu.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name in CATALOG_HOOKS:
        values = catalogs.get(name) or {}
        if not values:
            continue
        lines = [f"-- Generated by the FireRed pipeline ({language}): {name}", "return {"]
        lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items()))
        lines.append("}")
        (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        registration += f'  each("{name}", function(id, value) {CATALOG_HOOKS[name]} end)\n'
    if species_metrics:
        (lang_dir / "species_metrics.lua").write_text(
            lua_catalog(species_metrics, "species_metrics"), encoding="utf-8")
        registration += GEN3_SPECIES_METRICS_HOOK
    main = MAIN.replace("__CATALOG_REGISTRATION__", registration).replace(
        "__START_MENU_REGISTRATION__", _START_MENU_REGISTRATION if start_menu else "")
    (destination / "main.lua").write_text(main, encoding="utf-8")

    manifest = {
        "id": mod_id or frlg_mod_id(language), "name": target_name, "version": project_version(),
        "api": 2, "entry": "main.lua", "profile": "content", "games": games,
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

# pret's symbol table for each edition, in [pret.symbols.archive_files].
FRLG_SYMBOL_FILES: Mapping[str, str] = {"firered": "pokefirered.sym", "leafgreen": "pokeleafgreen.sym"}


def prepare_pret_inputs(workspace: str | Path, config: Mapping) -> tuple[dict[str, Path], Path]:
    """Download the pinned pret symbol tables (one per edition) and charmap."""
    root = Path(workspace) / "dependencies" / "pret"
    pret = config.get("pret") or {}
    folders = {}
    for name in ("symbols", "charmap"):
        section = pret.get(name) or {}
        try:
            folders[name] = fetch_files(
                str(section["archive_base_url"]), dict(section["archive_files"]),
                root / name, revision=str(section.get("revision", "")),
            )
        except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
            raise BuildError(f"Unable to download pinned pret {name}: {error}") from error
    symbols = {edition: folders["symbols"] / filename for edition, filename in FRLG_SYMBOL_FILES.items()}
    return symbols, folders["charmap"] / "charmap.txt"


def _numbered(path: Path) -> dict[int, object]:
    return {int(key): value for key, value in json.loads(path.read_text(encoding="utf-8")).items()}


# Items left out of the join and of the coverage: ITEM_POKEBLOCK_CASE cannot be
# obtained in FireRed, and the items extractor drops its POKEBLOCK glyph run, so
# even English reads " CASE" (docs/upstream-fixes.md, FireRed entry 9).
UNOBTAINABLE_ITEMS = frozenset({273})


# ---------------------------------------------------------------- join

def join_frlg(
    extracted: str | Path,
    corpus_dir: str | Path,
    language: str,
    symbols_path: str | Path,
    charmap_path: str | Path,
    gen1recomp: str | Path | None = None,
    luajit: str | None = None,
    edition: str = "firered",
) -> dict:
    """Run every join of one edition for one language; return catalogs and stats."""
    extracted = Path(extracted)
    language = canonical_language(language)
    charmap = load_charmap(charmap_path)
    symbols = load_symbols(symbols_path)
    corpus = load_gen3_corpus(corpus_dir, language, FRLG)

    text = json.loads((extracted / "frlg_text.json").read_text(encoding="utf-8"))
    entries, dialogue_stats = join_gen3_dialogue(
        text, corpus, symbols, charmap,
        overrides=load_dialogue_overrides(language, FRLG),
        decisions=load_dialogue_decisions(FRLG, edition=edition),
    )

    species = _numbered(extracted / "frlg_species.json")
    moves = _numbered(extracted / "frlg_moves.json")
    items = {number: row for number, row in _numbered(extracted / "frlg_items.json").items()
             if number not in UNOBTAINABLE_ITEMS}
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
        "trainer_class_names": join_trainer_class_names(trainers, trainer_ids, corpus, charmap),
        "start_menu": join_start_menu(corpus, charmap),
    }
    scope = load_engine_scope(FRLG)
    strings, engine_stats = join_gen3_engine_strings(scope, corpus, charmap)
    by_english: dict[str, str] = {}
    if gen1recomp is not None:
        strings, by_english = rom_label_strings(strings, gen1recomp, scope, luajit, ENGLISH_LOOKUP_SITES,
                                                 dialogue_label_rows(entries))
    catalogs = {name: result.values for name, result in results.items()}
    catalogs["strings"] = strings
    if by_english:
        catalogs["strings_by_english"] = by_english
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


# ---------------------------------------------------------------- ROM labels

# The three places where the runtime reads a cart string by its English text
# and by nothing else: the label it also has is never looked up for them, so
# an entry moved onto that label would simply stop being found.  Checked at
# the pinned revision, and recorded as upstream entries 14 and 15:
#   src/ui/game3/region_map.lua:384, :404  Strings(SECTION_NAMES[...]),
#       Strings(desc) -- the town names the map name popup prints too
#   src/ui/game3/summary_menu.lua:652      Strings(tostring(ability)), whose
#       name comes from src/core/game3/battle/abilities.lua's table
#   src/ui/game3/map_name_popup.lua:83     Strings(label) for the floor
# Every other value this catalog carries is drawn from the cart's own rows
# (RomText), so its label is the key the runtime reads.
ENGLISH_LOOKUP_SITES = (
    "src/core/game3/battle/abilities.lua",
    "src/ui/game3/map_name_popup.lua",
    "src/ui/game3/region_map.lua",
)


# ---------------------------------------------------------------- gate

def _sample(values: Mapping[str, str], prefer: str | None = None) -> tuple[str, str] | None:
    if not values:
        return None
    key = prefer if prefer in values else sorted(values)[0]
    return key, values[key]


def write_gate_expectations(path: Path, joined: dict, *,
                            dialogue_keys: frozenset[str] | set[str] | None = None,
                            edition_guard: Mapping | None = None) -> dict:
    """One sample per shipped catalog, read back by tools/frlg/gate.lua.

    ``dialogue_keys`` limits the dialogue sample to an edition's own layer;
    ``edition_guard`` is a ``{key, ir}`` the other edition ships at the same
    address with other text, which the gate expects this edition to keep.
    """
    catalogs = joined["catalogs"]
    numbers = joined["numbers"]
    expectations: dict[str, dict] = {}
    if edition_guard:
        expectations["edition_guard"] = dict(edition_guard)

    for entry in joined["entries"]:
        if dialogue_keys is not None and entry.key not in dialogue_keys:
            continue
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
    # The samples the gate must find: one for every catalog that has rows.
    expectations["required"] = sorted(
        name for name in ("species_names", "move_names", "item_names", "item_descriptions",
                          "trainer_names", "trainer_class_names", "start_menu", "strings")
        if catalogs.get(name))
    if any(entry.status in SHIPPED and entry.translation and (dialogue_keys is None or entry.key in dialogue_keys)
           and any(segment.get("t") == "player" for segment in entry.translation)
           for entry in joined["entries"]):
        expectations["required"].append("dialogue")
    path.write_text(json.dumps(expectations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expectations


def run_frlg_gate(
    mod_dir: Path, extracted: Path, joined: dict, gen1recomp: Path, luajit: str,
    *, edition: str = "firered", dialogue_keys: frozenset[str] | set[str] | None = None,
    edition_guard: Mapping | None = None, log_fn: Callable[[str], None] | None = None,
) -> dict:
    suffix = "" if edition == "firered" else f".{edition}"
    expectation_path = mod_dir.parent / f".{mod_dir.name}{suffix}.gate.json"
    report_path = mod_dir.parent / f".{mod_dir.name}{suffix}.{GATE_REPORT_NAME}"
    write_gate_expectations(expectation_path, joined, dialogue_keys=dialogue_keys,
                            edition_guard=edition_guard)
    script = resource_root() / "tools" / "frlg" / "gate.lua"
    try:
        _run([luajit, str(script), str(gen1recomp), str(extracted / "cache"), str(mod_dir),
              str(expectation_path), str(report_path), edition], cwd=gen1recomp, log_fn=log_fn)
        return json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        expectation_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- coverage

def write_frlg_report(path: Path, joined: dict, coverage: dict, gate: dict, *,
                      leafgreen: dict | None = None, leafgreen_gate: dict | None = None) -> None:
    """Private per-key report (it quotes ROM text: never packaged)."""
    report = {
        "coverage": coverage,
        "dialogue_unresolved": unresolved_entries(joined),
        "catalog_issues": joined["catalog_issues"],
        "engine_details": joined["engine_stats"]["details"],
        "gate": gate,
    }
    if leafgreen is not None:
        report["leafgreen_dialogue_unresolved"] = unresolved_entries(leafgreen)
        report["leafgreen_gate"] = leafgreen_gate
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# The catalogs a LeafGreen join is compared on: the engine strings are keyed
# by ROM label only in the FireRed join (rom_label_strings), which is the one
# the mod ships.
_EDITION_CATALOGS = ("species_names", "move_names", "item_names", "item_descriptions",
                     "trainer_names", "trainer_class_names", "start_menu")


def check_shared_catalogs(firered: dict, leafgreen: dict) -> None:
    """One mod ships one set of name catalogs: both carts must agree on it."""
    differ = [name for name in _EDITION_CATALOGS
              if firered["catalogs"].get(name) != leafgreen["catalogs"].get(name)]
    if differ:
        raise BuildError(
            "FireRed and LeafGreen translate these catalogs differently, and the mod "
            "ships one set for both: " + ", ".join(differ)
        )


def edition_guards(firered: Mapping[str, list[dict]],
                   leafgreen: Mapping[str, list[dict]]) -> dict[str, dict]:
    """An address both editions use for different text, with each one's IR.

    The gate loads the mod under each edition and expects that edition's
    line there: the other edition's layer must not reach it.
    """
    for key in sorted(set(firered) & set(leafgreen)):
        if text_key_address(key) is not None and firered[key] != leafgreen[key]:
            return {"firered": {"key": key, "ir": firered[key]},
                    "leafgreen": {"key": key, "ir": leafgreen[key]}}
    return {}


# ---------------------------------------------------------------- build

def build_frlg(
    firered_rom: str | Path,
    leafgreen_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> Path:
    """Extract both editions, join, gate and package the generation-3 mod."""

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
    verify_leafgreen_rom(leafgreen_rom)

    from ..shared.orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language, font_profile=None,
    )
    workspace, destination, gen1recomp = context.workspace, context.destination, context.gen1recomp
    corpus_dir = context.corpus / "corpus" / spec.corpus_collection
    status("Preparing dependencies")
    symbols, charmap = prepare_pret_inputs(workspace, project_config())

    extracted: dict[str, Path] = {}
    for edition, rom, label in (("firered", firered_rom, "FireRed"), ("leafgreen", leafgreen_rom, "LeafGreen")):
        log(f"\nExtracting private {label} ROM data...")
        status(f"Extracting private {label} ROM data")
        extracted[edition] = workspace / edition / "extracted"
        import_frlg_rom(rom, gen1recomp, extracted[edition], log_fn=log_fn, edition=edition)

    with rom_text_cache(gen1recomp, extracted["firered"]):
        log("\nJoining corpus and generating the mod...")
        status("Joining corpus and generating the mod")
        joined = join_frlg(extracted["firered"], corpus_dir, language, symbols["firered"], charmap,
                           gen1recomp, luajit)
        leafgreen = join_frlg(extracted["leafgreen"], corpus_dir, language, symbols["leafgreen"], charmap,
                              edition="leafgreen")
        check_shared_catalogs(joined, leafgreen)
        build_root = workspace / "interactive-gen3" / language
        mod_dir = build_root / frlg_mod_id(language)
        generate_frlg_mod(
            mod_dir, language=language, target_name=f"{language_name} translation for FireRed and LeafGreen",
            dialogue=joined["dialogue"], leafgreen_dialogue=leafgreen["dialogue"], catalogs=joined["catalogs"],
            species_metrics=species_metrics(prepare_pokedex_metrics(workspace, project_config()),
                                            joined["numbers"]["species"].values()),
        )
        coverage = gen3_coverage(joined)
        coverage["rom_leafgreen"] = gen3_coverage(leafgreen)["rom"]

        _shared, firered_layer, leafgreen_layer = split_frlg_dialogue(joined["dialogue"], leafgreen["dialogue"])
        guards = edition_guards(firered_layer, leafgreen_layer)
        status("Running FireRed release gate")
        gate = run_frlg_gate(mod_dir, extracted["firered"], joined, gen1recomp, luajit,
                             dialogue_keys=set(firered_layer), edition_guard=guards.get("firered"),
                             log_fn=log_fn)
        status("Running LeafGreen release gate")
        leafgreen_gate = run_frlg_gate(mod_dir, extracted["leafgreen"], leafgreen, gen1recomp, luajit,
                                       edition="leafgreen", dialogue_keys=set(leafgreen_layer),
                                       edition_guard=guards.get("leafgreen"), log_fn=log_fn)
        attach_frlg_validation(mod_dir, {
            "schema": 1, "policy": "english-fallback", "coverage": coverage,
            "runtime_limits": {
                "blank_glyphs": gate.get("blank_glyphs", {}).get("total", 0),
                "names_survive_field_entry": all(
                    row.get("survives") for row in (gate.get("persistence") or {}).values()),
                "strings_resolve_in_game": bool((gate.get("strings_live") or {}).get("resolves")),
            },
        })
        write_frlg_report(build_root / "coverage.json", joined, coverage, gate,
                          leafgreen=leafgreen, leafgreen_gate=leafgreen_gate)
        for key, label in (("rom", "FireRed ROM aggregate"), ("rom_leafgreen", "LeafGreen ROM aggregate"),
                           ("engine_gen3", "FireRed engine strings")):
            section = coverage[key]
            log(f"  {label}: {section['translated']}/{section['total']} ({section['percent']:.2f}%)")
        blank = gate.get("blank_glyphs", {})
        if blank.get("total"):
            log(f"  runtime limit: {blank['total']} shipped characters have no glyph in FrlgFont yet"
                " (docs/upstream-fixes.md, FireRed)")

        status("Packaging translation mod")
        published = package_gen3_mod(mod_dir, gen1recomp, build_root, destination,
                                     frlg_archive_name(language, project_version()), luajit, log_fn)
    status("Build complete")
    return published
