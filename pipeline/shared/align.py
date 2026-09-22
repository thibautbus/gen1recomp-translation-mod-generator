from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Iterable

from .model import Alignment, CorpusRecord
from .corpus import canonical_language


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r\n", "\n").strip())


def align(records: Iterable[CorpusRecord], source_lang: str = "en", target_lang: str = "fr") -> list[Alignment]:
    records = list(records)
    en = [r for r in records if r.language == source_lang]
    target_lang = canonical_language(target_lang or "fr")
    fr = [r for r in records if r.language == target_lang]
    by_qid: dict[tuple[str, str], list[CorpusRecord]] = defaultdict(list)
    by_text: dict[tuple[str, str], list[CorpusRecord]] = defaultdict(list)
    for r in fr:
        if r.qid:
            by_qid[(r.game, r.qid)].append(r)
        if r.english:
            by_text[(r.game, _norm(r.english))].append(r)
    result: list[Alignment] = []
    for source in en:
        target = by_qid.get((source.game, source.qid), []) if source.qid else []
        method = "qid" if len(target) == 1 else "unmatched"
        if not target:
            target = by_text.get((source.game, _norm(source.text)), [])
            method = "english-exact" if len(target) == 1 else "unmatched"
        chosen = target[0] if len(target) == 1 else None
        result.append(Alignment(source.qid or f"unkeyed:{len(result)}", source.game, source, chosen, method, target_lang=target_lang))
    return result


CORPUS_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/corpus-overrides"


def apply_corpus_overrides(items: list[Alignment], corpus_overrides: str | Path | None) -> list[Alignment]:
    """Apply qid-indexed corpus corrections without changing source records."""
    if not corpus_overrides or not Path(corpus_overrides).exists():
        return items
    data = json.loads(Path(corpus_overrides).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("corpus overrides must be a JSON object")
    schema = data.get("schema")
    version = data.get("version")
    if schema != CORPUS_OVERRIDES_SCHEMA:
        raise ValueError("unsupported corpus overrides schema")
    if version != 1:
        raise ValueError("unsupported corpus overrides schema version")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("corpus overrides entries must be an object")
    for item in items:
        row = entries.get(item.qid)
        if row is None:
            continue
        if isinstance(row, dict):
            # Optional justification-bearing rows keep the value under override.
            value = row.get("override")
        else:
            value = row
        if value is not None:
            item.override = str(value)
    return items


def corpus_overrides(items: Iterable[Alignment]) -> dict:
    """Return a qid-indexed corpus-overrides document."""
    return {"schema": CORPUS_OVERRIDES_SCHEMA, "version": 1,
            # Only discovered overrides are persisted. Values contain only an
            # override (and may carry a short justification), never review
            # status or notes.
            "entries": {x.qid: {"override": x.override} for x in items if x.override is not None}}
