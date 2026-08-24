from __future__ import annotations

import re
from collections import Counter
from typing import Mapping

TOKEN_RE = re.compile(r"(?:<[^>]+>|\{[^}]+\}|\\(?:[nrt]|x[0-9A-Fa-f]{2}))")
# <ENEMY> joins PLAYER/RIVAL/TARGET/USER: Gold's battle text substitutes the
# opposing Pokemon/trainer name there the same way RBY substitutes the
# other four. Matched here in its
# POST-corpus_to_engine spelling ("{ENEMY}", from _CORPUS_EXPANSIONS below),
# the same as its siblings.
#
# RAM/NUM/STRBUF are bare here on purpose (no mandatory ":arg"): RBY's own
# extracted text.lua threads the RAM variable name into the token
# ("{RAM:wBattleMonNick}", "{NUM:wDayCareTotalCost, 2, ...}"), but Gold's
# RomExtractorGen2.lua:decodeGen2Text does not -- verified against a real
# ROM: TX_STRINGBUFFER and TX_DECIMAL decode to bare "{STRBUF}"/"{NUM}"
# ("Got {NUM} for\n{STRBUF}(S)."), because the call site fills the value
# in at runtime and the byte stream itself never names which buffer.
# check_placeholders below compares by family (ignoring any ":arg") so a
# corpus-side "{NUM:hMoneyTemp, 3, 6}" (from _CORPUS_EXPANSIONS'
# {text_decimal ...} conversion) is not flagged as "unexpected" against a
# Gold pointer's bare engine-side "{NUM}". STRBUF is treated as the RAM
# family: both mean "a runtime buffer's contents go here".
DYNAMIC_TOKEN_RE = re.compile(r"\{(?:PLAYER|RIVAL|TARGET|USER|ENEMY|ID|RAM(?::[^}]+)?|NUM(?::[^}]+)?|STRBUF)\}")

_CORPUS_EXPANSIONS = {
    "#": "POKé",
    "<PKMN>": "POKéMON",
    "<PC>": "PC",
    "<TM>": "TM",
    "<TRAINER>": "TRAINER",
    "<ROCKET>": "ROCKET",
    "<……>": "……",
    "<LV>": "{LV}",
    "<PLAYER>": "{PLAYER}",
    "<RIVAL>": "{RIVAL}",
    "<TARGET>": "{TARGET}",
    "<USER>": "{USER}",
    "<ENEMY>": "{ENEMY}",
    "<ID>": "{ID}",
    "<PARA>": "\f",
    "<PAGE>": "\f",
    "<LINE>": "\n",
    "<CONT>": "\v",
    "<NEXT>": "\n",
    "<DONE>": "",
    "<PROMPT>": "",
    "<NULL>": "",
    "@": "",
    # Gold-only, verified against RomExtractorGen2.lua:decodeGen2Text's own
    # TEXT_NO_GLYPH table (its comment: "TX_LOW / TX_SCROLL / TX_PAUSE /
    # TX_WAIT_BUTTON / TX_DAY and the six TX_SOUND_* jingles: box and timing
    # commands that print nothing... consumed here rather than printed").
    # The engine's own ROM-side decoder already treats these as invisible
    # to text content, so mapping them to "" here is not a silent guess: it
    # matches the one place that already decodes real ROM bytes at these
    # positions. Re-verified directly against the decode loop itself
    # (RomExtractorGen2.lua, the `elseif not inString and TEXT_NO_GLYPH[b]`
    # branch, ~line 2488): these bytes fall into the same branch as
    # TX_PROMPT_BUTTON (0x06) and produce literally no entry in `out` --
    # not a placeholder, not a marker, nothing. A translated pointer's text
    # is the decoded Lua string itself (mod.content.text:override sets the
    # already-decoded value, never a raw ROM byte stream), so there is no
    # mechanism by which leaving one of these tokens in engine-side text
    # could reproduce whatever timing/audio the original ROM byte drove --
    # the decode step that byte would have driven already happened once,
    # upstream of this table, for both the English original and any
    # translation sharing its pointer. Dropping the corpus's markup here
    # matches that decoder exactly; it is not a guess and not a gap.
    "{sound_caught_mon}": "",
    "{sound_dex_fanfare_50_79}": "",
    "{sound_dex_fanfare_80_109}": "",
    "{sound_item}": "",
    "{sound_slot_machine_start}": "",
    "{text_pause}": "",
    "{text_promptbutton}": "",
    "{text_today}": "",
    "{text_low}": "",
    # <POKE> is a full "POKé" compression byte, same idea as RBY's "#"
    # (verified: corpus has "<POKE>GEAR@" for the POKéGEAR item name).
    "<POKE>": "POKé",
    # <BSP>/<WBR> are narrow-display line-break points inside compound
    # place names, not glyphs (verified: "NEW BARK<BSP>TOWN@",
    # "DOUBLON<WBR>VILLE@" in the French Goldenrod-area map names) -- a
    # plain space renders the same names correctly at normal widths.
    "<BSP>": " ",
    "<WBR>": " ",
    "<LF>": "\n",
    # Japanese Gold uses single-byte abbreviations for common words and
    # particles. These expansions mirror pret/pokecrystal's charmap.asm;
    # the full-width spaces are part of the original abbreviations.
    "<NI>": "に　",
    "<TTE>": "って",
    "<WO>": "を　",
    "<TA!>": "た！",
    "<KOUGEKI>": "こうげき",
    "<WA>": "は　",
    "<NO>": "の　",
    "<ROUTE>": "ばん　どうろ",
    "<WATASHI>": "わたし",
    "<KOKO_WA>": "ここは",
    "<GA>": "が　",
    # poke-corpus also names two precomposed Japanese glyphs in lowercase.
    "<zu>": "ず",
    "<do>": "ど",
    # Gold text controls that do not draw a glyph. _CONT has the same
    # layout effect as CONT; SCROLL and DEXEND only control the text box.
    "<_CONT>": "\v",
    "<SCROLL>": "",
    "<DEXEND>": "",
    # <PO>/<KE> are deliberately NOT mapped, matching this table's existing
    # RBY precedent for <PK>/<MN>: verified (corpus has "<PO><KE>@" next to
    # the naming-screen alphabet row, exactly like RBY's "<PK><MN>@") to be
    # two-piece fragments of the "POKé" glyph used only in the naming
    # screen's character grid, not in prose. RBY's own <PK>/<MN> ship today
    # via hand-authored engine-string overrides rather than a table entry
    # here (see corpus_to_engine's docstring); <PO>/<KE> should follow the
    # same path if either ever surfaces in `strings`-registry content.
}


def known_literal_tokens() -> frozenset[str]:
    """Tokens corpus_to_engine converts away -- safe to see in raw corpus
    text, never in shipped output. Callers auditing final translations
    (pipeline/gs_join.py:audit_join) check against this rather than the
    private expansion table directly.
    """
    return frozenset(_CORPUS_EXPANSIONS)


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def convert_tokens(text: str, mapping: Mapping[str, str] | None = None) -> str:
    mapping = mapping or {}
    return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def corpus_to_engine(text: str, *, bare_dynamic_tokens: bool = False) -> str:
    """Convert poke-corpus' pret syntax to Gen1Recomp's extracted text form.

    A token missing from _CORPUS_EXPANSIONS passes through unconverted --
    this is the ONLY place that ever runs on `text`-registry (pointer)
    content (pipeline/join.py:437/485, the RBY dialogue join), and there is
    no override escape hatch on that path: pipeline/align.py's
    apply_corpus_overrides rewrites Alignment.translation, but join.py still
    runs the result through this function afterwards.

    `strings`-registry (engine string) content is different:
    pipeline/engine.py:match_engine_catalog also calls this for its
    automated matches, but an `rby/engine.json` or
    `rby/yellow_engine.json` entry is used VERBATIM
    (pipeline/engine.py:~1221, `out[source] = value` with no
    corpus_to_engine call) and short-circuits before automated matching
    ever runs. That is how RBY's own preexisting unmapped tokens (<PK>,
    <MN>: 34 of RedBlue-union-Yellow's tokens have no entry here) ship
    today -- e.g. "WITHDRAW <PK><MN>" is a literal engine Strings() source
    key (overrides/fr/rby/engine.json), hand-translated to
    "RETIRER POKéMON" without ever going through this table.

    Consequence for Gold's tokens: a token used only in `strings`-registry
    content can ship via a hand-authored gold/engine.json entry
    without touching _CORPUS_EXPANSIONS.
    A token used in `text`-registry (pointer) content has no such bypass
    and must be mapped here (or in DYNAMIC_TOKEN_RE).

    `bare_dynamic_tokens`: RBY's own extracted text.lua names the RAM
    variable/decimal source in its token ("{RAM:wBattleMonNick}",
    "{NUM:wDayCareTotalCost, 2, ...}"), and the engine-side renderer
    understands those names -- so RBY callers (the default) keep
    `{text_ram X}`/`{text_decimal X}` as named `{RAM:X}`/`{NUM:X}`. Gold's
    RomExtractorGen2.lua:decodeGen2Text never names the buffer -- TX_RAM/
    TX_STRINGBUFFER and TX_DECIMAL always decode to bare "{STRBUF}"/"{NUM}"
    (the call site fills the value at runtime; the byte stream itself never
    carries which of the cart's several wStringBufferN/wNameBuffer RAM
    slots is meant), and TextBox.lua's RAM token handler only recognises
    the bare "wStringBuffer"/"wNameBuffer" spellings -- not a numbered
    "wStringBuffer2"/"wStringBuffer4" etc. Gold callers (gs_join.py,
    gs_index_join.py, gs_mod.py) must pass True so a corpus row that
    names its buffer (poke-corpus mirrors pret's own asm, which does name
    it) collapses to the bare form the engine actually renders, instead of
    shipping an unrecognised named token that silently prints nothing
    (confirmed against a real Gold build: the Pokegear/starter "receives"
    text -- gs.std_text.ReceivedItemText / gs.ElmsLab.ReceivedStarterText --
    rendered with the item/mon name missing, while the engine's own
    extracted English for the same pointer is the bare
    "{PLAYER} received\n{STRBUF}.").
    """
    if bare_dynamic_tokens:
        text = re.sub(r"\{text_ram\s+[^}]+\}", "{STRBUF}", text)
        text = re.sub(r"\{text_(?:decimal|bcd)\s+[^}]+\}", "{NUM}", text)
    else:
        text = re.sub(r"\{text_ram\s+([^}]+)\}", r"{RAM:\1}", text)
        text = re.sub(r"\{text_(?:decimal|bcd)\s+([^}]+)\}", r"{NUM:\1}", text)
    text = text.replace("{text_start}", "")
    # Longest tokens first prevents partial expansion.
    for token in sorted(_CORPUS_EXPANSIONS, key=len, reverse=True):
        text = text.replace(token, _CORPUS_EXPANSIONS[token])
    return text


def placeholders(text: str) -> set[str]:
    return set(tokens(text))


def _placeholder_family(token: str) -> str:
    """A dynamic token's identity for comparison, ignoring any ":arg" and
    treating STRBUF as the RAM family (see DYNAMIC_TOKEN_RE's comment:
    RBY's extracted text names its RAM variable, Gold's does not).
    """
    name = token[1:-1].split(":", 1)[0]
    return "{RAM}" if name == "STRBUF" else "{" + name + "}"


def check_placeholders(source: str, target: str) -> list[str]:
    # Official localizations may legitimately reflow pages and terminators.
    # Only runtime substitutions must retain their multiplicity.
    left_tokens = DYNAMIC_TOKEN_RE.findall(corpus_to_engine(source))
    right_tokens = DYNAMIC_TOKEN_RE.findall(corpus_to_engine(target))
    left = Counter(_placeholder_family(t) for t in left_tokens)
    right = Counter(_placeholder_family(t) for t in right_tokens)
    errors = []
    for token, count in sorted((left - right).items()):
        errors.append(f"missing placeholder {token} x{count}")
    for token, count in sorted((right - left).items()):
        errors.append(f"unexpected placeholder {token} x{count}")
    if errors:
        return errors
    # Same family multiset on both sides. When a family is named on BOTH
    # sides for every occurrence, also require the exact named tokens to
    # match: comparing by family alone would let a translation reference a
    # different buffer of the same family (e.g. {RAM:wPlayerName} swapped
    # in for {RAM:wBattleMonNick}) without ever being flagged. Restricted to
    # families where every token on both sides is named, on purpose: Gold's
    # ROM-side decoder never names a buffer (bare {NUM}/{STRBUF} -- see
    # GoldBareDynamicTokenTests), so a family with any bare token is left to
    # the family-only comparison above, matching this project's verified
    # real Gold pointers.
    for family in sorted(set(left) | set(right)):
        left_family = [t for t in left_tokens if _placeholder_family(t) == family]
        right_family = [t for t in right_tokens if _placeholder_family(t) == family]
        if not all(":" in t for t in left_family) or not all(":" in t for t in right_family):
            continue
        left_named = Counter(left_family)
        right_named = Counter(right_family)
        for token, count in sorted((left_named - right_named).items()):
            errors.append(f"missing placeholder {token} x{count}")
        for token, count in sorted((right_named - left_named).items()):
            errors.append(f"unexpected placeholder {token} x{count}")
    return errors


def encode(text: str, charmap: Mapping[str, int], token_map: Mapping[str, int] | None = None) -> bytes:
    token_map = token_map or {}
    out = bytearray()
    pos = 0
    for match in TOKEN_RE.finditer(text):
        for char in text[pos:match.start()]:
            if char not in charmap:
                raise ValueError(f"unsupported glyph {char!r}")
            out.append(int(charmap[char]))
        token = match.group(0)
        if token not in token_map:
            raise ValueError(f"unsupported token {token}")
        out.append(int(token_map[token]))
        pos = match.end()
    for char in text[pos:]:
        if char not in charmap:
            raise ValueError(f"unsupported glyph {char!r}")
        out.append(int(charmap[char]))
    return bytes(out)
