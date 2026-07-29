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


def apply_overrides(items: list[Alignment], overrides: str | Path | None) -> list[Alignment]:
    """Apply versioned, non-destructive qid overrides to aligned rows.

    New files contain ``entries: {qid: text}``.  The reader also accepts the
    former worksheet shape so existing local files can be migrated without
    copying corpus text into the versioned overrides file.
    """
    if not overrides or not Path(overrides).exists():
        return items
    data = json.loads(Path(overrides).read_text(encoding="utf-8"))
    if not isinstance(data, (dict, list)):
        raise ValueError("overrides must be a JSON object")
    schema = data.get("schema") if isinstance(data, dict) else None
    version = data.get("version") if isinstance(data, dict) else None
    if schema and schema not in {"gen1recomp-translation-mods/overrides", "gen1recomp-translation-mods/worksheet"}:
        raise ValueError("unsupported overrides schema")
    if schema == "gen1recomp-translation-mods/overrides" and version not in (None, 1):
        raise ValueError("unsupported overrides schema version")
    if schema == "gen1recomp-translation-mods/worksheet" and version not in (None, 2):
        raise ValueError("unsupported legacy worksheet schema version")
    entries = data.get("overrides", data.get("entries", data)) if isinstance(data, dict) else data
    for item in items:
        row = entries.get(item.qid) if isinstance(entries, dict) else next((x for x in entries if x.get("qid") == item.qid), None)
        if row is None:
            continue
        if isinstance(row, dict):
            # Legacy worksheet rows and optional justification-bearing rows.
            value = row.get("override")
        else:
            value = row
        if value is not None:
            item.override = str(value)
    return items


def apply_worksheet(items: list[Alignment], worksheet: str | Path | None) -> list[Alignment]:
    """Backward-compatible alias for callers still using the old name."""
    return apply_overrides(items, worksheet)


def worksheet(items: Iterable[Alignment]) -> dict:
    """Return the minimal serializable overrides document."""
    return {"schema": "gen1recomp-translation-mods/overrides", "version": 1,
            # Only discovered overrides are persisted. Values contain only an
            # override (and may carry a short justification), never review
            # status or notes.
            "entries": {x.qid: {"override": x.override} for x in items if x.override is not None}}


overrides = worksheet
