"""Ruby and Sapphire joins: one cart read, every cart's text keyed.

gen1recomp keys a script message by its ROM address (``g3:<address>``),
and the English Ruby and Sapphire carts lay their text out in four ways:
Ruby and Sapphire differ, and so do revision 1.0 and revisions 1.1/1.2 of
each (pret's rev1 and rev2 symbol tables are byte-identical).  A build reads
one of those carts; the others' keys come from pret's own symbol tables,
the way pokeruby builds every cart from one source:

* every text the extract keys by address sits exactly on a pret label, so
  the same label in another cart's symbol table is the same text there;
* the few texts only the other edition prints (its own Team Magma or Team
  Aqua scenes) have no address in the cart that was read, and are taken
  from their PokeCorpus row, whose English is the other cart's text;
* the Pokédex entries, which differ between the editions under one label,
  are the other edition's corpus rows (``pokedex_entries^S`` for Sapphire).

Each of the four layouts is then joined like a cart of its own
(pipeline.gen3.join), so a derived layout goes through every check a read
one does.  The named text (labels, tables and battle strings, the same keys
in every cart) is one layer per edition; the catalogs and engine strings,
which the two editions share, are one.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Iterable, Mapping

from ..gen3.family import RUBY_SAPPHIRE
from ..gen3.join import (
    Gen3Corpus,
    dialogue_catalog,
    dialogue_stats,
    edition_rows,
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
from ..gen3.text import EncodeError, PretCharmap, corpus_ir, load_charmap, load_symbols, text_key_address
from ..shared.builder import BuildError
from ..shared.corpus import canonical_language
from ..shared.dependencies import DependencyError, fetch_files
from ..shared.roms import RsRevision

# The four text layouts, by (edition, layout), and the pret symbol table
# that names each one's addresses ([pret.ruby_sapphire_symbols]).
RS_SYMBOL_FILES: Mapping[tuple[str, str], str] = {
    ("ruby", "1_0"): "pokeruby.sym",
    ("ruby", "1_1"): "pokeruby_rev1.sym",
    ("sapphire", "1_0"): "pokesapphire.sym",
    ("sapphire", "1_1"): "pokesapphire_rev1.sym",
}
RS_LAYOUTS: tuple[tuple[str, str], ...] = tuple(RS_SYMBOL_FILES)

# The PokeCorpus groups of script text (a label the other edition's scripts
# print has its row there).
_SCRIPT_QIDS = f"{RUBY_SAPPHIRE.qid_prefix}script."

# The places where the game3 runtime reads a string by its English text and
# by nothing else, once gen1recomp's Emerald display hooks route them through
# Strings(): the ability names, the summary's descriptions and contest texts,
# the map sections and the Pokédex entries (Ruby and Sapphire run the same
# screens).  Their entries are kept under their English (rom_label_strings)
# in a file of their own.
ENGLISH_LOOKUP_SITES = (
    "src/core/game3/battle/abilities.lua",
    "src/core/game3/summary_data.lua",
    "src/ui/game3/rse/mapsec.lua",
    "src/ui/game3/rse/pokedex.lua",
)


# The charmap Ruby and Sapphire are encoded with ([pret.ruby_sapphire_charmap]).
RS_CHARMAP_FILE = "charmap.txt"


def prepare_rs_pret_inputs(workspace: str | Path, config: Mapping) -> tuple[Path, Path]:
    """Download the pinned pokeruby symbol tables and charmap; return the
    symbol tables' directory and the charmap."""
    root = Path(workspace) / "dependencies" / "pret"
    pret = config.get("pret") or {}
    folders = {}
    for name in ("ruby_sapphire_symbols", "ruby_sapphire_charmap"):
        section = pret.get(name) or {}
        try:
            folders[name] = fetch_files(
                str(section["archive_base_url"]), dict(section["archive_files"]),
                root / name, revision=str(section.get("revision", "")),
            )
        except (DependencyError, KeyError, TypeError, ValueError, OSError) as error:
            raise BuildError(f"Unable to download pinned pret {name}: {error}") from error
    return folders["ruby_sapphire_symbols"], folders["ruby_sapphire_charmap"] / RS_CHARMAP_FILE


def layer_name(edition: str, layout: str | None = None) -> str:
    """The mod's directory for one edition's named text (``ruby``) or one
    layout's script text (``ruby_1_1``)."""
    return edition if layout is None else f"{edition}_{layout}"


def _labels_to_addresses(symbols: Mapping[int, list[str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for address, labels in symbols.items():
        for label in labels:
            out.setdefault(label, address)
    return out


def _edition_row(corpus: Gen3Corpus, labels: Iterable[str], edition: str) -> int | None:
    """The row one edition owns for one of ``labels`` (pokedex_entries^S,
    Text_Version^S), if the collection splits the label between the
    editions."""
    for label in labels:
        rows = corpus.by_label.get(label, ())
        own = edition_rows(rows, corpus, corpus.family.edition_marker(edition))
        if len(own) < len(rows) and own:
            return own[0]
    return None


def japanese_single_page_entries(corpus: Gen3Corpus) -> Gen3Corpus:
    """The Japanese carts print a Pokédex entry on one page, where the
    English carts split it in two (DexDescription_<Species>_1 and _2): the
    collection's Japanese second pages are [NULL] and the whole entry is the
    first.  Each such page is one the cart leaves blank, so the second page
    shows nothing rather than its English half."""
    if corpus.language != "ja-Hrkt" or not corpus.missing:
        return corpus
    target, missing = list(corpus.target), list(corpus.missing)
    for index, qid in enumerate(corpus.qids):
        label = qid.rsplit(".", 1)[-1]
        if not (missing[index] and label.startswith("DexDescription_") and label.endswith("_2")):
            continue
        first = corpus.by_qid.get(qid[:-2] + "_1")
        if first is not None and not missing[first] and target[first]:
            target[index], missing[index] = "", False
    return dataclasses.replace(corpus, target=tuple(target), missing=tuple(missing))


def derive_layout_text(
    text: Mapping[str, list[dict]],
    source_symbols: Mapping[int, list[str]],
    target_symbols: Mapping[int, list[str]],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
    *,
    source_edition: str,
    target_edition: str,
    aliases: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, list[dict]]:
    """Another cart's text, keyed as that cart's extract would key it.

    An address key moves to the address its pret label has in the target
    cart; a named key stays.  For the other edition, the labels the
    collection splits between the editions read that edition's English
    (other_edition_script_text adds the scenes only its scripts print).
    """
    target_addresses = _labels_to_addresses(target_symbols)
    out: dict[str, list[dict]] = {}
    for key, ir in text.items():
        address = text_key_address(key)
        if address is None:
            out[key] = ir
            continue
        for label in source_symbols.get(address, ()):
            if label in target_addresses:
                out[f"g3:{target_addresses[label]:08x}"] = ir
                break
    if target_edition == source_edition:
        return out
    for key in list(out):
        if text_key_address(key) is not None:
            continue
        index = _edition_row(corpus, (key, *(aliases or {}).get(key, ())), target_edition)
        if index is None:
            continue
        try:
            out[key] = corpus_ir(corpus.english[index], charmap)
        except EncodeError:
            continue
    return out


def other_edition_script_text(
    text: Mapping[str, list[dict]],
    source_symbols: Mapping[int, list[str]],
    target_symbols: Mapping[int, list[str]],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
) -> dict[str, list[dict]]:
    """The script text the cart that was read never keys by address, at the
    other edition's addresses, read from its corpus rows: the scenes only
    that edition's scripts print (pokeruby's ``.ifdef SAPPHIRE`` blocks).
    Without that edition's script graph, every such corpus row is placed;
    the rows no script of that edition prints sit at an address its runtime
    never looks up."""
    target_addresses = _labels_to_addresses(target_symbols)
    read_labels = {label for key in text if text_key_address(key) is not None
                   for label in source_symbols.get(text_key_address(key), ())}
    out: dict[str, list[dict]] = {}
    for index, qid in enumerate(corpus.qids):
        label = qid.rsplit(".", 1)[-1]
        if not qid.startswith(_SCRIPT_QIDS) or label in read_labels or label not in target_addresses:
            continue
        key = f"g3:{target_addresses[label]:08x}"
        if key in out:
            continue
        try:
            out[key] = corpus_ir(corpus.english[index], charmap)
        except EncodeError:
            continue
    return out


def keyed_by_layout(entries: Mapping[str, object], symbols: Mapping[int, list[str]],
                    aliases: Mapping[str, Iterable[str]] | None = None) -> dict[str, object]:
    """Reviewed entries written against a label (a decision, an override),
    also keyed by the address that label has in one layout and by the
    pointer-table slots that reach it (``aliases``: STRINGID_USEDMOVE for
    BattleText_OpponentUsedMove)."""
    addresses = _labels_to_addresses(symbols)
    out = dict(entries)
    for key, value in entries.items():
        if key in addresses:
            out.setdefault(f"g3:{addresses[key]:08x}", value)
    for key, labels in (aliases or {}).items():
        for label in labels:
            if label in entries:
                out.setdefault(key, entries[label])
                break
    return out


def _plain(ir: Iterable[Mapping]) -> str:
    """What main.lua compares a guard with: the IR's text segments."""
    return "".join(segment.get("s", "") for segment in ir if segment.get("t") == "text")


def layout_guards(text: Mapping[str, list[dict]], read_symbols: Mapping[int, list[str]],
                  tables: Mapping[tuple[str, str], Mapping[int, list[str]]],
                  skip_labels: Iterable[str] = ()) -> dict[str, list[dict]]:
    """For each edition, how main.lua tells revision 1.0 from 1.1/1.2: an
    address where the two layouts start two different texts, and what each
    one's cart holds there (``mod.content.text:get`` reads the extract's
    English).  Only texts the cart that was read prints are used, so the
    address holds a text in every revision, and no text a revision rewords
    (``skip_labels``, the reviewed revision decisions)."""
    skip = set(skip_labels)
    by_label: dict[str, str] = {}
    for key, ir in text.items():
        address = text_key_address(key)
        for label in read_symbols.get(address, ()) if address is not None else ():
            if label not in skip:
                by_label.setdefault(label, _plain(ir))
    guards: dict[str, list[dict]] = {}
    for edition in ("ruby", "sapphire"):
        old, new = tables[(edition, "1_0")], tables[(edition, "1_1")]
        new_addresses = _labels_to_addresses(new)
        found = None
        for label in sorted(by_label, key=lambda name: new_addresses.get(name, 1 << 32)):
            address = new_addresses.get(label)
            if address is None:
                continue
            for other in old.get(address, ()):
                if other in by_label and other != label and by_label[other] != by_label[label] \
                        and by_label[label] and by_label[other]:
                    found = (address, label, other)
                    break
            if found:
                break
        if found is None:
            raise ValueError(f"no address tells {edition} 1.0 from 1.1/1.2 apart")
        address, label, other = found
        guards[edition] = [
            {"dir": layer_name(edition, "1_1"), "key": f"g3:{address:08x}", "text": by_label[label]},
            {"dir": layer_name(edition, "1_0"), "key": f"g3:{address:08x}", "text": by_label[other]},
        ]
    return guards


def numbered(path: Path) -> dict[int, object]:
    return {int(key): value for key, value in json.loads(path.read_text(encoding="utf-8")).items()}


def _description_qids(items: Mapping[int, Mapping], symbols: Mapping[int, list[str]],
                      corpus: Gen3Corpus) -> dict[int, list[str]]:
    """Each item's own description rows, named after the string its
    description pointer reaches (gItemDescription_MasterBall)."""
    prefix = f"{RUBY_SAPPHIRE.qid_prefix}common.item_descriptions."
    out: dict[int, list[str]] = {}
    for number, row in items.items():
        pointer = row.get("description_pointer") if isinstance(row, Mapping) else None
        if not isinstance(pointer, int):
            continue
        qids = [corpus.qids[index] for label in symbols.get(pointer, ())
                for index in corpus.by_label.get(label, ()) if corpus.qids[index].startswith(prefix)]
        if qids:
            out[number] = qids
    return out


def text_aliases(extracted: Path, symbols: Mapping[int, list[str]]) -> dict[str, tuple[str, ...]]:
    """The pret symbols each pointer-table key reaches (rse_text_pointers.json)."""
    pointers = json.loads((Path(extracted) / "rse_text_pointers.json").read_text(encoding="utf-8"))
    return {key: tuple(symbols.get(int(address), ())) for key, address in pointers.items()
            if symbols.get(int(address))}


def join_rs(
    extracted: str | Path,
    corpus_dir: str | Path,
    language: str,
    symbols_dir: str | Path,
    charmap_path: str | Path,
    source: RsRevision,
    gen1recomp: str | Path | None = None,
    luajit: str | None = None,
    companion_strings: Mapping[str, str] | None = None,
) -> dict:
    """Run every Ruby/Sapphire join for one language from one cart's
    extract; return the layers, catalogs and stats.  ``companion_strings``
    are Emerald's engine strings, before the ROM-label migration
    (join_gen3_engine_strings' ``companion``)."""
    extracted = Path(extracted)
    language = canonical_language(language)
    family = RUBY_SAPPHIRE
    charmap = load_charmap(charmap_path, family.dialect)
    corpus = japanese_single_page_entries(load_gen3_corpus(corpus_dir, language, family))
    tables = {layout: load_symbols(Path(symbols_dir) / name) for layout, name in RS_SYMBOL_FILES.items()}
    read = (source.edition, source.layout)
    text = json.loads((extracted / "rse_text.json").read_text(encoding="utf-8"))
    aliases = text_aliases(extracted, tables[read])
    overrides = load_dialogue_overrides(language, family)

    layouts: dict[tuple[str, str], dict] = {}
    for layout in RS_LAYOUTS:
        edition = layout[0]
        layout_text = text if layout == read else derive_layout_text(
            text, tables[read], tables[layout], corpus, charmap,
            source_edition=source.edition, target_edition=edition, aliases=aliases)
        exclusive: dict[str, list[dict]] = {}
        if edition != source.edition:
            exclusive = {key: ir for key, ir in other_edition_script_text(
                text, tables[read], tables[layout], corpus, charmap).items() if key not in layout_text}
            layout_text = {**layout_text, **exclusive}
        entries, _stats = join_gen3_dialogue(
            layout_text, corpus, tables[layout], charmap,
            overrides=keyed_by_layout(overrides, tables[layout], aliases),
            decisions=keyed_by_layout(load_dialogue_decisions(family, edition=edition), tables[layout], aliases),
            aliases=aliases, edition=edition,
        )
        # measured on the text keyed from the cart that was read, so every
        # layout is measured on the same lines
        stats = dialogue_stats(entry for entry in entries if entry.key not in exclusive)
        layouts[layout] = {"entries": entries, "stats": stats, "read": layout == read,
                           "exclusive": len(exclusive)}

    # The named text of each edition, from the layout of the revision that
    # was read; the script text of each layout.
    named: dict[str, dict[str, list[dict]]] = {}
    script: dict[str, dict[str, list[dict]]] = {}
    for (edition, layout), joined in layouts.items():
        catalog = dialogue_catalog(joined["entries"])
        script[layer_name(edition, layout)] = {key: ir for key, ir in catalog.items()
                                               if text_key_address(key) is not None}
        if layout == source.layout:
            named[layer_name(edition)] = {key: ir for key, ir in catalog.items()
                                          if text_key_address(key) is None}

    species = numbered(extracted / "rse_species.json")
    moves = numbered(extracted / "rse_moves.json")
    items = numbered(extracted / "rse_items.json")
    trainers = numbered(extracted / "rse_trainers.json")
    species_ids = registry_ids(species)
    item_names = {number: row.get("name") for number, row in items.items()}
    item_ids = registry_ids(item_names)
    trainer_ids = {number: str(number) for number in trainers}
    prefix = f"{family.qid_prefix}common."
    results = {
        "species_names": join_indexed_catalog(
            species, species_ids, corpus, prefix + "species_names.gSpeciesNames.", charmap),
        "move_names": join_indexed_catalog(
            moves, registry_ids(moves), corpus, prefix + "move_names.gMoveNames.", charmap),
        "item_names": join_indexed_catalog(item_names, item_ids, corpus, prefix + "items.gItems.", charmap),
        "item_descriptions": join_item_descriptions(
            items, item_ids, corpus, charmap, own_qids=_description_qids(items, tables[read], corpus)),
        "trainer_names": join_indexed_catalog(
            {number: row.get("name") for number, row in trainers.items()}, trainer_ids, corpus,
            prefix + "trainers.gTrainers.", charmap),
        "trainer_class_names": join_trainer_class_names(trainers, trainer_ids, corpus, charmap),
    }
    scope = load_engine_scope(family)
    strings, engine_stats = join_gen3_engine_strings(scope, corpus, charmap, companion=companion_strings)
    by_english: dict[str, str] = {}
    if gen1recomp is not None:
        strings, by_english = rom_label_strings(strings, gen1recomp, scope, luajit, ENGLISH_LOOKUP_SITES,
                                                 dialogue_label_rows(layouts[read]["entries"]))
    catalogs = {name: result.values for name, result in results.items()}
    catalogs["strings"] = strings
    if by_english:
        catalogs["strings_by_english"] = by_english
    decisions = load_dialogue_decisions(family)
    return {
        "source": source,
        "layouts": layouts,
        "guards": layout_guards(text, tables[read], tables, decisions),
        # the cart that was read, as one dialogue catalog (the release gate's
        # samples)
        "dialogue": {**named[layer_name(source.edition)], **script[layer_name(*read)]},
        "named": named,
        "script": script,
        # the cart that was read, for the coverage and the report
        "entries": layouts[read]["entries"],
        "dialogue_stats": layouts[read]["stats"],
        "catalogs": catalogs,
        "catalog_stats": {name: result.summary() for name, result in results.items()},
        "catalog_issues": {name: result.issues for name, result in results.items()},
        "engine_stats": engine_stats,
        "numbers": {"species": species_ids, "items": item_ids, "moves": registry_ids(moves)},
        "scope": scope,
    }
