import tempfile
import unittest
from pathlib import Path

from pipeline.align import align
from pipeline.corpus import read_parallel_yellow
from pipeline.join import join_catalogs, WorksheetEntry
from pipeline.model import Alignment, CorpusRecord
from pipeline.yellow import parse_text_catalog, yellow_dialogue_layer
from pipeline.mod import effective_yellow_engine_coverage, yellow_coverage_metrics


def _corpus_records(directory: Path, lang: str = "fr") -> list[CorpusRecord]:
    return read_parallel_yellow(directory, lang)


class ParseTextCatalogTests(unittest.TestCase):
    def test_parses_identifiers_and_lua_escapes(self):
        body = (
            "return {\n"
            '  SilphCo2FWorkerText = "Eeek!\\nNo! Stop!",\n'
            '  _AbraDexEntry = "Sleeps 18 hours\\na day.\\fIt teleports.",\n'
            '  _QuotedText = "Say \\"hi\\"\\\\",\n'
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.lua"
            path.write_text(body, encoding="utf-8")
            catalog = parse_text_catalog(path)
        self.assertEqual(catalog["SilphCo2FWorkerText"], "Eeek!\nNo! Stop!")
        self.assertEqual(catalog["_AbraDexEntry"], "Sleeps 18 hours\na day.\fIt teleports.")
        self.assertEqual(catalog["_QuotedText"], 'Say "hi"\\')


class YellowDialogueLayerTests(unittest.TestCase):
    def _layer(self, red_text, yellow_text, records):
        return yellow_dialogue_layer(red_text, yellow_text, records, "fr")

    def test_shared_safe_labels_are_skipped(self):
        red = {"_OakSpeechText1": "Hello there!\nWelcome."}
        yellow = {"_OakSpeechText1": "Hello there!\nWelcome."}
        records = [
            Alignment("y.text_3.OakSpeechText1", "yellow",
                      CorpusRecord("y.text_3.OakSpeechText1", "en", "Hello there!\nWelcome.", "yellow", "corpus/Yellow/en_msg.txt"),
                      CorpusRecord("y.text_3.OakSpeechText1", "fr", "Bonjour !\nBienvenue.", "yellow", "corpus/Yellow/fr_msg.txt"),
                      "anchor"),
        ]
        layer, stats = self._layer(red, yellow, records)
        self.assertEqual(layer, {})
        self.assertEqual(stats["shared_safe"], 1)
        self.assertEqual(stats["versioned_required"], 0)

    def test_same_english_with_yellow_target_variant_is_emitted(self):
        red = {"_BattleCryText": "Ayah!"}
        yellow = {"_BattleCryText": "Ayah!"}
        records = [
            Alignment("y.text.BattleCryText", "yellow",
                      CorpusRecord("y.text.BattleCryText", "en", "Ayah!", "yellow", "en"),
                      CorpusRecord("y.text.BattleCryText", "fr", "Ahh!", "yellow", "fr"),
                      "anchor"),
        ]
        layer, stats = yellow_dialogue_layer(
            red, yellow, records, "fr", red_translation={"_BattleCryText": "Yaah!"}
        )
        self.assertEqual(layer["_BattleCryText"], "Ahh!")
        self.assertEqual(stats["translation_variant"], 1)
        # Regression: a translation-variant label must not also inflate
        # versioned_required — the two counters must stay mutually exclusive.
        self.assertEqual(stats["versioned_required"], 0)

    def test_versioned_and_yellow_only_labels_are_emitted(self):
        red = {"_OakSpeechText1": "Hello there!\nWelcome."}
        yellow = {"_OakSpeechText1": "I am PROF. OAK!\nWelcome!", "_PikachuSceneText": "Pika pi!"}
        records = [
            Alignment("y.text_3.OakSpeechText1", "yellow",
                      CorpusRecord("y.text_3.OakSpeechText1", "en", "I am PROF. OAK!\nWelcome!", "yellow", "en"),
                      CorpusRecord("y.text_3.OakSpeechText1", "fr", "Je suis le PROF. CHEN!\nBienvenue !", "yellow", "fr"),
                      "anchor"),
            Alignment("y.text_3.PikachuSceneText", "yellow",
                      CorpusRecord("y.text_3.PikachuSceneText", "en", "Pika pi!", "yellow", "en"),
                      CorpusRecord("y.text_3.PikachuSceneText", "fr", "Pika pi !", "yellow", "fr"),
                      "anchor"),
        ]
        layer, stats = self._layer(red, yellow, records)
        self.assertEqual(layer["_OakSpeechText1"], "Je suis le PROF. CHEN!\nBienvenue !")
        self.assertEqual(layer["_PikachuSceneText"], "Pika pi !")
        self.assertEqual(stats["versioned_required"], 1)
        self.assertEqual(stats["yellow_only"], 1)
        self.assertEqual(stats["matched"], 2)

    def test_unmatched_versioned_label_falls_back_to_rom_text_not_rb_fr(self):
        # The Yellow corpus has no match for this label; the Red translation
        # must NOT be reused (it would misrepresent Yellow's different text).
        # The layer re-emits the ROM's own Yellow content as the fallback.
        red = {"_RivalIntroText": "Red's rival is\nhere!"}
        yellow = {"_RivalIntroText": "Yellow rival\nintroduces himself!"}
        records = []  # no corpus rows at all
        layer, stats = self._layer(red, yellow, records)
        self.assertEqual(layer["_RivalIntroText"], "Yellow rival\nintroduces himself!")
        self.assertEqual(stats["versioned_required"], 1)
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(stats["unmatched"], 1)

    def test_yellow_dex_entry_matches_through_y_canonical_prefix(self):
        # Regression: the canonical dex_text rule used to hard-code the "rb."
        # prefix, so Yellow dex entries (y.dex_text.*) never matched.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "Yellow"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text(
                "y.dex_text.AbraDexEntry\ny.dex_entries.AbraDexEntry.Species\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text(
                "{text_start}Sleeps 18 hours<NEXT>a day.<dexend>\nABRA\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text(
                "{text_start}Il dort 18 heures<NEXT>par jour.\nABRA\n", encoding="utf-8")
            rows = align(read_parallel_yellow(corpus, "fr"), target_lang="fr")
        yellow_text = {"_AbraDexEntry": "Sleeps 18 hours\na day."}
        layer, stats = yellow_dialogue_layer({}, yellow_text, rows, "fr")
        self.assertEqual(layer["_AbraDexEntry"], "Il dort 18 heures\npar jour.")
        self.assertEqual(stats["matched"], 1)


class YellowCoverageTests(unittest.TestCase):
    def test_yellow_coverage_counts_dialogue_and_named_catalogs(self):
        metrics = yellow_coverage_metrics({
            "yellow_labels": 10, "layer_entries": 4,
            "matched": 3, "unmatched": 1,
            "catalogs": {"species_names": {"matched": 4, "total": 5}},
        })
        self.assertEqual(metrics["dialogue"], {"translated": 9, "total": 10, "percent": 90.0})
        self.assertEqual(metrics["named_catalogs"], {"translated": 4, "total": 5, "percent": 80.0})
        self.assertEqual(metrics["rom"], {"translated": 13, "total": 15, "percent": 86.67})
        self.assertEqual(metrics["specific_diff"], {"translated": 3, "total": 4, "percent": 75.0})

    def test_yellow_coverage_specific_diff_excludes_yellow_only_unmatched(self):
        # Regression: stats["unmatched"] counts both unmatched-versioned
        # labels (present in the layer as a ROM-English fallback) and
        # unmatched yellow-only labels (never added to the layer at all).
        # Here layer_entries=4 already excludes 1 unmatched yellow-only
        # label, so subtracting the full unmatched count from layer_entries
        # would wrongly report 3/4 translated instead of the true 4/4.
        metrics = yellow_coverage_metrics({
            "yellow_labels": 10, "layer_entries": 4,
            "matched": 4, "unmatched": 1,
            "catalogs": {},
        })
        self.assertEqual(metrics["specific_diff"], {"translated": 4, "total": 4, "percent": 100.0})

    def test_yellow_coverage_uses_effective_common_catalog_fallbacks(self):
        metrics = yellow_coverage_metrics({
            "yellow_labels": 10,
            "effective_dialogue_total": 9,
            "catalogs": {"item_names": {"matched": 2, "total": 5}},
            "effective_dialogue_translated": 9,
            "effective_named_catalog_translated": 5,
        })
        self.assertEqual(metrics["rom"], {"translated": 14, "total": 14, "percent": 100.0})

    def test_yellow_rom_aggregate_includes_shared_runtime_extras(self):
        metrics = yellow_coverage_metrics(
            {
                "effective_dialogue_total": 6,
                "effective_dialogue_translated": 5,
                "catalogs": {"item_names": {"matched": 3, "total": 4}},
                "effective_named_catalog_translated": 3,
            },
            {
                # Base Red/Blue catalogs are replaced by their Yellow
                # equivalents; only the remaining runtime resources are shared.
                "dialogue": {"translated": 20, "total": 20},
                "item_names": {"translated": 8, "total": 8},
                "type_names": {"translated": 2, "total": 2},
                "literal_handlers": {"translated": 1, "total": 2},
            },
        )

        self.assertEqual(metrics["rom"], {"translated": 11, "total": 14, "percent": 78.57})
        self.assertEqual(metrics["shared_runtime"]["translated"], 3)
        self.assertEqual(metrics["shared_runtime"]["total"], 4)
        self.assertEqual(set(metrics["shared_runtime"]["details"]), {
            "type_names", "literal_handlers",
        })

    def test_refusing_dialogue_completes_effective_engine_metric(self):
        coverage = effective_yellow_engine_coverage(
            {"translated": 248, "total": 249},
            {"_RefusingText": "{RAM:wNameBuffer}\nrefuse!"},
        )
        self.assertEqual(coverage["translated"], 249)
        self.assertEqual(coverage["covered_by_dialogue"], 1)
        self.assertEqual(coverage, {"translated": 249, "total": 249, "percent": 100.0, "covered_by_dialogue": 1})

    def test_english_refusal_fallback_is_not_counted(self):
        coverage = effective_yellow_engine_coverage(
            {"translated": 248, "total": 249},
            {"_RefusingText": "{RAM:wNameBuffer}\nis refusing!"},
        )
        self.assertEqual(coverage["covered_by_dialogue"], 0)
        self.assertEqual(coverage["translated"], 248)


class YellowJoinReportTests(unittest.TestCase):
    def test_join_report_counts_are_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "Yellow"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text("y.text_1.HelloText\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour\n", encoding="utf-8")
            rows = align(read_parallel_yellow(corpus, "fr"), target_lang="fr")
        joined, report = join_catalogs(rows, {"dialogue": [WorksheetEntry("_HelloText", "Hello", "dialogue")]}, target_lang="fr")
        self.assertEqual(joined["dialogue"]["_HelloText"], "Bonjour")
        self.assertEqual(report["matched"]["dialogue"], 1)

    def test_yellow_join_summary_counts_entries_not_catalogs(self):
        rows = [
            Alignment(f"y.text.Hello{i}", "yellow",
                      CorpusRecord(f"y.text.Hello{i}", "en", f"Hello {i}", "yellow", "en"),
                      CorpusRecord(f"y.text.Hello{i}", "fr", f"Salut {i}", "yellow", "fr"),
                      "anchor")
            for i in range(2)
        ]
        layer, stats = yellow_dialogue_layer(
            {}, {"_Hello0": "Hello 0", "_Hello1": "Hello 1"}, rows, "fr"
        )
        self.assertEqual(len(layer), 2)
        self.assertEqual(stats["join_report"]["matched"], 2)


class YellowAuditTests(unittest.TestCase):
    def test_audit_writes_separate_statuses_and_deferred(self):
        import json
        from pipeline.yellow_audit import write_yellow_audit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            red_text = {"_OakSpeechText1": "Hello there!"}
            yellow_text = {"_OakSpeechText1": "I am PROF. OAK!", "_PikachuSceneText": "Pika pi!"}
            rows = [
                Alignment("y.text_3.OakSpeechText1", "yellow",
                          CorpusRecord("y.text_3.OakSpeechText1", "en", "I am PROF. OAK!", "yellow", "en"),
                          CorpusRecord("y.text_3.OakSpeechText1", "fr", "Je suis le PROF. CHEN!", "yellow", "fr"),
                          "anchor"),
            ]
            out = root / "audit"
            path = write_yellow_audit(Path("red.lua"), Path("yellow.lua"), rows, "fr", out,
                                      red_text=red_text, yellow_text=yellow_text)
            audit = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(audit["statuses"]["versioned-required"], 1)
            self.assertEqual(audit["statuses"]["yellow-only"], 1)
            self.assertEqual(audit["versioned"][0]["translated"], True)
            self.assertTrue(any(item["engine_key"] == "%s is refusing!" for item in audit["deferred_engine_strings"]))


class UniversalBuildTests(unittest.TestCase):
    """generate_mod emission, scaffold hook injection, override merge, parse_yellow."""

    def _write_minimal_worksheet(self, root: Path) -> Path:
        # The engine-string report only ships with a modkit worksheet present.
        worksheet = root / "worksheet"
        worksheet.mkdir(parents=True)
        for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels"):
            (worksheet / f"{name}.txt").write_text("# header\n", encoding="utf-8")
        (worksheet / "strings.lua").write_text('return { ["X"] = "" }\n', encoding="utf-8")
        return worksheet

    def test_generate_mod_emits_dialogue_yellow_and_isYellow_hook(self):
        import json
        from pipeline.corpus import parse_redblue
        from pipeline.mod import generate_mod
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "RedBlue"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text("rb.text.HelloText\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour\n", encoding="utf-8")
            rows = align(parse_redblue(corpus, "fr"), target_lang="fr")
            worksheet = self._write_minimal_worksheet(root)
            mod = root / "mod"
            generate_mod(rows, mod, mod_id="translation-fr", language="fr",
                         modkit_worksheet=worksheet, report_path=root / "report.json",
                         yellow_dialogue={"_PikachuSceneText": "Pika pi !"},
                         yellow_engine_overrides={"A: done": "A: FINI"},
                         yellow_stats={"layer_entries": 1})
            body = (mod / "lang" / "dialogue_yellow.lua").read_text(encoding="utf-8")
            self.assertIn('["_PikachuSceneText"] = "Pika pi !"', body)
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn("GameVersion.isYellow()", main)
            self.assertIn('each("dialogue_yellow"', main)
            self.assertIn('each("strings_yellow"', main)
            self.assertIn('"A: FINI"', (mod / "lang" / "strings_yellow.lua").read_text(encoding="utf-8"))
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["yellow"]["layer"]["layer_entries"], 1)

    def test_generate_mod_reuses_precomputed_join_instead_of_rejoining(self):
        # The universal-mod builder already runs join_catalogs once (to diff
        # Red/Blue against Yellow) before calling generate_mod; passing that
        # result through as precomputed_join must skip the second, redundant
        # match pass over the same rows/worksheet.
        from unittest.mock import patch
        from pipeline.corpus import parse_redblue
        from pipeline.mod import generate_mod
        from pipeline.join import join_catalogs, read_worksheets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "RedBlue"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text("rb.text.HelloText\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour\n", encoding="utf-8")
            rows = align(parse_redblue(corpus, "fr"), target_lang="fr")
            worksheet = self._write_minimal_worksheet(root)
            worksheets = read_worksheets(worksheet)
            joined, join_report = join_catalogs(rows, worksheets, "fr")
            mod = root / "mod"
            with patch("pipeline.mod.join_catalogs") as mocked_join:
                generate_mod(rows, mod, mod_id="translation-fr", language="fr",
                             modkit_worksheet=worksheet,
                             precomputed_join=(joined, join_report))
            mocked_join.assert_not_called()
            self.assertTrue((mod / "lang" / "dialogue.lua").is_file())

    def test_generate_mod_removes_stale_yellow_catalogs(self):
        from pipeline.corpus import parse_redblue
        from pipeline.mod import generate_mod
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "RedBlue"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text("rb.text.HelloText\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hello\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Bonjour\n", encoding="utf-8")
            rows = align(parse_redblue(corpus, "fr"), target_lang="fr")
            worksheet = self._write_minimal_worksheet(root)
            mod = root / "mod"
            (mod / "lang").mkdir(parents=True)
            (mod / "lang" / "dialogue_yellow.lua").write_text("return {}\n", encoding="utf-8")
            generate_mod(rows, mod, modkit_worksheet=worksheet, yellow_dialogue=None)
            self.assertFalse((mod / "lang" / "dialogue_yellow.lua").exists())

    def test_preserve_scaffold_support_injects_yellow_hook(self):
        from pipeline.builder import preserve_scaffold_support
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                "  counts.dialogue = each(\"dialogue\", function(id, value)\n"
                "    mod.content.text:override(id, value)\n"
                "  end)\n"
                "  counts.statuses = each(\"status_labels\", function(id, value)\n"
                "    mod.content.statuses:patch(id, { label = value })\n"
                "  end)\n"
                "end\n", encoding="utf-8")
            (mod / "lang" / "dialogue_yellow.lua").write_text('return {}\n', encoding="utf-8")
            preserve_scaffold_support(scaffold, mod, "fr")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn("GameVersion.isYellow()", main)
            self.assertIn("GameVersion.isYellow()", main)
            self.assertIn('each("dialogue_yellow"', main)

    def test_merge_engine_overrides_merges_shared_then_yellow(self):
        import json
        from pipeline.builder import _merge_engine_overrides
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared.json"
            yellow = root / "yellow.json"
            shared.write_text(json.dumps({"schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                                          "entries": {"FOE": {"override": "ENNEMI"}}}), encoding="utf-8")
            yellow.write_text(json.dumps({"schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                                          "entries": {"FOE": {"override": "ADVERSAIRE"}, "A: done": {"override": "OK"}}}), encoding="utf-8")
            out = _merge_engine_overrides(shared, yellow, destination_dir=root / "tmp", name="merged_fr.json")
            merged = json.loads(out.read_text(encoding="utf-8"))["entries"]
            self.assertEqual(merged["FOE"]["override"], "ADVERSAIRE")  # yellow wins
            self.assertEqual(merged["A: done"]["override"], "OK")
            self.assertIsNone(_merge_engine_overrides(None, None, destination_dir=root / "tmp"))

    def test_parse_yellow_resolves_corpus_subdirectory(self):
        from pipeline.corpus import parse_yellow
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus" / "Yellow"
            corpus.mkdir(parents=True)
            (corpus / "qid_msg.txt").write_text("y.text.A\n", encoding="utf-8")
            (corpus / "en_msg.txt").write_text("Hi\n", encoding="utf-8")
            (corpus / "fr_msg.txt").write_text("Salut\n", encoding="utf-8")
            records = parse_yellow(root, "fr")
            self.assertEqual([r.qid for r in records if r.language == "fr"], ["y.text.A"])


class EscapeDecodingTests(unittest.TestCase):
    """parse_text_catalog single-pass escape decoding (review regression)."""

    def _parse(self, raw: str) -> str:
        from pipeline.yellow import parse_text_catalog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.lua"
            path.write_text(f'return {{\n  Key = "{raw}",\n}}\n', encoding="utf-8")
            return parse_text_catalog(path)["Key"]

    def test_decimal_and_hex_escapes_decode(self):
        # \011 (form feed) and \012 (line feed) are the live import escapes.
        self.assertEqual(self._parse(r"a\011b\012c"), "a\x0bb\x0cc")
        self.assertEqual(self._parse(r"a\x41b"), "aAb")

    def test_doubled_backslash_keeps_text_literal(self):
        # \\x41 must NOT decode to 'A': the doubled backslash is the literal
        # escape, so the following 'x41' is plain text.
        self.assertEqual(self._parse(r"\\x41"), "\\x41")
        self.assertEqual(self._parse(r"\\123"), "\\123")

    def test_plain_digit_runs_and_unknown_escapes_are_untouched(self):
        self.assertEqual(self._parse(r"route 123"), "route 123")
        self.assertEqual(self._parse(r"abc\q"), "abc\\q")


class YellowAuditFallbackTests(unittest.TestCase):
    def test_audit_marks_rom_fallback_as_untranslated(self):
        import json
        from pipeline.yellow_audit import write_yellow_audit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            red_text = {"_RivalIntroText": "Red's rival is here!"}
            yellow_text = {"_RivalIntroText": "Yellow rival introduces himself!"}
            out = root / "audit"
            path = write_yellow_audit(Path("red.lua"), Path("yellow.lua"), [], "fr", out,
                                      red_text=red_text, yellow_text=yellow_text)
            audit = json.loads(path.read_text(encoding="utf-8"))
            entry = audit["versioned"][0]
            self.assertFalse(entry["translated"])
            self.assertTrue(entry["rom_fallback"])
            self.assertIn("_RivalIntroText", audit["unmatched_labels"])
            self.assertEqual(audit["statuses"]["unmatched"], 1)

    def test_translation_equal_to_english_is_not_misclassified_as_fallback(self):
        # Regression: a genuine corpus translation can legitimately equal the
        # ROM's own English content — e.g. a pure RAM-token placeholder with
        # nothing to translate, such as "{RAM:wBattleMonNick} ". The old
        # `fr == yellow_text.get(label)` heuristic flagged this as an
        # untranslated ROM fallback; it must be reported as translated.
        import json
        from pipeline.yellow_audit import write_yellow_audit
        content = "{RAM:wBattleMonNick} "
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            red_text = {"_PlayerMon2Text": content}
            yellow_text = {"_PlayerMon2Text": content}
            records = [
                Alignment("y.text.PlayerMon2Text", "yellow",
                          CorpusRecord("y.text.PlayerMon2Text", "en", content, "yellow", "en"),
                          CorpusRecord("y.text.PlayerMon2Text", "fr", content, "yellow", "fr"),
                          "anchor"),
            ]
            out = root / "audit"
            path = write_yellow_audit(Path("red.lua"), Path("yellow.lua"), records, "fr", out,
                                      red_text=red_text, yellow_text=yellow_text,
                                      red_translation={})
            audit = json.loads(path.read_text(encoding="utf-8"))
            entry = audit["translation_variants"][0]
            self.assertTrue(entry["translated"])
            self.assertFalse(entry["rom_fallback"])
            self.assertNotIn("_PlayerMon2Text", audit["unmatched_labels"])


class YellowCoverageExceptionsTests(unittest.TestCase):
    def test_loads_entries_as_frozensets_per_language(self):
        from pipeline.builder import load_yellow_coverage_exceptions
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yellow_coverage_exceptions.json"
            path.write_text(
                '{"schema": "x", "version": 1, "entries": '
                '{"it": {"_RoseText": {"reason": "composition"}}}}',
                encoding="utf-8",
            )
            overrides = load_yellow_coverage_exceptions(path)
        self.assertEqual(overrides, {"it": frozenset({"_RoseText"})})

    def test_missing_file_returns_empty_mapping(self):
        from pipeline.builder import load_yellow_coverage_exceptions
        self.assertEqual(load_yellow_coverage_exceptions(Path("/nonexistent.json")), {})

    def test_repo_config_matches_expected_italian_entry(self):
        from pipeline.builder import load_yellow_coverage_exceptions
        from pipeline.project import resource_root
        overrides = load_yellow_coverage_exceptions(
            resource_root() / "config" / "rby" / "yellow_coverage_exceptions.json"
        )
        self.assertEqual(overrides.get("it"), frozenset({"_RoseText"}))


if __name__ == "__main__":
    unittest.main()
