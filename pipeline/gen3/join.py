"""Join a PokeCorpus generation-3 collection to its cart's ROM data.

FireRed/LeafGreen and Emerald are joined the same way, each through its own
collection, pret symbol table and charmap (pipeline.gen3.family); the
examples below are FireRed's.  Three different keys meet here, one per
surface gen1recomp's game3 runtime exposes to a content mod:

* Dialogue.  The script extractor keys each message by its ROM pointer
  (``g3:081722c7``).  pret's ``pokefirered.sym`` names that address
  (``ViridianForest_Text_RickIntro``) and the corpus qid ends with the same
  label (``frlg.script.ViridianForest.ViridianForest_Text_RickIntro``), so
  the join is exact: no text similarity, no reviewed ambiguity.  This is a
  third strategy next to Red/Blue's label join and Gold/Silver's normalised
  English join; it is only possible because pret publishes FireRed's symbol
  table.  The English corpus row must still reproduce the extracted ROM IR
  (pipeline.gen3.text), so a stale symbol or a corpus/ROM mismatch falls
  back to English instead of shipping the wrong line.
* Named catalogs (species, moves, items, trainers, trainer classes) are ROM
  tables indexed by number, like the corpus rows
  (``gSpeciesNames.1``); the ROM's own English value must match the corpus
  English value before a translation is used.
* Item descriptions are separate ROM strings the extractor inlines into the
  item record; they are matched by their English text among the corpus's
  item and move description rows, and only used when every candidate row
  carries the same translation.

A ROM text table the extractor keys by its own name and index
(``gNatureNamePointers[3]``) points at a string pret names
(``sHardyNatureName``): an extract that reads those pointers hands the
symbols over as ``aliases``, which join exactly like an address's symbols.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from ..shared.corpus import CORPUS_NULL, canonical_language, corpus_target_text
from .family import FAMILIES, FRLG, Gen3Family
from .text import (
    LANGUAGE_FOLDS,
    EncodeError,
    PretCharmap,
    braille_text,
    corpus_ir,
    dynamic_signature,
    JAPANESE,
    JAPANESE_FULLWIDTH,
    ir_plain,
    is_braille,
    normalise_ir,
    text_key_address,
)

# Dialogue provenance values.
TRANSLATED = "translated"
SAME_AS_ENGLISH = "same_as_english"
OVERRIDE = "override"
MARKUP_ONLY = "markup_only"
NO_SYMBOL = "no_symbol"
NO_MATCH = "no_match"
UNRESOLVED = "unresolved"
ENGLISH_MISMATCH = "english_mismatch"
UNENCODABLE = "unencodable"
NO_TRANSLATION = "no_translation"
CART_EMPTY = "cart_empty"
UNDECODABLE = "undecodable"
JAPANESE_SOURCE = "japanese_source"
PLACEHOLDER_MISMATCH = "placeholder_mismatch"
REVIEWED = "reviewed"
CONTENT_MATCH = "content_match"

# Rows nothing can translate, left out of the total as the markup-only ones
# are: the extractor cannot read the first (they are drawn from tiles, not
# from charmap bytes), and the second are Ruby/Sapphire leftovers the US cart
# still carries, whose only corpus row is the Japanese text.
IGNORED = frozenset({MARKUP_ONLY, UNDECODABLE, JAPANESE_SOURCE})

SHIPPED = frozenset({TRANSLATED, OVERRIDE, REVIEWED, CONTENT_MATCH, CART_EMPTY})
COVERED = frozenset({TRANSLATED, OVERRIDE, REVIEWED, CONTENT_MATCH, CART_EMPTY, SAME_AS_ENGLISH})

FRLG_DIALOGUE_OVERRIDES_SCHEMA = FRLG.schema("dialogue-overrides")
FRLG_DIALOGUE_DECISIONS_SCHEMA = FRLG.schema("dialogue-decisions")


@dataclass(frozen=True)
class Gen3Corpus:
    language: str
    qids: tuple[str, ...]
    english: tuple[str, ...]
    target: tuple[str, ...]
    by_qid: Mapping[str, int]
    by_label: Mapping[str, tuple[int, ...]]
    missing: tuple[bool, ...] = ()
    family: Gen3Family = FRLG

    def row(self, qid: str) -> tuple[str, str] | None:
        index = self.by_qid.get(qid)
        if index is None:
            return None
        return self.english[index], self.target[index]


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def load_gen3_corpus(corpus_dir: str | Path, language: str, family: Gen3Family = FRLG) -> Gen3Corpus:
    """Load one language of a generation-3 family's parallel corpus."""
    language = canonical_language(language)
    root = Path(corpus_dir)
    qids = _read_lines(root / "qid_msg.txt")
    english = _read_lines(root / "en_msg.txt")
    raw_target = _read_lines(root / f"{language}_msg.txt")
    target = [corpus_target_text(line) for line in raw_target]
    # "[NULL]" is not an empty row: it says the collection has no line there
    # for this language, where an empty line is a row the cart itself leaves
    # blank.  The two are told apart before the marker is read as empty.
    missing = tuple(line == CORPUS_NULL for line in raw_target)
    if not (len(qids) == len(english) == len(target)):
        raise ValueError(f"{family.collection} corpus files for {language} are not parallel")
    by_label: dict[str, list[int]] = defaultdict(list)
    markers = {marker for _edition, marker in family.edition_markers}
    for index, qid in enumerate(qids):
        label = qid.rsplit(".", 1)[-1]
        by_label[label].append(index)
        # one edition's row of a label (gDefaultBoyName1^S) is also a row of
        # the label itself, which the join tells apart by edition
        base, sep, marker = label.rpartition("^")
        if sep and marker in markers:
            by_label[base].append(index)
        # A ROM text table: the extractor keys its rows "gTypeNames[0]" and
        # "sFlavorTextOriginLocationTexts[3][1]" (RomText.key), the corpus
        # writes the same rows as "<family>.gTypeNames.0".
        parts = qid.split(".")
        indices = []
        while len(parts) > 1 and parts[-1].isdigit():
            indices.insert(0, parts.pop())
        if indices:
            by_label[parts[-1] + "".join(f"[{number}]" for number in indices)].append(index)
    return Gen3Corpus(
        language, tuple(qids), tuple(english), tuple(target),
        {qid: index for index, qid in enumerate(qids)},
        {label: tuple(indices) for label, indices in by_label.items()},
        missing, family,
    )


@dataclass
class Gen3DialogueEntry:
    key: str
    status: str
    english: list[dict]
    qid: str | None = None
    labels: tuple[str, ...] = ()
    translation: list[dict] | None = None
    detail: str = ""


def load_dialogue_overrides(language: str, family: Gen3Family = FRLG,
                            root: str | Path | None = None) -> dict[str, dict]:
    """Reviewed per-key corrections: ``{key: {"qid": ..., "text": ...}}``.

    ``text`` is corpus notation (``\\n``, ``\\c``, ``[PLAYER]``...) and goes
    through the same encoder as a corpus row; ``qid`` documents which corpus
    row the correction replaces.  ``runtime_fills`` lists the ``STR_VAR_n``
    buffers the runtime fills for this key although the English line does
    not print them (the provenance cites the callsite), so a translation
    that prints one passes the placeholder check.
    """
    language = canonical_language(language)
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    path = family.overrides_path(base, language, "dialogue.json")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != family.schema("dialogue-overrides") or data.get("version") != 1:
        raise ValueError(f"unsupported {family.game} dialogue overrides: {path}")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"{family.game} dialogue overrides need an entries object: {path}")
    for key, row in entries.items():
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str)
                or not isinstance(row.get("qid"), str) or not isinstance(row.get("reason"), str)
                or not row["qid"].startswith(family.qid_prefix)
                or not isinstance(row.get("runtime_fills", []), list)
                or not all(isinstance(fill, str) and re.fullmatch(r"STR_VAR_[123]", fill)
                           for fill in row.get("runtime_fills", []))):
            raise ValueError(f"invalid {family.game} dialogue override for {key!r}: {path}")
    return entries


FRLG_EDITIONS = FRLG.editions
EDITION_NAMES: Mapping[str, str] = {"firered": "FireRed", "leafgreen": "LeafGreen", "emerald": "Emerald",
                                    "ruby": "Ruby", "sapphire": "Sapphire"}


def load_dialogue_decisions(family: Gen3Family = FRLG, path: str | Path | None = None,
                            edition: str | None = None) -> dict[str, str]:
    """Reviewed ``{key: qid}`` picks for text gen1recomp rewrote itself.

    The standard scripts (nurse, PC, item pickup) are keyed by label and
    their English is gen1recomp's own wording, not the cart's, so it can
    never reproduce the corpus row.  A decision records that the corpus row
    is still the same message; the translation keeps every other check.

    A label names the same table in both carts, but a table can point at
    another string in LeafGreen (pret's ``#elif defined(LEAFGREEN)``): such an
    entry carries a ``leafgreen`` pick of its own, which that edition reads
    instead.
    """
    edition = edition or family.editions[0]
    if edition not in family.editions:
        raise ValueError(f"unsupported {family.game} edition: {edition!r}")
    if path is None:
        path = family.config_path(Path(__file__).resolve().parents[2], "dialogue_decisions.json")
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != family.schema("dialogue-decisions") or data.get("version") != 1:
        raise ValueError(f"unsupported {family.game} dialogue decisions: {path}")

    def pick(row) -> bool:
        return (isinstance(row, dict) and isinstance(row.get("qid"), str)
                and row["qid"].startswith(family.qid_prefix) and isinstance(row.get("reason"), str))

    others = set(family.editions[1:])
    result: dict[str, str] = {}
    for key, row in (data.get("entries") or {}).items():
        if not pick(row) or set(row) - {"qid", "reason"} - others:
            raise ValueError(f"invalid {family.game} dialogue decision for {key!r}: {path}")
        for other in others & set(row):
            if not pick(row[other]) or set(row[other]) - {"qid", "reason"}:
                raise ValueError(f"invalid {EDITION_NAMES.get(other, other)} dialogue decision for {key!r}: {path}")
        result[key] = (row[edition] if edition in row else row)["qid"]
    return result


# Runtime values every line can print: the player's name and the honorific
# the cart puts after it (FD placeholder KUN, 0x05), which the runtime
# expands to the cart's word or, where it does not know it, to nothing.
ALWAYS_FILLED = frozenset({("player", None, None), ("ph", None, 0x05)})


def placeholders_supported(target: Iterable[Mapping], english: Iterable[Mapping],
                           runtime_fills: Iterable[str] = ()) -> bool:
    """Every runtime value the translation prints is one the English prints.

    The runtime substitutes placeholders by kind (text_ir.lua expand_seg),
    not by position, and the player's name and its honorific are always
    known (``ALWAYS_FILLED``), but a ``STR_VAR_n`` is only filled when the
    script buffered it -- which the English line proves.  Official translations legitimately repeat or drop
    the player's name, so counts are not compared.
    """
    available = set(dynamic_signature(english)) | ALWAYS_FILLED
    available.update(("strvar", int(fill[-1]), None) for fill in runtime_fills)
    return all(row in available for row in dynamic_signature(target))


def _is_undecodable(segments: Iterable[Mapping]) -> bool:
    """A row the extractor could not read: its bytes are not charmap text.

    The battle HUD's status strings and the Union Room's activity list are
    drawn from tiles, so ``TextIR.decode`` writes a "?" per byte; a cart row
    of real text never reads like that.
    """
    body = "".join(segment.get("s", "") for segment in segments)
    body = body.replace(" ", "").replace("\n", "")
    marks = body.count("?")
    if marks >= 2 and marks == len(body):
        return True
    return marks >= 3 and marks >= len(body) / 2


# Ruby/Sapphire leftovers the US cart still carries: the corpus has them only
# as the Japanese source, which the cart's Latin charmap cannot encode -- as
# a Japanese character, or as a token only the Japanese charmap names (the
# full-width parentheses of the help system's own EXP and Level entries).
_JAPANESE_RANGES = ((0x2E80, 0x9FFF), (0x3000, 0x303F), (0xFF01, 0xFF9F))
_TOKEN = re.compile(r"\[([^\]\[]*)\]")


def _is_japanese(text: str, charmap: PretCharmap | None = None) -> bool:
    if any(start <= ord(char) <= stop for char in text for start, stop in _JAPANESE_RANGES):
        return True
    if charmap is None:
        return False
    return any(token.split()[0] not in charmap.names for token in _TOKEN.findall(text) if token.split())


def _has_prose(segments: Iterable[Mapping]) -> bool:
    for segment in segments:
        kind = segment.get("t")
        if kind == "text" and str(segment.get("s", "")).strip():
            return True
        if kind in {"player", "rival", "strvar"}:
            return True
    return False


# The cart's battle string table: the extractor keys its rows by pret's
# STRINGID_* constant (src/core/game3/battle/battle_text.lua), the corpus by
# the pret symbol each one points at, so they are joined on their English
# text instead of on a label.  Their FD escapes are battle placeholders.
def battle_qid_prefix(family: Gen3Family) -> str:
    return f"{family.qid_prefix}common.{family.battle_group}."


BATTLE_QID_PREFIX = battle_qid_prefix(FRLG)


def is_battle_key(key: str, rom_ir: Iterable[Mapping] = ()) -> bool:
    """A row of the battle string table: the extractor keys most of them by
    their STRINGID_* constant, the rest by their own pret symbol, and gives
    every one of them battle placeholders."""
    return key.startswith("STRINGID_") or any(segment.get("t") == "bph" for segment in rom_ir)


def _battle_index(corpus: Gen3Corpus, charmap: PretCharmap) -> Mapping[str, tuple[int, ...]]:
    rows: dict[str, list[int]] = defaultdict(list)
    prefix = battle_qid_prefix(corpus.family)
    for index, qid in enumerate(corpus.qids):
        if not qid.startswith(prefix):
            continue
        try:
            english = corpus_ir(corpus.english[index], charmap, battle=True)
        except EncodeError:
            continue
        rows[json.dumps(english, sort_keys=True)].append(index)
    return {key: tuple(indices) for key, indices in rows.items()}


# Families that share an English word with a menu label but not its
# translation, so a match on their text says nothing: the Easy Chat
# vocabulary, the quest log's phrases, the Pokedex categories and the Fame
# Checker's lines (the same list the engine scope keeps as a last resort).
_CONTENT_LAST_RESORT = (".easy_chat_", ".gEasyChatGroupName_", ".quest_log.",
                        ".gPokedexEntries.", ".fame_checker.")


def _content_index(corpus: Gen3Corpus, charmap: PretCharmap) -> Mapping[str, tuple[int, ...]]:
    """Every corpus row by the IR its English reads as.

    A ROM text table the cart reaches through a pointer (the Fame Checker's
    prompts, the list menus, the cursor options...) is named in the corpus
    by the symbol it points at, not by the table, so those rows are joined
    on their English text.  Only a text whose rows all agree on one
    translation is taken, the way the engine catalog's automatic matches
    are.
    """
    rows: dict[str, list[int]] = defaultdict(list)
    for index, qid in enumerate(corpus.qids):
        try:
            english = corpus_ir(corpus.english[index], charmap)
        except EncodeError:
            continue
        rows[json.dumps(english, sort_keys=True)].append(index)
    # A family that shares its English but not its translation is only read
    # when it is the only one that has the text at all.
    return {key: tuple(sorted(indices, key=lambda index: _is_last_resort(corpus.qids[index])))
            for key, indices in rows.items()}


def _is_last_resort(qid: str) -> bool:
    return any(family in qid for family in _CONTENT_LAST_RESORT)


def _candidates(key: str, symbols: Mapping[int, list[str]], corpus: Gen3Corpus,
                aliases: Mapping[str, Iterable[str]] | None = None) -> tuple[tuple[str, ...], list[int]]:
    address = text_key_address(key)
    labels = tuple(symbols.get(address, ())) if address is not None else (key,)
    if aliases and key in aliases:
        labels += tuple(label for label in aliases[key] if label not in labels)
    indices: list[int] = []
    for label in labels:
        indices.extend(corpus.by_label.get(label, ()))
    return labels, sorted(set(indices))


def _reads_as(english: str, rom_ir: list[dict], charmap: PretCharmap, battle: bool) -> bool:
    try:
        return corpus_ir(english, charmap, battle=battle) == rom_ir
    except EncodeError:
        return False


def _join_braille(entry: "Gen3DialogueEntry", spelled: str, target: str,
                  rom_ir: list[dict]) -> None:
    """A braille message: the corpus writes every cart's line as Unicode
    braille, the extractor decodes the cart's own line to Latin letters
    (``decode_braille``), and the runtime draws a Unicode cell as that very
    cell since gen1recomp v0.3.4 (gen1recomp#2400).  So the English row is
    checked by spelling it back into letters, and the translation is shipped
    as the cells themselves."""
    if spelled != ir_plain(rom_ir):
        entry.status = ENGLISH_MISMATCH
        entry.detail = f"ROM {ir_plain(rom_ir)!r} != corpus braille {spelled!r}"
        return
    if not target:
        entry.status = NO_TRANSLATION
        return
    if not is_braille(target):
        entry.status = UNENCODABLE
        entry.detail = "target row is not braille"
        return
    if braille_text(target) == spelled:
        entry.status = SAME_AS_ENGLISH
        return
    entry.translation = [{"t": "text", "s": target}, {"t": "eos"}]
    entry.status = TRANSLATED


# An edition's group (pokedex_entries^R.) or label (Text_Version^S).
_EDITION_MARKER = re.compile(r"\^([A-Za-z]+)(?=\.|$)")


def edition_rows(indices: Iterable[int], corpus: Gen3Corpus, marker: str | None) -> list[int]:
    """Leave out the rows another edition owns (pokedex_entries^S or
    Text_Version^S for Ruby): a collection that covers two carts keeps both
    lines under one label."""
    indices = list(indices)
    if marker is None:
        return indices
    kept = []
    for index in indices:
        found = _EDITION_MARKER.search(corpus.qids[index])
        if found is None or found.group(1) == marker:
            kept.append(index)
    return kept


def join_gen3_dialogue(
    text: Mapping[str, list[dict]],
    corpus: Gen3Corpus,
    symbols: Mapping[int, list[str]],
    charmap: PretCharmap,
    *,
    overrides: Mapping[str, Mapping] | None = None,
    decisions: Mapping[str, str] | None = None,
    aliases: Mapping[str, Iterable[str]] | None = None,
    edition: str | None = None,
) -> tuple[list[Gen3DialogueEntry], dict]:
    """Join every extracted text key to one corpus row and translate it.

    ``aliases`` names the pret symbols a table key's pointer reaches;
    ``edition`` is the cart the text was read from, for a collection that
    marks some of its rows as one edition's (Gen3Family.edition_markers).
    """
    overrides = overrides or {}
    decisions = decisions or {}
    marker = corpus.family.edition_marker(edition)
    battle_index = _battle_index(corpus, charmap)
    content_index = _content_index(corpus, charmap)
    entries: list[Gen3DialogueEntry] = []
    for key in sorted(text):
        rom_ir = normalise_ir(text[key])
        battle = is_battle_key(key, rom_ir)
        labels, indices = _candidates(key, symbols, corpus, aliases)
        indices = edition_rows(indices, corpus, marker)
        if battle and not indices:
            indices = list(battle_index.get(json.dumps(rom_ir, sort_keys=True), ()))
        matched_on_text = False
        entry = Gen3DialogueEntry(key, NO_MATCH, rom_ir, labels=labels)
        entries.append(entry)
        if not _has_prose(rom_ir) and key not in overrides:
            # (a reviewed override can fill a string the US cart leaves
            # empty: the Japanese honorific gText_ExpandedPlaceholder_Kun)
            entry.status = MARKUP_ONLY
            continue
        if text_key_address(key) is not None and not labels:
            entry.status = NO_SYMBOL
            continue
        reviewed = decisions.get(key)
        if reviewed is not None:
            if reviewed not in corpus.by_qid:
                raise ValueError(f"{corpus.family.game} dialogue decision {key!r}: unknown qid {reviewed}")
            indices = [corpus.by_qid[reviewed]]
        if not indices and reviewed is None:
            rows = edition_rows(content_index.get(json.dumps(rom_ir, sort_keys=True), ()), corpus, marker)
            if _is_undecodable(rom_ir):
                # Text the extractor could not read matches only another row
                # it could not read: neither says anything about the other.
                entry.status = UNDECODABLE
                continue
            preferred = [i for i in rows if not _is_last_resort(corpus.qids[i])]
            rows = preferred or list(rows)
            targets = {corpus.target[i] for i in rows if corpus.target[i]}
            if len(targets) == 1:
                indices = [next(i for i in rows if corpus.target[i])]
                matched_on_text = True
        if not indices:
            # A row the extractor could not read has no corpus row to find:
            # it is not a gap a translation could close.
            entry.status = UNDECODABLE if _is_undecodable(rom_ir) else NO_MATCH
            continue
        if len(indices) > 1 and len({corpus.target[i] for i in indices}) > 1:
            # Rows that share a label (a qid the corpus lists twice, the same
            # symbol in two files) are told apart by the ROM itself: only a
            # row whose English reproduces the extracted IR can be its line.
            same = [i for i in indices if _reads_as(corpus.english[i], rom_ir, charmap, battle)]
            if same and len({corpus.target[i] for i in same}) == 1:
                indices = same
        if len(indices) > 1 and len({corpus.target[i] for i in indices}) > 1:
            entry.status = UNRESOLVED
            entry.detail = ", ".join(corpus.qids[i] for i in indices)
            continue
        index = indices[0]
        entry.qid = corpus.qids[index]
        if entry.qid.endswith("Jpn"):
            # The cart keeps its Japanese status strings for a comparison the
            # battle HUD draws from tiles (pret's *Jpn rows,
            # battle_message.c:1798): they are Japanese text stored with the
            # Japanese charmap, which this cart's Latin table reads as junk.
            entry.status = JAPANESE_SOURCE
            continue
        spelled = braille_text(corpus.english[index])
        if spelled is not None:
            _join_braille(entry, spelled, corpus.target[index], rom_ir)
            continue
        try:
            english_ir = corpus_ir(corpus.english[index], charmap, battle=battle)
        except EncodeError as exc:
            entry.status = (JAPANESE_SOURCE if _is_japanese(corpus.english[index], charmap)
                            else UNENCODABLE)
            entry.detail = f"English corpus row: {exc}"
            continue
        override = overrides.get(key)
        if english_ir != rom_ir and reviewed is None and override is None:
            entry.status = ENGLISH_MISMATCH
            entry.detail = f"ROM {ir_plain(rom_ir)!r} != corpus {ir_plain(english_ir)!r}"
            continue
        source = override["text"] if override else corpus.target[index]
        if not source:
            if override is not None or (corpus.missing and corpus.missing[index]):
                # The collection has no line there for this language: nothing
                # says the cart prints nothing, so the English stays.
                entry.status = NO_TRANSLATION
                continue
            # The cart itself has nothing at this row in this language: its
            # sentences are worded without the fragment ("From ", the plural
            # "S", the "Big girl" a French NPC never says).  Shipping the
            # cart's own empty row prints nothing, as the cart does, instead
            # of leaving the English fragment on screen.
            entry.translation = [{"t": "eos"}]
            entry.status = CART_EMPTY
            continue
        try:
            target_ir = corpus_ir(source, charmap, language=corpus.language, battle=battle)
        except EncodeError as exc:
            entry.status = UNENCODABLE
            entry.detail = str(exc)
            continue
        if not placeholders_supported(target_ir, rom_ir, (override or {}).get("runtime_fills", ())):
            entry.status = PLACEHOLDER_MISMATCH
            entry.detail = f"{dynamic_signature(rom_ir)} != {dynamic_signature(target_ir)}"
            continue
        if target_ir == rom_ir:
            entry.status = SAME_AS_ENGLISH
            continue
        entry.translation = target_ir
        entry.status = (OVERRIDE if override else REVIEWED if reviewed
                        else CONTENT_MATCH if matched_on_text else TRANSLATED)
    return entries, dialogue_stats(entries)


def dialogue_stats(entries: Iterable[Gen3DialogueEntry]) -> dict:
    """The coverage of a set of joined entries."""
    entries = list(entries)
    stats = Counter(entry.status for entry in entries)
    ignored = sum(stats[status] for status in IGNORED)
    total = len(entries) - ignored
    covered = sum(stats[status] for status in COVERED)
    return {
        "total": total,
        "covered": covered,
        "shipped": sum(stats[status] for status in SHIPPED),
        "percent": round(100.0 * covered / total, 2) if total else 100.0,
        "ignored_markup_only": stats[MARKUP_ONLY],
        "ignored_undecodable": stats[UNDECODABLE],
        "ignored_japanese_source": stats[JAPANESE_SOURCE],
        "by_status": dict(sorted(stats.items())),
    }


def dialogue_catalog(entries: Iterable[Gen3DialogueEntry]) -> dict[str, list[dict]]:
    return {entry.key: entry.translation for entry in entries
            if entry.status in SHIPPED and entry.translation}


# ------------------------------------------------------------------ names

def registry_id(name: str) -> str | None:
    """Port of ``G3.idOf`` (src/mods/Schemas.lua): a record's registry id."""
    if not isinstance(name, str):
        return None
    value = (name.replace("é", "E").replace("É", "E")
             .replace("♀", "_F").replace("♂", "_M").replace("'", ""))
    value = "".join(char.upper() if char.isascii() else char for char in value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value or None


def registry_ids(names: Mapping[int, str]) -> dict[int, str]:
    """``G3``'s nameIndex: ids in ascending number order, first one wins."""
    taken: set[str] = set()
    result: dict[int, str] = {}
    for number in sorted(n for n in names if n > 0):
        id_ = registry_id(names[number])
        if id_ and id_ not in taken:
            taken.add(id_)
            result[number] = id_
    return result


# The ROM's PK/MN ligature pair (charmap PKMN = 53 54).  TextIR.decode
# reads it as "POKé", but the table extractors (trainer_extract.lua's
# decode_name) spell it out, which is what the catalogs carry.
_CATALOG_PKMN = ("[PKMN]", "POKéMON")


def _dialect_tag(segment: Mapping, charmap: PretCharmap) -> str | None:
    """A glyph-run tag the cart's dialect draws ({POKEBLOCK}), which the
    table extractors write into a catalog value as its tag."""
    tag = segment.get("tag") if segment.get("t") == "tag" else None
    return tag if tag and tag in charmap.dialect.charmap_extra.values() else None


def _plain(text: str, charmap: PretCharmap, language: str) -> str:
    """Encode/decode a one-line catalog value through the cart's glyph set."""
    segments = corpus_ir(text.replace(*_CATALOG_PKMN), charmap, language=language)
    parts = []
    for segment in segments:
        tag = _dialect_tag(segment, charmap)
        if segment["t"] not in {"text", "eos"} and tag is None:
            raise EncodeError("catalog value carries control codes")
        parts.append(tag or segment.get("s", ""))
    return "".join(parts)


def _plain_multiline(text: str, charmap: PretCharmap, language: str) -> str:
    """Item/move descriptions: newlines are kept, other controls refused."""
    segments = corpus_ir(text.replace(*_CATALOG_PKMN), charmap, language=language)
    parts = []
    for segment in segments:
        kind = segment["t"]
        tag = _dialect_tag(segment, charmap)
        if kind == "text":
            parts.append(segment["s"])
        elif kind == "nl":
            parts.append("\n")
        elif tag is not None:
            parts.append(tag)
        elif kind != "eos":
            raise EncodeError("description carries control codes")
    return "".join(parts)


@dataclass
class CatalogResult:
    values: dict[str, str] = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        total = self.stats["total"]
        covered = self.stats["translated"] + self.stats["same_as_english"]
        return {
            "total": total,
            "translated": self.stats["translated"],
            "same_as_english": self.stats["same_as_english"],
            "percent": round(100.0 * covered / total, 2) if total else 100.0,
            "fallback_english": total - covered,
        }


def join_indexed_catalog(
    rom_values: Mapping[int, str],
    ids: Mapping[int, str],
    corpus: Gen3Corpus,
    qid_prefix: str,
    charmap: PretCharmap,
    *,
    multiline: bool = False,
) -> CatalogResult:
    """Join ``rom_values[number]`` to ``{qid_prefix}{index}`` corpus rows.

    ``ids`` maps each number to the registry id the patch is keyed by; a
    number without an id (unused slots, duplicate names) is skipped, as the
    runtime could not address it either.
    """
    convert = _plain_multiline if multiline else _plain
    result = CatalogResult()
    for number, english in sorted(rom_values.items()):
        id_ = ids.get(number)
        if not id_ or not isinstance(english, str) or not english.strip():
            continue
        result.stats["total"] += 1
        row = corpus.row(f"{qid_prefix}{number}")
        if row is None:
            result.stats["no_corpus_row"] += 1
            result.issues.append(f"{id_}: no corpus row {qid_prefix}{number}")
            continue
        corpus_english, target = row
        try:
            if convert(corpus_english, charmap, "en") != english:
                result.stats["english_mismatch"] += 1
                result.issues.append(f"{id_}: ROM {english!r} != corpus {corpus_english!r}")
                continue
            if not target:
                result.stats["no_translation"] += 1
                continue
            value = convert(target, charmap, corpus.language)
        except EncodeError as exc:
            result.stats["unencodable"] += 1
            result.issues.append(f"{id_}: {exc}")
            continue
        if value == english:
            result.stats["same_as_english"] += 1
            continue
        result.values[id_] = value
        result.stats["translated"] += 1
    return result


# Each family's item description rows, and the prefix that names an item's
# own row after its constant (FireRed's gItemDescription_ITEM_<ID>); Emerald
# names them after the string (sMasterBallDesc), which the extract reaches
# through each item's description pointer (``own_qids``).
_ITEM_DESCRIPTION_FAMILIES: Mapping[str, tuple[tuple[str, ...], str | None]] = {
    "frlg": (("frlg.common.items.gItemDescription_", "frlg.common.move_descriptions."),
             "frlg.common.items.gItemDescription_ITEM_"),
    "emerald": (("e.common.item_descriptions.", "e.common.move_descriptions."), None),
    "rs": (("rs.common.item_descriptions.", "rs.common.move_descriptions."), None),
}


def join_item_descriptions(
    items: Mapping[int, Mapping[str, str]],
    ids: Mapping[int, str],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
    *,
    own_qids: Mapping[int, Iterable[str]] | None = None,
) -> CatalogResult:
    """Item descriptions (TM/HM descriptions are their move's description).

    ``own_qids`` maps an item number to the rows its description pointer
    names, for a family whose rows are not named after the item.
    """
    by_english: dict[str, set[str]] = defaultdict(set)
    own_label: dict[str, str] = {}
    families, own_prefix = _ITEM_DESCRIPTION_FAMILIES[corpus.family.id]
    for index, qid in enumerate(corpus.qids):
        if not qid.startswith(families):
            continue
        try:
            english = _plain_multiline(corpus.english[index], charmap, "en")
        except EncodeError:
            continue
        by_english[english].add(corpus.target[index])
        if own_prefix and qid.startswith(own_prefix):
            own_label[qid[len(own_prefix):]] = corpus.target[index]
    for number, qids in (own_qids or {}).items():
        english = (items.get(number) or {}).get("description")
        targets = set()
        for qid in qids:
            index = corpus.by_qid.get(qid)
            try:
                if index is not None and _plain_multiline(corpus.english[index], charmap, "en") == english:
                    targets.add(corpus.target[index])
            except EncodeError:
                continue
        if len(targets) == 1 and ids.get(number):
            own_label[ids[number]] = next(iter(targets))
    result = CatalogResult()
    for number, row in sorted(items.items()):
        id_ = ids.get(number)
        english = row.get("description") if isinstance(row, Mapping) else None
        if not id_ or not isinstance(english, str) or not english.strip():
            continue
        result.stats["total"] += 1
        targets = by_english.get(english)
        if not targets:
            result.stats["no_corpus_row"] += 1
            result.issues.append(f"{id_}: no corpus description matches {english!r}")
            continue
        if len(targets) > 1 and id_ in own_label:
            # e.g. BEAD MAIL and DREAM MAIL share their English description
            # but not their translation: the item's own row decides.
            targets = {own_label[id_]}
        if len(targets) > 1:
            result.stats["unresolved"] += 1
            result.issues.append(f"{id_}: {len(targets)} different translations for {english!r}")
            continue
        target = next(iter(targets))
        if not target:
            result.stats["no_translation"] += 1
            continue
        try:
            value = _plain_multiline(target, charmap, corpus.language)
        except EncodeError as exc:
            result.stats["unencodable"] += 1
            result.issues.append(f"{id_}: {exc}")
            continue
        if value == english:
            result.stats["same_as_english"] += 1
            continue
        result.values[id_] = value
        result.stats["translated"] += 1
    return result


def join_trainer_class_names(trainers: Mapping[int, Mapping], trainer_ids: Mapping[int, str],
                             corpus: Gen3Corpus, charmap: PretCharmap) -> CatalogResult:
    """Join each trainer class once, then fan it out to its trainers.

    Every class is translated since gen1recomp v0.3.4: the runtime reads a
    trainer's class by id (gen1recomp#2398), so RIVAL, LEADER, ELITE FOUR
    and CHAMPION no longer have to stay in English for the rival's name, the
    quest log and the battle transition to work.
    """
    per_class = join_indexed_catalog(
        {row["class"]: row["className"] for row in trainers.values()
         if isinstance(row.get("class"), int) and row.get("className")},
        {row["class"]: str(row["class"]) for row in trainers.values() if isinstance(row.get("class"), int)},
        corpus, f"{corpus.family.qid_prefix}common.trainer_class_names.gTrainerClassNames.", charmap,
    )
    result = CatalogResult()
    result.stats.update(per_class.stats)
    result.issues = per_class.issues
    for number, row in trainers.items():
        value = per_class.values.get(str(row.get("class")))
        if row.get("className") and value:
            result.values[trainer_ids[number]] = value
    return result


# ----------------------------------------------------------- engine strings

FRLG_ENGINE_SCOPE_SCHEMA = FRLG.schema("engine-scope")
FRLG_ENGINE_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/engine-overrides"


def load_engine_scope(family: Gen3Family = FRLG, path: str | Path | None = None) -> dict[str, dict]:
    if path is None:
        path = family.config_path(Path(__file__).resolve().parents[2], "engine_scope.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != family.schema("engine-scope") or data.get("version") != 1:
        raise ValueError(f"unsupported {family.game} engine scope: {path}")
    keys = data.get("keys")
    if not isinstance(keys, dict) or not keys:
        raise ValueError(f"{family.game} engine scope has no keys: {path}")
    for key, row in keys.items():
        if not isinstance(row, dict) or not isinstance(row.get("callsite"), str):
            raise ValueError(f"{family.game} engine scope row {key!r} needs a callsite")
        qid = row.get("qid")
        if qid is not None and not (isinstance(qid, str) and qid.startswith(family.qid_prefix)):
            raise ValueError(f"{family.game} engine scope row {key!r} has an invalid qid")
    return keys


def _override_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    values = {}
    for key, row in entries.items():
        if isinstance(row, dict) and isinstance(row.get("override"), str):
            values[key] = row["override"]
    return values


def _engine_value(text: str, charmap: PretCharmap, language: str) -> str:
    """Corpus row -> Strings() value: \\n kept, paragraph -> blank line."""
    segments = corpus_ir(text, charmap, language=language)
    parts = []
    for segment in segments:
        kind = segment["t"]
        if kind == "text":
            parts.append(segment["s"])
        elif kind in {"nl", "scroll"}:
            parts.append("\n")
        elif kind == "para":
            parts.append("\n\n")
        elif kind == "ext" and segment.get("cmd") == 0x00:
            # NAME_END: where a Ruby/Sapphire map name's short form ends; the
            # screens that look the name up print all of it
            continue
        elif kind != "eos":
            raise EncodeError("engine string carries a runtime placeholder")
    return "".join(parts)


# A Strings() directive (``%s``, ``%03d``, ``%-5s``, numbered ``%2$s``);
# ``%%`` is a literal.
_ENGINE_DIRECTIVE = re.compile(r"%(%|(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[A-Za-z])")
_DYNAMIC = frozenset({"strvar", "ph"})
_KEYPAD = re.compile(r"\[((?:A|B|L|R|START|SELECT)_BUTTON|DPAD_[A-Z]+)\]")


def _key_parts(key: str) -> tuple[list[str], list[str]]:
    """Literal chunks around a key's directives, and the directives."""
    chunks, directives, start = [], [], 0
    literal = ""
    for match in _ENGINE_DIRECTIVE.finditer(key):
        literal += key[start:match.start()]
        start = match.end()
        if match.group(1) == "%":
            literal += "%"
            continue
        chunks.append(literal)
        directives.append(match.group(1))
        literal = ""
    chunks.append(literal + key[start:])
    return chunks, directives


def _corpus_parts(text: str, charmap: PretCharmap, language: str, *, battle: bool = False,
                  literal_buffers: bool = False, paragraph: str = "\n", line: str = "\n",
                  scroll: str = "\n") -> tuple[list[str], list[tuple]]:
    """Literal chunks around a corpus row's runtime values, and those values.

    Field text keeps the player's and rival's names as the ``{PLAYER}`` and
    ``{RIVAL}`` tokens the game3 runtime substitutes itself, and so are the
    ``{STR_VAR_n}`` buffers when the key spells them out (``literal_buffers``);
    other buffers become directive slots.  Battle text (``battle``) uses
    another placeholder table, which the field decoder reads as names and
    buffers: there, every placeholder is a slot, identified by its code.
    A paragraph becomes ``paragraph``, a line break ``line`` and a scrolled
    line ``scroll``, and a trailing one is dropped; text effects (sound
    waits, colours) are dropped too.
    """
    chunks, values, current = [], [], []
    segments = []
    # Keypad icons ([A_BUTTON]) are the {A_BUTTON} tokens FrlgFont draws; the
    # field decoder has no glyph for them.
    for index, piece in enumerate(_KEYPAD.split(text)):
        if index % 2:
            segments.append({"t": "text", "s": "{%s}" % piece})
        elif piece:
            segments += [segment for segment in corpus_ir(piece, charmap, language=language)
                         if segment["t"] != "eos"]
    while segments and segments[-1]["t"] in {"para", "nl", "scroll"}:
        segments.pop()
    for segment in segments:
        kind = segment["t"]
        if kind == "text":
            current.append(segment["s"])
        elif kind == "nl":
            current.append(line)
        elif kind == "scroll":
            current.append(scroll)
        elif kind == "para":
            current.append(paragraph)
        elif battle and kind in {"player", "rival", "strvar", "ph"}:
            chunks.append("".join(current))
            current = []
            values.append((kind, segment.get("n"), segment.get("code")))
        elif kind == "player":
            current.append("{PLAYER}")
        elif kind == "rival":
            current.append("{RIVAL}")
        elif kind == "strvar" and literal_buffers:
            current.append("{STR_VAR_%d}" % segment["n"])
        elif kind in _DYNAMIC:
            chunks.append("".join(current))
            current = []
            values.append((kind, segment.get("n"), segment.get("code")))
    chunks.append("".join(current))
    return chunks, values


def _loose(text: str) -> str:
    """Line breaks and paragraph marks count as spaces."""
    return re.sub(r"(?:\\[pnl]|[\s\f])+", " ", text).strip()


def _paragraph_mark(key: str) -> str:
    """How ``key`` separates paragraphs, for the value to do the same."""
    for mark in ("\f", "\\p", "\n\n"):
        if mark in key:
            return mark
    return "\n"


def _break_marks(key: str) -> tuple[str, str]:
    """How ``key`` writes a line break and a scrolled line.

    Most keys use real newlines.  Some hand the runtime pret's own escapes
    instead (Teachy TV's lessons are "...\\n...\\l...\\p"), and a value
    must write its breaks the same way for the runtime to act on them.
    """
    if "\\n" in key or "\\l" in key:
        return "\\n", ("\\l" if "\\l" in key else "\\n")
    return "\n", "\n"


def engine_context_source(key: str) -> str:
    """A context key ("option.battleStyle|SHIFT") reads as its English source."""
    return key.split("|", 1)[1] if re.match(r"[a-z][\w.]*(?: [\w.]+)*\|", key) else key


def is_battle_qid(qid: str) -> bool:
    return ".battle_message." in qid


def apply_fills(text: str, fills: Mapping[str, str]) -> str:
    """Put a corpus row's text in place of a placeholder the engine spells out."""
    for placeholder, value in fills.items():
        text = text.replace(placeholder, value)
    return text


def engine_template(key: str, english: str, target: str, charmap: PretCharmap,
                    language: str, *, battle: bool = False) -> tuple[str, str]:
    """A corpus row's translation as the Strings() value for ``key``.

    The English row must read as the key, runtime values standing where the
    key has directives; ``loose`` when only line breaks and paragraph marks
    differ (the value then keeps the translation's own breaks).  Each
    translated value keeps the directive of the English value it stands for.
    When the translation orders them differently the directives are
    numbered (``%2$s``), which Strings() maps back to its arguments.  Raises
    ValueError when the row cannot stand for the key.
    """
    line, scroll = _break_marks(key)
    options = {"battle": battle, "literal_buffers": "{STR_VAR_" in key,
               "paragraph": _paragraph_mark(key), "line": line, "scroll": scroll}
    key_chunks, directives = _key_parts(key)
    en_chunks, en_values = _corpus_parts(english, charmap, "en", **options)
    if len(en_values) != len(directives):
        raise ValueError(f"{len(en_values)} runtime value(s) for {len(directives)} directive(s)")
    if en_chunks == key_chunks:
        how = "exact"
    elif [_loose(c) for c in en_chunks] == [_loose(c) for c in key_chunks]:
        how = "loose"
    else:
        raise ValueError("the English row does not read as the key")
    chunks, values = _corpus_parts(target, charmap, language, **options)
    if sorted(values, key=repr) != sorted(en_values, key=repr):
        raise ValueError("the translation prints other runtime values than the English")
    if values == en_values:
        slots = [f"%{directive}" for directive in directives]
    elif len(set(en_values)) != len(en_values):
        raise ValueError("a repeated runtime value cannot be renumbered")
    else:
        positions = [en_values.index(value) for value in values]
        slots = [f"%{index + 1}${directives[index]}" for index in positions]
    parts = [chunks[0].replace("%", "%%")]
    for slot, chunk in zip(slots, chunks[1:]):
        parts.append(slot)
        parts.append(chunk.replace("%", "%%"))
    value = "".join(parts)
    # Spaces the cart pads a label with ("NAME: ") are the key's to decide.
    if not key.endswith(" "):
        value = value.rstrip(" ")
    if not key.startswith(" "):
        value = value.lstrip(" ")
    # A trailing page or line mark in the key is a pause the runtime acts on
    # (Oak's "\f" waits for A); the corpus row's own trailing mark is dropped.
    return value + re.search(r"(?:\f|\\[pnl]|\n)*$", key).group(0), how


def check_engine_directives(key: str, value: str, language: str = "") -> None:
    """Refuse a value Strings() would reject for ``key`` (src/core/Strings.lua):
    numbered directives all or none, each naming one of the key's arguments,
    and otherwise the key's directives in order."""
    wanted = [d for d in (m.group(1) for m in _ENGINE_DIRECTIVE.finditer(key)) if d != "%"]
    given = [d for d in (m.group(1) for m in _ENGINE_DIRECTIVE.finditer(value)) if d != "%"]
    numbered = [d for d in given if re.match(r"\d+\$", d)]
    where = f"engine string {key!r} ({language})"
    if numbered:
        if len(numbered) != len(given):
            raise ValueError(f"{where}: mixes numbered and plain directives")
        for directive in given:
            index, spec = directive.split("$", 1)
            if not 1 <= int(index) <= len(wanted) or spec[-1] != wanted[int(index) - 1][-1]:
                raise ValueError(f"{where}: %{directive} does not name an argument of the key")
    elif [d[-1] for d in given] != [d[-1] for d in wanted]:
        raise ValueError(f"{where}: directives {given} for {wanted}")


def _check_engine_glyphs(key: str, value: str, charmap: PretCharmap, language: str) -> None:
    """A Strings() value is drawn by FrlgFont too: refuse what it cannot print."""
    folds = {**LANGUAGE_FOLDS["*"], **LANGUAGE_FOLDS.get(language, {})}
    drawable = set(charmap.translation_glyphs.values())
    if language == JAPANESE:
        # FrlgFont draws the cart's Japanese fonts since v0.3.4: the kana and
        # the symbols the Japanese sheets keep at the Latin block's codes,
        # which a Japanese string writes in their full-width form.
        drawable |= set(charmap.japanese) | JAPANESE_FULLWIDTH
    # Directives are filled in at runtime ("%%" prints a percent sign), and
    # {PLAYER}/{A_BUTTON}-style tokens are names or icons the runtime draws.
    printed = _ENGINE_DIRECTIVE.sub(lambda m: "%" if m.group(1) == "%" else "", value)
    printed = re.sub(r"\{[A-Z0-9_]+\}|\\[pnl]", "", printed)
    for char in printed:
        # the key's own characters are drawn in English already (↑↓ icons)
        if char in "\n\f" or char in key:
            continue
        if folds.get(char, char) not in drawable:
            raise ValueError(f"engine string {key!r} ({language}): {char!r} has no game3 glyph")


def join_gen3_engine_strings(
    scope: Mapping[str, Mapping],
    corpus: Gen3Corpus,
    charmap: PretCharmap,
    *,
    root: str | Path | None = None,
    companion: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Resolve each ``Strings()`` key the family's runtime can reach.

    Order: this language's overrides for the family, then the reviewed corpus
    row named in the scope (the cart's own wording for its original menus),
    then ``companion``, the values another game of the same release resolved
    for the same runtime (Ruby and Sapphire take Emerald's for the screens
    their own carts never had: the link lobby, the Union Room words, Mystery
    Gift), then the reviewed overrides of the other game3 families for the port-added
    rows they share (Emerald falls back on FireRed's), then the project's
    existing Gold/Silver and Red/Blue overrides for port-added rows the
    runtimes share (same key, same Strings() registry), then English.
    """
    language = corpus.language
    family = corpus.family
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    own = _override_values(family.overrides_path(base, language, "engine.json"))
    game3 = {}
    for other in reversed(family.shared_engine_families):
        game3.update(_override_values(FAMILIES[other].overrides_path(base, language, "engine.json")))
    shared = {**_override_values(base / "overrides" / language / "rby" / "engine.json"),
              **_override_values(base / "overrides" / language / "gsc" / "engine.json")}
    where = f"{family.game} engine scope row"
    values: dict[str, str] = {}
    details: dict[str, str] = {}
    for key, row in sorted(scope.items()):
        value, origin = None, None
        if key in own:
            value, origin = own[key], f"{family.id}_override"
        elif row.get("qid"):
            pair = corpus.row(row["qid"])
            if pair is None:
                raise ValueError(f"{where} {key!r}: unknown qid {row['qid']}")
            english, target = pair
            fills = row.get("fill") or {}
            if fills:
                rows = {placeholder: corpus.row(qid) for placeholder, qid in fills.items()}
                if any(pair is None for pair in rows.values()):
                    raise ValueError(f"{where} {key!r}: unknown fill qid")
                english = apply_fills(english, {p: pair[0] for p, pair in rows.items()})
                target = apply_fills(target, {p: pair[1] for p, pair in rows.items()}) if target else target
                if any(not pair[1] for pair in rows.values()):
                    target = ""
            battle = is_battle_qid(row["qid"])
            source = engine_context_source(key)
            try:
                engine_template(source, english, english, charmap, "en", battle=battle)
            except ValueError as error:
                raise ValueError(
                    f"{where} {key!r}: {row['qid']} reads {english!r} ({error})") from None
            if target:
                try:
                    value, _how = engine_template(source, english, target, charmap, language, battle=battle)
                    origin = "corpus"
                except ValueError:
                    value = None
        if value is None and companion and key in companion:
            value, origin = companion[key], "companion"
        if value is None and key in game3:
            value, origin = game3[key], "game3_override"
        if value is None and key in shared:
            # Gold's text engine marks a scrolled line break with \v.
            value, origin = shared[key].replace("\v", "\n"), "shared_override"
        if value is None:
            details[key] = "fallback_english"
            continue
        check_engine_directives(engine_context_source(key), value, language)
        _check_engine_glyphs(key, value, charmap, language)
        details[key] = origin if value != key else "same_as_english"
        if value != key:
            values[key] = value
    translated = sum(1 for origin in details.values() if origin != "fallback_english")
    return values, {
        "total": len(scope),
        "translated": translated,
        "percent": round(100.0 * translated / len(scope), 2) if scope else 100.0,
        "fallback_english": sorted(key for key, origin in details.items() if origin == "fallback_english"),
        "details": details,
    }

