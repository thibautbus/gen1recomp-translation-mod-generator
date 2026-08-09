"""Identify translated text the optional line-break reflow
(pipeline.text_pacing) should leave alone: anything hooked into
Gen1Recomp's own Lua code, or scripted around a trainer battle, rather
than plain qid-driven dialogue/catalog content.

Fails safe by construction rather than by exhaustive detection: an
unrecognized or future engine pattern should fall on the "don't touch"
side, not the "optimize" side, so a miss here degrades to the
ROM-original line breaks (today's behavior) instead of risking a broken
battle-screen display. Three signals feed the exclusion set, each
deliberately erring toward excluding more rather than less:

- The entire ``strings``/engine-literal catalog is never reflowed
  (pipeline.mod.generate_mod hardcodes this directly; nothing here is
  needed for it) -- any text with a dedicated code callsite anywhere in
  the engine is out of scope for reflow, not just in ``src/battle/``.
- Every ``self.data.text._Xxx``/``data.text._Xxx`` dynamic lookup
  anywhere in ``src/`` (not just ``src/battle/``): a qid read straight
  off the dialogue table by engine code, wherever that code lives, is
  engine-adjacent and stays untouched.
- ``show_text``/``save_end_battle_text`` targets in any
  ``data/scripts/*.lua`` file that contains at least one scripted
  trainer-battle opcode: the whole file is treated as battle-adjacent,
  not just entries near the opcode.
"""
from __future__ import annotations

import re
from pathlib import Path

# This opcode's own string argument is rendered directly on the battle
# screen (data/scripts/yellow_jessie_james.lua: "renders... on the battle
# screen ahead of MoneyForWinningText").
_DIRECT_BATTLE_TEXT_OPCODES = {"save_end_battle_text"}

# Opcodes that trigger or resolve a scripted trainer battle.  Any
# show_text elsewhere in the same file is treated as battle-adjacent too
# (pre-battle taunt, post-battle victory/defeat speech): scripts are
# organized per area/story chunk, so a file with a battle in it is safer
# to exclude wholesale than to try to bound how far its narrative reaches.
_BATTLE_TRIGGER_OPCODES = {"start_battle", "rival_battle", "static_battle", "check_battle_result"}

_SHOW_TEXT_OPCODE = "show_text"

# Not anchored to the start of the line: table.insert(rows, { "start_battle",
# ... }) (data/scripts/oaks_lab.lua's conditionally-built rival encounter,
# among others) puts the entry after a wrapping call rather than as a bare
# list literal, and a battle opcode hidden that way must still be found.
_ENTRY_RE = re.compile(r'\{\s*"(\w+)"\s*(?:,\s*"((?:[^"\\]|\\.)*)")?')

# self.data.text._Rival1WinText-style lookups: read straight off the
# dialogue table at runtime instead of a literal Strings(...)/romText(...)
# call, so iter_callsites can't see them -- it deliberately skips any call
# whose argument isn't a literal string.  Scanned across all of src/, not
# just src/battle/: a qid hooked into engine code anywhere is treated the
# same way, regardless of which module owns it.
_DYNAMIC_TEXT_RE = re.compile(r"\.text\.(_\w+)")

# data/generated/trainer_headers.lua: one {after, battle, event, won} record
# per generic trainer.  Only `won` renders on the battle screen itself
# (src/world/OverworldController.lua's engageTrainer -> battle.endBattleText
# -> PrintEndBattleText, "before MoneyForWinningText").  `battle` (the
# pre-fight challenge) and `after` (talking to the trainer post-defeat) are
# both plain TextBox.new calls on the field and stay eligible for reflow.
_TRAINER_WON_RE = re.compile(r'\bwon\s*=\s*"([^"]+)"')


def _script_entries(path: Path) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _ENTRY_RE.search(line)
        if match:
            entries.append((match.group(1), match.group(2)))
    return entries


def battle_adjacent_text_keys(scripts_root: str | Path) -> set[str]:
    """Scan every ``data/scripts/*.lua`` file; if it contains a scripted
    trainer-battle opcode, every ``show_text``/``save_end_battle_text``
    argument in that file is treated as battle-adjacent.  Returns the raw
    argument strings found: either a dialogue runtime ID (``_XxxText``,
    matching pipeline.join's qid -> runtime-key convention) or, for
    hardcoded lines, the literal English text as written in the script.
    """
    root = Path(scripts_root)
    keys: set[str] = set()
    if not root.is_dir():
        return keys
    for path in sorted(root.glob("*.lua")):
        entries = _script_entries(path)
        if not any(opcode in _BATTLE_TRIGGER_OPCODES for opcode, _ in entries):
            continue
        for opcode, arg in entries:
            if arg and (opcode == _SHOW_TEXT_OPCODE or opcode in _DIRECT_BATTLE_TEXT_OPCODES):
                keys.add(arg)
    return keys


def gen1recomp_root(engine_source: str | Path) -> Path:
    """``engine_source`` is conventionally the ``src/`` checkout; battle
    scripts live in the sibling ``data/`` directory."""
    path = Path(engine_source)
    return path.parent if path.name == "src" else path


def dynamic_text_lookup_keys(engine_source: str | Path) -> set[str]:
    """Every runtime ID read directly off the dialogue table anywhere in
    ``src/`` (e.g. ``self.data.text._Rival1WinText``) rather than through a
    literal call -- the one gap ``iter_callsites`` leaves, since it
    deliberately skips dynamic/variable lookups.  Deliberately not scoped
    to ``src/battle/``: any qid engine code reaches for directly, in any
    module, is treated as off-limits for reflow.
    """
    src_dir = gen1recomp_root(engine_source) / "src"
    keys: set[str] = set()
    if not src_dir.is_dir():
        return keys
    for path in sorted(src_dir.rglob("*.lua")):
        for match in _DYNAMIC_TEXT_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            keys.add(match.group(1))
    return keys


def trainer_won_text_keys(engine_source: str | Path) -> set[str]:
    """Every generic trainer's post-battle 'won' (defeat) line -- shown
    directly on the battle screen, not after returning to the field."""
    path = gen1recomp_root(engine_source) / "data" / "generated" / "trainer_headers.lua"
    if not path.is_file():
        return set()
    return set(_TRAINER_WON_RE.findall(path.read_text(encoding="utf-8", errors="replace")))


def is_excluded_qid(qid: str, excluded: set[str]) -> bool:
    """True if ``qid``'s Gen1Recomp runtime symbol(s) are in ``excluded``
    (see pipeline.join._symbols for the qid -> runtime-key convention)."""
    from .join import _symbols

    return bool(_symbols(qid) & excluded)
