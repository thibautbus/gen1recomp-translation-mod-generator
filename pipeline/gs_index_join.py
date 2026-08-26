"""Join GoldSilver corpus rows to Gold's index-keyed registries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .gs_text import normalise, split_lines
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


def _dex_entry_rows(
    corpus_rows: list[tuple[str, str, str]], category: str, label_suffix: str = "",
) -> list[tuple[str, str]]:
    """Yield (normalised_species_name, raw_corpus_text) for a dex-entry
    category's matching qids -- the qid-parsing/filtering shared by every
    dex-entry join below, regardless of whether the row holds one value
    (the kind label) or a multi-page flavor text.
    """
    matches: list[tuple[str, str]] = []
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
        matches.append((normalise(name), fr))
    return matches


def _dex_entries_by_species_name(
    corpus_rows: list[tuple[str, str, str]], category: str, label_suffix: str = "",
) -> dict[str, str]:
    """Collect one dex-entry category by normalized species name."""
    by_name: dict[str, str] = {}
    for key, fr in _dex_entry_rows(corpus_rows, category, label_suffix):
        translation = corpus_to_engine(fr, bare_dynamic_tokens=True)
        if translation:
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


# A placeholder no real corpus text contains, standing in for the literal
# "<NEXT>" substring while corpus_to_engine runs (see _convert_dex_page).
_NEXT_PLACEHOLDER = "\x00__DEX_NEXT__\x00"


def _convert_dex_page(raw: str, category: str, key: str, page_number: int) -> str:
    """Convert one dex-entry flavor-text page, keeping "<NEXT>" literal.

    gen1recomp's PokedexMenu:drawEntryBody splits entry.text/entry.text2 on
    the literal "<NEXT>" substring, not on a real newline: confirmed against
    Rom:readString, which RomExtractorGen2:extractPokedex uses to build
    data/generated/pokedex.lua and which emits charmap tokens (like
    "<NEXT>") as their literal spelling rather than a control character --
    unlike decodeGen2Text, the decoder behind the ordinary "dialogue"
    pointer catalog, which is where corpus_to_engine's "<NEXT>" -> "\\n"
    mapping comes from and is correct there.

    "<NEXT>" is protected before corpus_to_engine runs (rather than
    converting normally and reversing "\\n" -> "<NEXT>" afterward) because
    _CORPUS_EXPANSIONS maps "<LINE>" to the same "\\n" as "<NEXT>": a blind
    reversal would mislabel a genuine "<LINE>" as "<NEXT>" with no way to
    tell them apart. No current GoldSilver dex-entries_gold/_silver row uses
    "<LINE>"/"<PARA>"/"<CONT>" (confirmed directly against the corpus), so a
    stray "\\n" surviving conversion here means one now does, or the corpus
    format has changed -- raised rather than silently mishandled.
    """
    converted = corpus_to_engine(raw.replace("<NEXT>", _NEXT_PLACEHOLDER), bare_dynamic_tokens=True)
    if "\n" in converted:
        raise ValueError(
            f"{category!r} page {page_number} for normalised species {key!r} contains a "
            "line-break token other than <NEXT> (<LINE>/<PARA>/<CONT>) -- "
            "PokedexMenu:drawEntryBody only understands a literal <NEXT>"
        )
    return converted.replace(_NEXT_PLACEHOLDER, "<NEXT>")


def _dex_entry_pages_by_species_name(
    corpus_rows: list[tuple[str, str, str]], category: str,
) -> dict[str, tuple[str, str | None]]:
    """Collect a dex-entry flavor-text category by normalized species name,
    each row's literal "@" page markers split into up to two pages.

    Not every language's corpus preserves both pages: verified directly
    against poke-corpus's GoldSilver collection, every ja-Hrkt row has no
    "@" at all (it ends on "<DEXEND>" instead) and every ko row has exactly
    one (a single page, still "@"-terminated) -- only en/fr/de/es/it split
    cleanly into two. A row with fewer pages ships whatever it has (page2 is
    None rather than the species being dropped entirely); a row claiming
    more than two real pages raises, since silently truncating extra
    content would be a worse failure than a loud one.
    """
    by_name: dict[str, tuple[str, str | None]] = {}
    for key, fr in _dex_entry_rows(corpus_rows, category):
        raw_pages = fr.split("@")
        if raw_pages and raw_pages[-1] == "":
            raw_pages = raw_pages[:-1]
        if not raw_pages:
            continue
        if len(raw_pages) > 2:
            raise ValueError(
                f"unexpected {category!r} page count ({len(raw_pages)}) for "
                f"normalised species {key!r}: expected 1 (no page break preserved, "
                "e.g. ja-Hrkt/ko) or 2 (\"page1@page2@\")"
            )
        converted = [
            _convert_dex_page(page, category, key, number)
            for number, page in enumerate(raw_pages, start=1)
        ]
        if not converted[0]:
            continue
        pages = (converted[0], converted[1] if len(converted) == 2 and converted[1] else None)
        if key in by_name and by_name[key] != pages:
            raise ValueError(
                f"conflicting {category!r} translations for normalised species {key!r}"
            )
        by_name[key] = pages
    return by_name


def join_dex_entries_pages(
    species: list[IndexedEntry], corpus_rows: list[tuple[str, str, str]], category: str,
) -> tuple[dict[str, str], dict[str, str], dict, dict]:
    """Join a dex-entry flavor-text category by normalized species name.

    Returns (page1, page2, page1_stats, page2_stats): page1/page2 are
    {species_id: text}. page2 only covers species whose corpus row actually
    preserved a second page (see _dex_entry_pages_by_species_name) -- for a
    language whose corpus never does (ja-Hrkt, ko), page2 and page2_stats
    end up empty rather than the whole category failing.
    """
    by_name = _dex_entry_pages_by_species_name(corpus_rows, category)
    page1: dict[str, str] = {}
    page2: dict[str, str] = {}
    page1_stats = {"total": len(species), "translated": 0, "no_corpus_entry": 0}
    page2_stats = {"total": len(species), "translated": 0, "no_corpus_entry": 0}
    for entry in species:
        pages = by_name.get(normalise(entry.id))
        if pages is None:
            page1_stats["no_corpus_entry"] += 1
            page2_stats["no_corpus_entry"] += 1
            continue
        page1[entry.id] = pages[0]
        page1_stats["translated"] += 1
        if pages[1] is not None:
            page2[entry.id] = pages[1]
            page2_stats["translated"] += 1
        else:
            page2_stats["no_corpus_entry"] += 1
    return page1, page2, page1_stats, page2_stats


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
