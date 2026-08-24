"""Generate, validate, and package the generation-2 Gold translation mod."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from .builder import BuildError, _run
from .corpus import canonical_language
from .roms import GOLD_REQUIRED_TSV, import_gold_rom, verify_gold_rom
from .generate import lua_string
from .gold_engine import match_gold_engine_strings
from .gold_index_join import join_by_index, join_dex_entries, join_landmarks, parse_indexed_catalog
from .gold_join import (
    GoldJoinEntry, GoldPlaceholderDecision, audit_join, gold_coverage_report,
    join_gold_pointers, load_gold_placeholder_decisions,
    load_gold_pointer_decisions, load_gold_silver_pointer_aliases, read_corpus_rows,
)
from .gold_text import parse_gold_text_catalog
from .tokens import corpus_to_engine
from .mod import TRANSLATION_MOD_PRIORITY, install_font_assets, ttf_registration, validate_font_profile
from .project import is_frozen, project_config, project_version, resource_root
from .specs import game_spec, release_profile

MAIN = '''-- Generated Gold translation mod.
return function(mod)
__TTF_REGISTRATION__
__CATALOG_REGISTRATION__
end
'''

_CATALOG_HELPER = '''  local function catalog(name)
    local body = mod:read("lang/" .. name .. ".lua")
    if not body then return {} end
    local chunk = loadstring(body)
    if not chunk then return {} end
    local ok, value = pcall(chunk)
    return ok and type(value) == "table" and value or {}
  end
  local function each(name, apply)
    for id, value in pairs(catalog(name)) do
      if type(value) == "string" and value ~= "" then apply(id, value) end
    end
  end
'''

# Catalog names avoid modkit's generated-module names.  In particular,
# "dialogue" prevents Gold's pointer catalog from being treated as a full
# dump of the engine's generated "text" module.
GOLD_CATALOG_HOOKS = {
    "dialogue": "mod.content.text:override(id, value)",
    "strings": "mod.content.strings:override(id, value)",
    "species_names": "mod.content.pokemon:patch(id, { name = value })",
    "species_kinds": "mod.content.pokemon:patch(id, { dexEntry = { kind = value } })",
    "species_dex_text": "mod.content.pokemon:patch(id, { dexEntry = { text = value } })",
    "move_names": "mod.content.moves:patch(id, { name = value })",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { name = value })",
    "landmarks": "mod.content.landmarks:patch(id, { name = value })",
}
GOLD_CATALOG_HOOKS["ui_labels"] = None

# A small part of Gold's menu text is supplied as labels to existing mod
# hooks rather than through the engine Strings registry.  Keep the reviewed
# QID/segment recipes in config/gold/literal_handlers.json; the runtime hook
# below only changes labels already exposed by gen1recomp's public hooks.
_GOLD_UI_HANDLER_PATH = Path(__file__).resolve().parents[1] / "config" / "gold" / "literal_handlers.json"


def _load_gold_ui_handlers() -> dict[str, tuple[str, int, int | None]]:
    data = json.loads(_GOLD_UI_HANDLER_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != "gen1recomp-translation-mods/gold-literal-handlers" or data.get("version") != 1:
        raise ValueError("unsupported Gold literal handler schema")
    result: dict[str, tuple[str, int, int | None]] = {}
    for source, row in data.get("entries", {}).items():
        if (not isinstance(source, str) or not source or not isinstance(row, dict)
                or not isinstance(row.get("qid"), str) or not row["qid"].startswith("gs.")
                or (not isinstance(row.get("segment"), int) and row.get("full") is not True)
                or (isinstance(row.get("segment"), int) and row["segment"] < 0)
                or (row.get("full") is True and isinstance(row.get("segment"), int))):
            raise ValueError(f"invalid Gold literal handler for {source!r}")
        page = row.get("page")
        if page is not None and (not isinstance(page, int) or page < 0):
            raise ValueError(f"invalid Gold literal handler page for {source!r}")
        result[source] = (row["qid"], -1 if row.get("full") is True else row["segment"], page)
    return result


def _gold_ui_labels(corpus_rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Return corpus-backed labels used by already exposed Gold menu hooks."""
    rows = {qid: target for qid, _english, target in corpus_rows}
    result: dict[str, str] = {}
    for source, (qid, index, page) in _load_gold_ui_handlers().items():
        target = rows.get(qid, "")
        if index < 0:
            value = corpus_to_engine(target, bare_dynamic_tokens=True)
            if page is not None:
                value = value.split("\f")[page] if page < len(value.split("\f")) else ""
            if value:
                result[source] = value
        else:
            parts = target.split("@")
            if index < len(parts) and parts[index].strip():
                value = parts[index].strip()
                # Segment mode ships the raw corpus markup verbatim (these
                # labels are tile-width-critical, e.g. "WITHDRAW <PK><MN>"
                # must stay two glyphs, not expand to "POKéMON"). Some
                # corpus rows spell the same glyph pair "<PKMN>" instead of
                # "<PK><MN>" (observed in es/it's MOVE W/O MAIL row); that
                # single-tag spelling has no matching Font.split() macro and
                # would render as literal garbage, so normalize it here.
                value = value.replace("<PKMN>", "<PK><MN>")
                result[source] = value
    return result

GOLD_OAK_SPEECH_CATALOG = "oak_speech"
GOLD_OAK_SPEECH_KEYS = frozenset({
    "_OakText1", "_OakText2", "_OakText4", "_OakText5", "_OakText6", "_OakText7",
})

_OAK_SPEECH_REGISTRATION = '''  local oakSpeech = catalog("oak_speech")
  mod.hooks:wrap("intro.oak_speech.build", function(nextFn, steps, speech)
    speech.texts = speech.texts or {}
    for id, value in pairs(oakSpeech) do
      if type(value) == "string" and value ~= "" then speech.texts[id] = value end
    end
    return nextFn(steps, speech)
  end)
'''

_UI_LABEL_REGISTRATION = '''  local uiLabels = catalog("ui_labels")
  local function localizeItems(_, items)
    for _, item in ipairs(items or {}) do
      if type(item) == "table" and type(item.label) == "string" then
        local value = uiLabels[item.label]
        if value then item.label = value end
      end
      -- Two-line row descriptions (e.g. the START menu's highlighted-entry
      -- box) are keyed by their two lines joined with "\\n", matching the
      -- catalog entry's source key; only replace both lines together so a
      -- one-line-only or missing translation never desyncs the pair.
      if type(item) == "table" and type(item.desc) == "table"
          and type(item.desc[1]) == "string" and type(item.desc[2]) == "string" then
        local value = uiLabels[item.desc[1] .. "\\n" .. item.desc[2]]
        if value then
          local line1, line2 = value:match("^(.-)\\n(.*)$")
          if line1 and line2 then
            item.desc[1], item.desc[2] = line1, line2
          end
        end
      end
    end
    return items
  end
  for _, hook in ipairs({
    "ui.pc.items", "ui.title_menu.items", "ui.start_menu.items",
    "ui.options.rows", "ui.party.submenu",
  }) do
    mod.hooks:wrap(hook, function(nextFn, ...)
      local args = {...}
      -- Public list hooks use (identity, game, items, ...).
      local items = args[3] or args[2] or args[1]
      if type(items) == "table" then localizeItems(nil, items) end
      return nextFn(table.unpack(args))
    end)
  end
'''

# Keep this mirror in sync with tools/modkit.py's GENERATED_MODULES.
_MODKIT_GENERATED_MODULES = frozenset({
    "constants", "maps", "tilesets", "text", "text_pointers",
    "trainer_headers", "font", "sprites", "pokemon", "moves", "items",
    "type_chart", "trainers", "encounters", "field", "battle_anims",
    "audio", "palettes", "icons",
})
assert not (set(GOLD_CATALOG_HOOKS) & _MODKIT_GENERATED_MODULES), (
    "a GOLD_CATALOG_HOOKS name collides with modkit's GENERATED_MODULES "
    "and will make `modkit pack` fail under --strict; rename the catalog"
)

GOLD_REQUIRED_REGISTRIES = (
    "strings", "species_names", "species_kinds", "species_dex_text", "move_names",
    "item_names", "trainer_class_names", "landmarks", GOLD_OAK_SPEECH_CATALOG,
)

def gold_mod_id(language: str) -> str:
    """Return the generation-scoped Gold mod identifier."""
    return f"translation-{canonical_language(language).lower()}-gen2"


def gold_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen2-{version}.zip"


def generate_gold_mod(
    destination: str | Path,
    mod_id: str | None = None,
    language: str = "fr",
    target_name: str | None = None,
    target_description: str | None = None,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
    text_catalog: dict[str, str] | None = None,
    extra_catalogs: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Write a deterministic Gold manifest, entry point, and catalogs."""
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    mod_id = mod_id or gold_mod_id(language)

    catalogs = {"dialogue": text_catalog or {}, **(extra_catalogs or {})}
    catalogs = {name: values for name, values in catalogs.items() if values}
    unknown = set(catalogs) - set(GOLD_CATALOG_HOOKS) - {GOLD_OAK_SPEECH_CATALOG}
    if unknown:
        raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")

    lang_dir = destination / "lang"
    if lang_dir.exists():
        shutil.rmtree(lang_dir)
    catalog_registration = ""
    if catalogs:
        lang_dir.mkdir(parents=True, exist_ok=True)
        for name, values in catalogs.items():
            lines = [f"-- Generated by the Gold pipeline ({language}): {name}", "return {"]
            lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items()))
            lines.append("}")
            (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        catalog_registration = _CATALOG_HELPER + "".join(
            f'  each("{name}", function(id, value) {GOLD_CATALOG_HOOKS[name]} end)\n'
            for name in catalogs if name in GOLD_CATALOG_HOOKS and GOLD_CATALOG_HOOKS[name]
        )
        if "ui_labels" in catalogs:
            catalog_registration += _UI_LABEL_REGISTRATION
        if GOLD_OAK_SPEECH_CATALOG in catalogs:
            catalog_registration += _OAK_SPEECH_REGISTRATION
    main_body = (
        MAIN.replace("__TTF_REGISTRATION__", ttf_registration(language, font_source, font_profile))
        .replace("__CATALOG_REGISTRATION__", catalog_registration)
    )
    (destination / "main.lua").write_text(main_body, encoding="utf-8")
    install_font_assets(destination, language, font_source, font_profile)

    display_name = target_name or f"{language} translation for Gold/Silver"
    description = target_description or (
        f"{display_name}, based mostly on PokeCorpus."
        + ("" if catalogs else " Text is not wired up yet; this is a loadable skeleton.")
    )
    # "silver" alongside "gold": this mod is still built and extracted from a
    # Gold ROM only, but Gold and Silver share the same pokegold source tree
    # and near-identical dialogue text-table addresses (gen1recomp's own
    # tools/make_silver_manifest.py derives Silver's manifest from Gold's,
    # touching only sprite/Pokédex/graphics symbols), and a mod override that
    # doesn't find a matching key in a Silver import's own extracted text
    # just silently no-ops rather than showing wrong text (src/mods/
    # Registry.lua's override folds onto the base table by exact id match) --
    # so declaring "silver" here lets the same mod apply to a real Silver
    # save via src/mods/ModTargets.lua's specApplies() with no separate
    # Silver-specific build.
    manifest_body = {
        "id": mod_id, "name": display_name, "version": project_version(), "api": 2,
        "entry": "main.lua", "profile": "content", "games": ["gold", "silver"],
        "game_version": ">=0.0.0-dev <1.0.0", "category": "LANGUAGE",
        "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [],
        "conflicts": [], "permissions": [], "description": description,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return destination


def package_gold_mod(
    mod_dir: str | Path,
    gen1recomp: str | Path,
    modkit: str | Path,
    build_root: str | Path,
    destination: str | Path,
    language: str = "fr",
    luajit: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> Path:
    """Pack and publish a generation-scoped Gold archive."""
    from .orchestration import package_release

    language = canonical_language(language)
    archive_name = gold_archive_name(language, project_version())
    env = None
    if luajit is not None:
        env = dict(os.environ)
        env["MODKIT_LUAJIT"] = str(luajit)
        env["LUA"] = str(luajit)
        # Matches builder.py's build(): modkit's dump_dataset() decodes the
        # LuaJIT dump with subprocess text=True and no explicit encoding,
        # which falls back to the OS locale codepage (e.g. cp1252 on
        # Windows) and can crash on dumped text outside it.
        env["PYTHONUTF8"] = "1"
        if is_frozen():
            lua_dir = str(Path(luajit).resolve().parent)
            env["PATH"] = lua_dir + os.pathsep + env.get("PATH", "")
    return package_release(
        mod_dir, gen1recomp, modkit, build_root, destination, archive_name,
        env=env, log_fn=log_fn,
    )


def gold_text_catalog_from_join(entries: list[GoldJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved.

    Also aliases the handful of pointers config/gold/silver_pointer_aliases.json
    knows shift address between Gold and Silver for verbatim-identical text
    (see load_gold_silver_pointer_aliases's docstring): each Gold pointer's
    own resolved translation, whatever it ended up being, is reused under
    its Silver pointer too -- this mod already declares itself compatible
    with a Silver save (see the "games" field in generate_gold_mod below),
    and without this a Silver player would silently miss these 8 lines even
    though the exact same English text is translated for Gold.
    """
    catalog = {entry.pointer: entry.translation for entry in entries if entry.translation}
    for gold_pointer, silver_pointer in load_gold_silver_pointer_aliases().items():
        if gold_pointer in catalog:
            catalog[silver_pointer] = catalog[gold_pointer]
    return catalog


def gold_oak_speech_catalog_from_join(entries: list[GoldJoinEntry]) -> dict[str, str]:
    """Return translated intro labels consumed by Gold's Oak speech hook."""
    return {
        entry.label: entry.translation
        for entry in entries
        if entry.label in GOLD_OAK_SPEECH_KEYS and entry.translation
    }


def _write_gate_expectations(mod_dir: Path, catalogs: dict[str, dict[str, str]]) -> Path:
    """Write a tiny, private expectation file consumed by the registry gate."""
    optional = {"ui_labels"}
    if not set(catalogs) - optional >= set(GOLD_REQUIRED_REGISTRIES):
        missing = sorted(set(GOLD_REQUIRED_REGISTRIES) - set(catalogs))
        raise BuildError(
            "Gold registry gate expectations are incomplete"
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    extra = sorted(set(catalogs) - set(GOLD_REQUIRED_REGISTRIES) - optional)
    if extra:
        raise BuildError(
            "Gold registry gate expectations are incomplete"
            + f"; unexpected: {', '.join(extra)}"
        )
    expected: dict[str, dict[str, str]] = {}
    for name in GOLD_REQUIRED_REGISTRIES:
        values = catalogs[name]
        if not isinstance(values, dict) or not values:
            raise BuildError(f"Gold registry gate expectation is empty: {name}")
        key = sorted(values)[0]
        value = values[key]
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise BuildError(f"Gold registry gate expectation is malformed: {name}")
        expected[name] = {"id": key, "value": value}
    path = mod_dir.parent / f".{mod_dir.name}.registry-gate.json"
    path.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _write_dialogue_gate_expectation(
    mod_dir: Path, resolved_pointer: str, expected_translation: str, unresolved_pointer: str,
) -> Path:
    """Write a tiny, private expectation file consumed by the dialogue gate.

    Not passed as plain command-line arguments: Windows narrows a child
    process's argv to the console's active ANSI codepage before the C
    runtime's main() ever sees it (the same mechanism as the modkit-pack
    non-ASCII *path* bug documented in docs/upstream-fixes.md, here hitting
    translated *text* instead). A real report confirmed it -- German passed
    (representable in a Western codepage), Japanese and Korean did not,
    failing "gen2Text[...] is the expected translation" with mojibake in
    place of the real string. A file read as raw UTF-8 bytes is not subject
    to that narrowing at all.
    """
    path = mod_dir.parent / f".{mod_dir.name}.dialogue-gate.json"
    path.write_text(
        json.dumps(
            {
                "resolved_pointer": resolved_pointer,
                "expected_translation": expected_translation,
                "unresolved_pointer": unresolved_pointer,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run_gold_release_gates(
    mod_dir: str | Path,
    entries: list[GoldJoinEntry],
    gen1recomp: str | Path,
    luajit: str,
    *,
    catalogs: dict[str, dict[str, str]] | None = None,
    coverage: dict | None = None,
    placeholder_decisions: dict[str, GoldPlaceholderDecision] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> dict:
    """Run every technical Gold gate before publishing the candidate.

    Unavailable gates, invalid generated content, bad tokens, and loader
    errors fail closed.  Unresolved corpus joins deliberately remain English;
    their counts are release provenance, not technical failures.

    The ROM charmap check is intentionally omitted because translated Gold
    text is rendered by the bundled TTF, not the stock ROM tile font.
    """
    problems = audit_join(entries, placeholder_decisions)
    if problems:
        raise BuildError("Gold join audit failed:\n" + "\n".join(problems))
    gen1recomp = Path(gen1recomp).resolve()
    coverage = coverage or gold_coverage_report(entries)

    tools = resource_root() / "tools"
    fixtures = tools / "gen2_gate_fixtures"
    for script in ("gate_gen2.lua", "gate_gold_dialogue.lua", "gate_gold_registries.lua"):
        if not (tools / script).is_file():
            raise BuildError(f"Gold release gate script is missing: {tools / script}")
    if not Path(luajit).is_file() and shutil.which(luajit) is None:
        raise BuildError("Gold release gate requires a LuaJIT executable")
    mod_dir = Path(mod_dir).resolve()
    # First prove the generation=2 harness itself, then prove this generated
    # mod's dialogue and index registries with values from its own catalogs.
    _run([luajit, str(tools / "gate_gen2.lua"), str(gen1recomp), str(fixtures)], log_fn=log_fn)
    translated = next((e for e in entries if e.translation), None)
    if translated is None:
        raise BuildError("Gold dialogue gate requires at least one translated pointer")
    unresolved = next((e for e in entries if e.translation is None), None)
    unresolved_pointer = unresolved.pointer if unresolved else "__gold_unresolved_gate_pointer__"
    dialogue_expectation_path = _write_dialogue_gate_expectation(
        mod_dir, translated.pointer, translated.translation, unresolved_pointer,
    )
    try:
        _run([luajit, str(tools / "gate_gold_dialogue.lua"), str(gen1recomp), str(mod_dir),
              str(dialogue_expectation_path)], log_fn=log_fn)
    finally:
        dialogue_expectation_path.unlink(missing_ok=True)
    expectation_path = _write_gate_expectations(mod_dir, catalogs or {})
    try:
        _run([luajit, str(tools / "gate_gold_registries.lua"), str(gen1recomp), str(mod_dir), str(expectation_path)], log_fn=log_fn)
    finally:
        expectation_path.unlink(missing_ok=True)
    engine_revision = str(project_config()["gen1recomp"]["revision"])

    def coverage_summary(name: str) -> dict:
        section = coverage[name]
        return {
            key: section[key] for key in ("translated", "total", "percent", "source_revision")
            if key in section
        }

    validation = {
        "schema": 1,
        "policy": "english-fallback",
        "coverage": {
            **coverage["rom"],
            "ambiguous": len(coverage["ambiguous"]),
            "unmatched": len(coverage["unmatched"]),
            "ignored_markup_only": coverage["ignored_markup_only"],
            **({"engine": coverage_summary("engine")} if "engine" in coverage else {}),
            **({"engine_gen2": coverage_summary("engine_gen2")} if "engine_gen2" in coverage else {}),
        },
        "checks": [
            {
                "tool": "pipeline.gold_join.audit_join",
                "version": project_version(),
                "command": "internal Gold join audit",
                "status": "passed",
            },
            {
                "tool": "tools/gate_gen2.lua",
                "version": engine_revision,
                "command": "luajit tools/gate_gen2.lua <gen1recomp> tools/gen2_gate_fixtures",
                "status": "passed",
            },
            {
                "tool": "tools/gate_gold_dialogue.lua",
                "version": engine_revision,
                "command": "luajit tools/gate_gold_dialogue.lua <gen1recomp> <mod> <expectation_json_path>",
                "status": "passed",
            },
            {
                "tool": "tools/gate_gold_registries.lua",
                "version": engine_revision,
                "command": "luajit tools/gate_gold_registries.lua <gen1recomp> <mod> <expectations>",
                "status": "passed",
            },
        ],
    }
    return {"coverage": coverage, "validation": validation}


def attach_gold_validation(mod_dir: str | Path, validation: dict) -> None:
    """Attach deterministic, ROM-free validation provenance to the manifest."""
    manifest_path = Path(mod_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["validation"] = validation
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


# Numeric suffixes are reused across corpus categories, so every index join
# must retain its full qid prefix.
_INDEX_CATALOG_QID_PREFIXES = {
    "species_names": "gs.names.PokemonNames.",
    "move_names": "gs.names.MoveNames.",
    "item_names": "gs.names.ItemNames.",
    "trainer_class_names": "gs.class_names.TrainerClassNames.",
}


def build_gold_dialogue_mod(
    gold_out_dir: str | Path,
    corpus_dir: str | Path,
    destination: str | Path,
    mod_id: str | None = None,
    language: str = "fr",
    target_name: str | None = None,
    target_description: str | None = None,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
    overrides: dict[str, str] | None = None,
    engine_source: str | Path | None = None,
) -> tuple[Path, list[GoldJoinEntry], dict]:
    """Join extracted Gold catalogs to the corpus and generate the mod."""
    gold_out_dir = Path(gold_out_dir)
    language = canonical_language(language)
    missing_or_empty = []
    for filename in GOLD_REQUIRED_TSV:
        path = gold_out_dir / filename
        if not path.is_file() or not any(line.strip() for line in path.read_text(encoding="utf-8").splitlines()):
            missing_or_empty.append(filename)
    if missing_or_empty:
        raise ValueError(
            "Gold release extraction is incomplete; required non-empty TSVs missing: "
            + ", ".join(missing_or_empty)
        )
    records = parse_gold_text_catalog(
        gold_out_dir / "gold_text.tsv", gold_out_dir / "gold_labels.tsv",
    )
    corpus_rows = read_corpus_rows(corpus_dir, target_lang=language)
    entries, stats = join_gold_pointers(
        records, corpus_rows, overrides=overrides,
        qid_decisions=load_gold_pointer_decisions(),
    )

    extra_catalogs: dict[str, dict[str, str]] = {}
    oak_speech = gold_oak_speech_catalog_from_join(entries)
    if oak_speech:
        missing_oak = sorted(GOLD_OAK_SPEECH_KEYS - set(oak_speech))
        if missing_oak:
            raise ValueError("Gold Oak speech catalog is incomplete: " + ", ".join(missing_oak))
        extra_catalogs[GOLD_OAK_SPEECH_CATALOG] = oak_speech
    index_stats: dict[str, dict] = {}
    species_path = gold_out_dir / "gold_species.tsv"
    species = parse_indexed_catalog(species_path)
    if not species:
        raise ValueError("gold_species.tsv contains no valid indexed entries")
    for catalog_name, tsv_name in (
        ("species_names", "gold_species.tsv"), ("move_names", "gold_moves.tsv"),
        ("item_names", "gold_items.tsv"), ("trainer_class_names", "gold_trainer_classes.tsv"),
    ):
        tsv_path = gold_out_dir / tsv_name
        catalog_entries = species if catalog_name == "species_names" else parse_indexed_catalog(tsv_path)
        if not catalog_entries:
            raise ValueError(f"{tsv_name} contains no valid indexed entries")
        translations, catalog_stats = join_by_index(
            catalog_entries, corpus_rows, _INDEX_CATALOG_QID_PREFIXES[catalog_name],
        )
        extra_catalogs[catalog_name] = translations
        index_stats[catalog_name] = catalog_stats
    kind_translations, kind_stats = join_dex_entries(species, corpus_rows, "dex_entries", "Species")
    extra_catalogs["species_kinds"] = kind_translations
    index_stats["species_kinds"] = kind_stats
    text_translations, text_stats = join_dex_entries(species, corpus_rows, "dex_entries_gold")
    extra_catalogs["species_dex_text"] = text_translations
    index_stats["species_dex_text"] = text_stats
    landmarks_path = gold_out_dir / "gold_landmarks.tsv"
    landmarks = parse_indexed_catalog(landmarks_path)
    if not landmarks:
        raise ValueError("gold_landmarks.tsv contains no valid indexed entries")
    landmark_translations, landmark_stats = join_landmarks(landmarks, corpus_rows)
    extra_catalogs["landmarks"] = landmark_translations
    index_stats["landmarks"] = landmark_stats
    if engine_source is not None:
        engine_values, engine_coverage = match_gold_engine_strings(
            corpus_rows, engine_source, language,
        )
        extra_catalogs["strings"] = engine_values
        stats.update(engine_coverage)
    extra_catalogs["ui_labels"] = _gold_ui_labels(corpus_rows)
    stats["index_catalogs"] = index_stats
    pointer_coverage = gold_coverage_report(entries)
    registry_translated = sum(int(item["translated"]) for item in index_stats.values())
    registry_total = sum(int(item["total"]) for item in index_stats.values())
    registry_coverage = {
        "translated": registry_translated,
        "total": registry_total,
        "percent": round(100.0 * registry_translated / registry_total, 2) if registry_total else 100.0,
    }
    rom_translated = pointer_coverage["rom"]["translated"] + registry_translated
    rom_total = pointer_coverage["rom"]["total"] + registry_total
    stats["coverage"] = {
        **pointer_coverage,
        "rom_dialogue": pointer_coverage["rom"],
        "rom_catalogs": registry_coverage,
        "rom": {
            "translated": rom_translated,
            "total": rom_total,
            "percent": round(100.0 * rom_translated / rom_total, 2) if rom_total else 100.0,
        },
    }
    for key in ("engine", "engine_gen2"):
        if key in stats:
            stats["coverage"][key] = stats[key]
    # Kept in-memory for the pre-publication registry gate; callers that
    # serialize stats can omit this private payload.
    stats["_gate_catalogs"] = extra_catalogs
    stats["_placeholder_decisions"] = load_gold_placeholder_decisions(language)

    mod_dir = generate_gold_mod(
        destination, mod_id=mod_id, language=language, target_name=target_name,
        target_description=target_description, font_source=font_source, font_profile=font_profile,
        text_catalog=gold_text_catalog_from_join(entries),
        extra_catalogs=extra_catalogs,
    )
    return mod_dir, entries, stats


def build_gold(
    gold_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
    font_profile: str = "fusion",
) -> Path:
    """Run Gold's private extraction, join, validation, and packaging flow."""
    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    profile = release_profile("gold")
    spec = game_spec("gold")
    if spec.corpus_collection not in profile.corpus_collections:
        raise BuildError("Gold release profile and game spec disagree on corpus collection")
    font_profile = validate_font_profile(language, font_profile)
    status("Validating ROM")
    verify_gold_rom(gold_rom)

    from .orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language,
        font_profile=font_profile,
    )
    workspace = context.workspace
    destination = context.destination

    status("Preparing dependencies")
    gen1recomp, corpus, font_source = context.gen1recomp, context.corpus, context.font_source
    corpus_gold_silver = corpus / "corpus" / "GoldSilver"

    log("\nExtracting private Gold ROM data...")
    status("Extracting private Gold ROM data")
    gold_out = workspace / "gold" / "extracted"
    import_gold_rom(gold_rom, gen1recomp, gold_out, log_fn=log_fn)

    build_root = workspace / "interactive-gold" / language
    mod_id = gold_mod_id(language)
    mod_dir = build_root / mod_id
    log("\nJoining corpus and generating the mod...")
    status("Joining corpus and generating the mod")
    mod_dir, entries, stats = build_gold_dialogue_mod(
        gold_out, corpus_gold_silver, mod_dir, mod_id=mod_id, language=language,
        target_name=f"{language_name} translation for Gold/Silver", font_source=font_source, font_profile=font_profile,
        engine_source=gen1recomp,
    )
    log(
        f"  text: {stats['unique'] + stats['harmless_ambiguous'] + stats['override'] + stats['reviewed_qid']}/{stats['total']} pointers"
        f" ({stats['unresolved']} unresolved, left in English)"
    )

    status("Running Gold release gates")
    gate_report = run_gold_release_gates(
        mod_dir, entries, gen1recomp, luajit,
        catalogs=stats.get("_gate_catalogs", {}),
        coverage=stats["coverage"],
        placeholder_decisions=stats.get("_placeholder_decisions", {}),
        log_fn=log_fn,
    )
    attach_gold_validation(mod_dir, gate_report["validation"])
    coverage_path = build_root / "coverage.json"
    coverage_path.write_text(
        json.dumps(gate_report["coverage"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for key, label in (
        ("rom", "Gold ROM aggregate"),
        ("engine_gen2", "Gold-related engine strings"),
        ("engine", "All engine strings"),
    ):
        section = gate_report["coverage"].get(key) or {}
        log(
            f"  {label}: {section.get('translated', 0)}/{section.get('total', 0)}"
            f" ({float(section.get('percent', 0.0)):.2f}%)"
        )

    destination.mkdir(parents=True, exist_ok=True)
    modkit = gen1recomp / "tools" / "modkit.py"
    status("Packaging translation mod")
    published = package_gold_mod(
        mod_dir, gen1recomp, modkit, build_root, destination, language=language, luajit=luajit, log_fn=log_fn,
    )
    status("Build complete")
    return published
