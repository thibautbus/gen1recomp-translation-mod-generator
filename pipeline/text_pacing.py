"""Font-aware pacing for the optional line-break reflow.

Mirrors Gen1Recomp's own text box wrap so the "optimized" line-break mode
still pauses roughly every two lines instead of running text on until the
next real page break.  The budget and cut rule are ported from the engine:

- ``MAX_COLS = 18`` and the per-line pixel budget (``MAX_COLS * 8``) come
  from gen1recomp's ``src/render/TextBox.lua``/``src/ui/Theme.lua``.
- The greedy fill-then-cut-at-last-space rule mirrors
  ``Font.spansFitting``/``TextBox.paginate``'s ``pushLine`` in
  ``src/render/Font.lua``/``src/render/TextBox.lua``: advance widths come
  from the same TTF the mod ships, so a proportional font (Fusion Pixel)
  fits noticeably more per line than a fixed-width one (Pokemon Font).

Runtime placeholders (``{PLAYER}`` etc.) can't be measured directly: the
substituted value isn't known until the player actually picks a name, or
the ROM buffer it reads from isn't known until the game runs. every one
of them is priced at a worst case instead: the game's own max length for
that field (NamingScreen.lua's ``maxLen`` -- 7 for a trainer, 10 for a
Pokémon nickname, 5 fixed digits for a trainer ID) times the widest
single letter glyph in the font; ``{RAM:...}``/``{NUM:...}`` (an item,
move, or species name pulled from RAM, with no maxLen to read) fall back
to Gen1's own 12-character text-data convention.  No valid substitution
can measure wider than that, so a page never needs a 3rd wrapped line no
matter what the player typed or the ROM buffer holds -- deliberately
chosen over a narrower "typical" estimate (e.g. the font's average
letter width), which packs better for the common case but can't rule out
a wide-lettered name pushing a two-line page to three.
"""
from __future__ import annotations

import string
from functools import lru_cache
from pathlib import Path

from .tokens import DYNAMIC_TOKEN_RE

TEXTBOX_MAX_COLS = 18
TEXTBOX_LINE_BUDGET_PX = TEXTBOX_MAX_COLS * 8

# NamingScreen.lua: opts.maxLen defaults to 7 for a trainer (player/rival);
# Pokémon nicknames (BattleState.lua/Commands.lua askNicknameUI) use 10;
# the trainer ID is always exactly 5 digits (src/ui/TradeAnim.lua/
# SummaryMenu.lua: ("%05d"):format(...)).
_NAME_TOKEN_MAX_LEN = {"PLAYER": 7, "RIVAL": 7, "TARGET": 10, "USER": 10, "ID": 5}

# {RAM:...}/{NUM:...} (and bare {RAM}) substitute whatever the ROM buffer
# holds -- an item, move, or species name, with no single source to read a
# maxLen from.  Gen1's own text data conventionally caps those at 12
# characters (e.g. "HYPER POTION", "THUNDERSTONE", "SELFDESTRUCT"); used as
# the same kind of worst-case bound as the named tokens above rather than
# falling back to the literal "{RAM:wStringBuffer}" token text, which is
# neither a real bound nor representative of what's actually substituted.
_DEFAULT_TOKEN_MAX_LEN = 12


@lru_cache(maxsize=None)
def _load_font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


@lru_cache(maxsize=None)
def _widest_letter_width(path: str, size: int) -> float:
    font = _load_font(path, size)
    return max(font.getlength(letter) for letter in string.ascii_letters)


def _spans(text: str) -> list[tuple[int, str]]:
    """(end_offset, span_text) pairs: a whole ``{TOKEN}`` is one span (a
    cut can fall before or after it, never inside it), everything else is
    one character per span -- mirrors the engine's own ``Font.split``."""
    spans: list[tuple[int, str]] = []
    pos = 0
    for match in DYNAMIC_TOKEN_RE.finditer(text):
        for index in range(pos, match.start()):
            spans.append((index + 1, text[index]))
        spans.append((match.end(), match.group(0)))
        pos = match.end()
    for index in range(pos, len(text)):
        spans.append((index + 1, text[index]))
    return spans


def _span_width(span_text: str, font, widest_letter_px: float) -> float:
    if span_text.startswith("{") and span_text.endswith("}"):
        name = span_text[1:-1].split(":")[0]
        max_len = _NAME_TOKEN_MAX_LEN.get(name, _DEFAULT_TOKEN_MAX_LEN)
        return widest_letter_px * max_len
    return font.getlength(span_text)


def _fit_one_line(text: str, font, budget_px: float, widest_letter_px: float) -> int:
    """Return the cut point (character index) for one line: how much of
    ``text`` fits in ``budget_px``, preferring the last space over
    splitting a word.  Mirrors ``Font.spansFitting`` + ``pushLine``'s
    space search, treating a ``{PLACEHOLDER}`` as one unbreakable span."""
    spans = _spans(text)
    used = 0.0
    fit = 0
    for span_end, span_text in spans:
        used += _span_width(span_text, font, widest_letter_px)
        if used > budget_px:
            break
        fit += 1
    if fit >= len(spans):
        return len(text)
    fit = max(fit, 1)
    cut = spans[fit - 1][0]
    for index in range(fit - 1, -1, -1):
        if spans[index][1] == " ":
            return spans[index][0]
    return cut


def paginate_for_pacing(text: str, font_path: str | Path, font_size: int, lines_per_page: int = 2) -> str:
    """Insert ``\\f`` every ``lines_per_page`` greedily-wrapped lines.

    Only splits within the segments already delimited by ``\\f`` in
    ``text``; never introduces a break inside a genuine page marker.  Call
    after :func:`reflow_for_display`, once ``\\n``/``\\v`` have already
    become spaces -- this function assumes there are none left to handle.
    """
    font = _load_font(str(font_path), font_size)
    widest_letter_px = _widest_letter_width(str(font_path), font_size)
    return "\f".join(
        _paginate_page(page, font, widest_letter_px, lines_per_page)
        for page in text.split("\f")
    )


def _paginate_page(page: str, font, widest_letter_px: float, lines_per_page: int) -> str:
    chunks: list[str] = []
    remaining = page
    while remaining:
        cursor = remaining
        consumed = 0
        for _ in range(lines_per_page):
            if not cursor:
                break
            cut = _fit_one_line(cursor, font, TEXTBOX_LINE_BUDGET_PX, widest_letter_px)
            consumed += cut
            cursor = cursor[cut:]
        chunk = remaining[:consumed].strip(" ")
        remaining = remaining[consumed:]
        if chunk:
            chunks.append(chunk)
    return "\f".join(chunks)
