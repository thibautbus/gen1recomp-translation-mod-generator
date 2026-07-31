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
    _extract_anchor,
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

    def test_parts_separators_are_boundary_specific_and_schema_safe(self):
        valid = {"X": {"parts": [
            {"qid": "a", "extraction": {"kind": "full", "preserve_edges": True}},
            {"qid": "b", "extraction": {"kind": "full", "preserve_edges": True}},
        ], "separators": ["\f"], "placeholders": {}}}
        self.assertIn("separators", load_semantic_anchors(valid)["X"])
        for separators in (("\f",), ["\f", "\n"], [1]):
            with self.assertRaises(ValueError):
                load_semantic_anchors({"X": {**valid["X"], "separators": separators}})
        for unsafe in ("\x00", "\x7f", "\u0085", "\u202e", "\ud800", "\ue000"):
            with self.assertRaises(ValueError):
                load_semantic_anchors({"X": {**valid["X"], "separators": [unsafe]}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {**valid["X"], "separators": ["\f"], "join": "\n"}})

    def test_full_extraction_can_preserve_edges_explicitly(self):
        self.assertEqual(_extract_anchor(" A<PARA>@", {"kind": "full"}), "A")
        self.assertEqual(_extract_anchor(" A<PARA>@", {"kind": "full", "preserve_edges": True}), " A\f")

    def test_target_extraction_inherits_kind_and_edge_policy(self):
        base = {"qid": "q", "extraction": {
            "kind": "full", "preserve_edges": True,
            "targets": {"fr": {"index": 0}},
        }}
        self.assertEqual(load_semantic_anchors({"X": base})["X"]["extraction"]["kind"], "full")
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"qid": "q", "extraction": {
                "kind": "full", "preserve_edges": True,
                "targets": {"fr": {"kind": "segment", "index": 0}},
            }}})
        self.assertEqual(load_semantic_anchors({"X": {"qid": "q", "extraction": {
            "kind": "full", "preserve_edges": True,
            "targets": {"fr": {"kind": "full", "preserve_edges": False}},
        }}})["X"]["extraction"]["targets"]["fr"]["kind"], "full")

    def test_parts_dynamic_identity_order_and_multiplicity_fail_closed(self):
        def rows(target_a, target_b):
            return [
                Alignment("a", "both", CorpusRecord("a", "en", "A {RAM:x} {RAM:y}"), CorpusRecord("a", "fr", target_a), "qid"),
                Alignment("b", "both", CorpusRecord("b", "en", "B {RAM:x}"), CorpusRecord("b", "fr", target_b), "qid"),
            ]
        anchor = {"parts": [
            {"qid": "a", "extraction": {"kind": "full", "preserve_edges": True}},
            {"qid": "b", "extraction": {"kind": "full", "preserve_edges": True}},
        ], "separators": [" "], "placeholders": {"{RAM:x}": "%s", "{RAM:y}": "%s"}}
        source = "A %s %s B %s"
        good, report = match_engine_catalog({source: ""}, rows("A {RAM:x} {RAM:y}", "B {RAM:x}"), semantic_anchors={source: anchor}, target_lang="fr")
        self.assertEqual(good[source], source)
        self.assertEqual(report["details"][source], "semantic")
        for target_a, target_b in (("A {RAM:y} {RAM:x}", "B {RAM:x}"),
                                   ("A {RAM:x} {RAM:x}", "B {RAM:x}"),
                                   ("A {RAM:x}", "B {RAM:x}"),
                                   ("A {RAM:x} {RAM:y} {RAM:z}", "B {RAM:x}"),
                                   ("A {RAM:x} {RAM:z}", "B {RAM:x}")):
            output, details = match_engine_catalog({source: ""}, rows(target_a, target_b), semantic_anchors={source: anchor}, target_lang="fr")
            self.assertEqual(output[source], "")
            self.assertEqual(details["details"][source], "semantic_unresolved")

    def test_parts_placeholder_contract_fails_closed_on_extra_or_missing_token(self):
        rows = [
            Alignment("a", "both", CorpusRecord("a", "en", "A {RAM:x}"), CorpusRecord("a", "fr", "A {RAM:x}"), "qid"),
            Alignment("b", "both", CorpusRecord("b", "en", "B"), CorpusRecord("b", "fr", "B"), "qid"),
        ]
        base = {"parts": [
            {"qid": "a", "extraction": {"kind": "full", "preserve_edges": True}},
            {"qid": "b", "extraction": {"kind": "full", "preserve_edges": True}},
        ], "separators": [" "], "placeholders": {"{RAM:x}": "%s"}}
        output, report = match_engine_catalog({"A %s B": ""}, rows, semantic_anchors={"A %s B": base}, target_lang="fr")
        self.assertEqual(output["A %s B"], "A %s B")
        self.assertEqual(report["details"]["A %s B"], "semantic")
        output, report = match_engine_catalog({"A %s B": ""}, rows, semantic_anchors={"A %s B": {**base, "placeholders": {}}}, target_lang="fr")
        self.assertEqual(output["A %s B"], "")
        self.assertEqual(report["details"]["A %s B"], "semantic_unresolved")

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

    def test_semantic_printf_anchor_converts_typed_dynamic_tokens(self):
        source = "%s\nlearned\n%d!"
        english = "{text_start}<USER><LINE>learned<CONT>@{text_decimal hNum}{text_start}!<PROMPT>"
        french = "{text_start}<USER><LINE>a appris<CONT>@{text_decimal hNum}{text_start}!<PROMPT>"
        qid = "rb.test.SemanticPrintf"
        row_value = Alignment(
            qid, "both", CorpusRecord(qid, "en", english),
            CorpusRecord(qid, "fr", french), "qid", target_lang="fr",
        )
        anchors = {source: {"qid": qid, "extraction": {"kind": "full", "index": 0}}}
        output, report = match_engine_catalog({source: ""}, [row_value], semantic_anchors=anchors, target_lang="fr")
        self.assertEqual(output[source], "%s\na appris\v%d!")
        self.assertEqual(report["details"][source], "semantic")
        self.assertEqual(report["provenance"][source]["qid"], qid)

    def test_semantic_printf_anchor_restores_literal_percent_and_exact_formatting(self):
        source = "%% %s\nlearned\n%03d!"
        english = "{text_start}%% <USER><LINE>learned<CONT>@{text_decimal hNum}{text_start}!<PROMPT>"
        french = "{text_start}%% <USER><LINE>a appris<CONT>@{text_decimal hNum}{text_start}!<PROMPT>"
        qid = "rb.test.SemanticPrintfFormatting"
        row_value = Alignment(
            qid, "both", CorpusRecord(qid, "en", english),
            CorpusRecord(qid, "fr", french), "qid", target_lang="fr",
        )
        anchors = {source: {"qid": qid, "extraction": {"kind": "full", "index": 0}}}
        output, report = match_engine_catalog({source: ""}, [row_value], semantic_anchors=anchors, target_lang="fr")
        self.assertEqual(output[source], "%% %s\na appris\v%03d!")
        self.assertEqual(printf_directives(output[source]), ["%%", "%s", "%03d"])
        self.assertEqual(check_printf_directives(source, output[source]), [])
        self.assertEqual(report["details"][source], "semantic")

    def test_semantic_printf_anchor_fails_closed_on_type_order_cardinality_mixed_and_unknown(self):
        source = "%s\nlearned\n%d!"
        english = "{text_start}<USER><LINE>learned<CONT>@{text_decimal hNum}{text_start}!<PROMPT>"
        anchors = {source: {"qid": "q", "extraction": {"kind": "full", "index": 0}}}
        targets = {
            "type": "{text_start}{text_decimal hNum}<LINE>appris<CONT>@{text_decimal hNum}{text_start}!<PROMPT>",
            "order": "{text_start}{text_decimal hNum}<LINE>appris<CONT>@{text_ram wName}{text_start}!<PROMPT>",
            "cardinality": "{text_start}<USER><LINE>appris<CONT>@{text_start}!<PROMPT>",
            "mixed": "{text_start}<USER><LINE>appris<CONT>@{text_decimal hNum}{text_start}! %s<PROMPT>",
            "unknown": "{text_start}{UNKNOWN}<LINE>appris<CONT>@{text_decimal hNum}{text_start}!<PROMPT>",
        }
        for label, target in targets.items():
            row_value = Alignment(
                "q", "both", CorpusRecord("q", "en", english),
                CorpusRecord("q", "fr", target), "qid", target_lang="fr",
            )
            output, report = match_engine_catalog({source: ""}, [row_value], semantic_anchors=anchors, target_lang="fr")
            self.assertEqual(output[source], "", label)
            self.assertEqual(report["details"][source], "semantic_unresolved", label)

    def test_semantic_dynamic_anchor_keeps_legacy_placeholder_path(self):
        source = "A {RAM:x}"
        row_value = Alignment(
            "q.dynamic", "both",
            CorpusRecord("q.dynamic", "en", "A {text_ram x}"),
            CorpusRecord("q.dynamic", "fr", "Un {text_ram x}"),
            "qid", target_lang="fr",
        )
        anchors = {source: {"qid": "q.dynamic", "extraction": {"kind": "full", "index": 0}}}
        output, report = match_engine_catalog({source: ""}, [row_value], semantic_anchors=anchors, target_lang="fr")
        self.assertEqual(output[source], "Un {RAM:x}")
        self.assertEqual(report["details"][source], "semantic")

    def test_semantic_anchor_qid_duplicates_fail_closed_even_when_identical(self):
        source = "FIGHT"
        anchor = {source: {"qid": "q.duplicate", "extraction": {"kind": "full", "index": 0}}}
        rows = [
            Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", "COMBAT"), "qid"),
            Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", "COMBAT"), "qid"),
        ]
        output, report = match_engine_catalog({source: ""}, rows, semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(output[source], "")
        self.assertEqual(report["details"][source], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)

    def test_semantic_anchor_qid_conflicting_and_malformed_duplicates_fail_closed(self):
        source = "FIGHT"
        anchor = {source: {"qid": "q.duplicate", "extraction": {"kind": "full", "index": 0}}}
        cases = (
            [
                Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", "COMBAT"), "qid"),
                Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", "KAMPF"), "qid"),
            ],
            [
                Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", None), "qid"),
                Alignment("q.duplicate", "both", CorpusRecord("q.duplicate", "en", "FIGHT"), CorpusRecord("q.duplicate", "fr", "COMBAT"), "qid"),
            ],
        )
        for rows in cases:
            output, report = match_engine_catalog({source: ""}, rows, semantic_anchors=anchor, target_lang="fr")
            self.assertEqual(output[source], "")
            self.assertEqual(report["details"][source], "semantic_unresolved")
            self.assertEqual(report["fallback_english"], 1)

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

    def test_release_requires_rom_coverage_engine_is_informational(self):
        item = row("A", "Un")
        charmap = {"U": 1, "n": 2}
        incomplete = {"unmatched": {}, "ambiguous": {},
                      "rom": {"translated": 1, "total": 2, "percent": 50.0},
                      "engine": {"translated": 1, "total": 2, "percent": 50.0}}
        findings = []
        self.assertFalse(release_gate([item], findings, charmap, incomplete)[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-incomplete" and f["severity"] == "warning"
                            for f in findings))
        complete = {"unmatched": {}, "ambiguous": {},
                    "rom": {"translated": 2, "total": 2, "percent": 100.0},
                    "engine": {"translated": 2, "total": 2, "percent": 100.0}}
        self.assertTrue(release_gate([item], [], charmap, complete)[0])
        informational = {**complete, "engine": {"translated": 1, "total": 2, "percent": 50.0, "unmatched": ["X"]}}
        findings = []
        self.assertTrue(release_gate([item], findings, charmap, informational)[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-unmatched" and f["severity"] == "warning"
                            for f in findings))
        self.assertTrue(any(f["rule"] == "coverage-engine-incomplete" and f["severity"] == "warning"
                            for f in findings))
        for bad in (
            {"translated": True, "total": 1, "percent": 100.0},
            {"translated": 1, "total": 1, "percent": math.nan},
            {"translated": 1, "total": 1, "percent": math.inf},
            {"translated": 1, "total": 1, "percent": 10 ** 1000},
        ):
            coverage = {"unmatched": {}, "ambiguous": {}, "rom": complete["rom"], "engine": bad}
            self.assertTrue(release_gate([item], [], charmap, coverage)[0])

        for status, bad in (("unmatched", {"key": "not-an-array"}), ("ambiguous", "not-a-map")):
            coverage = {"unmatched": {}, "ambiguous": {}, "rom": complete["rom"],
                        "engine": {"translated": 1, "total": 1, "percent": 100.0, status: bad}}
            findings = []
            self.assertTrue(release_gate([item], findings, charmap, coverage)[0])
            self.assertTrue(any(f["rule"] == f"coverage-engine-{status}-invalid" and f["severity"] == "warning"
                                for f in findings))

    def test_malformed_rby_is_non_gating_warning(self):
        item = row("A", "Un")
        coverage = {
            "unmatched": {}, "ambiguous": {},
            "rom": {"translated": 1, "total": 1, "percent": 100.0},
            "engine": {"translated": 1, "total": 2, "percent": 50.0, "unmatched": ["X"]},
            "engine_rby": {"translated": "bad", "total": 383, "percent": 0},
        }
        findings = []
        ok, _ = release_gate([item], findings, {"U": 1, "n": 2}, coverage)
        self.assertTrue(ok)
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-invalid" for f in findings))

    def test_rby_optional_diagnostics_are_non_gating(self):
        item = row("A", "Un")
        base = {
            "unmatched": {}, "ambiguous": {},
            "rom": {"translated": 1, "total": 1, "percent": 100.0},
            "engine": {"translated": 1, "total": 1, "percent": 100.0},
        }
        findings = []
        self.assertTrue(release_gate([item], findings, {"U": 1, "n": 2}, base)[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-missing" for f in findings))

        rby = {"translated": 1, "total": 2, "percent": 50.0,
               "unmatched": ["A"], "ambiguous": {"B": ["candidate"]}}
        findings = []
        self.assertTrue(release_gate([item], findings, {"U": 1, "n": 2}, {**base, "engine_rby": rby})[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-unmatched" for f in findings))
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-ambiguous" for f in findings))
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-incomplete" for f in findings))

        malformed = {"translated": 1, "total": 1, "percent": 100.0,
                     "ambiguous": "not-a-map"}
        findings = []
        self.assertTrue(release_gate([item], findings, {"U": 1, "n": 2}, {**base, "engine_rby": malformed})[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-ambiguous-invalid" for f in findings))

        findings = []
        with_warning = {**base, "engine_rby_warning": "source unavailable"}
        self.assertTrue(release_gate([item], findings, {"U": 1, "n": 2}, with_warning)[0])
        self.assertTrue(any(f["rule"] == "coverage-engine-rby-warning" for f in findings))
        self.assertFalse(any(f["rule"] == "coverage-engine-rby-missing" for f in findings))

    def test_rom_join_maps_are_required_and_nested_shapes_are_strict(self):
        item = row("A", "Un")
        base = {
            "unmatched": {}, "ambiguous": {},
            "rom": {"translated": 1, "total": 1, "percent": 100.0},
            "engine": {"translated": 1, "total": 1, "percent": 100.0},
        }
        self.assertTrue(release_gate([item], [], {"U": 1, "n": 2}, base)[0])
        for status in ("unmatched", "ambiguous"):
            for bad in (None, [], {"dialogue": "not-an-array"}):
                coverage = dict(base)
                coverage[status] = bad
                ok, summary = release_gate([item], [], {"U": 1, "n": 2}, coverage)
                self.assertFalse(ok)
                self.assertTrue(any(f["rule"] == f"coverage-{status}-invalid" for f in summary["findings"]))

            coverage = dict(base)
            coverage.pop(status)
            ok, summary = release_gate([item], [], {"U": 1, "n": 2}, coverage)
            self.assertFalse(ok)
            self.assertTrue(any(f["rule"] == f"coverage-{status}-invalid" for f in summary["findings"]))

    def test_release_summary_returns_diagnostics_for_any_findings_iterable(self):
        item = row("A", "Un")
        coverage = {
            "unmatched": {}, "ambiguous": {},
            "rom": {"translated": 1, "total": 1, "percent": 100.0},
            "engine": {"translated": 1, "total": 2, "percent": 50.0},
        }
        for initial in ([], (), ({"rule": "pre-existing", "severity": "warning"},), (x for x in ())):
            ok, summary = release_gate([item], initial, {"U": 1, "n": 2}, coverage)
            self.assertTrue(ok)
            self.assertIsInstance(summary["findings"], list)
            self.assertEqual(summary["finding_count"], len(summary["findings"]))
            self.assertTrue(any(f["rule"] == "coverage-engine-incomplete" for f in summary["findings"]))

    def test_missing_report_and_charmap_are_independent_release_errors(self):
        item = row("A", "Un")
        coverage = {
            "unmatched": {}, "ambiguous": {},
            "rom": {"translated": 1, "total": 1, "percent": 100.0},
            "engine": {"translated": 1, "total": 1, "percent": 100.0},
        }
        findings = []
        self.assertFalse(release_gate([item], findings, {"U": 1, "n": 2}, None)[0])
        self.assertTrue(any(f["rule"] == "coverage-required" for f in findings))
        findings = []
        self.assertFalse(release_gate([item], findings, None, coverage)[0])
        self.assertTrue(any(f["rule"] == "charmap-required" for f in findings))


if __name__ == "__main__":
    unittest.main()
