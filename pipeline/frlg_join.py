"""Join PokeCorpus's FireRedLeafGreen collection to FireRed ROM data.

Three different keys meet here, one per surface gen1recomp's game3 runtime
exposes to a content mod:

* Dialogue.  The script extractor keys each message by its ROM pointer
  (``g3:081722c7``).  pret's ``pokefirered.sym`` names that address
  (``ViridianForest_Text_RickIntro``) and the corpus qid ends with the same
  label (``frlg.script.ViridianForest.ViridianForest_Text_RickIntro``), so
  the join is exact: no text similarity, no reviewed ambiguity.  This is a
  third strategy next to Red/Blue's label join and Gold/Silver's normalised
  English join; it is only possible because pret publishes FireRed's symbol
  table.  The English corpus row must still reproduce the extracted ROM IR
  (pipeline.frlg_text), so a stale symbol or a corpus/ROM mismatch falls
  back to English instead of shipping the wrong line.
* Named catalogs (species, moves, items, trainers, trainer classes) are ROM
  tables indexed by number, like the corpus rows
  (``gSpeciesNames.1``); the ROM's own English value must match the corpus
  English value before a translation is used.
* Item descriptions are separate ROM strings the extractor inlines into the
  item record; they are matched by their English text among the corpus's
  item and move description rows, and only used when every candidate row
  carries the same translation.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .corpus import canonical_language, corpus_target_text
from .frlg_text import (
    LANGUAGE_FOLDS,
    EncodeError,
    PretCharmap,
    corpus_ir,
    dynamic_signature,
    ir_plain,
    normalise_ir,
    text_key_address,
)

COLLECTION = "FireRedLeafGreen"

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
PLACEHOLDER_MISMATCH = "placeholder_mismatch"
REVIEWED = "reviewed"

SHIPPED = frozenset({TRANSLATED, OVERRIDE, REVIEWED})
COVERED = frozenset({TRANSLATED, OVERRIDE, REVIEWED, SAME_AS_ENGLISH})

FRLG_DIALOGUE_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/frlg-dialogue-overrides"
FRLG_DIALOGUE_DECISIONS_SCHEMA = "gen1recomp-translation-mods/frlg-dialogue-decisions"


@dataclass(frozen=True)
class FrlgCorpus:
    language: str
    qids: tuple[str, ...]
    english: tuple[str, ...]
    target: tuple[str, ...]
    by_qid: Mapping[str, int]
    by_label: Mapping[str, tuple[int, ...]]

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


def load_frlg_corpus(corpus_dir: str | Path, language: str) -> FrlgCorpus:
    """Load one language of the FireRedLeafGreen parallel corpus."""
    language = canonical_language(language)
    root = Path(corpus_dir)
    qids = _read_lines(root / "qid_msg.txt")
    english = _read_lines(root / "en_msg.txt")
    target = [corpus_target_text(line) for line in _read_lines(root / f"{language}_msg.txt")]
    if not (len(qids) == len(english) == len(target)):
        raise ValueError(f"{COLLECTION} corpus files for {language} are not parallel")
    by_label: dict[str, list[int]] = defaultdict(list)
    for index, qid in enumerate(qids):
        by_label[qid.rsplit(".", 1)[-1]].append(index)
    return FrlgCorpus(
        language, tuple(qids), tuple(english), tuple(target),
        {qid: index for index, qid in enumerate(qids)},
        {label: tuple(indices) for label, indices in by_label.items()},
    )


@dataclass
class FrlgDialogueEntry:
    key: str
    status: str
    english: list[dict]
    qid: str | None = None
    labels: tuple[str, ...] = ()
    translation: list[dict] | None = None
    detail: str = ""


def load_frlg_dialogue_overrides(language: str, root: str | Path | None = None) -> dict[str, dict]:
    """Reviewed per-key corrections: ``{key: {"qid": ..., "text": ...}}``.

    ``text`` is corpus notation (``\\n``, ``\\c``, ``[PLAYER]``...) and goes
    through the same encoder as a corpus row; ``qid`` documents which corpus
    row the correction replaces.
    """
    language = canonical_language(language)
    base = Path(root) if root else Path(__file__).resolve().parents[1]
    path = base / "overrides" / language / "frlg" / "dialogue.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != FRLG_DIALOGUE_OVERRIDES_SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported FireRed dialogue overrides: {path}")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"FireRed dialogue overrides need an entries object: {path}")
    for key, row in entries.items():
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str)
                or not isinstance(row.get("qid"), str) or not isinstance(row.get("reason"), str)):
            raise ValueError(f"invalid FireRed dialogue override for {key!r}: {path}")
    return entries


def load_frlg_dialogue_decisions(path: str | Path | None = None) -> dict[str, str]:
    """Reviewed ``{key: qid}`` picks for text gen1recomp rewrote itself.

    The standard scripts (nurse, PC, item pickup) are keyed by label and
    their English is gen1recomp's own wording, not the cart's, so it can
    never reproduce the corpus row.  A decision records that the corpus row
    is still the same message; the translation keeps every other check.
    """
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "frlg" / "dialogue_decisions.json"
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != FRLG_DIALOGUE_DECISIONS_SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported FireRed dialogue decisions: {path}")
    result: dict[str, str] = {}
    for key, row in (data.get("entries") or {}).items():
        if (not isinstance(row, dict) or not isinstance(row.get("qid"), str)
                or not row["qid"].startswith("frlg.") or not isinstance(row.get("reason"), str)):
            raise ValueError(f"invalid FireRed dialogue decision for {key!r}: {path}")
        result[key] = row["qid"]
    return result


def placeholders_supported(target: Iterable[Mapping], english: Iterable[Mapping]) -> bool:
    """Every runtime value the translation prints is one the English prints.

    The runtime substitutes placeholders by kind (text_ir.lua expand_seg),
    not by position, and the player/rival names are always known, but a
    ``STR_VAR_n`` is only filled when the script buffered it -- which the
    English line proves.  Official translations legitimately repeat or drop
    the player's name, so counts are not compared.
    """
    available = set(dynamic_signature(english))
    return all(row in available for row in dynamic_signature(target))


def _has_prose(segments: Iterable[Mapping]) -> bool:
    for segment in segments:
        kind = segment.get("t")
        if kind == "text" and str(segment.get("s", "")).strip():
            return True
        if kind in {"player", "rival", "strvar"}:
            return True
    return False


def _candidates(key: str, symbols: Mapping[int, list[str]], corpus: FrlgCorpus) -> tuple[tuple[str, ...], list[int]]:
    address = text_key_address(key)
    labels = tuple(symbols.get(address, ())) if address is not None else (key,)
    indices: list[int] = []
    for label in labels:
        indices.extend(corpus.by_label.get(label, ()))
    return labels, sorted(set(indices))


def join_frlg_dialogue(
    text: Mapping[str, list[dict]],
    corpus: FrlgCorpus,
    symbols: Mapping[int, list[str]],
    charmap: PretCharmap,
    *,
    overrides: Mapping[str, Mapping] | None = None,
    decisions: Mapping[str, str] | None = None,
) -> tuple[list[FrlgDialogueEntry], dict]:
    """Join every extracted text key to one corpus row and translate it."""
    overrides = overrides or {}
    decisions = decisions or {}
    entries: list[FrlgDialogueEntry] = []
    for key in sorted(text):
        rom_ir = normalise_ir(text[key])
        labels, indices = _candidates(key, symbols, corpus)
        entry = FrlgDialogueEntry(key, NO_MATCH, rom_ir, labels=labels)
        entries.append(entry)
        if not _has_prose(rom_ir):
            entry.status = MARKUP_ONLY
            continue
        if text_key_address(key) is not None and not labels:
            entry.status = NO_SYMBOL
            continue
        reviewed = decisions.get(key)
        if reviewed is not None:
            if reviewed not in corpus.by_qid:
                raise ValueError(f"FireRed dialogue decision {key!r}: unknown qid {reviewed}")
            indices = [corpus.by_qid[reviewed]]
        if not indices:
            entry.status = NO_MATCH
            continue
        if len(indices) > 1 and len({corpus.target[i] for i in indices}) > 1:
            entry.status = UNRESOLVED
            entry.detail = ", ".join(corpus.qids[i] for i in indices)
            continue
        index = indices[0]
        entry.qid = corpus.qids[index]
        try:
            english_ir = corpus_ir(corpus.english[index], charmap)
        except EncodeError as exc:
            entry.status = UNENCODABLE
            entry.detail = f"English corpus row: {exc}"
            continue
        override = overrides.get(key)
        if english_ir != rom_ir and reviewed is None and override is None:
            entry.status = ENGLISH_MISMATCH
            entry.detail = f"ROM {ir_plain(rom_ir)!r} != corpus {ir_plain(english_ir)!r}"
            continue
        source = override["text"] if override else corpus.target[index]
        if not source:
            entry.status = NO_TRANSLATION
            continue
        try:
            target_ir = corpus_ir(source, charmap, language=corpus.language)
        except EncodeError as exc:
            entry.status = UNENCODABLE
            entry.detail = str(exc)
            continue
        if not placeholders_supported(target_ir, rom_ir):
            entry.status = PLACEHOLDER_MISMATCH
            entry.detail = f"{dynamic_signature(rom_ir)} != {dynamic_signature(target_ir)}"
            continue
        if target_ir == rom_ir:
            entry.status = SAME_AS_ENGLISH
            continue
        entry.translation = target_ir
        entry.status = OVERRIDE if override else REVIEWED if reviewed else TRANSLATED
    stats = Counter(entry.status for entry in entries)
    total = len(entries) - stats[MARKUP_ONLY]
    covered = sum(stats[status] for status in COVERED)
    return entries, {
        "total": total,
        "covered": covered,
        "shipped": sum(stats[status] for status in SHIPPED),
        "percent": round(100.0 * covered / total, 2) if total else 100.0,
        "ignored_markup_only": stats[MARKUP_ONLY],
        "by_status": dict(sorted(stats.items())),
    }


def dialogue_catalog(entries: Iterable[FrlgDialogueEntry]) -> dict[str, list[dict]]:
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


def _plain(text: str, charmap: PretCharmap, language: str) -> str:
    """Encode/decode a one-line catalog value through the cart's glyph set."""
    segments = corpus_ir(text.replace(*_CATALOG_PKMN), charmap, language=language)
    if any(segment["t"] not in {"text", "eos"} for segment in segments):
        raise EncodeError("catalog value carries control codes")
    return "".join(segment.get("s", "") for segment in segments)


def _plain_multiline(text: str, charmap: PretCharmap, language: str) -> str:
    """Item/move descriptions: newlines are kept, other controls refused."""
    segments = corpus_ir(text, charmap, language=language)
    parts = []
    for segment in segments:
        kind = segment["t"]
        if kind == "text":
            parts.append(segment["s"])
        elif kind == "nl":
            parts.append("\n")
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
    corpus: FrlgCorpus,
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


def join_item_descriptions(
    items: Mapping[int, Mapping[str, str]],
    ids: Mapping[int, str],
    corpus: FrlgCorpus,
    charmap: PretCharmap,
) -> CatalogResult:
    """Item descriptions (TM/HM descriptions are their move's description)."""
    by_english: dict[str, set[str]] = defaultdict(set)
    own_label: dict[str, str] = {}
    for index, qid in enumerate(corpus.qids):
        if not (qid.startswith("frlg.common.items.gItemDescription_")
                or qid.startswith("frlg.common.move_descriptions.")):
            continue
        try:
            english = _plain_multiline(corpus.english[index], charmap, "en")
        except EncodeError:
            continue
        by_english[english].add(corpus.target[index])
        prefix = "frlg.common.items.gItemDescription_ITEM_"
        if qid.startswith(prefix):
            own_label[qid[len(prefix):]] = corpus.target[index]
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


# ------------------------------------------------------------- start menu

# src/ui/game3/start_menu.lua build_entries(): entry id -> the cart's own
# label row.  The labels are printed as-is (no Strings()), but the public
# ``ui.start_menu.items`` hook hands the entry list to mods before it is
# drawn.  The player's own name row ("trainer") is left alone.
START_MENU_QIDS: Mapping[str, str] = {
    "pokedex": "frlg.common.strings.gText_MenuPokedex",
    "pokemon": "frlg.common.strings.gText_MenuPokemon",
    "bag": "frlg.common.strings.gText_MenuBag",
    "save": "frlg.common.strings.gText_MenuSave",
    "option": "frlg.common.strings.gText_MenuOption",
    "exit": "frlg.common.strings.gText_MenuExit",
}
START_MENU_ENGLISH: Mapping[str, str] = {
    "pokedex": "POKéDEX", "pokemon": "POKéMON", "bag": "BAG",
    "save": "SAVE", "option": "OPTION", "exit": "EXIT",
}


def join_start_menu(corpus: FrlgCorpus, charmap: PretCharmap) -> CatalogResult:
    """Start-menu labels, keyed by entry id; English must match the engine's."""
    result = CatalogResult()
    for entry_id, qid in START_MENU_QIDS.items():
        result.stats["total"] += 1
        row = corpus.row(qid)
        if row is None:
            result.stats["no_corpus_row"] += 1
            result.issues.append(f"{entry_id}: no corpus row {qid}")
            continue
        english, target = row
        if _plain(english, charmap, "en") != START_MENU_ENGLISH[entry_id]:
            result.stats["english_mismatch"] += 1
            result.issues.append(f"{entry_id}: {qid} reads {english!r}")
            continue
        if not target:
            result.stats["no_translation"] += 1
            continue
        value = _plain(target, charmap, corpus.language)
        if value == START_MENU_ENGLISH[entry_id]:
            result.stats["same_as_english"] += 1
            continue
        result.values[entry_id] = value
        result.stats["translated"] += 1
    return result


# ----------------------------------------------------------- engine strings

FRLG_ENGINE_SCOPE_SCHEMA = "gen1recomp-translation-mods/frlg-engine-scope"
FRLG_ENGINE_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/engine-overrides"


def load_frlg_engine_scope(path: str | Path | None = None) -> dict[str, dict]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "frlg" / "engine_scope.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != FRLG_ENGINE_SCOPE_SCHEMA or data.get("version") != 1:
        raise ValueError(f"unsupported FireRed engine scope: {path}")
    keys = data.get("keys")
    if not isinstance(keys, dict) or not keys:
        raise ValueError(f"FireRed engine scope has no keys: {path}")
    for key, row in keys.items():
        if not isinstance(row, dict) or not isinstance(row.get("callsite"), str):
            raise ValueError(f"FireRed engine scope row {key!r} needs a callsite")
        qid = row.get("qid")
        if qid is not None and not (isinstance(qid, str) and qid.startswith("frlg.")):
            raise ValueError(f"FireRed engine scope row {key!r} has an invalid qid")
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
        elif kind != "eos":
            raise EncodeError("engine string carries a runtime placeholder")
    return "".join(parts)


def _check_engine_glyphs(key: str, value: str, charmap: PretCharmap, language: str) -> None:
    """A Strings() value is drawn by FrlgFont too: refuse what it cannot print."""
    folds = {**LANGUAGE_FOLDS["*"], **LANGUAGE_FOLDS.get(language, {})}
    drawable = set(charmap.translation_glyphs.values())
    for char in value:
        if char == "\n":
            continue
        if folds.get(char, char) not in drawable:
            raise ValueError(f"FireRed engine string {key!r} ({language}): {char!r} has no FireRed glyph")


def join_frlg_engine_strings(
    scope: Mapping[str, Mapping],
    corpus: FrlgCorpus,
    charmap: PretCharmap,
    *,
    root: str | Path | None = None,
) -> tuple[dict[str, str], dict]:
    """Resolve each FireRed-reachable ``Strings()`` key.

    Order: this language's FireRed overrides, then the reviewed FireRed
    corpus row named in the scope (the cart's own wording for its original
    menus), then the project's existing Gold/Silver and Red/Blue overrides
    for port-added rows the three runtimes share (same key, same Strings()
    registry), then English.
    """
    language = corpus.language
    base = Path(root) if root else Path(__file__).resolve().parents[1]
    frlg = _override_values(base / "overrides" / language / "frlg" / "engine.json")
    shared = {**_override_values(base / "overrides" / language / "rby" / "engine.json"),
              **_override_values(base / "overrides" / language / "gsc" / "engine.json")}
    values: dict[str, str] = {}
    details: dict[str, str] = {}
    for key, row in sorted(scope.items()):
        value, origin = None, None
        if key in frlg:
            value, origin = frlg[key], "frlg_override"
        elif row.get("qid"):
            pair = corpus.row(row["qid"])
            if pair is None:
                raise ValueError(f"FireRed engine scope row {key!r}: unknown qid {row['qid']}")
            english, target = pair
            if _engine_value(english, charmap, "en") != key:
                raise ValueError(
                    f"FireRed engine scope row {key!r}: {row['qid']} reads {english!r}")
            if target:
                value, origin = _engine_value(target, charmap, language), "corpus"
        elif key in shared:
            value, origin = shared[key], "shared_override"
        if value is None:
            details[key] = "fallback_english"
            continue
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

