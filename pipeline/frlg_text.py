"""FireRed text model: pret charmap, runtime text IR and pret symbols.

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
from dataclasses import dataclass
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
    0xF0: ":",
}

# TextIR.decode's FC sub-command lengths (bytes including FC and the command).
_EXT_THREE = frozenset({0x01, 0x02, 0x03, 0x05, 0x06, 0x08, 0x0C, 0x0D, 0x0E,
                        0x0F, 0x11, 0x12, 0x13, 0x14})
_EXT_FOUR = frozenset({0x0B, 0x10})
_EXT_FIVE = frozenset({0x04})

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
class PretCharmap:
    chars: Mapping[str, bytes]
    names: Mapping[str, bytes]
    glyphs: Mapping[int, str]

    @property
    def translation_glyphs(self) -> dict[int, str]:
        """RUNTIME_CHARMAP plus the Latin glyphs pret defines on top of it."""
        table = dict(RUNTIME_CHARMAP)
        for byte, char in self.glyphs.items():
            if byte not in table and byte not in _CONTROL_BYTES and byte not in (0x53, 0x54):
                table[byte] = char
        return table


def load_charmap(path: str | Path) -> PretCharmap:
    """Parse pret's ``charmap.txt`` (the pinned pokefirered revision).

    Only the first definition of a character is kept: the Latin block comes
    first in the file and the Japanese block reuses the same byte values.
    The byte -> glyph table likewise stops at the Japanese block, so a Latin
    byte decodes to its Latin character.
    """
    chars: dict[str, bytes] = {}
    names: dict[str, bytes] = {}
    glyphs: dict[int, str] = {}
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
            continue
        match = _CHARMAP_NAME_RE.match(line)
        if match:
            names.setdefault(match.group(1), bytes.fromhex(match.group(2)))
    for required in ("\\n", "\\p", "\\l", "PLAYER", "COLOR"):
        if required not in chars and required not in names:
            raise ValueError(f"pret charmap is missing {required!r}: {path}")
    return PretCharmap(chars, names, glyphs)


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
            out += _encode_token(token, charmap)
        elif escape is not None:
            key = _CORPUS_ESCAPES.get(escape)
            if key is None:
                raise EncodeError(f"unsupported corpus escape \\{escape}")
            out += charmap.chars[key]
        else:
            char = folds.get(char, char)
            value = charmap.chars.get(char)
            if value is None or len(value) != 1 or value[0] in _CONTROL_BYTES:
                raise EncodeError(f"character {char!r} has no FireRed glyph")
            out += value
    return bytes(out)


def _encode_token(token: str, charmap: PretCharmap) -> bytes:
    parts = token.split()
    if not parts:
        raise EncodeError("empty [] token")
    name, args = parts[0], parts[1:]
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


def decode(data: bytes, glyphs: Mapping[int, str] = RUNTIME_CHARMAP) -> list[dict]:
    """Port of ``TextIR.decode`` (src/core/game3/scripting/text_ir.lua).

    ``ext`` segments carry ``cmd`` only, exactly as the extractor stores them
    (it hands TextIR.decode a byte table, so ``raw`` is never set).
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
            if nn == 0x01:
                out.append({"t": "player"})
            elif nn == 0x06:
                out.append({"t": "rival"})
            elif 0x02 <= nn <= 0x04:
                out.append({"t": "strvar", "n": nn - 1})
            else:
                out.append({"t": "ph", "code": nn})
            i += 2
        elif c == 0xFC:
            flush()
            cmd = data[i + 1] if i + 1 < n else 0
            skip = 2
            if cmd in _EXT_THREE:
                skip = 3
            elif cmd in _EXT_FOUR:
                skip = 4
            elif cmd in _EXT_FIVE:
                skip = 5
            out.append({"t": "ext", "cmd": cmd})
            i += skip
        elif c in (0xF8, 0xF9) and i + 1 < n:
            # Keypad icon / extra symbol escapes: the runtime has no case for
            # them, so it prints "?" and then the argument byte as a glyph of
            # its own table.  Mirror that exactly: the translation glyph table
            # would otherwise turn the argument into an accented letter.
            buf.append("?")
            buf.append(RUNTIME_CHARMAP.get(data[i + 1], "?"))
            i += 2
        else:
            glyph = glyphs.get(c)
            if glyph is not None:
                buf.append(glyph)
                i += 1
            elif c == 0x53:
                if i + 1 < n and data[i + 1] == 0x54:
                    buf.append("POKé")
                    i += 2
                else:
                    buf.append("PK")
                    i += 1
            else:
                buf.append("?")
                i += 1
    flush()
    return out


def corpus_ir(text: str, charmap: PretCharmap, *, language: str = "en") -> list[dict]:
    """Encode a corpus row and decode it into the runtime IR (with ``eos``)."""
    glyphs = RUNTIME_CHARMAP if language == "en" else charmap.translation_glyphs
    return decode(encode(text, charmap, language=language) + b"\xff", glyphs)


def normalise_ir(segments: Iterable[Mapping]) -> list[dict]:
    """Canonical form of an extracted IR list, for equality checks."""
    result = []
    for segment in segments:
        row = {key: segment[key] for key in ("t", "s", "n", "code", "cmd") if key in segment}
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
        if segment.get("t") in {"player", "rival", "strvar", "ph"}
    )
