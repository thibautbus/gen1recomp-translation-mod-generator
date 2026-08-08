"""Readers for local poke-corpus/RedBlue exports.

The upstream corpus has changed layout over time.  We deliberately accept
JSON, JSONL, CSV and TSV and normalize their common qid/language/text fields;
unknown fields are retained in ``metadata`` for provenance.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .model import CorpusRecord

_TEXT_KEYS = ("text", "translation", "value", "line", "content", "string")
_QID_KEYS = ("qid", "q_id", "id", "key", "label", "pointer", "address")
_LANG_RE = re.compile(r"(?:^|[-_.])(en|eng|fr|fra|french|english|de|deu|german|es|spa|spanish|it|ita|italian|ja|ja-hrkt|jpn|japanese)(?:$|[-_.])", re.I)
_GAME_RE = re.compile(r"(?:^|[-_.])(red|blue|yellow|redblue)(?:$|[-_.])", re.I)
_VERSION_SUFFIX = re.compile(r"\^(RG|R|G|B)(?=\.|$)")


def canonical_language(value: Any, default: str = "en") -> str:
    value = str(value or "").lower()
    if value in {"fr", "fra", "french", "français", "francais"}:
        return "fr"
    if value in {"en", "eng", "english"}:
        return "en"
    if value in {"de", "deu", "german", "deutsch"}: return "de"
    if value in {"es", "spa", "spanish", "español", "espanol"}: return "es"
    if value in {"it", "ita", "italian", "italiano"}: return "it"
    if value in {"ja", "jpn", "japanese", "ja-hrkt", "ja_hrkt", "ja.hrkt"}: return "ja-Hrkt"
    if value: return str(value)
    return default


def _language(value: Any, path: Path, default: str = "en") -> str:
    explicit = canonical_language(value, "")
    if explicit:
        return explicit
    match = _LANG_RE.search(path.stem)
    return canonical_language(match.group(1), default) if match else default


def _game(value: Any, path: Path) -> str:
    text = str(value or "").lower()
    if text in {"red", "blue", "yellow"}:
        return text
    match = _GAME_RE.search(path.stem)
    if match and match.group(1).lower() in {"red", "blue", "yellow"}:
        return match.group(1).lower()
    return "red"


def _records(value: Any, path: Path, inherited: dict[str, Any] | None = None, target_lang: str = "fr") -> Iterable[CorpusRecord]:
    inherited = inherited or {}
    if isinstance(value, list):
        for item in value:
            yield from _records(item, path, inherited, target_lang)
        return
    if not isinstance(value, dict):
        return
    # Compact bilingual records: {qid: ..., en: "...", fr: "..."}.
    language_keys = [k for k, v in value.items() if isinstance(v, str) and (k == "en" or canonical_language(k, "") == target_lang)]
    if not any(k in value for k in _TEXT_KEYS) and language_keys:
        qid = next((value.get(k) for k in _QID_KEYS if value.get(k) not in (None, "")), inherited.get("qid"))
        for lang in language_keys:
            if isinstance(value.get(lang), str):
                known = set(_QID_KEYS) | set(language_keys) | {"game", "version"}
                yield CorpusRecord(str(qid) if qid is not None else None, canonical_language(lang), value[lang],
                                   _game(value.get("game", value.get("version", inherited.get("game"))), path),
                                   str(path), metadata={**inherited, **{k: v for k, v in value.items() if k not in known}})
        return
    # A mapping of qid -> text is a common corpus export.
    if not any(k in value for k in _TEXT_KEYS):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                yield from _records(item, path, {**inherited, "qid": key}, target_lang)
            elif isinstance(item, str):
                yield CorpusRecord(str(key), _language(inherited.get("language"), path), item,
                                   _game(inherited.get("game"), path), str(path), metadata=inherited)
        return
    qid = next((value.get(k) for k in _QID_KEYS if value.get(k) not in (None, "")), inherited.get("qid"))
    text = next((value.get(k) for k in _TEXT_KEYS if isinstance(value.get(k), str)), None)
    if text is None:
        return
    lang = _language(value.get("language", value.get("lang", inherited.get("language"))), path)
    game = _game(value.get("game", value.get("version", inherited.get("game"))), path)
    english = value.get("english", value.get("en"))
    known = set(_TEXT_KEYS) | set(_QID_KEYS) | {
        "language", "lang", "game", "version", "english", "en", "override",
        # Ignore obsolete editorial fields from older local exports instead
        # of carrying them into parsed corpus metadata.
        "reviewed", "notes",
    }
    metadata = {**inherited, **{k: v for k, v in value.items() if k not in known}}
    yield CorpusRecord(str(qid) if qid is not None else None, lang, text, game, str(path),
                       str(english) if english is not None else None,
                       str(value["override"]) if value.get("override") is not None else None,
                       metadata=metadata)


def read_file(path: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        result: list[CorpusRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.extend(_records(json.loads(line), path, target_lang=target_lang))
        return result
    if suffix == ".json":
        return list(_records(json.loads(path.read_text(encoding="utf-8")), path, target_lang=target_lang))
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open(encoding="utf-8", newline="") as fh:
            return list(_records(list(csv.DictReader(fh, delimiter=delimiter)), path, target_lang=target_lang))
    return []


def read_parallel_game(directory: str | Path, target_lang: str = "fr", game: str = "redblue") -> list[CorpusRecord]:
    """Read the canonical poke-corpus parallel message files for a game family.

    ``game`` selects the corpus convention: ``redblue`` (version-suffixed qids,
    e.g. ``^RG``/``^B``) or ``yellow`` (single game, qids prefixed ``y.``).

    Each file is UTF-8 and has exactly one logical entry per line.  We keep
    empty lines and backslash escapes (``\\x60``) verbatim: token conversion is
    a later, explicit pipeline step.  Qids are preserved as-is (``y.*`` qids
    are never rewritten into ``rb.*``).
    """
    directory = Path(directory)
    target_lang = canonical_language(target_lang)
    target_file_lang = target_lang
    target_path = directory / f"{target_file_lang}_msg.txt"
    if not target_path.is_file():
        target_path = next((p for p in directory.glob("*_msg.txt") if p.stem[:-4].lower() == target_file_lang.lower()), target_path)
    paths = {"qid": directory / "qid_msg.txt", "en": directory / "en_msg.txt", target_file_lang: target_path}
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"{game} parallel corpus missing: " + ", ".join(missing))
    lines = {name: path.read_text(encoding="utf-8").splitlines() for name, path in paths.items()}
    counts = {name: len(values) for name, values in lines.items()}
    if len(set(counts.values())) != 1:
        raise ValueError(f"{game} parallel files have different line counts: {counts}")
    result: list[CorpusRecord] = []
    for index, (qid, english, translation) in enumerate(zip(lines["qid"], lines["en"], lines[target_file_lang])):
        if game == "yellow":
            scope = "yellow"
            suffix = None
            base_qid = qid
        else:
            suffix_match = _VERSION_SUFFIX.search(qid)
            suffix = suffix_match.group(1) if suffix_match else ""
            # ^B is Blue-only; ^RG/^R are Red-side data. Unsuffixed rows are
            # shared. ^G is retained explicitly (not silently treated as Red).
            scope = {"B": "blue", "R": "red", "RG": "red", "G": "green"}.get(suffix, "both")
            base_qid = qid[:suffix_match.start()] if suffix_match else qid
        metadata = {"version_suffix": suffix or None, "version_scope": scope, "base_qid": base_qid, "line": index + 1, "format": "parallel-msg"}
        result.append(CorpusRecord(qid, "en", english, scope, str(paths["en"]), metadata=metadata))
        # English is retained on the target-language record to make exact
        # fallback auditable even when a qid is absent in future corpus
        # revisions.
        result.append(CorpusRecord(qid, target_lang, translation, scope, str(paths[target_file_lang]), english=english, metadata=metadata.copy()))
    return result


def read_parallel_redblue(directory: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    """Read the canonical poke-corpus RedBlue parallel message files."""
    return read_parallel_game(directory, target_lang, "redblue")


def read_parallel_yellow(directory: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    """Read the canonical poke-corpus Yellow parallel message files."""
    return read_parallel_game(directory, target_lang, "yellow")


def parse_yellow(root: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    """Parse Yellow exports while retaining qids and source provenance."""
    root = Path(root)
    if root.is_dir() and (root / "qid_msg.txt").is_file():
        return read_parallel_yellow(root, target_lang)
    yellow = root / "Yellow"
    if not yellow.is_dir():
        yellow = root / "corpus" / "Yellow"
    if yellow.is_dir() and (yellow / "qid_msg.txt").is_file():
        return read_parallel_yellow(yellow, target_lang)
    raise FileNotFoundError(f"Yellow corpus not found under {root}")


def load_corpus(root: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    root = Path(root)
    if root.is_dir() and (root / "qid_msg.txt").is_file():
        return read_parallel_redblue(root, target_lang)
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.suffix.lower() in {".json", ".jsonl", ".ndjson", ".csv", ".tsv"})
    records: list[CorpusRecord] = []
    for path in paths:
        records.extend(read_file(path, target_lang))
    return records


def parse_redblue(root: str | Path, target_lang: str = "fr") -> list[CorpusRecord]:
    """Parse RedBlue exports while retaining qids and source provenance."""
    root = Path(root)
    if root.is_dir() and (root / "qid_msg.txt").is_file():
        return read_parallel_redblue(root, target_lang)
    redblue = root / "RedBlue"
    if not redblue.is_dir():
        redblue = root / "corpus" / "RedBlue"
    if redblue.is_dir() and (redblue / "qid_msg.txt").is_file():
        return read_parallel_redblue(redblue, target_lang)
    records = load_corpus(root, target_lang)
    for record in records:
        if record.game == "red" and "blue" in record.source.lower():
            record.game = "blue"
    return records
