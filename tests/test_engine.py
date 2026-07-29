import json
import math
import tempfile
import unittest
from pathlib import Path

from pipeline.engine import (
    check_printf_directives,
    load_semantic_anchors,
    load_engine_overrides,
    match_engine_catalog,
    printf_directives,
    read_engine_catalog,
)
from pipeline.model import Alignment, CorpusRecord
from pipeline.validate import release_gate


def row(source, french):
    return Alignment(source, "both", CorpusRecord(source, "en", source),
                     CorpusRecord(source, "fr", french), "qid")


class EngineTests(unittest.TestCase):
    def test_multi_qid_parts_anchor_composes_bicycle_off_with_one_printf(self):
        rows = [
            Alignment("off1", "both", CorpusRecord("off1", "en", "{text_start}<PLAYER> got off@@"), CorpusRecord("off1", "fr", "{text_start}<PLAYER> descend@@"), "qid"),
            Alignment("off2", "both", CorpusRecord("off2", "en", "the @{text_ram wStringBuffer}{text_start}.<PROMPT>"), CorpusRecord("off2", "fr", "la @{text_ram wStringBuffer}{text_start}.<PROMPT>"), "qid"),
            Alignment("bike", "both", CorpusRecord("bike", "en", "BICYCLE@"), CorpusRecord("bike", "fr", "BICYCLETTE@"), "qid"),
        ]
        anchors = {"off": {"parts": [
            {"qid": "off1", "extraction": {"kind": "full"}},
            {"qid": "off2", "extraction": {"kind": "full"}},
            {"qid": "bike", "extraction": {"kind": "full"}, "include": False},
        ], "join": "\n", "placeholders": {"{PLAYER}": "%s", "{RAM:wStringBuffer}": {"part": 2}}}}
        output, report = match_engine_catalog({"%s got off\nthe BICYCLE.": ""}, rows, semantic_anchors={"%s got off\nthe BICYCLE.": anchors["off"]}, target_lang="fr")
        self.assertEqual(output["%s got off\nthe BICYCLE."], "%s descend\nla BICYCLETTE.")
        self.assertEqual(report["details"]["%s got off\nthe BICYCLE."], "semantic")
        self.assertEqual(report["provenance"]["%s got off\nthe BICYCLE."]["qids"], ["off1", "off2", "bike"])

    def test_multi_qid_parts_anchor_fails_closed_on_missing_or_ambiguous_part(self):
        anchor = {"X": {"parts": [
            {"qid": "a", "extraction": {"kind": "full"}},
            {"qid": "b", "extraction": {"kind": "full"}},
        ], "join": " ", "placeholders": {}}}
        rows = [Alignment("a", "both", CorpusRecord("a", "en", "A"), CorpusRecord("a", "fr", "A"), "qid")]
        output, report = match_engine_catalog({"A B": ""}, rows, semantic_anchors={"A B": anchor["X"]}, target_lang="fr")
        self.assertEqual(output["A B"], "")
        self.assertEqual(report["details"]["A B"], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)
        rows.extend([
            Alignment("b", "both", CorpusRecord("b", "en", "B"), CorpusRecord("b", "fr", "UN"), "qid"),
            Alignment("b", "both", CorpusRecord("b", "en", "B"), CorpusRecord("b", "fr", "DEUX"), "qid"),
        ])
        output, _ = match_engine_catalog({"A B": ""}, rows, semantic_anchors={"A B": anchor["X"]}, target_lang="fr")
        self.assertEqual(output["A B"], "")

    def test_multi_qid_parts_schema_rejects_bool_index_and_duplicate_qid(self):
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"parts": [{"qid": "a", "extraction": {"kind": "full"}}, {"qid": "a", "extraction": {"kind": "full"}}]}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"parts": [{"qid": "a", "extraction": {"kind": "segment", "index": True}}]}})

    def test_real_corpus_bicycle_off_anchor_all_languages(self):
        root = Path(".cache/build")
        if not (root / "aligned.json").is_file():
            self.skipTest("cached aligned corpus is unavailable")
        anchors = load_semantic_anchors()
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            path = root / "aligned.json" if language == "fr" else root / language / "aligned.json"
            if not path.is_file():
                self.skipTest(f"cached {language} corpus is unavailable")
            records = json.loads(path.read_text(encoding="utf-8"))
            rows = [Alignment(item["qid"], "both", CorpusRecord(item["qid"], "en", item["english"]), CorpusRecord(item["qid"], language, item["translation"]), "qid", target_lang=language)
                    for item in records if item.get("qid") in {"rb.text_7.GotOffBicycleText1", "rb.text_7.GotOffBicycleText2", "rb.names.ItemNames.6"}]
            output, report = match_engine_catalog({"%s got off\nthe BICYCLE.": ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertTrue(output["%s got off\nthe BICYCLE."], language)
            self.assertEqual(report["details"]["%s got off\nthe BICYCLE."], "semantic", language)
    def test_exact_normalized_collision_and_fallback(self):
        output, report = match_engine_catalog(
            {"Hello": "", "  WORLD  ": "", "Collision": "", "Missing": ""},
            [row("Hello", "Bonjour"), row("world", "Monde"),
             row("Collision", "Un"), row("Collision", "Deux")],
        )
        self.assertEqual(output["Hello"], "Bonjour")
        self.assertEqual(output["  WORLD  "], "Monde")
        self.assertEqual(output["Collision"], "")
        self.assertEqual(output["Missing"], "")
        self.assertEqual(report["auto_exact"], 1)
        self.assertEqual(report["auto_normalized"], 1)
        self.assertIn("Collision", report["ambiguous"])

    def test_override_priority_and_printf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.json"
            path.write_text(json.dumps({"schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                                        "entries": {"Hello %s": {"override": "Salut %s"}}}), encoding="utf-8")
            overrides = load_engine_overrides(path)
        output, report = match_engine_catalog({"Hello %s": ""}, [row("Hello %s", "Bonjour %s")], overrides)
        self.assertEqual(output["Hello %s"], "Salut %s")
        self.assertEqual(report["override"], 1)
        self.assertEqual(printf_directives("%% %s %.1f"), ["%%", "%s", "%.1f"])
        self.assertEqual(check_printf_directives("%% %s", "%% %s"), [])

    def test_printf_parser_matches_luajit_formats_without_false_percent_hits(self):
        self.assertEqual(
            printf_directives("100% ready: %q %r"), [],
            "prose and unsupported conversions are not directives",
        )
        self.assertEqual(
            printf_directives("%s %03d %.1f %02X %+05.2f"),
            ["%s", "%03d", "%.1f", "%02X", "%+05.2f"],
        )
        # LuaJIT's string.format scanner does not support C's dynamic width,
        # positional arguments, length modifiers, or overlong width fields.
        self.assertEqual(printf_directives("%*d %1$s %lld %hhd %100d"), [])

    def test_catalog_reader_requires_empty_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strings.lua"
            path.write_text('return { ["A"] = "", }\n', encoding="utf-8")
            self.assertEqual(read_engine_catalog(path), {"A": ""})
            path.write_text('return { ["A"] = "B", }\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_engine_catalog(path)
            path.write_text('return { ["A"] = "", } garbage\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_engine_catalog(path)
            for malformed in (
                'return { ["A"] = "" ["B"] = "", }\n',
                'return { ["A"] = "",, ["B"] = "", }\n',
                'return { ["A"] = "", , }\n',
            ):
                path.write_text(malformed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_engine_catalog(path)

    def test_override_must_be_nonempty_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.json"
            path.write_text(json.dumps({"entries": {"A": {"override": 42}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_engine_overrides(path)

    def test_raw_records_qid_is_prioritized_and_conflicts_are_ambiguous(self):
        records = [CorpusRecord("q", "en", "English"),
                   CorpusRecord("q", "fr", "Par qid", english="Different"),
                   CorpusRecord("other", "fr", "Par texte", english="English")]
        output, report = match_engine_catalog({"English": ""}, records)
        self.assertEqual(output["English"], "Par qid")
        self.assertEqual(report["translated"], 1)

    def test_structural_matching_converts_runtime_ram_placeholder(self):
        source = "Wild %s\nappeared!"
        english = "{text_start}Wild @{text_ram wEnemyMonNick}{text_start}<LINE>appeared!<PROMPT>"
        french = "{text_start}Un @{text_ram wEnemyMonNick}{text_start}<LINE>sauvage apparaît!<PROMPT>"
        output, report = match_engine_catalog(
            {source: ""},
            [Alignment("wild", "both", CorpusRecord("wild", "en", english),
                       CorpusRecord("wild", "fr", french), "qid")],
        )
        self.assertEqual(output[source], "Un %s\nsauvage apparaît!")
        self.assertEqual(report["auto_structural"], 1)
        self.assertEqual(report["details"][source], "structural")
        self.assertEqual(
            report["auto_structural"],
            sum(value == "structural" for value in report["details"].values()),
        )

    def test_structural_matching_requires_order_and_type_compatibility(self):
        source = "Value %s costs %03d"
        english = "Value {text_ram wStringBuffer1} costs {text_decimal wMoney}"
        wrong_order = "Valeur {text_decimal wMoney} coûte {text_ram wStringBuffer1}"
        wrong_type = "Valeur {text_decimal wMoney} coûte {text_decimal wOther}"
        records = [
            Alignment("order", "both", CorpusRecord("order", "en", english),
                      CorpusRecord("order", "fr", wrong_order), "qid"),
            Alignment("type", "both", CorpusRecord("type", "en", english),
                      CorpusRecord("type", "fr", wrong_type), "qid"),
        ]
        output, report = match_engine_catalog({source: ""}, records)
        self.assertEqual(output[source], "")
        self.assertEqual(report["auto_structural"], 0)
        self.assertIn(source, report["ambiguous"])

    def test_structural_incompatible_candidate_cannot_be_ignored(self):
        source = "Value %s costs %03d"
        english = "Value {text_ram wStringBuffer1} costs {text_decimal wMoney}"
        records = [
            row(english, "Valeur {text_ram wStringBuffer1} coûte {text_decimal wMoney}"),
            row(english, "Valeur {text_ram wStringBuffer1} coûte {text_ram wMoney}"),
        ]
        output, report = match_engine_catalog({source: ""}, records)
        self.assertEqual(output[source], "")
        self.assertIn(source, report["ambiguous"])

    def test_structural_collision_is_ambiguous(self):
        source = "Wild %s\nappeared!"
        english = "Wild {text_ram wEnemyMonNick}<LINE>appeared!"
        records = [
            row(english, "Un {text_ram wEnemyMonNick}<LINE>sauvage apparaît!"),
            row(english, "Un {text_ram wEnemyMonNick}<LINE>un autre apparaît!"),
        ]
        output, report = match_engine_catalog({source: ""}, records)
        self.assertEqual(output[source], "")
        self.assertIn(source, report["ambiguous"])

    def test_release_requires_rom_and_engine_coverage_separately(self):
        item = row("A", "Un")
        charmap = {"U": 1, "n": 2}
        incomplete = {"unmatched": {}, "ambiguous": {},
                      "rom": {"translated": 1, "total": 2, "percent": 50.0},
                      "engine": {"translated": 1, "total": 2, "percent": 50.0}}
        self.assertFalse(release_gate([item], [], charmap, incomplete)[0])
        complete = {"unmatched": {}, "ambiguous": {},
                    "rom": {"translated": 2, "total": 2, "percent": 100.0},
                    "engine": {"translated": 2, "total": 2, "percent": 100.0}}
        self.assertTrue(release_gate([item], [], charmap, complete)[0])
        for bad in (
            {"translated": True, "total": 1, "percent": 100.0},
            {"translated": 1, "total": 1, "percent": math.nan},
            {"translated": 1, "total": 1, "percent": math.inf},
        ):
            coverage = {"unmatched": {}, "ambiguous": {}, "rom": complete["rom"], "engine": bad}
            self.assertFalse(release_gate([item], [], charmap, coverage)[0])


if __name__ == "__main__":
    unittest.main()
