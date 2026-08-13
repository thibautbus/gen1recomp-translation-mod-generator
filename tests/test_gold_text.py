import tempfile
import unittest
from pathlib import Path

from pipeline.gold_text import GoldTextRecord, parse_gold_text_catalog


class GoldTextCatalogTests(unittest.TestCase):
    def test_parses_pointer_and_escaped_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_text(
                "55:4067\tLine one\\nLine two\\ttabbed\n"
                "10:0001\t{text_start}Hello\\\\World<DONE>\n",
                encoding="utf-8",
            )
            records = parse_gold_text_catalog(text_tsv)
            self.assertEqual(records, [
                GoldTextRecord("10:0001", "{text_start}Hello\\World<DONE>"),
                GoldTextRecord("55:4067", "Line one\nLine two\ttabbed"),
            ])

    def test_never_splits_on_gold_scroll_control_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_bytes(b"01:0001\tPage one\x0cPage two\n")
            records = parse_gold_text_catalog(text_tsv)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].text, "Page one\x0cPage two")

    def test_resolves_labels_from_the_labels_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gold_text.tsv").write_text("21:6514\tNew Hall of Famer!\n55:4067\tOther\n", encoding="utf-8")
            (root / "gold_labels.tsv").write_text(
                "AnimateHallOfFame.String_NewHallOfFamer\t21:6514\n", encoding="utf-8",
            )
            records = parse_gold_text_catalog(root / "gold_text.tsv", root / "gold_labels.tsv")
            by_pointer = {r.pointer: r for r in records}
            self.assertEqual(by_pointer["21:6514"].label, "AnimateHallOfFame.String_NewHallOfFamer")
            self.assertIsNone(by_pointer["55:4067"].label)

    def test_labels_tsv_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_text("55:4067\tHello\n", encoding="utf-8")
            records = parse_gold_text_catalog(text_tsv)
            self.assertEqual(records, [GoldTextRecord("55:4067", "Hello")])

    def test_rejects_a_pointer_repeated_with_different_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_text("55:4067\tFirst\n55:4067\tSecond\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ambiguous pointer '55:4067'"):
                parse_gold_text_catalog(text_tsv)

    def test_tolerates_a_pointer_repeated_with_identical_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_text("55:4067\tSame\n55:4067\tSame\n", encoding="utf-8")
            records = parse_gold_text_catalog(text_tsv)
            self.assertEqual(records, [GoldTextRecord("55:4067", "Same")])

    def test_rejects_a_label_naming_a_pointer_absent_from_the_text_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gold_text.tsv").write_text("55:4067\tHello\n", encoding="utf-8")
            (root / "gold_labels.tsv").write_text("SomeLabel\t99:9999\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SomeLabel.*99:9999"):
                parse_gold_text_catalog(root / "gold_text.tsv", root / "gold_labels.tsv")

    def test_rejects_two_different_labels_for_the_same_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gold_text.tsv").write_text("55:4067\tHello\n", encoding="utf-8")
            (root / "gold_labels.tsv").write_text("LabelA\t55:4067\nLabelB\t55:4067\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ambiguous label for pointer '55:4067'"):
                parse_gold_text_catalog(root / "gold_text.tsv", root / "gold_labels.tsv")

    def test_records_are_sorted_by_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_tsv = Path(tmp) / "gold_text.tsv"
            text_tsv.write_text("55:4067\tB\n10:0001\tA\n02:0010\tC\n", encoding="utf-8")
            records = parse_gold_text_catalog(text_tsv)
            self.assertEqual([r.pointer for r in records], ["02:0010", "10:0001", "55:4067"])


if __name__ == "__main__":
    unittest.main()
