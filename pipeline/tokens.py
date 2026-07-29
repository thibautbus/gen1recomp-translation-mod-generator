from __future__ import annotations

import re
from collections import Counter
from typing import Mapping

TOKEN_RE = re.compile(r"(?:<[^>]+>|\{[^}]+\}|\\(?:[nrt]|x[0-9A-Fa-f]{2}))")
DYNAMIC_TOKEN_RE = re.compile(r"\{(?:PLAYER|RIVAL|TARGET|USER|ID|RAM(?::[^}]+)?|NUM:[^}]+)\}")

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
}


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def convert_tokens(text: str, mapping: Mapping[str, str] | None = None) -> str:
    mapping = mapping or {}
    return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def corpus_to_engine(text: str) -> str:
    """Convert poke-corpus' pret syntax to Gen1Recomp's extracted text form."""
    text = re.sub(r"\{text_ram\s+([^}]+)\}", r"{RAM:\1}", text)
    text = re.sub(r"\{text_(?:decimal|bcd)\s+([^}]+)\}", r"{NUM:\1}", text)
    text = text.replace("{text_start}", "")
    # Longest tokens first prevents partial expansion.
    for token in sorted(_CORPUS_EXPANSIONS, key=len, reverse=True):
        text = text.replace(token, _CORPUS_EXPANSIONS[token])
    return text


def placeholders(text: str) -> set[str]:
    return set(tokens(text))


def check_placeholders(source: str, target: str) -> list[str]:
    # Official localizations may legitimately reflow pages and terminators.
    # Only runtime substitutions must retain their multiplicity.
    left = Counter(DYNAMIC_TOKEN_RE.findall(corpus_to_engine(source)))
    right = Counter(DYNAMIC_TOKEN_RE.findall(corpus_to_engine(target)))
    errors = []
    for token, count in sorted((left - right).items()):
        errors.append(f"missing placeholder {token} x{count}")
    for token, count in sorted((right - left).items()):
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
