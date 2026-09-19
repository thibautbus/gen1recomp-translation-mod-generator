"""Build config/frlg/engine_scope.json from a gen1recomp checkout.

The scope lists every ``Strings()`` key the game3 (FireRed) runtime can look
up, and for each one the FireRed corpus row that says it, when the cart has
one.  Keys come from four places:

* literal ``Strings("...")`` and ``Strings.source("...")`` callsites under
  ``src/*/game3`` (a ``Strings.source`` table is translated where it is read);
* tables the runtime passes to ``Strings()`` through a variable, read from
  the engine's modules with LuaJIT (``_PROBE``);
* local tables and literal lists passed the same way, read from the source
  text (``LOCAL_SOURCES``), since no module exposes them;
* the ROM's move and ability descriptions, which the summary passes to
  ``Strings()``; they are ROM text, so they come from the headless
  extraction, not from the engine source.

A key is matched to a corpus row when the English row reads as the key, with
runtime values (buffers, battle placeholders) standing where the key has
directives (``engine_template``).  Rows reviewed by hand in an existing scope
keep their qid; a new key takes the first matching row in ``_QID_PREFERENCE``
order and lists the other rows under ``alternatives`` for review.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

from .engine_backlog import iter_literal_strings_callsites
from .frlg_join import (
    FRLG_ENGINE_SCOPE_SCHEMA,
    _corpus_parts,
    _key_parts,
    _loose,
    apply_fills,
    engine_template,
    is_battle_qid,
    load_frlg_corpus,
)
from .frlg_text import PretCharmap, load_charmap

ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = ROOT / "config" / "frlg" / "engine_scope.json"
GAME3_DIRS = ("src/core/game3", "src/ui/game3", "src/battle/game3", "src/world/game3")

# Tables that reach Strings() through a variable and that a module exposes.
# Each line printed is "<callsite>\t<value>".
_PROBE = r"""
package.path = "./?.lua;./?/init.lua;" .. package.path
love = require("tests.love_stub")
local out = {}
local function add(site, v)
  if type(v) == "string" and v ~= "" then out[#out + 1] = site .. "\t" .. v end
end
local function each(site, t, field)
  for _, v in pairs(t or {}) do add(site, field and type(v) == "table" and v[field] or v) end
end
local Rows = require("src.ui.game3.option_rows")
for _, g in ipairs(Rows.GROUPS) do add("src/ui/game3/option_rows.lua (Rows.GROUPS label)", g.label) end
for _, row in ipairs(Rows.build({ options = {} })) do add("src/ui/game3/option_rows.lua (row label)", row.label) end
local L = require("src.render.Letterbox"); for _, m in ipairs(L.MODES) do add("src/ui/game3/option_rows.lua (Letterbox.label)", L.label(m)) end
local V = require("src.core.VideoMode"); for _, m in ipairs({ "windowed", "borderless" }) do add("src/ui/game3/option_rows.lua (VideoMode.modeLabel)", V.modeLabel(m)) end
local O = require("src.core.Orientation"); for _, m in ipairs(O.MODES) do add("src/ui/game3/option_rows.lua (Orientation.modeLabel)", O.modeLabel(m)) end
local F = require("src.core.FaithfulRes"); for v = 0, 6 do add("src/ui/game3/option_rows.lua (FaithfulRes.label)", F.label(v)) end
local S = require("src.core.ScreenPosition"); for _, m in ipairs(S.MODES) do add("src/ui/game3/option_rows.lua (ScreenPosition.label)", S.label(m)) end
add("src/ui/game3/option_rows.lua (ScreenPosition.label)", "SKIN")
local C = require("src.core.FrameCap"); add("src/ui/game3/option_rows.lua (FrameCap.label)", C.label(C.DISPLAY))
local Y = require("src.core.VSync"); for _, m in ipairs(Y.MODES) do add("src/ui/game3/option_rows.lua (VSync.label)", Y.label(m)) end
add("src/ui/game3/option_rows.lua (VSync.label)", Y.label("adaptive"))
local P = require("src.core.Performance"); each("src/ui/game3/option_rows.lua (Performance.label)", P.LABELS)
local Z = require("src.render.Zoom"); for o = -4, 4 do add("src/ui/game3/option_rows.lua (Zoom.offsetLabel)", Z.offsetLabel(o)) end
local VF = require("src.core.game3.void_fill"); each("src/ui/game3/option_rows.lua (VoidFill.label)", VF.LABELS)
local TC = require("src.core.TouchControls"); for _, m in ipairs(TC.HAPTICS) do add("src/ui/game3/option_rows.lua (TouchControls.hapticLabel)", TC.hapticLabel(m)) end
local Secondary = require("src.core.game3.battle.effects.secondary")
each("src/core/game3/battle/effects/secondary.lua (Secondary.STAT_NAME)", Secondary.STAT_NAME)
local Types = require("src.core.game3.battle.types")
each("src/core/game3/battle/ui.lua (Types.get)", Types.NAME)
local SummaryData = require("src.core.game3.summary_data")
each("src/core/game3/summary_data.lua (SummaryData.NATURES)", SummaryData.NATURES)
local Storage = require("src.core.game3.storage")
each("src/ui/game3/box_storage_ui.lua (Storage.WALLPAPERS)", Storage.WALLPAPERS)
local Region = require("src.import.gba.region_map_extract")
each("src/ui/game3/region_map.lua (RegionExtract.SECTION_NAMES)", Region.SECTION_NAMES)
each("src/ui/game3/region_map.lua (RegionExtract.DUNGEON_DESCRIPTIONS)", Region.DUNGEON_DESCRIPTIONS)
local Sections = require("src.import.gba.map_sections_extract")
each("src/ui/game3/map_name_popup.lua (MapSectionsExtract.SECTIONS name)", Sections.SECTIONS, "name")
add("src/ui/game3/map_name_popup.lua (MapSectionsExtract.getInfo)", "CELADON DEPT.")
for floor = 1, 11 do add("src/ui/game3/map_name_popup.lua (floor label)", floor .. "F") end
for floor = 1, 4 do add("src/ui/game3/map_name_popup.lua (floor label)", "B" .. floor .. "F") end
add("src/ui/game3/map_name_popup.lua (floor label)", "ROOFTOP")
local ItemsData = require("src.core.game3.items_data")
each("src/ui/game3/bag_menu.lua (ItemsData.POCKET_LABEL)", ItemsData.POCKET_LABEL)
local Shop = require("src.ui.game3.shop_menu")
each("src/ui/game3/shop_menu.lua (ShopMenu.ROOT label)", Shop.ROOT, "label")
local Party = require("src.ui.game3.party_menu")
each("src/ui/game3/party_menu.lua (PartyMenu.ACTIONS)", Party.ACTIONS)
each("src/ui/game3/party_menu.lua (PartyMenu.ITEM_ACTIONS)", Party.ITEM_ACTIONS)
local FieldMoves = require("src.core.game3.field_moves")
for _, name in pairs(FieldMoves.MOVE_NAME_BY_ID) do
  add("src/ui/game3/party_menu.lua (field move label)", (tostring(name):gsub("_", " ")))
end
io.write(table.concat(out, "\n"))
"""

# Values Strings() receives from a local table or an inline list, read from
# the source: (path, selector, what).  A "table" selector names a table
# constructor (its string values are keys, except id/icon fields); a "lines"
# selector is a pattern, and every string literal on a matching line is a key.
LOCAL_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("src/core/game3/battle/items.lua", "table", "ENEMY_STAT_NAME"),
    ("src/core/game3/battle/items.lua", "lines", r"^\s*(local label = Strings\(\(\{|attack = \"ATTACK\", defense|accuracy = \"ACCURACY\")"),
    ("src/ui/game3/bag_menu.lua", "lines", r"(ACTIONS = \{|return \{ \")"),
    ("src/ui/game3/berry_pouch.lua", "table", "ACTIONS"),
    ("src/ui/game3/tm_case.lua", "table", "ACTIONS"),
    ("src/ui/game3/box_storage_ui.lua", "lines", r"(_activeActions = \{|boxActions = \{|or \{ \"CANCEL\" \})"),
    ("src/ui/game3/help_system.lua", "lines", r"(local controls=|or Help\.level=='main')"),
    ("src/ui/game3/new_game_scene.lua", "table", "CONTROLS_TEXT"),
    ("src/ui/game3/new_game_scene.lua", "table", "PIKA_TEXT"),
    ("src/ui/game3/new_game_scene.lua", "table", "OAK_TEXT"),
    ("src/ui/game3/new_game_scene.lua", "table", "MALE_NAMES@intro.nameChoice"),
    ("src/ui/game3/new_game_scene.lua", "table", "FEMALE_NAMES@intro.nameChoice"),
    ("src/ui/game3/new_game_scene.lua", "table", "RIVAL_NAMES@intro.nameChoice"),
    ("src/ui/game3/new_game_scene.lua", "lines", r"(^local HINT_NEXT|setTopBar\(\")"),
    ("src/ui/game3/option_menu.lua", "table", "HELP_TEXT"),
    ("src/ui/game3/option_rows.lua", "lines", r"(cartLabel\(c, \"\w+\", \{|^local FILTERS|uiLayout == \"dynamic\" and)"),
    ("src/core/VSync.lua", "table", "LABELS"),
    ("src/core/FrameCap.lua", "lines", r"FrameCap\.DISPLAY and \"DISPLAY\""),
    ("src/ui/game3/party_menu.lua", "lines", r"(ACTIONS = \{|actions\[#actions \+ 1\] = \"|local options = \{)"),
    ("src/ui/game3/party_menu.lua", "table", "statNames"),
    ("src/ui/game3/pc_menu.lua", "table", "storageOptions"),
    ("src/ui/game3/stat_growth.lua", "table", "STAT_NAMES"),
    ("src/ui/game3/summary_menu.lua", "table", "PAGE_TITLES"),
)

# Corpus families to prefer when several rows read as the same key.  Rows
# from _QID_LAST_RESORT are never used: Easy Chat words, quest-log phrases
# ("la CAVERNE AZUREE") and Pokédex categories share English with menu
# labels but not their translation.
_QID_PREFERENCE = (
    "frlg.common.strings.",
    "frlg.common.region_map_entries.",
    "frlg.common.battle_main.",
    "frlg.common.battle_message.",
    "frlg.common.",
    "frlg.script.",
)
# Context keys whose sense a corpus family pins down: a name choice is the
# cart's gNameChoice_* row, not the type or Easy Chat word it spells.
CONTEXT_FAMILIES: Mapping[str, str] = {"intro.nameChoice": ".gNameChoice_"}

# Keys whose automatic row is the wrong sense of the word, reviewed by hand:
# the row the cart uses in that spot, or None when no single row says the
# key and overrides/<language>/frlg/engine.json supplies it.
REVIEWED: Mapping[str, str | None] = {
    # battle menu labels, which the cart prints as one row (gText_BattleMenu)
    "FIGHT": None,
    "RUN": None,
    # the engine's type identifiers, which the cart abbreviates (gTypeNames)
    "FIGHTING": None,
    "ELECTRIC": None,
    "PSYCHIC": None,
    "MYSTERY": None,
    # the bag's register action (gOtherText_Register reads REGISTER)
    "SET": None,
    # new-game hint parts, which the cart prints as one row
    # (gText_ABUTTONNext_BBUTTONBack)
    "NEXT": None,
    "NEXT ": None,
    "BACK": None,
    # the party's battle action, not the PC's (gPCText_Shift)
    "SHIFT": "frlg.common.strings.gText_Shift",
    "option.battleStyle|SHIFT": "frlg.common.strings.gText_BattleStyleShift",
    "option.battleStyle|SET": "frlg.common.strings.gText_BattleStyleSet",
}
_QID_LAST_RESORT = (".easy_chat_", ".gEasyChatGroupName_", ".quest_log.", ".gPokedexEntries.", ".fame_checker.")

_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'')


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "f": "\f"}.get(m.group(1), m.group(1)), value)


def _table_values(text: str, name: str) -> list[str]:
    """String values of the table constructor assigned to ``name``."""
    match = re.search(r"(?<![\w.])" + re.escape(name) + r"\s*=\s*(\{|\")", text)
    if not match:
        raise ValueError(f"no table {name!r}")
    start = match.start(1)
    if match.group(1) == '"':  # a plain string constant (HELP_TEXT)
        literal = _LITERAL.match(text, start)
        return [_unescape(literal.group(1))]
    depth, index, values = 0, start, []
    while index < len(text):
        char = text[index]
        if char in "\"'":
            literal = _LITERAL.match(text, index)
            before = text[max(0, index - 12):index]
            if not re.search(r"\b(id|icon)\s*=\s*$|\[\s*$", before):
                values.append(_unescape(next(g for g in literal.groups() if g is not None)))
            index = literal.end()
            continue
        if text.startswith("--", index):
            index = text.find("\n", index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return values
        index += 1
    raise ValueError(f"unbalanced table {name!r}")


def _line_values(text: str, pattern: str) -> list[str]:
    values = []
    for line in text.splitlines():
        if re.search(pattern, line):
            code = line.split("--", 1)[0]
            values += [_unescape(next(g for g in m.groups() if g is not None)) for m in _LITERAL.finditer(code)]
    if not values:
        raise ValueError(f"no line matches {pattern!r}")
    return values


def local_source_values(engine: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path, selector, what in LOCAL_SOURCES:
        text = (engine / path).read_text(encoding="utf-8")
        if selector == "table":
            name, _, context = what.partition("@")
            values, label = _table_values(text, name), name
            if context:  # the runtime looks these up as Strings(value, context)
                values = [f"{context}|{value}" for value in values]
        else:
            values, label = _line_values(text, what), "literal list"
        for value in values:
            found.setdefault(value, f"{path} ({label})")
    return found


def reachable_by_file(engine: Path) -> dict[str, set[str]]:
    """Each value the runtime passes to Strings() through a variable -> the
    files that hold it (for the hardcoded-text audit)."""
    found: dict[str, set[str]] = {}
    for path, selector, what in LOCAL_SOURCES:
        text = (engine / path).read_text(encoding="utf-8")
        if selector == "table":
            name, _, _ = what.partition("@")
            values = _table_values(text, name)
        else:
            values = _line_values(text, what)
        for value in values:
            found.setdefault(value, set()).add(path)
    for value, site in {**probe_values(engine), **fallback_values(engine)}.items():
        found.setdefault(value, set()).add(site.split(" ", 1)[0].split(":", 1)[0])
    return found


def probe_values(engine: Path, luajit: str | None = None) -> dict[str, str]:
    luajit = luajit or shutil.which("luajit")
    if not luajit:
        raise RuntimeError("LuaJIT is needed to read the engine's tables")
    out = subprocess.run([luajit, "-e", _PROBE], cwd=engine, capture_output=True,
                         text=True, check=True).stdout
    found: dict[str, str] = {}
    for line in out.split("\n"):
        if "\t" in line:
            site, value = line.split("\t", 1)
            found.setdefault(value.replace("\\n", "\n"), site)
    return found


def rom_description_values(extracted: Path, luajit: str | None = None) -> dict[str, str]:
    """The ROM's move and ability descriptions and ability names, as shown."""
    luajit = luajit or shutil.which("luajit")
    path = extracted / "data" / "generated" / "gba" / "pokemon" / "descriptions.lua"
    probe = (
        f"local d = dofile({json.dumps(str(path))}) local out = {{}} "
        "for _, group in ipairs({ 'ABILITIES', 'MOVES' }) do "
        "for k, v in pairs(d[group] or {}) do out[#out + 1] = group .. '\\t' .. v:gsub('\\n', '\\\\n') end end "
        "io.write(table.concat(out, '\\n'))"
    )
    out = subprocess.run([luajit, "-e", probe], capture_output=True, text=True, check=True).stdout
    found: dict[str, str] = {}
    for line in out.split("\n"):
        if "\t" in line:
            group, value = line.split("\t", 1)
            site = "src/core/game3/summary_data.lua (ROM " + group.lower()[:-1] + " description)"
            found.setdefault(value.replace("\\n", "\n"), site)
    names = extracted / "data" / "generated" / "gba" / "pokemon" / "ability_names.lua"
    probe = (f"for id, v in pairs(dofile({json.dumps(str(names))})) do "
             "if id > 0 then io.write(v, '\\n') end end")
    out = subprocess.run([luajit, "-e", probe], capture_output=True, text=True, check=True).stdout
    for value in out.split("\n"):
        if value:
            found.setdefault(value, "src/core/game3/battle/abilities.lua (Abilities.name; ROM ability name)")
    return found


# A key is language-neutral when nothing in it needs translating: numbers,
# multipliers and refresh rates the option rows print.
def _neutral(value: str) -> bool:
    value = context_source(value)
    stripped = _key_parts(value)[0]
    return not re.search(r"[A-Za-z]{2}", "".join(stripped)) or bool(re.fullmatch(r"[0-9.]+(X|HZ)?", value))


# The literal lists LOCAL_SOURCES reads also hold identifiers compared on the
# same lines (Help.level == 'main', "a_button" icons, option keys).
def context_source(key: str) -> str:
    """The English source of a context key ("option.battleStyle|SHIFT")."""
    return key.split("|", 1)[1] if re.match(r"[a-z][\w.]*\|", key) else key


def _option_context_keys(engine: Path) -> dict[str, str]:
    """cartLabel values under their option's context (Strings(value, "option.key"))."""
    path = "src/ui/game3/option_rows.lua"
    text = (engine / path).read_text(encoding="utf-8")
    found = {}
    for match in re.finditer(r'cartLabel\(c, "(\w+)", \{([^}]*)\}\)', text):
        for literal in _LITERAL.finditer(match.group(2)):
            value = _unescape(next(g for g in literal.groups() if g is not None))
            found[f"option.{match.group(1)}|{value}"] = f"{path} (cartLabel {match.group(1)})"
    return found


def _identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*|[a-z]+(?:[A-Z][a-z0-9]*)+", value))


# Strings(expr or "fallback"): the fallback literal is a key the literal
# scanner cannot see, since the call's first argument is an expression.
_FALLBACK = re.compile(r'Strings\((?:[^()\n]|\([^()\n]*\))*?\bor ("(?:[^"\\\n]|\\.)*")\)')


def fallback_values(engine: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for directory in GAME3_DIRS:
        for path in sorted((engine / directory).rglob("*.lua")):
            rel = path.relative_to(engine).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in _FALLBACK.finditer(line.split("--", 1)[0]):
                    found.setdefault(_unescape(match.group(1)[1:-1]), f"{rel}:{number} (fallback)")
    return found


def collect_keys(engine: Path, extracted: Path | None = None) -> dict[str, dict]:
    keys: dict[str, dict] = {}
    for row in iter_literal_strings_callsites(engine / "src"):
        path = "src/" + row["path"] if not row["path"].startswith("src/") else row["path"]
        if not any(path.startswith(directory) for directory in GAME3_DIRS):
            continue
        keys.setdefault(row["source"], {"callsite": f"{path}:{row['line']}", "kind": "literal"})
    for value, site in {**local_source_values(engine), **probe_values(engine)}.items():
        kind = "floor" if "floor label" in site else "dynamic"
        keys.setdefault(value, {"callsite": site, "kind": kind})
    for value, site in fallback_values(engine).items():
        keys.setdefault(value, {"callsite": site, "kind": "dynamic"})
    for value, site in _option_context_keys(engine).items():
        keys.setdefault(value, {"callsite": site, "kind": "context"})
    if extracted is not None:
        for value, site in rom_description_values(extracted).items():
            keys.setdefault(value, {"callsite": site, "kind": "rom"})
    return {key: row for key, row in keys.items()
            if (row["kind"] == "floor" or not _neutral(key))
            and not (row["kind"] == "dynamic" and _identifier(key))}


def _shape(chunks: Iterable[str], loose: bool) -> str:
    return "\x01".join(_loose(chunk) if loose else chunk for chunk in chunks)


def corpus_index(corpus, charmap: PretCharmap) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """English rows by shape, read both with buffers as slots and spelled out."""
    exact: dict[str, list[str]] = {}
    loose: dict[str, list[str]] = {}
    for qid, english in zip(corpus.qids, corpus.english):
        for literal_buffers in (False, True):
            try:
                chunks, _values = _corpus_parts(english, charmap, "en", battle=is_battle_qid(qid),
                                                literal_buffers=literal_buffers)
            except Exception:
                continue
            exact.setdefault(_shape(chunks, False), []).append(qid)
            loose.setdefault(_shape(chunks, True), []).append(qid)
    return exact, loose


def _rank(qid: str) -> tuple[int, int, str]:
    last = any(family in qid for family in _QID_LAST_RESORT)
    for rank, prefix in enumerate(_QID_PREFERENCE):
        if qid.startswith(prefix):
            return int(last), rank, qid
    return int(last), len(_QID_PREFERENCE), qid


def match_qids(key: str, index, corpora: Mapping[str, object], charmap: PretCharmap) -> list[str]:
    """Corpus rows that read as ``key`` and translate cleanly in every language.

    Rows of the last-resort families never stand for a key on their own (an
    Easy Chat word shares its English with a menu label, not its sense).
    Best first: the rows whose
    translations agree with the most other candidates across the languages
    (the cart words one English label differently from menu to menu, and
    the most common wording is the one a reused label should take), then
    ``_QID_PREFERENCE`` order.
    """
    exact, loose = index
    chunks, _directives = _key_parts(context_source(key))
    candidates = exact.get(_shape(chunks, False)) or loose.get(_shape(chunks, True)) or []
    family = CONTEXT_FAMILIES.get(key.split("|", 1)[0]) if key != context_source(key) else None
    if family:
        candidates = [qid for qid in candidates if family in qid]
    values: dict[str, tuple[str, ...]] = {}
    for qid in sorted(set(candidates), key=_rank):
        try:
            row = []
            for corpus in corpora.values():
                english, target = corpus.row(qid)
                if not target:
                    raise ValueError("no translation")
                row.append(engine_template(context_source(key), english, target, charmap,
                                           corpus.language, battle=is_battle_qid(qid))[0])
        except ValueError:
            continue
        values[qid] = tuple(row)
    if not values:
        return []
    preferred = [qid for qid in values if not _rank(qid)[0]]
    if not preferred:
        return []
    def agreement(qid: str) -> int:
        return sum(1 for other in preferred for a, b in zip(values[qid], values[other]) if a == b)
    best = sorted(preferred, key=lambda qid: (-agreement(qid), _rank(qid)))
    return best + [qid for qid in sorted(values, key=_rank) if qid not in best]


# Name tables a row's placeholder may stand for when the engine spells the
# name out ("%s's DRIZZLE\nmade it rain!" for "[..]'s [B_SCR_ACTIVE_ABILITY]").
_NAME_FAMILIES = (".gAbilityNames.", ".gMoveNames.", ".gTypeNames.", ".gItems.")
_PLACEHOLDER = re.compile(r"\[[A-Z0-9_]+\]")


def _name_index(corpus) -> dict[str, str]:
    names: dict[str, str] = {}
    for qid, english in zip(corpus.qids, corpus.english):
        if any(family in qid for family in _NAME_FAMILIES) and english:
            names.setdefault(english, qid)
    return names


def _row_pattern(english: str) -> re.Pattern | None:
    """The English row as a regex: placeholders capture, breaks match any space."""
    pieces = _PLACEHOLDER.split(english)
    holders = _PLACEHOLDER.findall(english)
    if not holders:
        return None
    def literal(piece: str) -> str:
        parts = re.split(r"(?:\\[nlpc]|\s)+", piece)
        return r"\s+".join(re.escape(part) for part in parts)
    body = literal(pieces[0])
    for piece in pieces[1:]:
        body += "(.+?)" + literal(piece)
    return re.compile(body, re.S)


def derive_fills(unmatched: Iterable[str], corpora: Mapping[str, object], charmap: PretCharmap) -> dict[str, dict]:
    """Rows whose placeholders the key fills with a spelled-out name."""
    corpus = corpora["fr"]
    names = _name_index(corpus)
    rows = []
    for qid, english in zip(corpus.qids, corpus.english):
        pattern = _row_pattern(english)
        if pattern is not None:
            rows.append((qid, english, pattern, _PLACEHOLDER.findall(english)))
    found: dict[str, dict] = {}
    for key in unmatched:
        flat = re.sub(r"\s+", " ", key)
        for qid, english, pattern, holders in sorted(rows, key=lambda row: _rank(row[0])):
            match = pattern.fullmatch(key) or pattern.fullmatch(flat)
            if not match:
                continue
            fills = {}
            for holder, captured in zip(holders, match.groups()):
                if re.fullmatch(r"%[-+ #0]*\d*(?:\.\d+)?[A-Za-z]", captured):
                    continue
                if captured == "{" + holder[1:-1] + "}":  # the key spells the token out
                    continue
                if captured not in names or holder in fills:
                    fills = None
                    break
                fills[holder] = names[captured]
            if not fills:
                continue
            try:
                for language, loaded in corpora.items():
                    en, target = loaded.row(qid)
                    pairs = {h: loaded.row(q) for h, q in fills.items()}
                    if not target or any(not pair[1] for pair in pairs.values()):
                        raise ValueError("no translation")
                    engine_template(key, apply_fills(en, {h: p[0] for h, p in pairs.items()}),
                                    apply_fills(target, {h: p[1] for h, p in pairs.items()}),
                                    charmap, language, battle=is_battle_qid(qid))
            except ValueError:
                continue
            found[key] = {"qid": qid, "fill": fills}
            break
    return found


def build_scope(engine: Path, corpus_dir: Path, charmap: PretCharmap, *, extracted: Path | None,
                previous: Mapping[str, Mapping] | None = None, revision: str = "") -> dict:
    keys = collect_keys(engine, extracted)
    corpora = {language: load_frlg_corpus(corpus_dir, language) for language in ("fr", "de", "es", "it")}
    index = corpus_index(corpora["fr"], charmap)
    previous = previous or {}
    for key, row in keys.items():
        old = previous.get(key, {})
        if old.get("qid"):
            row["qid"] = old["qid"]
            if old.get("fill"):
                row["fill"] = old["fill"]
            continue
        if old.get("fill"):
            continue
        found = match_qids(key, index, corpora, charmap)
        if found:
            row["qid"] = found[0]
            if len(found) > 1:
                row["alternatives"] = found[1:]
    for key, qid in REVIEWED.items():
        if key in keys:
            for field in ("qid", "alternatives", "fill"):
                keys[key].pop(field, None)
            if qid:
                keys[key]["qid"] = qid
            keys[key]["reviewed"] = True
    unmatched = [key for key, row in keys.items() if not row.get("qid") and not row.get("reviewed")]
    for key, match in derive_fills(unmatched, corpora, charmap).items():
        keys[key].update(match)
    return {
        "schema": FRLG_ENGINE_SCOPE_SCHEMA,
        "version": 1,
        "source_revision": revision,
        "description": (
            "Strings() keys reachable from gen1recomp's game3 (FireRed) runtime, generated by "
            "pipeline/frlg_engine_scope.py: literal Strings()/Strings.source() callsites under "
            "src/*/game3, tables and lists the runtime passes to Strings() through a variable, and "
            "the ROM's move and ability descriptions.  Language-neutral values (numbers, "
            "multipliers, refresh rates) are left out.  A qid names the FireRed cart's own row for "
            "the key; runtime values in that row fill the key's directives, renumbered when the "
            "translation orders them differently.  Keys without a qid are port-added or have no "
            "clean corpus row, and are covered by overrides/<language>/frlg/engine.json."
        ),
        "keys": dict(sorted(keys.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--engine", type=Path, default=ROOT / ".cache" / "dependencies" / "gen1recomp")
    parser.add_argument("--extracted", type=Path, default=ROOT / ".cache" / "firered" / "extracted" / "cache")
    parser.add_argument("--corpus", type=Path,
                        default=ROOT / ".cache" / "dependencies" / "poke-corpus" / "corpus" / "FireRedLeafGreen")
    parser.add_argument("--charmap", type=Path, default=ROOT / ".cache" / "dependencies" / "pret" / "charmap" / "charmap.txt")
    parser.add_argument("--revision", required=True, help="the gen1recomp commit --engine is checked out at")
    parser.add_argument("--out", type=Path, default=SCOPE_PATH)
    args = parser.parse_args(argv)
    previous = {}
    if args.out.is_file():
        previous = json.loads(args.out.read_text(encoding="utf-8")).get("keys", {})
    scope = build_scope(args.engine, args.corpus, load_charmap(args.charmap), extracted=args.extracted,
                        previous=previous, revision=args.revision)
    args.out.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    matched = sum(1 for row in scope["keys"].values() if row.get("qid"))
    print(f"{len(scope['keys'])} keys, {matched} matched to a corpus row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
