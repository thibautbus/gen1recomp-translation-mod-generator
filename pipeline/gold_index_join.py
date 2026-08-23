"""Join GoldSilver corpus rows to Gold's index-keyed registries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .gold_text import normalise, split_lines
from .tokens import corpus_to_engine


@dataclass(frozen=True)
class IndexedEntry:
    id: str
    index: int
    name: str


def parse_indexed_catalog(tsv: str | Path) -> list[IndexedEntry]:
    """Parse an id\\tindex\\tname catalog emitted by the Gold extractor."""
    entries = []
    for line in split_lines(Path(tsv).read_text(encoding="utf-8")):
        if not line:
            continue
        # maxsplit=2: a name legitimately containing a tab must still land
        # entirely in the third field, not raise or get truncated.
        fields = line.split("\t", 2)
        if len(fields) != 3:
            raise ValueError(f"malformed indexed catalog row in {tsv}: expected id\\tindex\\tname, got {line!r}")
        id_, index, name = fields
        if not index.isdigit():
            # A handful of extracted entries carry no index (unused
            # slots); they cannot be joined by index at all, so they are
            # dropped here rather than producing a fake key.
            continue
        entries.append(IndexedEntry(id_, int(index), name))
    return entries


def join_by_index(
    entries: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]], qid_prefix: str,
) -> tuple[dict[str, str], dict]:
    """Join entries by numeric suffix within one exact qid prefix."""
    by_index: dict[int, tuple[str, str]] = {}
    for qid, en, fr in corpus_rows:
        if not qid.startswith(qid_prefix):
            continue
        suffix = qid[len(qid_prefix):]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        if index in by_index and by_index[index] != (en, fr):
            raise ValueError(
                f"duplicate qid index {index} within {qid_prefix!r}: "
                f"{by_index[index]!r} vs {(en, fr)!r}"
            )
        by_index[index] = (en, fr)

    translations: dict[str, str] = {}
    stats = {"total": len(entries), "translated": 0, "no_corpus_entry": 0, "same_as_english": 0}
    for entry in entries:
        row = by_index.get(entry.index)
        if row is None:
            stats["no_corpus_entry"] += 1
            continue
        en, fr = row
        if not fr.strip():
            stats["no_corpus_entry"] += 1
            continue
        translation = corpus_to_engine(fr, bare_dynamic_tokens=True)
        if not translation:
            stats["no_corpus_entry"] += 1
            continue
        translations[entry.id] = translation
        stats["translated"] += 1
        if normalise(fr) == normalise(en):
            stats["same_as_english"] += 1
    return translations, stats


_POKEDEX_ENTRY_SUFFIX = "PokedexEntry"


def _dex_entries_by_species_name(
    corpus_rows: list[tuple[str, str, str]], category: str, label_suffix: str = "",
) -> dict[str, str]:
    """Collect one dex-entry category by normalized species name."""
    by_name: dict[str, str] = {}
    for qid, _en, fr in corpus_rows:
        parts = qid.split(".")
        if len(parts) < 3 or parts[1] != category:
            continue
        label = parts[2]
        if label_suffix:
            if (len(parts) < 4 or parts[3] not in {label_suffix, label_suffix + "^G"} or
                    not label.endswith(_POKEDEX_ENTRY_SUFFIX)):
                continue
        elif len(parts) != 3 or not label.endswith(_POKEDEX_ENTRY_SUFFIX):
            continue
        name = label[: -len(_POKEDEX_ENTRY_SUFFIX)]
        translation = corpus_to_engine(fr, bare_dynamic_tokens=True)
        if translation:
            key = normalise(name)
            if key in by_name and by_name[key] != translation:
                raise ValueError(
                    f"conflicting {category!r} translations for normalised species {key!r}"
                )
            by_name[key] = translation
    return by_name


def join_dex_entries(
    species: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]],
    category: str, label_suffix: str = "",
) -> tuple[dict[str, str], dict]:
    """Join a dex-entry category by normalized species name."""
    by_name = _dex_entries_by_species_name(corpus_rows, category, label_suffix)
    translations: dict[str, str] = {}
    stats = {"total": len(species), "translated": 0, "no_corpus_entry": 0}
    for entry in species:
        fr = by_name.get(normalise(entry.id))
        if fr is None:
            stats["no_corpus_entry"] += 1
            continue
        translations[entry.id] = fr
        stats["translated"] += 1
    return translations, stats


def join_landmarks(
    landmarks: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]],
) -> tuple[dict[str, str], dict]:
    """Join landmark records by normalized name; corpus qids lack indices."""
    by_name: dict[str, str] = {}
    for qid, _en, fr in corpus_rows:
        parts = qid.split(".")
        if len(parts) != 3 or parts[1] != "landmarks" or not parts[2].endswith("Name"):
            continue
        name = parts[2][: -len("Name")]
        translation = corpus_to_engine(fr, bare_dynamic_tokens=True)
        if translation:
            key = normalise(name)
            if key in by_name and by_name[key] != translation:
                raise ValueError(
                    f"conflicting landmark translations for normalised name {key!r}"
                )
            by_name[key] = translation

    id_aliases = {
        "SPECIAL": "SPECIAL_MAP",
        "UNDERGROUND_PATH": "UNDERGROUND",
    }
    translations: dict[str, str] = {}
    stats = {"total": len(landmarks), "translated": 0, "no_corpus_entry": 0}
    for entry in landmarks:
        id_name = entry.id[len("LANDMARK_"):] if entry.id.startswith("LANDMARK_") else entry.id
        corpus_name = id_aliases.get(id_name, id_name)
        fr = by_name.get(normalise(corpus_name))
        if fr is None:
            stats["no_corpus_entry"] += 1
            continue
        translations[entry.id] = fr
        stats["translated"] += 1
    return translations, stats
