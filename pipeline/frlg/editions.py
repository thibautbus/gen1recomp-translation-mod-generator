"""One FireRed or LeafGreen ROM keys both editions' text.

The two carts lay their script text out at different addresses, but every
address the extractor keys sits on a pret label both editions share
(pokefirered.sym, pokeleafgreen.sym): the other edition's text is the read
edition's, each address key moved to the address its label has there.  The
named text is the same in both carts except the naming screen's default
names, whose tables list other ``gNameChoice_*`` labels in each edition
(``config/frlg/edition_name_choices.json``).  Measured on the US carts, the
text derived this way from either ROM equals the other ROM's own extract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..gen3.join import Gen3Corpus
from ..gen3.text import EncodeError, PretCharmap, corpus_ir, text_key_address
from ..shared.project import resource_root

EDITIONS = ("firered", "leafgreen")
NAME_CHOICES_SCHEMA = "gen1recomp-translation-mods/frlg-edition-name-choices"


def load_name_choices(path: str | Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Each naming-screen table's edition-specific leading labels."""
    path = Path(path) if path is not None else resource_root() / "config" / "frlg" / "edition_name_choices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != NAME_CHOICES_SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported FireRed/LeafGreen name choices: {path}")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise ValueError(f"FireRed/LeafGreen name choices without tables: {path}")
    for name, editions in tables.items():
        if set(editions) != set(EDITIONS) or len(editions["firered"]) != len(editions["leafgreen"]):
            raise ValueError(f"name choice table {name!r} needs as many FireRed as LeafGreen labels")
    return tables


def other_edition(edition: str) -> str:
    if edition not in EDITIONS:
        raise ValueError(f"unsupported FireRed/LeafGreen edition: {edition!r}")
    return EDITIONS[1 - EDITIONS.index(edition)]


def _labels_to_addresses(symbols: Mapping[int, list[str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for address, labels in symbols.items():
        for label in labels:
            out.setdefault(label, address)
    return out


def _label_ir(label: str, read_slots: Mapping[str, list[dict]], corpus: Gen3Corpus,
              charmap: PretCharmap) -> list[dict] | None:
    """A default name's text: the read cart's own when one of its slots holds
    the label, else the label's English corpus row."""
    if label in read_slots:
        return read_slots[label]
    for index in corpus.by_label.get(label, ()):
        try:
            return corpus_ir(corpus.english[index], charmap)
        except EncodeError:
            continue
    return None


def derive_edition_text(
    text: Mapping[str, list[dict]],
    source_symbols: Mapping[int, list[str]],
    target_symbols: Mapping[int, list[str]],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
    *,
    source: str,
    name_choices: Mapping[str, Mapping[str, list[str]]] | None = None,
) -> dict[str, list[dict]]:
    """The other edition's text, keyed as its own extract would key it.

    Every key must land: an address whose labels the other cart lacks, two
    addresses landing on one, or a default name with neither a read slot nor
    a corpus row raises (a pret symbols pin bump that renamed a label would
    otherwise drop the other edition's lines silently)."""
    target = other_edition(source)
    tables = name_choices if name_choices is not None else load_name_choices()
    target_addresses = _labels_to_addresses(target_symbols)
    out: dict[str, list[dict]] = {}
    dropped: list[str] = []
    for key, ir in text.items():
        address = text_key_address(key)
        if address is None:
            out[key] = ir
            continue
        for label in source_symbols.get(address, ()):
            if label in target_addresses:
                moved = f"g3:{target_addresses[label]:08x}"
                if moved in out:
                    raise ValueError(f"{key} and another {source} key both land on {target} {moved}")
                out[moved] = ir
                break
        else:
            dropped.append(key)
    if dropped:
        raise ValueError(f"{len(dropped)} {source} text keys have no pret label in {target} "
                         f"(first: {', '.join(sorted(dropped)[:3])})")
    read_slots: dict[str, list[dict]] = {}
    for table, editions in tables.items():
        for index, label in enumerate(editions[source]):
            key = f"{table}[{index}]"
            if key in text:
                read_slots.setdefault(label, text[key])
    for table, editions in tables.items():
        for index, label in enumerate(editions[target]):
            key = f"{table}[{index}]"
            if key not in out:
                continue
            ir = _label_ir(label, read_slots, corpus, charmap)
            if ir is None:
                raise ValueError(f"no {target} text for {key} ({label}): neither the read cart nor the corpus has it")
            out[key] = ir
    return out
