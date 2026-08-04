import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pipeline.align import CORPUS_OVERRIDES_SCHEMA, align, apply_corpus_overrides
from pipeline.cli import main as cli_main
from pipeline.corpus import load_corpus
from pipeline.generate import generate_lua, lua_string
from pipeline.model import CorpusRecord
from pipeline.mod import generate_mod
from pipeline.join import demo_names_catalog, join_catalogs, read_worksheets, type_names_catalog, WorksheetEntry
from pipeline.tokens import check_placeholders, encode
from pipeline.validate import release_gate, validate
from pipeline.worksheet import dump, load


class PipelineTests(unittest.TestCase):
    def test_corpus_override_skeletons_have_explicit_schema_and_are_empty(self):
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            with self.subTest(language=language):
                body = json.loads((Path("overrides") / language / "corpus_overrides.json").read_text(encoding="utf-8"))
                self.assertEqual(body, {"schema": CORPUS_OVERRIDES_SCHEMA, "version": 1, "entries": {}})

    def test_corpus_overrides_are_qid_scoped_and_empty_file_is_noop(self):
        rows = align([
            CorpusRecord("a", "en", "A"), CorpusRecord("a", "fr", "Un"),
            CorpusRecord("b", "en", "B"), CorpusRecord("b", "fr", "Deux"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus_overrides.json"
            path.write_text(json.dumps({"schema": CORPUS_OVERRIDES_SCHEMA, "version": 1, "entries": {}}), encoding="utf-8")
            self.assertEqual([row.translation for row in apply_corpus_overrides(rows, path)], ["Un", "Deux"])
            path.write_text(json.dumps({"schema": CORPUS_OVERRIDES_SCHEMA, "version": 1, "entries": {"a": {"override": "Une"}, "orphan": {"override": "X"}}}), encoding="utf-8")
            self.assertEqual([row.translation for row in apply_corpus_overrides(rows, path)], ["Une", "Deux"])

    def test_cli_release_engine_warning_is_printed_without_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aligned = root / "aligned.json"
            aligned.write_text(json.dumps([{
                "qid": "a", "game": "red", "english": "A", "translation": "Un",
            }]), encoding="utf-8")
            charmap = root / "charmap.json"
            charmap.write_text(json.dumps({"U": 1, "n": 2}), encoding="utf-8")
            coverage = root / "coverage.json"
            coverage.write_text(json.dumps({
                "unmatched": {}, "ambiguous": {},
                "rom": {"translated": 1, "total": 1, "percent": 100.0},
                "engine": {"translated": 1, "total": 2, "percent": 50.0, "unmatched": ["X"]},
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = cli_main([
                    "validate", str(aligned), "--release",
                    "--charmap", str(charmap), "--coverage", str(coverage),
                ])
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["ok"])
        self.assertTrue(any(f["rule"] == "coverage-engine-unmatched" for f in report["findings"]))
    def test_cli_generate_defaults_engine_overrides_to_language_tree(self):
        for language, expected in (
            ("fr", "overrides/fr/engine_overrides.json"),
            ("es", "overrides/es/engine_overrides.json"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                aligned = Path(tmp) / "aligned.json"
                aligned.write_text(json.dumps([{
                    "qid": "example", "game": "red", "english": "HELLO",
                    "target_lang": language, "translation": "BONJOUR",
                }]), encoding="utf-8")
                output = Path(tmp) / "mod"
                with patch("pipeline.cli.generate_mod") as generate:
                    self.assertEqual(cli_main([
                        "generate", str(aligned), "-o", str(output),
                        "--target-lang", language,
                    ]), 0)
                self.assertEqual(
                    generate.call_args.kwargs["engine_overrides"], expected,
                )

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
            sheet = Path(tmp) / "corpus_overrides.json"
            dump(items, sheet, "fixture")
            body = json.loads(sheet.read_text())
            body["entries"]["a"] = {"override": "Une", "justification": "in-game spelling"}
            sheet.write_text(json.dumps(body), encoding="utf-8")
            apply_corpus_overrides(items, sheet)
            dumped = load(sheet)
            self.assertEqual(items[0].translation, "Une")
            self.assertNotIn("english", dumped)
            self.assertNotIn("reviewed", dumped["entries"]["a"])
            self.assertNotIn("notes", dumped["entries"]["a"])
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
                "schema": "gen1recomp-translation-mods/corpus-overrides",
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
                "--corpus-overrides", str(overrides),
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

    def test_lua_string_pads_control_escapes_before_digits(self):
        self.assertEqual(lua_string("Rapport:\f1er"), '"Rapport:\\0121er"')
        self.assertEqual(lua_string("Note:\v2e"), '"Note:\\0112e"')

    def test_read_worksheets_repairs_cp1252_mojibake(self):
        def mojibake(value):
            return value.encode("utf-8").decode("cp1252")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "item_names.txt").write_text(
                f'"{mojibake("POKéDEX")}"\t"{mojibake("POKé BALL")}"\n'
                f'"{mojibake("NIDORAN♀")}"\t"{mojibake("NIDORAN♂")}"\n'
                '"POKéMON"\t"NIDORAN♀"\n'
                '"日本語"\t"日本語"\n',
                encoding="utf-8",
            )
            entries = read_worksheets(root)["item_names"]

        self.assertEqual(
            [(entry.key, entry.english) for entry in entries],
            [
                ("POKéDEX", "POKé BALL"),
                ("NIDORAN♀", "NIDORAN♂"),
                ("POKéMON", "NIDORAN♀"),
                ("日本語", "日本語"),
            ],
        )

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

    def test_type_names_join_uses_runtime_ids_and_excludes_bird(self):
        rows = align([
            CorpusRecord("rb.names.TypeNames.Fire", "en", "FIRE@"),
            CorpusRecord("rb.names.TypeNames.Fire", "fr", "FEU@"),
            CorpusRecord("rb.names.TypeNames.Psychic", "en", "PSYCHIC@"),
            CorpusRecord("rb.names.TypeNames.Psychic", "fr", "PSY@"),
            CorpusRecord("rb.names.TypeNames.Bird", "en", "BIRD@"),
            CorpusRecord("rb.names.TypeNames.Bird", "fr", "OISEAU@"),
            CorpusRecord("rb.names.TypeNames.Water", "en", "WATER@"),
        ])
        worksheets = {name: [] for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")}
        output, report = join_catalogs(rows, worksheets)
        self.assertEqual(output["type_names"]["FIRE"], "FEU")
        self.assertEqual(output["type_names"]["PSYCHIC_TYPE"], "PSY")
        # The engine registers no Bird record, so the corpus row is recorded
        # as excluded instead of emitted.
        self.assertNotIn("BIRD", output["type_names"])
        self.assertEqual(report["type_names"]["excluded"]["Bird"]["qid"], "rb.names.TypeNames.Bird")
        # An English-only row stays empty (runtime English fallback); the
        # other runtime ids without corpus rows are unmatched too.
        self.assertEqual(output["type_names"]["WATER"], "")
        self.assertEqual(report["matched"]["type_names"], 2)
        self.assertIn("WATER", report["unmatched"]["type_names"])
        self.assertNotIn("FIRE", report["unmatched"]["type_names"])
        self.assertEqual(report["strategies"]["type_names"]["FIRE"], "type_name_qid")

    def test_type_names_catalog_is_empty_without_corpus_rows(self):
        rows = align([
            CorpusRecord("rb.names.SpeciesNames", "en", "ABRA"),
            CorpusRecord("rb.names.SpeciesNames", "fr", "ABRA"),
        ])
        values, report = type_names_catalog(rows, "fr")
        self.assertEqual(values, {})
        self.assertEqual(report["translated"], 0)
        self.assertIn("Bird", report["excluded"])

    def test_generate_mod_writes_type_names_catalog_and_main_patch(self):
        rows = align([
            CorpusRecord("rb.names.TypeNames.Fire", "en", "FIRE@"),
            CorpusRecord("rb.names.TypeNames.Fire", "fr", "FEU@"),
            CorpusRecord("rb.names.TypeNames.Psychic", "en", "PSYCHIC@"),
            CorpusRecord("rb.names.TypeNames.Psychic", "fr", "PSY@"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"; ws.mkdir()
            for name in ("dialogue", "species_names", "move_names", "item_names", "trainer_names", "status_labels"):
                (ws / f"{name}.txt").write_text("# header\n", encoding="utf-8")
            (ws / "strings.lua").write_text('return { ["X"] = "" }\n', encoding="utf-8")
            mod = generate_mod(rows, root / "mod", language="fr", modkit_worksheet=ws, strict_engine=True)
            body = (mod / "lang/type_names.lua").read_text(encoding="utf-8")
            self.assertIn('["FIRE"] = "FEU"', body)
            self.assertIn('["PSYCHIC_TYPE"] = "PSY"', body)
            self.assertNotIn("BIRD", body)
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('by_english[canonical] = localized', main)
            self.assertIn('Font.draw = function(text, x, y, ...)', main)
            self.assertIn('local demo_names = catalog("demo_names")', main)
            self.assertIn('BS.oldManThrow = function(self, ...)', main)
            self.assertIn('localizedDemoName(self, canonical)', main)
            self.assertIn('self.demoName = localized', main)
            self.assertNotIn('Runtime.hooks:wrap("player.sprite"', main)
            self.assertNotIn('BS.makeOldManDemo = function', main)
            self.assertNotIn('mod.content.type_chart:patch', main)

    def test_generate_mod_without_worksheet_uses_runtime_type_ids(self):
        rows = align([
            CorpusRecord("rb.names.TypeNames.Fire", "en", "FIRE@"),
            CorpusRecord("rb.names.TypeNames.Fire", "fr", "FEU@"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            mod = generate_mod(rows, Path(tmp) / "mod", language="fr")
            body = (mod / "lang/type_names.lua").read_text(encoding="utf-8")
            self.assertIn('["FIRE"] = "FEU"', body)
            self.assertNotIn("rb.names.TypeNames", body)


    def test_demo_names_join_uses_corpus_literal(self):
        rows = align([
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "en", "OLD MAN@"),
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "fr", "VIEILLARD@"),
        ])
        values, report = demo_names_catalog(rows, "fr")
        self.assertEqual(values, {"OLD MAN": "VIEILLARD"})
        self.assertEqual(report["translated"], 1)
        self.assertEqual(report["unmatched"], ["PROF.OAK"])
        self.assertEqual(report["strategies"]["OLD MAN"], "demo_name_qid")

    def test_demo_names_catalog_empty_without_corpus_rows(self):
        values, report = demo_names_catalog([], "fr")
        self.assertEqual(values, {})
        self.assertEqual(report["translated"], 0)
        self.assertEqual(report["unmatched"], ["OLD MAN", "PROF.OAK"])

    def test_demo_names_join_covers_both_engine_literals(self):
        rows = align([
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "en", "OLD MAN@"),
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "fr", "VIEILLARD@"),
            CorpusRecord("rb.name_pointers.TrainerNamePointers.ProfOakName", "en", "PROF.OAK@"),
            CorpusRecord("rb.name_pointers.TrainerNamePointers.ProfOakName", "fr", "PROF.CHEN@"),
        ])
        values, report = demo_names_catalog(rows, "fr")
        self.assertEqual(values, {"OLD MAN": "VIEILLARD", "PROF.OAK": "PROF.CHEN"})
        self.assertEqual(report["translated"], 2)
        self.assertEqual(report["unmatched"], [])

    def test_demo_names_join_counts_empty_translation(self):
        # A corpus row with an empty translation must still be counted by the
        # gate: the literal is emitted empty (English fallback at runtime) and
        # the catalog reports it unmatched so rom coverage fails the 100% gate.
        rows = align([
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "en", "OLD MAN@"),
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "fr", ""),
        ])
        values, report = demo_names_catalog(rows, "fr")
        self.assertEqual(values, {"OLD MAN": ""})
        self.assertEqual(report["translated"], 0)
        self.assertEqual(report["unmatched"], ["OLD MAN", "PROF.OAK"])

    def test_generate_mod_writes_demo_names_catalog(self):
        rows = align([
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "en", "OLD MAN@"),
            CorpusRecord("rb.core.DisplayBattleMenu.oldManName", "fr", "VIEILLARD@"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            mod = generate_mod(rows, Path(tmp) / "mod", language="fr")
            body = (mod / "lang/demo_names.lua").read_text(encoding="utf-8")
            self.assertIn('["OLD MAN"] = "VIEILLARD"', body)


if __name__ == "__main__":
    unittest.main()
