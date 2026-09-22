"""Harvest the engine's Strings() and RomText callsites from its Lua source.

Every release reads the engine's keys from literal call sites: the pinned
manifest (pipeline/shared/engine_manifest.py), FireRed's scope and
hardcoded-text audit (pipeline/frlg/), and Red/Blue's backlog report
(pipeline/rby/engine_backlog.py).  Calls whose first argument is not a
literal are left to the tables and fallbacks each release reads itself.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .engine import _decode_lua_string


_CALL_RE = re.compile(r"\bStrings(?:\.source)?\s*\(")


# ``require("src.core.Strings")("...")`` is the same call without a local
# (FireRed's link/union_room.lua prints two messages that way).  Its module
# name is a string, which comment stripping blanks, so it is found in the raw
# text; see strings_calls().
_REQUIRE_CALL_RE = re.compile(r"\brequire\(\s*\"src\.core\.Strings\"\s*\)\s*\(")
_ROMTEXT_CALL_RE = re.compile(
    r"(?P<callee>\b(?:[Rr]omText|[A-Za-z_][A-Za-z0-9_.]*:romText))\s*\("
)


def _strip_lua_comments(text: str) -> str:
    """Mask Lua comments and literals, leaving executable code positions."""
    result: list[str] = []
    quote: str | None = None
    long_end: str | None = None
    escaped = False
    block = False
    index = 0
    while index < len(text):
        char = text[index]
        if block or long_end:
            end_marker = long_end
            if end_marker and text.startswith(end_marker, index):
                block = False
                long_end = None
                result.extend(end_marker)
                index += len(end_marker)
            elif char == "\n":
                result.append("\n")
                index += 1
            else:
                result.append(" ")
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            result.append(char if char == quote else "\n" if char == "\n" else " ")
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(char)
            index += 1
        elif text.startswith("--", index) and (delimiter := re.match(r"--(\[=*)\[", text[index:])):
            block = True
            opening = delimiter.group(1)
            long_end = "]" + opening[1:] + "]"
            result.extend(delimiter.group(0) if not block else " " * len(delimiter.group(0)))
            index += len(delimiter.group(0))
        elif text.startswith("--", index):
            end = text.find("\n", index)
            if end < 0:
                result.extend(" " * (len(text) - index))
                break
            result.extend(" " * (end - index))
            result.append("\n")
            index = end + 1
        elif char == "[" and (delimiter := re.match(r"(\[=*)\[", text[index:])):
            opening = delimiter.group(1)
            long_end = "]" + opening[1:] + "]"
            result.extend(delimiter.group(0))
            index += len(delimiter.group(0))
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _read_quoted(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] not in {"'", '"'}:
        return None
    quote = text[start]
    index = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return text[start:index + 1], index + 1
        index += 1
    return None


def _read_lua_literal(text: str, start: int) -> tuple[str, int] | None:
    quoted = _read_quoted(text, start)
    if quoted is not None:
        return quoted
    match = re.match(r"(\[=*)\[", text[start:])
    if not match:
        return None
    opening = match.group(1)
    closing = "]" + opening[1:] + "]"
    body_start = start + len(match.group(0))
    body_end = text.find(closing, body_start)
    if body_end < 0:
        return None
    return text[start:body_end + len(closing)], body_end + len(closing)


def _decode_lua_literal(token: str) -> str | None:
    if token.startswith("["):
        opening = re.match(r"(\[=*)\[", token)
        closing = "]" + opening.group(1)[1:] + "]" if opening else None
        value = token[len(opening.group(0)):-len(closing)] if opening and token.endswith(closing) else None
        if value is not None:
            value = value[2:] if value.startswith("\r\n") else value[1:] if value.startswith(("\n", "\r")) else value
        return value
    return _decode_lua_string(token)


def _read_concatenated_lua_literal(raw: str, cleaned: str, start: int) -> tuple[str, int] | None:
    """Read a first argument made only of Lua literals joined with ``..``."""
    token = _read_lua_literal(raw, start)
    if token is None:
        return None
    quoted, end = token
    value = _decode_lua_literal(quoted)
    if value is None:
        return None
    parts = [value]
    while True:
        index = end
        while index < len(cleaned) and cleaned[index].isspace():
            index += 1
        if cleaned[index:index + 2] != "..":
            break
        index += 2
        while index < len(cleaned) and cleaned[index].isspace():
            index += 1
        token = _read_lua_literal(raw, index)
        if token is None:
            return None
        quoted, end = token
        value = _decode_lua_literal(quoted)
        if value is None:
            return None
        parts.append(value)
    return "".join(parts), end


def _lua_call_arguments(raw: str, cleaned: str, start: int) -> tuple[list[tuple[int, int]], int] | None:
    """Return top-level argument spans for the call opened before ``start``.

    ``cleaned`` has comments and literal bodies masked by
    :func:`_strip_lua_comments`, while retaining their delimiters and byte
    positions.  That lets this small scanner balance nested calls/tables and
    split on commas without trying to implement Lua's grammar.
    """
    spans: list[tuple[int, int]] = []
    begin = start
    parens = brackets = braces = 0
    index = start
    while index < len(cleaned):
        char = cleaned[index]
        if char == "(":
            parens += 1
        elif char == ")":
            if parens == 0 and brackets == 0 and braces == 0:
                spans.append((begin, index))
                return spans, index + 1
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets:
            brackets -= 1
        elif char == "{":
            braces += 1
        elif char == "}" and braces:
            braces -= 1
        elif char == "," and parens == 0 and brackets == 0 and braces == 0:
            spans.append((begin, index))
            begin = index + 1
        index += 1
    return None


def _literal_argument(raw: str, cleaned: str, span: tuple[int, int]) -> str | None:
    start, end = span
    while start < end and cleaned[start].isspace():
        start += 1
    while end > start and cleaned[end - 1].isspace():
        end -= 1
    token = _read_concatenated_lua_literal(raw, cleaned, start)
    if token is None:
        return None
    value, consumed = token
    return value if not cleaned[consumed:end].strip() else None


def _argument_expression(raw: str, span: tuple[int, int]) -> str:
    start, end = span
    return " ".join(raw[start:end].strip().split())[:300]


def strings_calls(raw: str, cleaned: str) -> list[re.Match]:
    """Every ``Strings(``/``Strings.source(`` call opening in live code, and
    every ``require("src.core.Strings")(`` one, in source order."""
    calls = list(_CALL_RE.finditer(cleaned))
    # a require() call in a comment starts on a blanked character
    calls += [m for m in _REQUIRE_CALL_RE.finditer(raw) if cleaned[m.start()] == "r"]
    return sorted(calls, key=lambda m: m.start())


def iter_literal_strings_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Collect every literal ``Strings(...)``/``Strings.source(...)`` use.

    Paths are relative to ``checkout`` and contexts are private source
    snippets.  Calls whose first argument is a variable/table are deliberately
    omitted: they cannot safely be tied to one engine source key.
    """
    root = Path(checkout)
    if not root.is_dir():
        raise FileNotFoundError(f"Gen1Recomp checkout missing: {root}")
    # Production scope is src/ only.  Keep paths relative to the supplied
    # checkout for backwards-compatible backlog reports (src/foo.lua).
    scan_root = root / "src" if (root / "src").is_dir() else root
    result: list[dict[str, Any]] = []
    paths = sorted(path for path in scan_root.rglob("*.lua") if ".git" not in path.parts)
    for path in paths:
        raw = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_lua_comments(raw)
        lines = raw.splitlines()
        for match in strings_calls(raw, cleaned):
            index = match.end()
            while index < len(cleaned) and cleaned[index].isspace():
                index += 1
            token = _read_concatenated_lua_literal(raw, cleaned, index)
            if token is None:
                continue
            source, end = token
            line = cleaned.count("\n", 0, match.start()) + 1
            end_line = cleaned.count("\n", 0, end) + 1
            context = " ".join(item.strip() for item in lines[line - 1:end_line] if item.strip())
            rel = path.relative_to(root).as_posix()
            result.append({
                "path": rel,
                "line": line,
                "context": context[:300],
                "source": source,
                "kind": "source" if ".source" in match.group(0) else "call",
            })
    return sorted(result, key=lambda item: (item["source"], item["path"], item["line"], item["kind"], item["context"]))


def iter_romtext_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Inventory every production RomText call, including dynamic arguments.

    Gen1Recomp uses all three spellings ``romText(...)``, ``RomText(...)`` and
    ``state:romText(...)``.  Every literal fallback can reach
    ``Strings(fallback, ...)`` when imported ROM text is missing or has an
    incompatible slot shape, so the translation universe must not be defined
    by a hand-maintained allowlist.  Dynamic labels/fallbacks are retained as
    expressions in this audit inventory so a new upstream domain is visible
    instead of silently disappearing.
    """
    root = Path(checkout)
    if not root.is_dir():
        raise FileNotFoundError(f"Gen1Recomp checkout missing: {root}")
    scan_root = root / "src" if (root / "src").is_dir() else root
    result: list[dict[str, Any]] = []
    for path in sorted(p for p in scan_root.rglob("*.lua") if ".git" not in p.parts):
        raw = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_lua_comments(raw)
        lines = raw.splitlines()
        for match in _ROMTEXT_CALL_RE.finditer(cleaned):
            # ``function BattleState:romText(label, fallback, ...)`` declares
            # the helper; it is not a callsite and cannot render its formal
            # parameter names.
            prefix = cleaned[max(0, match.start() - 32):match.start()]
            if re.search(r"\bfunction\s+$", prefix):
                continue
            parsed = _lua_call_arguments(raw, cleaned, match.end())
            if parsed is None:
                continue
            arguments, end = parsed
            callee = match.group("callee")
            # Method calls receive self implicitly: arguments[0] is the label.
            if ":" in callee:
                label_index = 0
            # Function calls normally receive data first
            # (data, label, fallback, ...), and either form may carry
            # trailing varargs, so 3+ arguments is genuinely ambiguous
            # between "shorthand plus varargs" and "full form" -- guess from
            # whether the first argument is a literal, same as before.
            elif len(arguments) != 2:
                label_index = 0 if arguments and _literal_argument(raw, cleaned, arguments[0]) is not None else 1
            # Exactly two arguments is not ambiguous, though: the full form
            # needs at least three, so two arguments can only be the
            # (label, fallback) shorthand, regardless of whether label
            # happens to be a literal. The previous version kept using the
            # literal guess even here, so a dynamic label
            # (RomText(labels[i], "fallback")) read the real fallback as the
            # label, found no third argument, and silently dropped the row
            # -- exactly what this function's docstring says a RomText
            # callsite must never do.
            else:
                label_index = 0
            fallback_index = label_index + 1
            if fallback_index >= len(arguments):
                continue
            label = _literal_argument(raw, cleaned, arguments[label_index])
            fallback = _literal_argument(raw, cleaned, arguments[fallback_index])
            line = cleaned.count("\n", 0, match.start()) + 1
            end_line = cleaned.count("\n", 0, end) + 1
            context = " ".join(item.strip() for item in lines[line - 1:end_line] if item.strip())
            rel = path.relative_to(root).as_posix()
            row: dict[str, Any] = {
                "path": rel,
                "line": line,
                "context": context[:300],
                "callee": callee,
                "label": label,
                "label_expression": _argument_expression(raw, arguments[label_index]),
                "fallback_expression": _argument_expression(raw, arguments[fallback_index]),
                "kind": "romtext-fallback",
            }
            if fallback is not None:
                row["source"] = fallback
            result.append(row)
    return sorted(result, key=lambda item: (
        str(item.get("source", "")), item["path"], item["line"], item["callee"],
    ))


def iter_romtext_fallback_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Return all literal RomText fallbacks that can reach ``Strings``."""
    return [row for row in iter_romtext_callsites(checkout) if "source" in row]
