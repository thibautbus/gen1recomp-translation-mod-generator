import json
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from pipeline.engine_backlog import (
    analyze_engine_backlog,
    analyze_engine_backlog_matrix,
    iter_literal_strings_callsites,
    run_backlog,
    run_backlog_matrix,
)
from pipeline.cli import main as cli_main


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
        (corpus / "de_msg.txt").write_text("{PLAYER} ist aufgewacht!\nLink\nOberfläche\n", encoding="utf-8")
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

    def test_matrix_joins_language_rows_and_writes_deterministically(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            first = run_backlog_matrix(
                root=root,
                languages=["de", "fr"],
                checkout=checkout,
                corpus_root=corpus,
                coverage_paths={"de": coverage, "fr": coverage},
                engine_catalog_paths={"de": catalog, "fr": catalog},
            )
            output = root / ".cache" / "audit" / "engine-backlog"
            first_json = (output / "matrix.json").read_bytes()
            first_md = (output / "matrix.md").read_bytes()
            second = run_backlog_matrix(
                root=root,
                languages=["fr", "de"],
                checkout=checkout,
                corpus_root=corpus,
                coverage_paths={"fr": coverage, "de": coverage},
                engine_catalog_paths={"fr": catalog, "de": catalog},
            )
            self.assertEqual(first["languages"], ["fr", "de"])
            self.assertEqual(first["stats"], second["stats"])
            self.assertEqual(first_json, (output / "matrix.json").read_bytes())
            self.assertEqual(first_md, (output / "matrix.md").read_bytes())
            report = analyze_engine_backlog_matrix(
                root=root,
                languages="fr",
                checkout=checkout,
                corpus_root=corpus,
                coverage_paths={"fr": coverage},
                engine_catalog_paths={"fr": catalog},
            )
            self.assertEqual(report["schema"], "gen1recomp-translation-mods/engine-backlog-matrix")
            self.assertEqual(report["languages"], ["fr"])
            wake = next(item for item in report["entries"] if item["key"] == "%s woke!")
            self.assertEqual(wake["commonality"], {"languages_present": 1, "language_count": 1, "all_languages": True, "fraction": 1.0})
            self.assertEqual(wake["triage"], "common-rby")
            self.assertEqual(wake["languages"]["fr"]["status"], "ambiguous")
            run_backlog_matrix(
                root=root,
                languages=["fr"],
                checkout=checkout,
                corpus_root=corpus,
                coverage_paths={"fr": coverage},
                engine_catalog_paths={"fr": catalog},
            )
            output = root / ".cache" / "audit" / "engine-backlog"
            matrix = json.loads((output / "matrix.json").read_text())
            self.assertEqual(matrix["stats"], report["stats"])
            self.assertEqual((output / "matrix.md").read_text().splitlines()[0], "# Engine backlog matrix")
        finally:
            tmp.cleanup()

    def test_matrix_explicit_mappings_are_canonical_and_fail_closed(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            common = dict(root=root, languages=["fr", "ja"], checkout=checkout, corpus_root=corpus)
            with self.assertRaisesRegex(ValueError, "missing coverage mapping"):
                analyze_engine_backlog_matrix(
                    **common, coverage_paths={"fr": coverage},
                    engine_catalog_paths={"fr": catalog, "ja-Hrkt": catalog},
                )
            with self.assertRaisesRegex(ValueError, "duplicate coverage language aliases"):
                analyze_engine_backlog_matrix(
                    **common, coverage_paths={"fr": coverage, "ja": coverage, "ja-Hrkt": coverage},
                    engine_catalog_paths={"fr": catalog, "ja": catalog},
                )
            with self.assertRaisesRegex(ValueError, "unsupported coverage language"):
                analyze_engine_backlog_matrix(
                    **common, coverage_paths={"fr": coverage, "xx": coverage},
                    engine_catalog_paths={"fr": catalog, "ja": catalog},
                )
        finally:
            tmp.cleanup()

    def test_matrix_template_rejects_unsafe_fields(self):
        tmp, root, checkout, corpus, coverage, catalog = self._fixture()
        try:
            kwargs = dict(root=root, languages=["fr"], checkout=checkout, corpus_root=corpus,
                          coverage_paths=None, engine_catalog_paths={"fr": catalog})
            with self.assertRaisesRegex(ValueError, r"only the \{language\} placeholder"):
                analyze_engine_backlog_matrix(**kwargs, coverage_dir="coverage-{other}.json")
            with self.assertRaisesRegex(ValueError, "invalid matrix path template"):
                analyze_engine_backlog_matrix(**kwargs, coverage_dir="coverage-{language.json")
        finally:
            tmp.cleanup()

    def test_matrix_mixed_rby_eligibility_is_review(self):
        reports = {
            "fr": {"sources": {}, "coverage_snapshot": {}, "classifier": {}, "stats": {},
                   "entries": [{"key": "X", "status": "unmatched", "category": "rby",
                                 "rby_eligibility": "eligible", "rby_eligible": True,
                                 "qid_candidates": [], "placeholders": [], "placeholder_signature": {},
                                 "callsites": [], "fallback_reason": "english_fallback",
                                 "coverage_provenance": {}, "semantic_anchor": None}]},
            "de": {"sources": {}, "coverage_snapshot": {}, "classifier": {}, "stats": {},
                   "entries": [{"key": "X", "status": "unmatched", "category": "link",
                                 "rby_eligibility": "ineligible", "rby_eligible": False,
                                 "qid_candidates": [], "placeholders": [], "placeholder_signature": {},
                                 "callsites": [], "fallback_reason": "english_fallback",
                                 "coverage_provenance": {}, "semantic_anchor": None}]},
        }
        with patch("pipeline.engine_backlog.analyze_engine_backlog", side_effect=lambda language, **_: reports[language]):
            matrix = analyze_engine_backlog_matrix(languages=["de", "fr"], coverage_paths={"de": "d", "fr": "f"}, engine_catalog_paths={"de": "d", "fr": "f"})
        self.assertEqual(matrix["entries"][0]["triage"], "rby-review")
        self.assertEqual(matrix["stats"]["triage"]["common-rby"], 0)

    def test_matrix_cli_rejects_duplicate_aliases_and_dir_mapping_conflicts(self):
        cases = [
            (["--coverage", "fr=a", "--coverage", "fr=b"], "duplicate coverage language mapping"),
            (["--coverage", "ja=a", "--coverage", "ja-Hrkt=b"], "duplicate coverage language mapping"),
            (["--coverage", "fr=a", "--coverage-dir", "cache"], "cannot combine --coverage mappings with --coverage-dir"),
            (["--engine-catalog", "fr=a", "--engine-catalog-dir", "cache"], "cannot combine --engine-catalog mappings with --engine-catalog-dir"),
        ]
        for options, message in cases:
            error = io.StringIO()
            with redirect_stderr(error):
                status = cli_main(["engine-backlog-matrix", "--languages", "fr", *options])
            self.assertEqual(status, 2, options)
            self.assertIn(message, error.getvalue(), options)


if __name__ == "__main__":
    unittest.main()
