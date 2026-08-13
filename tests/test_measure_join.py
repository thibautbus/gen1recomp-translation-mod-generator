"""Guards the two normalisation traps in tools/measure_join.py: each
produces a wrong-but-plausible number rather than a crash, which is
exactly the failure mode a test has to catch on purpose rather than by
accident.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_join", ROOT / "tools" / "measure_join.py")
measure_join = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measure_join
spec.loader.exec_module(measure_join)


class NormaliseTests(unittest.TestCase):
    def test_pokemon_compression_bytes_fold_the_same_as_the_accented_rom_spelling(self):
        # The ROM writes "POKéMON" with a literal U+00E9; the corpus writes
        # the #MON compression byte. Without NFKD folding before the
        # alnum filter, the accent is dropped and "pokmon" matches nothing:
        # 841 of 3044 pointers in the real catalog.
        self.assertEqual(measure_join.normalise("POKéMON"), measure_join.normalise("#MON"))
        self.assertEqual(measure_join.normalise("#"), "poke")

    def test_markup_is_stripped_from_both_sides(self):
        self.assertEqual(measure_join.normalise("<LINE>Hello, %s!"), measure_join.normalise("{text_start}Hello %s"))

    def test_empty_after_normalisation_is_markup_only(self):
        self.assertEqual(measure_join.normalise("<PARA><DONE>"), "")


class SplitLinesTests(unittest.TestCase):
    def test_splits_only_on_newline_not_on_gold_control_bytes(self):
        # str.splitlines() also splits on \x0b/\x0c/\x1c/\x85 among others;
        # \x0c is Gold's text-scroll control byte, and using splitlines()
        # here silently turns 3044 catalog records into 8013 fragments.
        text = "one\x0ctwo\x0bthree\ffour\nfive"
        self.assertEqual(measure_join.split_lines(text), ["one\x0ctwo\x0bthree\ffour", "five"])

    def test_trailing_newline_does_not_produce_an_empty_trailing_line(self):
        self.assertEqual(measure_join.split_lines("a\nb\n"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
