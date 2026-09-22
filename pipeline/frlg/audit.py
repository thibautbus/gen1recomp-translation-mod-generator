"""Developer audit: hardcoded player-visible text in gen1recomp's game3 runtime.

A translation mod can only reach what goes through a registry, a hook or
``Strings()``.  This scans the pinned engine's game3 sources for string
literals that look like text and are not handed to ``Strings()``, then
separates the files whose literals reach the screen from the files whose
literals are internal (identifiers, asset paths, log lines, mod-API errors).
A literal the runtime passes to ``Strings()`` through a variable (a table or
list pipeline/frlg/engine_scope.py reads from that same file) is reachable
too, so it is not counted; the same text drawn raw elsewhere still is.
The second list is reviewed by hand and lives in ``NON_DISPLAY_FILES`` with
its reason; any other file the scan flags is player-visible and must be
listed in docs/upstream-fixes.md (a test enforces it).

The per-file counts are an upper bound: a player-visible file can still hold
some internal identifiers next to its messages.  The report is written below
``.cache/audit`` and never packaged.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Mapping

SCANNED_DIRS = ("src/ui/game3", "src/core/game3", "src/world/game3", "src/battle/game3")

# Files the scan flags whose literals never reach the screen, reviewed at the
# pinned revision.  Keep one line per file so a pin bump shows what changed.
NON_DISPLAY_FILES: Mapping[str, str] = {
    "src/battle/game3/BattleAPI.lua": "mod-API error messages",
    "src/core/game3/audio.lua": "log lines",
    "src/core/game3/bag.lua": "pocket identifiers",
    "src/core/game3/battle/abilities.lua": "status and weather identifiers",
    "src/core/game3/battle/adapter.lua": "status identifiers",
    "src/core/game3/battle/ai.lua": "cache paths",
    "src/core/game3/battle/ai_items.lua": "status identifiers",
    "src/core/game3/battle/anim.lua": "animation identifiers",
    "src/core/game3/battle/anim_callbacks.lua": "animation identifiers",
    "src/core/game3/battle/anim_pack_fallback.lua": "animation identifiers",
    "src/core/game3/battle/anim_tasks.lua": "animation identifiers",
    "src/core/game3/battle/anim_templates.lua": "animation identifiers",
    "src/core/game3/battle/anim_vm.lua": "animation identifiers",
    "src/core/game3/battle/ball_open.lua": "asset paths",
    "src/core/game3/battle/catch_seq.lua": "fallback ball name for a missing item pack",
    "src/core/game3/battle/catching.lua": "fallback OT name",
    "src/core/game3/battle/commands.lua": "command and move identifiers, fallback item name",
    "src/core/game3/battle/damage.lua": "stat identifiers",
    "src/core/game3/battle/effect_ctx.lua": "internal error messages",
    "src/core/game3/battle/effect_ids.lua": "effect identifiers",
    "src/core/game3/battle/effects/hazards.lua": "move identifier",
    "src/core/game3/battle/effects/healing.lua": "status identifier, fallback move name",
    "src/core/game3/battle/effects/hit.lua": "status and move identifiers",
    "src/core/game3/battle/effects/screens.lua": "fallback move names for a missing move pack",
    "src/core/game3/battle/effects/secondary.lua": "status and move identifiers, fallback move names",
    "src/core/game3/battle/effects/setup.lua": "status identifiers, fallback move name",
    "src/core/game3/battle/effects/special.lua": "fallback trainer name",
    "src/core/game3/battle/effects/stats.lua": "status identifier",
    "src/core/game3/battle/effects/status.lua": "status identifiers",
    "src/core/game3/battle/effects/weather.lua": "weather identifiers",
    "src/core/game3/battle/engine.lua": "status, semi-invulnerable and volatile identifiers",
    "src/core/game3/battle/evo_seq.lua": "fallback species name",
    "src/core/game3/battle/held_items.lua": "item name fallbacks for a missing item pack (names come from the pack and the items registry)",
    "src/core/game3/battle/init.lua": "weather and status identifiers, internal errors, fallback ball name",
    "src/core/game3/battle/items.lua": "fallback player, species and trainer names",
    "src/core/game3/battle/learn_move.lua": "fallback move label for a missing move pack",
    "src/core/game3/battle/moves.lua": "move identifiers (names come from the ROM pack)",
    "src/core/game3/battle/oak_advice.lua": "fallback player name",
    "src/core/game3/battle/prize.lua": "fallback player name",
    "src/core/game3/battle/residual_handlers.lua": "status identifiers, fallback move name",
    "src/core/game3/battle/rules.lua": "weather identifiers",
    "src/core/game3/battle/state.lua": "fallback name",
    "src/core/game3/battle/status.lua": "status identifier",
    "src/core/game3/battle/switch_seq.lua": "fallback trainer and species names",
    "src/core/game3/battle/types.lua": "type identifiers",
    "src/core/game3/battle/ui.lua": "fallback species name",
    "src/core/game3/battle_bridge.lua": "internal error messages",
    "src/core/game3/battle_downgrade.lua": "move identifiers",
    "src/core/game3/bridge.lua": "log lines",
    "src/core/game3/collision.lua": "log lines",
    "src/core/game3/dataset.lua": "cache paths and log lines",
    "src/core/game3/display.lua": "log line",
    "src/core/game3/easy_chat_text.lua": "catalog context prefix",
    "src/core/game3/encounters.lua": "log lines",
    "src/core/game3/evolution.lua": "species identifiers",
    "src/core/game3/field.lua": "quest-log event keys",
    "src/core/game3/field_moves.lua": "badge and move identifiers, fallback species name",
    "src/core/game3/field_view.lua": "log lines and time-of-day identifiers",
    "src/core/game3/forced_movement.lua": "movement action identifiers",
    "src/core/game3/item_use.lua": "status identifier and quest-log event keys",
    "src/core/game3/items.lua": "item identifiers (names come from the ROM pack)",
    "src/core/game3/items_data.lua": "item name fallbacks for a missing item pack, TM/HM identifiers",
    "src/core/game3/link/battle.lua": "fallback player name",
    "src/core/game3/link/chat.lua": "fallback player name",
    "src/core/game3/link/init.lua": "script-command key prefix",
    "src/core/game3/link/trade.lua": "fallback player name",
    "src/core/game3/link/union_room.lua": "fallback player name",
    "src/core/game3/m4a_player.lua": "log lines",
    "src/core/game3/m4a_sample.lua": "code comment string",
    "src/core/game3/map.lua": "internal error message",
    "src/core/game3/mystery_gift.lua": "card status codes, an environment variable name and file paths",
    "src/core/game3/objects.lua": "movement identifiers and log lines",
    "src/core/game3/ow_sprites.lua": "asset paths",
    "src/core/game3/palette.lua": "internal error message",
    "src/core/game3/player.lua": "log line",
    "src/core/game3/pokedex_data.lua": "category identifier",
    "src/core/game3/pokemon.lua": "species name normalisation and ROM file names",
    "src/core/game3/quest_log_recorder.lua": "quest-log event keys",
    "src/core/game3/runtime.lua": "log lines",
    "src/core/game3/save_schema_firered.lua": "default save names",
    "src/core/game3/scripting/adapters.lua": "default rival name, quest-log keys and log lines",
    "src/core/game3/scripting/collision.lua": "collision identifiers",
    "src/core/game3/scripting/ctx.lua": "fallback placeholder names",
    "src/core/game3/scripting/flags.lua": "flag identifiers",
    "src/core/game3/scripting/gfx_ids.lua": "graphics identifiers",
    "src/core/game3/scripting/interaction_scripts.lua": "interaction identifiers (text comes from the ROM object pack)",
    "src/core/game3/scripting/natives.lua": "special/native identifiers, quest-log keys and log lines",
    "src/core/game3/scripting/natives_seagallop.lua": "log lines",
    "src/core/game3/scripting/natives_tower.lua": "log lines",
    "src/core/game3/scripting/natives_trade.lua": "log lines",
    "src/core/game3/scripting/ops_a.lua": "debug fallbacks and log lines",
    "src/core/game3/scripting/space.lua": "cache paths and log lines",
    "src/core/game3/scripting/stdscripts.lua": "script names",
    "src/core/game3/scripting/text_ir.lua": "fallback placeholder names",
    "src/core/game3/scripting/trainers.lua": "fallback trainer class and name identifiers",
    "src/core/game3/step_events.lua": "fallback player name",
    "src/core/game3/storage.lua": "quest-log event keys",
    "src/core/game3/summary_descriptions.lua": "fallback description table, passed to Strings() where the summary reads it",
    "src/core/game3/teachy_tv.lua": "lesson identifiers (the lessons themselves go through Strings())",
    "src/core/game3/tileset_anim.lua": "asset paths and log lines",
    "src/core/game3/tileset_native.lua": "asset paths and log lines",
    "src/core/game3/trainer_pic.lua": "ROM file names and asset paths",
    "src/core/game3/trainer_tower.lua": "log lines and status codes",
    "src/core/game3/void_fill.lua": "labels passed to Strings() by option_rows.lua",
    "src/core/game3/vs_seeker.lua": "map identifier and quest-log key",
    "src/core/game3/warp.lua": "internal error message",
    "src/ui/game3/bag_menu.lua": "fallback item name",
    "src/ui/game3/battle_chrome.lua": "asset paths",
    "src/ui/game3/boot.lua": "title menu identifiers (drawn through Strings())",
    "src/ui/game3/easy_chat.lua": "screen-mode and button identifiers (its prompts and footer labels go through Strings())",
    "src/ui/game3/egg_hatch.lua": "fallback species name",
    "src/ui/game3/evolution_scene.lua": "fallback species name",
    "src/ui/game3/fame_checker.lua": "fallback player and rival names",
    "src/ui/game3/frlg_font.lua": "asset paths and log lines",
    "src/ui/game3/hall_of_fame.lua": "fallback player and species names",
    "src/ui/game3/intro_movie.lua": "scene identifiers",
    "src/ui/game3/map_name_popup.lua": "fallback name for a map with no id",
    "src/ui/game3/move_relearner.lua": "fallback type identifier",
    "src/ui/game3/new_game_scene.lua": "naming template identifier",
    "src/ui/game3/option_menu.lua": "fallback page title",
    "src/ui/game3/option_rows.lua": "Strings() context prefix",
    "src/ui/game3/party_menu.lua": "quest-log event keys",
    "src/ui/game3/pc_chrome.lua": "default box name format compared with the stored name",
    "src/ui/game3/pc_menu.lua": "pocket identifier, fallback player name",
    "src/ui/game3/pokedex.lua": "input-side action labels (the drawn list goes through Strings()), fallback category",
    "src/ui/game3/pokedex_chrome.lua": "type identifier",
    "src/ui/game3/quest_log.lua": "default rival name and quest-log key",
    "src/ui/game3/release_seq.lua": "fallback species name",
    "src/ui/game3/save_menu.lua": "fallback player and map names",
    "src/ui/game3/shop_menu.lua": "quest-log event keys",
    "src/ui/game3/slot_machine.lua": "cache key suffix",
    "src/ui/game3/start_menu.lua": "labels the ui.start_menu.items hook translates",
    "src/ui/game3/summary_menu.lua": "stat colour identifiers",
    "src/ui/game3/teachy_tv.lua": "bag pocket identifier",
    "src/ui/game3/tm_case.lua": "fallback TM label for a missing item pack",
    "src/ui/game3/trainer_card.lua": "badge flag identifiers",
    "src/ui/game3/trainer_tower_records.lua": "log lines",
    "src/world/game3/WorldAPI.lua": "mod-API error messages",
}

_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|\[\[(.*?)\]\]', re.S)
_NOT_DISPLAY_CALL = re.compile(
    r"(require|print|log|warn|warnOnce|error|assert|Logger\.\w+|pcall|:find|:match|:gmatch|:gsub|:sub"
    r"|string\.find|string\.match|wasPressed|isDown|rawget|getInfo|read|write|playSe|playSong|event"
    r"|emit|call|wants|wantsHook|newImage|newQuad|setShader|Strings\.source)\s*\($")


def _looks_like_text(value: str) -> bool:
    text = value.replace("\\n", " ").replace("\\f", " ").strip()
    if not re.search(r"[A-Za-z]{2}", text):
        return False
    if re.fullmatch(r"[A-Za-z0-9_]+", text) and "_" in text:
        return False
    if re.fullmatch(r"[a-z][A-Za-z0-9_]*", text):
        return False
    if re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+", text):
        return False
    if re.fullmatch(r"g3:[0-9a-f]+", text):
        return False
    if " " not in text and ("/" in text or re.search(r"\.[a-z0-9]{2,4}$", text)):
        return False  # file and asset paths
    return True


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "f": "\f"}.get(m.group(1), m.group(1)), value)


def _literal_argument_spans(text: str) -> list[bool]:
    """Which characters of ``text`` a literal Strings() argument covers,
    concatenation included.

    The scan below reads one literal at a time, so a message split across
    lines ("..." .. "..." inside Strings(), Teachy TV's lessons) or called
    through require("src.core.Strings")(...) would read as bare literals;
    the catalog harvester already recognises those calls, so ask it.  Only
    the argument itself is covered: another literal on the same line (an
    "or" fallback, a second value after the call) is still scanned.
    """
    from ..shared.strings_harvest import _read_concatenated_lua_literal, _strip_lua_comments, strings_calls
    cleaned = _strip_lua_comments(text)
    covered = [False] * len(text)
    for match in strings_calls(text, cleaned):
        index = match.end()
        while index < len(cleaned) and cleaned[index].isspace():
            index += 1
        token = _read_concatenated_lua_literal(text, cleaned, index)
        if token is None:
            continue
        for offset in range(index, token[1]):
            covered[offset] = True
    return covered


def scan_hardcoded_literals(checkout: str | Path, reachable: Mapping[str, frozenset[str]] | None = None) -> dict[str, list[tuple[int, str]]]:
    """Every text-looking literal in game3 that is not a Strings() argument
    and that ``reachable`` (key -> files the scope reads it from) does not
    record for its file."""
    reachable = reachable or {}
    root = Path(checkout)
    found: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for directory in SCANNED_DIRS:
        for path in sorted((root / directory).rglob("*.lua")):
            relative = path.relative_to(root).as_posix()
            if "/anim_port/" in relative or path.name == "flags_table.lua":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            covered = _literal_argument_spans(text)
            line_start = 0
            for number, line in enumerate(text.split("\n"), 1):
                start, line_start = line_start, line_start + len(line) + 1
                if line.lstrip().startswith("--"):
                    continue
                for match in _LITERAL.finditer(line):
                    if covered[start + match.start()]:
                        continue
                    value = next(group for group in match.groups() if group is not None)
                    if not _looks_like_text(value) or relative in reachable.get(_unescape(value), ()):
                        continue
                    before = line[:match.start()].rstrip()
                    after = line[match.end():].lstrip()
                    if re.search(r"Strings(\.get)?\s*\(\s*$", before) or _NOT_DISPLAY_CALL.search(before):
                        continue
                    if re.search(r"(==|~=)\s*$", before) or re.match(r"(==|~=)", after):
                        continue
                    if before.endswith("[") and after.startswith("]"):
                        continue
                    found[relative].append((number, value))
    return dict(found)


def reachable_keys(checkout: str | Path) -> dict[str, frozenset[str]]:
    """Text the runtime passes to Strings() through a variable -> the files
    holding it, read from the engine the way the scope generator reads it."""
    from .engine_scope import reachable_by_file
    return {key: frozenset(paths) for key, paths in reachable_by_file(Path(checkout)).items()}


def player_visible_files(checkout: str | Path, reachable: Mapping[str, frozenset[str]] | None = None) -> dict[str, list[tuple[int, str]]]:
    reachable = reachable_keys(checkout) if reachable is None else reachable
    return {path: rows for path, rows in scan_hardcoded_literals(checkout, reachable).items()
            if path not in NON_DISPLAY_FILES}


def run_frlg_hardcoded_audit(checkout: str | Path, output: str | Path) -> dict:
    visible = player_visible_files(checkout)
    report = {
        "checkout": str(checkout),
        "files": {path: {"literals": len(rows), "rows": [{"line": n, "text": t} for n, t in rows]}
                  for path, rows in sorted(visible.items())},
        "total_literals": sum(len(rows) for rows in visible.values()),
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
