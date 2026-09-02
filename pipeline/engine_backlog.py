"""Private, read-only inventory of unresolved Gen1Recomp engine strings.

This module is intentionally separate from the translation matcher.  It is a
developer aid for deciding which empty/ambiguous engine keys are worth manual
review.  It never writes catalog, anchor, or override files; its only outputs
are ignored reports below ``.cache/audit/engine-backlog``.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from string import Formatter
from typing import Any, Iterable, Mapping

from .corpus import canonical_language, parse_redblue
from .engine import (
    _decode_lua_string,
    _normal as _normal_form,
    _placeholder_tokens,
    _printf_marker_type,
    _structural_form,
    printf_directives,
    load_semantic_anchors,
    load_semantic_anchor_decisions,
    merge_semantic_anchors,
    read_engine_catalog,
)
from .project import ROOT, project_config
from .tokens import corpus_to_engine
from .engine_scope import classify_catalog, engine_dynamic_values, forced_dynamic_keys, load_scope, coverage_metadata
from .join import ENGINE_CATALOG_EXTRA_KEYS


SCHEMA = "gen1recomp-translation-mods/engine-backlog"
VERSION = 1
MATRIX_SCHEMA = "gen1recomp-translation-mods/engine-backlog-matrix"
MATRIX_VERSION = 1
MATRIX_LANGUAGES = ("fr", "de", "es", "it", "ja-Hrkt")
_CALL_RE = re.compile(r"\bStrings(?:\.source)?\s*\(")
_ROMTEXT_CALL_RE = re.compile(
    r"(?P<callee>\b(?:[Rr]omText|[A-Za-z_][A-Za-z0-9_.]*:romText))\s*\("
)
_LANGUAGE_CODES = {"fr", "de", "es", "it", "ja-Hrkt"}


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
        for match in _CALL_RE.finditer(cleaned):
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
            # Method calls receive self implicitly.  Function calls normally
            # receive data first, but the two-argument shorthand is useful in
            # fixtures and remains compatible with RomText's contract.
            label_index = 0 if ":" in callee else (
                0 if arguments and _literal_argument(raw, cleaned, arguments[0]) is not None
                else 1
            )
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


def iter_dynamic_strings_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Inventory ``Strings`` calls whose first argument is not a literal.

    These expressions cannot safely be added to the key universe.  The report
    is deliberately source-oriented: finite domains belong in the pinned
    manifest, while open-ended/runtime expressions remain explicit audit
    findings.
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
        for match in _CALL_RE.finditer(cleaned):
            parsed = _lua_call_arguments(raw, cleaned, match.end())
            if parsed is None or not parsed[0]:
                continue
            arguments, end = parsed
            if _literal_argument(raw, cleaned, arguments[0]) is not None:
                continue
            line = cleaned.count("\n", 0, match.start()) + 1
            end_line = cleaned.count("\n", 0, end) + 1
            result.append({
                "path": path.relative_to(root).as_posix(),
                "line": line,
                "kind": "dynamic-source" if ".source" in match.group(0) else "dynamic-call",
                "expression": _argument_expression(raw, arguments[0]),
                "context": " ".join(
                    item.strip() for item in lines[line - 1:end_line] if item.strip()
                )[:300],
            })
    return sorted(result, key=lambda item: (
        item["path"], item["line"], item["kind"], item["expression"],
    ))


def _annotate_dynamic_manifest(
    callsites: Iterable[Mapping[str, Any]], manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Mark dynamic callsites covered by a revision-pinned finite domain."""
    locations: list[tuple[str, int, int, str]] = []
    for key, entry in manifest.get("engine_dynamic_values", {}).items():
        for path, first, last in re.findall(
            r"(src/[^ :()]+\.lua):(\d+)(?:-(\d+))?", str(entry.get("callsite", "")),
        ):
            locations.append((path, int(first), int(last or first), str(key)))
    result: list[dict[str, Any]] = []
    for item in callsites:
        row = dict(item)
        row["manifested_keys"] = sorted({
            key for path, first, last, key in locations
            if path == row.get("path") and first <= int(row.get("line", 0)) <= last
        })
        row["manifested"] = bool(row["manifested_keys"])
        result.append(row)
    return result


def _classify_path(path: str) -> tuple[str, str]:
    from .engine_scope import classify_path
    category = classify_path(path)
    return category, "eligible" if category == "rby" else "ineligible" if category in {"link", "import", "core", "modern"} else "review"


def _placeholder_signature(text: str) -> dict[str, Any]:
    tokens = _placeholder_tokens(text)
    values: list[str] = []
    for kind, token in tokens:
        values.append(_printf_marker_type(token) if kind == "printf" else token)
    return {"tokens": values, "count": len(values), "directives": printf_directives(text)}


def _compatible(source: str, english: str, translation: str | None) -> bool | None:
    source_shape = _placeholder_signature(source)["tokens"]
    english_shape = _placeholder_signature(english)["tokens"]
    if source_shape != english_shape:
        return False
    if translation in (None, ""):
        return None
    # Corpus translations use dynamic tokens ({PLAYER}, {NUM:...}); compare
    # their typed/ordered shape rather than raw printf spelling.
    if _placeholder_signature(translation)["tokens"] != source_shape:
        return False
    return True


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"engine coverage report missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid engine backlog coverage JSON: {path}") from exc
    return data if isinstance(data, dict) else {}


def _find_file(root: Path, language: str, names: Iterable[Path]) -> Path | None:
    for name in names:
        path = name if name.is_absolute() else root / name
        if path.is_file():
            return path
    return None


def _decode_catalog_values(path: Path) -> dict[str, str]:
    """Read generated non-empty Lua values without weakening scaffold checks."""
    result: dict[str, str] = {}
    pattern = re.compile(r'^\s*\[("(?:\\.|[^"\\])*")]\s*=\s*("(?:\\.|[^"\\])*")')
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        key = _decode_lua_string(match.group(1))
        value = _decode_lua_string(match.group(2))
        if key is None or value is None:
            continue
        if value:
            result[key] = value
    return result


def _corpus_candidates(corpus_root: Path, language: str, keys: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    if not corpus_root.exists():
        return {key: [] for key in keys}
    try:
        records = parse_redblue(corpus_root, language)
    except (OSError, ValueError, FileNotFoundError):
        return {key: [] for key in keys}
    english: dict[str, list[Any]] = defaultdict(list)
    exact: dict[str, list[Any]] = defaultdict(list)
    shapes: dict[str, list[Any]] = defaultdict(list)
    targets: dict[str, dict[str, str | None]] = defaultdict(dict)
    for row in records:
        if row.language == "en" and row.text is not None:
            english[_normal_form(row.text)].append(row)
            exact[row.text].append(row)
            shapes[_structural_form(row.text)].append(row)
        elif row.language == language:
            targets[row.qid or ""][row.game] = row.text
    result: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        key_norm = _normal_form(key)
        key_shape = _structural_form(key)
        source_rows: list[tuple[str, list[Any]]] = []
        if key in exact:
            source_rows.append(("exact", exact[key]))
        elif key_norm in english:
            source_rows.append(("normalized", english[key_norm]))
        elif key_shape in shapes:
            source_rows.append(("structural", shapes[key_shape]))
        for method, rows in source_rows:
            for row in rows:
                translation = targets.get(row.qid or "", {}).get(row.game)
                compatible = _compatible(key, row.text, translation)
                confidence = {"exact": 1.0, "normalized": 0.95, "structural": 0.88}[method]
                found[(row.qid or "", row.game, method)] = {
                    "qid": row.qid,
                    "game": row.game,
                    "english": row.text,
                    "translation": translation,
                    "confidence": confidence,
                    "method": method,
                    "placeholder_compatible": compatible,
                    "eligible": False,
                }
        # Fuzzy suggestions are intentionally advisory only.
        if not found and key_norm:
            scored = []
            for norm, rows in english.items():
                if not norm or norm[0] != key_norm[0] or abs(len(norm) - len(key_norm)) > max(32, len(key_norm)):
                    continue
                score = SequenceMatcher(None, key_norm, norm).ratio()
                if score >= 0.72:
                    scored.extend((score, row) for row in rows)
            for score, row in sorted(scored, key=lambda item: (-item[0], item[1].qid or ""))[:3]:
                translation = targets.get(row.qid or "", {}).get(row.game)
                found[(row.qid or "", row.game, "fuzzy")] = {
                    "qid": row.qid,
                    "game": row.game,
                    "english": row.text,
                    "translation": translation,
                    "confidence": round(score, 3),
                    "method": "fuzzy",
                    "placeholder_compatible": _compatible(key, row.text, translation),
                    "eligible": False,
                }
        result[key] = sorted(found.values(), key=lambda item: (-item["confidence"], item["qid"] or "", item.get("game", ""), item["method"]))
    return result


def analyze_engine_backlog(
    language: str,
    *,
    root: str | Path = ROOT,
    checkout: str | Path | None = None,
    corpus_root: str | Path | None = None,
    coverage_path: str | Path | None = None,
    engine_catalog: str | Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic in-memory backlog report for one language."""
    root = Path(root)
    language = canonical_language(language)
    if language not in _LANGUAGE_CODES:
        raise ValueError(f"unsupported engine backlog language {language!r}; choose one of {', '.join(sorted(_LANGUAGE_CODES))}")
    config = project_config(root)
    if checkout is None:
        checkout = root / ".cache" / "dependencies" / "gen1recomp"
    checkout = Path(checkout)
    if corpus_root is None:
        corpus_root = config.get("corpus", {}).get("path")
        corpus_root = (root / corpus_root).resolve() if corpus_root and not Path(corpus_root).is_absolute() else corpus_root
        if not corpus_root or not Path(corpus_root).exists():
            corpus_root = root / ".cache" / "dependencies" / "poke-corpus"
    corpus_root = Path(corpus_root)
    if coverage_path is None:
        coverage_path = _find_file(root, language, (
            Path(f".cache/interactive/{language}/coverage.json"),
            Path(f".cache/reports/{language}/coverage.json"),
            Path(".cache/reports/coverage.json"),
        ))
    else:
        coverage_path = Path(coverage_path)
    if coverage_path is None:
        raise FileNotFoundError(f"engine coverage report not found for {language}; run the translation build first")
    if engine_catalog is None:
        engine_catalog = _find_file(root, language, (
            Path(f".cache/interactive/{language}/complete-modkit-worksheet/strings.lua"),
            Path(f".cache/build/{language}/mod-worksheet/strings.lua"),
            Path(".cache/build/mod-worksheet/strings.lua"),
        ))
    else:
        engine_catalog = Path(engine_catalog)
    if engine_catalog is None:
        raise FileNotFoundError(f"engine strings catalogue not found for {language}; run the build first")
    scope = load_scope()
    catalog = read_engine_catalog(engine_catalog)
    optional_keys = forced_dynamic_keys(scope) | engine_dynamic_values(scope) | ENGINE_CATALOG_EXTRA_KEYS
    coverage = _load_json(Path(coverage_path))
    if not coverage or not isinstance(coverage, dict) or not (
        isinstance(coverage.get("engine"), dict) or "unmatched" in coverage or "ambiguous" in coverage
    ):
        raise ValueError(f"engine coverage report has no engine section: {coverage_path}")
    engine_report = coverage.get("engine") if isinstance(coverage.get("engine"), dict) else coverage
    expected_revision = scope.get("gen1recomp_revision")
    snapshot_revision = engine_report.get("source_revision") if isinstance(engine_report, dict) else None
    if snapshot_revision != expected_revision:
        raise ValueError(
            "engine coverage snapshot source_revision does not match the pinned "
            f"Gen1Recomp revision: expected {expected_revision}, got {snapshot_revision}"
        )
    unmatched = engine_report.get("unmatched", []) if isinstance(engine_report, dict) else []
    ambiguous = engine_report.get("ambiguous", {}) if isinstance(engine_report, dict) else {}
    snapshot_details = engine_report.get("details", {}) if isinstance(engine_report, dict) else {}
    snapshot_provenance = engine_report.get("provenance", {}) if isinstance(engine_report, dict) else {}
    snapshot_keys = set()
    for value in (unmatched, ambiguous, snapshot_details, snapshot_provenance):
        if isinstance(value, dict):
            snapshot_keys.update(str(key) for key in value)
        elif isinstance(value, list):
            snapshot_keys.update(str(key) for key in value)
    snapshot_total = engine_report.get("total") if isinstance(engine_report, dict) else None
    if isinstance(snapshot_total, bool) or not isinstance(snapshot_total, int):
        raise ValueError("engine coverage snapshot total must be an integer")
    if snapshot_total == len(set(catalog) | optional_keys):
        for key in optional_keys:
            catalog.setdefault(key, "")
    elif snapshot_total != len(catalog):
        raise ValueError(f"engine coverage snapshot total {snapshot_total} does not match catalog total {len(catalog)}")
    if snapshot_keys != set(catalog):
        raise ValueError("engine coverage snapshot key universe does not match the selected catalog")
    if not unmatched and not ambiguous:
        generated = _find_file(root, language, (
            Path(f".cache/interactive/{language}/mod/lang/strings.lua"),
            Path(f".cache/build/{language}/mod/lang/strings.lua"),
            Path(".cache/build/mod/lang/strings.lua"),
        ))
        translated = _decode_catalog_values(generated) if generated else {}
        unmatched = [key for key in catalog if key not in translated]
    unmatched_set = {str(key) for key in (unmatched if isinstance(unmatched, list) else unmatched.keys() if isinstance(unmatched, dict) else [])}
    ambiguous_map = {str(key): value for key, value in ambiguous.items()} if isinstance(ambiguous, dict) else {}
    keys = sorted(set(catalog) & (unmatched_set | set(ambiguous_map)) or (unmatched_set | set(ambiguous_map)))
    romtext_inventory = iter_romtext_callsites(checkout)
    callsite_rows = iter_literal_strings_callsites(checkout) + [
        row for row in romtext_inventory if "source" in row
    ]
    dynamic_strings = _annotate_dynamic_manifest(
        iter_dynamic_strings_callsites(checkout), scope,
    )
    classified = classify_catalog(catalog, callsite_rows, scope)
    callsites = defaultdict(list)
    for key, info in classified.items():
        callsites[key].extend({k: v for k, v in row.items() if k not in {"source", "category"}} for row in info["callsites"])
    candidates = _corpus_candidates(corpus_root, language, keys)
    decisions_path = root / "config" / "rby" / "semantic_anchor_decisions.json"
    anchors, _ = merge_semantic_anchors(
        load_semantic_anchors(root / "config" / "rby" / "semantic_anchors.json"),
        load_semantic_anchor_decisions(decisions_path) if decisions_path.is_file() else {},
    )
    entries: list[dict[str, Any]] = []
    for key in keys:
        sites = sorted(callsites.get(key, []), key=lambda item: (item["path"], item["line"], item["kind"], item["context"]))
        scope_info = classified.get(key, {"category": "unknown", "eligibility": "review"})
        categories = scope_info.get("categories", [])
        dynamic_kind = scope_info.get("provenance") if scope_info.get("provenance") in {"forced_dynamic", "engine_dynamic"} else None
        category = scope_info.get("category", "unknown") if (sites or dynamic_kind) else "unknown"
        eligibility = scope_info.get("eligibility", "review") if (sites or dynamic_kind) else "review"
        for candidate in candidates.get(key, []):
            candidate["eligible"] = bool(eligibility == "eligible" and candidate["method"] != "fuzzy" and candidate["placeholder_compatible"] is not False and candidate.get("translation"))
        provenance = (engine_report.get("provenance", {}) or {}).get(key, {}) if isinstance(engine_report, dict) else {}
        if dynamic_kind:
            provenance = {
                **provenance,
                "provenance": dynamic_kind,
                "reason": scope_info.get("reason"),
                "callsite": scope_info.get("callsite"),
                "qid": scope_info.get("qid"),
            }
        details = (engine_report.get("details", {}) or {}).get(key) if isinstance(engine_report, dict) else None
        fallback_reason = details or (provenance.get("method") if isinstance(provenance, dict) else None) or "english_fallback"
        status = "ambiguous" if key in ambiguous_map else "unmatched"
        signature = _placeholder_signature(key)
        entries.append({
            "key": key,
            "status": status,
            "category": category,
            "classifier_version": coverage_metadata(load_scope())["classifier_version"],
            "provenance": dynamic_kind or ("gen1recomp_literal" if sites else "engine_catalog"),
            "provenance_kind": dynamic_kind or ("gen1recomp_literal" if sites else "engine_catalog"),
            "coverage_provenance": provenance,
            "rby_eligibility": eligibility,
            "rby_eligible": {"eligible": True, "ineligible": False}.get(eligibility),
            "callsites": sites,
            "placeholders": signature["tokens"],
            "placeholder_signature": signature,
            "matcher": {"status": status, "fallback_reason": fallback_reason},
            "fallback_reason": fallback_reason,
            "ambiguous_values": ambiguous_map.get(key, []),
            "semantic_anchor": anchors.get(key),
            "qid_candidates": candidates.get(key, []),
        })
    stats = {
        "catalog_total": len(catalog),
        "keys": len(entries),
        "unmatched": sum(item["status"] == "unmatched" for item in entries),
        "unresolved": sum(item["status"] == "unmatched" for item in entries),
        "ambiguous": sum(item["status"] == "ambiguous" for item in entries),
        "callsites": sum(len(item["callsites"]) for item in entries),
        "keys_with_callsites": sum(bool(item["callsites"]) for item in entries),
        "rby_eligible": sum(item["rby_eligible"] is True for item in entries),
        "romtext_callsites": len(romtext_inventory),
        "romtext_dynamic_labels": sum(item.get("label") is None for item in romtext_inventory),
        "romtext_dynamic_fallbacks": sum("source" not in item for item in romtext_inventory),
        "dynamic_strings_callsites": len(dynamic_strings),
        "unmanifested_dynamic_strings_callsites": sum(
            not item["manifested"] for item in dynamic_strings
        ),
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "language": language,
        "sources": {"checkout": str(checkout), "corpus": str(corpus_root), "coverage": str(coverage_path), "engine_catalog": str(engine_catalog)},
        "coverage_snapshot": {
            "source_revision": snapshot_revision,
            "total": snapshot_total,
            "translated": engine_report.get("translated") if isinstance(engine_report, dict) else None,
            "unmatched": len(unmatched_set),
            "ambiguous": len(ambiguous_map),
        },
        "source_inventory": {
            "romtext": romtext_inventory,
            "dynamic_strings": dynamic_strings,
        },
        "limitations": [
            "Fuzzy qid suggestions are advisory and never marked eligible.",
            "Modern/link/import/core callsites are ineligible; UI and unknown callsites require review.",
            "The report reflects the selected cached coverage snapshot and does not mutate review data.",
        ],
        "stats": stats,
        "classifier": coverage_metadata(load_scope()),
        "entries": entries,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    stats = report["stats"]
    lines = [f"# Engine backlog: `{report['language']}`", "", "Private developer report; no review data or catalogs were modified.", "", "## Statistics", "", "| catalog | unresolved | ambiguous | literal callsites | RBY eligible |", "| ---: | ---: | ---: | ---: | ---: |", f"| {stats['catalog_total']} | {stats['unmatched']} | {stats['ambiguous']} | {stats['callsites']} | {stats['rby_eligible']} |", "", "## Entries", "", "| key | status | category | RBY | callsites | fallback | qid candidates |", "| --- | --- | --- | --- | ---: | --- | --- |"]
    for item in report["entries"]:
        candidates = ", ".join(f"{row['qid']} ({row['method']},{row['confidence']})" for row in item["qid_candidates"][:3]) or "—"
        escaped_key = item["key"].replace("|", "\\|")
        lines.append(f"| `{escaped_key}` | {item['status']} | {item['category']} | {item['rby_eligibility']} | {len(item['callsites'])} | {item['fallback_reason']} | {candidates} |")
    return "\n".join(lines) + "\n"


def run_backlog(root: str | Path = ROOT, language: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Write the private JSON/Markdown backlog reports and return the report."""
    root = Path(root)
    if language is None:
        language = project_config(root).get("corpus", {}).get("target_lang", "fr")
    report = analyze_engine_backlog(language, root=root, **kwargs)
    output = root / ".cache" / "audit" / "engine-backlog"
    output.mkdir(parents=True, exist_ok=True)
    lang = report["language"]
    (output / f"{lang}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / f"{lang}.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _matrix_language_path(
    value: str | Path | Mapping[str, str | Path] | None,
    language: str,
    *,
    filename: str,
) -> Path | None:
    """Resolve an optional per-language snapshot/catalog path.

    Matrix callers may provide an explicit mapping, a ``{language}`` template,
    or a directory containing ``<language>/<filename>`` (and, for convenience,
    ``<language>.<suffix>``).  Returning ``None`` delegates to the analyzer's
    existing deterministic cache lookup rules.
    """
    selected: str | Path | None
    if isinstance(value, Mapping):
        selected = value.get(language)
    else:
        selected = value
    if selected is None:
        return None
    template = str(selected)
    if "{" in template or "}" in template:
        try:
            fields = list(Formatter().parse(template))
        except ValueError as exc:
            raise ValueError(f"invalid matrix path template {template!r}: {exc}") from exc
        invalid = [field for _, field, spec, conversion in fields
                   if field not in (None, "language") or spec or conversion]
        if invalid or any(field is None and (literal.count("{") or literal.count("}"))
                          for literal, field, _, _ in fields):
            raise ValueError("matrix path templates may use only the {language} placeholder")
        template = template.replace("{language}", language)
    path = Path(template)
    if path.is_dir():
        nested = path / language / filename
        if nested.is_file():
            return nested
        suffix = Path(filename).suffix
        sibling = path / f"{language}{suffix}"
        if sibling.is_file():
            return sibling
        # Let the analyzer produce its normal English missing-file error.
        return nested
    return path


def _matrix_languages(values: Iterable[str] | str | None) -> tuple[str, ...]:
    if values is None:
        return MATRIX_LANGUAGES
    if isinstance(values, str):
        values = values.split(",")
    result: list[str] = []
    for value in values:
        language = canonical_language(value)
        if language not in MATRIX_LANGUAGES:
            raise ValueError(
                f"unsupported engine backlog matrix language {language!r}; choose one of {', '.join(MATRIX_LANGUAGES)}"
            )
        if language in result:
            raise ValueError(f"duplicate engine backlog matrix language {language!r}")
        result.append(language)
    if not result:
        raise ValueError("engine backlog matrix requires at least one language")
    # Reports use the pinned canonical order, independent of caller input
    # ordering, so equivalent invocations produce byte-identical artifacts.
    selected = set(result)
    return tuple(language for language in MATRIX_LANGUAGES if language in selected)


def _canonical_matrix_mapping(
    value: Mapping[str, str | Path] | str | Path | None,
    *,
    selected_languages: tuple[str, ...],
    label: str,
) -> Mapping[str, str | Path] | str | Path | None:
    """Canonicalize and validate explicit per-language path mappings."""
    if not isinstance(value, Mapping):
        return value
    canonical: dict[str, str | Path] = {}
    for raw_language, path in value.items():
        language = canonical_language(raw_language, "")
        if language not in MATRIX_LANGUAGES:
            raise ValueError(f"unsupported {label} language {raw_language!r}")
        if language in canonical:
            raise ValueError(f"duplicate {label} language aliases for {language!r}")
        if path is None or str(path) == "":
            raise ValueError(f"missing {label} mapping for language: {language}")
        canonical[language] = path
    missing = [language for language in selected_languages if language not in canonical]
    if missing:
        raise ValueError(f"missing {label} mapping for language(s): {', '.join(missing)}")
    extra = [language for language in canonical if language not in selected_languages]
    if extra:
        raise ValueError(f"{label} mapping contains unselected language(s): {', '.join(extra)}")
    return canonical


def analyze_engine_backlog_matrix(
    *,
    root: str | Path = ROOT,
    languages: Iterable[str] | str | None = None,
    checkout: str | Path | None = None,
    corpus_root: str | Path | None = None,
    coverage_paths: Mapping[str, str | Path] | str | Path | None = None,
    engine_catalog_paths: Mapping[str, str | Path] | str | Path | None = None,
    coverage_dir: str | Path | None = None,
    engine_catalog_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze the canonical language backlogs and join them by engine key.

    Every language is validated by :func:`analyze_engine_backlog`; no config,
    catalog, or review file is written.  The returned structure is stable for
    reproducible private audit reports and intentionally retains only metadata
    plus unresolved/ambiguous key details from each per-language report.
    """
    root = Path(root)
    selected_languages = _matrix_languages(languages)
    coverage_paths = _canonical_matrix_mapping(
        coverage_paths, selected_languages=selected_languages, label="coverage"
    )
    engine_catalog_paths = _canonical_matrix_mapping(
        engine_catalog_paths, selected_languages=selected_languages, label="engine catalog"
    )
    reports: dict[str, dict[str, Any]] = {}
    for language in selected_languages:
        coverage_value = coverage_paths if coverage_paths is not None else coverage_dir
        catalog_value = engine_catalog_paths if engine_catalog_paths is not None else engine_catalog_dir
        coverage_path = _matrix_language_path(coverage_value, language, filename="coverage.json")
        catalog_path = _matrix_language_path(catalog_value, language, filename="strings.lua")
        reports[language] = analyze_engine_backlog(
            language,
            root=root,
            checkout=checkout,
            corpus_root=corpus_root,
            coverage_path=coverage_path,
            engine_catalog=catalog_path,
        )

    by_language = {
        language: {entry["key"]: entry for entry in reports[language]["entries"]}
        for language in selected_languages
    }
    keys = sorted({key for entries in by_language.values() for key in entries})
    matrix_entries: list[dict[str, Any]] = []
    for key in keys:
        language_rows: dict[str, dict[str, Any]] = {}
        present = 0
        eligible = 0
        for language in selected_languages:
            entry = by_language[language].get(key)
            if entry is None:
                # The selected catalog may contain a resolved key, or the key
                # can be absent from a language-specific snapshot.  The
                # analyzer's snapshot validation guarantees a known universe,
                # but deliberately does not expose resolved values.
                language_rows[language] = {
                    "status": "resolved_or_absent",
                    "candidates": [],
                    "placeholders": _placeholder_signature(key)["tokens"],
                    "placeholder_signature": _placeholder_signature(key),
                    "callsites": [],
                    "rby_eligibility": None,
                    "rby_eligible": None,
                }
                continue
            present += 1
            eligible += int(entry.get("rby_eligible") is True)
            language_rows[language] = {
                "status": entry["status"],
                "category": entry.get("category"),
                "candidates": entry.get("qid_candidates", []),
                "placeholders": entry.get("placeholders", []),
                "placeholder_signature": entry.get("placeholder_signature", {}),
                "callsites": entry.get("callsites", []),
                "rby_eligibility": entry.get("rby_eligibility"),
                "rby_eligible": entry.get("rby_eligible"),
                "fallback_reason": entry.get("fallback_reason"),
                "coverage_provenance": entry.get("coverage_provenance", {}),
                "semantic_anchor": entry.get("semantic_anchor"),
            }
        if eligible:
            triage = "common-rby" if present == len(selected_languages) and eligible == len(selected_languages) else "rby-review"
        elif present > 1:
            triage = "common-review"
        elif present == 1:
            triage = "language-specific"
        else:  # Defensive; keys is built from ``present`` rows.
            triage = "unseen"
        matrix_entries.append({
            "key": key,
            "languages": language_rows,
            "commonality": {
                "languages_present": present,
                "language_count": len(selected_languages),
                "all_languages": present == len(selected_languages),
                "fraction": round(present / len(selected_languages), 6),
            },
            "triage": triage,
            "triage_classification": triage,
            "placeholders": _placeholder_signature(key),
        })

    language_metadata = {
        language: {
            "sources": reports[language]["sources"],
            "coverage_snapshot": reports[language]["coverage_snapshot"],
            "classifier": reports[language]["classifier"],
            "stats": reports[language]["stats"],
        }
        for language in selected_languages
    }
    return {
        "schema": MATRIX_SCHEMA,
        "version": MATRIX_VERSION,
        "languages": list(selected_languages),
        "sources": {language: metadata["sources"] for language, metadata in language_metadata.items()},
        "coverage_snapshots": {language: metadata["coverage_snapshot"] for language, metadata in language_metadata.items()},
        "classifiers": {language: metadata["classifier"] for language, metadata in language_metadata.items()},
        "language_reports": language_metadata,
        "entries": matrix_entries,
        "stats": {
            "languages": len(selected_languages),
            "keys": len(matrix_entries),
            "common_keys": sum(item["commonality"]["all_languages"] for item in matrix_entries),
            "rby_keys": sum(item["triage"] in {"common-rby", "rby-review"} for item in matrix_entries),
            "triage": {
                value: sum(item["triage"] == value for item in matrix_entries)
                for value in ("common-rby", "rby-review", "common-review", "language-specific", "unseen")
            },
        },
        "limitations": [
            "Per-language rows are unresolved/ambiguous backlog entries; resolved keys are represented as resolved_or_absent.",
            "Fuzzy qid suggestions remain advisory and never count as translated.",
            "The matrix reflects the selected cached snapshots and does not mutate review data, catalogs, or configuration.",
        ],
    }


def _markdown_matrix(report: Mapping[str, Any]) -> str:
    stats = report["stats"]
    languages = report["languages"]
    lines = [
        "# Engine backlog matrix",
        "",
        "Private developer report; no review data, catalogs, or configuration were modified.",
        "",
        f"Languages: {', '.join(f'`{language}`' for language in languages)}",
        "",
        "## Statistics",
        "",
        "| keys | common keys | RBY triage |",
        "| ---: | ---: | ---: |",
        f"| {stats['keys']} | {stats['common_keys']} | {stats['rby_keys']} |",
        "",
        "## Entries",
        "",
        "| key | commonality | triage | per-language status |",
        "| --- | ---: | --- | --- |",
    ]
    for item in report["entries"]:
        key = str(item["key"]).replace("|", "\\|")
        statuses = ", ".join(f"{language}:{item['languages'][language]['status']}" for language in languages)
        lines.append(f"| `{key}` | {item['commonality']['languages_present']}/{item['commonality']['language_count']} | {item['triage']} | {statuses} |")
    return "\n".join(lines) + "\n"


def run_backlog_matrix(root: str | Path = ROOT, **kwargs: Any) -> dict[str, Any]:
    """Write deterministic private multilingual matrix JSON and Markdown."""
    root = Path(root)
    report = analyze_engine_backlog_matrix(root=root, **kwargs)
    output = root / ".cache" / "audit" / "engine-backlog"
    output.mkdir(parents=True, exist_ok=True)
    (output / "matrix.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "matrix.md").write_text(_markdown_matrix(report), encoding="utf-8")
    return report


engine_backlog = analyze_engine_backlog
engine_backlog_matrix = analyze_engine_backlog_matrix
