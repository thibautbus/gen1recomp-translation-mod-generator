"""Trainer text the European Emerald carts build in their own code.

The French, Italian and Spanish carts name a few trainers with words the US
code never prints, and French and Spanish put a Team Aqua or Team Magma
grunt's name before its class (pret pokeemerald, branch ``multi-language``,
``#if EUROPE``). The runtime runs the US code, but it reads each trainer's
name and class from the mod by trainer id, so the mod ships them as the cart
prints them: ``config/rse/european_trainer_text.json`` lists the reviewed
words and where the carts take them from.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Mapping, MutableMapping

from ..gen3.join import Gen3Corpus, _plain
from ..gen3.text import EncodeError, PretCharmap

CONFIG = Path(__file__).resolve().parents[2] / "config" / "rse" / "european_trainer_text.json"
SCHEMA = "gen1recomp-translation-mods/rse-european-trainer-text"


def load_european_trainer_text(path: str | Path = CONFIG) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported European trainer text: {path}")
    return data


def _resolved(corpus: Gen3Corpus, qid: str, english: str, charmap: PretCharmap) -> str | None:
    """The corpus row's translation of ``english``, which may be ``english``
    itself; None when the row is missing, another string or unencodable."""
    row = corpus.row(qid)
    if row is None or not row[1]:
        return None
    try:
        if _plain(row[0], charmap, "en") != english:
            return None
        return _plain(row[1], charmap, corpus.language)
    except EncodeError:
        return None


def _applies(variant: Mapping, row: Mapping) -> bool:
    if row.get("class") not in variant["classes"]:
        return False
    when = variant["when"]
    # src/battle_message.c:4329 passes encounterMusic_gender & 0x7F, the
    # music alone: any music but TRAINER_ENCOUNTER_MUSIC_MALE (0) counts.
    if when == "encounter_music":
        if not isinstance(row.get("encounterMusic"), int):
            raise ValueError(f"trainer {row.get('name')!r} has no encounterMusic; re-extract the Emerald ROM "
                             "(tools/rse/extract.lua exports it)")
        return row["encounterMusic"] != 0
    return row.get("name") == when["trainer_name"]


def apply_european_trainer_text(
    trainer_names: MutableMapping[str, str],
    trainer_class_names: MutableMapping[str, str],
    trainers: Mapping[int, Mapping],
    trainer_ids: Mapping[int, str],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
    config: Mapping | None = None,
) -> Counter:
    """Rewrite the joined trainer catalogs in place; return what changed."""
    config = config or load_european_trainer_text()
    language = corpus.language
    prefix = corpus.family.qid_prefix + "common."
    stats: Counter = Counter()
    for number, row in sorted(trainers.items()):
        id_ = trainer_ids.get(number)
        if not id_:
            continue
        for variant in config["class_variants"]:
            word = variant["values"].get(language)
            if word is None or not _applies(variant, row):
                continue
            value = _plain(word, charmap, language)
            current = trainer_class_names.get(id_, row.get("className"))
            if value != current:
                trainer_class_names[id_] = value
                stats[variant["label"]] += 1
        swap = config["class_name_swap"]
        if language in swap["languages"] and row.get("class") in swap["classes"]:
            class_name = trainer_class_names.get(id_) or _resolved(
                corpus, f"{prefix}trainer_class_names.gTrainerClassNames.{row['class']}", row.get("className"), charmap)
            name = trainer_names.get(id_) or _resolved(
                corpus, f"{prefix}trainers.gTrainers.{number}", row.get("name"), charmap)
            if class_name and name:
                trainer_class_names[id_], trainer_names[id_] = name, class_name
                stats["class_name_swap"] += 1
    return stats
