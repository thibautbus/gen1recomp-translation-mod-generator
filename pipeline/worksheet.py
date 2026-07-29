"""Versioned storage for the small set of discovered in-game overrides.

The file deliberately contains no corpus text and no review state.  It is a
qid-to-override map that can be applied repeatedly without modifying aligned
source data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .align import worksheet as as_overrides
from .model import Alignment

SCHEMA = "gen1recomp-translation-mods/overrides"
VERSION = 1


def dump(items: Iterable[Alignment], path: str | Path, corpus_revision: str = "") -> Path:
    body = as_overrides(items)
    # A revision is useful provenance, but never include corpus rows or text.
    if corpus_revision:
        body["corpus_revision"] = corpus_revision
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def load(path: str | Path) -> dict:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("overrides file must contain a JSON object")
    schema = body.get("schema")
    version = body.get("version")
    # Read the old empty worksheet during migration; writing always emits the
    # new schema and therefore cannot reintroduce review/notes fields.
    if schema == "gen1recomp-translation-mods/worksheet":
        if version not in (None, 2):
            raise ValueError("legacy worksheet schema/version mismatch")
        entries = body.get("entries", {})
        migrated = {
            qid: {"override": row["override"]}
            for qid, row in entries.items()
            if isinstance(row, dict) and row.get("override") is not None
        }
        return {"schema": SCHEMA, "version": VERSION, "entries": migrated}
    if schema != SCHEMA or version != VERSION:
        raise ValueError(f"overrides schema/version mismatch (expected {SCHEMA} v{VERSION})")
    entries = body.get("entries", body.get("overrides", {}))
    if not isinstance(entries, dict):
        raise ValueError("overrides entries must be an object")
    return {**body, "entries": entries}


# Names used by older integrations.  Keep these aliases while callers migrate
# from worksheet terminology; they do not preserve any review state.
dump_overrides = dump
load_overrides = load
