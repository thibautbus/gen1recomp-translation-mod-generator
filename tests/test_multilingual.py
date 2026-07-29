import json
import tempfile
import unittest
from pathlib import Path

from pipeline.align import align, apply_overrides
from pipeline.corpus import parse_redblue, canonical_language
from pipeline.engine import load_semantic_anchors, match_engine_catalog, _extract_anchor
from pipeline.model import Alignment, CorpusRecord
from pipeline.mod import generate_mod
from pipeline.cli import main as cli_main


class MultilingualTests(unittest.TestCase):
    def test_cli_ja_alias_serializes_canonical_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "qid_msg.txt").write_text("q\n", encoding="utf-8"); (root / "en_msg.txt").write_text("HELLO\n", encoding="utf-8"); (root / "ja-Hrkt_msg.txt").write_text("こんにちは\n", encoding="utf-8")
            records = root / "records.json"; aligned = root / "aligned.json"
            self.assertEqual(cli_main(["parse", str(root), "--target-lang", "ja", "-o", str(records)]), 0)
            self.assertEqual(cli_main(["align", str(records), "--target-lang", "jpn", "-o", str(aligned)]), 0)
            body = json.loads(aligned.read_text(encoding="utf-8"))
            self.assertEqual(body[0]["target_lang"], "ja-Hrkt")
            self.assertEqual(body[0]["translation"], "こんにちは")

    def test_language_aliases_are_canonical(self):
        self.assertEqual(canonical_language("ja"), "ja-Hrkt")
        self.assertEqual(canonical_language("jpn"), "ja-Hrkt")
        self.assertEqual(canonical_language("deu"), "de")

    def test_anchor_strips_fullwidth_delimiter_only_at_edges(self):
        spec = {"kind": "segment", "index": 0}
        self.assertEqual(_extract_anchor("プレイじかん／", spec), "プレイじかん")
        self.assertEqual(_extract_anchor("ARG.@", spec), "ARG.")

    def test_segment_uses_control_boundaries_and_token_uses_whitespace(self):
        self.assertEqual(_extract_anchor("A B<NEXT>C D@", {"kind": "segment", "index": 0}), "A B")
        self.assertEqual(_extract_anchor("A B<NEXT>C D@", {"kind": "segment", "index": 1}), "C D")
        self.assertEqual(_extract_anchor("A B<NEXT>C D@", {"kind": "token", "index": 1}), "B")

    def test_de_parallel_parse_align_and_composite_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "qid_msg.txt").write_text("rb.text_boxes.BattleMenuText\nrb.text_boxes.MoneyText\n", encoding="utf-8")
            (root / "en_msg.txt").write_text("FIGHT <PK><MN><NEXT>ITEM  RUN@\nMONEY@\n", encoding="utf-8")
            (root / "de_msg.txt").write_text("KAMPF <PK><MN><NEXT>ITEM FLUCHT@\nGELD@\n", encoding="utf-8")
            items = align(parse_redblue(root, "de"), target_lang="de")
        anchors = {
            "FIGHT": {"qid": "rb.text_boxes.BattleMenuText", "extraction": {"kind": "token", "index": 0}},
            "RUN": {"qid": "rb.text_boxes.BattleMenuText", "extraction": {"kind": "token", "index": 3}},
            "MONEY": {"qid": "rb.text_boxes.MoneyText", "extraction": {"kind": "segment", "index": 0}},
        }
        output, report = match_engine_catalog({"FIGHT": "", "RUN": "", "MONEY": ""}, items, semantic_anchors=anchors, target_lang="de")
        self.assertEqual(output, {"FIGHT": "KAMPF", "RUN": "FLUCHT", "MONEY": "GELD"})
        self.assertEqual(report["translated"], 3)
        self.assertEqual(report["auto_semantic"], 3)

    def test_yes_no_are_extracted_from_corpus_menu_order(self):
        row = Alignment(
            "rb.yes_no_menu_strings.TwoOptionMenuStrings.YesNoMenu",
            "both",
            CorpusRecord("yes-no", "en", "YES<NEXT>NO@"),
            CorpusRecord("yes-no", "fr", "OUI<NEXT>NON@"),
            "qid",
            target_lang="fr",
        )
        anchors = {
            "YES": {
                "qid": row.qid,
                "extraction": {"kind": "segment", "index": 0},
            },
            "NO": {
                "qid": row.qid,
                "extraction": {"kind": "segment", "index": 1},
            },
        }
        output, report = match_engine_catalog(
            {"YES": "", "NO": ""},
            [row],
            semantic_anchors=anchors,
            target_lang="fr",
        )
        self.assertEqual(output, {"YES": "OUI", "NO": "NON"})
        self.assertEqual(report["auto_semantic"], 2)

    def test_anchor_malformed_and_missing_are_safe(self):
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"qid": "q"}})
        for extraction in (
            {"kind": "segment", "index": True},
            {"kind": "span", "index": 0, "count": True},
        ):
            with self.assertRaises(ValueError):
                load_semantic_anchors({"X": {"qid": "q", "extraction": extraction}})
        row = Alignment("q", "both", CorpusRecord("q", "en", "ONE TWO"), CorpusRecord("q", "de", "EINS ZWEI"), "qid", target_lang="de")
        output, report = match_engine_catalog({"X": ""}, [row], semantic_anchors={"X": {"qid": "missing", "extraction": {"kind": "segment", "index": 0}}}, target_lang="de")
        self.assertEqual(output["X"], "")
        self.assertTrue("X" in report["unmatched"] or "X" in report["ambiguous"])
        self.assertEqual(report["translated"], 0)
        self.assertLessEqual(report["unmatched"].count("X"), 1)

        failed_anchor = Alignment("q.anchor", "both", CorpusRecord("q.anchor", "en", "FIGHT"), CorpusRecord("q.anchor", "de", "KAMPF"), "qid", target_lang="de")
        exact_candidate = Alignment("q.exact", "both", CorpusRecord("q.exact", "en", "FIGHT"), CorpusRecord("q.exact", "de", "STREIT"), "qid", target_lang="de")
        anchor = {"FIGHT": {"qid": "q.anchor", "extraction": {"kind": "segment", "index": 1}}}
        output, report = match_engine_catalog({"FIGHT": ""}, [failed_anchor, exact_candidate], semantic_anchors=anchor, target_lang="de")
        self.assertEqual(output["FIGHT"], "")
        self.assertEqual(report["details"]["FIGHT"], "semantic_unresolved")
        self.assertEqual(report["translated"], 0)
        self.assertEqual(report["fallback_english"], 1)
        self.assertEqual(report["unmatched"].count("FIGHT"), 1)
        self.assertEqual(list(report["ambiguous"]), [])

    def test_versioned_anchor_rejects_malformed_context_and_nested_parts(self):
        with self.assertRaises(ValueError):
            load_semantic_anchors({"schema": "gen1recomp-translation-mods/semantic-anchors", "version": 1,
                                   "anchors": {"X": {"qid": "q", "source_aliases": "bad", "extraction": {"kind": "full"}}}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"schema": "gen1recomp-translation-mods/semantic-anchors", "version": 1,
                                   "anchors": {"X": {"parts": [{"qid": "q", "extraction": {
                                       "kind": "span", "count": True,
                                   }}]}}})

    def test_parts_anchor_extracts_disjoint_segments_and_validates_shape(self):
        text = "A<LINE>B<CONT>C<PARA>D<LINE>E@"
        self.assertEqual(
            _extract_anchor(text, {
                "kind": "parts", "parts": [0, 1, 3, 4],
                "separators": ["\n", "\f", "\n"],
            }),
            "A\nB\fD\nE",
        )
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"qid": "q", "extraction": {
                "kind": "parts", "parts": [0, 1], "separators": ["\n", "\n"],
            }}})

    def test_completion_anchor_fails_closed_on_missing_runtime_number(self):
        source = ("POKéDEX comp-\nletion is:\f{NUM:hDexRatingNumMonsSeen} "
                  "POKéMON seen\n{NUM:hDexRatingNumMonsOwned} POKéMON owned")
        row = Alignment(
            "q.dex", "both",
            CorpusRecord("q.dex", "en", "{text_start}#DEX comp-<PARA>@{text_decimal hDexRatingNumMonsSeen}{text_start} #MON seen<LINE>@{text_decimal hDexRatingNumMonsOwned}{text_start} #MON owned<PROMPT>"),
            CorpusRecord("q.dex", "de", "{text_start}Im #DEX gesehen @{text_decimal hDexRatingNumMonsSeen}{text_start} #MON<PROMPT>"),
            "qid", target_lang="de",
        )
        output, report = match_engine_catalog(
            {source: ""}, [row], semantic_anchors={source: {
                "qid": "q.dex", "extraction": {"kind": "full"},
            }}, target_lang="de")
        self.assertEqual(output[source], "")
        self.assertEqual(report["details"][source], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)

    def test_raw_parallel_records_can_resolve_semantic_anchor(self):
        records = [CorpusRecord("q", "en", "FIGHT ITEM RUN"), CorpusRecord("q", "de", "KAMPF ITEM FLUCHT", english="FIGHT ITEM RUN")]
        output, report = match_engine_catalog({"RUN": ""}, records, semantic_anchors={"RUN": {"qid": "q", "extraction": {"kind": "token", "index": 2}}}, target_lang="de")
        self.assertEqual(output["RUN"], "FLUCHT")
        self.assertEqual(report["details"]["RUN"], "semantic")

    def test_real_japanese_menu_and_center_anchor_regressions(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        items = align(parse_redblue(root, "ja-Hrkt"), target_lang="ja-Hrkt")
        output, report = match_engine_catalog(
            {"BUY": "", "SELL": "", "Welcome to our\nPOKéMON CENTER!": ""},
            items,
            target_lang="ja-Hrkt",
        )
        self.assertEqual(output["BUY"], "かいに　きた")
        self.assertEqual(output["SELL"], "うりに　きた")
        self.assertNotIn("ここでは", output["Welcome to our\nPOKéMON CENTER!"])
        self.assertEqual(report["details"]["Welcome to our\nPOKéMON CENTER!"], "semantic")

    def test_real_corpus_story_engine_anchor_batch_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = {
            "Hey! There's a\nswitch under the\ntrash!\fThe 1st electric\nlock opened!":
                "rb.text_2.VermilionGymTrashSuccessText1",
            "POKéDEX comp-\nletion is:\f{NUM:hDexRatingNumMonsSeen} POKéMON seen\n{NUM:hDexRatingNumMonsOwned} POKéMON owned\fPROF.OAK's\nRating:":
                "rb.pokedex_ratings.DexCompletionText",
            "You don't have the\n{RAM} yet!":
                "rb.Route23.Route23YouDontHaveTheBadgeYetText",
            "You need a\nBICYCLE for the\nCycling Road!":
                "rb.Route18Gate1F.Route18Gate1FGuardYouNeedABicycleText",
            "PA: You're out of\nSAFARI BALLs!": "rb.text_2.OutOfSafariBallsText",
        }
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({key: "" for key in keys}, items,
                                                   target_lang=language)
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key, qid in keys.items():
                self.assertTrue(output[key], (language, qid))
                self.assertEqual(report["provenance"][key]["qid"], qid)

    def test_semantic_span_reflows_by_target_language_without_literals(self):
        row = Alignment(
            "q", "both", CorpusRecord("q", "en", "A B C D"),
            CorpusRecord("q", "de", "EINS ZWEI DREI VIER"), "qid", target_lang="de",
        )
        anchor = {"B C": {"qid": "q", "extraction": {
            "kind": "span", "index": 1, "count": 2,
            "targets": {"de": {"kind": "span", "index": 1, "count": 2}},
        }}}
        output, report = match_engine_catalog({"B C": ""}, [row], semantic_anchors=anchor, target_lang="de")
        self.assertEqual(output["B C"], "ZWEI DREI")
        self.assertEqual(report["details"]["B C"], "semantic")

    def test_semantic_anchor_fails_closed_on_placeholder_loss(self):
        row = Alignment("q", "both", CorpusRecord("q", "en", "A {RAM:x}"), CorpusRecord("q", "de", "EINS"), "qid", target_lang="de")
        anchor = {"A {RAM:x}": {"qid": "q", "extraction": {"kind": "full", "index": 0}}}
        output, report = match_engine_catalog({"A {RAM:x}": ""}, [row], semantic_anchors=anchor, target_lang="de")
        self.assertEqual(output["A {RAM:x}"], "")
        self.assertEqual(report["details"]["A {RAM:x}"], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)

    def test_override_has_priority_and_fr_schema_is_legacy_compatible(self):
        row = Alignment("q", "both", CorpusRecord("q", "en", "Hello"), CorpusRecord("q", "fr", "Bonjour"), "qid")
        output, report = match_engine_catalog({"Hello": ""}, [row], {"Hello": {"override": "Salut"}}, target_lang="fr")
        self.assertEqual(output["Hello"], "Salut")
        self.assertEqual(report["override"], 1)
        self.assertEqual(row.as_dict()["french"], "Bonjour")

    def test_anchor_beats_exact_but_override_beats_anchor(self):
        anchored = Alignment("q.anchor", "both", CorpusRecord("q.anchor", "en", "FIGHT"), CorpusRecord("q.anchor", "de", "KAMPF"), "qid", target_lang="de")
        conflicting_exact = Alignment("q.exact", "both", CorpusRecord("q.exact", "en", "FIGHT"), CorpusRecord("q.exact", "de", "STREIT"), "qid", target_lang="de")
        anchor = {"FIGHT": {"qid": "q.anchor", "extraction": {"kind": "segment", "index": 0}}}
        output, report = match_engine_catalog({"FIGHT": ""}, [anchored, conflicting_exact], semantic_anchors=anchor, target_lang="de")
        self.assertEqual(output["FIGHT"], "KAMPF")
        self.assertEqual(report["details"]["FIGHT"], "semantic")
        self.assertEqual(report["auto_semantic"], 1)
        self.assertEqual(report["auto_exact"], 0)
        row = anchored
        output, report = match_engine_catalog({"FIGHT": ""}, [row], {"FIGHT": {"override": "KAMPF!"}}, semantic_anchors=anchor, target_lang="de")
        self.assertEqual(output["FIGHT"], "KAMPF!")
        self.assertEqual(report["details"]["FIGHT"], "override")

    def test_manifest_is_json_and_respects_display_name_override(self):
        row = Alignment("q", "both", CorpusRecord("q", "en", "A"), CorpusRecord("q", "fr", 'Une "citation"'), "qid")
        with tempfile.TemporaryDirectory() as tmp:
            mod_id = 'id"\\\n\t\x00\x1f'
            target_name = 'Nom "fr"\\\n\t\x01'
            mod = generate_mod([row], Path(tmp) / "custom", mod_id=mod_id, target_name=target_name)
            manifest = json.loads((mod / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], mod_id)
            self.assertEqual(manifest["name"], target_name)
            self.assertEqual(manifest["description"], f"{target_name} for Pokémon Red and Blue, generated from PokeCorpus. Some special characters may not display correctly in game, and some text remains untranslated.")
            default_mod = generate_mod([row], Path(tmp) / "default")
            default_manifest = json.loads((default_mod / "manifest.json").read_text(encoding="utf-8"))
            described_mod = generate_mod(
                [row], Path(tmp) / "described", target_description="Use the supplied description."
            )
            described_manifest = json.loads((described_mod / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(default_manifest["name"], "fr translation")
        self.assertEqual(default_manifest["description"], "fr translation for Pokémon Red and Blue, generated from PokeCorpus. Some special characters may not display correctly in game, and some text remains untranslated.")
        self.assertEqual(described_manifest["description"], "Use the supplied description.")

    def test_manifest_fallbacks_are_language_neutral_with_uniform_priority(self):
        row = Alignment("q", "both", CorpusRecord("q", "en", "A"), CorpusRecord("q", "fr", "Une"), "qid")
        expected_codes = {"fr": "fr", "de": "de", "ja": "ja-Hrkt"}
        with tempfile.TemporaryDirectory() as tmp:
            manifests = {}
            for language, code in expected_codes.items():
                mod = generate_mod([row], Path(tmp) / language, language=language)
                manifests[language] = json.loads((mod / "manifest.json").read_text(encoding="utf-8"))

        for language, code in expected_codes.items():
            self.assertEqual(manifests[language]["name"], f"{code} translation")
            self.assertEqual(manifests[language]["description"], f"{code} translation for Pokémon Red and Blue, generated from PokeCorpus. Some special characters may not display correctly in game, and some text remains untranslated.")
            self.assertEqual(manifests[language]["priority"], 100)


if __name__ == "__main__":
    unittest.main()
