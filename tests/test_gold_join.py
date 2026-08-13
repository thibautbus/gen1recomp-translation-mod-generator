import tempfile
import unittest
from pathlib import Path

from pipeline.gold_text import GoldTextRecord
from pipeline.gold_join import (
    HARMLESS_AMBIGUOUS, MAP_CONTEXT, MARKUP_ONLY, NO_MATCH, OVERRIDE, UNIQUE, UNRESOLVED,
    audit_join, convert_manifest_map_name, gold_charmap, gold_coverage_report, join_gold_pointers,
    load_map_banks, read_corpus_rows, to_aligned_rows, unresolved_report,
)


class ConvertManifestMapNameTests(unittest.TestCase):
    def test_simple_names(self):
        self.assertEqual(convert_manifest_map_name("AZALEA_GYM"), "AzaleaGym")
        self.assertEqual(convert_manifest_map_name("CHARCOAL_KILN"), "CharcoalKiln")

    def test_floor_suffixes_stay_uppercase(self):
        self.assertEqual(convert_manifest_map_name("BLACKTHORN_GYM_1F"), "BlackthornGym1F")
        self.assertEqual(convert_manifest_map_name("DRAGONS_DEN_B1F"), "DragonsDenB1F")

    def test_route_numbers(self):
        self.assertEqual(convert_manifest_map_name("ROUTE_34_ILEX_FOREST_GATE"), "Route34IlexForestGate")


class LoadMapBanksTests(unittest.TestCase):
    def test_reads_and_converts_map_bank_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gold_maps.tsv"
            path.write_text("ILEX_FOREST\t45\nCHARCOAL_KILN\t55\n", encoding="utf-8")
            banks = load_map_banks(path)
            self.assertEqual(banks, {"IlexForest": {"45"}, "CharcoalKiln": {"55"}})


class ReadCorpusRowsTests(unittest.TestCase):
    def test_reads_parallel_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "qid_msg.txt").write_text("gs.a.One\ngs.b.Two\n", encoding="utf-8")
            (root / "en_msg.txt").write_text("Hello\nWorld\n", encoding="utf-8")
            (root / "fr_msg.txt").write_text("Bonjour\nMonde\n", encoding="utf-8")
            rows = read_corpus_rows(root)
            self.assertEqual(rows, [("gs.a.One", "Hello", "Bonjour"), ("gs.b.Two", "World", "Monde")])

    def test_rejects_non_parallel_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "qid_msg.txt").write_text("gs.a.One\n", encoding="utf-8")
            (root / "en_msg.txt").write_text("Hello\nExtra\n", encoding="utf-8")
            (root / "fr_msg.txt").write_text("Bonjour\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not parallel"):
                read_corpus_rows(root)


class JoinGoldPointersTests(unittest.TestCase):
    def test_unique_match(self):
        records = [GoldTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, stats = join_gold_pointers(records, rows)
        self.assertEqual(entries[0].provenance, UNIQUE)
        self.assertEqual(entries[0].translation, "Bonjour!")
        self.assertEqual(entries[0].qid, "gs.a.Greeting")
        self.assertEqual(stats["unique"], 1)

    def test_markup_only_needs_no_translation(self):
        records = [GoldTextRecord("55:0001", "<PARA><DONE>")]
        entries, stats = join_gold_pointers(records, [])
        self.assertEqual(entries[0].provenance, MARKUP_ONLY)
        self.assertEqual(stats["markup_only"], 1)

    def test_markup_only_sound_and_timing_controls_are_dropped(self):
        # Regression test: the ROM's own decoder (RomExtractorGen2.lua's
        # TEXT_NO_GLYPH branch) drops these bytes from decoded text with no
        # replacement and no side effect visible to a mod override, so
        # dropping the corpus's markup for them here must not raise --
        # earlier code briefly treated this as unsafe and made a real,
        # boot-verified build of the actual corpus crash outright.
        for control in ("{sound_item}", "{text_pause}"):
            with self.subTest(control=control):
                records = [GoldTextRecord("55:0001", control)]
                entries, _ = join_gold_pointers(records, [])
                self.assertEqual(entries[0].translation, "")

    def test_no_match_keeps_english_and_is_flagged(self):
        records = [GoldTextRecord("55:0001", "Nothing matches this.")]
        entries, stats = join_gold_pointers(records, [("gs.a.Other", "Something else.", "Autre chose.")])
        self.assertEqual(entries[0].provenance, NO_MATCH)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(stats["no_match"], 1)

    def test_harmless_ambiguity_picks_either_identical_translation(self):
        records = [GoldTextRecord("55:0001", "Same text.")]
        rows = [("gs.a.One", "Same text.", "Meme texte."), ("gs.b.Two", "Same text.", "Meme texte.")]
        entries, stats = join_gold_pointers(records, rows)
        self.assertEqual(entries[0].provenance, HARMLESS_AMBIGUOUS)
        self.assertEqual(entries[0].translation, "Meme texte.")
        self.assertEqual(stats["harmless_ambiguous"], 1)

    def test_command_difference_is_not_erased_by_ambiguity_normalisation(self):
        records = [GoldTextRecord("55:0001", "Same text.")]
        rows = [
            ("gs.a.One", "Same text.", "Même texte<LINE>!"),
            ("gs.b.Two", "Same text.", "Même texte<CONT>!"),
        ]
        entries, stats = join_gold_pointers(records, rows)
        self.assertEqual(entries[0].provenance, UNRESOLVED)
        self.assertEqual(stats["unresolved"], 1)

    def test_reachable_sound_control_is_dropped_not_fatal(self):
        records = [GoldTextRecord("55:0001", "Same text.")]
        rows = [("gs.a.One", "Same text.", "Même texte{sound_item}")]
        entries, _ = join_gold_pointers(records, rows)
        self.assertEqual(entries[0].translation, "Même texte")

    def test_map_context_disambiguates_when_banks_differ(self):
        records = [GoldTextRecord("45:0001", "What?!")]
        rows = [
            ("gs.IlexForest.Grunt", "What?!", "Quoi?!"),
            ("gs.CharcoalKiln.Grunt", "What?!", "De quoi?!"),
        ]
        map_banks = {"IlexForest": {"45"}, "CharcoalKiln": {"55"}}
        entries, stats = join_gold_pointers(records, rows, map_banks=map_banks)
        self.assertEqual(entries[0].provenance, MAP_CONTEXT)
        self.assertEqual(entries[0].translation, "Quoi?!")
        self.assertEqual(entries[0].qid, "gs.IlexForest.Grunt")
        self.assertEqual(stats["map_context"], 1)

    def test_unresolved_when_map_context_does_not_narrow_it_down(self):
        # Both candidates live on the same map (a lost/won branch pair):
        # map context cannot distinguish them, so this must fall back
        # rather than silently pick one.
        records = [GoldTextRecord("48:0001", "...\x0cMy name's ???.")]
        rows = [
            ("gs.CherrygroveCity.YouLost", "...\x0cMy name's ???.", "...Perdu."),
            ("gs.CherrygroveCity.YouWon", "...\x0cMy name's ???.", "...Gagne."),
        ]
        map_banks = {"CherrygroveCity": {"48"}}
        entries, stats = join_gold_pointers(records, rows, map_banks=map_banks)
        self.assertEqual(entries[0].provenance, UNRESOLVED)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(entries[0].candidate_qids, ("gs.CherrygroveCity.YouLost", "gs.CherrygroveCity.YouWon"))
        self.assertEqual(stats["unresolved"], 1)

    def test_override_wins_over_everything_else(self):
        records = [GoldTextRecord("45:0001", "What?!")]
        rows = [
            ("gs.IlexForest.Grunt", "What?!", "Quoi?!"),
            ("gs.CharcoalKiln.Grunt", "What?!", "De quoi?!"),
        ]
        entries, stats = join_gold_pointers(records, rows, overrides={"45:0001": "Hand-picked!"})
        self.assertEqual(entries[0].provenance, OVERRIDE)
        self.assertEqual(entries[0].translation, "Hand-picked!")
        self.assertEqual(stats["override"], 1)

    def test_translation_is_converted_to_engine_form(self):
        records = [GoldTextRecord("55:0001", "Hello<LINE>there!")]
        rows = [("gs.a.Greeting", "Hello<LINE>there!", "Bonjour<LINE>!")]
        entries, _ = join_gold_pointers(records, rows)
        self.assertEqual(entries[0].translation, "Bonjour\n!")


class AuditJoinTests(unittest.TestCase):
    def test_flags_duplicate_pointers(self):
        records = [GoldTextRecord("55:0001", "A"), GoldTextRecord("55:0001", "A")]
        # join_gold_pointers itself never produces duplicates from
        # parse_gold_text_catalog's deduped records; construct the
        # collision directly to exercise the audit's own check.
        entries, _ = join_gold_pointers(records[:1], [("gs.a.One", "A", "B")])
        entries = entries + entries
        problems = audit_join(entries)
        self.assertTrue(any("duplicate pointer" in p for p in problems))

    def test_flags_an_unknown_token_in_shipped_output(self):
        records = [GoldTextRecord("55:0001", "Hi")]
        entries, _ = join_gold_pointers(records, [("gs.a.One", "Hi", "<UNKNOWN_TOKEN>")])
        problems = audit_join(entries)
        self.assertTrue(any("unknown token" in p for p in problems))

    def test_known_and_dynamic_tokens_do_not_trip_the_audit(self):
        # The English source must carry the same dynamic substituents the
        # translation does -- an English record with none, matched to a
        # translation that introduces {PLAYER}/{ENEMY} out of nowhere, is
        # exactly the class of error the placeholder-consistency check
        # (added alongside this test) exists to catch.
        records = [GoldTextRecord("55:0001", "{PLAYER} says hi to {ENEMY}.")]
        entries, _ = join_gold_pointers(
            records, [("gs.a.One", "<PLAYER> says hi to <ENEMY>.", "{PLAYER} dit <LINE>Salut a<ENEMY>!")],
        )
        self.assertEqual(audit_join(entries), [])

    def test_clean_join_has_no_problems(self):
        records = [GoldTextRecord("55:0001", "Hello there!")]
        entries, _ = join_gold_pointers(records, [("gs.a.Greeting", "Hello there!", "Bonjour!")])
        self.assertEqual(audit_join(entries), [])


class UnresolvedReportTests(unittest.TestCase):
    def test_lists_only_no_match_and_unresolved_entries(self):
        records = [
            GoldTextRecord("55:0001", "Matched."),
            GoldTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gold_pointers(records, rows)
        report = unresolved_report(entries)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["pointer"], "55:0002")
        self.assertEqual(report[0]["provenance"], NO_MATCH)


class ToAlignedRowsTests(unittest.TestCase):
    def test_resolved_and_unresolved_entries_serialize_correctly(self):
        records = [
            GoldTextRecord("55:0001", "Hello there!"),
            GoldTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, _ = join_gold_pointers(records, rows)
        aligned = to_aligned_rows(entries, target_lang="fr")
        self.assertEqual(aligned[0], {
            "qid": "55:0001", "game": "gold", "english": "Hello there!",
            "translation": "Bonjour!", "target_lang": "fr", "method": UNIQUE,
        })
        self.assertEqual(aligned[1]["translation"], None)
        self.assertEqual(aligned[1]["method"], NO_MATCH)

    def test_flows_through_the_real_validate_cli_command(self):
        # End-to-end: pipeline/cli.py's `validate` command is generic over
        # any aligned.json, so Gold's join should flow through it
        # unchanged except for --version gaining "gold" as a choice.
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from pipeline.cli import main as cli_main

        records = [GoldTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, _ = join_gold_pointers(records, rows)
        with tempfile.TemporaryDirectory() as tmp:
            aligned_path = Path(tmp) / "aligned.json"
            aligned_path.write_text(json.dumps(to_aligned_rows(entries)), encoding="utf-8")
            charmap_path = Path(tmp) / "charmap.json"
            charmap_path.write_text(json.dumps({"B": 1, "o": 2, "n": 3, "j": 4, "u": 5, "r": 6, "!": 7}),
                                     encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                rc = cli_main(["validate", str(aligned_path), "--version", "gold", "--charmap", str(charmap_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue()), [])


class GoldCoverageReportTests(unittest.TestCase):
    def test_shape_matches_the_release_gate_expectations(self):
        records = [
            GoldTextRecord("55:0001", "Matched."),
            GoldTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gold_pointers(records, rows)
        coverage = gold_coverage_report(entries)
        self.assertEqual(coverage["rom"], {"translated": 1, "total": 2, "percent": 50.0})
        self.assertEqual(coverage["unmatched"], {"55:0002": ["Unmatched."]})
        self.assertEqual(coverage["ambiguous"], {})

    def test_feeds_release_gate_directly(self):
        from pipeline.validate import release_gate
        from pipeline.model import Alignment, CorpusRecord

        records = [GoldTextRecord("55:0001", "Matched.")]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gold_pointers(records, rows)
        coverage = gold_coverage_report(entries)
        items = [Alignment("55:0001", "gold", CorpusRecord("55:0001", "en", "Matched.", "gold"),
                            CorpusRecord("55:0001", "fr", "Traduit.", "gold"), UNIQUE)]
        ok, summary = release_gate(items, [], charmap={"T": 1, "r": 2, "a": 3, "d": 4, "u": 5, "i": 6, "t": 7, ".": 8},
                                    coverage=coverage)
        self.assertTrue(ok, summary)


class GoldCharmapTests(unittest.TestCase):
    def test_extracts_single_character_glyphs_only(self):
        manifest = {"charmap": {"10": "A", "11": "<BOLD_A>", "12": "b"}}
        self.assertEqual(gold_charmap(manifest), {"A": 10, "b": 12})

    def test_requires_a_charmap_table(self):
        with self.assertRaisesRegex(ValueError, "charmap"):
            gold_charmap({})


if __name__ == "__main__":
    unittest.main()
