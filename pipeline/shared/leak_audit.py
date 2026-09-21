"""Catch poke-corpus markup that reaches a generated mod's catalogs verbatim.

A corpus row carries syntax the pipeline converts before shipping: pret
script commands ({text_dots 3}), control characters (<WBR>, <SHY>), the
[NULL] no-text marker, and \\xNN escapes for bytes with no charmap entry.
Anything of the kind left in a shipped value is printed as-is in-game, so
the generated catalogs are audited once more before packaging.  A marker
also present in the catalog key (the engine's own source text) is not a leak.
"""
from __future__ import annotations

import re
from pathlib import Path

_ENTRY = re.compile(r'^\s*\[("(?:\\.|[^"\\])*"|\d+)\]\s*=\s*("(?:\\.|[^"\\])*")', re.M)
_LUA_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}

LEAKED_MARKUP = {
    "hex escape": re.compile(r"\\x[0-9A-Fa-f]{2}"),
    "pret command": re.compile(r"\{(?:text|sound)_[a-z_]+[^}]*\}"),
    "control character": re.compile(r"<(?:SHY|WBR|BSP|NULL|LF|CR|MOBILE)>"),
    "no-text marker": re.compile(r"\[NULL\]"),
}


def _lua_unquote(token: str) -> str:
    if not token.startswith('"'):
        return token

    def replace(match: re.Match) -> str:
        escaped = match.group(1)
        if escaped.isdigit():
            return chr(int(escaped))
        return _LUA_ESCAPES.get(escaped, "\\" + escaped)

    return re.sub(r'\\(\d{1,3}|.)', replace, token[1:-1])


def leaked_markup(key: str, value: str) -> list[str]:
    """The kinds of corpus markup in ``value`` that ``key`` does not carry."""
    return [
        name for name, pattern in LEAKED_MARKUP.items()
        if set(pattern.findall(value)) - set(pattern.findall(key))
    ]


def audit_generated_catalogs(mod_dir: str | Path) -> list[str]:
    """One problem line per shipped catalog value that leaks corpus markup."""
    problems = []
    lang_dir = Path(mod_dir) / "lang"
    for path in sorted(lang_dir.glob("*.lua")) if lang_dir.is_dir() else []:
        body = path.read_text(encoding="utf-8")
        for raw_key, raw_value in _ENTRY.findall(body):
            key, value = _lua_unquote(raw_key), _lua_unquote(raw_value)
            for kind in leaked_markup(key, value):
                problems.append(f"{path.name} [{key!r}]: {kind} in {value!r}")
    return problems
