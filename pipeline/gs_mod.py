"""Generate, validate, and package the generation-2 Gold translation mod."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from .builder import BuildError, _run
from .corpus import canonical_language
from .crystal_mod import (
    crystal_feature_catalogs, crystal_text_catalog_from_join, join_crystal_dialogue,
)
from .roms import GS_REQUIRED_TSV, gs_required_tsv, import_crystal_rom, import_gs_rom, verify_crystal_rom, verify_gs_rom
from .generate import lua_string
from .gs_engine import match_gs_engine_strings
from .gs_index_join import (
    join_by_index, join_dex_entries, join_dex_entries_pages, join_landmarks,
    parse_indexed_catalog,
)
from .gs_localized_registries import (
    _index_corpus, decoration_catalog, phone_contact_catalog, radio_channel_catalog,
    status_label_catalog, type_name_catalog,
)
from .gs_join import (
    GsJoinEntry, GsPlaceholderDecision, audit_join, gs_coverage_report,
    join_gs_pointers, load_gs_dialogue_overrides, load_gs_placeholder_decisions,
    load_gs_pointer_decisions, load_gold_silver_pointer_aliases, read_corpus_rows,
)
from .gs_text import parse_gs_text_catalog
from .tokens import corpus_to_engine
from .mod import TRANSLATION_MOD_PRIORITY, install_font_assets, ttf_registration, validate_font_profile
from .project import is_frozen, project_config, project_version, resource_root
from .specs import game_spec, release_profile
from .engine_profile import (
    PINNED_PROFILE, UPSTREAM_PROFILE, normalize_engine_profile, profile_for,
    validate_engine_profile_and_source,
)

MAIN = '''-- Generated Gold translation mod.
return function(mod)
__TTF_REGISTRATION__
__CATALOG_REGISTRATION__
__CRYSTAL_DIALOGUE_REGISTRATION__
__CRYSTAL_REGISTRY_REGISTRATION__
__SILVER_DEX_TEXT_REGISTRATION__
__CRYSTAL_DEX_TEXT_REGISTRATION__
end
'''

# GameVersion.isCrystal() doesn't exist upstream (only isBlue()/isYellow()/
# isGold() do); GameVersion.get() == "crystal" is exactly what those do
# internally for their own edition. Mirrors mod.py's yellow_isyellow_guard_lines()
# for RBY's own Yellow layer.
_CRYSTAL_GUARD = (
    '  local okGame, GameVersion = pcall(require, "src.core.GameVersion")\n'
    "  local crystal_game_version = okGame and type(GameVersion) == \"table\"\n"
    "      and type(GameVersion.get) == \"function\"\n"
    "      and GameVersion.get() == \"crystal\"\n"
)

# Same shape as _CRYSTAL_GUARD, for the same reason (no GameVersion.isSilver()
# upstream). Kept separate rather than sharing a helper with the Crystal
# guard: the two guards are generated into the same main.lua independently
# (species_dex_text_silver may exist with no crystal_text_catalog and vice
# versa), and this project's other per-edition guards (mod.py's
# yellow_isyellow_guard_lines()) are each self-contained the same way.
_SILVER_GUARD = (
    '  local okSilverGame, SilverGameVersion = pcall(require, "src.core.GameVersion")\n'
    "  local silver_game_version = okSilverGame and type(SilverGameVersion) == \"table\"\n"
    "      and type(SilverGameVersion.get) == \"function\"\n"
    "      and SilverGameVersion.get() == \"silver\"\n"
)

# Same shape again, for the same reason -- the dialogue layer's own
# crystal_registration block (_CRYSTAL_GUARD above) and this dex-text layer
# are each generated independently (an empty crystal_text_catalog, e.g.
# Korean, still declares Crystal compatibility with no dialogue layer, while
# crystal_dex_text_catalog is keyed by species name and unaffected by that),
# so neither can assume the other's guard variables exist.
_CRYSTAL_DEX_GUARD = (
    '  local okCrystalDexGame, CrystalDexGameVersion = pcall(require, "src.core.GameVersion")\n'
    "  local crystal_dex_game_version = okCrystalDexGame and type(CrystalDexGameVersion) == \"table\"\n"
    "      and type(CrystalDexGameVersion.get) == \"function\"\n"
    "      and CrystalDexGameVersion.get() == \"crystal\"\n"
)

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
GS_CATALOG_HOOKS = {
    "dialogue": "mod.content.text:override(id, value)",
    "strings": "mod.content.strings:override(id, value)",
    "species_names": "mod.content.pokemon:patch(id, { name = value })",
    "species_kinds": "mod.content.pokemon:patch(id, { dexEntry = { kind = value } })",
    "species_dex_text": "mod.content.pokemon:patch(id, { dexEntry = { text = value } })",
    "species_dex_text2": "mod.content.pokemon:patch(id, { dexEntry = { text2 = value } })",
    "move_names": "mod.content.moves:patch(id, { name = value })",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { name = value })",
    "landmarks": "mod.content.landmarks:patch(id, { name = value })",
    "type_names": "mod.content.type_chart:patch(id, { name = value })",
    "status_labels": "mod.content.statuses:patch(id, { label = value })",
    "phone_contacts": "mod.content.phone_contacts:patch(id, { name = value })",
    "decorations": "mod.content.decorations:patch(id, { name = value })",
    "radio_channels": "mod.content.radio_channels:patch(id, { name = value })",
}
GS_CATALOG_HOOKS["ui_labels"] = None

# A small part of Gold's menu text is supplied as labels to existing mod
# hooks rather than through the engine Strings registry.  Keep the reviewed
# QID/segment recipes in config/gsc/literal_handlers.json; the runtime hook
# below only changes labels already exposed by gen1recomp's public hooks.
_GS_UI_HANDLER_PATH = Path(__file__).resolve().parents[1] / "config" / "gsc" / "literal_handlers.json"


def _load_gs_ui_handlers() -> dict[str, tuple[str, int, int | None]]:
    data = json.loads(_GS_UI_HANDLER_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != "gen1recomp-translation-mods/gs-literal-handlers" or data.get("version") != 1:
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


def _gs_ui_labels(corpus_rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Return corpus-backed labels used by already exposed Gold menu hooks."""
    rows = {qid: target for qid, _english, target in corpus_rows}
    result: dict[str, str] = {}
    for source, (qid, index, page) in _load_gs_ui_handlers().items():
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

GS_OAK_SPEECH_CATALOG = "oak_speech"
GS_OAK_SPEECH_KEYS = frozenset({
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
assert not (set(GS_CATALOG_HOOKS) & _MODKIT_GENERATED_MODULES), (
    "a GS_CATALOG_HOOKS name collides with modkit's GENERATED_MODULES "
    "and will make `modkit pack` fail under --strict; rename the catalog"
)

GS_REQUIRED_REGISTRIES = (
    "strings", "species_names", "species_kinds", "species_dex_text",
    "move_names", "item_names", "trainer_class_names", "landmarks",
    "type_names", "status_labels", GS_OAK_SPEECH_CATALOG,
)

GS_PINNED_REQUIRED_REGISTRIES = (
    "species_names", "species_kinds", "species_dex_text", "move_names",
    "item_names", "trainer_class_names", "landmarks", "strings",
    GS_OAK_SPEECH_CATALOG,
)

# These registries are available only on the Gen 2 upstream target.  They are
# verified when populated, but kept optional for the historical fixture path
# and for callers that build a minimal synthetic Gold worksheet.
GS_OPTIONAL_CONTENT_REGISTRIES = ("phone_contacts", "decorations", "radio_channels")

CRYSTAL_CATALOG_HOOKS = {
    "strings": "mod.content.strings:override(id, value)",
    # Upstream-dependent: v0.2.41 routes content.text to gen2Text, while
    # src/core/RomText reads data.text. The dedicated non-routed registry is
    # the only public API that reaches these seven extracted labels.
    "rom_text": "mod.content.rom_text:override(id, value)",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { name = value })",
    "landmarks": "mod.content.landmarks:patch(id, { name = value })",
}

# Present in every language's catalogs dict (build_gs_dialogue_mod always
# joins it) but not guaranteed non-empty: ja-Hrkt/ko's dex_entries_gold
# corpus rows never preserved a second #DEX description page (verified
# directly against poke-corpus -- ja-Hrkt has no "@" page marker at all, ko
# has only the row's own terminator), so species_dex_text2 is {} for those
# languages rather than a BuildError. Verified by the release gate like any
# required registry when it does have content (en/fr/de/es/it today).
GS_OPTIONAL_VERIFIED_REGISTRIES = ("species_dex_text2",)

def gs_mod_id(language: str) -> str:
    """Return the generation-scoped Gold mod identifier."""
    return f"translation-{canonical_language(language).lower()}-gen2"


def gs_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen2-{version}.zip"


def generate_gs_mod(
    destination: str | Path,
    mod_id: str | None = None,
    language: str = "fr",
    target_name: str | None = None,
    target_description: str | None = None,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
    text_catalog: dict[str, str] | None = None,
    extra_catalogs: dict[str, dict[str, str]] | None = None,
    crystal_text_catalog: dict[str, str] | None = None,
    crystal_catalogs: dict[str, dict[str, str]] | None = None,
    silver_dex_text_catalog: dict[str, str] | None = None,
    silver_dex_text2_catalog: dict[str, str] | None = None,
    crystal_dex_text_catalog: dict[str, str] | None = None,
    crystal_dex_text2_catalog: dict[str, str] | None = None,
) -> Path:
    """Write a deterministic Gold manifest, entry point, and catalogs.

    ``crystal_text_catalog``, when not None, declares this mod compatible
    with Crystal too (mandatory companion ROM, see build_gs()): its own
    dialogue pointers are written to a separate lang/dialogue_crystal.lua
    layer, applied only at runtime when GameVersion.get() == "crystal" (the
    same conditional-layer pattern pipeline/mod.py's RBY build uses for
    Yellow's own dialogue_yellow.lua). An empty dict still declares "crystal"
    compatibility with no translated layer (Korean: Crystal has no corpus for
    it, so its dialogue simply stays in English on a Crystal save).

    ``silver_dex_text_catalog``/``silver_dex_text2_catalog``, when non-empty,
    are Silver's OWN #DEX flavor-text pages (join_dex_entries_pages against
    the "dex_entries_silver" corpus category, not "dex_entries_gold"):
    verified directly against poke-corpus, Gold and Silver have a genuinely
    different description for every one of the 251 species (unlike the kind
    label, species/move/item/trainer names, which the two versions share
    verbatim). Written to their own lang/species_dex_text{,2}_silver.lua
    layer and applied only when GameVersion.get() == "silver", AFTER the
    unconditional "species_dex_text"/"species_dex_text2" catalogs above (a
    later mod.content.pokemon:patch on the same dexEntry field replaces the
    earlier one) -- so a Gold or Crystal save is unaffected and keeps Gold's
    text, and a Silver save ends up with Silver's own.

    ``crystal_dex_text_catalog``/``crystal_dex_text2_catalog`` are the same
    idea for Crystal's OWN #DEX flavor-text pages (join_dex_entries_pages
    against Crystal's own poke-corpus collection, category "dex_entries" --
    verified directly against poke-corpus, Crystal's kind label and named
    catalogs are identical to Gold/Silver's, but its flavor text is its own
    third, genuinely different description per species). Written to their
    own lang/species_dex_text{,2}_crystal.lua layer, applied only when
    GameVersion.get() == "crystal", AFTER the unconditional Gold catalogs --
    so a Gold or Silver save is unaffected, and a Crystal save ends up with
    Crystal's own text instead of Gold's.
    """
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    mod_id = mod_id or gs_mod_id(language)

    catalogs = {"dialogue": text_catalog or {}, **(extra_catalogs or {})}
    catalogs = {name: values for name, values in catalogs.items() if values}
    unknown = set(catalogs) - set(GS_CATALOG_HOOKS) - {GS_OAK_SPEECH_CATALOG}
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
            f'  each("{name}", function(id, value) {GS_CATALOG_HOOKS[name]} end)\n'
            for name in catalogs if name in GS_CATALOG_HOOKS and GS_CATALOG_HOOKS[name]
        )
        if "ui_labels" in catalogs:
            catalog_registration += _UI_LABEL_REGISTRATION
        if GS_OAK_SPEECH_CATALOG in catalogs:
            catalog_registration += _OAK_SPEECH_REGISTRATION

    crystal_registration = ""
    if crystal_text_catalog:
        if not lang_dir.is_dir():
            lang_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"-- Generated by the Crystal pipeline ({language}): dialogue", "return {"]
        lines.extend(
            f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(crystal_text_catalog.items())
        )
        lines.append("}")
        (lang_dir / "dialogue_crystal.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        crystal_registration = (
            "  -- Crystal layer: its own dialogue catalog, applied only when\n"
            "  -- running Pokemon Crystal (different bank:address pointers from\n"
            "  -- Gold/Silver's own dialogue catalog above).\n"
            + _CRYSTAL_GUARD
            + "  if crystal_game_version then\n"
            "    local function crystalCatalog()\n"
            '      local body = mod:read("lang/dialogue_crystal.lua")\n'
            "      if not body then return {} end\n"
            "      local chunk = loadstring(body)\n"
            "      if not chunk then return {} end\n"
            "      local ok, value = pcall(chunk)\n"
            "      return ok and type(value) == \"table\" and value or {}\n"
            "    end\n"
            "    for id, value in pairs(crystalCatalog()) do\n"
            '      if type(value) == "string" and value ~= "" then mod.content.text:override(id, value) end\n'
            "    end\n"
            "  end\n"
        )
    crystal_registry_registration = ""
    if crystal_catalogs is not None:
        unknown_crystal = set(crystal_catalogs) - set(CRYSTAL_CATALOG_HOOKS)
        if unknown_crystal:
            raise ValueError(f"no Crystal registry hook for catalog(s): {sorted(unknown_crystal)}")
        active_crystal_catalogs = {
            name: values for name, values in crystal_catalogs.items() if values
        }
        if active_crystal_catalogs:
            if not lang_dir.is_dir():
                lang_dir.mkdir(parents=True, exist_ok=True)
            loops = ""
            for name, values in active_crystal_catalogs.items():
                file_name = f"crystal_{name}"
                lines = [f"-- Generated by the Crystal pipeline ({language}): {name}", "return {"]
                lines.extend(
                    f"  [{lua_string(id_)}] = {lua_string(value)},"
                    for id_, value in sorted(values.items())
                )
                lines.append("}")
                (lang_dir / f"{file_name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
                loops += (
                    f'    for id, value in pairs(crystalRegistryCatalog("{file_name}")) do\n'
                    "      if type(value) == \"string\" and value ~= \"\" then "
                    + CRYSTAL_CATALOG_HOOKS[name] + " end\n"
                    "    end\n"
                )
            crystal_registry_registration = (
                "  -- Crystal-only named records, RomText labels and Strings keys.\n"
                "  -- The edition guard prevents every one from leaking into Gold/Silver.\n"
                + _CRYSTAL_GUARD
                + "  if crystal_game_version then\n"
                "    local function crystalRegistryCatalog(name)\n"
                '      local body = mod:read("lang/" .. name .. ".lua")\n'
                "      if not body then return {} end\n"
                "      local chunk = loadstring(body)\n"
                "      if not chunk then return {} end\n"
                "      local ok, value = pcall(chunk)\n"
                "      return ok and type(value) == \"table\" and value or {}\n"
                "    end\n"
                + loops
                + "  end\n"
            )
    silver_registration = ""
    silver_dex_text_layers = [
        ("species_dex_text_silver", silver_dex_text_catalog, "text"),
        ("species_dex_text2_silver", silver_dex_text2_catalog, "text2"),
    ]
    active_silver_layers = [
        (name, values, field) for name, values, field in silver_dex_text_layers if values
    ]
    if active_silver_layers:
        if not lang_dir.is_dir():
            lang_dir.mkdir(parents=True, exist_ok=True)
        silver_loops = ""
        for name, values, field in active_silver_layers:
            lines = [f"-- Generated by the Gold pipeline ({language}): {name}", "return {"]
            lines.extend(
                f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items())
            )
            lines.append("}")
            (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
            silver_loops += (
                f'    for id, value in pairs(silverDexTextCatalog("{name}")) do\n'
                '      if type(value) == "string" and value ~= "" then\n'
                f"        mod.content.pokemon:patch(id, {{ dexEntry = {{ {field} = value }} }})\n"
                "      end\n"
                "    end\n"
            )
        silver_registration = (
            "  -- Silver layer: its own #DEX flavor-text pages, applied only\n"
            "  -- when running Pokemon Silver -- Gold and Silver have\n"
            "  -- genuinely different Pokedex descriptions per species,\n"
            "  -- unlike every other named catalog above (which the two\n"
            "  -- versions share verbatim), so this patches over the\n"
            "  -- unconditional species_dex_text{,2} catalogs' Gold text.\n"
            + _SILVER_GUARD
            + "  if silver_game_version then\n"
            "    local function silverDexTextCatalog(name)\n"
            '      local body = mod:read("lang/" .. name .. ".lua")\n'
            "      if not body then return {} end\n"
            "      local chunk = loadstring(body)\n"
            "      if not chunk then return {} end\n"
            "      local ok, value = pcall(chunk)\n"
            "      return ok and type(value) == \"table\" and value or {}\n"
            "    end\n"
            + silver_loops
            + "  end\n"
        )
    crystal_dex_registration = ""
    crystal_dex_text_layers = [
        ("species_dex_text_crystal", crystal_dex_text_catalog, "text"),
        ("species_dex_text2_crystal", crystal_dex_text2_catalog, "text2"),
    ]
    active_crystal_dex_layers = [
        (name, values, field) for name, values, field in crystal_dex_text_layers if values
    ]
    if active_crystal_dex_layers:
        if not lang_dir.is_dir():
            lang_dir.mkdir(parents=True, exist_ok=True)
        crystal_dex_loops = ""
        for name, values, field in active_crystal_dex_layers:
            lines = [f"-- Generated by the Crystal pipeline ({language}): {name}", "return {"]
            lines.extend(
                f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items())
            )
            lines.append("}")
            (lang_dir / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
            crystal_dex_loops += (
                f'    for id, value in pairs(crystalDexTextCatalog("{name}")) do\n'
                '      if type(value) == "string" and value ~= "" then\n'
                f"        mod.content.pokemon:patch(id, {{ dexEntry = {{ {field} = value }} }})\n"
                "      end\n"
                "    end\n"
            )
        crystal_dex_registration = (
            "  -- Crystal layer: its own #DEX flavor-text pages, applied only\n"
            "  -- when running Pokemon Crystal -- Crystal has a genuinely\n"
            "  -- different Pokedex description per species from both Gold and\n"
            "  -- Silver, unlike every other named catalog above (which all\n"
            "  -- three versions share verbatim), so this patches over the\n"
            "  -- unconditional species_dex_text{,2} catalogs' Gold text.\n"
            + _CRYSTAL_DEX_GUARD
            + "  if crystal_dex_game_version then\n"
            "    local function crystalDexTextCatalog(name)\n"
            '      local body = mod:read("lang/" .. name .. ".lua")\n'
            "      if not body then return {} end\n"
            "      local chunk = loadstring(body)\n"
            "      if not chunk then return {} end\n"
            "      local ok, value = pcall(chunk)\n"
            "      return ok and type(value) == \"table\" and value or {}\n"
            "    end\n"
            + crystal_dex_loops
            + "  end\n"
        )
    main_body = (
        MAIN.replace("__TTF_REGISTRATION__", ttf_registration(language, font_source, font_profile))
        .replace("__CATALOG_REGISTRATION__", catalog_registration)
        .replace("__CRYSTAL_DIALOGUE_REGISTRATION__", crystal_registration)
        .replace("__CRYSTAL_REGISTRY_REGISTRATION__", crystal_registry_registration)
        .replace("__SILVER_DEX_TEXT_REGISTRATION__", silver_registration)
        .replace("__CRYSTAL_DEX_TEXT_REGISTRATION__", crystal_dex_registration)
    )
    (destination / "main.lua").write_text(main_body, encoding="utf-8")
    install_font_assets(destination, language, font_source, font_profile)

    games = ["gold", "silver"]
    if (crystal_text_catalog is not None or crystal_catalogs is not None
            or crystal_dex_text_catalog or crystal_dex_text2_catalog):
        games.append("crystal")
    display_name = target_name or (
        f"{language} translation for Gold, Silver and Crystal" if "crystal" in games
        else f"{language} translation for Gold and Silver"
    )
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
    # Silver-specific build. "crystal" (when crystal_text_catalog is given,
    # even empty, or either Crystal dex-text catalog is non-empty -- either
    # one alone still emits a real conditional layer below that only runs on
    # a Crystal save, so the manifest must declare compatibility for it to
    # ever load there) works the same way, plus its own conditional
    # dialogue_crystal.lua layer above -- Crystal's pointers mostly don't
    # exist in the base "dialogue" catalog at all (95.8% diverge from
    # Gold's), so without that separate layer a Crystal save would see
    # almost no translation from this mod.
    manifest_body = {
        "id": mod_id, "name": display_name, "version": project_version(), "api": 2,
        "entry": "main.lua", "profile": "content", "games": games,
        "game_version": ">=0.0.0-dev <1.0.0", "category": "LANGUAGE",
        "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [],
        "conflicts": [], "permissions": [], "description": description,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return destination


def package_gs_mod(
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
    archive_name = gs_archive_name(language, project_version())
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


def gs_text_catalog_from_join(entries: list[GsJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved.

    Also aliases the handful of pointers config/gsc/silver_pointer_aliases.json
    knows shift address between Gold and Silver for verbatim-identical text
    (see load_gold_silver_pointer_aliases's docstring): each Gold pointer's
    own resolved translation, whatever it ended up being, is reused under
    its Silver pointer too -- this mod already declares itself compatible
    with a Silver save (see the "games" field in generate_gs_mod below),
    and without this a Silver player would silently miss these 8 lines even
    though the exact same English text is translated for Gold.
    """
    catalog = {entry.pointer: entry.translation for entry in entries if entry.translation}
    for gold_pointer, silver_pointer in load_gold_silver_pointer_aliases().items():
        if gold_pointer in catalog:
            catalog[silver_pointer] = catalog[gold_pointer]
    return catalog


def gs_oak_speech_catalog_from_join(entries: list[GsJoinEntry]) -> dict[str, str]:
    """Return translated intro labels consumed by Gold's Oak speech hook."""
    return {
        entry.label: entry.translation
        for entry in entries
        if entry.label in GS_OAK_SPEECH_KEYS and entry.translation
    }


def _write_gate_expectations(
    mod_dir: Path,
    catalogs: dict[str, dict[str, str]],
    edition_dex_text: dict[str, dict[str, dict[str, str]]] | None = None,
    crystal_catalogs: dict[str, dict[str, str]] | None = None,
    engine_profile: str = UPSTREAM_PROFILE,
) -> Path:
    """Write a tiny, private expectation file consumed by the registry gate.

    ``edition_dex_text`` (optional) carries Silver's/Crystal's own #DEX
    flavor text -- e.g. ``{"silver": {"text": {...}, "text2": {...}}}`` --
    from build_gs_dialogue_mod's own local silver_text/silver_text2/
    crystal_text/crystal_text2. Unlike every catalog in ``catalogs``, these
    only apply behind a GameVersion-gated conditional layer (see
    generate_gs_mod's docstring), so the gate needs the same species id
    checked under "species_dex_text" above to prove that id's text actually
    changes on that edition's save, not merely that the base Gold catalog is
    correct.
    """
    profile = normalize_engine_profile(engine_profile)
    required_registries = (GS_REQUIRED_REGISTRIES if profile == UPSTREAM_PROFILE
                           else GS_PINNED_REQUIRED_REGISTRIES)
    optional = {"ui_labels", *GS_OPTIONAL_VERIFIED_REGISTRIES}
    if profile == UPSTREAM_PROFILE:
        optional.update(GS_OPTIONAL_CONTENT_REGISTRIES)
    if not set(catalogs) - optional >= set(required_registries):
        missing = sorted(set(required_registries) - set(catalogs))
        raise BuildError(
            "Gold registry gate expectations are incomplete"
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    extra = sorted(set(catalogs) - set(required_registries) - optional)
    if extra:
        raise BuildError(
            "Gold registry gate expectations are incomplete"
            + f"; unexpected: {', '.join(extra)}"
        )
    expected: dict[str, dict[str, str]] = {}
    optional_content = GS_OPTIONAL_CONTENT_REGISTRIES if profile == UPSTREAM_PROFILE else ()
    for name in (*required_registries, *GS_OPTIONAL_VERIFIED_REGISTRIES, *optional_content):
        values = catalogs.get(name)
        if name in (*GS_OPTIONAL_VERIFIED_REGISTRIES, *GS_OPTIONAL_CONTENT_REGISTRIES) and not values:
            continue
        if not isinstance(values, dict) or not values:
            raise BuildError(f"Gold registry gate expectation is empty: {name}")
        key = sorted(values)[0]
        value = values[key]
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise BuildError(f"Gold registry gate expectation is malformed: {name}")
        expected[name] = {"id": key, "value": value}
    base_id = expected["species_dex_text"]["id"]
    for edition, pages in (edition_dex_text or {}).items():
        for field, catalog_name in (("text", f"species_dex_text_{edition}"), ("text2", f"species_dex_text2_{edition}")):
            values = pages.get(field) or {}
            if not values:
                continue
            key = base_id if base_id in values else sorted(values)[0]
            value = values[key]
            if isinstance(key, str) and isinstance(value, str) and value:
                expected[catalog_name] = {"id": key, "value": value}
    unknown_crystal = set(crystal_catalogs or {}) - set(CRYSTAL_CATALOG_HOOKS)
    if unknown_crystal:
        raise BuildError(
            "Crystal registry gate expectations contain unknown catalogs: "
            + ", ".join(sorted(unknown_crystal))
        )
    for name, values in (crystal_catalogs or {}).items():
        if not values:
            continue
        key = sorted(values)[0]
        value = values[key]
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise BuildError(f"Crystal registry gate expectation is malformed: {name}")
        expected[f"crystal_{name}"] = {"id": key, "value": value}
    path = mod_dir.parent / f".{mod_dir.name}.registry-gate.json"
    path.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _write_dialogue_gate_expectation(
    mod_dir: Path, resolved_pointer: str, expected_translation: str, unresolved_pointer: str,
    crystal_resolved_pointer: str | None = None, crystal_expected_translation: str | None = None,
    crystal_unresolved_pointer: str | None = None,
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

    The three ``crystal_*`` fields are optional and, when all given, add
    Crystal's own dialogue layer (its own mostly-disjoint pointer space,
    merged into the same data.gen2Text via the same mod.content.text
    registry Gold/Silver's own dialogue above uses, but only registered
    under GameVersion=="crystal") to what the gate checks -- mirroring the
    resolved/unresolved/leak shape tools/gate_gs_registries.lua already
    checks for Crystal's registries, just never extended to dialogue.
    """
    expectation = {
        "resolved_pointer": resolved_pointer,
        "expected_translation": expected_translation,
        "unresolved_pointer": unresolved_pointer,
    }
    if crystal_resolved_pointer and crystal_expected_translation and crystal_unresolved_pointer:
        expectation.update({
            "crystal_resolved_pointer": crystal_resolved_pointer,
            "crystal_expected_translation": crystal_expected_translation,
            "crystal_unresolved_pointer": crystal_unresolved_pointer,
        })
    path = mod_dir.parent / f".{mod_dir.name}.dialogue-gate.json"
    path.write_text(
        json.dumps(expectation, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def run_gs_release_gates(
    mod_dir: str | Path,
    entries: list[GsJoinEntry],
    gen1recomp: str | Path,
    luajit: str,
    *,
    crystal_entries: list[GsJoinEntry] | None = None,
    catalogs: dict[str, dict[str, str]] | None = None,
    edition_dex_text: dict[str, dict[str, dict[str, str]]] | None = None,
    crystal_catalogs: dict[str, dict[str, str]] | None = None,
    coverage: dict | None = None,
    placeholder_decisions: dict[str, GsPlaceholderDecision] | None = None,
    engine_profile: str = UPSTREAM_PROFILE,
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
    profile = normalize_engine_profile(engine_profile)
    gen1recomp = Path(gen1recomp).resolve()
    coverage = coverage or gs_coverage_report(entries)

    tools = resource_root() / "tools"
    fixtures = tools / "gen2_gate_fixtures"
    for script in ("gate_gen2.lua", "gate_gs_dialogue.lua", "gate_gs_registries.lua"):
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
    unresolved_pointer = unresolved.pointer if unresolved else "__gs_unresolved_gate_pointer__"
    # Crystal's own dialogue is optional here the same way its registries
    # already are: a language with no Crystal corpus row at all (Korean)
    # or a build that resolved nothing still has to gate cleanly rather than
    # fail on a layer that legitimately has no content to check.
    crystal_translated = next((e for e in (crystal_entries or []) if e.translation), None)
    crystal_resolved_pointer = crystal_expected_translation = crystal_unresolved_pointer = None
    if crystal_translated is not None:
        crystal_unresolved = next((e for e in crystal_entries if e.translation is None), None)
        crystal_resolved_pointer = crystal_translated.pointer
        crystal_expected_translation = crystal_translated.translation
        crystal_unresolved_pointer = (
            crystal_unresolved.pointer if crystal_unresolved else "__crystal_unresolved_gate_pointer__"
        )
    dialogue_expectation_path = _write_dialogue_gate_expectation(
        mod_dir, translated.pointer, translated.translation, unresolved_pointer,
        crystal_resolved_pointer, crystal_expected_translation, crystal_unresolved_pointer,
    )
    try:
        _run([luajit, str(tools / "gate_gs_dialogue.lua"), str(gen1recomp), str(mod_dir),
              str(dialogue_expectation_path)], log_fn=log_fn)
    finally:
        dialogue_expectation_path.unlink(missing_ok=True)
    expectation_path = _write_gate_expectations(
        mod_dir, catalogs or {}, edition_dex_text, crystal_catalogs, profile,
    )
    try:
        command = [luajit, str(tools / "gate_gs_registries.lua"), str(gen1recomp), str(mod_dir), str(expectation_path)]
        command.append(profile)
        _run(command, log_fn=log_fn)
    finally:
        expectation_path.unlink(missing_ok=True)
    engine_revision = (
        str(project_config()["gen1recomp"]["revision"])
        if profile == PINNED_PROFILE else "upstream-local"
    )

    def coverage_summary(name: str) -> dict:
        section = coverage[name]
        return {
            key: section[key] for key in ("translated", "total", "percent", "source_revision")
            if key in section
        }

    validation = {
        "schema": 1,
        "policy": "english-fallback",
        "engine_profile": profile,
        "coverage": {
            **coverage["rom"],
            "ambiguous": len(coverage["ambiguous"]),
            "unmatched": len(coverage["unmatched"]),
            "ignored_markup_only": coverage["ignored_markup_only"],
            **({"engine": coverage_summary("engine")} if "engine" in coverage else {}),
            **({"engine_gen2": coverage_summary("engine_gen2")} if "engine_gen2" in coverage else {}),
            **({"crystal": coverage["crystal"]} if "crystal" in coverage else {}),
        },
        "checks": [
            {
                "tool": "pipeline.gs_join.audit_join",
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
                "tool": "tools/gate_gs_dialogue.lua",
                "version": engine_revision,
                "command": "luajit tools/gate_gs_dialogue.lua <gen1recomp> <mod> <expectation_json_path>",
                "status": "passed",
            },
            {
                "tool": "tools/gate_gs_registries.lua",
                "version": engine_revision,
                "command": "luajit tools/gate_gs_registries.lua <gen1recomp> <mod> <expectations>",
                "status": "passed",
            },
        ],
    }
    return {"coverage": coverage, "validation": validation}


def attach_gs_validation(mod_dir: str | Path, validation: dict) -> None:
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


def build_gs_dialogue_mod(
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
    crystal_text_catalog: dict[str, str] | None = None,
    crystal_corpus_dir: str | Path | None = None,
    crystal_catalogs: dict[str, dict[str, str]] | None = None,
    crystal_coverage: dict | None = None,
    engine_profile: str | None = None,
) -> tuple[Path, list[GsJoinEntry], dict]:
    """Join extracted Gold catalogs to the corpus and generate the mod.

    ``crystal_text_catalog`` and ``crystal_catalogs`` are passed through to
    generate_gs_mod(); their Crystal ROM extraction and corpus joins happen
    separately in build_gs().  The latter contains only edition-exclusive
    names, RomText labels and Strings keys, never shared Gold/Silver rows.

    ``crystal_corpus_dir``, when given, is poke-corpus's own Crystal/
    collection: used here (not in crystal_mod.py, unlike the dialogue join)
    because Crystal's #DEX flavor text is joined by species name against the
    same ``species`` list this function already extracts from Gold's own
    gs_species.tsv (Crystal's roster/ids are identical -- only the kind
    label and named catalogs are shared verbatim; the flavor text itself is
    genuinely Crystal's own, see generate_gs_mod()'s docstring). Missing
    corpus file for ``language`` (Crystal's collection has no Korean, unlike
    GoldSilver's) degrades to empty catalogs, same as
    crystal_mod.join_crystal_dialogue()'s own graceful degradation.
    """
    # A local checkout is never a profile selector by itself: ``engine_source``
    # only says where to look for Strings()/Strings.source() callsites, and
    # match_gs_engine_strings() verifies it against the pin itself unless the
    # caller explicitly opted into the upstream-local profile. The genuinely
    # upstream-only registries stay behind that profile check below, on their
    # own -- not by refusing ``engine_source`` outright, which used to make a
    # perfectly ordinary ``--gen1recomp <pinned checkout>`` build-gs
    # invocation raise.
    profile = normalize_engine_profile(engine_profile)
    gold_out_dir = Path(gold_out_dir)
    language = canonical_language(language)
    missing_or_empty = []
    for filename in gs_required_tsv(profile):
        path = gold_out_dir / filename
        if not path.is_file() or not any(line.strip() for line in path.read_text(encoding="utf-8").splitlines()):
            missing_or_empty.append(filename)
    if missing_or_empty:
        raise ValueError(
            "Gold release extraction is incomplete; required non-empty TSVs missing: "
            + ", ".join(missing_or_empty)
        )
    records = parse_gs_text_catalog(
        gold_out_dir / "gs_text.tsv", gold_out_dir / "gs_labels.tsv",
    )
    corpus_rows = read_corpus_rows(corpus_dir, target_lang=language)
    entries, stats = join_gs_pointers(
        records, corpus_rows, overrides=overrides,
        qid_decisions=load_gs_pointer_decisions(),
    )

    extra_catalogs: dict[str, dict[str, str]] = {}
    # Oak's speech has shipped through mod.hooks:wrap("intro.oak_speech.build")
    # since before this profile split existed and needs no upstream-only
    # registry, so it applies on every profile, not just upstream-local.
    oak_speech = gs_oak_speech_catalog_from_join(entries)
    if oak_speech:
        missing_oak = sorted(GS_OAK_SPEECH_KEYS - set(oak_speech))
        if missing_oak:
            raise ValueError("Gold Oak speech catalog is incomplete: " + ", ".join(missing_oak))
        extra_catalogs[GS_OAK_SPEECH_CATALOG] = oak_speech
    index_stats: dict[str, dict] = {}
    if profile_for(profile).supports_gen2_registries:
        # Built once and threaded through instead of each of the four qid-
        # exact-match catalogs below rescanning the whole GS corpus on its
        # own (the same class of redundant read this diff already fixed for
        # crystal_mod.py's joiners).
        corpus_index = _index_corpus(corpus_rows)
        type_names, type_stats = type_name_catalog(gold_out_dir / "gs_types.tsv", corpus_rows)
        status_labels, status_stats = status_label_catalog(corpus_rows, corpus_index=corpus_index)
        phone_contacts, phone_stats = phone_contact_catalog(corpus_rows, corpus_index)
        decorations, decoration_stats = decoration_catalog(corpus_rows, corpus_index)
        radio_channels, radio_stats = radio_channel_catalog(corpus_rows, corpus_index)
        extra_catalogs["type_names"] = type_names
        extra_catalogs["status_labels"] = status_labels
        extra_catalogs["phone_contacts"] = phone_contacts
        extra_catalogs["decorations"] = decorations
        extra_catalogs["radio_channels"] = radio_channels
        index_stats["type_names"] = type_stats
        index_stats["status_labels"] = status_stats
        index_stats["phone_contacts"] = phone_stats
        index_stats["decorations"] = decoration_stats
        index_stats["radio_channels"] = radio_stats
    species_path = gold_out_dir / "gs_species.tsv"
    species = parse_indexed_catalog(species_path)
    if not species:
        raise ValueError("gs_species.tsv contains no valid indexed entries")
    for catalog_name, tsv_name in (
        ("species_names", "gs_species.tsv"), ("move_names", "gs_moves.tsv"),
        ("item_names", "gs_items.tsv"), ("trainer_class_names", "gs_trainer_classes.tsv"),
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
    text_translations, text2_translations, text_stats, text2_stats = join_dex_entries_pages(
        species, corpus_rows, "dex_entries_gold",
    )
    extra_catalogs["species_dex_text"] = text_translations
    extra_catalogs["species_dex_text2"] = text2_translations
    index_stats["species_dex_text"] = text_stats
    index_stats["species_dex_text2"] = text2_stats
    # Silver's OWN #DEX flavor text (genuinely different prose from Gold's
    # for every species -- verified against the corpus). Kept OUT of
    # extra_catalogs/index_stats' generic unconditional-registration path
    # on purpose: it must only apply on a Silver save, so it is passed to
    # generate_gs_mod() separately and registered behind its own
    # GameVersion.get() == "silver" guard (see generate_gs_mod's docstring).
    silver_text, silver_text2, silver_text_stats, silver_text2_stats = join_dex_entries_pages(
        species, corpus_rows, "dex_entries_silver",
    )
    index_stats["species_dex_text_silver"] = silver_text_stats
    index_stats["species_dex_text2_silver"] = silver_text2_stats
    # Crystal's OWN #DEX flavor text, same idea as Silver's above but joined
    # against a wholly separate corpus collection (Crystal's pointers are
    # 95.8% divergent from Gold/Silver's, see crystal_mod.py's docstring,
    # but the #DEX text is joined by species name here, not by pointer, so
    # that divergence doesn't matter for this join). Only counted into
    # index_stats/coverage when a Crystal corpus was actually supplied --
    # like "engine"/"engine_gen2" below, a caller not passing one keeps the
    # exact same coverage totals as before this catalog existed.
    crystal_text: dict[str, str] = {}
    crystal_text2: dict[str, str] = {}
    if crystal_corpus_dir is not None:
        crystal_corpus_dir = Path(crystal_corpus_dir)
        if (crystal_corpus_dir / f"{language}_msg.txt").is_file():
            crystal_corpus_rows = read_corpus_rows(crystal_corpus_dir, target_lang=language)
            crystal_text, crystal_text2, crystal_text_stats, crystal_text2_stats = join_dex_entries_pages(
                species, crystal_corpus_rows, "dex_entries",
            )
        else:
            crystal_text_stats = crystal_text2_stats = {
                "total": len(species), "translated": 0, "no_corpus_entry": len(species),
            }
        index_stats["species_dex_text_crystal"] = crystal_text_stats
        index_stats["species_dex_text2_crystal"] = crystal_text2_stats
    landmarks_path = gold_out_dir / "gs_landmarks.tsv"
    landmarks = parse_indexed_catalog(landmarks_path)
    if not landmarks:
        raise ValueError("gs_landmarks.tsv contains no valid indexed entries")
    landmark_translations, landmark_stats = join_landmarks(landmarks, corpus_rows)
    extra_catalogs["landmarks"] = landmark_translations
    index_stats["landmarks"] = landmark_stats
    # match_gs_engine_strings() supports both profiles itself (a pinned
    # checkout is verified against the pin, an upstream-local one is only
    # trusted informationally) -- supports_engine_strings is true for both
    # today, so the practical gate is whether a checkout was given at all,
    # but the capability flag stays consulted rather than a raw profile
    # comparison so a future profile that lacks the capability is honored.
    if engine_source is not None and profile_for(profile).supports_engine_strings:
        engine_values, engine_coverage = match_gs_engine_strings(
            corpus_rows, engine_source, language, engine_profile=profile,
        )
        extra_catalogs["strings"] = engine_values
        stats.update(engine_coverage)
    # ui_labels reuses hooks already exposed on the pinned engine
    # (ui.pc.items, ui.options.rows, ...), same as oak_speech above.
    extra_catalogs["ui_labels"] = _gs_ui_labels(corpus_rows)
    stats["index_catalogs"] = index_stats
    stats["engine_profile"] = profile
    pointer_coverage = gs_coverage_report(entries)
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
    if crystal_coverage is not None:
        stats["coverage"]["crystal"] = crystal_coverage
    # Kept in-memory for the pre-publication registry gate; callers that
    # serialize stats can omit this private payload.
    stats["_gate_catalogs"] = extra_catalogs
    # Silver's and Crystal's own #DEX flavor text are deliberately excluded
    # from extra_catalogs above (they must only apply on their own edition's
    # save, not unconditionally), so the gate needs them threaded through
    # separately to actually exercise their GameVersion-gated patch loops
    # instead of only checking the unconditional Gold catalogs.
    stats["_gate_edition_dex_text"] = {
        "silver": {"text": silver_text, "text2": silver_text2},
        "crystal": {"text": crystal_text, "text2": crystal_text2},
    }
    stats["_gate_crystal_catalogs"] = crystal_catalogs or {}
    stats["_placeholder_decisions"] = load_gs_placeholder_decisions(language)

    mod_dir = generate_gs_mod(
        destination, mod_id=mod_id, language=language, target_name=target_name,
        target_description=target_description, font_source=font_source, font_profile=font_profile,
        text_catalog=gs_text_catalog_from_join(entries),
        extra_catalogs=extra_catalogs,
        crystal_text_catalog=crystal_text_catalog,
        crystal_catalogs=crystal_catalogs,
        silver_dex_text_catalog=silver_text,
        silver_dex_text2_catalog=silver_text2,
        crystal_dex_text_catalog=crystal_text,
        crystal_dex_text2_catalog=crystal_text2,
    )
    return mod_dir, entries, stats


def build_gs(
    gold_rom: str | Path,
    crystal_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
    font_profile: str = "fusion",
    engine_profile: str = PINNED_PROFILE,
    engine_source: str | Path | None = None,
) -> Path:
    """Run Gold's private extraction, join, validation, and packaging flow.

    ``crystal_rom`` is a mandatory companion ROM, like Yellow is for the
    "rby" release (pipeline.builder.build()'s own yellow_rom): one mod
    covers gold/silver/crystal, Crystal's own dialogue applied at runtime
    only when GameVersion.get() == "crystal" (see generate_gs_mod()).
    """
    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    try:
        engine_profile = validate_engine_profile_and_source(engine_profile, engine_source)
    except ValueError as exc:
        raise BuildError(str(exc)) from exc
    profile = release_profile("gsc")
    spec = game_spec("gs")
    if spec.corpus_collection not in profile.corpus_collections:
        raise BuildError("Gold release profile and game spec disagree on corpus collection")
    font_profile = validate_font_profile(language, font_profile)
    status("Validating ROMs")
    verify_gs_rom(gold_rom)
    verify_crystal_rom(crystal_rom)

    from .orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language,
        font_profile=font_profile,
        engine_source=engine_source,
    )
    workspace = context.workspace
    destination = context.destination

    status("Preparing dependencies")
    gen1recomp, corpus, font_source = context.gen1recomp, context.corpus, context.font_source
    corpus_gold_silver = corpus / "corpus" / "GoldSilver"
    corpus_crystal = corpus / "corpus" / "Crystal"

    log("\nExtracting private Gold ROM data...")
    status("Extracting private Gold ROM data")
    gold_out = workspace / "gold" / "extracted"
    import_gs_rom(gold_rom, gen1recomp, gold_out, log_fn=log_fn, engine_profile=engine_profile)

    log("\nExtracting private Crystal ROM data...")
    status("Extracting private Crystal ROM data")
    crystal_out = workspace / "crystal" / "extracted"
    import_crystal_rom(crystal_rom, gen1recomp, crystal_out, log_fn=log_fn, engine_profile=engine_profile)
    crystal_entries, crystal_stats = join_crystal_dialogue(crystal_out, corpus_crystal, language)
    crystal_text_catalog = crystal_text_catalog_from_join(crystal_entries)
    crystal_catalogs, crystal_feature_stats = crystal_feature_catalogs(
        crystal_out, corpus_crystal, language, engine_profile=engine_profile,
    )
    crystal_dialogue_coverage = {
        "translated": len(crystal_text_catalog),
        "total": crystal_stats["total"],
        "percent": round(100.0 * len(crystal_text_catalog) / crystal_stats["total"], 2)
        if crystal_stats["total"] else 100.0,
        "unresolved": crystal_stats["unresolved"],
        "no_match": crystal_stats["no_match"],
        "policy": "english-fallback",
    }
    crystal_coverage = {"dialogue": crystal_dialogue_coverage, **crystal_feature_stats}
    if crystal_stats["total"]:
        # len(crystal_text_catalog), not a hand-picked sum of stats
        # categories: join_gs_pointers() may grow new resolution categories
        # over time, and a hand-picked sum silently drifts out of sync with
        # what actually ships (this line undercounted twice already, first
        # omitting reviewed_qid then override).
        crystal_resolved = len(crystal_text_catalog)
        log(
            f"  crystal dialogue: {crystal_resolved}/{crystal_stats['total']} pointers"
            f" ({crystal_stats['unresolved']} unresolved, {crystal_stats['no_match']} no-match,"
            " left in English)"
        )

    build_root = workspace / "interactive-gs" / language
    mod_id = gs_mod_id(language)
    mod_dir = build_root / mod_id
    log("\nJoining corpus and generating the mod...")
    status("Joining corpus and generating the mod")
    mod_dir, entries, stats = build_gs_dialogue_mod(
        gold_out, corpus_gold_silver, mod_dir, mod_id=mod_id, language=language,
        target_name=f"{language_name} translation for Gold, Silver and Crystal", font_source=font_source, font_profile=font_profile,
        engine_source=gen1recomp,
        engine_profile=engine_profile, crystal_text_catalog=crystal_text_catalog,
        crystal_corpus_dir=corpus_crystal,
        crystal_catalogs=crystal_catalogs, crystal_coverage=crystal_coverage,
        overrides=load_gs_dialogue_overrides(language),
    )
    log(
        f"  text: {stats['unique'] + stats['harmless_ambiguous'] + stats['override'] + stats['reviewed_qid']}/{stats['total']} pointers"
        f" ({stats['unresolved']} unresolved, left in English)"
    )

    status("Running Gold release gates")
    gate_report = run_gs_release_gates(
        mod_dir, entries, gen1recomp, luajit,
        crystal_entries=crystal_entries,
        catalogs=stats.get("_gate_catalogs", {}),
        edition_dex_text=stats.get("_gate_edition_dex_text", {}),
        crystal_catalogs=stats.get("_gate_crystal_catalogs", {}),
        coverage=stats["coverage"],
        placeholder_decisions=stats.get("_placeholder_decisions", {}),
        engine_profile=engine_profile,
        log_fn=log_fn,
    )
    attach_gs_validation(mod_dir, gate_report["validation"])
    coverage_path = build_root / "coverage.json"
    coverage_path.write_text(
        json.dumps(gate_report["coverage"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for key, label in (
        ("rom", "Gold and Silver ROM aggregate"),
        ("engine_gen2", "Gold and Silver-related engine strings"),
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
    published = package_gs_mod(
        mod_dir, gen1recomp, modkit, build_root, destination, language=language, luajit=luajit, log_fn=log_fn,
    )
    status("Build complete")
    return published
