"""Join each Gen 2 trainer's own name (JOEY, the class's first YOUNGSTER) to
the corpus.

tools/gsc/extract.lua writes gs_trainer_names.tsv from the extractor's own
trainers table: class id, member number (the ``entry.trainers[member]``
index src/world/gen2/Trainers.lua looks a trainer up by) and English name.
poke-corpus keeps the same rows as ``<prefix>.parties.<Class>Group._<member>``
(``gs.parties.YoungsterGroup._1`` = JOEY, GASPARD in French).  A row is only
joined when the corpus's English name is the extracted one, so a roster that
drifts from the corpus never gets another trainer's name.

Catalog ids are ``CLASS#member#ENGLISH``: the generated mod renames a roster
row only while its name is still that English one.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..shared.tokens import corpus_to_engine

# Classes whose corpus group is spelled differently from the extracted class
# id (pokegold's parties.asm group labels versus trainer_constants.asm).
_GROUP_ALIASES = {
    "BLACKBELT_T": "BLACKBELT",
    "PSYCHIC_T": "PSYCHIC",
    "CAL": "PKMNTRAINER",
}

# pokegold's PokemonProfGroup is empty, so the extractor reads the next
# group's first trainer (WILL) for it.  No script ever loads that member.
UNREACHABLE_CLASSES = frozenset({"POKEMON_PROF"})

_PARTY_QID = re.compile(r"(?:gs|c)\.parties\.(\w+)Group\._(\d+)")


def _normalise(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def parse_trainer_names(path: str | Path) -> list[tuple[str, int, str]]:
    """(class id, member number, English name) rows from gs_trainer_names.tsv."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0] or not parts[1].isdigit():
            raise ValueError(f"malformed gs_trainer_names.tsv row: {line!r}")
        rows.append((parts[0], int(parts[1]), parts[2]))
    return rows


def _name(text: str) -> str:
    return corpus_to_engine(text, bare_dynamic_tokens=True).replace("@", "").strip()


def trainer_name_catalog(
    rows: list[tuple[str, int, str]],
    corpus_rows: list[tuple[str, str, str]],
) -> tuple[dict[str, str], dict]:
    """Return ({"CLASS#member#ENGLISH": localized}, stats).

    A localized name identical to the English one counts as translated but is
    not emitted, since renaming a row to its own name is a no-op.
    """
    corpus: dict[tuple[str, int], tuple[str, str, str]] = {}
    for qid, english, target in corpus_rows:
        match = _PARTY_QID.fullmatch(qid)
        if match:
            corpus[(_normalise(match.group(1)), int(match.group(2)))] = (qid, english, target)
    catalog: dict[str, str] = {}
    translated = same_as_english = 0
    backlog = []
    scoped = [row for row in rows if row[0] not in UNREACHABLE_CLASSES]
    for class_id, member, english in scoped:
        group = _GROUP_ALIASES.get(class_id, _normalise(class_id))
        found = corpus.get((group, member))
        if found is None or _name(found[1]) != english or not _name(found[2]):
            backlog.append({
                "id": f"{class_id}#{member}#{english}",
                "reason": "missing-corpus" if found is None or not _name(found[2]) else "english-mismatch",
            })
            continue
        value = _name(found[2])
        translated += 1
        if value == english:
            same_as_english += 1
        else:
            catalog[f"{class_id}#{member}#{english}"] = value
    total = len(scoped)
    return catalog, {
        "total": total,
        "translated": translated,
        "no_corpus_entry": total - translated,
        "fallback_english": total - translated,
        "same_as_english": same_as_english,
        "excluded_unreachable": len(rows) - total,
        "scope": "each named trainer of every class, joined by class group and member number with a matching English name",
        "backlog": backlog,
    }
