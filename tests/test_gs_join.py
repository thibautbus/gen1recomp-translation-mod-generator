import tempfile
import unittest
from pathlib import Path

from pipeline.gs_text import GsTextRecord
from pipeline.gs_join import (
    GsPlaceholderDecision, HARMLESS_AMBIGUOUS, MARKUP_ONLY, NO_MATCH, OVERRIDE, REVIEWED_QID,
    UNIQUE, UNRESOLVED, audit_join, gs_coverage_report, join_gs_pointers,
    load_gs_placeholder_decisions, load_gs_pointer_decisions,
    load_gold_silver_pointer_aliases, read_corpus_rows, to_aligned_rows,
    unresolved_report,
)


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
        records = [GsTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, UNIQUE)
        self.assertEqual(entries[0].translation, "Bonjour!")
        self.assertEqual(entries[0].qid, "gs.a.Greeting")
        self.assertEqual(stats["unique"], 1)

    def test_markup_only_needs_no_translation(self):
        records = [GsTextRecord("55:0001", "<PARA><DONE>")]
        entries, stats = join_gs_pointers(records, [])
        self.assertEqual(entries[0].provenance, MARKUP_ONLY)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(stats["markup_only"], 1)

    def test_markup_only_sound_and_timing_controls_are_dropped(self):
        # RomExtractorGen2's TEXT_NO_GLYPH branch drops these controls, so
        # they do not produce a catalog override.
        for control in ("{sound_item}", "{text_pause}"):
            with self.subTest(control=control):
                records = [GsTextRecord("55:0001", control)]
                entries, _ = join_gs_pointers(records, [])
                self.assertIsNone(entries[0].translation)

    def test_no_match_keeps_english_and_is_flagged(self):
        records = [GsTextRecord("55:0001", "Nothing matches this.")]
        entries, stats = join_gs_pointers(records, [("gs.a.Other", "Something else.", "Autre chose.")])
        self.assertEqual(entries[0].provenance, NO_MATCH)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(stats["no_match"], 1)

    def test_harmless_ambiguity_picks_either_identical_translation(self):
        records = [GsTextRecord("55:0001", "Same text.")]
        rows = [("gs.a.One", "Same text.", "Meme texte."), ("gs.b.Two", "Same text.", "Meme texte.")]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, HARMLESS_AMBIGUOUS)
        self.assertEqual(entries[0].translation, "Meme texte.")
        self.assertEqual(stats["harmless_ambiguous"], 1)

    def test_command_difference_is_not_erased_by_ambiguity_normalisation(self):
        records = [GsTextRecord("55:0001", "Same text.")]
        rows = [
            ("gs.a.One", "Same text.", "Même texte<LINE>!"),
            ("gs.b.Two", "Same text.", "Même texte<CONT>!"),
        ]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, UNRESOLVED)
        self.assertEqual(stats["unresolved"], 1)

    def test_reachable_sound_control_is_dropped_not_fatal(self):
        records = [GsTextRecord("55:0001", "Same text.")]
        rows = [("gs.a.One", "Same text.", "Même texte{sound_item}")]
        entries, _ = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].translation, "Même texte")

    def test_ambiguous_candidates_stay_unresolved(self):
        records = [GsTextRecord("48:0001", "...\x0cMy name's ???.")]
        rows = [
            ("gs.CherrygroveCity.YouLost", "...\x0cMy name's ???.", "...Perdu."),
            ("gs.CherrygroveCity.YouWon", "...\x0cMy name's ???.", "...Gagne."),
        ]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, UNRESOLVED)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(entries[0].candidate_qids, ("gs.CherrygroveCity.YouLost", "gs.CherrygroveCity.YouWon"))
        self.assertEqual(stats["unresolved"], 1)

    def test_reviewed_qid_resolves_ambiguity_in_the_target_language(self):
        records = [GsTextRecord("48:0001", "...\fMy name's ???.")]
        rows = [
            ("gs.city.YouLost", "...\fMy name's ???.", "...Perdu."),
            ("gs.city.YouWon", "...\fMy name's ???.", "...Gagné."),
        ]
        entries, stats = join_gs_pointers(
            records, rows, qid_decisions={"48:0001": "gs.city.YouWon"},
        )
        self.assertEqual(entries[0].provenance, REVIEWED_QID)
        self.assertEqual(entries[0].qid, "gs.city.YouWon")
        self.assertEqual(entries[0].translation, "...Gagné.")
        self.assertEqual(stats["reviewed_qid"], 1)

    def test_reviewed_qid_must_match_the_pointer_source(self):
        records = [GsTextRecord("48:0001", "Original")]
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            join_gs_pointers(
                records, [("gs.city.Other", "Other", "Autre")],
                qid_decisions={"48:0001": "gs.city.Other"},
            )

    def test_override_wins_over_everything_else(self):
        records = [GsTextRecord("45:0001", "What?!")]
        rows = [
            ("gs.IlexForest.Grunt", "What?!", "Quoi?!"),
            ("gs.CharcoalKiln.Grunt", "What?!", "De quoi?!"),
        ]
        entries, stats = join_gs_pointers(records, rows, overrides={"45:0001": "Hand-picked!"})
        self.assertEqual(entries[0].provenance, OVERRIDE)
        self.assertEqual(entries[0].translation, "Hand-picked!")
        self.assertEqual(stats["override"], 1)

    def test_empty_override_is_rejected(self):
        records = [GsTextRecord("45:0001", "What?!")]
        with self.assertRaisesRegex(ValueError, "empty Gold pointer override"):
            join_gs_pointers(records, [], overrides={"45:0001": ""})

    def test_blank_corpus_translation_is_not_counted_as_a_match(self):
        records = [GsTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "")]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, NO_MATCH)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(stats["no_match"], 1)

    def test_control_only_corpus_translation_falls_back_to_english(self):
        records = [GsTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "{sound_item}")]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, NO_MATCH)
        self.assertIsNone(entries[0].translation)
        self.assertEqual(stats["no_match"], 1)

    def test_translation_is_converted_to_engine_form(self):
        records = [GsTextRecord("55:0001", "Hello<LINE>there!")]
        rows = [("gs.a.Greeting", "Hello<LINE>there!", "Bonjour<LINE>!")]
        entries, _ = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].translation, "Bonjour\n!")

    def test_candidates_differing_only_by_ram_buffer_number_are_harmless(self):
        # Regression: _token_aware_key used to compare candidates by their
        # RAW corpus token spelling, so two rows differing only in which
        # numbered {text_ram wStringBufferN} they name looked like a genuine
        # content difference and fell to UNRESOLVED -- even though Gold's
        # engine never names the buffer either way, so both ship the
        # byte-identical bare {STRBUF}.
        records = [GsTextRecord("55:0001", "Hello there!")]
        rows = [
            ("gs.a.One", "Hello there!", "Bonjour {text_ram wStringBuffer1}!"),
            ("gs.b.Two", "Hello there!", "Bonjour {text_ram wStringBuffer2}!"),
        ]
        entries, stats = join_gs_pointers(records, rows)
        self.assertEqual(entries[0].provenance, HARMLESS_AMBIGUOUS)
        self.assertEqual(entries[0].translation, "Bonjour {STRBUF}!")
        self.assertEqual(stats["harmless_ambiguous"], 1)

    def test_a_corpus_translation_naming_its_ram_buffer_bares_the_token(self):
        # Real bug, confirmed against a real Gold build: pointer 40:4d90
        # (gs.std_text.ReceivedItemText) shipped as
        # "{PLAYER} reçoit\n{RAM:wStringBuffer4}." -- a token
        # src/render/TextBox.lua's RAM handler does not recognise (it only
        # matches the bare "wStringBuffer"/"wNameBuffer" spellings), so the
        # item name silently rendered as nothing. The engine's own extracted
        # English for the same pointer is bare
        # ("{PLAYER} received\n{STRBUF}." -- .cache/gold/extracted/gs_text.tsv
        # line 59), which is what the French translation must collapse to.
        records = [GsTextRecord("40:4d90", "{PLAYER} received\n{STRBUF}.")]
        rows = [(
            "gs.std_text.ReceivedItemText",
            "{PLAYER} received\n{text_ram wStringBuffer4}.",
            "{PLAYER} reçoit\n{text_ram wStringBuffer4}.",
        )]
        entries, stats = join_gs_pointers(records, rows, qid_decisions={
            "40:4d90": "gs.std_text.ReceivedItemText",
        })
        self.assertEqual(entries[0].translation, "{PLAYER} reçoit\n{STRBUF}.")
        self.assertEqual(stats["reviewed_qid"], 1)


class AuditJoinTests(unittest.TestCase):
    def test_flags_duplicate_pointers(self):
        records = [GsTextRecord("55:0001", "A"), GsTextRecord("55:0001", "A")]
        # join_gs_pointers itself never produces duplicates from
        # parse_gs_text_catalog's deduped records; construct the
        # collision directly to exercise the audit's own check.
        entries, _ = join_gs_pointers(records[:1], [("gs.a.One", "A", "B")])
        entries = entries + entries
        problems = audit_join(entries)
        self.assertTrue(any("duplicate pointer" in p for p in problems))

    def test_flags_an_unknown_token_in_shipped_output(self):
        records = [GsTextRecord("55:0001", "Hi")]
        entries, _ = join_gs_pointers(records, [("gs.a.One", "Hi", "<UNKNOWN_TOKEN>")])
        problems = audit_join(entries)
        self.assertTrue(any("unknown token" in p for p in problems))

    def test_known_and_dynamic_tokens_do_not_trip_the_audit(self):
        # The English source must carry the same dynamic substituents the
        # translation does -- an English record with none, matched to a
        # translation that introduces {PLAYER}/{ENEMY} out of nowhere, is
        # exactly the class of error the placeholder-consistency check
        # (added alongside this test) exists to catch.
        records = [GsTextRecord("55:0001", "{PLAYER} says hi to {ENEMY}.")]
        entries, _ = join_gs_pointers(
            records, [("gs.a.One", "<PLAYER> says hi to <ENEMY>.", "{PLAYER} dit <LINE>Salut a<ENEMY>!")],
        )
        self.assertEqual(audit_join(entries), [])


class GsPointerDecisionConfigTests(unittest.TestCase):
    def test_repository_decisions_are_valid_and_qid_based(self):
        decisions = load_gs_pointer_decisions()
        self.assertEqual(decisions["48:4961"], "gs.CherrygroveCity.CherrygroveRivalText_YouLost")

    def test_clean_join_has_no_problems(self):
        records = [GsTextRecord("55:0001", "Hello there!")]
        entries, _ = join_gs_pointers(records, [("gs.a.Greeting", "Hello there!", "Bonjour!")])
        self.assertEqual(audit_join(entries), [])


class GoldSilverPointerAliasConfigTests(unittest.TestCase):
    def test_repository_aliases_are_valid_and_pointer_based(self):
        aliases = load_gold_silver_pointer_aliases()
        self.assertEqual(aliases["03:4d76"], "03:4d74")
        self.assertEqual(len(aliases), 8)


class GsPlaceholderDecisionConfigTests(unittest.TestCase):
    def test_repository_decisions_are_valid_and_qid_based(self):
        for language in ("de", "es", "it", "ja-Hrkt", "ko"):
            with self.subTest(language=language):
                self.assertTrue(load_gs_placeholder_decisions(language))
        self.assertEqual(
            load_gs_placeholder_decisions("de")["65:6092"].qid,
            "gs.common_2.MartBoughtText",
        )

    def test_exact_reviewed_difference_is_accepted(self):
        records = [GsTextRecord("55:0001", "Hello {PLAYER}!")]
        entries, _ = join_gs_pointers(records, [("gs.a.Greeting", "Hello <PLAYER>!", "Bonjour!")])
        decisions = {
            "55:0001": GsPlaceholderDecision(
                "gs.a.Greeting", frozenset({"missing placeholder {PLAYER} x1"}),
            ),
        }
        self.assertEqual(audit_join(entries, decisions), [])

    def test_stale_decision_fails_closed(self):
        records = [GsTextRecord("55:0001", "Hello {PLAYER}!")]
        entries, _ = join_gs_pointers(records, [("gs.a.Greeting", "Hello <PLAYER>!", "Bonjour!")])
        decisions = {
            "55:0001": GsPlaceholderDecision(
                "gs.a.Greeting", frozenset({"unexpected placeholder {NUM} x1"}),
            ),
        }
        problems = audit_join(entries, decisions)
        self.assertTrue(any("missing placeholder {PLAYER}" in item for item in problems))
        self.assertTrue(any("unused placeholder decision" in item for item in problems))


class UnresolvedReportTests(unittest.TestCase):
    def test_lists_only_no_match_and_unresolved_entries(self):
        records = [
            GsTextRecord("55:0001", "Matched."),
            GsTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gs_pointers(records, rows)
        report = unresolved_report(entries)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["pointer"], "55:0002")
        self.assertEqual(report[0]["provenance"], NO_MATCH)


class ToAlignedRowsTests(unittest.TestCase):
    def test_resolved_and_unresolved_entries_serialize_correctly(self):
        records = [
            GsTextRecord("55:0001", "Hello there!"),
            GsTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, _ = join_gs_pointers(records, rows)
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

        records = [GsTextRecord("55:0001", "Hello there!")]
        rows = [("gs.a.Greeting", "Hello there!", "Bonjour!")]
        entries, _ = join_gs_pointers(records, rows)
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
            GsTextRecord("55:0001", "Matched."),
            GsTextRecord("55:0002", "Unmatched."),
        ]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gs_pointers(records, rows)
        coverage = gs_coverage_report(entries)
        self.assertEqual(coverage["rom"], {"translated": 1, "total": 2, "percent": 50.0})
        self.assertEqual(coverage["unmatched"], {"55:0002": ["Unmatched."]})
        self.assertEqual(coverage["ambiguous"], {})
        self.assertEqual(coverage["ignored_markup_only"], 0)

    def test_markup_only_records_are_excluded_from_coverage(self):
        records = [
            GsTextRecord("55:0001", "Matched."),
            GsTextRecord("55:0002", "<PARA><DONE>"),
        ]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gs_pointers(records, rows)
        coverage = gs_coverage_report(entries)
        self.assertEqual(coverage["rom"], {"translated": 1, "total": 1, "percent": 100.0})
        self.assertEqual(coverage["ignored_markup_only"], 1)

    def test_feeds_release_gate_directly(self):
        from pipeline.validate import release_gate
        from pipeline.model import Alignment, CorpusRecord

        records = [GsTextRecord("55:0001", "Matched.")]
        rows = [("gs.a.One", "Matched.", "Traduit.")]
        entries, _ = join_gs_pointers(records, rows)
        coverage = gs_coverage_report(entries)
        items = [Alignment("55:0001", "gold", CorpusRecord("55:0001", "en", "Matched.", "gold"),
                            CorpusRecord("55:0001", "fr", "Traduit.", "gold"), UNIQUE)]
        ok, summary = release_gate(items, [], charmap={"T": 1, "r": 2, "a": 3, "d": 4, "u": 5, "i": 6, "t": 7, ".": 8},
                                    coverage=coverage)
        self.assertTrue(ok, summary)

if __name__ == "__main__":
    unittest.main()
