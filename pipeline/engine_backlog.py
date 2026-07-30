"""Private, read-only inventory of unresolved Gen1Recomp engine strings.

This module is intentionally separate from the translation matcher.  It is a
developer aid for deciding which empty/ambiguous engine keys are worth manual
review.  It never writes catalog, anchor, or override files; its only outputs
are ignored reports below ``.cache/audit/engine-backlog``.
"""
from __future__ import annotations

from collections import defaultdict
import ast
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .corpus import canonical_language, parse_redblue
from .engine import (
    _normal as _normal_form,
    _placeholder_tokens,
    _printf_marker_type,
    _structural_form,
    printf_directives,
    load_semantic_anchors,
    read_engine_catalog,
)
from .project import ROOT, project_config
from .tokens import corpus_to_engine


SCHEMA = "gen1recomp-translation-mods/engine-backlog"
VERSION = 1
_CALL_RE = re.compile(r"\bStrings(?:\.source)?\s*\(")
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


def _decode_lua_string(token: str) -> str | None:
    if len(token) < 2 or token[0] not in {"'", '"'} or token[-1] != token[0]:
        return None
    body = token[1:-1]
    values: list[str] = []
    escapes = {"n": "\n", "r": "\r", "t": "\t", "v": "\v", "f": "\f", "b": "\b", "a": "\a"}
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 >= len(body):
            values.append(char)
            index += 1
            continue
        index += 1
        escaped = body[index]
        if escaped in escapes:
            values.append(escapes[escaped])
        elif escaped in {"\\", "'", '"'}:
            values.append(escaped)
        elif escaped == "z":
            index += 1
            while index < len(body) and body[index].isspace():
                index += 1
            continue
        elif escaped.isdigit():
            match = re.match(r"[0-9]{1,3}", body[index:])
            assert match is not None
            values.append(chr(int(match.group(0), 10)))
            index += len(match.group(0)) - 1
        else:
            # Lua leaves unknown escapes useful for source-like strings; keep
            # the escaped character rather than dropping a literal callsite.
            values.append(escaped)
        index += 1
    return "".join(values)


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


def iter_literal_strings_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Collect every literal ``Strings(...)``/``Strings.source(...)`` use.

    Paths are relative to ``checkout`` and contexts are private source
    snippets.  Calls whose first argument is a variable/table are deliberately
    omitted: they cannot safely be tied to one engine source key.
    """
    root = Path(checkout)
    if not root.is_dir():
        raise FileNotFoundError(f"Gen1Recomp checkout missing: {root}")
    result: list[dict[str, Any]] = []
    paths = sorted(path for path in root.rglob("*.lua") if ".git" not in path.parts)
    for path in paths:
        raw = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_lua_comments(raw)
        lines = raw.splitlines()
        for match in _CALL_RE.finditer(cleaned):
            index = match.end()
            while index < len(cleaned) and cleaned[index].isspace():
                index += 1
            token = _read_lua_literal(raw, index)
            if token is None:
                continue
            quoted, end = token
            if quoted.startswith("["):
                opening = re.match(r"(\[=*)\[", quoted)
                closing = "]" + opening.group(1)[1:] + "]" if opening else None
                source = quoted[len(opening.group(0)):-len(closing)] if opening and quoted.endswith(closing) else None
                # Lua long strings discard one initial LF (or CRLF) after the
                # opener; mirror that semantics for catalog-key matching.
                if source is not None:
                    source = source[2:] if source.startswith("\r\n") else source[1:] if source.startswith(("\n", "\r")) else source
            else:
                source = _decode_lua_string(quoted)
            if source is None:
                continue
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


def _classify_path(path: str) -> tuple[str, str]:
    parts = {part.casefold() for part in Path(path).parts}
    lowered = path.casefold()
    if "link" in parts or "online" in lowered or "tournament" in lowered:
        return "link", "ineligible"
    if "import" in parts or "romimporter" in lowered:
        return "import", "ineligible"
    if "core" in parts:
        return "core", "ineligible"
    if any(part in parts for part in {"mod", "mods", "mobile", "desktop"}) or any(token in lowered for token in ("modmanager", "discord", "updater")):
        return "modern", "ineligible"
    if "ui" in parts:
        return "ui", "review"
    if any(part in parts for part in {"battle", "field", "overworld", "world", "inventory", "data", "text", "map", "maps", "game", "games", "script", "scripts", "pokemon"}):
        return "rby", "eligible"
    return "unknown", "review"


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
        try:
            key, value = ast.literal_eval(match.group(1)), ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            continue
        if value:
            result[str(key)] = str(value)
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
    catalog = read_engine_catalog(engine_catalog)
    coverage = _load_json(Path(coverage_path))
    if not coverage or not isinstance(coverage, dict) or not (
        isinstance(coverage.get("engine"), dict) or "unmatched" in coverage or "ambiguous" in coverage
    ):
        raise ValueError(f"engine coverage report has no engine section: {coverage_path}")
    engine_report = coverage.get("engine") if isinstance(coverage.get("engine"), dict) else coverage
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
    if snapshot_total != len(catalog):
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
    callsites = defaultdict(list)
    for item in iter_literal_strings_callsites(checkout):
        callsites[item["source"]].append({key: value for key, value in item.items() if key != "source"})
    candidates = _corpus_candidates(corpus_root, language, keys)
    anchors = load_semantic_anchors(root / "config" / "semantic_anchors.json")
    entries: list[dict[str, Any]] = []
    for key in keys:
        sites = sorted(callsites.get(key, []), key=lambda item: (item["path"], item["line"], item["kind"], item["context"]))
        categories = sorted({_classify_path(item["path"])[0] for item in sites})
        eligibility = "eligible" if categories and set(categories) == {"rby"} else "ineligible" if categories and not ({"rby", "ui", "unknown"} & set(categories)) else "review"
        if not sites:
            category = "unknown"
            eligibility = "review"
        elif len(categories) == 1:
            category = categories[0]
        else:
            category = "mixed"
            eligibility = "review"
        for candidate in candidates.get(key, []):
            candidate["eligible"] = bool(eligibility == "eligible" and candidate["method"] != "fuzzy" and candidate["placeholder_compatible"] is not False and candidate.get("translation"))
        provenance = (engine_report.get("provenance", {}) or {}).get(key, {}) if isinstance(engine_report, dict) else {}
        details = (engine_report.get("details", {}) or {}).get(key) if isinstance(engine_report, dict) else None
        fallback_reason = details or (provenance.get("method") if isinstance(provenance, dict) else None) or "english_fallback"
        status = "ambiguous" if key in ambiguous_map else "unmatched"
        signature = _placeholder_signature(key)
        entries.append({
            "key": key,
            "status": status,
            "category": category,
            "provenance": "gen1recomp_literal" if sites else "engine_catalog",
            "provenance_kind": "gen1recomp_literal" if sites else "engine_catalog",
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
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "language": language,
        "sources": {"checkout": str(checkout), "corpus": str(corpus_root), "coverage": str(coverage_path), "engine_catalog": str(engine_catalog)},
        "coverage_snapshot": {
            "total": snapshot_total,
            "translated": engine_report.get("translated") if isinstance(engine_report, dict) else None,
            "unmatched": len(unmatched_set),
            "ambiguous": len(ambiguous_map),
        },
        "limitations": [
            "Fuzzy qid suggestions are advisory and never marked eligible.",
            "Modern/link/import/core callsites are ineligible; UI and unknown callsites require review.",
            "The report reflects the selected cached coverage snapshot and does not mutate review data.",
        ],
        "stats": stats,
        "entries": entries,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    stats = report["stats"]
    lines = [f"# Engine backlog: `{report['language']}`", "", "Private developer report; no review data or catalogs were modified.", "", "## Statistics", "", "| catalog | unresolved | ambiguous | literal callsites | RBY eligible |", "| ---: | ---: | ---: | ---: | ---: |", f"| {stats['catalog_total']} | {stats['unmatched']} | {stats['ambiguous']} | {stats['callsites']} | {stats['rby_eligible']} |", "", "## Entries", "", "| key | status | category | RBY | callsites | fallback | qid candidates |", "| --- | --- | --- | --- | ---: | --- | --- |"]
    for item in report["entries"]:
        candidates = ", ".join(f"{row['qid']} ({row['method']},{row['confidence']})" for row in item["qid_candidates"][:3]) or "—"
        lines.append(f"| `{item['key'].replace('|', '\\|')}` | {item['status']} | {item['category']} | {item['rby_eligibility']} | {len(item['callsites'])} | {item['fallback_reason']} | {candidates} |")
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


engine_backlog = analyze_engine_backlog
