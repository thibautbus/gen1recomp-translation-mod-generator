import json
import tempfile
import unittest
from pathlib import Path

from pipeline.align import align, apply_overrides
from pipeline.cli import main as cli_main
from pipeline.corpus import load_corpus
from pipeline.generate import generate_lua
from pipeline.model import CorpusRecord
from pipeline.mod import generate_mod
from pipeline.join import join_catalogs, WorksheetEntry
from pipeline.tokens import check_placeholders, encode
from pipeline.validate import release_gate, validate
from pipeline.worksheet import dump, load


class PipelineTests(unittest.TestCase):
    def test_corpus_json_and_qid_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "redblue.jsonl"
            source.write_text(json.dumps({"qid": "intro.1", "language": "en", "text": "Hello <PLAYER>"}) + "\n" + json.dumps({"qid": "intro.1", "language": "fr", "text": "Bonjour <PLAYER>"}) + "\n", encoding="utf-8")
            result = align(load_corpus(tmp))
        self.assertEqual(result[0].method, "qid")
        self.assertEqual(result[0].translation, "Bonjour <PLAYER>")

    def test_exact_english_fallback_only(self):
        records = [CorpusRecord(None, "en", "Same"), CorpusRecord("x", "fr", "Même", english="Same")]
        self.assertEqual(align(records)[0].method, "english-exact")
        records[1].english = "same"
        self.assertEqual(align(records)[0].method, "unmatched")

    def test_overrides_and_release_require_technical_checks(self):
        records = [CorpusRecord("a", "en", "A"), CorpusRecord("a", "fr", "Un")]
        items = align(records)
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "overrides.json"
            dump(items, sheet, "fixture")
            body = json.loads(sheet.read_text())
            body["entries"]["a"] = {"override": "Une", "justification": "in-game spelling"}
            sheet.write_text(json.dumps(body), encoding="utf-8")
            apply_overrides(items, sheet)
            dumped = load(sheet)
            self.assertEqual(items[0].translation, "Une")
            self.assertNotIn("english", dumped)
            self.assertNotIn("reviewed", dumped["entries"]["a"])
            self.assertNotIn("notes", dumped["entries"]["a"])
            legacy = Path(tmp) / "legacy.json"
            legacy.write_text(json.dumps({"schema": "gen1recomp-translation-mods/worksheet", "version": 2,
                                          "entries": {"a": {"override": "Une", "review": {"reviewed": True}}}}), encoding="utf-8")
            migrated = load(legacy)
            self.assertEqual(migrated["entries"]["a"], {"override": "Une"})
        findings = validate(items)
        self.assertFalse(release_gate(items, findings)[0])
        coverage = {"unmatched": {}, "ambiguous": {},
                    "rom": {"translated": 1, "total": 1, "percent": 100.0},
                    "engine": {"translated": 1, "total": 1, "percent": 100.0}}
        self.assertTrue(release_gate(items, validate(items, {"U": 1, "n": 2, "e": 3}), {"U": 1, "n": 2, "e": 3}, coverage)[0])

    def test_generate_never_rewrites_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aligned = root / "aligned.json"
            aligned.write_text(json.dumps([{
                "qid": "a", "game": "both", "english": "A",
                "french": "Un", "method": "qid",
            }]), encoding="utf-8")
            overrides = root / "overrides.json"
            original = {
                "schema": "gen1recomp-translation-mods/overrides",
                "version": 1,
                "entries": {
                    "a": {"override": "Une", "justification": "test ingame"},
                    "orphan": {"override": "Conserver", "justification": "ancienne clé"},
                },
            }
            overrides.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
            before = overrides.read_bytes()
            self.assertEqual(cli_main([
                "generate", str(aligned), "-o", str(root / "fr.lua"),
                "--overrides", str(overrides),
            ]), 0)
            self.assertEqual(overrides.read_bytes(), before)

    def test_placeholder_multiplicity_and_glyph_encoding(self):
        self.assertTrue(check_placeholders("A <PLAYER> <PLAYER>@", "B <PLAYER>@"))
        self.assertEqual(check_placeholders("A <PLAYER>@", "B <PLAYER>@"), [])
        self.assertEqual(encode("AB<T>", {"A": 1, "B": 2}, {"<T>": 3}), bytes([1, 2, 3]))

    def test_lua_and_mod_generation(self):
        items = align([CorpusRecord("rb.names.ItemNames.1", "en", "POTION"), CorpusRecord("rb.names.ItemNames.1", "fr", "POTION")])
        with tempfile.TemporaryDirectory() as tmp:
            output = generate_lua(items, Path(tmp) / "lang/fr.lua")
            self.assertIn("POTION", output.read_text())
            mod = generate_mod(items, Path(tmp) / "mod")
            self.assertTrue((mod / "main.lua").exists())
            self.assertTrue((mod / "lang/item_names.lua").exists())
            self.assertTrue((Path(str(mod) + "-worksheet") / "item_names.txt").exists())

    def test_modkit_worksheet_join_preserves_unmatched_keys(self):
        items = align([
            CorpusRecord("rb.text_2.AIBattleUseItemText", "en", "Use item"),
            CorpusRecord("rb.text_2.AIBattleUseItemText", "fr", "Utiliser"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"; ws.mkdir()
            for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels"):
                (ws / f"{name}.txt").write_text("# header\n", encoding="utf-8")
            (ws / "dialogue.txt").write_text('"_AIBattleUseItemText"\t"Use item"\n"_Missing"\t"Missing"\n', encoding="utf-8")
            mod = generate_mod(items, Path(tmp) / "mod", modkit_worksheet=ws)
            body = (mod / "lang/dialogue.lua").read_text()
            self.assertIn('["_AIBattleUseItemText"] = "Utiliser"', body)
            self.assertIn('["_Missing"] = ""', body)

    def test_catalog_join_uses_canonical_qids_and_engine_aliases(self):
        rows = align([
            CorpusRecord("rb.dex_entries.AbraDexEntry.Species", "en", "DEX"),
            CorpusRecord("rb.dex_entries.AbraDexEntry.Species", "fr", "ESPECE"),
            CorpusRecord("rb.dex_text^RG.AbraDexEntry", "en", "DEX"),
            CorpusRecord("rb.dex_text^RG.AbraDexEntry", "fr", "[NULL]"),
            CorpusRecord("rb.dex_text.AbraDexEntry", "en", "DEX"),
            CorpusRecord("rb.dex_text.AbraDexEntry", "fr", "DESCRIPTION"),
            CorpusRecord("rb.text_2.ExclamationPoint1Text", "en", "!"),
            CorpusRecord("rb.text_2.ExclamationPoint1Text", "fr", ""),
            CorpusRecord("rb.text_1.ExclamationText", "en", "!"),
            CorpusRecord("rb.text_1.ExclamationText", "fr", "!"),
        ])
        worksheets = {name: [] for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")}
        worksheets["dialogue"] = [
            WorksheetEntry("_AbraDexEntry", "DEX", "dialogue"),
            WorksheetEntry("_EndUsedMove1Text", "!", "dialogue"),
        ]
        output, report = join_catalogs(rows, worksheets)
        self.assertEqual(output["dialogue"]["_AbraDexEntry"], "DESCRIPTION")
        self.assertEqual(output["dialogue"]["_EndUsedMove1Text"], "")
        self.assertEqual(report["unmatched"], {})
        self.assertEqual(report["ambiguous"], {})
        self.assertEqual(report["strategies"]["dialogue"]["_AbraDexEntry"], "canonical_dex_text")
        self.assertEqual(report["strategies"]["dialogue"]["_EndUsedMove1Text"], "engine_alias")

    def test_item_machine_identifiers_use_french_ct_cs_display(self):
        worksheets = {name: [] for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")}
        worksheets["item_names"] = [
            WorksheetEntry("TM_BIDE", "TM34", "item_names"),
            WorksheetEntry("HM_CUT", "HM01", "item_names"),
        ]
        rows = align([
            CorpusRecord("rb.names.TechnicalPrefix", "en", "TM"),
            CorpusRecord("rb.names.TechnicalPrefix", "fr", "CT"),
            CorpusRecord("rb.names.HiddenPrefix", "en", "HM"),
            CorpusRecord("rb.names.HiddenPrefix", "fr", "CS"),
            CorpusRecord("rb.list_menu.InitialQuantityText", "en", "×01@"),
            CorpusRecord("rb.list_menu.InitialQuantityText", "fr", "×01@"),
        ])
        output, report = join_catalogs(rows, worksheets)
        self.assertEqual(output["item_names"], {"TM_BIDE": "CT34", "HM_CUT": "CS01"})
        self.assertEqual(report["unmatched"], {})
        self.assertEqual(report["strategies"]["item_names"]["TM_BIDE"], "official_machine_display")

    def test_item_machine_identifiers_need_corpus_anchors(self):
        worksheets = {name: [] for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")}
        worksheets["item_names"] = [WorksheetEntry("TM_BIDE", "TM34", "item_names")]
        output, report = join_catalogs([], worksheets)
        self.assertEqual(output["item_names"]["TM_BIDE"], "")
        self.assertEqual(report["unmatched"]["item_names"], ["TM_BIDE"])

    def test_missing_french_candidate_is_not_counted_as_matched(self):
        rows = align([CorpusRecord("rb.text.MissingText", "en", "Missing")])
        worksheets = {name: [] for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")}
        worksheets["dialogue"] = [WorksheetEntry("_MissingText", "Missing", "dialogue")]
        output, report = join_catalogs(rows, worksheets)
        self.assertEqual(output["dialogue"]["_MissingText"], "")
        self.assertEqual(report["unmatched"]["dialogue"], ["_MissingText"])
        self.assertNotIn("dialogue", report["matched"])


if __name__ == "__main__":
    unittest.main()
