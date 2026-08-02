"""Versioned storage for qid-indexed corpus corrections.

The file deliberately contains no corpus text and no review state.  It is a
qid-to-override map that can be applied repeatedly without modifying aligned
source data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .align import corpus_overrides
from .model import Alignment

SCHEMA = "gen1recomp-translation-mods/corpus-overrides"
VERSION = 1


def dump(items: Iterable[Alignment], path: str | Path, corpus_revision: str = "") -> Path:
    body = corpus_overrides(items)
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
        raise ValueError("corpus overrides file must contain a JSON object")
    schema = body.get("schema")
    version = body.get("version")
    if schema != SCHEMA or version != VERSION:
        raise ValueError(f"corpus overrides schema/version mismatch (expected {SCHEMA} v{VERSION})")
    entries = body.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("corpus overrides entries must be an object")
    return {**body, "entries": entries}


dump_corpus_overrides = dump
load_corpus_overrides = load
