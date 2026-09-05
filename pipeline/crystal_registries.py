"""Corpus-backed named records that exist only in Pokémon Crystal."""
from __future__ import annotations

import json
from pathlib import Path

from .gs_index_join import IndexedEntry, join_by_index, join_landmarks, parse_indexed_catalog
from .gs_join import read_corpus_rows


CRYSTAL_ONLY_ITEMS = frozenset({"BLUE_CARD", "CLEAR_BELL", "EGG_TICKET", "GS_BALL"})
CRYSTAL_ONLY_TRAINER_CLASSES = frozenset({"MYSTICALMAN"})
CRYSTAL_ONLY_LANDMARKS = frozenset({"LANDMARK_BATTLE_TOWER"})

_REGISTRY_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/crystal-registry-overrides"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_crystal_registry_overrides(language: str, root: str | Path = _REPO_ROOT) -> dict[str, str]:
    """Load hand-composed id->name overrides for the six Crystal-only records.

    A language absent from the Crystal collection (Korean today) has no
    corpus row to draw a translation from at all, so this catalog's own
    established practice is to compose one by hand instead --
    overrides/<language>/gsc/crystal_registries.json, keyed by the same
    ``IndexedEntry.id`` (``BLUE_CARD``, ``LANDMARK_BATTLE_TOWER``, ...) the
    corpus join itself uses. Missing file: no overrides, same as an empty
    catalog.
    """
    path = Path(root) / "overrides" / language / "gsc" / "crystal_registries.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != _REGISTRY_OVERRIDES_SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported Crystal registry overrides schema: {path}")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"Crystal registry overrides require an entries object: {path}")
    result = {}
    for entry_id, row in entries.items():
        if not isinstance(entry_id, str) or not isinstance(row, dict) or not isinstance(row.get("override"), str) or not row["override"].strip():
            raise ValueError(f"invalid Crystal registry override for {entry_id!r} in {path}")
        result[entry_id] = row["override"]
    return result


def _apply_registry_overrides(
    catalog: dict[str, str], stats: dict, entries: list[IndexedEntry], overrides: dict[str, str],
) -> None:
    """Fill in ids the corpus join left untranslated from ``overrides``."""
    for entry in entries:
        if entry.id in catalog or entry.id not in overrides:
            continue
        catalog[entry.id] = overrides[entry.id]
        stats["translated"] += 1
        stats["no_corpus_entry"] -= 1


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
        rows = []
    else:
        rows = read_corpus_rows(corpus_dir, target_lang=language)
    overrides = load_crystal_registry_overrides(language)
    catalogs: dict[str, dict[str, str]] = {}
    stats: dict[str, dict] = {}
    for name, (entries, prefix) in groups.items():
        catalogs[name], stats[name] = join_by_index(entries, rows, prefix)
        _apply_registry_overrides(catalogs[name], stats[name], entries, overrides)
        stats[name]["fallback_english"] = stats[name]["no_corpus_entry"]
    catalogs["landmarks"], stats["landmarks"] = join_landmarks(landmarks, rows)
    _apply_registry_overrides(catalogs["landmarks"], stats["landmarks"], landmarks, overrides)
    stats["landmarks"]["fallback_english"] = stats["landmarks"]["no_corpus_entry"]
    return catalogs, stats
