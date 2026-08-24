"""Gold's text catalog: bank:address pointers, not TEXT_* labels.

Parsed from tools/gs_extract.lua's gs_text.tsv/gs_labels.tsv output,
not from a real data/generated/text.lua: this project's Gold import
deliberately neuters RomExtractorGen2:write/:save (see
tools/gs_extract.lua's module docstring), so there is no such file on
disk to parse. gs_text.tsv IS the Gold equivalent of RBY's text.lua for
this pipeline's purposes -- see pipeline/yellow.py:parse_text_catalog for
the RBY-side parser this mirrors, adapted for Gold's pointer keys
(Schemas.GEN2.text routes to "gen2Text"; ids are ROM pointer strings like
"55:4067", not TEXT_* names -- docs/mod-api-gen2-compat.md).

Text only: this module carries no other registry (pokemon/moves/items/...
are out of this slice).

Also the single source for normalise()/unescape()/split_lines(): the
corpus<->pointer join primitives tools/measure_join.py measured this
backlog's go/no-go with, and pipeline/gs_join.py's join reuses verbatim
so the shipped join and the measurement stay the same code.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_TAG = re.compile(r"<[^>]*>")
_BRACE = re.compile(r"\{[^}]*\}")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def normalise(value: str) -> str:
    """Collapse a string to its comparable letters, for joining across the
    corpus's markup and the engine's own (verified against the real data):

    - markup differs between the corpus (<LINE>, {text_start}) and the
      engine (\\n, \\x0c), so both sides are stripped of markup entirely;
    - the "#MON" compression byte spells POKEMON and a bare "#" spells
      POKE;
    - NFKD folding before the alnum filter matters, not just cosmetics:
      the ROM text spells POKéMON with a literal accented e-acute while
      the corpus uses the "#MON" compression byte: without folding, the
      accent is dropped, leaving "pokmon", which matches nothing (841 of
      3044 pointers in the real catalog).
    """
    value = value.replace("#MON", "pokemon").replace("#", "poke")
    value = _TAG.sub(" ", value)
    value = _BRACE.sub(" ", value)
    value = unicodedata.normalize("NFKD", value)
    return _NON_ALNUM.sub("", value.lower())


def unescape(value: str) -> str:
    """Undo tools/gs_extract.lua's TSV escaping (\\\\, \\n, \\r, \\t)."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            escaped = value[index + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(escaped, escaped))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def split_lines(text: str) -> list[str]:
    """Split on "\\n" only.

    Never str.splitlines(): it also splits on "\\x0c", Gold's text-scroll
    control byte, silently turning one catalog record into several
    fragments. tools/measure_join.py documents the measured cost of this
    exact mistake (3044 records -> 8013 fragments).
    """
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


@dataclass(frozen=True)
class GsTextRecord:
    pointer: str          # "bank:address", e.g. "55:4067"
    text: str             # raw pret-syntax value, as poke-corpus writes it
    label: str | None = None  # resolved NAMED_TEXT label, if any


def parse_gs_text_catalog(
    text_tsv: str | Path, labels_tsv: str | Path | None = None,
) -> list[GsTextRecord]:
    """Parse gs_text.tsv (+ optional gs_labels.tsv) into records.

    Rejects ambiguous input rather than guessing:

    - a pointer repeated in gs_text.tsv with a DIFFERENT text raises;
      repeated with the SAME text is tolerated (a harmless duplicate line);
    - a label naming a pointer absent from gs_text.tsv raises: the
      labels and pointer tables have drifted, and silently dropping the
      label would hide that;
    - two different labels naming the same pointer raise, and the same
      label naming two different pointers also raises: NAMED_TEXT is
      documented as an allowlist of unambiguous names
      (RomExtractorGen2.lua:2786), so either collision means that
      assumption no longer holds and needs a human, not a guess at which
      name or pointer wins.

    Records are returned sorted by pointer, for a deterministic diff
    between runs.
    """
    pointer_text: dict[str, str] = {}
    for line in split_lines(Path(text_tsv).read_text(encoding="utf-8")):
        if not line:
            continue
        pointer, _, raw = line.partition("\t")
        text = unescape(raw)
        if pointer in pointer_text and pointer_text[pointer] != text:
            raise ValueError(f"ambiguous pointer {pointer!r}: text differs between occurrences")
        pointer_text[pointer] = text

    labels: dict[str, str] = {}
    if labels_tsv is not None and Path(labels_tsv).is_file():
        pointer_labels: dict[str, str] = {}
        label_pointers: dict[str, str] = {}
        for line in split_lines(Path(labels_tsv).read_text(encoding="utf-8")):
            if not line:
                continue
            label, _, pointer = line.partition("\t")
            if pointer not in pointer_text:
                raise ValueError(f"label {label!r} names pointer {pointer!r}, absent from the text catalog")
            if pointer in pointer_labels and pointer_labels[pointer] != label:
                raise ValueError(
                    f"ambiguous label for pointer {pointer!r}: {pointer_labels[pointer]!r} and {label!r}"
                )
            if label in label_pointers and label_pointers[label] != pointer:
                raise ValueError(
                    f"ambiguous pointer for label {label!r}: {label_pointers[label]!r} and {pointer!r}"
                )
            pointer_labels[pointer] = label
            label_pointers[label] = pointer
            labels[pointer] = label

    return [
        GsTextRecord(pointer=pointer, text=text, label=labels.get(pointer))
        for pointer, text in sorted(pointer_text.items())
    ]
