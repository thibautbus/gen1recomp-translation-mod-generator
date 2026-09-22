"""Build the Red/Blue and Yellow translation mod.

The Red/Blue side of the interactive builder (pipeline/shared/builder.py):
assemble the Modkit worksheets, join the corpus, merge the Red/Blue and
Yellow engine overrides, generate the mod and its Yellow layer, and keep the
scaffold's support files.  The builder's prompts, pinned dependencies and
archive publishing stay shared; Gold/Silver/Crystal and FireRed build in
pipeline/gsc/mod.py and pipeline/frlg/mod.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Callable

from ..shared.align import align, apply_corpus_overrides
from ..shared.corpus import canonical_language, parse_redblue, parse_yellow
from .mod import YELLOW_CATALOG_HOOKS, generate_mod, yellow_isyellow_guard_lines
from ..shared.mod_assets import ttf_registration, validate_font_profile
from ..shared.project import is_frozen, project_version, resource_root
from ..shared.roms import import_rom, verify_rb_rom, verify_rom
from ..shared.leak_audit import audit_generated_catalogs
from ..shared.specs import release_profile
from ..shared.engine_profile import (
    PINNED_PROFILE, UPSTREAM_PROFILE, normalize_engine_profile, validate_engine_profile_and_source,
)
from ..shared.builder import BuildError, _modkit_command, _run


def load_yellow_coverage_exceptions(path: str | Path) -> dict[str, frozenset[str]]:
    """Load ``config/rby/yellow_coverage_exceptions.json`` reviewed exceptions.

    Mirrors the review discipline of ``config/rby/semantic_anchor_decisions.json``:
    each entry is a human-reviewed exception, not a blind override.  See that
    file's ``description`` for why this stays a separate, smaller schema.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        language: frozenset(labels)
        for language, labels in (data.get("entries") or {}).items()
    }


# Anchor for scaffold-splice injections that append after the status_labels
# patch (type_names, species_kinds): the last catalog the scaffold itself
# emits, so anything appended here runs after every scaffold-owned patch.
STATUS_LABELS_BLOCK = '  counts.statuses = each("status_labels", function(id, value)\n    mod.content.statuses:patch(id, { label = value })\n  end)'


def _language_override_path(language: str, game: str, filename: str) -> Path | None:
    path = resource_root() / "overrides" / language / game / filename
    return path if path.is_file() else None


def _corpus_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "rby", "corpus.json")


def _engine_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "rby", "engine.json")


def _rby_engine_override_paths(
    language: str, engine_profile: str | None = None,
) -> tuple[Path, ...]:
    """Return RBY override layers for one explicit engine profile.

    The ordinary ``engine.json`` is the pinned revision's contract.  Strings
    that only exist on a local upstream checkout live in a separate opt-in
    layer so a pinned build neither loads nor validates those keys; move them
    into ``engine.json`` once a pin bump makes them real.  We deliberately do not
    filter unknown keys here: once a profile has selected its layers,
    ``generate_mod`` still rejects genuine stale entries.
    """
    profile = normalize_engine_profile(engine_profile)
    paths = []
    base = _engine_overrides_path(language)
    if base is not None:
        paths.append(base)
    if profile == UPSTREAM_PROFILE:
        upstream = _language_override_path(language, "rby", "engine_upstream.json")
        if upstream is not None:
            paths.append(upstream)
    return tuple(paths)


def _yellow_engine_overrides_path(language: str) -> Path | None:
    return _language_override_path(language, "rby", "yellow_engine.json")


def _merge_engine_overrides(
    *paths: Path | None, destination_dir: Path | None = None,
    name: str = "merged_engine_overrides.json", strict: bool = False,
) -> Path | None:
    """Merge shared engine override files into one temporary JSON.

    Later layers win over earlier ones by default -- this is the shared/
    Yellow-specific layering's own deliberate contract (a Yellow-only
    wording legitimately shadows the shared default for the same key).

    ``strict=True`` instead rejects any key present in more than one layer:
    for the RBY engine.json/engine_upstream.json pairing, a key present in
    both is not a deliberate override, it is a real authoring mistake (e.g.
    a translator fixing engine.json's wording while a stale copy lingers in
    engine_upstream.json) that would otherwise resolve silently by file
    order -- the same class of overlap pipeline/gsc/engine.py's
    match_gs_engine_strings() already rejects between overrides and
    fallback_entries.
    """
    from ..shared.engine import ENGINE_SCHEMA, load_engine_overrides
    merged: dict = {}
    for path in paths:
        if path is None:
            continue
        layer = load_engine_overrides(path)
        if strict:
            overlap = sorted(set(merged) & set(layer))
            if overlap:
                raise BuildError(
                    f"engine override layers disagree on {len(overlap)} key(s), present in more "
                    f"than one of {[str(p) for p in paths if p is not None]}: {overlap!r}"
                )
        merged.update(layer)
    if not merged:
        return None
    destination = (destination_dir or resource_root() / ".cache" / "tmp") / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"schema": ENGINE_SCHEMA, "version": 1, "entries": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


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


def preserve_scaffold_support(
    scaffold: Path,
    mod: Path,
    language: str = "fr",
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> None:
    """Keep Modkit's runtime hooks while selecting the bundled TTF profile."""
    main = scaffold / "main.lua"
    if not main.is_file():
        raise BuildError(f"Modkit did not generate {main}")
    # The scaffold owns the catalog/font runtime. Keep it intact and append
    # the optional qid-driven handlers only when all branch translations are
    # proven; without the runtime file vanilla behavior remains untouched.
    runtime = mod / "lang" / "literal_handlers.lua"
    scaffold_main = main.read_text(encoding="utf-8")
    # Keep an existing scaffold registration during refreshes without a font
    # dependency; only a real source may select the custom TTF profile.
    ttf_line = ttf_registration(language, font_source, font_profile)
    registration_lines = [
        line for line in scaffold_main.splitlines()
        if line.strip().startswith('mod.content.font:register("ttf"')
    ]
    if registration_lines and font_source is not None:
        scaffold_main = "\n".join(
            ttf_line if line.strip().startswith('mod.content.font:register("ttf"') else line
            for line in scaffold_main.splitlines()
        ) + ("\n" if scaffold_main.endswith("\n") else "")
    elif not registration_lines:
        # Refreshes without a dependency source must not point at an absent
        # asset; a new scaffold uses the engine's Plain Pixel registration.
        lines = scaffold_main.splitlines()
        placeholder = next(
            (index for index, line in enumerate(lines)
             if line.strip().startswith('-- mod.content.font:register("ttf"')),
            None,
        )
        if placeholder is not None:
            lines[placeholder] = ttf_line
            scaffold_main = "\n".join(lines) + ("\n" if scaffold_main.endswith("\n") else "")
        else:
            marker = "return function(mod)"
            if marker not in scaffold_main:
                raise BuildError(f"Modkit scaffold main has no translation entry point: {main}")
            scaffold_main = scaffold_main.replace(marker, marker + "\n" + ttf_line, 1)
    # Type names are translated at draw time (Font.draw/Font.split) while the
    # type_chart registry keeps the English names, so third-party mods that
    # key colors/UI off TypeChart.displayName keep resolving them.  An empty
    # generated catalog (no corpus TypeNames rows) has nothing to apply and
    # leaves the scaffold untouched; when values exist but the scaffold
    # drifts, the failure is loud so a generated type_names.lua can never be
    # packed without its runtime hook.
    type_catalog = mod / "lang" / "type_names.lua"
    if type_catalog.is_file():
        type_body = type_catalog.read_text(encoding="utf-8")
        has_type_values = any(
            line.lstrip().startswith("[") and '= "' in line
            and not line.rstrip().endswith('"",')
            for line in type_body.splitlines()
        )
        if has_type_values:
            type_injection = (
                "\n  -- Injected: localized type display names from generated lang/type_names.lua\n"
                "  -- Type names stay English in the type_chart registry so third-party\n"
                "  -- mods that key colors/UI off TypeChart.displayName keep resolving,\n"
                "  -- and are localized at draw time instead: every engine site renders\n"
                "  -- the type name as a standalone Font.draw string, which is substituted\n"
                "  -- below.\n"
                '  local okType, TypeChart = pcall(require, "src.battle.TypeChart")\n'
                "  local by_english = {}\n"
                '  counts.type_names = each("type_names", function(typeId, localized)\n'
                "    if okType and TypeChart and type(TypeChart.displayName) == \"function\" then\n"
                "      local canonical = TypeChart.displayName(typeId)\n"
                "      if type(canonical) == \"string\" and canonical ~= \"\" and canonical ~= localized then\n"
                "        by_english[canonical] = localized\n"
                "      end\n"
                "    end\n"
                "  end)\n"
                "  if next(by_english) then\n"
                '    local okFont, Font = pcall(require, "src.render.Font")\n'
                "    if okFont and type(Font) == \"table\" then\n"
                "      local function localize(text)\n"
                "        if type(text) ~= \"string\" then return text end\n"
                "        local localized = by_english[text]\n"
                "        return type(localized) == \"string\" and localized or text\n"
                "      end\n"
                "      if type(Font.split) == \"function\" then\n"
                "        local original_split = Font.split\n"
                "        Font.split = function(text)\n"
                "          return original_split(localize(text))\n"
                "        end\n"
                "      end\n"
                "      if type(Font.draw) == \"function\" then\n"
                "        local original_draw = Font.draw\n"
                "        Font.draw = function(text, x, y, ...)\n"
                "          return original_draw(localize(text), x, y, ...)\n"
                "        end\n"
                "      end\n"
                "    end\n"
                "  end\n"
            )
            if "counts.type_names" not in scaffold_main:
                if STATUS_LABELS_BLOCK in scaffold_main:
                    scaffold_main = scaffold_main.replace(STATUS_LABELS_BLOCK, STATUS_LABELS_BLOCK + type_injection, 1)
                elif 'each("status_labels"' in scaffold_main:
                    # The statuses block drifted from the exact scaffold shape;
                    # fall back to the closing function boundary like the
                    # literal-handler injection below (counts and each are in
                    # scope for the whole function body).
                    end = scaffold_main.rfind("\nend")
                    if end < 0:
                        raise BuildError(f"Modkit scaffold main has no closing function: {main}")
                    scaffold_main = scaffold_main[:end] + type_injection + scaffold_main[end:]
                else:
                    raise BuildError(f"Modkit scaffold main has no statuses block to extend: {main}")
    # Pokedex categories are translated on the pokemon record's nested
    # ``dexEntry.kind`` field.  Keep this separate from the species name patch
    # so the two patches compose without replacing ``dexEntry.text``.
    species_kind_catalog = mod / "lang" / "species_kinds.lua"
    if species_kind_catalog.is_file():
        species_kind_body = species_kind_catalog.read_text(encoding="utf-8")
        has_species_kind_values = any(
            line.lstrip().startswith("[") and '= "' in line
            and not line.rstrip().endswith('"",')
            for line in species_kind_body.splitlines()
        )
        if has_species_kind_values and "counts.species_kinds" not in scaffold_main:
            species_kind_injection = (
                "\n  -- Injected: localized Pokedex categories from generated lang/species_kinds.lua\n"
                '  counts.species_kinds = each("species_kinds", function(id, value)\n'
                "    mod.content.pokemon:patch(id, { dexEntry = { kind = value } })\n"
                "  end)\n"
            )
            if STATUS_LABELS_BLOCK in scaffold_main:
                scaffold_main = scaffold_main.replace(STATUS_LABELS_BLOCK, STATUS_LABELS_BLOCK + species_kind_injection, 1)
            elif 'each("status_labels"' in scaffold_main:
                end = scaffold_main.rfind("\nend")
                if end < 0:
                    raise BuildError(f"Modkit scaffold main has no closing function: {main}")
                scaffold_main = scaffold_main[:end] + species_kind_injection + scaffold_main[end:]
            else:
                raise BuildError(f"Modkit scaffold main has no statuses block to extend: {main}")
    # Yellow layers are applied only after the shared catalogs and only for
    # the Yellow game.  Keep the hook in the final scaffold-owned main.lua;
    # generate_mod's standalone main remains useful for unit tests.
    yellow_names = [
        name for name in ("dialogue", "strings", "species_names", "move_names",
                          "item_names", "trainer_names", "status_labels")
        if (mod / "lang" / f"{name}_yellow.lua").is_file()
    ]
    if yellow_names and "local yellow_game_version" not in scaffold_main:
        yellow_injection = (
            "\n  -- Injected: versioned catalogs for Pokémon Yellow.\n"
            + yellow_isyellow_guard_lines()
            + "  if yellow_game_version then\n"
            + "\n".join(f"    {YELLOW_CATALOG_HOOKS[name]}" for name in yellow_names)
            + "\n  end\n"
        )
        end = scaffold_main.rfind("\nend")
        if end < 0:
            raise BuildError(f"Modkit scaffold main has no closing function: {main}")
        scaffold_main = scaffold_main[:end] + yellow_injection + scaffold_main[end:]

    # A few in-game Options values are raw Font strings in v0.1.69 instead
    # of Strings lookups. Keep this allowlist explicit and reuse the generated
    # strings catalog; do not patch the renderer/Kit itself.
    if "local raw_option_keys" not in scaffold_main and 'each("strings"' in scaffold_main:
        raw_options_injection = (
            "\n  -- Injected: localize raw values only while OptionsMenu draws\n"
            "  local raw_option_keys = {\n"
            '    ["OG RED"] = true, ["OG BLUE"] = true, ["OG YELLOW"] = true,\n'
            '    ["SGB"] = true, ["ADVANCED"] = true, ["OG INV"] = true,\n'
            '    ["SGB INV"] = true, ["CLASSIC"] = true, ["GBC"] = true,\n'
            '    ["WINDOWED"] = true, ["BORDERLESS"] = true,\n'
            '    ["TREES"] = true, ["WATER"] = true, ["BLACK"] = true,\n'
            '    ["OFF"] = true, ["1X"] = true, ["2X"] = true, ["3X"] = true,\n'
            '    ["NORMAL"] = true,\n'
            "  }\n"
            "  local by_raw_option = {}\n"
            '  each("strings", function(id, localized)\n'
            "    if raw_option_keys[id] and localized ~= id then\n"
            "      by_raw_option[id] = localized\n"
            "    end\n"
            "  end)\n"
            "  if next(by_raw_option) then\n"
            '    local okOptions, OptionsMenu = pcall(require, "src.ui.OptionsMenu")\n'
            '    local okFont, Font = pcall(require, "src.render.Font")\n'
            "    if okOptions and type(OptionsMenu) == \"table\" and type(OptionsMenu.draw) == \"function\"\n"
            "        and okFont and type(Font) == \"table\" then\n"
            "      local original_options_draw = OptionsMenu.draw\n"
            "      local function localizeRawOption(text)\n"
            "        if type(text) ~= \"string\" then return text end\n"
            "        return by_raw_option[text] or text\n"
            "      end\n"
            "      OptionsMenu.draw = function(self, ...)\n"
            "        local original_split, original_draw = Font.split, Font.draw\n"
            "        if type(original_split) == \"function\" then\n"
            "          Font.split = function(text) return original_split(localizeRawOption(text)) end\n"
            "        end\n"
            "        if type(original_draw) == \"function\" then\n"
            "          Font.draw = function(text, x, y, ...)\n"
            "            return original_draw(localizeRawOption(text), x, y, ...)\n"
            "          end\n"
            "        end\n"
            "        local ok, result = pcall(original_options_draw, self, ...)\n"
            "        Font.split, Font.draw = original_split, original_draw\n"
            "        if ok then return result end\n"
            "        error(result, 0)\n"
            "      end\n"
            "    end\n"
            "  end\n"
        )
        end = scaffold_main.rfind("\nend")
        if end < 0:
            raise BuildError(f"Modkit scaffold main has no closing function: {main}")
        scaffold_main = scaffold_main[:end] + raw_options_injection + scaffold_main[end:]
    # Yellow's Pallet-intro catch demo and the old-man tutorial show the
    # thrower name in the translated "%s used POKé BALL!" template
    # (BattleState.oldManThrow).  demoName must stay the canonical English
    # literal -- the engine keys Yellow's Pallet-intro sprite selection off
    # demoName == "PROF.OAK" -- so the translation happens only at the
    # render site and is reverted right after.
    if "oldManThrow" not in scaffold_main:
        demo_injection = (
            "\n  -- Injected: localize hard-coded demo-battle thrower names\n"
            '  local demo_names = catalog("demo_names")\n'
            "  local function localizedDemoName(self, name)\n"
            "    if type(name) == \"string\" then\n"
            "      local localized = demo_names and demo_names[name]\n"
            "      if type(localized) == \"string\" and localized ~= \"\" then\n"
            "        return localized\n"
            "      end\n"
            "      if name == \"PROF.OAK\" then\n"
            "        local trainers = self and self.game and self.game.data and self.game.data.trainers\n"
            "        local oak = trainers and trainers.OPP_PROF_OAK\n"
            "        if oak and type(oak.name) == \"string\" and oak.name ~= \"\" then\n"
            "          return oak.name\n"
            "        end\n"
            "      end\n"
            "    end\n"
            "    return nil\n"
            "  end\n"
            '  local okDemo, BS = pcall(require, "src.battle.BattleState")\n'
            "  if okDemo and type(BS) == \"table\" and type(BS.oldManThrow) == \"function\" then\n"
            "    local original_oldManThrow = BS.oldManThrow\n"
            "    BS.oldManThrow = function(self, ...)\n"
            "      if type(self) == \"table\" then\n"
            "        local canonical = self.demoName\n"
            "        local localized = localizedDemoName(self, canonical)\n"
            "        if type(localized) == \"string\" and localized ~= \"\" then\n"
            "          self.demoName = localized\n"
            "          local ok, result = pcall(original_oldManThrow, self, ...)\n"
            "          self.demoName = canonical\n"
            "          if ok then return result end\n"
            "          error(result, 0)\n"
            "        end\n"
            "      end\n"
            "      return original_oldManThrow(self, ...)\n"
            "    end\n"
            "  end\n"
            "  -- Injected: the Pallet-intro thrower sprite is NOT overridden; with\n"
            "  -- demoName kept canonical, the engine itself selects Prof. Oak's back\n"
            "  -- pic for that demo (vanilla behavior).\n"
        )
        end = scaffold_main.rfind("\nend")
        if end >= 0:
            scaffold_main = scaffold_main[:end] + demo_injection + scaffold_main[end:]
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
    # TTF mode supplies ordinary Unicode glyphs; ROM-derived font/charmap
    # catalogs and images are intentionally not copied into the mod.
    naming = scaffold / "lang" / "naming.lua"
    if naming.is_file():
        shutil.copy2(naming, mod / "lang" / "naming.lua")


def remove_legacy_font_artifacts(mod: Path) -> None:
    """Drop stale ROM-derived font files from an incremental build."""
    for relative in (
        Path("lang/font.lua"),
        Path("lang/charmap.lua"),
        Path("assets/font/localized.png"),
    ):
        (mod / relative).unlink(missing_ok=True)


def print_coverage(
    path: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    """Print ROM-gated and informational engine match percentages."""
    report = json.loads(path.read_text(encoding="utf-8"))
    lines = ["\nTranslation coverage:"]
    # ROM aggregates first (broad Red/Blue, then narrower Yellow), then
    # engine-authored-text metrics from most to least specific: RBY/Gold's
    # own filtered scope reads before the unfiltered "All engine strings"
    # total, since that total is the least actionable number here.
    section = report.get("rom") or {}
    lines.append(f"  Red Blue ROM aggregate: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    yellow = (report.get("yellow") or {}).get("coverage", {}).get("rom") or {}
    if yellow.get("total"):
        lines.append(f"  Yellow ROM aggregate: {int(yellow.get('translated', 0))}/{int(yellow.get('total', 0))} ({float(yellow.get('percent', 0.0)):.2f}%)")
    section = report.get("engine_rby") or {}
    if section.get("available", True) and section.get("total"):
        lines.append(f"  RBY-related engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    elif report.get("engine_rby_warning"):
        lines.append(f"  RBY-related engine strings: unavailable ({report['engine_rby_warning']})")
    section = report.get("engine_gen2") or {}
    if section.get("total"):
        lines.append(f"  Gold and Silver-related engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    section = report.get("engine") or {}
    lines.append(f"  All engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} ({float(section.get('percent', 0.0)):.2f}%)")
    for line in lines:
        print(line)
        if log_fn:
            log_fn(line)


def build(
    rb_rom: Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: Path | None = None,
    output_dir: Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
    font_profile: str = "fusion",
    yellow_rom: Path | None = None,
    engine_profile: str = PINNED_PROFILE,
    engine_source: str | Path | None = None,
) -> Path:
    """Execute the complete private extraction, translation, and pack flow.

    ``rb_rom`` accepts either a real Red or a real Blue ROM (verify_rb_rom
    picks whichever it is); the two share byte-identical dialogue text and
    pointer tables, so either extracts into the same canonical output with
    an equally correct result -- no separate Blue import or diff is needed.
    With ``yellow_rom`` the result is the universal Red/Blue/Yellow mod: the
    Yellow import stays in a separate cache directory and versioned catalog
    layers are applied at runtime when ``GameVersion.isYellow()``.
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
    profile = release_profile("rby")
    if not profile.corpus_collections:
        raise BuildError("RBY release profile has no supported corpus collection")
    font_profile = validate_font_profile(language, font_profile)
    status("Validating ROMs")
    rb_info = verify_rb_rom(rb_rom)
    if yellow_rom is not None:
        verify_rom(yellow_rom, "yellow")

    from ..shared.orchestration import package_release, prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language,
        font_profile=font_profile,
        engine_source=engine_source,
    )
    workspace = context.workspace
    destination = context.destination
    status("Preparing dependencies")
    gen1recomp, corpus, font_source = context.gen1recomp, context.corpus, context.font_source

    log("\nExtracting private ROM data...")
    status("Extracting private ROM data")
    import_rom(
        rb_info["version"], rb_rom, gen1recomp,
        gen1recomp / "data" / "generated",
        gen1recomp / "assets" / "generated",
        log_fn=log_fn,
    )
    if yellow_rom is not None:
        import_rom(
            "yellow", yellow_rom, gen1recomp,
            gen1recomp / "yellow" / "data" / "generated",
            gen1recomp / "yellow" / "assets" / "generated",
            log_fn=log_fn,
        )

    build_root = workspace / "interactive" / language
    scaffold = build_root / "translation_source"
    modkit = gen1recomp / "tools" / "modkit.py"
    env = dict(os.environ)
    env["MODKIT_LUAJIT"] = luajit
    env["LUA"] = luajit
    # Modkit's dump_dataset() decodes the LuaJIT dump with subprocess
    # text=True and no explicit encoding, which falls back to the OS locale
    # codepage (e.g. cp1252 on Windows). Some dumped text (observed in the
    # Yellow-layer dataset, across target languages) isn't representable in
    # that codepage and crashes the internal reader thread with a
    # UnicodeDecodeError, leaving proc.stdout as None. Force Python-wide
    # UTF-8 mode (PEP 540) for this worker so that fallback decodes as UTF-8
    # instead, matching the UTF-8 the Lua source files are read/written as.
    env["PYTHONUTF8"] = "1"
    # v0.1.69+'s modkit pack/validate drives the real loader headlessly.
    # Data.loadModule supports POKEPORT_DATA_DIR, which loadfiles the
    # imported dataset directly and skips the love.filesystem-dependent
    # CacheFs path (a bare loader run would crash on CacheFs.read).
    env["POKEPORT_DATA_DIR"] = str(gen1recomp / "data" / "generated")
    if is_frozen():
        lua_dir = str(Path(luajit).resolve().parent)
        env["PATH"] = lua_dir + os.pathsep + env.get("PATH", "")
    _run(_modkit_command(modkit, "--repo", str(gen1recomp),
            "translation", "translation_source", "--language", language_name,
            "--base", "imported", "--dest", str(build_root), "--pixel-font", "--force"),
        cwd=gen1recomp,
        env=env,
        log_fn=log_fn,
    )
    worksheet = assemble_worksheet(scaffold, build_root / "complete-modkit-worksheet")
    yellow_worksheet = None
    if yellow_rom is not None:
        yellow_root = build_root / "yellow_source"
        yellow_scaffold = yellow_root / "translation_source_yellow"
        yellow_env = dict(env)
        yellow_env["POKEPORT_DATA_DIR"] = str(gen1recomp / "yellow" / "data" / "generated")
        _run(_modkit_command(modkit, "--repo", str(gen1recomp),
                "translation", "translation_source_yellow", "--language", language_name,
                "--base", "imported", "--dest", str(yellow_root), "--pixel-font", "--force"),
            cwd=gen1recomp, env=yellow_env, log_fn=log_fn)
        yellow_worksheet = assemble_worksheet(
            yellow_scaffold, yellow_root / "complete-modkit-worksheet"
        )

    log("\nMatching poke-corpus translations...")
    status("Matching poke-corpus translations")
    records = parse_redblue(corpus, language)
    rows = align(records, target_lang=language)
    corpus_overrides = _corpus_overrides_path(language)
    if corpus_overrides:
        rows = apply_corpus_overrides(rows, corpus_overrides)
    mod = build_root / "mod"
    coverage = build_root / "coverage.json"
    remove_legacy_font_artifacts(mod)
    yellow_dialogue = None
    yellow_stats = None
    yellow_catalogs: dict[str, dict[str, str]] = {}
    yellow_engine_values: dict[str, str] = {}
    red_joined = None
    red_join_report = None
    if yellow_rom is not None:
        log("\nBuilding Yellow dialogue layer...")
        status("Building Yellow dialogue layer")
        from .yellow import parse_text_catalog, yellow_dialogue_layer
        from .join import join_catalogs, read_worksheets
        red_text = parse_text_catalog(gen1recomp / "data" / "generated" / "text.lua")
        yellow_text = parse_text_catalog(gen1recomp / "yellow" / "data" / "generated" / "text.lua")
        yellow_rows = align(parse_yellow(corpus, language), target_lang=language)
        red_worksheets = read_worksheets(worksheet)
        red_joined, red_join_report = join_catalogs(rows, red_worksheets, language)
        yellow_worksheets = read_worksheets(yellow_worksheet)
        yellow_joined, yellow_join_report = join_catalogs(
            yellow_rows, yellow_worksheets, language
        )
        yellow_dialogue, yellow_stats = yellow_dialogue_layer(
            red_text, yellow_text,
            yellow_rows,
            language,
            red_translation=red_joined.get("dialogue", {}),
        )
        for catalog_name, values in yellow_joined.items():
            if catalog_name == "dialogue":
                continue
            common = red_joined.get(catalog_name, {})
            layer = {
                key: value for key, value in values.items()
                if value and value != common.get(key)
            }
            if layer:
                yellow_catalogs[catalog_name] = layer
        yellow_stats["catalogs"] = {}
        for name in yellow_worksheets:
            if name == "dialogue":
                continue
            yellow_stats["catalogs"][name] = {
                "translated": len(yellow_catalogs.get(name, {})),
                "total": len(yellow_worksheets[name]),
                "matched": yellow_join_report.get("matched", {}).get(name, 0),
            }
        common_dialogue = red_joined.get("dialogue", {})
        yellow_dialogue_joined = yellow_joined.get("dialogue", {})
        unmatched_labels = set(yellow_stats.get("unmatched_labels", ()))
        coverage_exceptions = load_yellow_coverage_exceptions(
            resource_root() / "config" / "rby" / "yellow_coverage_exceptions.json"
        )
        composition_covered = coverage_exceptions.get(language, frozenset())
        yellow_stats["effective_dialogue_translated"] = sum(
            label not in unmatched_labels
            and (
                label in yellow_dialogue_joined
                or (
                    label in common_dialogue
                    and red_text.get(label) == yellow_text.get(label)
                )
            )
            or label in composition_covered
            for label in yellow_text
        )
        yellow_stats["composition_covered_labels"] = sorted(
            composition_covered & set(yellow_text)
        )
        # A composition-covered label is credited unconditionally in the
        # numerator above regardless of its own (possibly empty) ROM
        # content, so it must be credited unconditionally in the
        # denominator too — otherwise the numerator could exceed the
        # denominator and report >100% coverage.
        yellow_stats["effective_dialogue_total"] = sum(
            bool(content) or label in composition_covered
            for label, content in yellow_text.items()
        )
        effective_named = 0
        for name, entries in yellow_worksheets.items():
            if name == "dialogue":
                continue
            common_entries = {entry.key: entry.english for entry in red_worksheets.get(name, ())}
            common_values = red_joined.get(name, {})
            yellow_values = yellow_joined.get(name, {})
            effective_named += sum(
                bool(yellow_values.get(key))
                or (bool(common_values.get(key)) and common_entries.get(key) == entry.english)
                for entry in entries
                for key in (entry.key,)
            )
        yellow_stats["effective_named_catalog_translated"] = effective_named
        override_path = _yellow_engine_overrides_path(language)
        if override_path:
            from ..shared.engine import check_printf_directives, load_engine_overrides, read_engine_catalog
            overrides = load_engine_overrides(override_path)
            engine_catalog_values = read_engine_catalog(yellow_worksheet / "strings.lua")
            for source, row in overrides.items():
                if source not in engine_catalog_values:
                    raise BuildError(f"Yellow engine override contains unknown key: {source!r}")
                value = row["override"]
                errors = check_printf_directives(source, value)
                if errors:
                    raise BuildError(f"Invalid Yellow engine override {source!r}: {errors[0]}")
                yellow_engine_values[source] = value
            yellow_catalogs.setdefault("strings", {}).update(yellow_engine_values)
        log(f"  Yellow layer: {yellow_stats['layer_entries']} entries "
            f"({yellow_stats['versioned_required']} versioned, "
            f"{yellow_stats['yellow_only']} Yellow-only, "
            f"{yellow_stats['shared_safe']} shared-safe skipped, "
            f"{yellow_stats['unmatched']} unmatched)")
        # Independent Yellow audit: the versioned dialogue matrix, written
        # under .cache/audit/yellow/ next to the coverage report.
        from .yellow_audit import write_yellow_audit
        audit_path = write_yellow_audit(
            gen1recomp / "data" / "generated" / "text.lua",
            gen1recomp / "yellow" / "data" / "generated" / "text.lua",
            align(parse_yellow(corpus, language), target_lang=language),
            language,
            build_root / ".." / ".." / "audit" / "yellow",
            red_text=red_text,
            yellow_text=yellow_text,
            red_translation=red_joined.get("dialogue", {}),
            layer=yellow_dialogue,
            stats=yellow_stats,
        )
        log(f"  Yellow audit: {audit_path}")
    generate_mod(
        rows,
        mod,
        mod_id=f"translation-{language.lower()}",
        language=language,
        target_name=f"{language_name} translation for Red, Blue and Yellow",
        modkit_worksheet=worksheet,
        report_path=coverage,
        engine_overrides=_merge_engine_overrides(
            *_rby_engine_override_paths(language, engine_profile),
            destination_dir=workspace / "tmp",
            name=f"merged_engine_overrides_{language}.json",
            strict=True,
        ),
        semantic_anchors=resource_root() / "config" / "rby" / "semantic_anchors.json",
        semantic_anchor_decisions=resource_root() / "config" / "rby" / "semantic_anchor_decisions.json",
        strict_engine=True,
        engine_source=gen1recomp,
        engine_profile=engine_profile,
        engine_scope=resource_root() / "config" / "rby" / "engine_scope.json",
        font_source=font_source,
        font_profile=font_profile,
        yellow_dialogue=yellow_dialogue,
        yellow_stats=yellow_stats,
        yellow_catalogs=yellow_catalogs,
        yellow_engine_overrides=yellow_engine_values,
        precomputed_join=(red_joined, red_join_report) if red_joined is not None else None,
    )
    preserve_scaffold_support(scaffold, mod, language, font_source, font_profile)
    leaks = audit_generated_catalogs(mod)
    if leaks:
        raise BuildError("generated catalogs leak corpus markup:\n" + "\n".join(leaks))

    version = project_version()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"translation-{language.lower()}-{version}.zip"
    status("Packaging translation mod")
    published = package_release(
        mod, gen1recomp, modkit, build_root, destination,
        output.name, base="imported", env=env, log_fn=log_fn,
    )
    status("Build complete")
    print_coverage(coverage, log_fn=log_fn)
    return published
