"""Developer-only audit of localized pret/pokered disassemblies.

The audit is deliberately read-only with respect to versioned review data.  It
extracts text and callsites from a private checkout, compares labels with the
PokeCorpus qid suffixes, and writes reports below ``.cache/audit``.  It is not
part of the translation build and should not be used as a source of automatic
anchors or overrides.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .corpus import canonical_language, parse_redblue
from .project import ROOT, project_config
from .tokens import corpus_to_engine


_LABEL_RE = re.compile(r"^\s*([._A-Za-z][\w.$^'-]*)\s*:{1,2}\s*(?:;.*)?$")
_QUOTED_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_REFERENCE_RE = re.compile(r"(?<![\w.])(_[A-Za-z][\w.$^'-]*)(?![\w.])")
_MACRO_RE = re.compile(r"^\s*([A-Za-z][\w.]*)\b(.*)$")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+")

# pret's text macros are intentionally represented using the corpus' portable
# control-token spelling.  ``@`` remains the corpus terminator.
_CONTROL_MACROS = {
    "text_start": "{text_start}",
    "line": "<LINE>",
    "para": "<PARA>",
    "cont": "<CONT>",
    "next": "<NEXT>",
    "prompt": "<PROMPT>",
    "done": "<DONE>",
}
_TEXT_MACROS = {"text", "line", "para", "cont", "next", "prompt", "db", "dw"}
_TERMINATORS = {"done", "prompt", "endtext", "text_end"}
_LANGUAGE_WORDS = {
    "fr": {"bonjour", "vous", "une", "des", "dans", "avec", "pour", "pas", "est", "le", "la", "les"},
    "de": {"hallo", "guten", "tag", "der", "die", "das", "und", "nicht", "ein", "eine", "ist", "mit", "für", "sie"},
    "es": {"hola", "que", "los", "las", "una", "uno", "con", "para", "por", "no", "del", "el"},
    "it": {"ciao", "che", "gli", "una", "uno", "con", "per", "non", "del", "della", "il", "la"},
}


def _decode_quoted(value: str) -> str:
    try:
        decoded = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        decoded = value[1:-1]
    return str(decoded).replace("\n", "<LINE>").replace("\r", "")


def _quoted_text(value: str) -> str:
    return "".join(_decode_quoted(item) for item in _QUOTED_RE.findall(value))


def _normal_form(value: str) -> str:
    """Normalize assembly/corpus values for comparison without losing tokens."""
    value = corpus_to_engine(value or "")
    value = re.sub(r"<DONE>|@", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value.replace("\n", " ")).strip().casefold()
    return value


def _has_actual_text(value: str) -> bool:
    """Whether an assembled body contains text/control data, not just a terminator."""
    value = re.sub(r"<[^>]+>|\{[^}]+\}|@|\s+", "", value or "")
    return bool(value)


def _qid_suffix(qid: Any) -> str:
    return str(qid or "").split(".")[-1].lstrip("_")


def _label_qid(label: str) -> str:
    return _qid_suffix(label)


def _iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and path.suffix.lower() in {".asm", ".inc", ".s", ".txt"}:
            yield path


def parse_disassembly(root: str | Path) -> dict[str, dict[str, Any]]:
    """Return label candidates with normalized corpus text and callsites."""
    root = Path(root)
    candidates: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for path in _iter_source_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            label_match = _LABEL_RE.match(line)
            if label_match:
                # RGBDS local labels are scoped to the preceding global label.
                # They are intentionally excluded in v1: retaining one by its
                # raw spelling would collide across every source file and can
                # incorrectly attribute its body to the previous global.
                if label_match.group(1).startswith("."):
                    current = None
                    continue
                if current is not None and current.get("text") and not current.get("normalized"):
                    current["normalized"] = _normal_form(current["text"])
                label = label_match.group(1)
                current = candidates.setdefault(label, {
                    "label": label,
                    "qid": _label_qid(label),
                    "text": "",
                    "normalized": "",
                    "source": str(path),
                    "line": line_no,
                    "callsites": [],
                    "references": [],
                    "context": [],
                })
                continue
            macro_match = _MACRO_RE.match(line)
            if current is not None and macro_match:
                macro, args = macro_match.groups()
                macro = macro.lower()
                if macro in _CONTROL_MACROS:
                    current["text"] += _CONTROL_MACROS[macro]
                if macro in _TEXT_MACROS:
                    current["text"] += _quoted_text(args)
                if macro in _TERMINATORS:
                    if _has_actual_text(current["text"]):
                        current["text"] += "@"
                        current["normalized"] = _normal_form(current["text"])
                    else:
                        current["text"] = ""
                    current = None
            # A callsite is useful even when it occurs far from the label.  We
            # add it to every referenced label seen in the line and retain a
            # short private context window for manual review.
            references = [item.group(1) for item in _REFERENCE_RE.finditer(line)]
            if current is not None:
                current["references"].extend(references)
            for reference in references:
                candidate = candidates.setdefault(reference, {
                    "label": reference,
                    "qid": _label_qid(reference),
                    "text": "",
                    "normalized": "",
                    "source": str(path),
                    "line": None,
                    "callsites": [],
                    "references": [],
                    "context": [],
                })
                if reference != (current or {}).get("label"):
                    candidate["callsites"].append({"source": str(path), "line": line_no, "text": line.strip()})
    # Provide context after parsing, avoiding huge source excerpts.
    for candidate in candidates.values():
        if candidate.get("text") and not candidate.get("normalized"):
            candidate["normalized"] = _normal_form(candidate["text"])
        candidate["callsites"] = candidate["callsites"][:50]
        candidate["context"] = candidate["callsites"][:5]
    return {key: value for key, value in candidates.items() if value.get("text") or value.get("callsites") or value.get("references")}


def detect_language(candidates: Iterable[Mapping[str, Any]], expected: str) -> str:
    scores = Counter()
    for candidate in candidates:
        words = set(_WORD_RE.findall(str(candidate.get("text", "")).casefold()))
        for language, vocabulary in _LANGUAGE_WORDS.items():
            scores[language] += len(words & vocabulary)
    if not scores:
        return canonical_language(expected)
    detected, score = scores.most_common(1)[0]
    return detected if score else canonical_language(expected)


def _corpus_index(root: Path, language: str) -> dict[str, list[str]]:
    if not root.exists():
        return {}
    try:
        records = parse_redblue(root, language)
    except (OSError, ValueError, FileNotFoundError):
        return {}
    index: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.language == canonical_language(language) and record.text is not None:
            index[_qid_suffix(record.qid)].append(record.text)
    return dict(index)


def _corpus_english_values(root: Path) -> list[tuple[str, str]]:
    """Return normalized English text and qid suffixes for fuzzy matching."""
    if not root.exists():
        return []
    try:
        records = parse_redblue(root, "fr")
    except (OSError, ValueError, FileNotFoundError):
        return []
    values: list[tuple[str, str]] = []
    for record in records:
        if record.language == "en" and record.text is not None:
            values.append((_normal_form(record.text), _qid_suffix(record.qid)))
    return values


def _fuzzy_engine_candidates(key: str, english_values: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    """Find a few conservative English/qid candidates without quadratic noise."""
    key_norm = _normal_form(key)
    if not key_norm:
        return []
    allow_fuzzy = len(key_norm) >= 5
    initial = key_norm[0]
    scored: list[tuple[float, str]] = []
    for english_norm, qid in english_values:
        if not english_norm or english_norm[0] != initial:
            continue
        # Avoid comparing menu labels with long dialogue paragraphs.
        if abs(len(key_norm) - len(english_norm)) > max(32, len(key_norm)):
            continue
        score = 1.0 if key_norm == english_norm else SequenceMatcher(None, key_norm, english_norm).ratio()
        if score < 1.0 and not allow_fuzzy:
            continue
        if score >= 0.72:
            scored.append((score, qid))
    return [
        (qid, {"engine_key": key, "score": round(score, 3), "method": "exact" if score == 1.0 else "fuzzy"})
        for score, qid in sorted(scored, reverse=True)[:3]
    ]


def _coverage_candidates(root: Path, language: str, corpus_root: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    path = root / ".cache" / "interactive" / language / "coverage.json"
    if not path.is_file():
        return {}
    try:
        coverage = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched = (coverage.get("engine") or {}).get("unmatched") or coverage.get("unmatched") or {}
    english_values = _corpus_english_values(corpus_root or root)
    if isinstance(unmatched, dict):
        for catalog, values in unmatched.items():
            keys = values.keys() if isinstance(values, dict) else values if isinstance(values, list) else []
            for key in keys:
                for qid, candidate in _fuzzy_engine_candidates(key, english_values):
                    result[qid].append(candidate)
    elif isinstance(unmatched, list):
        for key in unmatched:
            for qid, candidate in _fuzzy_engine_candidates(key, english_values):
                result[qid].append(candidate)
    return dict(result)


def audit_language(
    language: str,
    checkout: str | Path,
    *,
    expected: str | None = None,
    trusted: bool = True,
    corpus_root: str | Path | None = None,
    project_root: str | Path = ROOT,
) -> dict[str, Any]:
    expected = canonical_language(expected or language)
    candidates = parse_disassembly(checkout)
    corpus = _corpus_index(Path(corpus_root), expected) if corpus_root else {}
    coverage = _coverage_candidates(Path(project_root), expected, Path(corpus_root) if corpus_root else None)
    stats = Counter()
    for candidate in candidates.values():
        qid = candidate["qid"]
        expected_values = corpus.get(qid, [])
        if not candidate.get("text"):
            status = "reference_only"
        elif not expected_values:
            status = "missing"
        elif any(_normal_form(candidate["text"]) == _normal_form(value) for value in expected_values):
            status = "match"
        else:
            status = "divergence"
        candidate["status"] = status
        candidate["corpus"] = expected_values[:5]
        candidate["engine_candidates"] = coverage.get(qid, [])
        stats[status] += 1
        stats["callsites"] += len(candidate.get("callsites", []))
    detected = detect_language(candidates.values(), expected)
    recommended = [
        candidate["qid"] for candidate in candidates.values()
        if candidate.get("status") == "match" and candidate.get("engine_candidates") and trusted and detected == expected
    ]
    return {
        "language": language,
        "expected_language": expected,
        "detected_language": detected,
        "trusted": bool(trusted),
        "recommended_candidates": recommended,
        "stats": {
            "candidates": len(candidates),
            "match": stats["match"],
            "divergence": stats["divergence"],
            "missing": stats["missing"],
            "reference_only": stats["reference_only"],
            "callsites": stats["callsites"],
            "engine_enriched": sum(bool(item.get("engine_candidates")) for item in candidates.values()),
        },
        "candidates": list(candidates.values()),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    stats = report["stats"]
    lines = [
        f"# Disassembly audit: `{report['language']}`", "",
        f"- Expected language: `{report['expected_language']}`",
        f"- Detected language: `{report['detected_language']}`",
        f"- Trusted source: `{str(report['trusted']).lower()}`", "",
        "## Statistics", "",
        "| candidates | match | divergence | missing | reference-only | callsites | engine enriched |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {stats['candidates']} | {stats['match']} | {stats['divergence']} | {stats['missing']} | {stats['reference_only']} | {stats['callsites']} | {stats['engine_enriched']} |", "",
        "## Candidates", "",
        "| label | qid | status | callsites | callsite context |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for candidate in report["candidates"]:
        context = "; ".join(item.get("text", "") for item in candidate.get("context", []))
        context = context.replace("|", "\\|")[:160]
        lines.append(f"| `{candidate['label']}` | `{candidate['qid']}` | {candidate.get('status', '')} | {len(candidate.get('callsites', []))} | {context} |")
    return "\n".join(lines) + "\n"


def run_audit(root: str | Path = ROOT, *, checkout_runner=None) -> list[dict[str, Any]]:
    """Clone pinned sources and write private JSON/Markdown audit reports."""
    root = Path(root)
    config = project_config(root)
    disassemblies = config.get("disassemblies", {})
    if not disassemblies:
        raise ValueError("config/pipeline.toml has no [disassemblies.*] entries")
    cache = root / ".cache" / "audit"
    checkout_root = cache / "disassemblies"
    reports_root = cache / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    corpus = Path(config.get("corpus", {}).get("path", ""))
    if not corpus.is_absolute():
        corpus = (root / corpus).resolve()
    if not corpus.exists():
        corpus = root / ".cache" / "dependencies" / "poke-corpus"
    reports: list[dict[str, Any]] = []
    if checkout_runner is None:
        # Keep the developer-only audit importable without pulling Pillow and
        # the interactive builder into ordinary parser/CLI startup.
        from .builder import ensure_checkout
        checkout_runner = ensure_checkout
    for language, source in sorted(disassemblies.items()):
        destination = checkout_root / language
        checkout_runner(source["source"], source["revision"], destination, sparse_paths=("text", "scripts"))
        report = audit_language(language, destination, expected=language, trusted=source.get("trusted", True), corpus_root=corpus, project_root=root)
        reports.append(report)
        (reports_root / f"{language}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (reports_root / f"{language}.md").write_text(_markdown(report), encoding="utf-8")
    index = {"languages": [item["language"] for item in reports], "reports": {item["language"]: item["stats"] for item in reports}}
    (reports_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return reports
