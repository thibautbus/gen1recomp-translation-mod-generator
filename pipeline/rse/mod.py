"""The rse release: one translation mod for Ruby, Sapphire and Emerald.

The three Hoenn games run on gen1recomp's game3 runtime, and one mod covers
them all, the way one covers Gold, Silver and Crystal (pipeline/gsc) or Red,
Blue and Yellow (pipeline/rby).  Ruby and Sapphire are the release's base
game (pipeline/rse/join.py), Emerald its companion edition
(pipeline/rse/emerald.py); a build reads the Emerald ROM and one Ruby or
Sapphire ROM, of any English revision.

The mod ships one layer per game, which main.lua picks by ``GameVersion``:

* ``lang/emerald/``: Emerald's text, catalogs and engine strings;
* ``lang/rs/``: the catalogs and engine strings Ruby and Sapphire share, and
  ``layouts.lua``, the guards that tell revision 1.0 from 1.1/1.2;
* ``lang/ruby/``, ``lang/sapphire/``: each edition's named text (labels,
  pointer tables, battle strings);
* ``lang/ruby_1_0/``, ``lang/ruby_1_1/``, ``lang/sapphire_1_0/``,
  ``lang/sapphire_1_1/``: the script text, keyed by ROM address, of each
  edition's two text layouts.

In each layer, ``text`` overrides every dialogue key with its IR segment
list; ``pokemon``, ``moves``, ``items`` and ``trainers`` take name,
description and class-name patches keyed as ``G3.idOf`` and
``G3.trainerIds`` key them; ``strings`` holds the game3 interface strings
that go through ``Strings()``.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable, Mapping

from ..gen3.mod import (
    CATALOG_HOOKS,
    gen3_coverage,
    lua_ir,
    package_gen3_mod,
    rom_text_cache,
    unresolved_entries,
)
from ..shared.builder import BuildError, _run
from ..shared.corpus import canonical_language
from ..shared.generate import lua_string
from ..shared.mod_assets import TRANSLATION_MOD_PRIORITY
from ..shared.project import project_config, project_version, resource_root
from ..shared.roms import (
    RS_EDITIONS, import_rse_rom, rs_revision_for_sha1, verify_emerald_rom, verify_rs_rom,
)
from ..shared.specs import game_spec, languages_for_collection, release_profile
from .emerald import join_emerald, prepare_emerald_pret_inputs
from .join import join_rs, layer_name, prepare_rs_pret_inputs

GAMES = ("ruby", "sapphire", "emerald")

MAIN = '''-- Generated Ruby, Sapphire and Emerald translation mod.
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
  local okGame, GameVersion = pcall(require, "src.core.GameVersion")
  local game = okGame and type(GameVersion) == "table" and type(GameVersion.get) == "function"
    and GameVersion.get() or nil
  -- Each game reads its own layers: Emerald one; Ruby and Sapphire the
  -- catalogs they share, their edition's named text and the script text of
  -- their revision's layout.  Script text is keyed by ROM address, and
  -- revision 1.0 lays it out apart from 1.1 and 1.2: a guard address starts
  -- another text in each, which the cart's own text there tells apart.
  local layers, shared
  if game == "ruby" or game == "sapphire" then
    layers, shared = { game }, "rs"
    local function plain(ir)
      if type(ir) == "string" then return ir end
      local out = {}
      for _, segment in ipairs(type(ir) == "table" and ir or {}) do
        if type(segment) == "table" and segment.t == "text" then out[#out + 1] = segment.s end
      end
      return table.concat(out)
    end
    local layouts, chosen = catalog("rs/layouts")[game] or {}, nil
    for _, layout in ipairs(layouts) do
      if plain(mod.content.text:get(layout.key)) == layout.text then
        chosen = layout.dir
        break
      end
    end
    -- no guard matched (another mod rewrote the guard's text): the first
    -- layout, 1.1/1.2, the revisions most carts are
    layers[#layers + 1] = chosen or (layouts[1] and layouts[1].dir)
  else
    -- Emerald, the manifest's only other game
    layers, shared = { "emerald" }, "emerald"
  end
  -- Dialogue values are text IR segment lists (src/core/game3/scripting/
  -- text_ir.lua, the game's dialect), in as many files as LuaJIT's per-chunk
  -- constant limit needs (dialogue, dialogue_2, ...).
  for _, layer in ipairs(layers) do
    local part = 1
    while mod:read("lang/" .. layer .. "/" .. dialogueFile(part) .. ".lua") do
      for key, ir in pairs(catalog(layer .. "/" .. dialogueFile(part))) do
        if type(ir) == "table" and #ir > 0 then mod.content.text:override(key, ir) end
      end
      part = part + 1
    end
  end
  local function each(name, apply)
    for id, value in pairs(catalog(shared .. "/" .. name)) do
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


def rse_mod_id(language: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen3-rse"


def rse_archive_name(language: str, version: str) -> str:
    return f"{rse_mod_id(language)}-{version}.zip"


def _write_table(path: Path, title: str, values: Mapping[str, object], render) -> None:
    lines = [f"-- Generated by the Ruby, Sapphire and Emerald pipeline ({title})", "return {"]
    lines.extend(f"  [{lua_string(key)}] = {render(value)}," for key, value in sorted(values.items()))
    lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_layouts(path: Path, guards: Mapping[str, list[Mapping[str, str]]]) -> None:
    lines = ["-- Generated by the Ruby, Sapphire and Emerald pipeline: each edition's text",
             "-- layouts, and the guard address that tells them apart (main.lua)", "return {"]
    for edition, layouts in sorted(guards.items()):
        lines.append(f"  {edition} = {{")
        for row in layouts:
            lines.append(f"    {{ dir = {lua_string(row['dir'])}, key = {lua_string(row['key'])}, "
                         f"text = {lua_string(row['text'])} }},")
        lines.append("  },")
    lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_rse_mod(
    destination: str | Path,
    *,
    language: str,
    target_name: str,
    emerald: Mapping | None = None,
    rs: Mapping | None = None,
    mod_id: str | None = None,
) -> Path:
    """Write a deterministic manifest, entry point and layers.

    ``emerald`` is join_emerald's result (its ``dialogue`` and ``catalogs``),
    ``rs`` join_rs's (``named``, ``script``, ``catalogs`` and ``guards``).
    """
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    lang_dir = destination / "lang"
    if lang_dir.exists():
        shutil.rmtree(lang_dir)
    lang_dir.mkdir(parents=True)

    dialogue_layers: dict[str, Mapping[str, list[dict]]] = {}
    catalog_layers: dict[str, Mapping[str, Mapping[str, str]]] = {}
    if emerald is not None:
        dialogue_layers["emerald"] = emerald["dialogue"]
        catalog_layers["emerald"] = emerald["catalogs"]
    if rs is not None:
        dialogue_layers.update(rs["named"])
        dialogue_layers.update(rs["script"])
        catalog_layers["rs"] = rs["catalogs"]
        _write_layouts(lang_dir / "rs" / "layouts.lua", rs["guards"])

    for layer, dialogue in sorted(dialogue_layers.items()):
        for name, entries in dialogue_files(dialogue).items():
            _write_table(lang_dir / layer / f"{name}.lua", f"{language}, {layer}/{name}", entries, lua_ir)
    registered: set[str] = set()
    for layer, catalogs in sorted(catalog_layers.items()):
        unknown = set(catalogs) - set(CATALOG_HOOKS)
        if unknown:
            raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")
        for name in CATALOG_HOOKS:
            values = catalogs.get(name) or {}
            if values:
                _write_table(lang_dir / layer / f"{name}.lua", f"{language}, {layer}/{name}", values, lua_string)
                registered.add(name)
    # The launcher fills its pre-boot Strings() catalog from each mod's
    # top-level lang/strings.lua alone, before a game is chosen
    # (src/mods/LauncherMods.lua, STRINGS_CATALOG): the entries every game's
    # layer agrees on, its own options among them.  The game's layer
    # replaces it once the game boots.
    layer_strings = [dict(catalogs.get("strings") or {}) for catalogs in catalog_layers.values()]
    if layer_strings:
        launcher = {key: value for key, value in layer_strings[0].items()
                    if all(other.get(key) == value for other in layer_strings[1:])}
        if launcher:
            _write_table(lang_dir / "strings.lua", f"{language}, strings", launcher, lua_string)
    registration = "".join(
        f'  each("{name}", function(id, value) {CATALOG_HOOKS[name]} end)\n'
        for name in CATALOG_HOOKS if name in registered)
    (destination / "main.lua").write_text(MAIN.replace("__CATALOG_REGISTRATION__", registration),
                                          encoding="utf-8")

    manifest = {
        "id": mod_id or rse_mod_id(language), "name": target_name, "version": project_version(),
        "api": 2, "entry": "main.lua", "profile": "content", "games": list(GAMES),
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


def _layers(joined: dict, game: str) -> tuple[list[str], str]:
    """The dialogue layers and the catalog layer main.lua reads for ``game``
    from the cart that was read."""
    if game == "emerald":
        return ["emerald"], "emerald"
    source = joined["source"]
    return [layer_name(source.edition), layer_name(source.edition, source.layout)], "rs"


def write_gate_expectations(path: Path, joined: dict, game: str = "emerald") -> dict:
    """One sample per shipped catalog and consumer of one game, read back by
    tools/rse/gate.lua."""
    catalogs = joined["catalogs"]
    numbers = joined["numbers"]
    dialogue = joined["dialogue"]
    dialogue_layers, catalog_layer = _layers(joined, game)
    expectations: dict[str, object] = {
        "game": game, "dialogue_layers": dialogue_layers, "catalog_layer": catalog_layer,
    }

    for key in sorted(dialogue):
        ir = dialogue[key]
        if key.startswith("g3:") and any(segment.get("t") == "player" for segment in ir):
            probe = max((segment.get("s", "") for segment in ir if segment.get("t") == "text"), key=len)
            expectations["dialogue"] = {"key": key, "ir": ir, "probe": probe.strip()}
            break
    # Ruby and Sapphire: the guard address holds the translation of the
    # layout this cart's revision uses, not the other's
    if game != "emerald":
        source = joined["source"]
        guard = next(row for row in joined["guards"][source.edition]
                     if row["dir"] == layer_name(source.edition, source.layout))
        own = joined["script"][guard["dir"]].get(guard["key"])
        if own:
            expectations["layout"] = {"key": guard["key"], "ir": own}
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
    # The Emerald screens that print the cart's English through Strings()
    # (gen1recomp#2678), which the gate checks.
    if game == "emerald":
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
    if game != "emerald":
        sources["layout"] = True
    expectations["required"] = sorted(name for name, present in sources.items() if present)
    path.write_text(json.dumps(expectations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expectations


def run_rse_gate(mod_dir: Path, extracted: Path, joined: dict, gen1recomp: Path, luajit: str,
                 *, game: str = "emerald", rom_sha1: str | None = None,
                 log_fn: Callable[[str], None] | None = None) -> dict:
    """Load the mod through the real generation-3 loader on top of one
    game's extract (tools/rse/gate.lua)."""
    expectation_path = mod_dir.parent / f".{mod_dir.name}.{game}.gate.json"
    report_path = mod_dir.parent / f".{mod_dir.name}.{game}.{GATE_REPORT_NAME}"
    write_gate_expectations(expectation_path, joined, game)
    script = resource_root() / "tools" / "rse" / "gate.lua"
    try:
        _run([luajit, str(script), str(gen1recomp), str(extracted / "cache"), str(mod_dir),
              str(expectation_path), str(report_path), game, rom_sha1 or game],
             cwd=gen1recomp, log_fn=log_fn)
        return json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        expectation_path.unlink(missing_ok=True)


def rs_coverage(joined: dict) -> dict:
    """gen3_coverage for the cart that was read, with each layout's
    dialogue."""
    coverage = gen3_coverage(joined)
    coverage["layouts"] = {
        layer_name(*layout): {"read": data["read"], **{key: data["stats"][key]
                                                      for key in ("total", "covered", "percent")}}
        for layout, data in joined["layouts"].items()
    }
    return coverage


def write_rse_report(path: Path, games: Mapping[str, tuple[dict, dict, dict]]) -> None:
    """Private per-key report (it quotes ROM text: never packaged): for each
    game, its coverage, unresolved rows and gate."""
    report = {}
    for game, (joined, coverage, gate) in games.items():
        report[game] = {
            "coverage": coverage,
            "dialogue_unresolved": unresolved_entries(joined),
            "catalog_issues": joined["catalog_issues"],
            "engine_details": joined["engine_stats"]["details"],
            "gate": gate,
        }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- build

def build_rse(
    rs_rom: str | Path,
    emerald_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> Path:
    """Extract, join, gate and package the Ruby, Sapphire and Emerald mod
    from one Ruby or Sapphire ROM and the Emerald ROM."""

    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    profile = release_profile("rse")
    supported = set.intersection(*({code for code, _name in languages_for_collection(game_spec(game).corpus_collection)}
                                   for game in profile.games))
    if language not in supported:
        raise BuildError(f"Ruby, Sapphire and Emerald have no {language} release.")
    status("Validating ROMs")
    rs_info = verify_rs_rom(rs_rom)
    verify_emerald_rom(emerald_rom)
    edition = rs_info["version"]
    edition_name = RS_EDITIONS[edition]

    from ..shared.orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language, font_profile=None,
    )
    workspace, destination, gen1recomp = context.workspace, context.destination, context.gen1recomp
    corpora = context.corpus / "corpus"
    status("Preparing dependencies")
    config = project_config()
    emerald_symbols, emerald_charmap = prepare_emerald_pret_inputs(workspace, config)
    rs_symbols, rs_charmap = prepare_rs_pret_inputs(workspace, config)

    extracted = {"emerald": workspace / "emerald" / "extracted", "rs": workspace / edition / "extracted"}
    log(f"\nExtracting private {edition_name} {rs_info['revision']} ROM data...")
    status(f"Extracting private {edition_name} ROM data")
    import_rse_rom(rs_rom, gen1recomp, extracted["rs"], log_fn=log_fn, game="rs")
    log("\nExtracting private Emerald ROM data...")
    status("Extracting private Emerald ROM data")
    import_rse_rom(emerald_rom, gen1recomp, extracted["emerald"], log_fn=log_fn)

    log("\nJoining corpus and generating the mod...")
    status("Joining corpus and generating the mod")
    with rom_text_cache(gen1recomp, extracted["emerald"], "emerald"):
        emerald = join_emerald(extracted["emerald"], corpora / game_spec("emerald").corpus_collection, language,
                               emerald_symbols, emerald_charmap, gen1recomp, luajit)
    with rom_text_cache(gen1recomp, extracted["rs"], edition):
        rs = join_rs(extracted["rs"], corpora / game_spec("rs").corpus_collection, language, rs_symbols,
                     rs_charmap, rs_revision_for_sha1(rs_info["sha1"]), gen1recomp, luajit,
                     companion_strings=emerald["engine_values"])
    build_root = workspace / "interactive-rse" / language
    mod_dir = build_root / rse_mod_id(language)
    generate_rse_mod(mod_dir, language=language,
                     target_name=f"{language_name} translation for Ruby, Sapphire and Emerald",
                     emerald=emerald, rs=rs)
    coverage = {"rs": rs_coverage(rs), "emerald": gen3_coverage(emerald)}
    status("Running the release gate")
    gates = {
        edition: run_rse_gate(mod_dir, extracted["rs"], rs, gen1recomp, luajit, game=edition,
                              rom_sha1=rs_info["sha1"], log_fn=log_fn),
        "emerald": run_rse_gate(mod_dir, extracted["emerald"], emerald, gen1recomp, luajit, log_fn=log_fn),
    }
    attach_rse_validation(mod_dir, {
        "schema": 1, "policy": "english-fallback", "coverage": coverage,
        "runtime_limits": {
            "blank_glyphs": sum(gate.get("blank_glyphs", {}).get("total", 0) for gate in gates.values()),
            "strings_resolve_in_game": all(bool((gate.get("strings_live") or {}).get("resolves"))
                                           for gate in gates.values()),
            "display_hooks": gates["emerald"].get("hooks", {}),
        },
    })
    write_rse_report(build_root / "coverage.json", {
        edition: (rs, coverage["rs"], gates[edition]),
        "emerald": (emerald, coverage["emerald"], gates["emerald"]),
    })
    for key, label in (("rs", f"{edition_name} {rs_info['revision']}"), ("emerald", "Emerald")):
        for part, what in (("rom", "ROM aggregate"), ("engine_gen3", "engine strings")):
            section = coverage[key][part]
            log(f"  {label} {what}: {section['translated']}/{section['total']} ({section['percent']:.2f}%)")
    for name, layout in sorted(coverage["rs"]["layouts"].items()):
        how = "read from the ROM" if layout["read"] else "keyed through pret's symbols"
        log(f"  {name} dialogue ({how}): {layout['covered']}/{layout['total']} ({layout['percent']:.2f}%)")
    blank = sum(gate.get("blank_glyphs", {}).get("total", 0) for gate in gates.values())
    if blank:
        log(f"  runtime limit: {blank} shipped characters have no glyph in FrlgFont yet"
            " (docs/upstream-fixes.md, Emerald)")

    status("Packaging translation mod")
    # modkit pack checks lang/strings.lua against both carts' text (MK306)
    with rom_text_cache(gen1recomp, extracted["emerald"], "emerald"), \
            rom_text_cache(gen1recomp, extracted["rs"], edition):
        published = package_gen3_mod(mod_dir, gen1recomp, build_root, destination,
                                     rse_archive_name(language, project_version()), luajit, log_fn)
    status("Build complete")
    return published
