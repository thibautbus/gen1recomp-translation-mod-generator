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

This is a pacing aid, not a promise of pixel-identical wrapping: runtime
placeholders (``{PLAYER}`` etc.) are measured as their literal token text
since the substituted value isn't known ahead of time -- the same
uncertainty the engine itself has at pagination time.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

TEXTBOX_MAX_COLS = 18
TEXTBOX_LINE_BUDGET_PX = TEXTBOX_MAX_COLS * 8


@lru_cache(maxsize=None)
def _load_font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def _fit_one_line(text: str, font, budget_px: float) -> int:
    """Return the cut point (character index) for one line: how much of
    ``text`` fits in ``budget_px``, preferring the last space over splitting
    a word.  Mirrors ``Font.spansFitting`` + ``pushLine``'s space search."""
    used = 0.0
    fit = 0
    for index, char in enumerate(text):
        used += font.getlength(char)
        if used > budget_px:
            break
        fit = index + 1
    if fit >= len(text):
        return len(text)
    fit = max(fit, 1)
    for index in range(fit, 0, -1):
        if text[index - 1] == " ":
            return index
    return fit


def paginate_for_pacing(text: str, font_path: str | Path, font_size: int, lines_per_page: int = 2) -> str:
    """Insert ``\\f`` every ``lines_per_page`` greedily-wrapped lines.

    Only splits within the segments already delimited by ``\\f`` in
    ``text``; never introduces a break inside a genuine page marker.  Call
    after :func:`reflow_for_display`, once ``\\n``/``\\v`` have already
    become spaces -- this function assumes there are none left to handle.
    """
    font = _load_font(str(font_path), font_size)
    return "\f".join(
        _paginate_page(page, font, lines_per_page)
        for page in text.split("\f")
    )


def _paginate_page(page: str, font, lines_per_page: int) -> str:
    chunks: list[str] = []
    remaining = page
    while remaining:
        cursor = remaining
        consumed = 0
        for _ in range(lines_per_page):
            if not cursor:
                break
            cut = _fit_one_line(cursor, font, TEXTBOX_LINE_BUDGET_PX)
            consumed += cut
            cursor = cursor[cut:]
        chunk = remaining[:consumed].strip(" ")
        remaining = remaining[consumed:]
        if chunk:
            chunks.append(chunk)
    return "\f".join(chunks)
