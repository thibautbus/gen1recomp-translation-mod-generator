import tempfile
import unittest
from pathlib import Path

from pipeline.generate import display_value
from pipeline.text_pacing import TEXTBOX_LINE_BUDGET_PX, _fit_one_line, paginate_for_pacing

FUSION_LATIN = Path(".cache/dependencies/fusion-pixel-font/fusion-pixel-10px-proportional-latin.ttf")


class _FixedWidthFont:
    """8px-per-character stand-in: budget 144px -> exactly 18 chars/line,
    matching gen1recomp's vanilla MAX_COLS so test math stays simple."""

    def getlength(self, char: str) -> float:
        return 8.0


class TextPacingTests(unittest.TestCase):
    def test_fit_one_line_returns_full_length_when_it_all_fits(self):
        font = _FixedWidthFont()
        self.assertEqual(_fit_one_line("short line", font, TEXTBOX_LINE_BUDGET_PX), len("short line"))

    def test_fit_one_line_cuts_at_last_space_before_budget(self):
        font = _FixedWidthFont()
        # "hello " (6) + 20 "b"s: the 18-char budget lands mid-word, so the
        # cut must back up to the space at index 6, not hard-cut at 18.
        text = "hello " + "b" * 20
        cut = _fit_one_line(text, font, TEXTBOX_LINE_BUDGET_PX)
        self.assertEqual(text[:cut], "hello ")

    def test_fit_one_line_hard_cuts_a_single_long_word(self):
        font = _FixedWidthFont()
        text = "a" * 30
        cut = _fit_one_line(text, font, TEXTBOX_LINE_BUDGET_PX)
        self.assertEqual(cut, 18)

    def test_paginate_leaves_short_text_as_one_page(self):
        from pipeline.text_pacing import _paginate_page
        font = _FixedWidthFont()
        text = "short text under two lines"
        self.assertEqual(_paginate_page(text, font, lines_per_page=2), text)

    def test_paginate_inserts_page_break_after_two_lines(self):
        font = _FixedWidthFont()
        words = ["word{}".format(i) for i in range(20)]
        text = " ".join(words)
        # Bypass the TTF loader: call the private page function directly with the fake font.
        from pipeline.text_pacing import _paginate_page
        result = _paginate_page(text, font, lines_per_page=2)
        pages = result.split("\f")
        self.assertGreater(len(pages), 1)
        for page in pages:
            first_cut = _fit_one_line(page, font, TEXTBOX_LINE_BUDGET_PX)
            rest = page[first_cut:]
            second_cut = _fit_one_line(rest, font, TEXTBOX_LINE_BUDGET_PX)
            self.assertEqual(second_cut, len(rest), f"page has more than two lines: {page!r}")

    def test_paginate_never_splits_inside_an_existing_page_break(self):
        from pipeline.text_pacing import _paginate_page
        font = _FixedWidthFont()
        page_text = ("word " * 20).strip() + "\f" + "second page"
        result = "\f".join(_paginate_page(part, font, 2) for part in page_text.split("\f"))
        self.assertIn("\fsecond page", result)

    def test_display_value_falls_back_to_plain_reflow_on_bad_font_file(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_font = Path(directory) / "fake.ttf"
            bad_font.write_bytes(b"not a real font")
            result = display_value("Mon\nPOKéMON est\vplus fort.", True, bad_font, 10)
            self.assertEqual(result, "Mon POKéMON est plus fort.")

    @unittest.skipUnless(FUSION_LATIN.is_file(), "fusion pixel font not downloaded in .cache")
    def test_paginate_for_pacing_public_entrypoint_loads_the_real_font(self):
        text = "Mon POKéMON est plus fort."
        self.assertEqual(paginate_for_pacing(text, FUSION_LATIN, 10), text)

    @unittest.skipUnless(FUSION_LATIN.is_file(), "fusion pixel font not downloaded in .cache")
    def test_display_value_paces_with_the_real_fusion_font(self):
        text = ("BLUE: Minute, RED! Voyons lequel de nos POKéMON est "
                "le plus fort! Allez viens te battre, minable!")
        result = display_value(text, True, FUSION_LATIN, 10)
        self.assertIn("\f", result)
        from PIL import ImageFont
        font = ImageFont.truetype(str(FUSION_LATIN), 10)
        for page in result.split("\f"):
            first_cut = _fit_one_line(page, font, TEXTBOX_LINE_BUDGET_PX)
            rest = page[first_cut:]
            second_cut = _fit_one_line(rest, font, TEXTBOX_LINE_BUDGET_PX)
            self.assertEqual(second_cut, len(rest), f"page overflows two lines: {page!r}")


if __name__ == "__main__":
    unittest.main()
