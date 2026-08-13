"""Join the GoldSilver corpus to Gold's index-keyed catalogs: species,
moves, items, trainer classes, and species dex entries.

This is where the 2469 corpus entries with a French distinct from their
English live: they never join by ROM pointer at all. Their join is by
registry index instead, a different mechanic from pipeline/gold_join.py's
normalised-English pointer join.

Unlike pipeline/gold_join.py's pointer join, every join here is a plain
dict lookup by a numeric key (dex number, move/item index) both sides
already agree on, or -- for dex entries only, where the corpus has no
numeric key -- a normalised species-name match (verified against the
real data: pipeline/gold_text.py's normalise() turns both
"FARFETCH_D" and the qid-derived "FarfetchD" into "farfetchd").

Patch call shapes are the SAME as RBY's shared-path registries --
verified against src/mods/Schemas.lua's own comment on R.trainers:
"the registry keeps the Gen 1 call shape -- mod.content.trainers:patch
("BEAUTY", { baseMoney = 99 }) -- and only the one level of indirection
to `.classes` is new" (handled internally by gen2BaseAt/gen2Write, not by
the mod author) -- so nothing here needs a Gen-2-shaped patch table.
"""
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
    """Parse an id\\tindex\\tname TSV (tools/gold_extract.lua's
    gold_species.tsv/gold_moves.tsv/gold_items.tsv/gold_trainer_classes.tsv).
    """
    entries = []
    for line in split_lines(Path(tsv).read_text(encoding="utf-8")):
        if not line:
            continue
        id_, index, name = line.split("\t")
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
    """{id: translation} for every entry whose index has a non-empty
    corpus translation at that qid.

    ``qid_prefix`` (e.g. "gs.names.PokemonNames.") is required, not
    inferred: many unrelated registries share the same bare numeric
    suffix (PokemonNames.29, TrainerClassNames.29, DecorationNames.29...
    are all real, different qids), so building one index without first
    filtering by qid produces silently wrong matches -- caught while
    building this join, when TrainerClassNames.29 ("BEAUTY" -> "CANON")
    was overwritten by a same-numbered row from a different category.
    """
    by_index: dict[int, tuple[str, str]] = {}
    for qid, en, fr in corpus_rows:
        if not qid.startswith(qid_prefix):
            continue
        suffix = qid[len(qid_prefix):]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        if index in by_index and by_index[index] != (en, fr):
            # Same class of bug as the cross-category collision this
            # function's own qid_prefix filter was added to catch: within
            # one category, two different qids sharing an index number
            # would otherwise silently pick whichever the dict iteration
            # order happened to visit last.
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
        translations[entry.id] = corpus_to_engine(fr)
        stats["translated"] += 1
        if normalise(fr) == normalise(en):
            stats["same_as_english"] += 1
    return translations, stats


_POKEDEX_ENTRY_SUFFIX = "PokedexEntry"


def _dex_entries_by_species_name(
    corpus_rows: list[tuple[str, str, str]], category: str, label_suffix: str = "",
) -> dict[str, str]:
    """{normalised species name: translation} for one dex-entry qid
    category ("dex_entries" for the .Species kind line -- qid
    "gs.dex_entries.BulbasaurPokedexEntry.Species" -- or "dex_entries_gold"
    for the Gold-version flavor text -- qid
    "gs.dex_entries_gold.BulbasaurPokedexEntry", no further suffix).
    """
    by_name: dict[str, str] = {}
    for qid, _en, fr in corpus_rows:
        parts = qid.split(".")
        if len(parts) < 3 or parts[1] != category:
            continue
        label = parts[2]
        if label_suffix:
            if len(parts) < 4 or parts[3] != label_suffix or not label.endswith(_POKEDEX_ENTRY_SUFFIX):
                continue
        elif len(parts) != 3 or not label.endswith(_POKEDEX_ENTRY_SUFFIX):
            continue
        name = label[: -len(_POKEDEX_ENTRY_SUFFIX)]
        if fr.strip():
            by_name[normalise(name)] = fr
    return by_name


def join_dex_entries(
    species: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]],
    category: str, label_suffix: str = "",
) -> tuple[dict[str, str], dict]:
    """{species id: translation} for a dex-entry qid category, matched by
    normalised species name rather than by index (the corpus has none
    here). ``category``/``label_suffix`` select which one -- see
    _dex_entries_by_species_name.
    """
    by_name = _dex_entries_by_species_name(corpus_rows, category, label_suffix)
    translations: dict[str, str] = {}
    stats = {"total": len(species), "translated": 0, "no_corpus_entry": 0}
    for entry in species:
        fr = by_name.get(normalise(entry.id))
        if fr is None:
            stats["no_corpus_entry"] += 1
            continue
        translations[entry.id] = corpus_to_engine(fr)
        stats["translated"] += 1
    return translations, stats


def join_landmarks(
    landmarks: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]],
) -> tuple[dict[str, str], dict]:
    """{landmark id: translation}, matched by normalised name rather than
    index: the corpus's "gs.landmarks.<Name>Name" qids carry no number,
    only a name derived from the same LANDMARK_* constant (verified:
    "AzaleaTownName" <-> "LANDMARK_AZALEA_TOWN" both normalise to
    "azaleatown"). data.gen2Landmarks.landmarks is Schemas.GEN2's routed
    target for the `landmarks` registry; despite that indirection the
    registry stays record/patch semantics
    (mod.content.landmarks:patch("LANDMARK_ROUTE_29", {...}) --
    Schemas.lua's own R.landmarks example), so nothing here is more
    exotic than the dex-entries join above.
    """
    by_name: dict[str, str] = {}
    for qid, _en, fr in corpus_rows:
        parts = qid.split(".")
        if len(parts) != 3 or parts[1] != "landmarks" or not parts[2].endswith("Name"):
            continue
        name = parts[2][: -len("Name")]
        if fr.strip():
            by_name[normalise(name)] = fr

    translations: dict[str, str] = {}
    stats = {"total": len(landmarks), "translated": 0, "no_corpus_entry": 0}
    for entry in landmarks:
        id_name = entry.id[len("LANDMARK_"):] if entry.id.startswith("LANDMARK_") else entry.id
        fr = by_name.get(normalise(id_name))
        if fr is None:
            stats["no_corpus_entry"] += 1
            continue
        translations[entry.id] = corpus_to_engine(fr)
        stats["translated"] += 1
    return translations, stats
