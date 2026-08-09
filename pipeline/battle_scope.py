"""Identify the translated text the optional line-break reflow
(pipeline.text_pacing) is allowed to touch: plain qid-driven dialogue that
positively proves it renders through Gen1Recomp's ordinary, generic field
text box, as opposed to a battle screen or a display path hardcoded
elsewhere in the engine.

``reflow_safe_keys`` is a whitelist, not a blocklist: a qid is only ever
reflowed if its runtime symbol shows up in one of the *positive* sources
below (an actual structural pointer from the engine's own generated data
or scripts to that symbol, going through a known-generic display call). A
qid no positive source names -- including one added by a future engine
change none of these sources anticipate -- keeps its ROM-original line
breaks by default, the same as any qid in a catalog
``pipeline.mod._REFLOW_ELIGIBLE_CATALOGS`` doesn't even consider. The
*negative* signals (``dynamic_text_lookup_keys``, ``battle_adjacent_text_keys``,
``trainer_won_text_keys``) still run and are subtracted from the union of
positive sources as a second, independent check: a qid a positive source
names but a negative one also catches (e.g. because it's referenced both
generically and from a special-cased engine callsite) stays excluded.

Positive sources, each a real structural pointer to a runtime text
symbol, not a guess:

- ``data/generated/text_pointers.lua``: every map object's ``label`` --
  the resolved runtime symbol behind a ``data/generated/maps.lua`` NPC's
  ``text`` pointer, read generically by
  ``OverworldController.lua``/``Commands.lua`` via ``Game.data.text[key]``
  or ``Game.data:resolveText(...)`` when the player talks to that NPC.
  This is the bulk of ordinary overworld dialogue.
- ``data/generated/field.lua``: ``text``/``failText``/``passText``/
  ``afterText`` fields on badge gates, guards, and signs -- the same
  generic ``Game.data.text[key]`` display path as an NPC, just for
  non-NPC field objects (confirmed for badge gates specifically at
  ``OverworldController.lua``'s badge-gate handler).
- ``data/generated/pokemon.lua``: each species' ``dexEntry.text`` --
  ``DexEntryMenu.lua``'s ``game.data.text[e.text]`` lookup, the Pokédex
  entry screen, unrelated to battle.
- ``data/generated/trainer_headers.lua``'s ``battle``/``after`` fields
  (not ``won``, see ``trainer_won_text_keys``): the pre-fight challenge
  line and the post-defeat rematch line, both plain
  ``TextBox.new``/``Game.stack:push`` calls on the field
  (``OverworldController.lua``'s ``engageTrainer``/post-defeat branch),
  never the battle screen itself.
- ``data/scripts/*.lua``'s ``show_text`` targets *outside* any battle
  zone (see ``battle_adjacent_text_keys`` for what "zone" means) --
  ordinary scripted cutscene/event dialogue, the same state machine as
  the blocklist below, just reading its complement.

Red, Blue and Yellow each import their own ROM into separate generated
files (root/``blue``/``yellow``, see ``trainer_won_text_keys``); every
positive source here is read across all three and merged the same way,
so a Yellow-only (or Blue-only) NPC/trainer/sign is covered too.

Measured against the real generated "dialogue" catalog (2582 qids), this
whitelist covers ~52% of it. The rest keeps its ROM-original line breaks
by default -- some of it genuinely risky (battle-screen defeat lines for
scripted, non-generic trainers; engine-hardcoded UI sequences like the
intro OakSpeech screen), some of it simply not yet audited (e.g. no
generic per-item/move description text pointer was found in
``items.lua``/``moves.lua``/``trainers.lua`` -- checked and empty, not
skipped). Either way, "not proven safe" already means "not reflowed"
here, so an unaudited case degrades to a missed optimization, never a
broken display.
"""
from __future__ import annotations

import re
from pathlib import Path

# This opcode's own string argument is rendered directly on the battle
# screen (data/scripts/yellow_jessie_james.lua: "renders... on the battle
# screen ahead of MoneyForWinningText").
_DIRECT_BATTLE_TEXT_OPCODES = {"save_end_battle_text"}

# Opcodes that trigger or resolve a scripted trainer battle: everything
# from here forward is "inside" that battle's aftermath until the scene
# visibly resets (see _SCENE_RESET_OPCODES below).
_BATTLE_TRIGGER_OPCODES = {"start_battle", "rival_battle", "static_battle", "check_battle_result"}

# The NPC leaving (hide_object) or the screen changing (warp/fade): once
# any of these fires, a battle's aftermath is over and later show_text
# entries in the same file are a new, unrelated story beat again.
_SCENE_RESET_OPCODES = {"hide_object", "warp", "fade"}

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
# -> PrintEndBattleText, "before MoneyForWinningText"); `battle` and `after`
# are both plain TextBox.new calls on the field (see module docstring).
_TRAINER_WON_RE = re.compile(r'\bwon\s*=\s*"([^"]+)"')
_TRAINER_BATTLE_RE = re.compile(r'\bbattle\s*=\s*"([^"]+)"')
_TRAINER_AFTER_RE = re.compile(r'\bafter\s*=\s*"([^"]+)"')

# data/generated/text_pointers.lua: { TEXT_XXX = { label = "...", ... } }
# per map -- label is the resolved runtime symbol regardless of whether
# this particular pointer has a Lua-side "text" value yet or is still
# "asm = true" (extraction-provenance metadata, not a different runtime
# path): both kinds of entry resolve to the same generic display call.
_TEXT_POINTER_LABEL_RE = re.compile(r'\blabel\s*=\s*"([^"]+)"')

# data/generated/field.lua: badge-gate/guard/sign text pointer fields.
# "textFacing"/"gamefreakText" (a direction word and an intro-screen
# image asset respectively) share the "text" substring but aren't text
# pointers, hence matching the field names exactly rather than by
# substring.
_FIELD_TEXT_RE = re.compile(r'\b(?:text|failText|passText|afterText)\s*=\s*"([^"]+)"')

# data/generated/pokemon.lua: dexEntry = { ..., text = "_XxxDexEntry", ... }
# -- no nested {} between the field's opening brace and its "text" value.
_DEX_ENTRY_TEXT_RE = re.compile(r"dexEntry\s*=\s*\{[^}]*?\btext\s*=\s*\"([^\"]+)\"", re.S)


def _script_entries(path: Path) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        # finditer, not search-first-match-only: a line can hold more than
        # one entry (e.g. "{ face_player }, { show_text, ... },"), and a
        # dropped opcode could hide a real battle trigger or show_text from
        # the zone state machine.
        for match in _ENTRY_RE.finditer(line):
            entries.append((match.group(1), match.group(2)))
    return entries


def _scan_scripts(scripts_root: str | Path) -> tuple[set[str], set[str]]:
    """One state-machine pass over every ``data/scripts/*.lua`` file,
    returning ``(battle_adjacent, safe)`` show_text/save_end_battle_text
    targets -- see ``battle_adjacent_text_keys``/``script_dialogue_text_keys``
    for what each half means. Shared so the blocklist and whitelist read
    of the same scripts can never drift out of sync with each other."""
    root = Path(scripts_root)
    battle_keys: set[str] = set()
    safe_keys: set[str] = set()
    if not root.is_dir():
        return battle_keys, safe_keys
    for path in sorted(root.glob("*.lua")):
        in_battle_zone = False
        for opcode, arg in _script_entries(path):
            if opcode in _DIRECT_BATTLE_TEXT_OPCODES:
                # save_end_battle_text *registers* text the battle shows
                # once it ends; it's routinely written before start_battle
                # in the script (see yellow_jessie_james.lua), so its
                # position relative to the zone doesn't matter.
                if arg:
                    battle_keys.add(arg)
                continue
            if opcode in _BATTLE_TRIGGER_OPCODES:
                in_battle_zone = True
                continue
            if opcode in _SCENE_RESET_OPCODES:
                in_battle_zone = False
                continue
            if opcode == _SHOW_TEXT_OPCODE and arg:
                (battle_keys if in_battle_zone else safe_keys).add(arg)
    return battle_keys, safe_keys


def battle_adjacent_text_keys(scripts_root: str | Path) -> set[str]:
    """Scan every ``data/scripts/*.lua`` file for text tied to a scripted
    trainer battle: a ``show_text`` is battle-adjacent (pre-fight taunt
    excepted) from a battle-trigger opcode until the scene next resets
    (``_SCENE_RESET_OPCODES``) -- a state machine over the file's entries
    in order, not a whole-file or fixed-distance rule, since one script
    file routinely holds several unrelated encounters back to back (see
    the module docstring).  A ``show_text`` *before* any trigger (or after
    a reset, before the next one) is a plain pre-fight challenge line, the
    same kind of ordinary field TextBox call as ``trainer_headers.lua``'s
    ``battle`` field, so it's in ``script_dialogue_text_keys`` instead.
    Returns the raw argument strings found: either a dialogue runtime ID
    (``_XxxText``, matching pipeline.join's qid -> runtime-key convention)
    or, for hardcoded lines, the literal English text as written in the
    script.
    """
    battle_keys, _safe_keys = _scan_scripts(scripts_root)
    return battle_keys


def script_dialogue_text_keys(scripts_root: str | Path) -> set[str]:
    """The complement of ``battle_adjacent_text_keys``: every ``show_text``
    target found *outside* an active battle zone -- ordinary scripted
    cutscene/event dialogue, safe to reflow."""
    _battle_keys, safe_keys = _scan_scripts(scripts_root)
    return safe_keys


def gen1recomp_root(engine_source: str | Path) -> Path:
    """``engine_source`` is conventionally the ``src/`` checkout; battle
    scripts live in the sibling ``data/`` directory."""
    path = Path(engine_source)
    return path.parent if path.name == "src" else path


def _engine_layers(engine_source: str | Path) -> tuple[Path, ...]:
    """Red (root), Blue and Yellow each import their own ROM into a
    separate generated-data tree (pipeline.builder.build's ``import_rom``
    calls); a layer whose ROM wasn't imported for this build simply has
    no files under its root and contributes nothing wherever this is
    used."""
    root = gen1recomp_root(engine_source)
    return (root, root / "blue", root / "yellow")


def _read_layers(engine_source: str | Path, relative_path: str) -> list[str]:
    texts: list[str] = []
    for layer_root in _engine_layers(engine_source):
        path = layer_root / relative_path
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return texts


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
    directly on the battle screen, not after returning to the field.

    Four Yellow-only encounters (Rocket Hideout B4F, Route 9, two
    Viridian Forest trainers) have no Red/Blue equivalent, so all three
    layers are read and merged (see ``_engine_layers``); missing one
    would silently reflow that version's defeat line.
    """
    keys: set[str] = set()
    for text in _read_layers(engine_source, "data/generated/trainer_headers.lua"):
        keys |= set(_TRAINER_WON_RE.findall(text))
    return keys


def trainer_challenge_text_keys(engine_source: str | Path) -> set[str]:
    """Every generic trainer's ``battle`` (pre-fight challenge) and
    ``after`` (post-defeat rematch) line -- both plain field TextBox calls
    (see module docstring), unlike ``won``. Read across all three
    Red/Blue/Yellow layers the same way as ``trainer_won_text_keys``."""
    keys: set[str] = set()
    for text in _read_layers(engine_source, "data/generated/trainer_headers.lua"):
        keys |= set(_TRAINER_BATTLE_RE.findall(text)) | set(_TRAINER_AFTER_RE.findall(text))
    return keys


def _symbol_forms(raw_values: set[str]) -> set[str]:
    """Both the bare and ``_``-prefixed spelling of every value in
    ``raw_values``. ``pipeline.join._symbols`` generates both forms from a
    dotted corpus qid, but a catalog built from a Modkit worksheet (the
    normal build; see pipeline.mod.generate_mod's ``modkit_worksheet``)
    keys its Lua table directly by the *exact* runtime symbol -- and
    checks membership against that key directly, without going through
    _symbols() -- so a source whose own naming convention only gives one
    form (``text_pointers.lua``'s ``label``, ``field.lua``'s text fields:
    both bare, unlike ``trainer_headers.lua``/dynamic-lookup/scripts,
    which already come back ``_``-prefixed) must be normalized to both,
    or it silently fails to match half the qids it should."""
    forms: set[str] = set()
    for value in raw_values:
        bare = value[1:] if value.startswith("_") else value
        forms.add(bare)
        forms.add("_" + bare)
    return forms


def map_text_pointer_keys(engine_source: str | Path) -> set[str]:
    """Every map object's resolved runtime text symbol
    (``data/generated/text_pointers.lua``'s ``label`` fields) -- the bulk
    of ordinary overworld NPC dialogue, read generically off
    ``data.text[key]`` when the player talks to that NPC. Read across all
    three Red/Blue/Yellow layers."""
    keys: set[str] = set()
    for text in _read_layers(engine_source, "data/generated/text_pointers.lua"):
        keys |= set(_TEXT_POINTER_LABEL_RE.findall(text))
    return _symbol_forms(keys)


def field_dialogue_text_keys(engine_source: str | Path) -> set[str]:
    """Badge-gate/guard/sign text (``data/generated/field.lua``'s
    ``text``/``failText``/``passText``/``afterText`` fields) -- the same
    generic field-object display path as an NPC. Read across all three
    Red/Blue/Yellow layers."""
    keys: set[str] = set()
    for text in _read_layers(engine_source, "data/generated/field.lua"):
        keys |= set(_FIELD_TEXT_RE.findall(text))
    return _symbol_forms(keys)


def dex_entry_text_keys(engine_source: str | Path) -> set[str]:
    """Every species' Pokédex entry text (``data/generated/pokemon.lua``'s
    ``dexEntry.text`` fields) -- the Pokédex info screen, unrelated to
    battle. Read across all three Red/Blue/Yellow layers."""
    keys: set[str] = set()
    for text in _read_layers(engine_source, "data/generated/pokemon.lua"):
        keys |= set(_DEX_ENTRY_TEXT_RE.findall(text))
    return keys


def reflow_safe_keys(engine_source: str | Path, scripts_root: str | Path | None = None) -> set[str]:
    """The full positive whitelist: the union of every source above that
    positively proves a qid renders through a known-generic display path,
    minus whatever the negative/blocklist signals independently catch
    (see module docstring for why both run). ``scripts_root`` defaults to
    ``gen1recomp_root(engine_source) / "data" / "scripts"``.
    """
    if scripts_root is None:
        scripts_root = gen1recomp_root(engine_source) / "data" / "scripts"
    positive = (
        map_text_pointer_keys(engine_source)
        | field_dialogue_text_keys(engine_source)
        | dex_entry_text_keys(engine_source)
        | trainer_challenge_text_keys(engine_source)
        | script_dialogue_text_keys(scripts_root)
    )
    negative = (
        dynamic_text_lookup_keys(engine_source)
        | battle_adjacent_text_keys(scripts_root)
        | trainer_won_text_keys(engine_source)
    )
    # Both sides normalized to bare + "_"-prefixed before subtracting: a
    # source on either side may only produce one spelling (see
    # _symbol_forms), and the bare half of a doubly-referenced symbol must
    # not survive just because the negative signal that caught it only
    # ever emits the "_"-prefixed form.
    return _symbol_forms(positive) - _symbol_forms(negative)


def is_excluded_qid(qid: str, excluded: set[str]) -> bool:
    """True if ``qid``'s Gen1Recomp runtime symbol(s) are in ``excluded``
    (see pipeline.join._symbols for the qid -> runtime-key convention)."""
    from .join import _symbols

    return bool(_symbols(qid) & excluded)


def is_reflow_safe_qid(qid: str, safe_keys: set[str]) -> bool:
    """True if ``qid``'s Gen1Recomp runtime symbol(s) are in
    ``safe_keys`` (see ``reflow_safe_keys``). Same symbol-set membership
    test as ``is_excluded_qid``, named for the whitelist call sites so
    the polarity reads correctly at a glance."""
    from .join import _symbols

    return bool(_symbols(qid) & safe_keys)
