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

    def test_gold_semantic_anchor_bares_a_named_ram_buffer(self):
        # Real bug, confirmed against a real Gold build: pointer 40:4d90's
        # "{PLAYER} received\n{STRBUF}." sibling key "{PLAYER} found\n{STRBUF}!"
        # (qid gs.common_2.FoundItemText) shipped as
        # "{PLAYER} trouve\n{RAM:wStringBuffer3}!" in the generated French
        # mod's strings.lua -- a token TextBox.lua's RAM handler does not
        # recognise, so the found item's name silently rendered as nothing.
        # match_gold_engine_strings tags every corpus row `game="gold"`
        # (pipeline/gold_engine.py:_corpus_records); this drives
        # match_engine_catalog the same way, with the real corpus source
        # (poke-corpus/corpus/GoldSilver/{en,fr}_msg.txt line 4727) and the
        # real anchor (config/gold/semantic_anchors.json).
        source = "{PLAYER} found\n{STRBUF}!"
        qid = "gs.common_2.FoundItemText"
        rows = [
            CorpusRecord(qid, "en", "{text_start}<PLAYER> found<LINE>@{text_ram wStringBuffer3}{text_start}!<DONE>", "gold"),
            CorpusRecord(qid, "fr", "{text_start}<PLAYER> trouve<LINE>@{text_ram wStringBuffer3}{text_start}!<DONE>", "gold"),
        ]
        anchor = {"qid": qid, "extraction": {"kind": "full"}}
        output, report = match_engine_catalog(
            {source: ""}, rows, semantic_anchors={source: anchor}, target_lang="fr",
        )
        self.assertEqual(output[source], "{PLAYER} trouve\n{STRBUF}!")
        self.assertEqual(report["details"][source], "semantic")

    def test_real_gold_semantic_anchor_config_matches_the_bare_form(self):
        # Real regression caught by an independent review of the fix above:
        # config/gold/semantic_anchors.json's ONE composite (multi-placeholder)
        # Gold anchor, gs.battle.BattleText_EnemyIsAboutToUseWillPlayerChangeMon,
        # declared its RAM placeholder as the OLD named engine form
        # ("{RAM:wEnemyMonNickname}") -- what corpus_to_engine used to produce
        # from the corpus's "{text_ram wEnemyMonNickname}" before this fix.
        # With bare_dynamic_tokens now baring the corpus side to "{STRBUF}",
        # the declared placeholder no longer matched what the source actually
        # produced, and resolve_parts failed the whole anchor closed
        # (declared_dynamic != source_dynamic) -- reproduced directly against
        # the checked-in config before it was corrected alongside this test.
        # Loads the REAL file, not a hand-built fixture, so a future edit that
        # reintroduces a named placeholder for Gold fails this test too.
        source = "%s\nis about to use\x0b%s.\x0cWill %s\nchange POKéMON?"
        qid = "gs.battle.BattleText_EnemyIsAboutToUseWillPlayerChangeMon"
        rows = [
            CorpusRecord(qid, "en",
                "{text_start}<ENEMY><LINE>is about to use<CONT>@{text_ram wEnemyMonNickname}"
                "{text_start}.<PARA>Will <PLAYER><LINE>change #MON?<DONE>", "gold"),
            CorpusRecord(qid, "fr",
                "{text_start}<ENEMY><LINE>va utiliser<CONT>@{text_ram wEnemyMonNickname}"
                "{text_start}.<PARA><PLAYER> va-t-il<LINE>changer de PKMN?<DONE>", "gold"),
        ]
        anchors_path = Path(__file__).resolve().parents[1] / "config" / "gold" / "semantic_anchors.json"
        output, report = match_engine_catalog(
            {source: ""}, rows, semantic_anchors=anchors_path, target_lang="fr",
        )
        self.assertEqual(output[source], "%s\nva utiliser\x0b%s.\x0c%s va-t-il\nchanger de PKMN?")
        self.assertEqual(report["details"][source], "semantic")

    def test_real_gold_source_alias_matches_the_bare_form(self):
        # Real regression caught by a THIRD independent review: this repo's
        # only other Gold semantic-anchor entry naming a RAM buffer,
        # gs.common_2.ContestJudging_FirstPlaceText's source_aliases, had the
        # exact same stale-named-token bug as the anchor above, just missed
        # in the first pass because it lives in a "source_aliases" list
        # rather than a "placeholders" dict. Reproduced directly: with the
        # alias still reading "{RAM:wBugContestWinnerName}"/"{RAM:wStringBuffer1}",
        # this exact setup returned "" (method "semantic_unresolved");
        # corrected to the bare "{STRBUF}" form.
        qid = "gs.common_2.ContestJudging_FirstPlaceText"
        rows = [
            CorpusRecord(qid, "en",
                "{text_start}This Bug-Catching<LINE>Contest winner is@{text_pause}{text_start}"
                "…<PARA>@{text_ram wBugContestWinnerName}{text_start},<LINE>who caught a<CONT>"
                "@{text_ram wStringBuffer1}{text_start}!@@", "gold"),
            CorpusRecord(qid, "fr",
                "{text_start}Le gagnant du<LINE>concours des<CONT>insectes est@{text_pause}"
                "{text_start}...<PARA>@{text_ram wBugContestWinnerName}{text_start},<LINE>"
                "qui a capturé un<CONT>@{text_ram wStringBuffer1}{text_start}!@@", "gold"),
        ]
        source = "This Bug-Catching\nContest winner is\x0c%s,\nwho caught a\n%s!"
        anchors_path = Path(__file__).resolve().parents[1] / "config" / "gold" / "semantic_anchors.json"
        output, report = match_engine_catalog(
            {source: ""}, rows, semantic_anchors=anchors_path, target_lang="fr",
        )
        self.assertEqual(
            output[source],
            "Le gagnant du\nconcours des\x0binsectes est...\x0c%s,\nqui a capturé un\x0b%s!",
        )
        self.assertEqual(report["details"][source], "semantic")

    def test_gold_config_never_names_a_ram_or_num_buffer(self):
        # General guard, added after TWO separate stale-named-token
        # regressions in this same config file were each only caught by a
        # dedicated end-to-end test (one in a "placeholders" dict, one in a
        # "source_aliases" list): Gold's own extracted source text is always
        # bare (RomExtractorGen2.lua:decodeGen2Text never names the buffer --
        # see corpus_to_engine's bare_dynamic_tokens docstring), so ANY
        # string anywhere in config/gold/*.json naming one
        # ("{RAM:...}"/"{NUM:...}") can never match again and silently drops
        # that entry -- regardless of which JSON key or file holds it. Walks
        # every file's whole structure rather than special-casing today's
        # known field names, so a new field (or a new gold/*.json file)
        # introduced later is covered too.
        import json
        import re

        gold_config_dir = Path(__file__).resolve().parents[1] / "config" / "gold"
        named_token = re.compile(r"\{(?:RAM|NUM):")

        def walk(value, where):
            if isinstance(value, str):
                self.assertNotRegex(value, named_token, f"stale named token at {where}: {value!r}")
            elif isinstance(value, dict):
                for key, sub in value.items():
                    walk(key, f"{where}[key]")
                    walk(sub, f"{where}[{key!r}]")
            elif isinstance(value, list):
                for index, sub in enumerate(value):
                    walk(sub, f"{where}[{index}]")

        json_paths = sorted(gold_config_dir.glob("*.json"))
        self.assertTrue(json_paths, f"no config JSON found under {gold_config_dir}")
        for path in json_paths:
            walk(json.loads(path.read_text(encoding="utf-8")), path.name)

    def test_rby_semantic_anchor_still_names_its_ram_buffer(self):
        # Regression: the Gold opt-in above must not change RBY's own
        # named-buffer behavior (game left at Alignment's default/"both").
        source = "%s gained\nthe %s!"
        qid_a, qid_b = "rb.a", "rb.b"
        rows = [
            Alignment(qid_a, "both", CorpusRecord(qid_a, "en", "{text_ram wNameBuffer}{text_start} gained@"),
                      CorpusRecord(qid_a, "fr", "{text_ram wNameBuffer}{text_start} gagne@"), "qid"),
            Alignment(qid_b, "both", CorpusRecord(qid_b, "en", "the {text_ram wStringBuffer}{text_start}!<DONE>"),
                      CorpusRecord(qid_b, "fr", "le {text_ram wStringBuffer}{text_start}!<DONE>"), "qid"),
        ]
        anchor = {"parts": [
            {"qid": qid_a, "extraction": {"kind": "full", "preserve_edges": True}},
            {"qid": qid_b, "extraction": {"kind": "full", "preserve_edges": True}},
        ], "separators": ["\n"], "placeholders": {
            "{RAM:wNameBuffer}": {"printf": 0},
            "{RAM:wStringBuffer}": {"printf": 1},
        }}
        output, _ = match_engine_catalog({source: ""}, rows,
            semantic_anchors={source: anchor}, target_lang="fr")
        self.assertEqual(output[source], "%s gagne\nle %s!")

    def test_multi_qid_parts_typed_printf_mapping_restores_numeric_format(self):
        source = "%s gained\n%03d EXP. Points!"
        qid_a, qid_b = "rb.test.Gained", "rb.test.ExpPoints"
        rows = [
            Alignment(qid_a, "both", CorpusRecord(qid_a, "en", "{text_ram wNameBuffer}{text_start} gained@"),
                      CorpusRecord(qid_a, "fr", "{text_ram wNameBuffer}{text_start} gagne@"), "qid"),
            Alignment(qid_b, "both", CorpusRecord(qid_b, "en", "{text_decimal wExpAmountGained, 2, 4}{text_start} EXP. Points!@"),
                      CorpusRecord(qid_b, "fr", "{text_decimal wExpAmountGained, 2, 4}{text_start} points d'EXP!@"), "qid"),
        ]
        anchor = {"parts": [
            {"qid": qid_a, "extraction": {"kind": "full", "preserve_edges": True}},
            {"qid": qid_b, "extraction": {"kind": "full", "preserve_edges": True}},
        ], "separators": ["\n"], "placeholders": {
            "{RAM:wNameBuffer}": {"printf": 0},
            "{NUM:wExpAmountGained}": {"printf": 1},
        }}
        output, report = match_engine_catalog({source: ""}, rows,
            semantic_anchors={source: anchor}, target_lang="fr")
        self.assertEqual(output[source], "%s gagne\n%03d points d'EXP!")
        self.assertEqual(report["details"][source], "semantic")

    def test_direct_printf_prefix_does_not_append_the_english_qid_suffix(self):
        source = "%s!\nI overslept!"
        qid = "gs.clock.overslept"
        rows = [Alignment(
            qid, "gold", CorpusRecord(qid, "en", "!<LINE>I overslept!"),
            CorpusRecord(qid, "fr", "!<LINE>Je suis en retard!"), "qid",
        )]
        anchor = {
            "parts": [
                {"printf": 0},
                {"qid": qid, "extraction": {"kind": "full"}},
            ],
            "separators": [""],
            "placeholders": {},
        }
        output, _ = match_engine_catalog(
            {source: ""}, rows, semantic_anchors={source: anchor}, target_lang="fr",
        )
        self.assertEqual(output[source], "%s!\nJe suis en retard!")

    def test_multi_qid_typed_printf_schema_rejects_invalid_type_and_ref(self):
        base = {"parts": [
            {"qid": "a", "extraction": {"kind": "full"}},
            {"qid": "b", "extraction": {"kind": "full"}},
        ], "separators": ["\n"]}
        with self.assertRaises(ValueError):
            load_semantic_anchors({"%s\n%d": {**base, "placeholders": {
                "{RAM:x}": "%d", "{NUM:y}": "%s"}}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"%s\n%d": {**base, "placeholders": {
                "{RAM:x}": {"printf": 2}, "{NUM:y}": {"printf": 1}}}})

    def test_multi_qid_typed_printf_rejects_mixed_target_printf(self):
        source = "%s gained\n%d EXP. Points!"
        rows = [
            Alignment("a", "both", CorpusRecord("a", "en", "{text_ram wNameBuffer} gained@"),
                      CorpusRecord("a", "fr", "{text_ram wNameBuffer} gagne %s@"), "qid"),
            Alignment("b", "both", CorpusRecord("b", "en", "{text_decimal wExpAmountGained, 2, 4} EXP. Points!@"),
                      CorpusRecord("b", "fr", "{text_decimal wExpAmountGained, 2, 4} points!@"), "qid"),
        ]
        anchor = {"parts": [
            {"qid": "a", "extraction": {"kind": "full"}},
            {"qid": "b", "extraction": {"kind": "full"}},
        ], "separators": ["\n"], "placeholders": {
            "{RAM:wNameBuffer}": {"printf": 0},
            "{NUM:wExpAmountGained}": {"printf": 1},
        }}
        output, report = match_engine_catalog({source: ""}, rows,
            semantic_anchors={source: anchor}, target_lang="fr")
        self.assertEqual(output[source], "")
        self.assertEqual(report["details"][source], "semantic_unresolved")

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

    def test_span_suffix_is_a_safe_string(self):
        extraction = {"kind": "span", "index": 0, "count": 1, "suffix": "\f"}
        self.assertEqual(_extract_anchor("A B", extraction), "A\f")
        for suffix in (None, False, 0, []):
            with self.assertRaises(ValueError):
                load_semantic_anchors({"X": {"qid": "q", "extraction": {**extraction, "suffix": suffix}}})

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

    def test_override_ships_unmapped_tokens_verbatim_without_corpus_to_engine(self):
        # This is how RBY's own preexisting unmapped tokens (<PK>, <MN>:
        # absent from pipeline/tokens.py's _CORPUS_EXPANSIONS) ship today --
        # an engine override is used verbatim, bypassing corpus_to_engine
        # entirely, so an override author can hand-translate a raw token
        # without pipeline support for it. Automated (non-override) matches
        # have no such escape hatch: they always go through corpus_to_engine.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.json"
            path.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                "entries": {"WITHDRAW <PK><MN>": {"override": "RETIRER <PK><MN>"}},
            }), encoding="utf-8")
            overrides = load_engine_overrides(path)
        output, report = match_engine_catalog(
            {"WITHDRAW <PK><MN>": ""}, [row("WITHDRAW <PK><MN>", "RETIRER POKéMON")], overrides,
        )
        # The override's raw <PK><MN> is untouched -- proof it never passed
        # through corpus_to_engine, which would leave it exactly as-is
        # anyway (it is unmapped) but for a different, non-bypass reason:
        # an automated match, without the override, keeps the corpus
        # translation instead, also unconverted since <PK>/<MN> are unmapped.
        self.assertEqual(output["WITHDRAW <PK><MN>"], "RETIRER <PK><MN>")
        self.assertEqual(report["override"], 1)

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
            path.write_text('return { ["A\\011B"] = "", }\n', encoding="utf-8")
            self.assertEqual(read_engine_catalog(path), {"A\vB": ""})
            path.write_text('return { ["A\\x42"] = "", }\n', encoding="utf-8")
            self.assertEqual(read_engine_catalog(path), {"AB": ""})
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

    def test_structural_matching_preserves_mixed_engine_and_printf_placeholders(self):
        source = "{PLAYER} put the\n%s in\nthe %s."
        english = (
            "<PLAYER> put the<LINE>{text_ram wStringBuffer1} in<CONT>"
            "the {text_ram wStringBuffer3}."
        )
        french = (
            "<PLAYER> range {text_ram wStringBuffer1}<LINE>dans "
            "{text_ram wStringBuffer3}."
        )
        output, report = match_engine_catalog(
            {source: ""},
            [Alignment("put", "gold", CorpusRecord("put", "en", english),
                       CorpusRecord("put", "fr", french), "qid")],
        )
        self.assertEqual(output[source], "{PLAYER} range %s\ndans %s.")
        self.assertEqual(report["auto_structural"], 1)

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
            "engine_rby": {"translated": "bad", "total": 2, "percent": 0},
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
