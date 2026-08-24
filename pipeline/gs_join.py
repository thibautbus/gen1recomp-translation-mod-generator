"""Join the GoldSilver corpus to Gold ROM text pointers.

Builds the worksheet on top of the measured normalised-English strategy.

Nothing here is guessed: an entry that cannot be resolved automatically, or
by a human-reviewed override, keeps its English text and is reported, never
silently translated by a low-confidence pick.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .gs_text import GsTextRecord, normalise, split_lines
from .tokens import DYNAMIC_TOKEN_RE, TOKEN_RE, check_placeholders, corpus_to_engine, known_literal_tokens

# Provenance values a GsJoinEntry can carry.
UNIQUE = "unique"
HARMLESS_AMBIGUOUS = "harmless_ambiguous"
OVERRIDE = "override"
REVIEWED_QID = "reviewed_qid"
UNRESOLVED = "unresolved"
NO_MATCH = "no_match"
MARKUP_ONLY = "markup_only"

# A token TOKEN_RE can find that is not a problem in shipped output: it was
# either converted away (absent from the translation because
# _CORPUS_EXPANSIONS maps it to "" or plain text) or is a legitimate runtime
# substituent DYNAMIC_TOKEN_RE recognises (checked separately, by identity,
# not just "known").
_KNOWN_LITERAL_TOKENS = known_literal_tokens()
GS_POINTER_DECISIONS_SCHEMA = "gen1recomp-translation-mods/gs-pointer-decisions"
GS_PLACEHOLDER_DECISIONS_SCHEMA = "gen1recomp-translation-mods/gs-placeholder-decisions"
GOLD_SILVER_POINTER_ALIASES_SCHEMA = "gen1recomp-translation-mods/gold-silver-pointer-aliases"


@dataclass(frozen=True)
class GsPlaceholderDecision:
    qid: str
    errors: frozenset[str]


def load_gs_pointer_decisions(path: str | Path | None = None) -> dict[str, str]:
    """Load reviewed Gold pointer-to-corpus-QID decisions.

    Decisions select corpus rows rather than carrying translated prose, so one
    review applies consistently to every target language.
    """
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gs" / "pointer_decisions.json"
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Gold pointer decisions JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != GS_POINTER_DECISIONS_SCHEMA:
        raise ValueError("unsupported Gold pointer decisions schema")
    if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
        raise ValueError("Gold pointer decisions require version 1 entries")
    result: dict[str, str] = {}
    for pointer, row in data["entries"].items():
        if not re.fullmatch(r"[0-7][0-9a-f]:[0-7][0-9a-f]{3}", pointer):
            raise ValueError(f"invalid Gold pointer decision key: {pointer!r}")
        if (not isinstance(row, dict) or set(row) != {"qid", "symbol"} or
                not isinstance(row.get("qid"), str) or not row["qid"].startswith("gs.") or
                not isinstance(row.get("symbol"), str) or not row["symbol"]):
            raise ValueError(f"invalid Gold pointer decision for {pointer!r}")
        result[pointer] = row["qid"]
    return result


def load_gs_placeholder_decisions(
    language: str,
    path: str | Path | None = None,
) -> dict[str, GsPlaceholderDecision]:
    """Load exact, reviewed placeholder differences in official localizations."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gs" / "placeholder_decisions.json"
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Gold placeholder decisions JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != GS_PLACEHOLDER_DECISIONS_SCHEMA:
        raise ValueError("unsupported Gold placeholder decisions schema")
    if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
        raise ValueError("Gold placeholder decisions require version 1 entries")
    language_entries = data["entries"].get(language, {})
    if not isinstance(language_entries, dict):
        raise ValueError(f"invalid Gold placeholder decisions for language {language!r}")
    result: dict[str, GsPlaceholderDecision] = {}
    for pointer, row in language_entries.items():
        if not re.fullmatch(r"[0-7][0-9a-f]:[0-7][0-9a-f]{3}", pointer):
            raise ValueError(f"invalid Gold placeholder decision key: {pointer!r}")
        if (not isinstance(row, dict) or set(row) != {"qid", "errors", "reason"} or
                not isinstance(row.get("qid"), str) or not row["qid"].startswith("gs.") or
                not isinstance(row.get("errors"), list) or not row["errors"] or
                not all(isinstance(item, str) and item for item in row["errors"]) or
                len(set(row["errors"])) != len(row["errors"]) or
                not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise ValueError(f"invalid Gold placeholder decision for {language}:{pointer}")
        result[pointer] = GsPlaceholderDecision(row["qid"], frozenset(row["errors"]))
    return result


def load_gold_silver_pointer_aliases(path: str | Path | None = None) -> dict[str, str]:
    """Load {gold_pointer: silver_pointer} for text confirmed identical in both.

    The handful of Gold dialogue pointers whose bank:address shifts in Silver
    (see tools/spike_gold_silver_text_overlap.lua's measurement and
    docs/upstream-fixes.md's "Silver: supported by declaration") but whose
    text doesn't -- so a Gold pointer's own resolved translation can be
    reused verbatim under its Silver pointer too, rather than left to
    silently miss on a Silver save.
    """
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gs" / "silver_pointer_aliases.json"
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Gold/Silver pointer aliases JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != GOLD_SILVER_POINTER_ALIASES_SCHEMA:
        raise ValueError("unsupported Gold/Silver pointer aliases schema")
    if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
        raise ValueError("Gold/Silver pointer aliases require version 1 entries")
    pointer_re = re.compile(r"[0-7][0-9a-f]:[0-7][0-9a-f]{3}")
    result: dict[str, str] = {}
    for gold_pointer, row in data["entries"].items():
        if not pointer_re.fullmatch(gold_pointer):
            raise ValueError(f"invalid Gold pointer in Gold/Silver alias key: {gold_pointer!r}")
        if (not isinstance(row, dict) or "silver_pointer" not in row or
                not isinstance(row.get("silver_pointer"), str) or
                not pointer_re.fullmatch(row["silver_pointer"]) or
                not isinstance(row.get("label"), str) or not row["label"]):
            raise ValueError(f"invalid Gold/Silver pointer alias for {gold_pointer!r}")
        result[gold_pointer] = row["silver_pointer"]
    return result


def _token_aware_key(value: str) -> tuple[tuple[str, str], ...]:
    """Normalize prose while preserving every command token's SHIPPED spelling.

    ``gs_text.normalise`` intentionally erases braces and punctuation for
    the English lookup.  That is unsafe for ambiguity resolution: two corpus
    rows can have the same prose but different sound/RAM commands.  This key
    only folds prose segments and keeps token spelling/ordering visible.

    Each token is put through ``corpus_to_engine(..., bare_dynamic_tokens=True)``
    -- the same conversion the candidate's translation itself gets before
    shipping (this module is Gold-only) -- rather than compared by raw corpus
    spelling.  Without it, two rows differing only in which numbered
    ``{text_ram wStringBufferN}`` they name looked like a genuine content
    difference and fell to UNRESOLVED, even though gen1recomp's Gold decoder
    never names the buffer either way (RomExtractorGen2.lua:decodeGen2Text),
    so both rows produce the byte-identical bare ``{STRBUF}`` once shipped.
    """
    parts: list[tuple[str, str]] = []
    position = 0
    for match in TOKEN_RE.finditer(value):
        prose = normalise(value[position:match.start()])
        if prose:
            parts.append(("text", prose))
        parts.append(("token", corpus_to_engine(match.group(0), bare_dynamic_tokens=True)))
        position = match.end()
    prose = normalise(value[position:])
    if prose:
        parts.append(("text", prose))
    return tuple(parts)


@dataclass(frozen=True)
class GsJoinEntry:
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


def join_gs_pointers(
    records: list[GsTextRecord],
    corpus_rows: list[tuple[str, str, str]],
    overrides: Mapping[str, str] | None = None,
    qid_decisions: Mapping[str, str] | None = None,
) -> tuple[list[GsJoinEntry], dict]:
    """Join Gold's pointer catalog to the corpus by normalised English.

    ``qid_decisions`` selects a reviewed corpus row while retaining its
    multilingual text and provenance. ``overrides`` remains the higher-priority
    escape hatch for pointer-specific prose that cannot come from the corpus.
    Everything else uses conservative automatic matching or stays English.
    """
    overrides = overrides or {}
    qid_decisions = qid_decisions or {}

    corpus_by_qid: dict[str, tuple[str, str]] = {}
    for qid, en, target in corpus_rows:
        if qid in corpus_by_qid:
            raise ValueError(f"duplicate Gold corpus qid: {qid!r}")
        corpus_by_qid[qid] = (en, target)

    by_english: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for qid, en, fr in corpus_rows:
        if fr.strip() and corpus_to_engine(fr, bare_dynamic_tokens=True):
            by_english[normalise(en)].append((qid, fr))

    entries: list[GsJoinEntry] = []
    stats = {
        "total": len(records), "unique": 0, "harmless_ambiguous": 0,
        "override": 0, "reviewed_qid": 0, "unresolved": 0, "no_match": 0,
        "markup_only": 0,
    }

    for record in records:
        pointer, english, label = record.pointer, record.text, record.label
        norm = normalise(english)

        if pointer in overrides:
            translation = corpus_to_engine(overrides[pointer], bare_dynamic_tokens=True)
            if not overrides[pointer].strip() or not translation:
                raise ValueError(f"empty Gold pointer override for {pointer!r}")
            entries.append(GsJoinEntry(pointer, label, english, translation, OVERRIDE))
            stats["override"] += 1
            continue

        if pointer in qid_decisions:
            qid = qid_decisions[pointer]
            row = corpus_by_qid.get(qid)
            if row is None:
                raise ValueError(f"unknown Gold pointer decision qid {qid!r} for {pointer!r}")
            source, target = row
            if normalise(source) != norm:
                raise ValueError(
                    f"Gold pointer decision source mismatch for {pointer!r}: {qid!r}"
                )
            translation = corpus_to_engine(target, bare_dynamic_tokens=True)
            if not target.strip() or not translation:
                raise ValueError(f"empty Gold pointer decision target for {pointer!r}: {qid!r}")
            entries.append(GsJoinEntry(pointer, label, english, translation, REVIEWED_QID, qid))
            stats["reviewed_qid"] += 1
            continue

        if not norm:
            entries.append(GsJoinEntry(pointer, label, english, None, MARKUP_ONLY))
            stats["markup_only"] += 1
            continue

        candidates = by_english.get(norm)
        if not candidates:
            entries.append(GsJoinEntry(pointer, label, english, None, NO_MATCH))
            stats["no_match"] += 1
            continue

        if len(candidates) == 1:
            qid, fr = candidates[0]
            entries.append(GsJoinEntry(pointer, label, english, corpus_to_engine(fr, bare_dynamic_tokens=True), UNIQUE, qid))
            stats["unique"] += 1
            continue

        distinct_french = {_token_aware_key(fr) for _, fr in candidates}
        if len(distinct_french) == 1:
            qid, fr = candidates[0]
            entries.append(GsJoinEntry(pointer, label, english, corpus_to_engine(fr, bare_dynamic_tokens=True), HARMLESS_AMBIGUOUS,
                                          qid, tuple(sorted(c for c, _ in candidates))))
            stats["harmless_ambiguous"] += 1
            continue

        entries.append(GsJoinEntry(pointer, label, english, None, UNRESOLVED,
                                      candidate_qids=tuple(sorted(c for c, _ in candidates))))
        stats["unresolved"] += 1

    return entries, stats


def audit_join(
    entries: list[GsJoinEntry],
    placeholder_decisions: Mapping[str, GsPlaceholderDecision] | None = None,
) -> list[str]:
    """Gate checks: no pointer collisions, no unknown token in any shipped
    translation, and no dropped/swapped runtime substitution.  Unresolved
    content is represented separately in the coverage report.
    """
    placeholder_decisions = placeholder_decisions or {}
    problems: list[str] = []
    seen: set[str] = set()
    used_decisions: dict[str, set[str]] = defaultdict(set)
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
        decision = placeholder_decisions.get(entry.pointer)
        if decision is not None and decision.qid != entry.qid:
            problems.append(
                f"placeholder decision qid mismatch for {entry.pointer!r}: "
                f"expected {decision.qid!r}, got {entry.qid!r}"
            )
            decision = None
        for message in check_placeholders(entry.english, entry.translation):
            if decision is not None and message in decision.errors:
                used_decisions[entry.pointer].add(message)
            else:
                problems.append(f"{message} in translation for {entry.pointer!r}")
    for pointer, decision in sorted(placeholder_decisions.items()):
        unused = decision.errors - used_decisions[pointer]
        for message in sorted(unused):
            problems.append(f"unused placeholder decision {message!r} for {pointer!r}")
    return problems


def unresolved_report(entries: list[GsJoinEntry]) -> list[dict]:
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


def to_aligned_rows(entries: list[GsJoinEntry], target_lang: str = "fr") -> list[dict]:
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


def gs_coverage_report(entries: list[GsJoinEntry]) -> dict:
    """Report target-language coverage for user-visible pointer content.

    Markup-only records are reported separately: they contain no prose to
    translate and must neither inflate the translated count nor lower the
    user-visible coverage denominator.
    """
    content_entries = [entry for entry in entries if entry.provenance != MARKUP_ONLY]
    total = len(content_entries)
    translated = sum(1 for entry in content_entries if entry.translation)
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
        "ignored_markup_only": sum(1 for entry in entries if entry.provenance == MARKUP_ONLY),
        "rom": {
            "translated": translated, "total": total,
            "percent": round(100.0 * translated / total, 2) if total else 100.0,
        },
    }
