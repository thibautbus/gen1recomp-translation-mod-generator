"""Yellow support: the versioned dialogue layer for the universal mod.

Gen1Recomp runs one codebase across Red, Blue, and Yellow; the ROM data the
engine loads at runtime differs per game.  The Red/Blue dialogue catalog
(``lang/dialogue.lua``) is built from the Red import, so it is only correct
for labels whose English content is identical in Yellow.  This module builds
the supplementary ``lang/dialogue_yellow.lua`` layer:

- **shared-safe** labels (same English and same localized translation in Red
  and Yellow) are skipped: the Red/Blue translation already applies;
- **translation-variant** labels (same English, different localized text)
  are emitted in the Yellow layer;
- **versioned-required** labels (same label, different English in Yellow) and
  **yellow-only** labels (absent from Red) are translated from the Yellow
  corpus and emitted in the Yellow layer, which ``main.lua`` applies after the
  common catalog when ``GameVersion.isYellow()``.

A Yellow translation never silently overwrites a Red/Blue one: the layer only
contains labels whose English differs from (or is absent in) Red, and labels
without a Yellow corpus match are dropped (English fallback) instead of
reusing the Red translation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .join import WorksheetEntry, join_catalogs
from .model import Alignment

_KEY = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*\"((?:[^\"\\]|\\.)*)\"\s*,?\s*$")
_ESCAPES = {"a": "\a", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v", '"': '"', "'": "'", "\\": "\\"}


def parse_text_catalog(path: str | Path) -> dict[str, str]:
    """Parse an imported ``data/generated/text.lua`` into ``{label: content}``.

    Keys are Lua identifiers; values are double-quoted Lua strings with the
    engine's ``\\n``/``\\f``/``\\v`` escapes and ``{RAM:...}`` tokens kept.
    """
    catalog: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _KEY.match(line)
        if not match:
            continue
        raw = match.group(2)
        # Single-pass Lua escape decoding: named escapes, then \xNN hex and
        # \ddd decimal byte escapes.  Unknown escapes keep the backslash
        # verbatim (a future import change then shows up as a literal
        # backslash in the catalog, not as a misclassified label).  A doubled
        # backslash (\\x41) decodes to a literal backslash first, so the
        # following "x41" is plain text — escapes are only decoded from the
        # single backslash that introduces them.
        out: list[str] = []
        index = 0
        while index < len(raw):
            char = raw[index]
            if char != "\\" or index + 1 >= len(raw):
                out.append(char)
                index += 1
                continue
            escaped = raw[index + 1]
            if escaped in _ESCAPES:
                out.append(_ESCAPES[escaped])
                index += 2
            elif escaped == "x" and index + 3 < len(raw):
                digits = raw[index + 2:index + 4]
                if re.fullmatch(r"[0-9a-fA-F]{2}", digits):
                    out.append(chr(int(digits, 16)))
                    index += 4
                else:
                    out.append(char)
                    index += 1
            elif escaped.isdigit() and index + 4 <= len(raw):
                digits = raw[index + 1:index + 4]
                if digits.isdigit() and int(digits) <= 255:
                    out.append(chr(int(digits)))
                    index += 4
                else:
                    out.append(char)
                    index += 1
            else:
                out.append(char)
                index += 1
        catalog[match.group(1)] = "".join(out)
    return catalog


def yellow_dialogue_layer(
    red_text: dict[str, str],
    yellow_text: dict[str, str],
    rows: Iterable[Alignment],
    target_lang: str = "fr",
    red_translation: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Build ``lang/dialogue_yellow.lua`` contents.

    ``red_text``/``yellow_text`` are ``{label: English content}`` from the
    Red and Yellow imports.  ``rows`` are the aligned Yellow corpus records.
    When ``red_translation`` is supplied, a label with identical English is
    skipped only when its Yellow translation is also identical.  Localized
    Yellow releases sometimes reword a line without changing the English
    source text.

    Returns ``(dialogue_yellow, stats)``; ``dialogue_yellow`` maps label to
    translation for versioned-required and yellow-only labels.  A
    versioned-required label without a Yellow corpus match is mapped to the
    ROM's own Yellow content (the genuine English fallback) so the shared
    Red/Blue translation never overrides Yellow's different text; yellow-only
    labels without a match are omitted (the ROM text shows untouched).
    """
    yellow_entries = [
        WorksheetEntry(label, content, "dialogue")
        for label, content in yellow_text.items()
        if content
    ]
    joined, report = join_catalogs(list(rows), {"dialogue": yellow_entries}, target_lang=target_lang)
    yellow_fr = joined["dialogue"]
    stats = {
        "yellow_labels": len(yellow_text),
        "versioned_required": 0,
        "yellow_only": 0,
        "shared_safe": 0,
        "translation_variant": 0,
        "matched": 0,
        "unmatched": 0,
    }
    layer: dict[str, str] = {}
    unmatched_labels: list[str] = []
    for label, translation in yellow_fr.items():
        red_content = red_text.get(label)
        if red_content is not None and red_content == yellow_text.get(label):
            if red_translation is None or red_translation.get(label, "") == translation:
                stats["shared_safe"] += 1
                continue
            stats["translation_variant"] += 1
        if red_content is None:
            stats["yellow_only"] += 1
        else:
            stats["versioned_required"] += 1
        if translation:
            stats["matched"] += 1
            layer[label] = translation
        elif red_content is not None:
            # Versioned-required label with no Yellow corpus match: the
            # shared Red/Blue dialogue.lua would otherwise override Yellow's
            # different text with the Red translation.  Re-emit the ROM's own
            # Yellow content (the genuine English fallback) so Yellow keeps
            # its original text instead — the Red translation is never
            # reused.  Yellow-only labels without a match are not in
            # dialogue.lua and simply show the ROM text (nothing to do).
            stats["unmatched"] += 1
            unmatched_labels.append(label)
            layer[label] = yellow_text[label]
        else:
            stats["unmatched"] += 1
            unmatched_labels.append(label)
    stats["layer_entries"] = len(layer)
    stats["unmatched_labels"] = sorted(unmatched_labels)
    def report_count(name: str) -> int:
        value = report.get(name, {})
        if not isinstance(value, dict):
            return 0
        total = 0
        for entry in value.values():
            total += entry if isinstance(entry, int) else len(entry) if isinstance(entry, (list, tuple, set, dict)) else 0
        return total

    stats["join_report"] = {
        "matched": report_count("matched"),
        "unmatched": report_count("unmatched"),
        "ambiguous": report_count("ambiguous"),
    }
    return layer, stats
