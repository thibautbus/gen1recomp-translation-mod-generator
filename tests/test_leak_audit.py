import json
import tempfile
import unittest
from pathlib import Path

from pipeline.leak_audit import audit_generated_catalogs, leaked_markup
from pipeline.tokens import corpus_to_engine

ROOT = Path(__file__).resolve().parents[1]


class LeakAuditTests(unittest.TestCase):
    def test_flags_corpus_markup_the_key_does_not_carry(self):
        self.assertEqual(leaked_markup("BRN", "\\xCD\\xCE\\xCF"), ["hex escape"])
        self.assertEqual(leaked_markup("…Repeating myself", "{text_dots 3}おなじ"), ["pret command"])
        self.assertEqual(leaked_markup("LANDMARK_X", "FIORPESCO<SHY>POLI"), ["control character"])
        self.assertEqual(leaked_markup("Now saving...", "[NULL]"), ["no-text marker"])
        self.assertEqual(leaked_markup("ABRA", "ligne<NEXT>suite"), [])
        self.assertEqual(leaked_markup("_RoseText", " rose!{PROMPT}"), [])
        self.assertEqual(leaked_markup("a {text_dots 3} b", "x {text_dots 3} y"), [])

    def test_audits_generated_lua_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            lang = Path(tmp) / "lang"
            lang.mkdir()
            (lang / "landmarks.lua").write_text(
                'return {\n  ["LANDMARK_A"] = "FIORPESCO<SHY>POLI",\n  ["LANDMARK_B"] = "DOUBLONVILLE",\n}\n',
                encoding="utf-8",
            )
            (lang / "strings.lua").write_text(
                'return {\n  ["Hello\\n"] = "Bonjour\\012!",\n}\n', encoding="utf-8",
            )
            problems = audit_generated_catalogs(tmp)
            self.assertEqual(len(problems), 1)
            self.assertIn("LANDMARK_A", problems[0])

    def test_place_name_break_characters_and_text_dots_convert(self):
        self.assertEqual(corpus_to_engine("DOUBLON<WBR>VILLE"), "DOUBLONVILLE")
        self.assertEqual(corpus_to_engine("ZINNOBER-<WBR>INSEL"), "ZINNOBER-INSEL")
        self.assertEqual(corpus_to_engine("FIORPESCO<SHY>POLI"), "FIORPESCOPOLI")
        self.assertEqual(corpus_to_engine("NEW BARK<BSP>TOWN"), "NEW BARK TOWN")
        self.assertEqual(corpus_to_engine("は{text_dots 3}そう", bare_dynamic_tokens=True), "は………そう")

    def test_checked_in_override_values_leak_no_corpus_markup(self):
        leaks = []
        for path in sorted((ROOT / "overrides").glob("*/*/*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, row in (data.get("entries") or {}).items():
                value = row.get("override") if isinstance(row, dict) else row
                if not isinstance(value, str):
                    continue
                shipped = corpus_to_engine(value, bare_dynamic_tokens=True) if path.name in (
                    "corpus.json", "dialogue.json", "crystal_dialogue.json") else value
                for kind in leaked_markup(key, shipped):
                    leaks.append(f"{path.relative_to(ROOT)} {key!r}: {kind}")
        self.assertEqual(leaks, [])


if __name__ == "__main__":
    unittest.main()
