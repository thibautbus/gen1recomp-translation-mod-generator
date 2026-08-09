"""Generate a consumable Gen1Recomp translation mod and modkit worksheets."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import tempfile
from typing import Iterable

from .model import Alignment
from .generate import lua_string, display_value as _display
from .join import read_worksheets, join_catalogs
from .engine import read_engine_catalog, match_engine_catalog, load_engine_overrides, require_worksheets, ROM_CATALOGS
from .corpus import canonical_language
from .project import project_version
from .literals import load_recipes, generate_handlers

CATALOGS = ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels", "type_names", "demo_names")

# Whitelist at the catalog granularity: the optional reflow only ever
# applies to a catalog listed here. Everything else -- including
# "strings" (see the module docstring on pipeline.battle_scope) and any
# catalog added to CATALOGS later without being reviewed for this --
# keeps its ROM-original line breaks by default. "dialogue" is the only
# catalog whose values are free-flowing multi-line text (species/move/
# item/trainer/status/type names are short single-phrase labels with no
# <LINE>/<CONT> breaks to begin with).
#
# Inside "dialogue" itself, pipeline.battle_scope.reflow_safe_keys is a
# real per-qid whitelist, not a blocklist: a qid is only reflowed if a
# structural source positively proves it renders through the plain,
# generic text box (map/field/Pokédex text pointers, non-battle scripted
# show_text targets, trainer challenge/rematch lines). Measured against
# the real generated dialogue catalog (2582 qids), those sources cover
# ~52% of it; the rest keeps its ROM-original line breaks by default,
# whether it's genuinely risky (most of it likely is: battle-screen
# defeat lines for scripted, non-generic trainers, engine-hardcoded UI
# sequences like the intro OakSpeech screen) or just not yet audited.
_REFLOW_ELIGIBLE_CATALOGS = {"dialogue"}

# Shared between the standalone main.lua this module generates and the
# scaffold-splice injection in pipeline/builder.py's preserve_scaffold_support
# — one source of truth so the two injection sites cannot drift apart.
YELLOW_CATALOG_HOOKS: dict[str, str] = {
    "dialogue": 'each("dialogue_yellow", function(id, value) mod.content.text:override(id, value) end)',
    "strings": 'each("strings_yellow", function(id, value) mod.content.strings:override(id, value) end)',
    "species_names": 'each("species_names_yellow", function(id, value) mod.content.pokemon:patch(id, { name = value }) end)',
    "move_names": 'each("move_names_yellow", function(id, value) mod.content.moves:patch(id, { name = value }) end)',
    "item_names": 'each("item_names_yellow", function(id, value) mod.content.items:patch(id, { name = value }) end)',
    "trainer_names": 'each("trainer_names_yellow", function(id, value) mod.content.trainers:patch(id, { name = value }) end)',
    "status_labels": 'each("status_labels_yellow", function(id, value) mod.content.statuses:patch(id, { label = value }) end)',
}


def yellow_isyellow_guard_lines() -> str:
    """The shared ``GameVersion.isYellow()`` pcall guard, defining a local
    ``yellow_game_version`` boolean. Callers append their own
    ``if yellow_game_version then ... end`` block after this."""
    return (
        '  local okGame, GameVersion = pcall(require, "src.core.GameVersion")\n'
        "  local yellow_game_version = okGame and type(GameVersion) == \"table\"\n"
        "      and type(GameVersion.isYellow) == \"function\"\n"
        "      and GameVersion.isYellow()\n"
    )


def effective_yellow_engine_coverage(engine_rby: dict, yellow_dialogue: dict[str, str] | None) -> dict:
    """Add corpus-backed Yellow dialogue fallbacks to engine coverage."""
    direct_translated = int(engine_rby.get("translated", 0))
    total = int(engine_rby.get("total", 0))
    refusal = (yellow_dialogue or {}).get("_RefusingText")
    covered = int(bool(refusal and refusal != "{RAM:wNameBuffer}\nis refusing!"))
    effective_translated = min(total, direct_translated + covered)

    def metric(translated: int) -> dict:
        return {"translated": translated, "total": total,
                "percent": round(translated * 100 / total, 2) if total else 100.0}

    result = metric(effective_translated)
    result["covered_by_dialogue"] = covered
    return result


def yellow_coverage_metrics(stats: dict) -> dict:
    """Summarize Yellow corpus coverage for dialogue and named catalogs."""
    dialogue_total = int(stats.get(
        "effective_dialogue_total", stats.get("yellow_labels", 0)
    ))
    dialogue_translated = int(stats.get(
        "effective_dialogue_translated",
        max(0, dialogue_total - int(stats.get("unmatched", 0))),
    ))
    catalogs = stats.get("catalogs") or {}
    catalog_total = sum(int(value.get("total", 0)) for value in catalogs.values())
    catalog_translated = int(stats.get(
        "effective_named_catalog_translated",
        sum(int(value.get("matched", 0)) for value in catalogs.values()),
    ))

    def metric(translated: int, total: int) -> dict:
        return {"translated": translated, "total": total,
                "percent": round(translated * 100 / total, 2) if total else 100.0}

    dialogue = metric(dialogue_translated, dialogue_total)
    named_catalogs = metric(catalog_translated, catalog_total)
    diff_total = int(stats.get("layer_entries", 0))
    # `stats["unmatched"]` counts both unmatched-versioned-fallback labels
    # (which ARE present in the layer, as the ROM's own English text) and
    # unmatched yellow-only labels (which are NEVER added to the layer).
    # Subtracting the full count from `diff_total` therefore double-dips on
    # the yellow-only ones. `stats["matched"]` already is the exact count of
    # layer entries that carry a real translation, so use it directly.
    diff_translated = int(stats.get("matched", 0))
    return {
        "rom": metric(dialogue_translated + catalog_translated,
                       dialogue_total + catalog_total),
        "specific_diff": metric(diff_translated, diff_total),
        "dialogue": dialogue,
        "named_catalogs": named_catalogs,
    }


FONT_PROFILES = {
    "fusion": {
        "warning": None,
        "files": {
            "latin": ("fusion-pixel-10px-proportional-latin.ttf", 10),
            "ja": ("fusion-pixel-8px-proportional-ja.ttf", 8),
        },
        "licenses": (
            Path("OFL.txt"),
            Path("LICENSES/boutique-bitmap-9x9/OFL.txt"),
            Path("LICENSES/ark-pixel/OFL.txt"),
            Path("LICENSES/galmuri/LICENSE.txt"),
        ),
    },
    "pokemon": {
        "warning": "Pokemon Font is 8px; some translated text may overflow.",
        "files": {"latin": ("pokemon-font.ttf", 8), "ja": None},
        "licenses": (Path("LICENSES/pokemon-font/LICENSE.md"),),
    },
}


def _font_variant(language: str) -> str:
    return "ja" if canonical_language(language) == "ja-Hrkt" else "latin"


def validate_font_profile(language: str, font_profile: str = "fusion") -> str:
    profile = str(font_profile or "fusion").strip().lower()
    if profile not in FONT_PROFILES:
        raise ValueError(f"Unsupported font profile: {font_profile!r}")
    if _font_variant(language) == "ja" and FONT_PROFILES[profile]["files"]["ja"] is None:
        raise ValueError("Pokemon Font is only available for French, German, Spanish, and Italian.")
    return profile


def font_profile_warning(font_profile: str) -> str | None:
    profile = str(font_profile or "fusion").strip().lower()
    if profile not in FONT_PROFILES:
        raise ValueError(f"Unsupported font profile: {font_profile!r}")
    return FONT_PROFILES[profile]["warning"]


def plain_pixel_registration() -> str:
    return '  mod.content.font:register("ttf", {})'


def ttf_registration(
    language: str,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> str:
    """Return the selected font registration, or Plain Pixel without a source."""
    if font_source is None:
        return plain_pixel_registration()
    profile = validate_font_profile(language, font_profile)
    filename, size = FONT_PROFILES[profile]["files"][_font_variant(language)]
    return (
        '  mod.content.font:register("ttf", '
        f'{{ file = mod.assets:path("fonts/{filename}"), size = {size} }})'
    )


def _font_source_file(source_root: Path, relative: Path) -> Path:
    """Resolve a selected file from either a checkout or extracted archive."""
    direct = source_root / relative
    if direct.is_file():
        return direct
    candidates = [path for path in source_root.rglob(relative.name) if path.is_file()]
    if relative.name == "OFL.txt":
        candidates = [
            path for path in candidates
            if "Fusion Pixel Font" in path.read_text(encoding="utf-8", errors="replace")
        ] or candidates
    suffix = relative.as_posix()
    candidates = [path for path in candidates if path.as_posix().endswith(suffix)] or candidates
    if len(candidates) != 1:
        raise FileNotFoundError(f"font dependency file not found: {relative}")
    return candidates[0]


def install_font_assets(
    destination: Path,
    language: str,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> None:
    """Copy only the selected font and its applicable release notices."""
    if font_source is None:
        return
    source_root = Path(font_source)
    if not source_root.is_dir():
        raise FileNotFoundError(f"font dependency directory not found: {source_root}")
    profile = validate_font_profile(language, font_profile)
    variant = _font_variant(language)
    selected_files = [
        (relative, _font_source_file(source_root, relative))
        for relative in FONT_PROFILES[profile]["licenses"]
    ]
    selected, _ = FONT_PROFILES[profile]["files"][variant]
    selected_files.append((Path(selected), _font_source_file(source_root, Path(selected))))
    destination.mkdir(parents=True, exist_ok=True)
    target_root = destination / "fonts"
    temporary = Path(tempfile.mkdtemp(prefix=".fonts-", dir=destination))
    try:
        for relative, source in selected_files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        backup = destination.parent / f".{destination.name}.fonts-old"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        had_target = target_root.exists()
        if had_target:
            os.replace(target_root, backup)
        try:
            os.replace(temporary, target_root)
        except Exception:
            if had_target and backup.exists() and not target_root.exists():
                os.replace(backup, target_root)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(destination / "assets" / "fonts", ignore_errors=True)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _catalog_scope(classified: dict[str, dict], catalog: Iterable[str]) -> dict[str, dict]:
    """Limit reporting statistics to keys present in the generated catalog."""
    keys = set(catalog)
    return {key: info for key, info in classified.items() if key in keys}

# Keep every language at the same priority; language choice must not affect load order.
TRANSLATION_MOD_PRIORITY = 100
COMMANDS_SHOW_TEXT_KEYS = {
    "You can't carry\nany more items!",
    "{PLAYER} got\n%s!",
    "Do you want to\ngive a nickname\nto %s?",
}


def validate_commands_show_text_collisions(engine_values: dict[str, str], dialogue_keys: Iterable[str]) -> None:
    """Reject the three known Commands.show_text double-lookups before formatting."""
    keys = {str(key) for key in dialogue_keys}
    for source in COMMANDS_SHOW_TEXT_KEYS:
        value = engine_values.get(source, "")
        if value and value in keys:
            raise ValueError(
                f"Commands.show_text double lookup collision for {source!r}: "
                f"translated value {value!r} is a dialogue/Data.text key; "
                "upstream Commands.show_text API limitation"
            )


def catalog_for(qid: str) -> str:
    value = qid.lower()
    from .join import _type_name_tail, _base_qid, DEMO_NAMES_QIDS, SENDOUT_QID, POKEDEX_FOOTER_LABEL_QIDS
    if _base_qid(qid) == SENDOUT_QID:
        return "strings"
    if _base_qid(qid) in POKEDEX_FOOTER_LABEL_QIDS.values():
        return "strings"
    if _base_qid(qid) in DEMO_NAMES_QIDS.values():
        return "demo_names"
    if _type_name_tail(qid):
        return "type_names"
    if "status" in value or "condition" in value:
        return "status_labels"
    if "item" in value:
        return "item_names"
    if "move" in value or "attacknames" in value:
        return "move_names"
    if "trainer" in value or "opponent" in value:
        return "trainer_names"
    if "species" in value or "pokemon" in value or "dex" in value or "monsternames" in value:
        return "species_names"
    if qid.startswith("engine.") or "strings" in value:
        return "strings"
    return "dialogue"


def _catalog(rows: list[Alignment], title: str, language: str = "fr", reflow_line_breaks: bool = False, font_path: str | Path | None = None, font_size: int | None = None, reflow_safe: set[str] | None = None) -> str:
    from .battle_scope import is_reflow_safe_qid
    reflow_safe = reflow_safe or set()
    lines = [f"-- Generated by multilingual pipeline ({language}): {title}", "return {"]
    for row in sorted(rows, key=lambda item: item.qid):
        if row.translation is not None:
            row_reflow = reflow_line_breaks and is_reflow_safe_qid(row.qid, reflow_safe)
            lines.append(f"  [{lua_string(row.qid)}] = {lua_string(_display(row.translation, row_reflow, font_path, font_size))},")
    lines.append("}")
    return "\n".join(lines) + "\n"


MAIN = '''-- Generated translation mod; catalogs are safe to refresh.
return function(mod)
__TTF_REGISTRATION__
  local function catalog(name)
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
  each("dialogue", function(id, value) mod.content.text:override(id, value) end)
__YELLOW_DIALOGUE_REGISTRATION__
  each("strings", function(id, value) mod.content.strings:override(id, value) end)
  each("species_names", function(id, value) mod.content.pokemon:patch(id, {name = value}) end)
  each("move_names", function(id, value) mod.content.moves:patch(id, {name = value}) end)
  each("item_names", function(id, value) mod.content.items:patch(id, {name = value}) end)
  each("trainer_names", function(id, value) mod.content.trainers:patch(id, {name = value}) end)
  each("status_labels", function(id, value) mod.content.statuses:patch(id, {label = value}) end)
  -- Type names stay English in the type_chart registry so third-party mods
  -- that key colors/UI off TypeChart.displayName keep resolving, and are
  -- localized at draw time instead: every engine site renders the type name
  -- as a standalone Font.draw string, which is substituted below.
  local okType, TypeChart = pcall(require, "src.battle.TypeChart")
  local by_english = {}
  each("type_names", function(typeId, localized)
    if okType and TypeChart and type(TypeChart.displayName) == "function" then
      local canonical = TypeChart.displayName(typeId)
      if type(canonical) == "string" and canonical ~= "" and canonical ~= localized then
        by_english[canonical] = localized
      end
    end
  end)
  -- A few in-game Options values are returned directly by label helpers
  -- rather than through Strings (v0.1.69: COLORS, VIDEO MODE, VOID FILL,
  -- music filter, faithful resolution and game speed).  Keep this allowlist
  -- narrow so this does not become a general renderer rewrite. The desktop
  -- launcher uses its own Kit renderer and is intentionally unaffected.
  local raw_option_keys = {
    ["OG RED"] = true, ["OG BLUE"] = true, ["OG YELLOW"] = true,
    ["SGB"] = true, ["ADVANCED"] = true, ["OG INV"] = true,
    ["SGB INV"] = true, ["CLASSIC"] = true, ["GBC"] = true,
    ["WINDOWED"] = true, ["BORDERLESS"] = true,
    ["TREES"] = true, ["WATER"] = true, ["BLACK"] = true,
    ["OFF"] = true, ["1X"] = true, ["2X"] = true, ["3X"] = true,
    ["NORMAL"] = true,
  }
  local by_raw_option = {}
  each("strings", function(id, localized)
    if raw_option_keys[id] and localized ~= id then
      by_raw_option[id] = localized
    end
  end)
  if next(by_english) then
    local okFont, Font = pcall(require, "src.render.Font")
    if okFont and type(Font) == "table" then
      local function localize(text)
        if type(text) ~= "string" then return text end
        local localized = by_english[text]
        return type(localized) == "string" and localized or text
      end
      if type(Font.split) == "function" then
        local original_split = Font.split
        Font.split = function(text)
          return original_split(localize(text))
        end
      end
      if type(Font.draw) == "function" then
        local original_draw = Font.draw
        Font.draw = function(text, x, y, ...)
          return original_draw(localize(text), x, y, ...)
        end
      end
    end
  end
  -- Scope raw substitutions to OptionsMenu.draw. Font is shared by gameplay
  -- and third-party mods, so a global OFF/WATER/NORMAL replacement is unsafe.
  if next(by_raw_option) then
    local okOptions, OptionsMenu = pcall(require, "src.ui.OptionsMenu")
    local okFont, Font = pcall(require, "src.render.Font")
    if okOptions and type(OptionsMenu) == "table" and type(OptionsMenu.draw) == "function"
        and okFont and type(Font) == "table" then
      local original_options_draw = OptionsMenu.draw
      local function localizeRawOption(text)
        if type(text) ~= "string" then return text end
        return by_raw_option[text] or text
      end
      OptionsMenu.draw = function(self, ...)
        local original_split, original_draw = Font.split, Font.draw
        if type(original_split) == "function" then
          Font.split = function(text) return original_split(localizeRawOption(text)) end
        end
        if type(original_draw) == "function" then
          Font.draw = function(text, x, y, ...)
            return original_draw(localizeRawOption(text), x, y, ...)
          end
        end
        local ok, result = pcall(original_options_draw, self, ...)
        Font.split, Font.draw = original_split, original_draw
        if ok then return result end
        error(result, 0)
      end
    end
  end
  -- Engine hard-coded demo-battle thrower names: the old-man tutorial's
  -- "OLD MAN" (BattleState.makeOldManDemo default) and Yellow's Pallet-intro
  -- catch demo "PROF.OAK" (data/scripts/story2.lua), shown in the translated
  -- "%s used POKé BALL!" template (BattleState.oldManThrow).  The corpus-
  -- backed demo_names catalog maps the literals per language.
  --
  -- demoName itself must stay the canonical English literal: the engine may
  -- key sprite selection off it (Yellow's Pallet intro picks Prof. Oak's
  -- sprite when demoName == "PROF.OAK"), so the translation happens only at
  -- the render site below and is reverted right after.
  local demo_names = catalog("demo_names")
  local function localizedDemoName(self, name)
    if type(name) == "string" then
      local localized = demo_names and demo_names[name]
      if type(localized) == "string" and localized ~= "" then
        return localized
      end
      if name == "PROF.OAK" then
        -- last-resort fallback: the translated trainer record
        local trainers = self and self.game and self.game.data and self.game.data.trainers
        local oak = trainers and trainers.OPP_PROF_OAK
        if oak and type(oak.name) == "string" and oak.name ~= "" then
          return oak.name
        end
      end
    end
    return nil
  end
  local okDemo, BS = pcall(require, "src.battle.BattleState")
  if okDemo and type(BS) == "table" and type(BS.oldManThrow) == "function" then
    local original_oldManThrow = BS.oldManThrow
    BS.oldManThrow = function(self, ...)
      if type(self) == "table" then
        local canonical = self.demoName
        local localized = localizedDemoName(self, canonical)
        if type(localized) == "string" and localized ~= "" then
          self.demoName = localized
          local ok, result = pcall(original_oldManThrow, self, ...)
          self.demoName = canonical
          if ok then return result end
          error(result, 0)
        end
      end
      return original_oldManThrow(self, ...)
    end
  end
  -- Note: the Pallet-intro thrower sprite is NOT overridden here — with
  -- demoName kept canonical above, the engine itself selects Prof. Oak's
  -- back pic for that demo (vanilla behavior); a player.sprite override
  -- would clobber it with the front trainer pic.
  local literal_body = mod:read("lang/literal_handlers.lua")
  if literal_body then
    local chunk, err = loadstring(literal_body, "lang/literal_handlers.lua")
    if not chunk then error(err) end
    local setup = chunk()
    if type(setup) ~= "function" then error("literal_handlers.lua must return a function") end
    setup(mod)
  end
end
'''


def generate_mod(items: Iterable[Alignment], destination: str | Path, mod_id: str = "translation-fr", language: str = "fr", modkit_worksheet: str | Path | None = None, report_path: str | Path | None = None, engine_catalog: str | Path | None = None, engine_overrides: str | Path | None = None, strict_engine: bool = False, semantic_anchors: str | Path | None = None, semantic_anchor_decisions: str | Path | None = None, target_name: str | None = None, literal_handlers: str | Path | None = None, target_description: str | None = None, engine_source: str | Path | None = None, engine_scope: str | Path | None = None, font_source: str | Path | None = None, font_profile: str = "fusion", yellow_dialogue: dict[str, str] | None = None, yellow_stats: dict | None = None, yellow_catalogs: dict[str, dict[str, str]] | None = None, yellow_engine_overrides: dict[str, str] | None = None, precomputed_join: tuple[dict, dict] | None = None, reflow_line_breaks: bool = False) -> Path:
    """Generate a mod; ``strict_engine`` requires scaffold/catalog presence only.

    It does not require complete engine translations: unresolved entries remain
    empty and the game uses its English fallback.

    ``precomputed_join`` lets a caller that already ran ``join_catalogs`` on
    the same ``items``/``modkit_worksheet`` pass the ``(joined, join_report)``
    result through instead of paying for the same match pass twice — the
    universal-mod builder needs this join anyway to diff against the Yellow
    catalogs. Ignored when ``modkit_worksheet`` is not given.
    """
    language = canonical_language(language)
    font_profile = validate_font_profile(language, font_profile)
    pacing_font_path: Path | None = None
    pacing_font_size: int | None = None
    if reflow_line_breaks and font_source is not None:
        file_info = FONT_PROFILES[font_profile]["files"][_font_variant(language)]
        if file_info is not None:
            pacing_filename, pacing_font_size = file_info
            pacing_font_path = _font_source_file(Path(font_source), Path(pacing_filename))
    # Two whitelists, nested: _REFLOW_ELIGIBLE_CATALOGS (see top of file)
    # gates entire catalogs -- only "dialogue" is reflow-eligible at all,
    # everything else (including "strings", the engine-literal catalog)
    # keeps its ROM-original line breaks with no computation needed here.
    # Inside that catalog, reflow_safe_keys is a real per-qid whitelist
    # (see pipeline.battle_scope's module docstring): a qid is only
    # reflowed if some structural source positively proves it renders
    # through the plain, generic text box; a qid no source names -- known
    # risk or not -- keeps its ROM-original line breaks by default.
    reflow_safe: set[str] = set()
    if reflow_line_breaks and engine_source is not None:
        from .battle_scope import reflow_safe_keys
        reflow_safe |= reflow_safe_keys(engine_source)
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    existing_registration = None
    existing_main = destination / "main.lua"
    if font_source is None and existing_main.is_file():
        for line in existing_main.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith('mod.content.font:register("ttf"'):
                existing_registration = line
                break
    rows = list(items)
    joined = None
    join_report = None
    engine_report = None
    engine_values = None
    worksheets = None
    if modkit_worksheet and strict_engine:
        require_worksheets(modkit_worksheet)
    if modkit_worksheet:
        worksheets = read_worksheets(modkit_worksheet)
        if precomputed_join is not None:
            joined, join_report = precomputed_join
        else:
            joined, join_report = join_catalogs(rows, worksheets, language)
        if engine_catalog is None and strict_engine:
            engine_catalog = Path(modkit_worksheet) / "strings.lua"
    if engine_catalog:
        from .engine_scope import engine_dynamic_values, forced_dynamic_keys, load_scope
        scope = load_scope(engine_scope) if engine_scope else load_scope()
        from .join import ENGINE_CATALOG_EXTRA_KEYS
        catalog = read_engine_catalog(engine_catalog)
        for key in forced_dynamic_keys(scope):
            catalog.setdefault(key, "")
        for key in engine_dynamic_values(scope):
            catalog.setdefault(key, "")
        for key in ENGINE_CATALOG_EXTRA_KEYS:
            catalog.setdefault(key, "")
        overrides = load_engine_overrides(engine_overrides)
        stale_overrides = sorted(set(overrides) - set(catalog))
        if stale_overrides:
            raise ValueError(f"engine overrides contain {len(stale_overrides)} unknown key(s): {stale_overrides!r}")
        engine_values, engine_report = match_engine_catalog(catalog, rows, overrides, semantic_anchors=semantic_anchors, semantic_anchor_decisions=semantic_anchor_decisions, target_lang=language)
        for key, dynamic in scope.get("forced_dynamic_keys", {}).items():
            if key in engine_values:
                engine_report["details"][key] = "forced_dynamic"
                engine_report["provenance"][key] = {
                    **engine_report["provenance"].get(key, {}),
                    "provenance": dynamic["provenance"],
                    "reason": dynamic["reason"],
                    "callsite": dynamic["callsite"],
                    "qid": dynamic["qid"],
                }
        empty_keys = {key for key, info in scope.get("key_scope_overrides", {}).items() if info.get("engine_empty")}
        for key in empty_keys & set(catalog):
            was_unmatched = key in engine_report.get("unmatched", [])
            if engine_values.get(key):
                engine_report["translated"] -= 1
            engine_report["details"][key] = "covered_by_rom"
            engine_report["provenance"][key] = {"method": "covered-by-rom", "reason": "ROM/Data.text dialogue owns localized text"}
            engine_report["unmatched"] = [item for item in engine_report.get("unmatched", []) if item != key]
            engine_report.get("ambiguous", {}).pop(key, None)
            if was_unmatched:
                engine_report["fallback_english"] = max(0, engine_report.get("fallback_english", 0) - 1)
            engine_values[key] = ""
            # The key renders localized via the dialogue (data.text), so even
            # though strings.lua stays empty it counts as translated.
            engine_report["translated"] += 1
            engine_report["covered_by_rom"] = engine_report.get("covered_by_rom", 0) + 1
        engine_report["percent"] = round(engine_report["translated"] * 100 / engine_report["total"], 2) if engine_report["total"] else 100.0
        if worksheets is not None:
            validate_commands_show_text_collisions(engine_values, (entry.key for entry in worksheets.get("dialogue", ())))
    # The trainer send-out templates (strings.lua) are qid-driven from one
    # corpus row (see pipeline/join.py) and merged into the engine strings,
    # so they ship even without a worksheet.
    from .join import sendout_strings_catalog, pokedex_footer_catalog, romtext_fallback_catalog, enemy_qualifier_catalog
    sendout_values, sendout_report = sendout_strings_catalog(rows, language)
    pokedex_values, pokedex_report = pokedex_footer_catalog(rows, language)
    if engine_values is None:
        engine_values = {}
    qid_values = {**sendout_values, **pokedex_values}
    engine_values.update(qid_values)
    romtext_values, romtext_report = romtext_fallback_catalog(engine_values, rows, language)
    qid_values.update(romtext_values)
    engine_values.update(romtext_values)
    enemy_values, enemy_report = enemy_qualifier_catalog(rows, language)
    qid_values.update(enemy_values)
    engine_values.update(enemy_values)
    # The qid-driven catalogs above inject translated values AFTER the matcher
    # ran, so the engine report still lists those keys as unmatched (or omits
    # them).  Sync the report so "All engine strings" reflects what ships.
    if engine_report is not None:
        unresolved_methods = {None, "english_fallback", "semantic_ambiguous", "semantic_unresolved", "ambiguous", "structural_incompatible"}
        for key, value in qid_values.items():
            if not (isinstance(value, str) and value):
                continue
            if engine_report["details"].get(key) in unresolved_methods:
                if key in engine_report.get("unmatched", []):
                    engine_report["unmatched"] = [item for item in engine_report["unmatched"] if item != key]
                    engine_report["fallback_english"] = max(0, engine_report.get("fallback_english", 0) - 1)
                engine_report.get("ambiguous", {}).pop(key, None)
                engine_report["translated"] += 1
            engine_report["details"][key] = "qid-driven"
            engine_report["provenance"][key] = {"method": "qid-driven", "reason": "corpus qid merged after matching (sendout/pokedex/romtext/enemy)"}
        engine_report["percent"] = round(engine_report["translated"] * 100 / engine_report["total"], 2) if engine_report["total"] else 100.0
    (destination / "lang").mkdir(exist_ok=True)
    for name in CATALOGS:
        catalog_reflow = reflow_line_breaks and name in _REFLOW_ELIGIBLE_CATALOGS
        if name == "strings" and engine_values:
            lines = [f"-- Generated by multilingual pipeline ({language}): strings", "return {"]
            lines.extend(f"  [{lua_string(key)}] = {lua_string(_display(value, catalog_reflow, pacing_font_path, pacing_font_size))}," for key, value in engine_values.items())
            lines.append("}")
            body = "\n".join(lines) + "\n"
        elif name == "demo_names" and joined is None:
            # Engine hard-coded demo names are qid-driven (see pipeline/join.py).
            from .join import demo_names_catalog
            values, _ = demo_names_catalog(rows, language)
            lines = [f"-- Generated by multilingual pipeline ({language}): demo_names", "return {"]
            lines.extend(f"  [{lua_string(key)}] = {lua_string(_display(value, catalog_reflow and key in reflow_safe, pacing_font_path, pacing_font_size))}," for key, value in sorted(values.items()))
            lines.append("}")
            body = "\n".join(lines) + "\n"
        elif name == "type_names" and joined is None:
            # Without a worksheet the join is purely qid-driven (see
            # pipeline/join.py); keys are the engine's type_chart ids.
            from .join import type_names_catalog
            values, _ = type_names_catalog(rows, language)
            lines = [f"-- Generated by multilingual pipeline ({language}): type_names", "return {"]
            lines.extend(f"  [{lua_string(key)}] = {lua_string(_display(value, catalog_reflow and key in reflow_safe, pacing_font_path, pacing_font_size))}," for key, value in sorted(values.items()))
            lines.append("}")
            body = "\n".join(lines) + "\n"
        elif joined is not None:
            lines = [f"-- Generated by multilingual pipeline ({language}): {name}", "return {"]
            lines.extend(f"  [{lua_string(key)}] = {lua_string(_display(value, catalog_reflow and key in reflow_safe, pacing_font_path, pacing_font_size))}," for key, value in sorted(joined[name].items()))
            lines.append("}")
            body = "\n".join(lines) + "\n"
        else:
            grouped = [row for row in rows if catalog_for(row.qid) == name]
            body = _catalog(grouped, name, language, catalog_reflow, pacing_font_path, pacing_font_size, reflow_safe)
        (destination / "lang" / f"{name}.lua").write_text(body, encoding="utf-8")
    yellow_catalogs = dict(yellow_catalogs or {})
    if yellow_dialogue:
        yellow_catalogs["dialogue"] = yellow_dialogue
    if yellow_engine_overrides:
        yellow_catalogs["strings"] = {
            **yellow_catalogs.get("strings", {}),
            **yellow_engine_overrides,
        }
    for name in CATALOGS:
        path = destination / "lang" / f"{name}_yellow.lua"
        values = yellow_catalogs.get(name, {})
        catalog_reflow = reflow_line_breaks and name in _REFLOW_ELIGIBLE_CATALOGS
        if values:
            lines = [f"-- Generated by multilingual pipeline ({language}): {name}_yellow", "return {"]
            lines.extend(f"  [{lua_string(key)}] = {lua_string(_display(value, catalog_reflow and key in reflow_safe, pacing_font_path, pacing_font_size))}," for key, value in sorted(values.items()))
            lines.append("}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    # Literal handlers are emitted only when every branch has a proven target
    # qid.  The runtime file is deliberately separate so an interactive build
    # can retain Modkit's scaffold main and load this optional contribution.
    literal_path = Path(literal_handlers) if literal_handlers else Path(__file__).resolve().parents[1] / "config" / "literal_handlers.json"
    runtime = destination / "lang" / "literal_handlers.lua"
    recipes = []
    generated_handlers = []
    if literal_path.is_file():
        recipes = load_recipes(literal_path)
        _, generated_handlers = generate_handlers(rows, recipes, runtime, reflow_line_breaks=reflow_line_breaks, font_path=pacing_font_path, font_size=pacing_font_size, reflow_safe=reflow_safe)
    elif runtime.exists():
        runtime.unlink()
    main_body = MAIN.replace(
        "__TTF_REGISTRATION__",
        existing_registration or ttf_registration(language, font_source, font_profile),
    )
    if yellow_catalogs or yellow_engine_overrides:
        yellow_hook = (
            "  -- Yellow layer: versioned and Yellow-only catalogs, applied only\n"
            "  -- when running Pokemon Yellow.\n"
            + yellow_isyellow_guard_lines()
            + "  if yellow_game_version then\n"
        )
        for name in CATALOGS:
            if name in yellow_catalogs:
                yellow_hook += f"    {YELLOW_CATALOG_HOOKS[name]}\n"
        yellow_hook += "  end\n"
        main_body = main_body.replace("__YELLOW_DIALOGUE_REGISTRATION__", yellow_hook)
    else:
        main_body = main_body.replace("__YELLOW_DIALOGUE_REGISTRATION__", "")
    # Install the selected assets only after all catalog, override, and
    # handler validation above has succeeded.  The installer swaps fonts
    # atomically, so a missing source leaves an existing refresh untouched.
    install_font_assets(destination, language, font_source, font_profile)
    (destination / "main.lua").write_text(main_body, encoding="utf-8")
    worksheet_root = Path(str(destination) + "-worksheet")
    worksheet_root.mkdir(parents=True, exist_ok=True)
    for name in CATALOGS:
        grouped = [row for row in rows if catalog_for(row.qid) == name]
        lines = [f"# {name} worksheet (source, not distributable)"]
        lines.extend(f"{row.qid}\t{row.english.text}" for row in sorted(grouped, key=lambda item: item.qid))
        (worksheet_root / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    import json
    display_name = target_name or f"{language} translation"
    description = target_description or (
        f"{display_name} for Pokémon Red, Blue and Yellow, based mostly on PokeCorpus. "
        "Some engine-specific text remains untranslated."
    )
    manifest_body = {"id": mod_id, "name": display_name, "version": project_version(), "api": 2, "entry": "main.lua", "profile": "content", "game_version": ">=0.0.0-dev <1.0.0", "category": "GAMEPLAY", "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [], "conflicts": [], "description": description}
    manifest = json.dumps(manifest_body, ensure_ascii=False, indent=2) + "\n"
    (destination / "manifest.json").write_text(manifest, encoding="utf-8")
    if report_path and join_report is not None:
        import json
        # A recipe is one reachable handler, regardless of how many qid
        # branches it contains.  Count qids separately for provenance; the
        # previous fixed ``* 2`` multiplier overstated coverage for flow
        # recipes and counted unavailable branches as translated.
        literal_total = len({
            (recipe.get("map"), recipe.get("text_constant"))
            for recipe in recipes if isinstance(recipe, dict)
        })
        literal_translated = len({(h.map_id, h.text_constant) for h in generated_handlers})
        def recipe_qids(handler):
            if getattr(handler, "flow_qids", ()):
                return set(handler.flow_qids)
            return {handler.prompt_qid, handler.yes_qid, handler.no_qid}
        literal_qids_total = set()
        for recipe in recipes:
            flow = recipe.get("flow") if isinstance(recipe, dict) else None
            if flow is not None:
                from .literals import _flow_qids
                try:
                    literal_qids_total.update(_flow_qids(flow))
                except ValueError:
                    continue
            else:
                for branch in ("prompt", "yes", "no"):
                    value = recipe.get(branch, {}) if isinstance(recipe, dict) else {}
                    if isinstance(value, dict) and isinstance(value.get("qid"), str):
                        literal_qids_total.add(value["qid"])
        literal_qids_translated = set().union(*(recipe_qids(handler) for handler in generated_handlers)) if generated_handlers else set()
        type_names = joined.get("type_names", {}) if joined is not None else {}
        type_names_total = len(type_names)
        type_names_translated = join_report.get("matched", {}).get("type_names", 0)
        rom_total = (sum(len(read_worksheets(modkit_worksheet)[name]) for name in ROM_CATALOGS) if modkit_worksheet else 0) + literal_total + type_names_total
        rom_translated = sum(join_report.get("matched", {}).get(name, 0) for name in ROM_CATALOGS) + literal_translated + type_names_translated
        report = dict(join_report)
        rom_details = {name: {"translated": join_report.get("matched", {}).get(name, 0), "total": len(read_worksheets(modkit_worksheet)[name])} for name in ROM_CATALOGS} if modkit_worksheet else {}
        rom_details["literal_handlers"] = {
            "translated": literal_translated, "total": literal_total,
            "qids_translated": len(literal_qids_translated),
            "qids_total": len(literal_qids_total),
        }
        rom_details["type_names"] = {
            "translated": type_names_translated, "total": type_names_total,
            "excluded": (join_report.get("type_names") or {}).get("excluded"),
        }
        demo_names = joined.get("demo_names", {}) if joined is not None else {}
        demo_names_total = len(demo_names)
        demo_names_translated = join_report.get("matched", {}).get("demo_names", 0)
        rom_total += demo_names_total
        rom_translated += demo_names_translated
        rom_details["demo_names"] = {
            "translated": demo_names_translated, "total": demo_names_total,
            "qids": (join_report.get("demo_names") or {}).get("qids"),
        }
        strings_sendout_total = len(sendout_values)
        strings_sendout_translated = sendout_report["translated"]
        rom_total += strings_sendout_total
        rom_translated += strings_sendout_translated
        rom_details["strings_sendout"] = {
            "translated": strings_sendout_translated, "total": strings_sendout_total,
            "qids": sendout_report.get("qids"),
        }
        strings_pokedex_total = len(pokedex_values)
        strings_pokedex_translated = len(pokedex_values)
        rom_total += strings_pokedex_total
        rom_translated += strings_pokedex_translated
        rom_details["strings_pokedex"] = {
            "translated": strings_pokedex_translated, "total": strings_pokedex_total,
        }
        strings_romtext_total = len(romtext_values)
        strings_romtext_translated = romtext_report["translated"]
        rom_total += strings_romtext_total
        rom_translated += strings_romtext_translated
        rom_details["strings_romtext"] = {
            "translated": strings_romtext_translated, "total": strings_romtext_total,
        }
        strings_enemy_total = len(enemy_values)
        strings_enemy_translated = enemy_report["translated"]
        rom_total += strings_enemy_total
        rom_translated += strings_enemy_translated
        rom_details["strings_enemy"] = {
            "translated": strings_enemy_translated, "total": strings_enemy_total,
        }
        report["rom"] = {"translated": rom_translated, "total": rom_total, "percent": round(rom_translated * 100 / rom_total, 2) if rom_total else 100.0, "details": rom_details}
        if engine_report is not None:
            report["engine"] = engine_report
            if engine_source:
                from .engine_scope import classify_catalog, coverage_metadata, iter_callsites, validate_catalog_universe, verified_source
                source_path, _, _ = verified_source(engine_source, scope)
                validate_catalog_universe(catalog.keys(), source_path, scope)
                classified = classify_catalog(catalog.keys(), iter_callsites(source_path), scope)
                report_scope = _catalog_scope(classified, catalog)
                eligible = {key for key, info in report_scope.items() if info["eligibility"] == "eligible"}
                translated_keys = {key for key, value in engine_values.items() if isinstance(value, str) and value}
                translated = len(eligible & translated_keys)
                categories = {}
                for info in report_scope.values():
                    categories[info["category"]] = categories.get(info["category"], 0) + 1
                report["engine_rby"] = {
                    **coverage_metadata(scope),
                    "translated": translated,
                    "total": len(eligible),
                    "percent": round(translated * 100 / len(eligible), 2) if eligible else 100.0,
                    "eligibility": {"eligible": len(eligible), "review": sum(i["eligibility"] == "review" for i in report_scope.values()), "ineligible": sum(i["eligibility"] == "ineligible" for i in report_scope.values())},
                    "categories": categories,
                    "catalog_total": len(catalog),
                }
            else:
                report["engine_rby_warning"] = "Gen1Recomp engine source unavailable; RBY engine coverage omitted."
        if yellow_stats is not None:
            report["yellow"] = {
                "layer": yellow_stats,
                "note": "Yellow catalog layers: versioned, translation-variant and Yellow-only values, applied when GameVersion.isYellow()",
            }
            report["yellow"]["coverage"] = yellow_coverage_metrics(yellow_stats)
            if "engine_rby" in report:
                effective = effective_yellow_engine_coverage(
                    report["engine_rby"], yellow_dialogue
                )
                report["engine_rby"].update({
                    key: effective[key] for key in ("translated", "total", "percent")
                })
                report["yellow"]["engine_coverage_provenance"] = {
                    "covered_by_dialogue": effective["covered_by_dialogue"],
                    "note": "Corpus-backed Yellow dialogue fallbacks are included in engine coverage.",
                }
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination
