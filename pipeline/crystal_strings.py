"""Corpus-backed catalogs that exist only when the running game is Crystal."""
from __future__ import annotations

import json
from pathlib import Path

from .engine import (
    _normal, _structural_form, load_engine_overrides, match_engine_catalog,
    printf_directives,
)
from .gs_engine import _corpus_records, load_gs_engine_scope_exclusions
from .gs_join import read_corpus_rows
from .tokens import DYNAMIC_TOKEN_RE, corpus_to_engine


_ROOT = Path(__file__).resolve().parents[1]
CRYSTAL_ANCHORS = _ROOT / "config" / "gsc" / "crystal_semantic_anchors.json"
CRYSTAL_SELECTORS = _ROOT / "config" / "gsc" / "crystal_string_selectors.json"


def _load_selectors(path: str | Path = CRYSTAL_SELECTORS) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "gen1recomp-translation-mods/crystal-string-selectors" or data.get("version") != 1:
        raise ValueError("unsupported Crystal string selectors schema")
    selectors = data.get("selectors")
    if not isinstance(selectors, dict):
        raise ValueError("Crystal string selectors require a selectors object")
    for source, row in selectors.items():
        if (not isinstance(source, str) or not source or not isinstance(row, dict)
                or not isinstance(row.get("qid"), str) or not row["qid"].startswith("c.")
                or row.get("kind") not in {"at_segment", "full"}):
            raise ValueError(f"invalid Crystal string selector for {source!r}")
        if row["kind"] == "at_segment" and (
            isinstance(row.get("index"), bool) or not isinstance(row.get("index"), int) or row["index"] < 0
        ):
            raise ValueError(f"invalid Crystal at-segment selector for {source!r}")
        maps = row.get("printf_indexes")
        if maps is not None and (
            not isinstance(maps, dict) or not all(
                isinstance(lang, str) and isinstance(indexes, list)
                and all(not isinstance(index, bool) and isinstance(index, int) and index >= 0 for index in indexes)
                for lang, indexes in maps.items()
            )
        ):
            raise ValueError(f"invalid Crystal printf selector for {source!r}")
    return selectors


def _selected_value(source: str, row: dict, english: str, target: str, language: str) -> str | None:
    if row["kind"] == "at_segment":
        index = row["index"]
        english_parts = english.split("@")
        target_parts = target.split("@")
        if index >= len(english_parts) or index >= len(target_parts):
            return None
        source_piece = corpus_to_engine(english_parts[index], bare_dynamic_tokens=True).strip()
        target_piece = corpus_to_engine(target_parts[index], bare_dynamic_tokens=True).strip()
    else:
        source_piece = corpus_to_engine(english, bare_dynamic_tokens=True).strip()
        target_piece = corpus_to_engine(target, bare_dynamic_tokens=True).strip()
    if not source_piece or not target_piece:
        return None
    if (_normal(source_piece, bare_dynamic_tokens=True) != _normal(source, bare_dynamic_tokens=True)
            and _structural_form(source_piece, bare_dynamic_tokens=True)
            != _structural_form(source, bare_dynamic_tokens=True)):
        return None
    maps = row.get("printf_indexes")
    if maps is None:
        return target_piece
    indexes = maps.get(language, maps.get("default"))
    if not isinstance(indexes, list):
        return None
    directives = [directive for directive in printf_directives(source) if directive != "%%"]
    occurrences = list(DYNAMIC_TOKEN_RE.finditer(target_piece))
    if len(occurrences) != len(indexes) or any(index >= len(directives) for index in indexes):
        return None
    out: list[str] = []
    cursor = 0
    for occurrence, index in zip(occurrences, indexes):
        out.append(target_piece[cursor:occurrence.start()])
        out.append(directives[index])
        cursor = occurrence.end()
    out.append(target_piece[cursor:])
    return "".join(out)


def match_crystal_engine_strings(
    corpus_dir: str | Path, language: str,
    corpus_rows: list[tuple[str, str, str]] | None = None,
) -> tuple[dict[str, str], dict]:
    """Resolve the 48 known Crystal-only ``Strings`` keys from PokeCorpus.

    A language absent from the Crystal collection (currently Korean) has no
    corpus rows to match against, but a hand-composed
    overrides/<language>/gsc/engine.json entry for one of these keys is
    still shipped -- composing Korean text for a Crystal-exclusive feature
    is exactly how this catalog covers that gap (there being no Crystal
    corpus row is the reason the translation has to be composed, not a
    reason to discard it). Keys neither in the corpus nor in overrides stay
    an explicit English fallback, same as everywhere else in this catalog.
    ``corpus_rows``, when given, is read_corpus_rows()'s own output for this
    corpus_dir/language, same as crystal_registry_catalogs()/
    join_crystal_rom_text().
    """
    keys = sorted(load_gs_engine_scope_exclusions())
    corpus_dir = Path(corpus_dir)
    target = corpus_dir / f"{language}_msg.txt"
    if corpus_rows is not None:
        rows = corpus_rows
    elif not target.is_file():
        rows = []
    else:
        rows = read_corpus_rows(corpus_dir, target_lang=language)
    all_overrides = load_engine_overrides(
        _ROOT / "overrides" / language / "gsc" / "engine.json",
    )
    # The scanner's file covers the complete Gen 2 engine catalog.  This
    # specialized matcher owns only the Crystal-exclusive subset, so pass
    # through just those rows rather than duplicating gs_engine's full match.
    crystal_overrides = {
        source: row for source, row in all_overrides.items() if source in keys
    }
    values, report = match_engine_catalog(
        keys,
        _corpus_records(rows, language),
        overrides=crystal_overrides,
        semantic_anchors=CRYSTAL_ANCHORS,
        target_lang=language,
    )
    shipped = {key: value for key, value in values.items() if value}
    rows_by_qid = {qid: (english, target) for qid, english, target in rows}
    for source, selector in _load_selectors().items():
        pair = rows_by_qid.get(selector["qid"])
        if pair is None:
            continue
        selected = _selected_value(source, selector, pair[0], pair[1], language)
        if selected:
            shipped[source] = selected
            report["details"][source] = "crystal_selector"
            report["provenance"][source] = {
                "method": "crystal_selector", "qid": selector["qid"], "target_lang": language,
            }
            report["ambiguous"].pop(source, None)
    report["unmatched"] = sorted(set(keys) - set(shipped))
    report["translated"] = len(shipped)
    report["fallback_english"] = len(keys) - len(shipped)
    report["percent"] = round(100.0 * len(shipped) / len(keys), 2) if keys else 100.0
    report["policy"] = "english-fallback"
    report["catalog_kind"] = "Crystal-exclusive Strings callsites"
    report["scope"] = "Crystal-only keys excluded from the Gold/Silver engine metric"
    return shipped, report
