"""Match GoldSilver corpus rows to versioned Gen1Recomp engine strings."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .engine import load_engine_overrides, match_engine_catalog
from .engine_scope import (
    complete_engine_keys, is_gen2_path, iter_callsites, load_manifest,
    verified_source,
)
from .model import CorpusRecord


def engine_string_keys(
    callsites: Iterable[Mapping[str, Any]], manifest: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str]]:
    """Return the full Strings catalog and its Gen-2 callsite subset."""
    manifest = manifest or load_manifest()
    gen2_keys: set[str] = set()
    rows = list(callsites)
    for callsite in rows:
        if callsite.get("kind") not in {"call", "source"}:
            continue
        source = callsite.get("source")
        path = callsite.get("path")
        if not isinstance(source, str) or not source or not isinstance(path, str):
            continue
        if is_gen2_path(path):
            gen2_keys.add(source)
    return complete_engine_keys(rows, manifest), gen2_keys


def _corpus_records(
    corpus_rows: Iterable[tuple[str, str, str]], target_lang: str,
) -> list[CorpusRecord]:
    records: list[CorpusRecord] = []
    for qid, english, target in corpus_rows:
        records.append(CorpusRecord(qid, "en", english, "gold"))
        records.append(CorpusRecord(qid, target_lang, target, "gold", english=english))
    return records


def match_gs_engine_strings(
    corpus_rows: Iterable[tuple[str, str, str]],
    gen1recomp: str | Path,
    target_lang: str,
) -> tuple[dict[str, str], dict[str, dict]]:
    """Match and scope the pinned engine's complete Strings callsite catalog."""
    manifest = load_manifest()
    source, _root, revision = verified_source(gen1recomp, manifest)
    callsites = iter_callsites(source)
    all_keys, gen2_keys = engine_string_keys(callsites, manifest)
    root = Path(__file__).resolve().parents[1]
    overrides = load_engine_overrides(
        root / "overrides" / target_lang / "gs" / "engine.json",
    )
    stale_overrides = sorted(set(overrides) - all_keys)
    if stale_overrides:
        raise ValueError(
            f"Gold engine overrides contain {len(stale_overrides)} unknown key(s): "
            f"{stale_overrides!r}"
        )
    values, report = match_engine_catalog(
        sorted(all_keys),
        _corpus_records(corpus_rows, target_lang),
        overrides=overrides,
        semantic_anchors=root / "config" / "gs" / "semantic_anchors.json",
        target_lang=target_lang,
    )
    translated = {key for key, value in values.items() if value}
    shipped = {key: value for key, value in values.items() if value}
    report.update({
        "source_revision": revision,
        "catalog_kind": "Strings and Strings.source callsites",
    })
    gen2_translated = len(translated & gen2_keys)
    gen2_report = {
        "source_revision": revision,
        "scope": "at least one callsite in a gen2 source subtree",
        "translated": gen2_translated,
        "total": len(gen2_keys),
        "percent": round(100.0 * gen2_translated / len(gen2_keys), 2) if gen2_keys else 100.0,
    }
    return shipped, {"engine": report, "engine_gen2": gen2_report}
