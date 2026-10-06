"""Generation-3 text model: pret charmap, runtime text IR and pret symbols.

gen1recomp's game3 runtime keeps every script message as an IR segment list
(``src/core/game3/scripting/text_ir.lua``), keyed by the message's ROM
pointer (``g3:081722c7``).  A translation overrides one key through
``mod.content.text:override(key, ir)``: Schemas.GEN3's ``text`` registry
accepts either a plain string (which ``G3.textIr`` only splits on ``\\n``) or
the IR list itself, which is the only form that can carry the player name,
``STR_VAR_n`` buffers or a scroll/paragraph break.

So a translation is produced the way the runtime produces the English one:
the PokeCorpus text is encoded to GBA bytes through pret's own
``charmap.txt``, then decoded by :func:`decode`, a line-for-line port of
``TextIR.decode``.  The English corpus row is decoded the same way and must
reproduce the extracted ROM IR exactly, which proves the encoder and the
port agree with the cart before any translation is trusted.

The runtime decodes each game family with its own dialect
(``TextIR.DIALECTS``): FireRed and LeafGreen use the default one, Emerald
(``rse``) names its placeholders and fonts on the segment and reads a few more
charmap bytes as tags, and Ruby and Sapphire (``rs``) do the same with their
own placeholder and font names.  :class:`Dialect` mirrors those tables, and a
:class:`PretCharmap` carries the dialect of the cart it was loaded for.

The one deliberate difference from the runtime decoder is the glyph table
used for translated text: ``TextIR.CHARMAP`` only lists the characters US
FireRed prints (``é``/``É`` are its only accented letters), while pret's
charmap and the US ROM font carry the whole European set (``à``, ``ç``,
``ß``, ``ñ``...).  Those bytes decode here to their real character, so the
override text is correct today and renders as soon as the runtime's glyph
lookup learns them (docs/upstream-fixes.md, FireRed section).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

# FRLG English charmap as the runtime decodes it (TextIR.CHARMAP), plus the
# two special cases TextIR.decode handles inline (0x53 0x54 -> "POKé").
RUNTIME_CHARMAP: dict[int, str] = {
    0x00: " ",
    0x1B: "é", 0x06: "É",
    0x2D: "&", 0x2E: "+", 0x35: "=",
    0x5B: "%", 0x5C: "(", 0x5D: ")",
    0x85: "<", 0x86: ">",
    0xA1: "0", 0xA2: "1", 0xA3: "2", 0xA4: "3", 0xA5: "4",
    0xA6: "5", 0xA7: "6", 0xA8: "7", 0xA9: "8", 0xAA: "9",
    0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xAF: "·",
    0xB0: "…", 0xB1: "“", 0xB2: "”", 0xB3: "‘", 0xB4: "'",
    0xB5: "♂", 0xB6: "♀", 0xB7: "¥", 0xB8: ",", 0xB9: "×",
    0xBA: "/",
    **{0xBB + i: chr(ord("A") + i) for i in range(26)},
    **{0xD5 + i: chr(ord("a") + i) for i in range(26)},
    0x36: ";", 0xEF: "▶",
    0xF0: ":",
}

# The 0xF9 escape's symbols and the 0xF8 escape's keypad icons, as the
# runtime names them (TextIR.EXTRA_SYMBOL, TextIR.KEYGFX; pret's
# charmap.txt:837-908).  A symbol written as a {NAME} tag has no glyph of
# its own and decodes to a tag segment; the others are ordinary text.
EXTRA_SYMBOL: dict[int, str] = {
    0x00: "↑", 0x01: "↓", 0x02: "←",
    0x03: "→", 0x04: "{PLUS}", 0x05: "{LV_2}",
    0x06: "{PP}", 0x07: "{ID}", 0x08: "№",
    0x09: "_", 0x0A: "①", 0x0B: "②",
    0x0C: "③", 0x0D: "④", 0x0E: "⑤",
    0x0F: "⑥", 0x10: "⑦", 0x11: "⑧",
    0x12: "⑨", 0x13: "{LEFT_PAREN}", 0x14: "{RIGHT_PAREN}",
    0x15: "◎", 0x16: "△", 0x17: "✕",
    0xD0: "{EMOJI_UNDERSCORE}", 0xD1: "{EMOJI_PIPE}", 0xD2: "{EMOJI_HIGHBAR}",
    0xD3: "{EMOJI_TILDE}", 0xD4: "{EMOJI_LEFT_PAREN}", 0xD5: "{EMOJI_RIGHT_PAREN}",
    0xD6: "{EMOJI_UNION}", 0xD7: "{EMOJI_GREATER_THAN}", 0xD8: "{EMOJI_LEFT_EYE}",
    0xD9: "{EMOJI_RIGHT_EYE}", 0xDA: "{EMOJI_AT}", 0xDB: "{EMOJI_SEMICOLON}",
    0xDC: "{EMOJI_PLUS}", 0xDD: "{EMOJI_MINUS}", 0xDE: "{EMOJI_EQUALS}",
    0xDF: "{EMOJI_SPIRAL}", 0xE0: "{EMOJI_TONGUE}", 0xE1: "{EMOJI_TRIANGLE_OUTLINE}",
    0xE2: "{EMOJI_ACUTE}", 0xE3: "{EMOJI_GRAVE}", 0xE4: "{EMOJI_CIRCLE}",
    0xE5: "{EMOJI_TRIANGLE}", 0xE6: "{EMOJI_SQUARE}", 0xE7: "{EMOJI_HEART}",
    0xE8: "{EMOJI_MOON}", 0xE9: "{EMOJI_NOTE}", 0xEA: "{EMOJI_BALL}",
    0xEB: "{EMOJI_BOLT}", 0xEC: "{EMOJI_LEAF}", 0xED: "{EMOJI_FIRE}",
    0xEE: "{EMOJI_WATER}", 0xEF: "{EMOJI_LEFT_FIST}", 0xF0: "{EMOJI_RIGHT_FIST}",
    0xF1: "{EMOJI_BIGWHEEL}", 0xF2: "{EMOJI_SMALLWHEEL}", 0xF3: "{EMOJI_SPHERE}",
    0xF4: "{EMOJI_IRRITATED}", 0xF5: "{EMOJI_MISCHIEVOUS}", 0xF6: "{EMOJI_HAPPY}",
    0xF7: "{EMOJI_ANGRY}", 0xF8: "{EMOJI_SURPRISED}", 0xF9: "{EMOJI_BIGSMILE}",
    0xFA: "{EMOJI_EVIL}", 0xFB: "{EMOJI_TIRED}", 0xFC: "{EMOJI_NEUTRAL}",
    0xFD: "{EMOJI_SHOCKED}", 0xFE: "{EMOJI_BIGANGER}",
}

KEYGFX: dict[int, str] = {
    0x00: "A_BUTTON", 0x01: "B_BUTTON", 0x02: "L_BUTTON",
    0x03: "R_BUTTON", 0x04: "START_BUTTON", 0x05: "SELECT_BUTTON",
    0x06: "DPAD_UP", 0x07: "DPAD_DOWN", 0x08: "DPAD_LEFT",
    0x09: "DPAD_RIGHT", 0x0A: "DPAD_UPDOWN", 0x0B: "DPAD_LEFTRIGHT",
    0x0C: "DPAD_ANY",
}

# The cart's braille cells (pokefirered/include/characters.h:285, as
# src/ui/game3/braille.lua lists them) and the dot each Unicode bit is: the
# cart numbers dot 1 = 0x01, 4 = 0x02, 2 = 0x04, 5 = 0x08, 3 = 0x10,
# 6 = 0x20, Unicode numbers them bit 0 to bit 5.  A braille line is drawn
# cell for cell, so the pipeline reads a corpus row of Unicode braille the
# way the runtime does (Braille.unicodeCell).
BRAILLE_CODE: Mapping[str, int] = {
    " ": 0x00,
    "A": 0x01, "B": 0x05, "C": 0x03, "D": 0x0B, "E": 0x09, "F": 0x07, "G": 0x0F,
    "H": 0x0D, "I": 0x06, "J": 0x0E, "K": 0x11, "L": 0x15, "M": 0x13, "N": 0x1B,
    "O": 0x19, "P": 0x17, "Q": 0x1F, "R": 0x1D, "S": 0x16, "T": 0x1E, "U": 0x31,
    "V": 0x35, "W": 0x2E, "X": 0x33, "Y": 0x3B, "Z": 0x39,
    ",": 0x04, ".": 0x2C, "?": 0x34, "!": 0x1C, ":": 0x0C, ";": 0x14,
    "-": 0x30, "/": 0x12, "(": 0x3C, "'": 0x10, "#": 0x3A, '"': 0x38,
}
_BRAILLE_CELL_BIT = (0x01, 0x04, 0x10, 0x02, 0x08, 0x20)
_BRAILLE_CHAR = {code: char for char, code in BRAILLE_CODE.items()}


def braille_cell(char: str) -> int | None:
    """The cart cell a Unicode braille character (U+2800-U+283F) draws."""
    if len(char) != 1 or not 0x2800 <= ord(char) <= 0x283F:
        return None
    dots, code = ord(char) - 0x2800, 0
    for bit in _BRAILLE_CELL_BIT:
        if dots % 2:
            code |= bit
        dots //= 2
    return code


def is_braille(value: str) -> bool:
    """True when a row is written as Unicode braille cells (and line breaks)."""
    return bool(value) and all(char == "\n" or braille_cell(char) is not None for char in value)


def braille_text(value: str) -> str | None:
    """The Latin text a row of Unicode braille cells spells, or ``None`` when
    the row is not braille (every cart's braille lines are written as
    Unicode braille in PokeCorpus, the cart's own as Latin letters)."""
    if not value or not any(0x2800 <= ord(char) <= 0x283F for char in value):
        return None
    out = []
    for char in value:
        if char == "\n":
            out.append("\n")
            continue
        cell = braille_cell(char)
        letter = _BRAILLE_CHAR.get(cell) if cell is not None else None
        if letter is None:
            return None
        out.append(letter)
    return "".join(out)


# Byte pairs the runtime reads as one ligature tag (TextIR.LIGATURE), plus
# the PK+MN pair it joins into {PKMN}.
LIGATURE: dict[int, str] = {0x53: "{PK}", 0x54: "{MN}"}

# TextIR.decode's FC sub-command argument counts (src/text.c:948).
_EXT_ARGS: Mapping[int, int] = {
    0x01: 1, 0x02: 1, 0x03: 1, 0x04: 3, 0x05: 1, 0x06: 1, 0x08: 1,
    0x0B: 2, 0x0C: 1, 0x0D: 1, 0x0E: 1, 0x10: 2, 0x11: 1, 0x12: 1,
    0x13: 1, 0x14: 1,
}

# Bytes that are text-control prefixes in the GBA text engine, never glyphs,
# even where pret's charmap lists a Japanese character at the same value.
_CONTROL_BYTES = frozenset(range(0xF7, 0x100))

# PokeCorpus's FireRedLeafGreen escapes: \c is pret's \p, \r is pret's \l.
_CORPUS_ESCAPES = {"n": "\\n", "c": "\\p", "r": "\\l", "p": "\\p", "l": "\\l"}

# Quotation marks the US font has no glyph for.  The European carts print
# them with the same 0xB1/0xB2 bytes the US cart uses for “ ”, so they are
# folded onto those before encoding (the glyph shape stays the US one).
LANGUAGE_FOLDS: Mapping[str, Mapping[str, str]] = {
    # PokeCorpus spells the money sign as "$" in every language, while pret's
    # charmap reserves "$" for the string terminator; the cart prints 0xB7.
    "*": {"$": "¥"},
    "fr": {"«": "“", "»": "”"},
    "de": {"„": "“", "“": "”", "‚": "‘", "‘": "’"},
}

# Named glyph runs the runtime cannot draw (no single character maps to them,
# so FrlgFont has no way to reach the glyph): translations spell them out.
# English rows keep the raw bytes, since they must reproduce the ROM's IR.
TRANSLATION_TOKEN_TEXT: Mapping[str, str] = {
    "SUPER_E": "e", "SUPER_ER": "er", "SUPER_RE": "re",
    "POKEBLOCK": "POKéBLOCK",
    # The Italian and French carts' own glyph runs (pokeemerald's
    # multi-language charmap.txt: POKEMELLA, POKEMELLE, MELLA, and Pco, the
    # French Battle Points symbol, which the French rows also write as "Pco"),
    "POKEMELLA": "POKéMELLA", "POKEMELLE": "POKéMELLE", "MELLA": "MELLA", "Pco": "Pco",
    # the first two glyphs of the POKéBLOCK run (the German "[POKE]NAV")
    "POKE": "POKé",
}

# FC 0B (PLAY_BGM) and FC 10 (PLAY_SE) take a 16-bit song id.
_SONG_COMMANDS = frozenset({b"\xfc\x0b", b"\xfc\x10"})

_TOKEN_RE = re.compile(r"\[([^\]\[]*)\]|\\(.)|(.)", re.S)
_CHARMAP_CHAR_RE = re.compile(r"^'((?:\\.|[^'\\])+)'\s*=\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)")
_CHARMAP_NAME_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)")
_SYMBOL_RE = re.compile(r"^([0-9a-fA-F]{8})\s+\S+\s+[0-9a-fA-F]+\s+(\S+)\s*$")
TEXT_KEY_RE = re.compile(r"^g3:([0-9a-f]{8})$")


class EncodeError(ValueError):
    """A corpus row contains something the pret charmap cannot encode."""


@dataclass(frozen=True)
class Dialect:
    """One family's decoding tables (``TextIR.DIALECTS``).

    ``ph_names`` and ``font_ids`` are written on the ``ph`` and ``FC 06``
    segments of a named dialect; ``charmap_extra`` and ``charmap_runs`` are
    bytes the runtime reads as tags; ``token_aliases`` are corpus tokens the
    cart's charmap spells under another name for the same bytes, and
    ``extra_names`` the escapes the cart's charmap has no name for at all.
    """
    name: str
    ph_names: Mapping[int, str] = field(default_factory=dict)
    font_ids: Mapping[int, str] = field(default_factory=dict)
    charmap_extra: Mapping[int, str] = field(default_factory=dict)
    charmap_runs: Mapping[int, tuple[bytes, str]] = field(default_factory=dict)
    token_aliases: Mapping[str, str] = field(default_factory=dict)
    extra_names: Mapping[str, bytes] = field(default_factory=dict)

    @property
    def named(self) -> bool:
        return self.name != FRLG_DIALECT_NAME


FRLG_DIALECT_NAME = "frlg"
FRLG_DIALECT = Dialect(FRLG_DIALECT_NAME)

# src/core/game3/scripting/text_ir.lua, TextIR.DIALECTS.rse.
RSE_DIALECT = Dialect(
    "rse",
    # pokeemerald/include/constants/characters.h:251
    ph_names={
        0x00: "UNKNOWN", 0x01: "PLAYER", 0x02: "STR_VAR_1", 0x03: "STR_VAR_2",
        0x04: "STR_VAR_3", 0x05: "KUN", 0x06: "RIVAL", 0x07: "VERSION",
        0x08: "AQUA", 0x09: "MAGMA", 0x0A: "ARCHIE", 0x0B: "MAXIE",
        0x0C: "KYOGRE", 0x0D: "GROUDON",
    },
    # pokeemerald/include/text.h:12
    font_ids={
        0: "FONT_SMALL", 1: "FONT_NORMAL", 2: "FONT_SHORT", 3: "FONT_SHORT_COPY_1",
        4: "FONT_SHORT_COPY_2", 5: "FONT_SHORT_COPY_3", 6: "FONT_BRAILLE",
        7: "FONT_NARROW", 8: "FONT_SMALL_NARROW", 9: "FONT_BOLD",
    },
    # pokeemerald/charmap.txt:45
    charmap_extra={
        0x34: "{LV}",
        0x55: "{POKEBLOCK}", 0x56: "", 0x57: "", 0x58: "", 0x59: "",
        0x77: "{UNK_SPACER}",
        0x79: "{UP_ARROW}", 0x7A: "{DOWN_ARROW}", 0x7B: "{LEFT_ARROW}", 0x7C: "{RIGHT_ARROW}",
    },
    # pokeemerald/charmap.txt:52
    charmap_runs={0x55: (bytes((0x55, 0x56, 0x57, 0x58, 0x59)), "{POKEBLOCK}")},
    # PokeCorpus keeps Ruby/Sapphire's version-dependent names, which
    # Emerald's charmap spells after the Sapphire strings it uses
    # (pokeemerald/charmap.txt:339): the same FD bytes.
    token_aliases={
        "EVIL_TEAM": "AQUA", "GOOD_TEAM": "MAGMA", "EVIL_LEADER": "ARCHIE",
        "GOOD_LEADER": "MAXIE", "EVIL_LEGENDARY": "KYOGRE", "GOOD_LEGENDARY": "GROUDON",
    },
)
# pokeruby/include/battle_message.h:21, the battle string placeholders of
# Ruby and Sapphire.  pokeruby writes them as bare FD escapes ({STRING 3}),
# so its charmap has no name for them; PokeCorpus names them as the later
# carts do, and TextIR.DIALECTS.rs reads them back under the same names.
_RS_BATTLE_PLACEHOLDERS = (
    "B_BUFF1", "B_BUFF2", "B_PLAYER_MON1_NAME", "B_OPPONENT_MON1_NAME", "B_PLAYER_MON2_NAME",
    "B_OPPONENT_MON2_NAME", "B_LINK_PLAYER_MON1_NAME", "B_LINK_OPPONENT_MON1_NAME",
    "B_LINK_PLAYER_MON2_NAME", "B_LINK_OPPONENT_MON2_NAME", "B_ATK_NAME_WITH_PREFIX_MON1",
    "B_ATK_PARTNER_NAME", "B_ATK_NAME_WITH_PREFIX", "B_DEF_NAME_WITH_PREFIX", "B_EFF_NAME_WITH_PREFIX",
    "B_ACTIVE_NAME_WITH_PREFIX", "B_SCR_ACTIVE_NAME_WITH_PREFIX", "B_CURRENT_MOVE", "B_LAST_MOVE",
    "B_LAST_ITEM", "B_LAST_ABILITY", "B_ATK_ABILITY", "B_DEF_ABILITY", "B_SCR_ACTIVE_ABILITY",
    "B_EFF_ABILITY", "B_TRAINER1_CLASS", "B_TRAINER1_NAME", "B_LINK_PLAYER_NAME", "B_LINK_PARTNER_NAME",
    "B_LINK_OPPONENT1_NAME", "B_LINK_OPPONENT2_NAME", "B_LINK_SCR_TRAINER_NAME", "B_PLAYER_NAME",
    "B_TRAINER1_LOSE_TEXT", "B_22", "B_PC_CREATOR_NAME", "B_ATK_PREFIX1", "B_DEF_PREFIX1",
    "B_ATK_PREFIX2", "B_DEF_PREFIX2", "B_ATK_PREFIX3", "B_DEF_PREFIX3", "B_BUFF3",
)

# src/core/game3/scripting/text_ir.lua, TextIR.DIALECTS.rs.
RS_DIALECT = Dialect(
    "rs",
    # pokeruby/src/string_util.c:476
    ph_names={
        0x00: "UNKNOWN", 0x01: "PLAYER", 0x02: "STR_VAR_1", 0x03: "STR_VAR_2",
        0x04: "STR_VAR_3", 0x05: "KUN", 0x06: "RIVAL", 0x07: "VERSION",
        0x08: "EVIL_TEAM", 0x09: "GOOD_TEAM", 0x0A: "EVIL_LEADER", 0x0B: "GOOD_LEADER",
        0x0C: "EVIL_LEGENDARY", 0x0D: "GOOD_LEGENDARY",
    },
    # pokeruby/src/text.c:419
    font_ids={
        0: "FONT_RS_0", 1: "FONT_RS_1", 2: "FONT_RS_2", 3: "FONT_RS_3",
        4: "FONT_RS_4", 5: "FONT_RS_5", 6: "FONT_BRAILLE",
    },
    # the runtime reads Ruby and Sapphire's glyph runs as Emerald's
    charmap_extra=RSE_DIALECT.charmap_extra,
    charmap_runs=RSE_DIALECT.charmap_runs,
    extra_names={
        **{name: bytes((0xFD, code)) for code, name in enumerate(_RS_BATTLE_PLACEHOLDERS)},
        # The Japanese rows name some of the cart's escapes after the
        # FireRed/Emerald table (pokeemerald charmap.txt:353, B_COPY_VAR_1 = FD 02,
        # to :389, B_26 = FD 26): the same bytes, which Ruby and Sapphire's table calls
        # PLAYER_MON1_NAME, OPPONENT_MON1_NAME, PLAYER_MON2_NAME, DEF_PREFIX1
        # and ATK_PREFIX2 (the English rows' names for them).
        "B_COPY_VAR_1": b"\xfd\x02", "B_COPY_VAR_2": b"\xfd\x03", "B_COPY_VAR_3": b"\xfd\x04",
        "B_TRAINER1_WIN_TEXT": b"\xfd\x25", "B_26": b"\xfd\x26",
    },
)
DIALECTS: Mapping[str, Dialect] = {dialect.name: dialect for dialect in (FRLG_DIALECT, RSE_DIALECT, RS_DIALECT)}


@dataclass(frozen=True)
class PretCharmap:
    chars: Mapping[str, bytes]
    names: Mapping[str, bytes]
    glyphs: Mapping[int, str]
    japanese: frozenset[str] = frozenset()
    dialect: Dialect = FRLG_DIALECT

    @property
    def translation_glyphs(self) -> dict[int, str]:
        """RUNTIME_CHARMAP plus the Latin glyphs pret defines on top of it."""
        table = dict(RUNTIME_CHARMAP)
        for byte, char in self.glyphs.items():
            if (byte not in table and byte not in _CONTROL_BYTES and byte not in (0x53, 0x54)
                    and byte not in self.dialect.charmap_extra):
                table[byte] = char
        return table


def load_charmap(path: str | Path, dialect: Dialect | str = FRLG_DIALECT) -> PretCharmap:
    """Parse pret's ``charmap.txt`` (the pinned pokefirered, pokeemerald or pokeruby
    revision), for the cart whose ``dialect`` the runtime decodes.

    Only the first definition of a character is kept: the Latin block comes
    first in the file and the Japanese block reuses the same byte values.
    The byte -> glyph table likewise stops at the Japanese block, so a Latin
    byte decodes to its Latin character.
    """
    chars: dict[str, bytes] = {}
    names: dict[str, bytes] = {}
    glyphs: dict[int, str] = {}
    japanese: set[str] = set()
    latin = True
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("@", 1)[0].strip()
        if not line:
            continue
        match = _CHARMAP_CHAR_RE.match(line)
        if match:
            char = match.group(1)
            if char.startswith("\\"):
                char = {"\\'": "'", "\\n": "\\n", "\\p": "\\p", "\\l": "\\l"}.get(char, char)
            value = bytes.fromhex(match.group(2))
            if char in ("あ", "ア"):
                latin = False
            chars.setdefault(char, value)
            if latin and len(value) == 1 and len(char) == 1:
                glyphs.setdefault(value[0], char)
            elif not latin and len(char) == 1:
                # the cart's Japanese fonts, which FrlgFont draws from since
                # gen1recomp v0.3.4 (FrlgFont.JAPANESE_GLYPHS)
                japanese.add(char)
            continue
        match = _CHARMAP_NAME_RE.match(line)
        if match:
            names.setdefault(match.group(1), bytes.fromhex(match.group(2)))
    for required in ("\\n", "\\p", "\\l", "PLAYER", "COLOR"):
        if required not in chars and required not in names:
            raise ValueError(f"pret charmap is missing {required!r}: {path}")
    if isinstance(dialect, str):
        dialect = DIALECTS[dialect]
    for name, value in dialect.extra_names.items():
        names.setdefault(name, value)
    return PretCharmap(chars, names, glyphs, frozenset(japanese), dialect)


def load_symbols(path: str | Path) -> dict[int, list[str]]:
    """Map every ROM address in pret's ``pokefirered.sym`` to its labels."""
    table: dict[int, list[str]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _SYMBOL_RE.match(line)
        if match:
            table.setdefault(int(match.group(1), 16), []).append(match.group(2))
    if not table:
        raise ValueError(f"no symbols found in {path}")
    return table


def text_key_address(key: str) -> int | None:
    match = TEXT_KEY_RE.match(key)
    return int(match.group(1), 16) if match else None


def encode(text: str, charmap: PretCharmap, *, language: str = "en") -> bytes:
    """Encode one PokeCorpus row to GBA bytes (without the 0xFF terminator)."""
    folds = {**LANGUAGE_FOLDS["*"], **LANGUAGE_FOLDS.get(language, {})}
    out = bytearray()
    for match in _TOKEN_RE.finditer(text):
        token, escape, char = match.groups()
        if token is not None:
            if (language != "en" and token in TRANSLATION_TOKEN_TEXT
                    and not _dialect_tag(token, charmap)):
                out += encode(TRANSLATION_TOKEN_TEXT[token], charmap, language=language)
                continue
            value = _encode_token(token, charmap)
            if (language != "en" and value[0] < 0xF7 and value != b"\x53\x54"
                    and not _dialect_tag(token, charmap)):
                glyphs = charmap.translation_glyphs
                if any(byte not in glyphs for byte in value):
                    raise EncodeError(f"[{token}] has no glyph the cart's font can draw")
            out += value
        elif escape is not None:
            key = _CORPUS_ESCAPES.get(escape)
            if key is None:
                raise EncodeError(f"unsupported corpus escape \\{escape}")
            out += charmap.chars[key]
        else:
            char = folds.get(char, char)
            value = charmap.chars.get(char)
            if value is None or len(value) != 1 or value[0] in _CONTROL_BYTES:
                raise EncodeError(f"character {char!r} has no glyph in the cart's font")
            out += value
    return bytes(out)


# Names PokeCorpus spells its own way: the cart's parentheses are the F9 13
# and F9 14 escapes, which pret's charmap calls LEFT_PAREN and RIGHT_PAREN
# (the help system's EXP and Level entries are the only rows that use them).
# PokeCorpus also writes NAME_END (FC 00), the mark Ruby and Sapphire's map
# names carry where their short form ends ("LITTLEROOT{NAME_END} TOWN"), as
# NOP.
_CORPUS_TOKEN_ALIASES = {"ROUND_LEFT_PAREN": "LEFT_PAREN", "ROUND_RIGHT_PAREN": "RIGHT_PAREN",
                         "NOP": "NAME_END"}


def _dialect_tag(token: str, charmap: PretCharmap) -> bool:
    """A token whose bytes the cart's dialect reads as a tag the runtime draws
    itself (Emerald's {LV}, {POKEBLOCK} and arrows), kept as in English."""
    value = charmap.names.get(token)
    extra = charmap.dialect.charmap_extra
    return bool(value) and value[0] in extra and all(byte in extra for byte in value)


def _encode_token(token: str, charmap: PretCharmap) -> bytes:
    parts = token.split()
    if not parts:
        raise EncodeError("empty [] token")
    name, args = parts[0], parts[1:]
    name = _CORPUS_TOKEN_ALIASES.get(name, name)
    name = charmap.dialect.token_aliases.get(name, name)
    prefix = charmap.names.get(name)
    if prefix is None:
        raise EncodeError(f"unknown charmap token [{token}]")
    out = bytearray(prefix)
    if prefix in _SONG_COMMANDS and len(args) == 1 and args[0] not in charmap.names:
        # A song constant the charmap does not list: TextIR.decode keeps only
        # the command byte of an FC 0B/FC 10 escape, so its two argument
        # bytes never reach the IR.
        return bytes(out) + b"\x00\x00"
    for arg in args:
        if arg in charmap.names:
            out += charmap.names[arg]
        elif re.fullmatch(r"\d+", arg) and int(arg) < 256:
            out.append(int(arg))
        elif re.fullmatch(r"0x[0-9A-Fa-f]{1,2}", arg):
            out.append(int(arg, 16))
        else:
            raise EncodeError(f"unsupported token argument in [{token}]")
    return bytes(out)


def decode(data: bytes, glyphs: Mapping[int, str] = RUNTIME_CHARMAP,
           *, battle: bool = False, dialect: Dialect = FRLG_DIALECT) -> list[dict]:
    """Port of ``TextIR.decode`` (src/core/game3/scripting/text_ir.lua).

    An ``ext`` segment carries its command and its argument bytes, as the
    extractor stores them and as the runtime reads them back: the Easy Chat
    keyboard and the Battle Records screen take the column of an ``FC 13``
    from ``seg.args[1]`` (src/ui/game3/easy_chat.lua:57,
    src/ui/game3/trainer_tower_records.lua:390).  In
    ``battle`` mode every ``FD xx`` escape is a battle placeholder (``bph``),
    as the battle string table is decoded (``opts.battle``).  A named
    ``dialect`` (Emerald's) writes each placeholder's and font's name on its
    segment and reads its extra charmap bytes as tags.
    """
    out: list[dict] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append({"t": "text", "s": "".join(buf)})
            buf.clear()

    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == 0xFF:
            flush()
            out.append({"t": "eos"})
            return out
        if c in (0xFE, 0xFA, 0xFB):
            flush()
            out.append({"t": {0xFE: "nl", 0xFA: "scroll", 0xFB: "para"}[c]})
            i += 1
        elif c == 0xFD:
            flush()
            nn = data[i + 1] if i + 1 < n else 0
            if battle:
                out.append({"t": "bph", "code": nn})
            elif nn == 0x01:
                out.append({"t": "player"})
            elif nn == 0x06:
                out.append({"t": "rival"})
            elif 0x02 <= nn <= 0x04:
                out.append({"t": "strvar", "n": nn - 1})
            elif dialect.named and nn in dialect.ph_names:
                out.append({"t": "ph", "code": nn, "name": dialect.ph_names[nn]})
            else:
                out.append({"t": "ph", "code": nn})
            i += 2
        elif c == 0xF7:
            flush()
            out.append({"t": "dynamic", "n": data[i + 1] if i + 1 < n else 0})
            i += 2
        elif c == 0xF8:
            flush()
            name = KEYGFX.get(data[i + 1] if i + 1 < n else -1)
            out.append({"t": "tag", "tag": f"{{{name}}}" if name else ""})
            i += 2
        elif c == 0xF9:
            symbol = EXTRA_SYMBOL.get(data[i + 1] if i + 1 < n else -1)
            if symbol and symbol.startswith("{"):
                flush()
                out.append({"t": "tag", "tag": symbol})
            else:
                buf.append(symbol or "?")
            i += 2
        elif c == 0xFC:
            flush()
            cmd = data[i + 1] if i + 1 < n else 0
            count = _EXT_ARGS.get(cmd, 0)
            args = [data[i + 2 + k] if i + 2 + k < n else 0 for k in range(count)]
            out.append({"t": "ext", "cmd": cmd, "args": args})
            if dialect.named and cmd == 0x06 and args and args[0] in dialect.font_ids:
                out[-1]["font"] = dialect.font_ids[args[0]]
            i += 2 + count
        else:
            glyph = glyphs.get(c)
            if glyph is not None:
                buf.append(glyph)
                i += 1
            elif c in LIGATURE:
                flush()
                if c == 0x53 and i + 1 < n and data[i + 1] == 0x54:
                    out.append({"t": "tag", "tag": "{PKMN}"})
                    i += 2
                else:
                    out.append({"t": "tag", "tag": LIGATURE[c]})
                    i += 1
            elif c in dialect.charmap_extra:
                run = dialect.charmap_runs.get(c)
                length = len(run[0]) if run and data[i:i + len(run[0])] == run[0] else 0
                tag = run[1] if length else dialect.charmap_extra[c]
                if tag:
                    flush()
                    out.append({"t": "tag", "tag": tag})
                i += max(length, 1)
            else:
                buf.append("?")
                i += 1
    flush()
    return out


JAPANESE = "ja-Hrkt"

# The forms the Japanese sheets draw at the Latin block's codes, which pret's
# charmap has no row for because the cart writes them with the Latin bytes:
# the digits, the two letter runs and the punctuation a Japanese line uses
# full-width (src/ui/game3/frlg_font.lua, japanese_glyphs).
JAPANESE_FULLWIDTH: frozenset[str] = frozenset(
    [chr(0xFF10 + i) for i in range(10)]
    + [chr(0xFF21 + i) for i in range(26)]
    + [chr(0xFF41 + i) for i in range(26)]
    + list("　！？。ー・‥…『』「」円．／：")
)


def corpus_ir(text: str, charmap: PretCharmap, *, language: str = "en",
              battle: bool = False) -> list[dict]:
    """Encode a corpus row and decode it into the runtime IR (with ``eos``)."""
    if language == JAPANESE:
        return japanese_ir(text, charmap, battle=battle)
    glyphs = RUNTIME_CHARMAP if language == "en" else charmap.translation_glyphs
    return decode(encode(text, charmap, language=language) + b"\xff", glyphs, battle=battle,
                  dialect=charmap.dialect)


def japanese_ir(text: str, charmap: PretCharmap, *, battle: bool = False) -> list[dict]:
    """The runtime IR of a Japanese corpus row, kana kept as characters.

    The cart's Japanese block reuses the Latin block's byte values, so a
    kana cannot survive the encode/decode round trip the other languages
    use: "あ" is the byte 0x01, which decodes to "À".  FrlgFont draws a
    Japanese character by the character itself since gen1recomp v0.3.4
    (FrlgFont.JAPANESE_GLYPHS), so a text run is kept as it is written and
    only the tokens and escapes go through the charmap, one at a time, to
    reach the very segments the runtime builds for them.
    """
    out: list[dict] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            out.append({"t": "text", "s": "".join(run)})
            run.clear()

    for match in _TOKEN_RE.finditer(text):
        token, escape, char = match.groups()
        if char is not None:
            run.append(char)
            continue
        source = f"[{token}]" if token is not None else "\\" + escape
        for segment in decode(encode(source, charmap, language=JAPANESE), RUNTIME_CHARMAP, battle=battle,
                              dialect=charmap.dialect):
            # a token that draws a character of its own (① in the help text,
            # № before a Pokédex number) belongs to the text run
            if segment["t"] == "text":
                run.append(segment["s"])
            else:
                flush()
                out.append(segment)
    flush()
    out.append({"t": "eos"})
    return out


def normalise_ir(segments: Iterable[Mapping]) -> list[dict]:
    """Canonical form of an extracted IR list, for equality checks."""
    result = []
    for segment in segments:
        row = {key: segment[key] for key in ("t", "s", "n", "code", "name", "cmd", "font", "tag", "args")
               if key in segment}
        result.append(row)
    return result


def ir_plain(segments: Iterable[Mapping]) -> str:
    """Human-readable rendering used in reports and worksheets."""
    parts = []
    for segment in segments:
        kind = segment.get("t")
        if kind == "text":
            parts.append(str(segment.get("s", "")))
        elif kind == "nl":
            parts.append("\n")
        elif kind == "para":
            parts.append("\\p")
        elif kind == "scroll":
            parts.append("\\l")
        elif kind == "player":
            parts.append("{PLAYER}")
        elif kind == "rival":
            parts.append("{RIVAL}")
        elif kind == "strvar":
            parts.append(f"{{STR_VAR_{segment.get('n')}}}")
        elif kind == "ph":
            parts.append(f"{{PH_{segment.get('code')}}}")
        elif kind == "ext":
            parts.append(f"{{EXT_{segment.get('cmd')}}}")
    return "".join(parts)


def dynamic_signature(segments: Iterable[Mapping]) -> list[tuple]:
    """Runtime-substituted segments, in order, for placeholder checks."""
    return sorted(
        (segment["t"], segment.get("n"), segment.get("code"))
        for segment in segments
        if segment.get("t") in {"player", "rival", "strvar", "ph", "bph"}
    )
