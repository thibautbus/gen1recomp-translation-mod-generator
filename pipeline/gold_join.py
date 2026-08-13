"""Join the GoldSilver corpus to Gold ROM text pointers.

Builds the worksheet on top of the measured strategy (normalised-English
join, 93.2% unique -- see tools/measure_join.py, which this module's
normalise()/join logic mirrors) plus a disambiguation measurement
(map-context via gold_maps.tsv).

Nothing here is guessed: an entry that cannot be resolved automatically, or
by a human-reviewed override, keeps its English text and is reported, never
silently translated by a low-confidence pick.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .gold_text import GoldTextRecord, normalise, split_lines
from .tokens import DYNAMIC_TOKEN_RE, TOKEN_RE, check_placeholders, corpus_to_engine, known_literal_tokens

# Provenance values a GoldJoinEntry can carry.
UNIQUE = "unique"
HARMLESS_AMBIGUOUS = "harmless_ambiguous"
MAP_CONTEXT = "map_context"
OVERRIDE = "override"
UNRESOLVED = "unresolved"
NO_MATCH = "no_match"
MARKUP_ONLY = "markup_only"

# A token TOKEN_RE can find that is not a problem in shipped output: it was
# either converted away (absent from the translation because
# _CORPUS_EXPANSIONS maps it to "" or plain text) or is a legitimate runtime
# substituent DYNAMIC_TOKEN_RE recognises (checked separately, by identity,
# not just "known").
_KNOWN_LITERAL_TOKENS = known_literal_tokens()


def _token_aware_key(value: str) -> tuple[tuple[str, str], ...]:
    """Normalize prose while preserving every command token exactly.

    ``gold_text.normalise`` intentionally erases braces and punctuation for
    the English lookup.  That is unsafe for ambiguity resolution: two corpus
    rows can have the same prose but different sound/RAM commands.  This key
    only folds prose segments and keeps token spelling/ordering visible.
    """
    parts: list[tuple[str, str]] = []
    position = 0
    for match in TOKEN_RE.finditer(value):
        prose = normalise(value[position:match.start()])
        if prose:
            parts.append(("text", prose))
        parts.append(("token", match.group(0)))
        position = match.end()
    prose = normalise(value[position:])
    if prose:
        parts.append(("text", prose))
    return tuple(parts)


@dataclass(frozen=True)
class GoldJoinEntry:
    pointer: str
    label: str | None
    english: str
    translation: str | None           # corpus_to_engine-converted; None when unresolved
    provenance: str
    qid: str | None = None            # the corpus qid the translation came from
    candidate_qids: tuple[str, ...] = field(default_factory=tuple)  # for ambiguous/unresolved entries


def read_corpus_rows(corpus_dir: str | Path, target_lang: str = "fr") -> list[tuple[str, str, str]]:
    """Read (qid, english, translation) triples from a poke-corpus directory."""
    corpus_dir = Path(corpus_dir)
    qids = split_lines((corpus_dir / "qid_msg.txt").read_text(encoding="utf-8"))
    ens = split_lines((corpus_dir / "en_msg.txt").read_text(encoding="utf-8"))
    targets = split_lines((corpus_dir / f"{target_lang}_msg.txt").read_text(encoding="utf-8"))
    if not (len(qids) == len(ens) == len(targets)):
        raise ValueError("corpus files are not parallel (qid/en/target line counts differ)")
    return list(zip(qids, ens, targets))


def _map_segment(qid: str) -> str | None:
    """The qid's second dot-component, a candidate map name (gs.Route29.Foo
    -> "Route29"). Meaningless for non-map categories (gs.names.Foo), but
    harmless: those never collide with a real converted manifest map name.
    """
    parts = qid.split(".", 2)
    return parts[1] if len(parts) >= 2 else None


_SHORT_UPPER = re.compile(r"^[A-Za-z]?\d+[A-Za-z]?$")  # 1F, B1F, 2F...


def convert_manifest_map_name(name: str) -> str:
    """SNAKE_CASE manifest map constant -> the corpus's PascalCase map name.

    Verified against the real manifest: 313/368 map names round-trip this
    way; the remainder are minor acronym-casing mismatches (PP_SPEECH_HOUSE
    -> PpSpeechHouse instead of PPSpeechHouse) this heuristic does not need
    to be perfect for -- a miss just means that one map's bank is not on
    offer as a disambiguation signal, not a wrong answer.
    """
    parts = []
    for part in name.split("_"):
        if _SHORT_UPPER.match(part):
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return "".join(parts)


def load_map_banks(gold_maps_tsv: str | Path) -> dict[str, set[str]]:
    """{corpus-style map name: {bank hex, ...}} from gold_extract.lua's
    gold_maps.tsv (manifest constant name -> scripts bank).
    """
    banks: dict[str, set[str]] = defaultdict(set)
    for line in split_lines(Path(gold_maps_tsv).read_text(encoding="utf-8")):
        if not line:
            continue
        name, _, bank = line.partition("\t")
        banks[convert_manifest_map_name(name)].add(bank.lower())
    return dict(banks)


def join_gold_pointers(
    records: list[GoldTextRecord],
    corpus_rows: list[tuple[str, str, str]],
    map_banks: Mapping[str, set[str]] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> tuple[list[GoldJoinEntry], dict]:
    """Join Gold's pointer catalog to the corpus by normalised English.

    ``overrides`` is pointer-keyed (unlike RBY's qid-keyed
    corpus_overrides.json): a pointer is what is ambiguous here, not a
    corpus row, so that is the natural key for a human-reviewed pick among
    several candidate qids. Checked before any automated matching, same
    priority as pipeline/engine.py's engine-string overrides.

    Disambiguation, in order, for a pointer with several candidates whose
    French actually differs:

    1. ``overrides[pointer]`` -- a human already resolved it.
    2. map context -- exactly one candidate's qid names a map whose
       scripts bank (map_banks) matches the pointer's own bank.
    3. unresolved -- the pointer keeps its English text and is reported
       rather than silently guessed at, matching this project's mandatory
       fallback rule for anything low-confidence.
    """
    overrides = overrides or {}
    map_banks = map_banks or {}

    by_english: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for qid, en, fr in corpus_rows:
        by_english[normalise(en)].append((qid, fr))

    entries: list[GoldJoinEntry] = []
    stats = {
        "total": len(records), "unique": 0, "harmless_ambiguous": 0,
        "map_context": 0, "override": 0, "unresolved": 0, "no_match": 0,
        "markup_only": 0,
    }

    for record in records:
        pointer, english, label = record.pointer, record.text, record.label
        norm = normalise(english)

        if pointer in overrides:
            entries.append(GoldJoinEntry(pointer, label, english, corpus_to_engine(overrides[pointer]),
                                          OVERRIDE))
            stats["override"] += 1
            continue

        if not norm:
            entries.append(GoldJoinEntry(pointer, label, english, corpus_to_engine(english), MARKUP_ONLY))
            stats["markup_only"] += 1
            continue

        candidates = by_english.get(norm)
        if not candidates:
            entries.append(GoldJoinEntry(pointer, label, english, None, NO_MATCH))
            stats["no_match"] += 1
            continue

        if len(candidates) == 1:
            qid, fr = candidates[0]
            entries.append(GoldJoinEntry(pointer, label, english, corpus_to_engine(fr), UNIQUE, qid))
            stats["unique"] += 1
            continue

        distinct_french = {_token_aware_key(fr) for _, fr in candidates}
        if len(distinct_french) == 1:
            qid, fr = candidates[0]
            entries.append(GoldJoinEntry(pointer, label, english, corpus_to_engine(fr), HARMLESS_AMBIGUOUS,
                                          qid, tuple(sorted(c for c, _ in candidates))))
            stats["harmless_ambiguous"] += 1
            continue

        pointer_bank = pointer.split(":", 1)[0].lower()
        by_map = [
            (qid, fr) for qid, fr in candidates
            if pointer_bank in map_banks.get(_map_segment(qid) or "", set())
        ]
        distinct_by_map_french = {_token_aware_key(fr) for _, fr in by_map}
        if len(distinct_by_map_french) == 1:
            qid, fr = by_map[0]
            entries.append(GoldJoinEntry(pointer, label, english, corpus_to_engine(fr), MAP_CONTEXT,
                                          qid, tuple(sorted(c for c, _ in candidates))))
            stats["map_context"] += 1
            continue

        entries.append(GoldJoinEntry(pointer, label, english, None, UNRESOLVED,
                                      candidate_qids=tuple(sorted(c for c, _ in candidates))))
        stats["unresolved"] += 1

    return entries, stats


def audit_join(entries: list[GoldJoinEntry]) -> list[str]:
    """Gate checks: no pointer collisions, no unknown token in any shipped
    translation, no dropped/swapped runtime substitution, unresolved
    content flagged rather than silently English.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.pointer in seen:
            problems.append(f"duplicate pointer in worksheet: {entry.pointer!r}")
        seen.add(entry.pointer)
        if entry.translation is None:
            continue
        for token in TOKEN_RE.findall(entry.translation):
            if token in _KNOWN_LITERAL_TOKENS or DYNAMIC_TOKEN_RE.fullmatch(token):
                continue
            if re.fullmatch(r"\\x[0-9A-Fa-f]{2}", token):
                # Documented, deliberate debt (pipeline/tokens.py): both RBY
                # and Gold carry unmapped hex glyph escapes that pass
                # through literally rather than being guessed.
                continue
            problems.append(f"unknown token {token!r} in translation for {entry.pointer!r}")
        for message in check_placeholders(entry.english, entry.translation):
            problems.append(f"{message} in translation for {entry.pointer!r}")
    return problems


def unresolved_report(entries: list[GoldJoinEntry]) -> list[dict]:
    """Pointers still needing a human -- for a future gold_pointer_overrides
    entry, not a guess. One row per NO_MATCH/UNRESOLVED entry.
    """
    return [
        {
            "pointer": entry.pointer, "label": entry.label, "english": entry.english,
            "provenance": entry.provenance, "candidate_qids": list(entry.candidate_qids),
        }
        for entry in entries if entry.provenance in (NO_MATCH, UNRESOLVED)
    ]


def to_aligned_rows(entries: list[GoldJoinEntry], target_lang: str = "fr") -> list[dict]:
    """Serialize entries into the flat aligned.json shape pipeline/cli.py's
    ``validate``/``generate`` commands already read (see the loader at
    pipeline/cli.py:129-140) -- so Gold's join flows through the existing
    release gate unchanged, with ``qid`` set to the pointer (the actual
    join/collision key here, unlike RBY's corpus qid).
    """
    return [
        {
            "qid": entry.pointer, "game": "gold", "english": entry.english,
            "translation": entry.translation, "target_lang": target_lang,
            "method": entry.provenance,
        }
        for entry in entries
    ]


def gold_coverage_report(entries: list[GoldJoinEntry]) -> dict:
    """The modkit-join-coverage shape pipeline/validate.py:release_gate
    expects. ``rom`` is the ROM-pointer join release_gate actually gates
    on; ``unmatched``/``ambiguous`` list the pointers a human still needs
    to look at, keyed by provenance so their cause stays visible in the
    report rather than collapsing into one undifferentiated bucket.
    """
    total = len(entries)
    translated = sum(1 for entry in entries if entry.translation is not None)
    unmatched = {
        entry.pointer: [entry.english]
        for entry in entries if entry.provenance == NO_MATCH
    }
    ambiguous = {
        entry.pointer: list(entry.candidate_qids)
        for entry in entries if entry.provenance == UNRESOLVED
    }
    return {
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "rom": {
            "translated": translated, "total": total,
            "percent": round(100.0 * translated / total, 2) if total else 0.0,
        },
    }


def gold_charmap(rom_manifest: Mapping) -> dict[str, int]:
    """{glyph: byte} for pipeline/validate.py's ``glyphs`` check, from
    tools/rom_manifest_gold.json's ``charmap`` -- published upstream but,
    until this, never actually wired to anything downstream.

    rom_manifest_gold.json's charmap is inverted ({byte-as-string: glyph})
    and includes multi-character control names ("<BOLD_E>") alongside
    single printable glyphs. Only the single-character entries are glyphs
    pipeline/validate.py's per-character check can ever see: the
    multi-character ones are bracket-wrapped tokens TOKEN_RE strips from
    the text before that check runs (pipeline/validate.py:23).
    """
    charmap = rom_manifest.get("charmap")
    if not isinstance(charmap, Mapping):
        raise ValueError("rom manifest has no charmap table")
    return {glyph: int(byte) for byte, glyph in charmap.items() if len(glyph) == 1}
