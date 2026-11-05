"""Gen 2 engine text the runtime reads by cart label (the `rom_text` registry).

Since gen1recomp v0.4.0 the Gen 2 battle prints most of its messages from
the cart's own text, looked up by pokegold/pokecrystal label in data.text
(`Battle:romText`, src/core/RomText.lua), instead of a `Strings()` literal.
`mod.content.rom_text:override(label, value)` is the only public route to
data.text on Gen 2 (`content.text` targets gen2Text, the script text keyed by
bank:address), so a translation ships these lines as a separate catalog.

The scope is computed from the pinned engine, the same way the engine-string
scope is: every label of the extracted rom_text catalog
(tools/gsc/extract.lua's gs_rom_text.tsv) named in the code of an engine file
config/gsc/rom_text_scope.json lists as a data.text reader.  Each label joins
the corpus row of the same name (`gs.<file>.<label>` on Gold/Silver,
`c.<file>.<label>` on Crystal) unless that file anchors it elsewhere for the
language.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from ..shared.tokens import DYNAMIC_TOKEN_RE, corpus_to_engine


ROM_TEXT_SCOPE_SCHEMA = "gen1recomp-translation-mods/gs-rom-text-scope"
SLOT_POLICIES = frozenset({"named", "exact"})
# Battle:romText always passes both battlers, so a `named` translation may
# name either one even where the English line does not.
NAMED_BATTLER_TOKENS = frozenset({"{USER}", "{TARGET}"})
# What the box does at the end of a line ({PROMPT}: wait for A; {DONE}: close,
# after a final paragraph break on some lines).  The translation keeps the
# English line's ending verbatim, so every reader behaves as without a mod.
_ENDING_RE = re.compile(r"[\n\v\f]*\{(?:PROMPT|DONE)\}$")
_LUA_BLOCK_COMMENT_RE = re.compile(r"--\[(=*)\[.*?\]\1\]", re.S)
_LUA_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A pret control code corpus_to_engine has no expansion for.
_UNCONVERTED_RE = re.compile(r"<[A-Z_!?#]+>|\{text_|@")
# How a corpus row ends a message.
_TERMINATOR_RE = re.compile(r"<(?:PROMPT|DONE)>")


def default_scope_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "gsc" / "rom_text_scope.json"


@dataclass(frozen=True)
class RomTextScope:
    readers: dict[str, str]
    anchors: dict[str, dict[str, str]]


def load_rom_text_scope(path: str | Path | None = None) -> RomTextScope:
    data = json.loads(Path(path or default_scope_path()).read_text(encoding="utf-8"))
    if data.get("schema") != ROM_TEXT_SCOPE_SCHEMA or data.get("version") != 1:
        raise ValueError("unsupported rom_text scope schema")
    readers = data.get("readers")
    if not isinstance(readers, dict) or not readers:
        raise ValueError("rom_text scope requires a non-empty readers object")
    policies: dict[str, str] = {}
    for reader, row in readers.items():
        if (not isinstance(reader, str) or not reader.startswith("src/")
                or not isinstance(row, dict) or row.get("slots") not in SLOT_POLICIES
                or not isinstance(row.get("why"), str) or not row["why"]):
            raise ValueError(f"invalid rom_text reader {reader!r}")
        policies[reader] = row["slots"]
    anchors: dict[str, dict[str, str]] = {}
    for language, rows in (data.get("anchors") or {}).items():
        if not isinstance(rows, dict):
            raise ValueError(f"invalid rom_text anchors for {language!r}")
        for label, row in rows.items():
            if (not isinstance(row, dict) or not isinstance(row.get("qid"), str)
                    or not row["qid"].rsplit(".", 1)[-1]
                    or not isinstance(row.get("why"), str) or not row["why"]):
                raise ValueError(f"invalid rom_text anchor {language}/{label}")
            anchors.setdefault(language, {})[label] = row["qid"]
    return RomTextScope(policies, anchors)


def parse_rom_text_catalog(path: str | Path) -> dict[str, str]:
    """Parse ``label<TAB>escaped text`` emitted by ``tools/gsc/extract.lua``."""
    result: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[0] or fields[0] in result:
            raise ValueError(f"malformed ROM-text catalog row: {line!r}")
        escaped = fields[1]
        out: list[str] = []
        index = 0
        while index < len(escaped):
            if escaped[index] != "\\":
                out.append(escaped[index])
                index += 1
                continue
            if index + 1 >= len(escaped) or escaped[index + 1] not in {"\\", "n", "r", "t"}:
                raise ValueError(f"invalid escape in ROM-text row for {fields[0]!r}")
            out.append({"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}[escaped[index + 1]])
            index += 2
        result[fields[0]] = "".join(out)
    return result


def _reader_files(source: Path, reader: str) -> list[Path]:
    path = source.parent / reader if source.name == "src" else source / reader
    if path.is_dir():
        return sorted(path.rglob("*.lua"))
    if path.is_file():
        return [path]
    raise ValueError(f"rom_text reader {reader!r} is not in the engine checkout")


def rom_text_scope(
    source: str | Path, labels: Iterable[str], scope: RomTextScope | None = None,
) -> dict[str, str]:
    """{label: slot policy} for the extracted labels the readers' code names.

    ``source`` is the engine checkout (or its ``src`` directory).  Comments
    are stripped first: the battle code cites pret labels in comments for
    lines it still prints through ``Strings()``.  A label named by readers of
    both policies is held to ``exact``.
    """
    scope = scope or load_rom_text_scope()
    source = Path(source)
    wanted = set(labels)
    result: dict[str, str] = {}
    for reader, policy in scope.readers.items():
        for path in _reader_files(source, reader):
            code = _LUA_BLOCK_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
            code = _LUA_LINE_COMMENT_RE.sub("", code)
            for label in set(_IDENTIFIER_RE.findall(code)) & wanted:
                if result.get(label) != "exact":
                    result[label] = policy
    return dict(sorted(result.items()))


def _split_ending(text: str) -> tuple[str, str]:
    match = _ENDING_RE.search(text)
    if not match:
        return text, ""
    return text[:match.start()], match.group(0)


def _tokens(text: str) -> list[str]:
    return DYNAMIC_TOKEN_RE.findall(text)


def _slots_fit(source: str, target: str, policy: str) -> bool:
    source_tokens, target_tokens = _tokens(source), _tokens(target)
    if policy == "exact":
        return source_tokens == target_tokens
    return set(target_tokens) <= set(source_tokens) | NAMED_BATTLER_TOKENS


def _run_on(target: str, qid: str, ordered: list[tuple[str, str, str]],
            position: Mapping[str, int]) -> str:
    """A row with no end of message runs on into the next row of its file.

    The Japanese carts print some battle lines as a "しかし　" (but) row
    with no prompt, which the cart's text runs straight into the row after
    it (AttackMissedText into AttackMissed2Text, ButItFailedText into
    ItFailedText): the line it shows is the two rows together.
    """
    file = qid.rsplit(".", 1)[0]
    index = position[qid]
    while (target.endswith("@") and not _TERMINATOR_RE.search(target)
           and index + 1 < len(ordered)):
        index += 1
        following = ordered[index]
        if following[0].rsplit(".", 1)[0] != file:
            break
        target = target.rstrip("@") + following[2].removeprefix("{text_start}")
    return target


def join_rom_text(
    extracted: Mapping[str, str],
    policies: Mapping[str, str],
    corpus_rows: Iterable[tuple[str, str, str]] | None,
    language: str,
    qid_prefix: str,
    scope: RomTextScope | None = None,
) -> tuple[dict[str, str], dict]:
    """Translate the in-scope labels from the corpus rows of the same name.

    ``qid_prefix`` is the corpus namespace (``gs.`` or ``c.``).  A label
    whose row reads the same as the English, or that no usable row
    translates, keeps the cart's English and is not shipped.
    """
    scope = scope or load_rom_text_scope()
    anchors = scope.anchors.get(language, {})
    total = len(policies)
    if corpus_rows is None:
        return {}, {
            "translated": 0, "total": total, "percent": 0.0, "shipped": 0,
            "fallback_english": total, "unmatched": sorted(policies),
            "details": {label: "no_corpus" for label in policies},
            "policy": "english-fallback",
        }
    ordered = [row for row in corpus_rows if row[0].startswith(qid_prefix)]
    by_name: dict[str, list[tuple[str, str, str]]] = {}
    position: dict[str, int] = {}
    for index, row in enumerate(ordered):
        by_name.setdefault(row[0].rsplit(".", 1)[-1], []).append(row)
        position[row[0]] = index
    catalog: dict[str, str] = {}
    details: dict[str, str] = {}
    identical = 0
    for label, policy in policies.items():
        source_body, ending = _split_ending(extracted[label])
        if label in anchors:
            index = position.get(anchors[label])
            candidates = [] if index is None else [ordered[index]]
        else:
            candidates = by_name.get(label.lstrip("_"), [])
            if len(candidates) > 1:
                # The same label in two pret files: keep the row whose
                # English is the line this cart prints.
                candidates = [
                    row for row in candidates
                    if corpus_to_engine(row[1], bare_dynamic_tokens=True) == source_body
                ]
        if len(candidates) != 1:
            details[label] = "no_row" if not candidates else "ambiguous"
            continue
        qid, _english, target = candidates[0]
        target = _run_on(target, qid, ordered, position)
        # The English line's own ending (its breaks before {PROMPT} included)
        # is appended below, so the row's trailing breaks go.
        value = corpus_to_engine(target, bare_dynamic_tokens=True).rstrip("\n\v\f")
        if not value.strip() or _UNCONVERTED_RE.search(value):
            details[label] = "unconverted"
            continue
        if not _slots_fit(source_body, value, policy):
            details[label] = "slots_mismatch"
            continue
        if value == source_body.rstrip("\n\v\f"):
            details[label] = qid
            identical += 1
            continue
        catalog[label] = value + ending
        details[label] = qid
    translated = len(catalog) + identical
    return catalog, {
        "translated": translated,
        "total": total,
        "percent": round(100.0 * translated / total, 2) if total else 100.0,
        "shipped": len(catalog),
        "fallback_english": total - translated,
        "unmatched": sorted(label for label, detail in details.items()
                            if detail in {"no_row", "ambiguous", "unconverted", "slots_mismatch"}),
        "details": details,
        "policy": "english-fallback",
    }
