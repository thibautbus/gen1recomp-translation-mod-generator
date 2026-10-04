"""Emerald (generation 3) translation mod: join, generation, release gate,
build.

Emerald runs on the same game3 runtime as FireRed and goes through the same
joins (pipeline.gen3), with its own family (pipeline.gen3.family.EMERALD):
the Emerald PokeCorpus collection, pret's pokeemerald symbol table and
charmap, the runtime's ``rse`` text dialect, and its own reviewed
configuration under ``config/rse/`` and ``overrides/<language>/rse/``.  The
mod ships:

* ``text``: every dialogue and named-text key, overridden with its IR
  segment list (the Emerald extract keys script text by ROM address and the
  cart's own tables by label; a pointer table's slot is joined through the
  symbol its pointer reaches);
* ``pokemon``, ``moves``, ``items`` and ``trainers`` name, description and
  class-name patches, keyed as ``G3.idOf`` and ``G3.trainerIds`` key them;
* ``strings`` for the game3 interface strings that go through ``Strings()``
  (config/rse/engine_scope.json): Emerald's corpus rows first, then the
  reviewed overrides of this family and of FireRed for the port-added rows
  both runtimes share.

It is a mod of its own, apart from the FireRed/LeafGreen one: an Emerald
build needs only the Emerald ROM.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable, Mapping

from ..shared.builder import BuildError, _run
from ..shared.corpus import canonical_language
from ..shared.dependencies import DependencyError, fetch_files
from ..gen3.family import EMERALD
from ..gen3.join import (
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
from ..gen3.text import load_charmap, load_symbols
from .european import apply_european_trainer_text
from ..shared.generate import lua_string
from ..shared.mod_assets import TRANSLATION_MOD_PRIORITY
from ..shared.project import project_config, project_version, resource_root
from ..shared.roms import import_rse_rom, verify_emerald_rom
from ..shared.specs import game_spec, languages_for_collection, release_profile

MAIN = '''-- Generated Emerald translation mod.
local function dialogueFile(part)
  return part == 1 and "dialogue" or ("dialogue_" .. part)
end

return function(mod)
  -- A catalog this build wrote and the loader cannot read is an error, not
  -- an empty catalog: the release gate must see it.
  local function catalog(name)
    local body = mod:read("lang/" .. name .. ".lua")
    if not body then return {} end
    local value = assert(loadstring(body, "lang/" .. name .. ".lua"))()
    assert(type(value) == "table", "lang/" .. name .. ".lua is not a table")
    return value
  end
  -- Dialogue values are text IR segment lists (src/core/game3/scripting/
  -- text_ir.lua, Emerald's dialect), keyed by the extractor's ROM-pointer
  -- text keys and the cart's own table labels, in as many files as LuaJIT's
  -- per-chunk constant limit needs (dialogue, dialogue_2, ...).
  local part = 1
  while mod:read("lang/" .. dialogueFile(part) .. ".lua") do
    for key, ir in pairs(catalog(dialogueFile(part))) do
      if type(ir) == "table" and #ir > 0 then mod.content.text:override(key, ir) end
    end
    part = part + 1
  end
  local function each(name, apply)
    for id, value in pairs(catalog(name)) do
      if type(value) == "string" and value ~= "" then apply(id, value) end
    end
  end
__CATALOG_REGISTRATION__end
'''

GATE_REPORT_NAME = "gate_report.json"

# Dialogue entries per file: one Lua chunk holds at most 65,536 constants
# (LuaJIT), and Emerald's 15,000 IR lists are far more than that.
DIALOGUE_FILE_ENTRIES = 1500


def dialogue_files(dialogue: Mapping[str, list[dict]]) -> dict[str, dict[str, list[dict]]]:
    """The dialogue split into the files main.lua reads, in key order."""
    keys = sorted(dialogue)
    files: dict[str, dict[str, list[dict]]] = {}
    for start in range(0, max(len(keys), 1), DIALOGUE_FILE_ENTRIES):
        part = start // DIALOGUE_FILE_ENTRIES + 1
        name = "dialogue" if part == 1 else f"dialogue_{part}"
        files[name] = {key: dialogue[key] for key in keys[start:start + DIALOGUE_FILE_ENTRIES]}
    return files

# The places where the Emerald runtime reads a string by its English text and
# by nothing else, once gen1recomp's Emerald display hooks route them through
# Strings(): the ability names, the summary's descriptions and contest texts,
# the map sections and the Pokédex entries.  Their entries are kept under
# their English (rom_label_strings) in a file of their own.
ENGLISH_LOOKUP_SITES = (
    "src/core/game3/battle/abilities.lua",
    "src/core/game3/summary_data.lua",
    "src/ui/game3/rse/mapsec.lua",
    "src/ui/game3/rse/pokedex.lua",
)

# pret's symbol table and charmap for Emerald, in [pret.emerald_symbols] and
# [pret.emerald_charmap].
EMERALD_SYMBOL_FILE = "pokeemerald.sym"


def rse_mod_id(language: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3-emerald"


def rse_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3-emerald-{version}.zip"


def generate_rse_mod(
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
    unknown = set(catalogs) - set(CATALOG_HOOKS)
    if unknown:
        raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")
    lang_dir = destination / "lang"
    if lang_dir.exists():
        shutil.rmtree(lang_dir)
    lang_dir.mkdir(parents=True)

    for name, entries in dialogue_files(dialogue).items():
        lines = [f"-- Generated by the Emerald pipeline ({language}): {name}", "return {"]
        lines.extend(f"  [{lua_string(key)}] = {lua_ir(ir)}," for key, ir in sorted(entries.items()))
        lines.append("}")
        (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")

    registration = ""
    for name in CATALOG_HOOKS:
        values = catalogs.get(name) or {}
        if not values:
            continue
        lines = [f"-- Generated by the Emerald pipeline ({language}): {name}", "return {"]
        lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items()))
        lines.append("}")
        (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        registration += f'  each("{name}", function(id, value) {CATALOG_HOOKS[name]} end)\n'
    (destination / "main.lua").write_text(MAIN.replace("__CATALOG_REGISTRATION__", registration),
                                          encoding="utf-8")

    manifest = {
        "id": mod_id or rse_mod_id(language), "name": target_name, "version": project_version(),
        "api": 2, "entry": "main.lua", "profile": "content", "games": list(EMERALD.editions),
        "game_version": ">=0.0.0-dev <1.0.0", "category": "LANGUAGE",
        "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [],
        "conflicts": [], "permissions": [],
        "description": f"{target_name}, based mostly on PokeCorpus.",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def attach_rse_validation(mod_dir: str | Path, validation: dict) -> None:
    path = Path(mod_dir) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["validation"] = validation
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- inputs

def prepare_emerald_pret_inputs(workspace: str | Path, config: Mapping) -> tuple[Path, Path]:
    """Download the pinned pret Emerald symbol table and charmap."""
    root = Path(workspace) / "dependencies" / "pret"
    pret = config.get("pret") or {}
    folders = {}
    for name in ("emerald_symbols", "emerald_charmap"):
        section = pret.get(name) or {}
        try:
            folders[name] = fetch_files(
                str(section["archive_base_url"]), dict(section["archive_files"]),
                root / name, revision=str(section.get("revision", "")),
            )
        except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
            raise BuildError(f"Unable to download pinned pret {name}: {error}") from error
    return folders["emerald_symbols"] / EMERALD_SYMBOL_FILE, folders["emerald_charmap"] / "charmap.txt"


def _numbered(path: Path) -> dict[int, object]:
    return {int(key): value for key, value in json.loads(path.read_text(encoding="utf-8")).items()}


def text_aliases(extracted: Path, symbols: Mapping[int, list[str]]) -> dict[str, tuple[str, ...]]:
    """The pret symbols each pointer-table key reaches (rse_text_pointers.json)."""
    pointers = json.loads((Path(extracted) / "rse_text_pointers.json").read_text(encoding="utf-8"))
    return {key: tuple(symbols.get(int(address), ())) for key, address in pointers.items()
            if symbols.get(int(address))}


def _description_qids(items: Mapping[int, Mapping], symbols: Mapping[int, list[str]],
                      corpus) -> dict[int, list[str]]:
    """Each item's own description rows, named after the string its
    description pointer reaches (sMasterBallDesc)."""
    out: dict[int, list[str]] = {}
    for number, row in items.items():
        pointer = row.get("description_pointer") if isinstance(row, Mapping) else None
        if not isinstance(pointer, int):
            continue
        qids = [corpus.qids[index] for label in symbols.get(pointer, ())
                for index in corpus.by_label.get(label, ())
                if corpus.qids[index].startswith(f"{EMERALD.qid_prefix}common.item_descriptions.")]
        if qids:
            out[number] = qids
    return out


# ---------------------------------------------------------------- join

def join_rse(
    extracted: str | Path,
    corpus_dir: str | Path,
    language: str,
    symbols_path: str | Path,
    charmap_path: str | Path,
    gen1recomp: str | Path | None = None,
    luajit: str | None = None,
) -> dict:
    """Run every Emerald join for one language; return catalogs and stats."""
    extracted = Path(extracted)
    language = canonical_language(language)
    charmap = load_charmap(charmap_path, EMERALD.dialect)
    symbols = load_symbols(symbols_path)
    corpus = load_gen3_corpus(corpus_dir, language, EMERALD)

    text = json.loads((extracted / "rse_text.json").read_text(encoding="utf-8"))
    entries, dialogue_stats = join_gen3_dialogue(
        text, corpus, symbols, charmap,
        overrides=load_dialogue_overrides(language, EMERALD),
        decisions=load_dialogue_decisions(EMERALD),
        aliases=text_aliases(extracted, symbols),
    )

    species = _numbered(extracted / "rse_species.json")
    moves = _numbered(extracted / "rse_moves.json")
    items = _numbered(extracted / "rse_items.json")
    trainers = _numbered(extracted / "rse_trainers.json")

    species_ids = registry_ids(species)
    item_names = {number: row.get("name") for number, row in items.items()}
    item_ids = registry_ids(item_names)
    trainer_ids = {number: str(number) for number in trainers}
    prefix = f"{EMERALD.qid_prefix}common."
    results = {
        "species_names": join_indexed_catalog(
            species, species_ids, corpus, prefix + "species_names.gSpeciesNames.", charmap),
        "move_names": join_indexed_catalog(
            moves, registry_ids(moves), corpus, prefix + "move_names.gMoveNames.", charmap),
        "item_names": join_indexed_catalog(item_names, item_ids, corpus, prefix + "items.gItems.", charmap),
        "item_descriptions": join_item_descriptions(
            items, item_ids, corpus, charmap, own_qids=_description_qids(items, symbols, corpus)),
        "trainer_names": join_indexed_catalog(
            {number: row.get("name") for number, row in trainers.items()}, trainer_ids, corpus,
            prefix + "trainers.gTrainers.", charmap),
        "trainer_class_names": join_trainer_class_names(trainers, trainer_ids, corpus, charmap),
    }
    european = apply_european_trainer_text(results["trainer_names"].values, results["trainer_class_names"].values,
                                           trainers, trainer_ids, corpus, charmap)
    scope = load_engine_scope(EMERALD)
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
        "european_trainer_text": dict(european),
        "engine_stats": engine_stats,
        "numbers": {"species": species_ids, "items": item_ids, "moves": registry_ids(moves)},
        "scope": scope,
    }


# ---------------------------------------------------------------- gate

def _sample(values: Mapping[str, str], prefer: str | None = None) -> tuple[str, str] | None:
    if not values:
        return None
    key = prefer if prefer in values else sorted(values)[0]
    return key, values[key]


def _plain_text(ir: list[dict]) -> str | None:
    """The text of an IR that is nothing but text."""
    if any(segment.get("t") not in {"text", "eos"} for segment in ir):
        return None
    return "".join(segment.get("s", "") for segment in ir)


def write_gate_expectations(path: Path, joined: dict) -> dict:
    """One sample per shipped catalog and Emerald consumer, read back by
    tools/rse/gate.lua."""
    catalogs = joined["catalogs"]
    numbers = joined["numbers"]
    dialogue = joined["dialogue"]
    expectations: dict[str, object] = {}

    for key in sorted(dialogue):
        ir = dialogue[key]
        if key.startswith("g3:") and any(segment.get("t") == "player" for segment in ir):
            probe = max((segment.get("s", "") for segment in ir if segment.get("t") == "text"), key=len)
            expectations["dialogue"] = {"key": key, "ir": ir, "probe": probe.strip()}
            break
    # the battle string table, read back by STRINGID through BattleText.get
    for key in sorted(dialogue):
        value = _plain_text(dialogue[key]) if key.startswith("STRINGID_") else None
        if value:
            expectations["battle"] = {"key": key, "value": value}
            break
    natures = []
    for index in range(25):
        value = _plain_text(dialogue.get(f"gNatureNamePointers[{index}]", []))
        if value:
            natures.append({"id": index, "value": value})
    if natures:
        expectations["natures"] = natures[:3]

    def number_of(kind: str, id_: str) -> int:
        return next(number for number, value in numbers[kind].items() if value == id_)

    sample = _sample(catalogs.get("species_names", {}), "TREECKO")
    if sample:
        expectations["species_names"] = {"id": sample[0], "number": number_of("species", sample[0]),
                                         "value": sample[1]}
    sample = _sample(catalogs.get("move_names", {}), "POUND")
    if sample:
        expectations["move_names"] = {"id": sample[0], "number": number_of("moves", sample[0]), "value": sample[1]}
    for name in ("item_names", "item_descriptions"):
        sample = _sample(catalogs.get(name, {}), "POTION")
        if sample:
            expectations[name] = {"id": sample[0], "number": number_of("items", sample[0]), "value": sample[1]}
    for name in ("trainer_names", "trainer_class_names"):
        sample = _sample(catalogs.get(name, {}))
        if sample:
            expectations[name] = {"id": sample[0], "value": sample[1]}
    strings = catalogs.get("strings", {})
    sample = _sample(strings, "YES")
    if sample:
        expectations["strings"] = {"id": sample[0], "value": sample[1]}
    # Easy Chat: a word under its label (easyChat.word[<id>], which the
    # ROM-label migration keys it by), or else as Strings(word, "easyChat.<group>")
    for key, value in sorted(strings.items()):
        label = re.fullmatch(r"easyChat\.word\[(\d+)\]", key)
        if label:
            expectations["easy_chat"] = {"id": int(label.group(1)), "value": value}
            break
        if key.startswith("easyChat.") and not key.startswith("easyChat.group|") and "|" in key:
            group, word = key[len("easyChat."):].split("|", 1)
            expectations["easy_chat"] = {"group": group, "word": word, "value": value}
            break
    # The display hooks gen1recomp's Emerald screens gain upstream: measured,
    # not failed, until the pinned engine carries them.
    by_english = catalogs.get("strings_by_english", {})
    english = {**strings, **by_english}
    hooks = {}
    for site, label in (("src/core/game3/battle/abilities.lua", "ability_name"),
                        ("src/ui/game3/rse/pokedex.lua", "pokedex"),
                        ("src/core/game3/summary_data.lua (SummaryData.contest", "contest"),
                        ("src/ui/game3/rse/mapsec.lua", "map_section")):
        for key, row in sorted(joined.get("scope", {}).items()):
            if (str(row.get("callsite", "")).startswith(site) and "{" not in key and key == key.strip()
                    and key in english and english[key] != key):
                hooks[label] = {"source": key, "value": english[key]}
                break
    if hooks:
        expectations["hooks"] = hooks
    # The samples the gate must find: one for every catalog that has rows.
    sources = {
        "dialogue": any(key.startswith("g3:") for key in dialogue),
        "battle": any(key.startswith("STRINGID_") for key in dialogue),
        "natures": any(key.startswith("gNatureNamePointers[") for key in dialogue),
        "strings": bool(strings),
        "easy_chat": any(key.startswith("easyChat.") and not key.startswith("easyChat.group")
                         for key in strings),
    }
    for name in ("species_names", "move_names", "item_names", "item_descriptions",
                 "trainer_names", "trainer_class_names"):
        sources[name] = bool(catalogs.get(name))
    expectations["required"] = sorted(name for name, present in sources.items() if present)
    path.write_text(json.dumps(expectations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expectations


def run_rse_gate(mod_dir: Path, extracted: Path, joined: dict, gen1recomp: Path, luajit: str,
                 *, log_fn: Callable[[str], None] | None = None) -> dict:
    expectation_path = mod_dir.parent / f".{mod_dir.name}.gate.json"
    report_path = mod_dir.parent / f".{mod_dir.name}.{GATE_REPORT_NAME}"
    write_gate_expectations(expectation_path, joined)
    script = resource_root() / "tools" / "rse" / "gate.lua"
    try:
        _run([luajit, str(script), str(gen1recomp), str(extracted / "cache"), str(mod_dir),
              str(expectation_path), str(report_path)], cwd=gen1recomp, log_fn=log_fn)
        return json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        expectation_path.unlink(missing_ok=True)


def write_rse_report(path: Path, joined: dict, coverage: dict, gate: dict) -> None:
    """Private per-key report (it quotes ROM text: never packaged)."""
    report = {
        "coverage": coverage,
        "dialogue_unresolved": unresolved_entries(joined),
        "catalog_issues": joined["catalog_issues"],
        "engine_details": joined["engine_stats"]["details"],
        "gate": gate,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- build

def build_rse(
    emerald_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> Path:
    """Extract, join, gate and package the Emerald mod."""

    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    profile = release_profile("rse")
    spec = game_spec("emerald")
    supported = {code for code, _name in languages_for_collection(spec.corpus_collection)}
    if language not in supported:
        raise BuildError(f"Emerald has no {language} release.")
    status("Validating ROM")
    verify_emerald_rom(emerald_rom)

    from ..shared.orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language, font_profile=None,
    )
    workspace, destination, gen1recomp = context.workspace, context.destination, context.gen1recomp
    corpus_dir = context.corpus / "corpus" / spec.corpus_collection
    status("Preparing dependencies")
    symbols, charmap = prepare_emerald_pret_inputs(workspace, project_config())

    log("\nExtracting private Emerald ROM data...")
    status("Extracting private Emerald ROM data")
    extracted = workspace / "emerald" / "extracted"
    import_rse_rom(emerald_rom, gen1recomp, extracted, log_fn=log_fn)

    with rom_text_cache(gen1recomp, extracted, "emerald"):
        log("\nJoining corpus and generating the mod...")
        status("Joining corpus and generating the mod")
        joined = join_rse(extracted, corpus_dir, language, symbols, charmap, gen1recomp, luajit)
        build_root = workspace / "interactive-rse" / language
        mod_dir = build_root / rse_mod_id(language)
        generate_rse_mod(mod_dir, language=language, target_name=f"{language_name} translation for Emerald",
                         dialogue=joined["dialogue"], catalogs=joined["catalogs"])
        coverage = gen3_coverage(joined)
        status("Running Emerald release gate")
        gate = run_rse_gate(mod_dir, extracted, joined, gen1recomp, luajit, log_fn=log_fn)
        attach_rse_validation(mod_dir, {
            "schema": 1, "policy": "english-fallback", "coverage": coverage,
            "runtime_limits": {
                "blank_glyphs": gate.get("blank_glyphs", {}).get("total", 0),
                "strings_resolve_in_game": bool((gate.get("strings_live") or {}).get("resolves")),
                "display_hooks": gate.get("hooks", {}),
            },
        })
        write_rse_report(build_root / "coverage.json", joined, coverage, gate)
        for key, label in (("rom", "Emerald ROM aggregate"), ("engine_gen3", "Emerald engine strings")):
            section = coverage[key]
            log(f"  {label}: {section['translated']}/{section['total']} ({section['percent']:.2f}%)")
        blank = gate.get("blank_glyphs", {})
        if blank.get("total"):
            log(f"  runtime limit: {blank['total']} shipped characters have no glyph in FrlgFont yet"
                " (docs/upstream-fixes.md, Emerald)")

        status("Packaging translation mod")
        published = package_gen3_mod(mod_dir, gen1recomp, build_root, destination,
                                     rse_archive_name(language, project_version()), luajit, log_fn)
    status("Build complete")
    return published
