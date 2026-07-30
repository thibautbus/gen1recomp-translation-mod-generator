import json
from pathlib import Path
import tempfile
import unittest

from pipeline.engine_backlog import analyze_engine_backlog, iter_literal_strings_callsites, run_backlog


class EngineBacklogTests(unittest.TestCase):
    def _fixture(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config").mkdir()
        (root / "config" / "pipeline.toml").write_text(
            '[corpus]\ntarget_lang = "fr"\npath = "corpus"\n', encoding="utf-8"
        )
        checkout = root / "gen1recomp" / "src"
        for directory in (checkout / "battle", checkout / "link", checkout / "ui"):
            directory.mkdir(parents=True)
        (checkout / "battle" / "Battle.lua").write_text(
            '-- Strings("ignored")\n--[[ Strings("also ignored") ]]\n'
            '--[=[ Strings("long comment ignored") ]=]\n'
            'local fake = "Strings(\\"fake\\")"\n'
            'local long = Strings([=[\nLong literal]=])\n'
            'local a = Strings("%s woke!")\n'
            'local b = Strings.source("%s woke!")\n', encoding="utf-8"
        )
        (checkout / "link" / "Link.lua").write_text('local x = Strings("Link only")\n', encoding="utf-8")
        (checkout / "ui" / "Menu.lua").write_text('local x = Strings("UI only")\n', encoding="utf-8")
        catalog = root / "strings.lua"
        catalog.write_text(
            'return {\n  ["%s woke!"] = "",\n  ["Link only"] = "",\n  ["UI only"] = "",\n}\n', encoding="utf-8"
        )
        coverage = root / "coverage.json"
        coverage.write_text(json.dumps({
            "engine": {
                "total": 3,
                "unmatched": ["%s woke!", "Link only", "UI only"],
                "ambiguous": {"%s woke!": ["réveillé", "s'est réveillé"]},
                "details": {"%s woke!": "ambiguous", "Link only": "english_fallback", "UI only": "english_fallback"},
                "provenance": {"%s woke!": {"method": "english_fallback"}},
            }
        }), encoding="utf-8")
        corpus = root / "corpus" / "RedBlue"
        corpus.mkdir(parents=True)
        (corpus / "qid_msg.txt").write_text("rb.text.Wake\nrb.text.Link\nrb.text.Ui\n", encoding="utf-8")
        (corpus / "en_msg.txt").write_text("{PLAYER} woke!\nLink only\nUI only\n", encoding="utf-8")
        (corpus / "fr_msg.txt").write_text("{PLAYER} s'est réveillé !\nLien\nInterface\n", encoding="utf-8")
        return tmp, root, checkout.parent, corpus, coverage, catalog

    def test_literal_callsites_include_source_and_ignore_comments(self):
        tmp, root, checkout, *_ = self._fixture()
        try:
            calls = iter_literal_strings_callsites(checkout)
            self.assertEqual([item["source"] for item in calls], ["%s woke!", "%s woke!", "Link only", "Long literal", "UI only"])
            self.assertEqual([item["kind"] for item in calls[:2]], ["call", "source"])
            self.assertEqual(calls[0]["path"], "src/battle/Battle.lua")
            self.assertEqual(calls[0]["line"], 7)
        finally:
            tmp.cleanup()

    def test_report_is_conservative_and_placeholder_compatible(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            report = analyze_engine_backlog("fr", root=root, checkout=checkout, corpus_root=corpus,
                                            coverage_path=coverage, engine_catalog=catalog)
            by_key = {entry["key"]: entry for entry in report["entries"]}
            wake = by_key["%s woke!"]
            self.assertEqual(wake["status"], "ambiguous")
            self.assertEqual(wake["category"], "rby")
            self.assertTrue(wake["rby_eligible"])
            self.assertEqual(wake["matcher"]["fallback_reason"], "ambiguous")
            self.assertTrue(wake["qid_candidates"][0]["placeholder_compatible"])
            self.assertTrue(wake["qid_candidates"][0]["eligible"])
            self.assertIn("game", wake["qid_candidates"][0])
            self.assertFalse(by_key["Link only"]["rby_eligible"])
            self.assertIsNone(by_key["UI only"]["rby_eligible"])
        finally:
            tmp.cleanup()

    def test_language_and_snapshot_validation(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            with self.assertRaisesRegex(ValueError, "unsupported engine backlog language"):
                analyze_engine_backlog("../../escape", root=root, checkout=checkout, corpus_root=corpus,
                                       coverage_path=coverage, engine_catalog=catalog)
            data = json.loads(coverage.read_text())
            data["engine"]["total"] = 999
            coverage.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "total"):
                analyze_engine_backlog("fr", root=root, checkout=checkout, corpus_root=corpus,
                                       coverage_path=coverage, engine_catalog=catalog)
            coverage.write_text(json.dumps({"engine": {"total": 3, "unmatched": [], "ambiguous": {}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "key universe"):
                analyze_engine_backlog("fr", root=root, checkout=checkout, corpus_root=corpus,
                                       coverage_path=coverage, engine_catalog=catalog)
        finally:
            tmp.cleanup()

    def test_missing_coverage_is_an_english_error(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            coverage.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "coverage"):
                analyze_engine_backlog("fr", root=root, checkout=checkout, corpus_root=corpus,
                                       coverage_path=coverage, engine_catalog=catalog)
        finally:
            tmp.cleanup()

    def test_run_writes_private_deterministic_reports(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            report = run_backlog(language="fr", root=root, checkout=checkout, corpus_root=corpus,
                                 coverage_path=coverage, engine_catalog=catalog)
            output = root / ".cache" / "audit" / "engine-backlog"
            self.assertTrue((output / "fr.json").is_file())
            self.assertTrue((output / "fr.md").is_file())
            self.assertEqual(json.loads((output / "fr.json").read_text())["stats"], report["stats"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
