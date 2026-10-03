"""FireRed start-menu labels, keyed by entry id.

The start menu prints its entry labels without ``Strings()``; the public
``ui.start_menu.items`` hook hands the entry list over first.  Emerald's
start menu reads the same labels from the cart (``RomText``), which the
named text join already covers.
"""
from __future__ import annotations

from typing import Mapping

from ..gen3.join import CatalogResult, Gen3Corpus, _plain
from ..gen3.text import PretCharmap


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


def join_start_menu(corpus: Gen3Corpus, charmap: PretCharmap) -> CatalogResult:
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
