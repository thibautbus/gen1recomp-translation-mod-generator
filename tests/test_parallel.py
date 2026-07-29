import tempfile
import unittest
from pathlib import Path
import os

from pipeline.corpus import parse_redblue, read_parallel_redblue
from pipeline.tokens import tokens, convert_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "RedBlue"


class ParallelCorpusTests(unittest.TestCase):
    def test_real_parallel_shape_and_version_scopes(self):
        records = parse_redblue(FIXTURE)
        self.assertEqual(len(records), 10)
        by_qid = {r.qid: r for r in records if r.language == "en"}
        self.assertEqual(by_qid["rb.text.PlacePKMNText^B"].game, "blue")
        self.assertEqual(by_qid["rb.title.CopyrightTextString^RG"].game, "red")
        self.assertEqual(by_qid["rb.dex_entries.PorygonDexEntry^RG.Species"].game, "red")
        self.assertEqual(by_qid["rb.text.TMCharText"].metadata["version_scope"], "both")
        self.assertEqual(records[4].text, r"\x61\x62\x63@ ")

    def test_cardinality_is_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, text in (("qid", "a\nb\n"), ("en", "A\n"), ("fr", "Un\n")):
                (root / f"{name}_msg.txt").write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_parallel_redblue(root)

    def test_hex_escape_is_preserved_as_a_token(self):
        escaped = chr(92) + "x60"
        self.assertEqual(tokens(escaped), [escaped])
        self.assertEqual(convert_tokens(escaped, {escaped: "`"}), "`")

    def test_real_checkout_integration(self):
        root = os.environ.get("POKE_CORPUS")
        if not root:
            self.skipTest("set POKE_CORPUS to run the real corpus integration test")
        records = parse_redblue(root)
        self.assertEqual(len(records), 7720)


if __name__ == "__main__":
    unittest.main()
