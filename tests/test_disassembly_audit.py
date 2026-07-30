import json
import tempfile
import unittest
from pathlib import Path

from pipeline.disassembly_audit import audit_language, parse_disassembly, run_audit


class DisassemblyAuditTests(unittest.TestCase):
    def test_parse_macros_labels_and_callsites_without_rom_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "text.asm").write_text(
                '_GreetingText::\n'
                '    text "Bonjour"\n'
                '    line "vous"\n'
                '    done\n'
                'StartScript::\n'
                '    fartext _GreetingText\n',
                encoding="utf-8",
            )
            parsed = parse_disassembly(root)
            self.assertEqual(parsed["_GreetingText"]["qid"], "GreetingText")
            self.assertEqual(parsed["_GreetingText"]["text"], "Bonjour<LINE>vous<DONE>@")
            self.assertEqual(parsed["_GreetingText"]["callsites"][0]["line"], 6)

    def test_rgbds_local_labels_are_excluded_without_cross_global_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "text.asm").write_text(
                '_First::\n text "ONE"\n done\n'
                '.Text::\n text "LOCAL ONE"\n done\n'
                '_Second::\n text "TWO"\n done\n'
                '.Text::\n text "LOCAL TWO"\n done\n', encoding="utf-8",
            )
            parsed = parse_disassembly(root)
            self.assertNotIn(".Text", parsed)
            self.assertEqual(parsed["_First"]["text"], "ONE<DONE>@")
            self.assertEqual(parsed["_Second"]["text"], "TWO<DONE>@")

    def test_reference_only_wrapper_is_not_counted_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "text.asm").write_text(
                '_Wrapper::\n text_far _RealText\n text_end\n'
                '_RealText::\n text "Bonjour"\n done\n', encoding="utf-8",
            )
            corpus = root / "corpus"; corpus.mkdir()
            (corpus / "qid_msg.txt").write_text("rb.text.Wrapper\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Wrapper\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Enveloppe\n", encoding="utf-8")
            report = audit_language("fr", root, corpus_root=corpus)
            wrapper = next(item for item in report["candidates"] if item["label"] == "_Wrapper")
            self.assertEqual(wrapper["status"], "reference_only")
            self.assertEqual(report["stats"]["missing"], 1)
            self.assertEqual(report["stats"]["reference_only"], 1)

    def test_audit_reports_match_divergence_missing_and_untrusted_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "localized"; source.mkdir()
            (source / "text.asm").write_text(
                '_Match::\n text "Bonjour"\n done\n'
                '_Different::\n text "Guten Tag"\n done\n'
                '_Absent::\n text "Inconnu"\n done\n', encoding="utf-8",
            )
            corpus = root / "corpus"; corpus.mkdir()
            (corpus / "qid_msg.txt").write_text("rb.text.Match\nrb.text.Different\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello\nBye\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour@\nSalut@\n", encoding="utf-8")
            (corpus / "it_msg.txt").write_text("Bonjour@\nGuten Tag@\n", encoding="utf-8")
            report = audit_language("it", source, expected="it", trusted=False, corpus_root=corpus, project_root=root)
            self.assertEqual(report["expected_language"], "it")
            self.assertEqual(report["detected_language"], "de")
            self.assertFalse(report["trusted"])
            self.assertEqual(report["recommended_candidates"], [])
            self.assertEqual(report["stats"]["candidates"], 3)
            self.assertEqual(report["stats"]["missing"], 1)

    def test_run_audit_writes_private_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config"; config.mkdir()
            (config / "pipeline.toml").write_text(
                '[disassemblies.fr]\nsource = "fixture"\nrevision = "abc"\ntrusted = true\n', encoding="utf-8",
            )
            checkout = root / "fixture"; checkout.mkdir()
            (checkout / "text.asm").write_text('_Hello::\n text "Bonjour"\n done\n', encoding="utf-8")
            def fake_checkout(url, revision, destination, **kwargs):
                self.assertEqual(kwargs["sparse_paths"], ("text", "scripts"))
                destination.mkdir(parents=True)
                for path in checkout.iterdir():
                    (destination / path.name).write_bytes(path.read_bytes())
            reports = run_audit(root, checkout_runner=fake_checkout)
            self.assertEqual(reports[0]["language"], "fr")
            report_dir = root / ".cache" / "audit" / "reports"
            self.assertTrue((report_dir / "fr.json").is_file())
            self.assertTrue((report_dir / "fr.md").is_file())
            self.assertEqual(json.loads((report_dir / "index.json").read_text())["languages"], ["fr"])

    def test_engine_coverage_enriches_candidates_by_english_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "localized"; source.mkdir()
            (source / "text.asm").write_text('_Greeting::\n text "Bonjour"\n done\n', encoding="utf-8")
            corpus = root / "corpus"; corpus.mkdir()
            (corpus / "qid_msg.txt").write_text("rb.text.Greeting\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello@\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour@\n", encoding="utf-8")
            coverage_dir = root / ".cache" / "interactive" / "fr"; coverage_dir.mkdir(parents=True)
            (coverage_dir / "coverage.json").write_text(json.dumps({"engine": {"unmatched": ["Hello@"]}}), encoding="utf-8")
            report = audit_language("fr", source, corpus_root=corpus, project_root=root)
            self.assertEqual(report["candidates"][0]["engine_candidates"][0]["engine_key"], "Hello@")
            self.assertEqual(report["candidates"][0]["engine_candidates"][0]["method"], "exact")
            self.assertEqual(report["recommended_candidates"], ["Greeting"])


if __name__ == "__main__":
    unittest.main()
