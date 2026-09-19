"""Developer audit: hardcoded player-visible text in gen1recomp's game3 runtime.

A translation mod can only reach what goes through a registry, a hook or
``Strings()``.  This scans the pinned engine's game3 sources for string
literals that look like text and are not handed to ``Strings()``, then
separates the files whose literals reach the screen from the files whose
literals are internal (identifiers, asset paths, log lines, mod-API errors).
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
    "src/world/game3/WorldAPI.lua": "mod-API error messages",
    "src/core/game3/audio.lua": "log lines",
    "src/core/game3/bag.lua": "pocket identifiers",
    "src/core/game3/battle/ai.lua": "cache paths",
    "src/core/game3/battle/ai_items.lua": "status identifiers",
    "src/core/game3/battle/anim.lua": "animation identifiers",
    "src/core/game3/battle/anim_callbacks.lua": "animation identifiers",
    "src/core/game3/battle/anim_pack_fallback.lua": "animation identifiers",
    "src/core/game3/battle/anim_tasks.lua": "animation identifiers",
    "src/core/game3/battle/anim_templates.lua": "animation identifiers",
    "src/core/game3/battle/anim_vm.lua": "animation identifiers",
    "src/core/game3/battle/ball_open.lua": "asset paths",
    "src/core/game3/battle/catching.lua": "fallback OT name",
    "src/core/game3/battle/damage.lua": "stat identifiers",
    "src/core/game3/battle/effect_ctx.lua": "internal error messages",
    "src/core/game3/battle/effect_ids.lua": "effect identifiers",
    "src/core/game3/battle/moves.lua": "move identifiers (names come from the ROM pack)",
    "src/core/game3/battle/state.lua": "fallback name",
    "src/core/game3/battle/types.lua": "type identifiers",
    "src/core/game3/battle_bridge.lua": "internal error messages",
    "src/core/game3/battle_downgrade.lua": "move identifiers",
    "src/core/game3/bridge.lua": "log lines",
    "src/core/game3/collision.lua": "log lines",
    "src/core/game3/dataset.lua": "cache paths and log lines",
    "src/core/game3/display.lua": "log line",
    "src/core/game3/doors.lua": "asset paths",
    "src/core/game3/encounters.lua": "log lines",
    "src/core/game3/evolution.lua": "species identifiers",
    "src/core/game3/field.lua": "quest-log event keys",
    "src/core/game3/field_view.lua": "log lines and time-of-day identifiers",
    "src/core/game3/items.lua": "item identifiers (names come from the ROM pack)",
    "src/core/game3/m4a_player.lua": "log lines",
    "src/core/game3/m4a_sample.lua": "code comment string",
    "src/core/game3/map.lua": "internal error message",
    "src/core/game3/objects.lua": "movement identifiers and log lines",
    "src/core/game3/ow_sprites.lua": "asset paths",
    "src/core/game3/palette.lua": "internal error message",
    "src/core/game3/party.lua": "fallback OT name",
    "src/core/game3/player.lua": "log line",
    "src/core/game3/quest_log_recorder.lua": "quest-log event keys",
    "src/core/game3/runtime.lua": "log lines",
    "src/core/game3/save_schema_firered.lua": "default save names",
    "src/core/game3/scripting/collision.lua": "collision identifiers",
    "src/core/game3/scripting/ctx.lua": "fallback placeholder names",
    "src/core/game3/scripting/flags.lua": "flag identifiers",
    "src/core/game3/scripting/gfx_ids.lua": "graphics identifiers",
    "src/core/game3/scripting/interaction_scripts.lua": "interaction identifiers (text comes from the ROM object pack)",
    "src/core/game3/scripting/ops_a.lua": "debug fallbacks and log lines",
    "src/core/game3/scripting/space.lua": "cache paths and log lines",
    "src/core/game3/scripting/text_ir.lua": "fallback placeholder names",
    "src/core/game3/tileset_anim.lua": "asset paths and log lines",
    "src/core/game3/tileset_native.lua": "asset paths and log lines",
    "src/core/game3/trainer_pic.lua": "ROM file names and asset paths",
    "src/core/game3/void_fill.lua": "labels passed to Strings() by option_rows.lua",
    "src/core/game3/warp.lua": "internal error message",
    "src/ui/game3/battle_chrome.lua": "asset paths",
    "src/ui/game3/frlg_font.lua": "asset paths and log lines",
    "src/ui/game3/intro_movie.lua": "scene identifiers",
    "src/ui/game3/map_name_popup.lua": "fallback name for a map with no id",
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


def scan_hardcoded_literals(checkout: str | Path) -> dict[str, list[tuple[int, str]]]:
    """Every text-looking literal in game3 that is not a Strings() argument."""
    root = Path(checkout)
    found: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for directory in SCANNED_DIRS:
        for path in sorted((root / directory).rglob("*.lua")):
            relative = path.relative_to(root).as_posix()
            if "/anim_port/" in relative or path.name == "flags_table.lua":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if line.lstrip().startswith("--"):
                    continue
                for match in _LITERAL.finditer(line):
                    value = next(group for group in match.groups() if group is not None)
                    if not _looks_like_text(value):
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


def player_visible_files(checkout: str | Path) -> dict[str, list[tuple[int, str]]]:
    return {path: rows for path, rows in scan_hardcoded_literals(checkout).items()
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
