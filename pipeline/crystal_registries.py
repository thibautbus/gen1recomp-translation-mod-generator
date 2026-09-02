"""Corpus-backed named records that exist only in Pokémon Crystal."""
from __future__ import annotations

from pathlib import Path

from .gs_index_join import IndexedEntry, join_by_index, join_landmarks, parse_indexed_catalog
from .gs_join import read_corpus_rows


CRYSTAL_ONLY_ITEMS = frozenset({"BLUE_CARD", "CLEAR_BELL", "EGG_TICKET", "GS_BALL"})
CRYSTAL_ONLY_TRAINER_CLASSES = frozenset({"MYSTICALMAN"})
CRYSTAL_ONLY_LANDMARKS = frozenset({"LANDMARK_BATTLE_TOWER"})


def _empty_stats(total: int) -> dict:
    return {"total": total, "translated": 0, "no_corpus_entry": total, "fallback_english": total}


def _filter(entries: list[IndexedEntry], ids: frozenset[str], name: str) -> list[IndexedEntry]:
    selected = [entry for entry in entries if entry.id in ids]
    missing = sorted(ids - {entry.id for entry in selected})
    if missing:
        raise ValueError(f"{name} is missing required Crystal ids: {', '.join(missing)}")
    return selected


def crystal_registry_catalogs(
    crystal_out_dir: str | Path, corpus_dir: str | Path, language: str,
    corpus_rows: list[tuple[str, str, str]] | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict]]:
    """Return the six Crystal-only named records, never Gold/Silver rows.

    ``corpus_rows``, when given, is read_corpus_rows()'s own output for this
    corpus_dir/language: callers that already read it for another Crystal
    catalog in the same build (crystal_feature_catalogs joins three) can pass
    it through instead of having this function read the corpus files again.
    """
    crystal_out_dir, corpus_dir = Path(crystal_out_dir), Path(corpus_dir)
    items = _filter(parse_indexed_catalog(crystal_out_dir / "gs_items.tsv"), CRYSTAL_ONLY_ITEMS, "gs_items.tsv")
    classes = _filter(
        parse_indexed_catalog(crystal_out_dir / "gs_trainer_classes.tsv"),
        CRYSTAL_ONLY_TRAINER_CLASSES,
        "gs_trainer_classes.tsv",
    )
    landmarks = _filter(
        parse_indexed_catalog(crystal_out_dir / "gs_landmarks.tsv"),
        CRYSTAL_ONLY_LANDMARKS,
        "gs_landmarks.tsv",
    )
    groups = {
        "item_names": (items, "c.names.ItemNames."),
        "trainer_class_names": (classes, "c.class_names.TrainerClassNames."),
    }
    if corpus_rows is not None:
        rows = corpus_rows
    elif not (corpus_dir / f"{language}_msg.txt").is_file():
        catalogs = {name: {} for name in (*groups, "landmarks")}
        stats = {name: _empty_stats(len(entries)) for name, (entries, _prefix) in groups.items()}
        stats["landmarks"] = _empty_stats(len(landmarks))
        return catalogs, stats
    else:
        rows = read_corpus_rows(corpus_dir, target_lang=language)
    catalogs: dict[str, dict[str, str]] = {}
    stats: dict[str, dict] = {}
    for name, (entries, prefix) in groups.items():
        catalogs[name], stats[name] = join_by_index(entries, rows, prefix)
        stats[name]["fallback_english"] = stats[name]["no_corpus_entry"]
    catalogs["landmarks"], stats["landmarks"] = join_landmarks(landmarks, rows)
    stats["landmarks"]["fallback_english"] = stats["landmarks"]["no_corpus_entry"]
    return catalogs, stats
