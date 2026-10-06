"""Emerald, the companion edition of the rse release: its join.

Emerald runs on the same game3 runtime as Ruby and Sapphire and goes through
the same joins (pipeline.gen3), with its own family
(pipeline.gen3.family.EMERALD): the Emerald PokeCorpus collection, pret's
pokeemerald symbol table and charmap, the runtime's ``rse`` text dialect,
and its own reviewed configuration (``config/rse/emerald_*.json``,
``overrides/<language>/rse/emerald_*.json``), the way Crystal is Gold and
Silver's companion (pipeline/gsc/crystal_mod.py).  Its European trainer
names and classes are pipeline/rse/european.py's.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..gen3.family import EMERALD
from ..gen3.join import (
    dialogue_catalog,
    join_gen3_dialogue,
    join_gen3_engine_strings,
    join_indexed_catalog,
    join_item_descriptions,
    join_trainer_class_names,
    load_dialogue_decisions,
    load_dialogue_overrides,
    load_engine_scope,
    load_gen3_corpus,
    registry_ids,
)
from ..gen3.mod import dialogue_label_rows, rom_label_strings
from ..gen3.text import load_charmap, load_symbols
from ..shared.builder import BuildError
from ..shared.corpus import canonical_language
from ..shared.dependencies import DependencyError, fetch_files
from .european import apply_european_trainer_text
from .join import ENGLISH_LOOKUP_SITES, numbered as _numbered, text_aliases

# pret's symbol table and charmap for Emerald, in [pret.emerald_symbols] and
# [pret.emerald_charmap].
EMERALD_SYMBOL_FILE = "pokeemerald.sym"


def prepare_emerald_pret_inputs(workspace: str | Path, config: Mapping) -> tuple[Path, Path]:
    """Download the pinned pret Emerald symbol table and charmap."""
    root = Path(workspace) / "dependencies" / "pret"
    pret = config.get("pret") or {}
    folders = {}
    for name in ("emerald_symbols", "emerald_charmap"):
        section = pret.get(name) or {}
        try:
            folders[name] = fetch_files(
                str(section["archive_base_url"]), dict(section["archive_files"]),
                root / name, revision=str(section.get("revision", "")),
            )
        except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
            raise BuildError(f"Unable to download pinned pret {name}: {error}") from error
    return folders["emerald_symbols"] / EMERALD_SYMBOL_FILE, folders["emerald_charmap"] / "charmap.txt"


def _description_qids(items: Mapping[int, Mapping], symbols: Mapping[int, list[str]],
                      corpus) -> dict[int, list[str]]:
    """Each item's own description rows, named after the string its
    description pointer reaches (sMasterBallDesc)."""
    out: dict[int, list[str]] = {}
    for number, row in items.items():
        pointer = row.get("description_pointer") if isinstance(row, Mapping) else None
        if not isinstance(pointer, int):
            continue
        qids = [corpus.qids[index] for label in symbols.get(pointer, ())
                for index in corpus.by_label.get(label, ())
                if corpus.qids[index].startswith(f"{EMERALD.qid_prefix}common.item_descriptions.")]
        if qids:
            out[number] = qids
    return out


# ---------------------------------------------------------------- join


def join_emerald(
    extracted: str | Path,
    corpus_dir: str | Path,
    language: str,
    symbols_path: str | Path,
    charmap_path: str | Path,
    gen1recomp: str | Path | None = None,
    luajit: str | None = None,
) -> dict:
    """Run every Emerald join for one language; return catalogs and stats."""
    extracted = Path(extracted)
    language = canonical_language(language)
    charmap = load_charmap(charmap_path, EMERALD.dialect)
    symbols = load_symbols(symbols_path)
    corpus = load_gen3_corpus(corpus_dir, language, EMERALD)

    text = json.loads((extracted / "rse_text.json").read_text(encoding="utf-8"))
    entries, dialogue_stats = join_gen3_dialogue(
        text, corpus, symbols, charmap,
        overrides=load_dialogue_overrides(language, EMERALD),
        decisions=load_dialogue_decisions(EMERALD),
        aliases=text_aliases(extracted, symbols),
    )

    species = _numbered(extracted / "rse_species.json")
    moves = _numbered(extracted / "rse_moves.json")
    items = _numbered(extracted / "rse_items.json")
    trainers = _numbered(extracted / "rse_trainers.json")

    species_ids = registry_ids(species)
    item_names = {number: row.get("name") for number, row in items.items()}
    item_ids = registry_ids(item_names)
    trainer_ids = {number: str(number) for number in trainers}
    prefix = f"{EMERALD.qid_prefix}common."
    results = {
        "species_names": join_indexed_catalog(
            species, species_ids, corpus, prefix + "species_names.gSpeciesNames.", charmap),
        "move_names": join_indexed_catalog(
            moves, registry_ids(moves), corpus, prefix + "move_names.gMoveNames.", charmap),
        "item_names": join_indexed_catalog(item_names, item_ids, corpus, prefix + "items.gItems.", charmap),
        "item_descriptions": join_item_descriptions(
            items, item_ids, corpus, charmap, own_qids=_description_qids(items, symbols, corpus)),
        "trainer_names": join_indexed_catalog(
            {number: row.get("name") for number, row in trainers.items()}, trainer_ids, corpus,
            prefix + "trainers.gTrainers.", charmap),
        "trainer_class_names": join_trainer_class_names(trainers, trainer_ids, corpus, charmap),
    }
    european = apply_european_trainer_text(results["trainer_names"].values, results["trainer_class_names"].values,
                                           trainers, trainer_ids, corpus, charmap)
    scope = load_engine_scope(EMERALD)
    strings, engine_stats = join_gen3_engine_strings(scope, corpus, charmap)
    # the values themselves, before the ROM-label migration below, which
    # Ruby and Sapphire take for the screens their carts never had (a value
    # the language keeps in English included)
    engine_values = {**{key: key for key, origin in engine_stats["details"].items()
                        if origin == "same_as_english"}, **strings}
    by_english: dict[str, str] = {}
    if gen1recomp is not None:
        strings, by_english = rom_label_strings(strings, gen1recomp, scope, luajit, ENGLISH_LOOKUP_SITES,
                                                 dialogue_label_rows(entries))
    catalogs = {name: result.values for name, result in results.items()}
    catalogs["strings"] = strings
    if by_english:
        catalogs["strings_by_english"] = by_english
    return {
        "entries": entries,
        "dialogue": dialogue_catalog(entries),
        "dialogue_stats": dialogue_stats,
        "catalogs": catalogs,
        "catalog_stats": {name: result.summary() for name, result in results.items()},
        "catalog_issues": {name: result.issues for name, result in results.items()},
        "european_trainer_text": dict(european),
        "engine_stats": engine_stats,
        "engine_values": engine_values,
        "numbers": {"species": species_ids, "items": item_ids, "moves": registry_ids(moves)},
        "scope": scope,
    }
