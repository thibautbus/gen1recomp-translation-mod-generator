"""Identify translated text shown on Gen1Recomp's battle screen, or right
around a scripted trainer battle, so the optional line-break reflow
(pipeline.text_pacing) can leave it alone.

Battle messages are often choreographed with animations/timing the pacing
pass has no way to know about (see pipeline.join.SENDOUT_ENGINE_KEYS for a
hand-tuned example), and some scripted lines render on the battle screen
itself rather than the normal field box.  Two independent signals feed the
exclusion set:

- Engine-literal strings whose only ``Strings(...)`` callsite lives under
  ``src/battle/`` (see pipeline.engine_scope.iter_callsites).
- ``show_text``/``save_end_battle_text`` targets in ``data/scripts/*.lua``
  that sit on or next to a scripted trainer-battle opcode.
"""
from __future__ import annotations

import re
from pathlib import Path

# This opcode's own string argument is rendered directly on the battle
# screen (data/scripts/yellow_jessie_james.lua: "renders... on the battle
# screen ahead of MoneyForWinningText").
_DIRECT_BATTLE_TEXT_OPCODES = {"save_end_battle_text"}

# Opcodes that trigger or resolve a scripted trainer battle.  A show_text
# within WINDOW script entries of one of these is treated as battle-adjacent
# (pre-battle taunt, post-battle victory/defeat speech).
_BATTLE_TRIGGER_OPCODES = {"start_battle", "rival_battle", "static_battle", "check_battle_result"}

_SHOW_TEXT_OPCODE = "show_text"

_ENTRY_RE = re.compile(r'^\s*\{\s*"(\w+)"\s*(?:,\s*"((?:[^"\\]|\\.)*)")?')


def _script_entries(path: Path) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _ENTRY_RE.match(line)
        if match:
            entries.append((match.group(1), match.group(2)))
    return entries


def battle_adjacent_text_keys(scripts_root: str | Path, window: int = 4) -> set[str]:
    """Scan every ``data/scripts/*.lua`` file for text tied to a scripted
    trainer battle.  Returns the raw argument strings found: either a
    dialogue runtime ID (``_XxxText``, matching pipeline.join's qid ->
    runtime-key convention) or, for hardcoded lines, the literal English
    text as written in the script.
    """
    root = Path(scripts_root)
    keys: set[str] = set()
    if not root.is_dir():
        return keys
    for path in sorted(root.glob("*.lua")):
        entries = _script_entries(path)
        for index, (opcode, arg) in enumerate(entries):
            if opcode in _DIRECT_BATTLE_TEXT_OPCODES and arg:
                keys.add(arg)
            if opcode in _BATTLE_TRIGGER_OPCODES:
                lo, hi = max(0, index - window), min(len(entries), index + window + 1)
                for other_opcode, other_arg in entries[lo:hi]:
                    if other_opcode == _SHOW_TEXT_OPCODE and other_arg:
                        keys.add(other_arg)
    return keys


def gen1recomp_root(engine_source: str | Path) -> Path:
    """``engine_source`` is conventionally the ``src/`` checkout; battle
    scripts live in the sibling ``data/`` directory."""
    path = Path(engine_source)
    return path.parent if path.name == "src" else path


def is_excluded_qid(qid: str, excluded: set[str]) -> bool:
    """True if ``qid``'s Gen1Recomp runtime symbol(s) are in ``excluded``
    (see pipeline.join._symbols for the qid -> runtime-key convention)."""
    from .join import _symbols

    return bool(_symbols(qid) & excluded)
