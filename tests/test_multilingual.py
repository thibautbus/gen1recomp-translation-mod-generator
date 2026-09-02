import json
import tempfile
import unittest
from pathlib import Path

from pipeline.align import align
from pipeline.corpus import parse_redblue, parse_yellow, canonical_language
from pipeline.engine import check_printf_directives, load_engine_overrides, load_semantic_anchors, match_engine_catalog, _extract_anchor, printf_directives, read_engine_catalog
from pipeline.generate import lua_string
from pipeline.model import Alignment, CorpusRecord
from pipeline.mod import generate_mod, validate_commands_show_text_collisions
from pipeline.cli import main as cli_main
from pipeline.engine_scope import classify_callsites, iter_callsites
from pipeline.tokens import corpus_to_engine


class MultilingualTests(unittest.TestCase):
    @staticmethod
    def _partial_fixture_overrides(language, engine_catalog, destination):
        """Scope the full override manifest to an intentionally partial fixture.

        These integration tests use cached, historical strings.lua scaffolds to
        exercise corpus joins.  Strict generation must still reject stale
        overrides for a complete engine catalog, so the fixture explicitly
        carries only the entries that its partial catalog can validate.
        """
        source = Path("overrides") / language / "rby" / "engine.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        entries = payload.get("entries", payload)
        known = read_engine_catalog(engine_catalog)
        scoped = {key: row for key, row in entries.items() if key in known}
        output = Path(destination) / "rby-engine-overrides.json"
        output.write_text(json.dumps({
            "schema": "gen1recomp-translation-mods/engine-overrides",
            "version": 1,
            "entries": scoped,
        }, ensure_ascii=False), encoding="utf-8")
        return output

    def test_commands_show_text_collision_guard_is_narrow_and_pre_format(self):
        with self.assertRaisesRegex(ValueError, "double lookup.*upstream Commands.show_text API limitation"):
            validate_commands_show_text_collisions(
                {"{PLAYER} got\n%s!": "_BagFullText"}, {"_BagFullText"},
            )
        validate_commands_show_text_collisions(
            {"{PLAYER} got\n%s!": "Vous avez reçu %s !"}, {"_BagFullText"},
        )

    def test_summary_menu_dynamic_labels_match_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout unavailable")
        keys = {"NAME": "rb.start_sub_menus.TrainerInfo_NameMoneyTimeText", "ATTACK": "rb.stat_names.VitaminStats.2", "DEFENSE": "rb.stat_names.VitaminStats.3", "SPEED": "rb.stat_names.VitaminStats.4", "SPECIAL": "rb.stat_names.VitaminStats.5"}
        anchors = load_semantic_anchors()
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(keys, items, semantic_anchors=anchors, target_lang=language)
            self.assertTrue(all(output[key] for key in keys), language)
            self.assertTrue(all(report["details"][key] == "semantic" for key in keys), language)

    def test_generated_strings_include_dynamic_labels_and_empty_rom_owned_keys(self):
        corpus_root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (corpus_root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout unavailable")
        worksheets = Path(".cache/interactive/fr/complete-modkit-worksheet")
        if not (worksheets / "strings.lua").is_file():
            self.skipTest("cached modkit worksheet unavailable")
        rom_keys = ("Crammed full of\nPOKéMON books!", "Keep it up!", "No SURFing here!", "Nothing to CUT!", "POKéDEX Rating{COLON}", "{RIVAL}: Yeah! Am\nI great or what?", "Welcome to our\nPOKéMON CENTER!", "Your POKéMON are\nfighting fit!")
        from tempfile import TemporaryDirectory
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            worksheet = Path(".cache/interactive") / language / "complete-modkit-worksheet"
            if not (worksheet / "strings.lua").is_file():
                self.skipTest(f"cached {language} worksheet unavailable")
            rows = align(parse_redblue(corpus_root, language), target_lang=language)
            with TemporaryDirectory() as tmp:
                mod = generate_mod(rows, Path(tmp) / "mod", language=language, modkit_worksheet=worksheet, engine_catalog=worksheet / "strings.lua", engine_overrides=self._partial_fixture_overrides(language, worksheet / "strings.lua", tmp), strict_engine=True)
                strings = (mod / "lang/strings.lua").read_text(encoding="utf-8")
                for key in ("NAME", "ATTACK", "DEFENSE", "SPEED", "SPECIAL"):
                    self.assertIn(f'  ["{key}"] = ', strings, language)
                for key in rom_keys:
                    self.assertIn(f'  [{lua_string(key)}] = "",', strings, language)
                self.assertIn('  ["_PokemonBooksText"] = ', (mod / "lang/dialogue.lua").read_text(encoding="utf-8"), language)

    def test_real_corpus_type_names_cover_all_runtime_types(self):
        corpus_root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (corpus_root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout unavailable")
        runtime_ids = ("NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST", "FIRE", "WATER", "GRASS", "ELECTRIC", "PSYCHIC_TYPE", "ICE", "DRAGON")
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            worksheet = Path(".cache/interactive") / language / "complete-modkit-worksheet"
            if not (worksheet / "strings.lua").is_file():
                self.skipTest(f"cached {language} worksheet unavailable")
            rows = align(parse_redblue(corpus_root, language), target_lang=language)
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as tmp:
                mod = generate_mod(rows, Path(tmp) / "mod", language=language, modkit_worksheet=worksheet, engine_catalog=worksheet / "strings.lua", engine_overrides=self._partial_fixture_overrides(language, worksheet / "strings.lua", tmp), strict_engine=True)
                body = (mod / "lang/type_names.lua").read_text(encoding="utf-8")
                for type_id in runtime_ids:
                    self.assertIn(f'  ["{type_id}"] = ', body, (language, type_id))
                    self.assertNotIn(f'  ["{type_id}"] = "",', body, (language, type_id))
                # The corpus Bird row must never reach the catalog: the engine
                # registers no Bird type, so the patch would fail validation.
                self.assertNotIn('"BIRD"', body, language)
                # Drive the real builder path against the modkit scaffold so
                # the runtime hook lands exactly as an interactive build would.
                scaffold = Path(".cache/interactive") / language / "translation_source"
                if not (scaffold / "main.lua").is_file():
                    self.skipTest(f"cached {language} scaffold unavailable")
                from pipeline import builder
                builder.preserve_scaffold_support(scaffold, mod)
                main = (mod / "main.lua").read_text(encoding="utf-8")
                self.assertIn('counts.type_names = each("type_names"', main, language)
                self.assertIn('by_english[canonical] = localized', main, language)
                self.assertIn('Font.draw = function(text, x, y, ...)', main, language)
                self.assertIn('local demo_names = catalog("demo_names")', main, language)
                self.assertIn('BS.oldManThrow = function(self, ...)', main, language)
                self.assertIn('localizedDemoName(self, canonical)', main, language)
                self.assertNotIn('Runtime.hooks:wrap("player.sprite"', main, language)
                self.assertNotIn('BS.makeOldManDemo = function', main, language)
                self.assertNotIn('mod.content.type_chart:patch', main, language)
                # engine send-out templates translated from the corpus row
                strings = (mod / "lang/strings.lua").read_text(encoding="utf-8")
                self.assertIn('["%s is\\nabout to use\\011%s!"] = "', strings, language)
                self.assertNotIn('["%s is\\nabout to use\\011%s!"] = "",', strings, language)
                self.assertIn('["Will %s\\nchange POKéMON?"] = "', strings, language)
                self.assertNotIn('["%s is\\nabout to use"] = "', strings, language)
                # romText fallback keys the engine renders via Strings
                self.assertIn('["%s\\nused %s!"] = "', strings, language)
                self.assertNotIn('["%s\\nused %s!"] = "",', strings, language)
                self.assertIn('["The enemy\'s weak!\\nGet\'m! %s!"] = "', strings, language)
                self.assertNotIn('["The enemy\'s weak!\\nGet\'m! %s!"] = "",', strings, language)
                self.assertIn('["Enemy %s"] = "', strings, language)
                self.assertNotIn('["Enemy %s"] = "",', strings, language)
                self.assertIn('["FOE"] = "', strings, language)
                self.assertNotIn('["FOE"] = "",', strings, language)
                self.assertIn('["SEEN %3d  OWN %3d"] = "', strings, language)
                self.assertNotIn('["SEEN %3d  OWN %3d"] = "",', strings, language)
                # corpus-backed demo name (old-man tutorial literal) translated
                demo = (mod / "lang/demo_names.lua").read_text(encoding="utf-8")
                self.assertIn('  ["OLD MAN"] = ', demo, language)
                self.assertIn('  ["PROF.OAK"] = ', demo, language)
                self.assertNotIn('  ["OLD MAN"] = "",', demo, language)
                self.assertNotIn('  ["PROF.OAK"] = "",', demo, language)

    def test_oak_speech_rom_symbol_stays_empty_engine_and_dialogue_localized(self):
        source = "{text_start}This world is<LINE>inhabited by<CONT>creatures called<CONT>#MON!@@"
        qid = "rb.text_2.OakSpeechText2A"
        self.assertNotIn("_OakSpeechText2A", load_semantic_anchors())
        translations = {
            "fr": "{text_start}Ce monde est<LINE>peuplé de<CONT>créatures du nom<CONT>de #MON!@@",
            "de": "{text_start}Diese Welt wird<LINE>von Wesen<CONT>bewohnt, die man<CONT>#MON nennt!@@",
            "es": "{text_start}¡Este mundo está<LINE>habitado por unas<CONT>criaturas<CONT>llamadas #MON!@@",
            "it": "{text_start}Questo mondo è<LINE>abitato da<CONT>creature<CONT>chiamate #MON!@@",
            "ja-Hrkt": "{text_start}この　せかいには<LINE>ポケット　モンスターと　よばれる<PARA>いきもの　たちが<LINE>いたるところに　すんでいる！@",
        }
        oak_source = Path(".cache/dependencies/gen1recomp/src/ui/OakSpeech.lua")
        if not oak_source.is_file():
            self.skipTest("pinned Gen1Recomp checkout is unavailable")
        oak_runtime = oak_source.read_text(encoding="utf-8")
        self.assertIn('Strings.source("This world is\\ninhabited by\\vcreatures called\\vPOKéMON!")', oak_runtime)
        self.assertIn('self:say("_OakSpeechText2A", function() self:advance() end)', oak_runtime)
        for language, target in translations.items():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                worksheet = root / "worksheet"
                worksheet.mkdir()
                for name in ("dialogue", "species_names", "move_names", "item_names", "trainer_names", "status_labels"):
                    (worksheet / f"{name}.txt").write_text("# header\n", encoding="utf-8")
                (worksheet / "dialogue.txt").write_text(
                    '"_OakSpeechText2A"\t"This world is\\ninhabited by\\11creatures called\\11POKéMON!"\n',
                    encoding="utf-8",
                )
                (worksheet / "strings.lua").write_text(
                    'return {\n  ["_OakSpeechText2A"] = "",\n}\n', encoding="utf-8"
                )
                row = Alignment(
                    qid, "both", CorpusRecord(qid, "en", source),
                    CorpusRecord(qid, language, target), "qid", target_lang=language,
                )
                mod = generate_mod(
                    [row], root / "mod", language=language,
                    modkit_worksheet=worksheet, engine_catalog=worksheet / "strings.lua",
                    strict_engine=True,
                )
                dialogue = (mod / "lang/dialogue.lua").read_text(encoding="utf-8")
                strings = (mod / "lang/strings.lua").read_text(encoding="utf-8")
                self.assertIn(
                    f'  ["_OakSpeechText2A"] = {lua_string(corpus_to_engine(target))},',
                    dialogue, language,
                )
                self.assertIn('  ["_OakSpeechText2A"] = "",', strings, language)

    def test_es_it_engine_override_files_load_from_overrides_tree(self):
        for language in ("es", "it"):
            path = Path("overrides") / language / "rby" / "engine.json"
            self.assertTrue(path.is_file())
            overrides = load_engine_overrides(path)
            self.assertEqual(sum(entry.get("reason") == "editorial-correction" for entry in overrides.values()), 4)
            self.assertTrue(all(entry.get("provenance") for entry in overrides.values()))

    def test_german_greatly_stage_overrides_cover_empty_corpus_fragments(self):
        expected = {
            "%s's\n%s\ngreatly rose!": "%ss\n%s nimmt stark zu!",
            "%s's\n%s\ngreatly fell!": "%ss\n%s sinkt stark!",
        }
        overrides = load_engine_overrides(Path("overrides/de/rby/engine.json"))
        for key, value in expected.items():
            self.assertEqual(overrides[key]["override"], value)
            self.assertEqual(overrides[key]["reason"], "engine-original")
            self.assertIn("German corpus omits", overrides[key]["provenance"])
            self.assertEqual(printf_directives(key), printf_directives(value))

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

    def test_rby_numeric_experience_and_status_anchor_batch_all_languages(self):
        root = Path("../poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = [
            "%s gained\n%d EXP. Points!",
            "%s gained\nwith EXP.ALL,\x0b%d EXP. Points!",
            "%s gained\na boosted\x0b%d EXP. Points!",
            "%s grew\nto level %d!",
            "EXP POINTS", "LEVEL UP", "%s\nis glowing!",
        ]
        expected = {
            "fr": ["%s gagne\n%d points d'EXP!", "%s gagne\navec MULTI EXP,\x0b%d points d'EXP!", "%s gagne\nun bonus de\x0b%d points d'EXP!", "%s monte\nau niveau %d!", "PTS EXP.", "PROCH.NIV.", "%s\nbrille!"],
            "de": ["%s erhält\n%d EP!", "%s erhält\nmittels EP-TEILER\x0b%d EP!", "%s erhält\nspezielle\x0b%d EP!", "%s\nerreicht\x0bLevel %d!", "EP-PUNKTE", "LEVEL UP", "%s\nleuchtet!"],
            "es": ["¡%s ganó\n%d Puntos EXP.!", "¡%s ganó\ncon REPARTIR EXP,\x0b%d Puntos EXP.!", "¡%s ganó\nun extra de\x0b%d Puntos EXP.!", "¡%s subió\nal nivel %d!", "PUNTOS EXP", "SIG.NIVEL", "¡%s\nestá brillando!"],
            "it": ["%s riceve\n%d Punti ESP.!", "%s riceve\ncon DISTRIB. ESP,\x0b%d Punti ESP.!", "%s riceve\nben\x0b%d Punti ESP.!", "%s sale\nal livello %d!", "PUNTI ESP.", "LIV. SUP.", "%s\nsta brillando!"],
            "ja-Hrkt": ["%sは\n%d　けいけんちを　もらった！", "%sは\nがくしゅうそうちで\x0b%d　けいけんちを　もらった！", "%sは\nおおめに\x0b%d　けいけんちを　もらった！", "%sは\nレベル%d　に　あがった！", "けいけんち", "あと", "%s\nを\nはげしい　ひかりが　つつむ！"],
        }
        anchors = load_semantic_anchors()
        for language, language_expected in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language)
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            for key, value in zip(keys, language_expected):
                self.assertEqual(output[key], value, (language, key))
                self.assertEqual(report["details"][key], "semantic", (language, key))
                self.assertEqual(report["provenance"][key].get("target_lang"), language)
                self.assertNotRegex(value, r"[<>@]", (language, key))

    def test_real_corpus_rby_safe_anchor_batch_exact_outputs_and_provenance(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        box_prompt = "When you change a\nPOKéMON BOX, data\x0bwill be saved.\x0cIs that okay?"
        legacy_box_prompt = "When you change a\nPOKéMON BOX, data\nwill be saved. OK?"
        keys = [
            "TYPE/", "TYPE1/", "TYPE2/", "OT/", "LEVEL/", "BALLx", "THROW ROCK",
            "STATS", "SWITCH", "CHANGE BOX", "SEE YA!", "WITHDRAW ITEM",
            "DEPOSIT ITEM", "TOSS ITEM",
            "You don't have\nenough money.", "BATTLE ANIMATION",
            box_prompt,
        ]
        qids = {
            "TYPE/": "rb.core.TypeText",
            "TYPE1/": "rb.status_screen.TypesIDNoOTText",
            "TYPE2/": "rb.status_screen.TypesIDNoOTText",
            "OT/": "rb.status_screen.TypesIDNoOTText",
            "LEVEL/": "rb.hall_of_fame.HoFMonInfoText",
            "BALLx": "rb.text_boxes.SafariZoneBattleMenuText",
            "THROW ROCK": "rb.text_boxes.SafariZoneBattleMenuText",
            "STATS": "rb.text_box.PokemonMenuEntries",
            "SWITCH": "rb.text_box.PokemonMenuEntries",
            "CHANGE BOX": "rb.bills_pc.BillsPCMenuText",
            "SEE YA!": "rb.bills_pc.BillsPCMenuText",
            "WITHDRAW ITEM": "rb.players_pc.PlayersPCMenuEntries",
            "DEPOSIT ITEM": "rb.players_pc.PlayersPCMenuEntries",
            "TOSS ITEM": "rb.players_pc.PlayersPCMenuEntries",
            "You don't have\nenough money.": "rb.text_4.PokemartNotEnoughMoneyText",
            "BATTLE ANIMATION": "rb.main_menu.BattleAnimationOptionText",
            box_prompt: "rb.text_3.WhenYouChangeBoxText",
        }
        expected = {
            "fr": ["TYPE", "TYPE1", "TYPE2", "DO", "NIVEAU", "BALL×", "CAILLOU", "STATS", "ORDRE", "CHANGER BOITE", "SALUT!", "RETIRER OBJET", "STOCKER OBJET", "JETER OBJET", "Ah! Pas d'argent,\npas d'copains!", "ANIMATION COMBAT", "En activant\nune autre boîte\x0bde POKéMON, les\x0bdonnées seront\x0bsauvegardées.\x0cEtes-vous\nd'accord?"],
            "de": ["TYP", "TYP1", "TYP2", "OT", "LEVEL", "BALL×", "STEIN", "STATUS", "TAUSCH", "BOX WECHSELN", "TSCHÜSS!", "ITEM AUFNEHMEN", "ITEM ABLEGEN", "ITEM WEGWERFEN", "Du hast nicht\ngenug Geld.", "KAMPFANIMATION", "Vor einem Wechsel\nder POKéMON-BOX\x0bwird das Spiel\x0bgesichert!\x0cEinverstanden?"],
            "es": ["TIPO", "TIPO1", "TIPO2", "EO", "NIVEL", "BALL×", "LANZA ROCA", "ESTAD.", "CAMBIO", "CAMBIA CAJA", "¡NOS VEMOS!", "SACAR OBJETO", "DEJAR OBJETO", "TIRAR OBJETO", "No tienes\ntanto dinero.", "ANIMACIÓN BATALLA", "Si cambias una\nCAJA de POKéMON,\x0bsus datos serán\x0bguardados.\x0c¿Estás de\nacuerdo?"],
            "it": ["TIPO", "TIPO1", "TIPO2", "AO", "LIVELLO", "BALL×", "TIRA SASSO", "STAT.", "ORDINA", "CAMBIA BOX", "CIAO!", "RITIRA STRUM.", "DEPOSITA STRUM.", "BUTTA STRUM.", "Non hai\nabbastanza soldi.", "ANIMAZIONE LOTTA", "Al cambio del\nPOKéMON BOX\x0bil gioco verrà\x0bsalvato!\x0cD'accordo?"],
            "ja-Hrkt": ["わざタイプ", "タイプ１", "タイプ２", "おや", "レベル", "サファリボール×", "いしをなげる", "つよさをみる", "ならびかえ", "ボックスを　かえる", "さようなら", "どうぐを　ひきだす", "どうぐを　あずける", "どうぐを　すてる", "おかねが　たりないようですね", "せんとう　アニメーション", "POKé　ボックスを　かえると\nどうじに　レポートが　かかれます\x0c……　それでも　いいですか？"],
        }
        anchors = load_semantic_anchors()
        for key in keys:
            self.assertEqual(anchors[key]["qid"], qids[key])
        self.assertEqual(anchors["TYPE/"]["source_aliases"], ["TYPE"])
        self.assertEqual(anchors["BALLx"]["source_aliases"], ["BALL×"])
        self.assertEqual(anchors["BATTLE ANIMATION"]["source_aliases"], ["BATTLE ANIMATION"])
        self.assertEqual(anchors[box_prompt]["engine_keys"], [legacy_box_prompt])
        self.assertEqual(anchors[box_prompt]["source_aliases"], [legacy_box_prompt])
        for language, values in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({key: "" for key in keys}, items, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key, value in zip(keys, values):
                self.assertEqual(output[key], value, (language, key))
                self.assertEqual(report["details"][key], "semantic", (language, key))
                self.assertEqual(report["provenance"][key]["qid"], qids[key], (language, key))
            legacy_output, legacy_report = match_engine_catalog(
                {legacy_box_prompt: ""}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(legacy_output[legacy_box_prompt], values[-1], language)
            self.assertEqual(legacy_report["details"][legacy_box_prompt], "semantic", language)
            self.assertEqual(
                legacy_report["provenance"][legacy_box_prompt]["qid"],
                "rb.text_3.WhenYouChangeBoxText", language,
            )

    def test_yellow_engine_print_box_override_matches_the_real_corpus_segment(self):
        # PRINT BOX is Yellow-exclusive: y.bills_pc.BillsPCMenuText inserts a
        # sixth <NEXT>-separated menu item ("PRINT BOX") that RedBlue's
        # five-item rb.bills_pc.BillsPCMenuText does not have. The base RBY
        # engine-matching pass in generate_mod() only ever sees RedBlue-
        # aligned records (see builder.build()), so a semantic anchor
        # pointing at a Yellow-only qid can never resolve there -- this key
        # must instead be a manual overrides/<language>/rby/yellow_engine.json
        # entry (like its Yellow-only siblings), not a semantic_anchors.json
        # entry. This test only confirms that override text still matches
        # the real, current corpus segment.
        root = Path(".cache/dependencies/poke-corpus/corpus/Yellow")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            records = parse_yellow(root, language)
            row = next(r for r in records if r.qid == "y.bills_pc.BillsPCMenuText" and r.language == language)
            segment = _extract_anchor(row.text, {"kind": "segment", "index": 4})
            override = load_engine_overrides(Path("overrides") / language / "rby" / "yellow_engine.json")["PRINT BOX"]
            self.assertEqual(override["override"], segment, language)

    def test_rby_safe_anchor_batch_fails_closed_on_duplicate_or_missing_qid(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        anchors = load_semantic_anchors()
        items = align(parse_redblue(root, "fr"), target_lang="fr")
        for key in ("TYPE/", "THROW ROCK", "BATTLE ANIMATION", "When you change a\nPOKéMON BOX, data\x0bwill be saved.\x0cIs that okay?"):
            qid = anchors[key]["qid"]
            row = next(item for item in items if item.qid == qid)
            output, report = match_engine_catalog({key: ""}, items + [row], semantic_anchors=anchors, target_lang="fr")
            self.assertEqual(output[key], "", key)
            self.assertEqual(report["details"][key], "semantic_unresolved", key)
            missing = json.loads(json.dumps(anchors))
            missing[key]["qid"] = "rb.missing.Anchor"
            output, report = match_engine_catalog({key: ""}, items, semantic_anchors=missing, target_lang="fr")
            self.assertEqual(output[key], "", key)
            self.assertEqual(report["details"][key], "semantic_unresolved", key)

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

    def test_dex_seen_owned_anchor_fails_closed_on_bad_selector_or_qid(self):
        source = "SEEN %d  OWNED %d"
        with self.assertRaises(ValueError):
            load_semantic_anchors({source: {"qid": "q", "extraction": {
                "kind": "dex_counter", "selector": "broad"}}})
        rows = [Alignment(
            "q.dex", "both",
            CorpusRecord("q.dex", "en", "{text_start}#DEX Seen:@{text_decimal seen}{text_start}<LINE>Owned:@{text_decimal owned}@"),
            CorpusRecord("q.dex", "fr", "{text_start}#DEX Vus:@{text_decimal seen}{text_start}<LINE>Pris:@{text_decimal owned}@"),
            "qid", target_lang="fr")]
        anchor = {source: {"qid": "q.dex", "extraction": {
            "kind": "dex_counter", "selector": "rby_dex_seen_owned"}}}
        output, report = match_engine_catalog({source: ""}, rows,
            semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(output[source], "Vus:%d  Pris:%d")
        duplicate, duplicate_report = match_engine_catalog({source: ""}, rows + [rows[0]],
            semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(duplicate[source], "")
        self.assertEqual(duplicate_report["details"][source], "semantic_unresolved")
        missing, missing_report = match_engine_catalog({source: ""}, rows,
            semantic_anchors={source: {"qid": "q.missing", "extraction": {
                "kind": "dex_counter", "selector": "rby_dex_seen_owned"}}}, target_lang="fr")
        self.assertEqual(missing[source], "")
        self.assertEqual(missing_report["details"][source], "semantic_unresolved")
        incompatible = Alignment(
            "q.dex", "both", rows[0].english,
            CorpusRecord("q.dex", "fr", "{text_start}#DEX Vus:@{text_decimal owned}{text_start}<LINE>Pris:@{text_decimal seen}@"),
            "qid", target_lang="fr")
        failed, failed_report = match_engine_catalog({source: ""}, [incompatible],
            semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(failed[source], "")
        self.assertEqual(failed_report["details"][source], "semantic_unresolved")

    def test_dex_seen_owned_selector_rejects_unaudited_regions(self):
        source = "SEEN %d  OWNED %d"
        anchor = {source: {"qid": "q.dex", "extraction": {
            "kind": "dex_counter", "selector": "rby_dex_seen_owned"}}}
        english = "{text_start}#DEX Seen:@{text_decimal seen}{text_start}<LINE>Owned:@{text_decimal owned}@"
        french = "{text_start}#DEX Vus:@{text_decimal seen}{text_start}<LINE>Pris:@{text_decimal owned}@"
        cases = {
            # Text between the first number and the audited line boundary is
            # not a suffix variant from any of the five corpus rows.
            "unknown_first_suffix": english.replace("{text_start}<LINE>Owned", " UNKNOWN{text_start}<LINE>Owned"),
            # A second heading/control line before the first number is never
            # part of DexSeenOwnedText's reviewed shape.
            "extra_heading_line": english.replace("#DEX Seen", "#DEX<LINE>Extra Seen"),
            # PKMN is audited only as a paired German suffix, not generically
            # on one counter or one localized target row.
            "generic_pkmn_suffix": french.replace("{text_decimal owned}@", "{text_decimal owned} PKMN@"),
            "generic_pkmn_pair": french.replace(
                "{text_start}<LINE>", " PKMN{text_start}<LINE>"
            ).replace("{text_decimal owned}@", "{text_decimal owned} PKMN@"),
        }
        for label, mutated in cases.items():
            target = mutated if label != "generic_pkmn_suffix" else mutated
            rows = [Alignment(
                "q.dex", "both", CorpusRecord("q.dex", "en", english),
                CorpusRecord("q.dex", "fr", target), "qid", target_lang="fr")]
            output, report = match_engine_catalog({source: ""}, rows,
                semantic_anchors=anchor, target_lang="fr")
            self.assertEqual(output[source], "", label)
            self.assertEqual(report["details"][source], "semantic_unresolved", label)

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
        self.assertEqual(output["Welcome to our\nPOKéMON CENTER!"], "")
        self.assertEqual(report["details"]["Welcome to our\nPOKéMON CENTER!"], "english_fallback")

    def test_real_corpus_story_engine_anchor_batch_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = {
            "Hey! There's a\nswitch under the\ntrash!\fThe 1st electric\nlock opened!":
                "rb.text_2.VermilionGymTrashSuccessText1",
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

    def test_real_corpus_proven_rby_ui_anchor_batch_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = {
            "AREA": "rb.pokedex.PokedexMenuItemsText",
            "BATTLE STYLE": "rb.main_menu.BattleStyleOptionText",
            "TEXT SPEED": "rb.main_menu.TextSpeedOptionText",
            "NEW GAME": "rb.main_menu.NewGameText",
            "Which move should": "rb.text_4.WhichMoveToForgetText",
            "be forgotten?": "rb.text_4.WhichMoveToForgetText",
        }
        anchors = load_semantic_anchors()
        for key, qid in keys.items():
            self.assertEqual(anchors[key]["qid"], qid)
            self.assertEqual(anchors[key]["extraction"]["kind"], "segment")
            self.assertIsInstance(anchors[key]["extraction"]["index"], int)
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key, qid in keys.items():
                rows = [row for row in parse_redblue(root, language) if row.qid == qid and row.language == language]
                self.assertEqual(len(rows), 1, (language, key))
                expected = _extract_anchor(rows[0].text, anchors[key]["extraction"], language)
                self.assertTrue(expected, (language, key))
                self.assertEqual(output[key], expected, (language, key))
                self.assertEqual(report["details"][key], "semantic", (language, key))
                self.assertEqual(report["provenance"][key]["qid"], qid, (language, key))

    def test_real_corpus_printf_engine_anchor_batch_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = {
            "%s learned\n%s!": "rb.text_4.LearnedMove1Text",
            "%s found\n%d coins!": "rb.text_2.FoundHiddenCoinsText",
        }
        anchors = load_semantic_anchors()
        for key, qid in keys.items():
            self.assertEqual(
                anchors[key],
                {"qid": qid, "extraction": {"kind": "full", "index": 0}},
            )
        expected = {
            "fr": {
                "%s learned\n%s!": "%s\napprend...\v%s!",
                "%s found\n%d coins!": "%s trouve\n%d jetons!",
            },
            "de": {
                "%s learned\n%s!": "%s lernt\n%s!",
                "%s found\n%d coins!": "%s findet\n%d Münzen!",
            },
            "es": {
                "%s learned\n%s!": "¡%s\naprendió\v%s!",
                "%s found\n%d coins!": "¡%s\nencontró\v%d fichas!",
            },
            "it": {
                "%s learned\n%s!": "%s impara\n%s!",
                "%s found\n%d coins!": "%s trova\n%d gettoni!",
            },
            "ja-Hrkt": {
                "%s learned\n%s!": "%sは　あたらしく\n%sを　おぼえた！",
                "%s found\n%d coins!": "%sは\nコインを　%dまい　みつけた！",
            },
        }
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors,
                target_lang=language,
            )
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key, qid in keys.items():
                self.assertEqual(output[key], expected[language][key], (language, qid))
                self.assertEqual(report["details"][key], "semantic", language)
                self.assertEqual(report["provenance"][key]["qid"], qid, language)

    def test_real_corpus_rby_anchor_batch_exact_outputs_and_provenance(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = (
            "%s is out of\nuseable POKéMON!",
            "%s blacked\nout!",
        )
        qids = {
            keys[0]: "rb.text_2.PlayerBlackedOutText2",
            keys[1]: "rb.text_2.PlayerBlackedOutText2",
        }
        expected = {
            "fr": {
                keys[0]: "%s n'a plus\nde POKéMON!",
                keys[1]: "%s est\nhors-jeu!",
            },
            "de": {
                keys[0]: "Alle POKéMON von\n%s wurden\x0bbesiegt!",
                keys[1]: "%s fällt\nin Ohnmacht!",
            },
            "es": {
                keys[0]: "¡%s no tiene\nmás POKéMON!",
                keys[1]: "¡%s perdió\nel conocimiento!",
            },
            "it": {
                keys[0]: "%s non ha più\nPOKéMON utili!",
                keys[1]: "%s è\ncrollato!",
            },
            "ja-Hrkt": {
                keys[0]: "%sの　てもとには\nたたかえる　POKéが　いない！",
                keys[1]: "%sは\nめのまえが　まっくらに　なった！",
            },
        }
        anchors = load_semantic_anchors()
        for key in keys:
            self.assertEqual(anchors[key]["qid"], qids[key])
            self.assertEqual(anchors[key]["extraction"]["kind"], "parts")
            self.assertEqual(anchors[key]["extraction"]["separators"], ["\n"])
            self.assertEqual(set(anchors[key]["extraction"]["targets"]), {"fr", "de", "es", "it", "ja-Hrkt"})
        for language, language_expected in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key in keys:
                self.assertEqual(output[key], language_expected[key], (language, key))
                self.assertEqual(report["details"][key], "semantic", (language, key))
                self.assertEqual(report["provenance"][key]["qid"], qids[key], (language, key))
                self.assertNotRegex(output[key], r"[<>@]", (language, key))
            self.assertEqual(printf_directives(output[keys[0]]), ["%s"], language)
            self.assertEqual(printf_directives(output[keys[1]]), ["%s"], language)

    def test_real_corpus_legendary_cries_reach_generated_dialogue_catalog(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        qids = {
            "rb.SeafoamIslandsB4F.SeafoamIslandsB4FArticunoBattleText":
                ("_SeafoamIslandsB4FArticunoBattleText", {
                    "de": "Jauul!", "es": "¡Ar Tic!", "it": "Ghiooo!",
                }),
            "rb.PowerPlant.PowerPlantZapdosBattleText":
                ("_PowerPlantZapdosBattleText", {
                    "de": "Jauul!", "es": "¡Zap Zap!", "it": "Yhuhu!",
                }),
            "rb.VictoryRoad2F.VictoryRoad2FMoltresBattleText":
                ("_VictoryRoad2FMoltresBattleText", {
                    "de": "Jauuul!", "es": "¡Mol Tres!", "it": "Yhuhu!",
                }),
        }
        for language in ("de", "es", "it"):
            worksheet = Path(".cache/interactive") / language / "complete-modkit-worksheet"
            if not (worksheet / "strings.lua").is_file():
                self.skipTest(f"cached {language} complete modkit worksheet is unavailable")
            records = [row for row in parse_redblue(root, language) if row.qid in qids]
            items = align(records, target_lang=language)
            self.assertEqual({row.method for row in items}, {"qid"}, language)
            with tempfile.TemporaryDirectory() as tmp:
                mod = generate_mod(
                    items,
                    Path(tmp) / "mod",
                    language=language,
                    modkit_worksheet=worksheet,
                    engine_catalog=worksheet / "strings.lua",
                    engine_overrides=self._partial_fixture_overrides(language, worksheet / "strings.lua", tmp),
                    strict_engine=True,
                )
                dialogue = (mod / "lang/dialogue.lua").read_text(encoding="utf-8")
                for qid, (label, translations) in qids.items():
                    self.assertIn(f'  ["{label}"] = "{translations[language]}",', dialogue,
                                  (language, qid))
                # The shared English cry remains unresolved in the engine table;
                # each species-specific dialogue qid supplies the localized value.
                strings = (mod / "lang/strings.lua").read_text(encoding="utf-8")
                self.assertIn('  ["Gyaoo!"] = "",', strings, language)

    def test_real_corpus_item_use_anchor_exact_outputs_and_provenance_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        key = "USE"
        qid = "rb.text_boxes.UseTossText"
        expected = {
            "fr": "UTIL.",
            "de": "OK",
            "es": "USAR",
            "it": "USA",
            "ja-Hrkt": "つかう",
        }
        anchors = load_semantic_anchors()
        self.assertEqual(anchors[key]["qid"], qid)
        self.assertEqual(anchors[key]["extraction"], {"kind": "segment", "index": 0})
        for language, value in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({key: ""}, items, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], value, language)
            self.assertEqual(report["details"][key], "semantic", language)
            self.assertEqual(report["provenance"][key]["qid"], qid, language)
            self.assertEqual(report["provenance"][key]["extraction"], {"kind": "segment", "index": 0}, language)

    def test_item_use_anchor_fails_closed_on_missing_or_ambiguous_qid(self):
        key = "USE"
        anchor = load_semantic_anchors()[key]
        source = "USE<NEXT>TOSS@"
        good = Alignment(
            "rb.text_boxes.UseTossText", "both",
            CorpusRecord("rb.text_boxes.UseTossText", "en", source),
            CorpusRecord("rb.text_boxes.UseTossText", "fr", "UTIL.<NEXT>JETER@"), "qid",
        )
        output, report = match_engine_catalog({key: ""}, [good], semantic_anchors={key: anchor}, target_lang="fr")
        self.assertEqual(output[key], "UTIL.")
        self.assertEqual(report["details"][key], "semantic")
        missing = dict(anchor, qid="rb.missing.UseTossText")
        output, report = match_engine_catalog({key: ""}, [good], semantic_anchors={key: missing}, target_lang="fr")
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)
        duplicate = Alignment(
            "rb.text_boxes.UseTossText", "both",
            CorpusRecord("rb.text_boxes.UseTossText", "en", source),
            CorpusRecord("rb.text_boxes.UseTossText", "fr", "VERWENDEN<NEXT>JETER@"), "qid",
        )
        output, report = match_engine_catalog({key: ""}, [good, duplicate], semantic_anchors={key: anchor}, target_lang="fr")
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")

    def test_real_corpus_item_carry_period_anchor_exact_outputs_and_provenance_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout unavailable")
        key = "You can't carry\nany more items."
        expected = {
            "fr": "Votre inventaire\nest plein.",
            "de": "Du kannst keine\nweiteren Items\vtragen.",
            "es": "No puedes llevar\nmás objetos.",
            "it": "Non puoi portare\naltri strumenti.",
            "ja-Hrkt": "どうぐが　いっぱいです\nもう　もてません！",
        }
        qid = "rb.text_2.CantCarryMoreText"
        anchors = load_semantic_anchors()
        self.assertEqual(anchors[key], {"qid": qid, "extraction": {"kind": "full"}})
        for language, value in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({key: ""}, items, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], value, language)
            self.assertEqual(report["details"][key], "semantic", language)
            self.assertEqual(report["provenance"][key]["qid"], qid, language)
            self.assertEqual(report["provenance"][key]["extraction"], {"kind": "full"}, language)

    def test_item_carry_period_anchor_fails_closed_and_exclamation_variant_is_untranslated(self):
        key = "You can't carry\nany more items."
        anchor = load_semantic_anchors()[key]
        good = Alignment(
            "rb.text_2.CantCarryMoreText", "both",
            CorpusRecord("rb.text_2.CantCarryMoreText", "en", "{text_start}You can't carry<LINE>any more items.<PROMPT>"),
            CorpusRecord("rb.text_2.CantCarryMoreText", "fr", "{text_start}Votre inventaire<LINE>est plein.<PROMPT>"),
            "qid", target_lang="fr",
        )
        output, report = match_engine_catalog({key: ""}, [good], semantic_anchors={key: anchor}, target_lang="fr")
        self.assertEqual(output[key], "Votre inventaire\nest plein.")
        self.assertEqual(report["details"][key], "semantic")
        missing = dict(anchor, qid="rb.missing.CantCarryMoreText")
        output, report = match_engine_catalog({key: ""}, [good], semantic_anchors={key: missing}, target_lang="fr")
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)
        duplicate = Alignment(
            "rb.text_2.CantCarryMoreText", "both",
            CorpusRecord("rb.text_2.CantCarryMoreText", "en", "{text_start}You can't carry<LINE>any more items.<PROMPT>"),
            CorpusRecord("rb.text_2.CantCarryMoreText", "fr", "Duplikat"),
            "qid", target_lang="fr",
        )
        output, report = match_engine_catalog({key: ""}, [good, duplicate], semantic_anchors={key: anchor}, target_lang="fr")
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")
        exclamation = "You can't carry\nany more items!"
        output, report = match_engine_catalog({exclamation: ""}, [good], semantic_anchors={key: anchor}, target_lang="fr")
        self.assertEqual(output[exclamation], "")
        self.assertEqual(report["details"][exclamation], "english_fallback")

    def test_rby_anchor_batch_fails_closed_on_duplicate_or_missing_qids(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = (
            "%s is out of\nuseable POKéMON!",
            "%s blacked\nout!",
        )
        anchors = load_semantic_anchors()
        items = align(parse_redblue(root, "fr"), target_lang="fr")
        for key in keys:
            qid = anchors[key]["qid"]
            row = next(item for item in items if item.qid == qid)
            output, report = match_engine_catalog(
                {key: ""}, items + [row], semantic_anchors=anchors, target_lang="fr",
            )
            self.assertEqual(output[key], "", key)
            self.assertEqual(report["details"][key], "semantic_unresolved", key)
            self.assertEqual(report["fallback_english"], 1, key)
            missing = json.loads(json.dumps(anchors))
            missing[key]["qid"] = "rb.missing.Anchor"
            output, report = match_engine_catalog(
                {key: ""}, items, semantic_anchors=missing, target_lang="fr",
            )
            self.assertEqual(output[key], "", key)
            self.assertEqual(report["details"][key], "semantic_unresolved", key)
            self.assertEqual(report["fallback_english"], 1, key)

    def test_rby_anchor_callsites_are_unique_and_contextually_eligible(self):
        checkout = Path(".cache/dependencies/gen1recomp")
        if not checkout.is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        # gen1recomp's v0.2.49 Gen 2 UI localization pass wrapped its own
        # hardcoded "USE" label in Strings.source() (ui/gen2/PackMenu.lua),
        # so the RBY key now has a real Gen 2 callsite too -- mixed, like
        # BATTLE ANIMATION, rather than an RBY exclusive.
        mixed_keys = {"BATTLE ANIMATION", "USE"}
        keys = {
            "USE": {"ui/BagMenu.lua", "ui/gen2/PackMenu.lua"},
            "You can't carry\nany more items.": {"ui/PlayerPC.lua", "ui/ShopMenu.lua"},
            "SEEN %3d  OWN %3d": {"ui/PokedexMenu.lua"},
            "%s is out of\nuseable POKéMON!": {"battle/BattleState.lua"},
            "%s blacked\nout!": {"battle/BattleState.lua", "world/OverworldController.lua"},
            "Diploma": {"ui/Diploma.lua"},
            "BATTLE ANIMATION": {"ui/OptionsMenu.lua", "import/LauncherSettings.lua"},
            "When you change a\nPOKéMON BOX, data\x0bwill be saved.\x0cIs that okay?": {"ui/BoxMenu.lua"},
        }
        catalog = classify_callsites(iter_callsites(checkout))
        for key, paths in keys.items():
            self.assertIn(key, catalog)
            row = catalog[key]
            self.assertEqual(row["eligibility"], "eligible", key)
            self.assertEqual(row["category"], "mixed" if key in mixed_keys else "rby", key)
            self.assertEqual({call["path"] for call in row["callsites"]}, paths, key)
            self.assertTrue(all("Strings(" in call["context"] or "Strings.source(" in call["context"]
                                 for call in row["callsites"]), key)

    def test_real_corpus_battle_charge_anchor_batch_all_languages(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = {
            "%s\nmade a whirlwind!": [
                "rb.text_3.ChargeMoveEffectText", "rb.text_3.MadeWhirlwindText",
            ],
            "%s\ntook in sunlight!": [
                "rb.text_3.ChargeMoveEffectText", "rb.text_3.TookInSunlightText",
            ],
            "%s\nlowered its head!": [
                "rb.text_3.ChargeMoveEffectText", "rb.text_3.LoweredItsHeadText",
            ],
            "%s\nflew up high!": [
                "rb.text_3.ChargeMoveEffectText", "rb.text_3.FlewUpHighText",
            ],
            "%s\ndug a hole!": [
                "rb.text_3.ChargeMoveEffectText", "rb.text_3.DugAHoleText",
            ],
            "%s\nis storing energy!": ["rb.text_2.SavingEnergyText"],
        }
        expected = {
            "fr": {
                "%s\nmade a whirlwind!": "%s\ncrée un cyclone!",
                "%s\ntook in sunlight!": "%s\nrayonne!",
                "%s\nlowered its head!": "%s\nprend du recul!",
                "%s\nflew up high!": "%s\ns'envole!",
                "%s\ndug a hole!": "%s\ncreuse un trou!",
                "%s\nis storing energy!": "%s\nse concentre!",
            },
            "de": {
                "%s\nmade a whirlwind!": "%s\nerz. WIRBELWIND!",
                "%s\ntook in sunlight!": "%s\nbadet im Licht!",
                "%s\nlowered its head!": "%s\nduckt sich!",
                "%s\nflew up high!": "%s\nfliegt empor!",
                "%s\ndug a hole!": "%s\ngräbt sich ein!",
                "%s\nis storing energy!": "%s\nsammelt Kräfte!",
            },
            "es": {
                "%s\nmade a whirlwind!": "¡%s\ncreó un remolino!",
                "%s\ntook in sunlight!": "¡%s\nrecogió luz-sol!",
                "%s\nlowered its head!": "¡%s\nbajó su cabeza!",
                "%s\nflew up high!": "¡%s\nvoló muy alto!",
                "%s\ndug a hole!": "¡%s\ncavó un hoyo!",
                "%s\nis storing energy!": "¡%s\nguarda energía!",
            },
            "it": {
                "%s\nmade a whirlwind!": "%s\ngenera TURBINE!",
                "%s\ntook in sunlight!": "%s\nassorbe la luce!",
                "%s\nlowered its head!": "%s\nabbassa la testa!",
                "%s\nflew up high!": "%s\nè volato in alto!",
                "%s\ndug a hole!": "%s\nscava una fossa!",
                "%s\nis storing energy!": "%s\naccumula energia!",
            },
            "ja-Hrkt": {
                "%s\nmade a whirlwind!": "%s\nの　まわりで\nくうきが　うずを　まく！",
                "%s\ntook in sunlight!": "%s\nは\nひかりを　きゅうしゅうした！",
                "%s\nlowered its head!": "%s\nは\nくびを　ひっこめた！",
                "%s\nflew up high!": "%s\nは\nそらたかく　とびあがった！",
                "%s\ndug a hole!": "%s\nは\nあなをほって　ちちゅうに　もぐった！",
                "%s\nis storing energy!": "%sは　がまんしている",
            },
        }
        anchors = load_semantic_anchors()
        for language in expected:
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], len(keys), language)
            self.assertEqual(report["auto_semantic"], len(keys), language)
            self.assertFalse(report["ambiguous"], language)
            for key, qids in keys.items():
                self.assertEqual(output[key], expected[language][key], (language, key))
                self.assertEqual(report["details"][key], "semantic", (language, key))
                provenance = report["provenance"][key]
                if len(qids) == 1:
                    self.assertEqual(provenance["qid"], qids[0], (language, key))
                else:
                    self.assertEqual(provenance["qids"], qids, (language, key))

    def test_real_corpus_stat_stage_anchor_batch_preserves_runtime_stat_label(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = (
            "%s's\n%s rose!", "%s's\n%s\ngreatly rose!",
            "%s's\n%s fell!", "%s's\n%s\ngreatly fell!",
        )
        qids = {
            keys[0]: ["rb.text_3.MonsStatsRoseText", "rb.text_3.RoseText"],
            keys[1]: ["rb.text_3.MonsStatsRoseText", "rb.text_3.GreatlyRoseText", "rb.text_3.RoseText"],
            keys[2]: ["rb.text_3.MonsStatsFellText", "rb.text_3.FellText"],
            keys[3]: ["rb.text_3.MonsStatsFellText", "rb.text_3.GreatlyFellText", "rb.text_3.FellText"],
        }
        expected = {
            "fr": {
                keys[0]: "%s\ngagne %s!",
                keys[1]: "%s\ngagne %s\nà fond!",
                keys[2]: "%s\nperd %s!",
                keys[3]: "%s\nperd %s\nà fond!",
            },
            "de": {
                keys[0]: "%ss\n%s nimmt zu!",
                # FellText's corpus row has a trailing space; preserve_edges
                # keeps it as an intentional boundary byte.
                keys[2]: "%ss\n%s sinkt! ",
            },
            "ja-Hrkt": {
                keys[0]: "%sの\n%sが　あがった！",
                keys[1]: "%sの\n%sが\nぐーんと　あがった！",
                keys[2]: "%sの\n%sが　さがった！",
                keys[3]: "%sの\n%sが\nがくっと　さがった！",
            },
        }
        resolved = {
            "fr": set(keys), "de": {keys[0], keys[2]},
            "es": set(), "it": set(), "ja-Hrkt": set(keys),
        }
        anchors = load_semantic_anchors()
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], len(resolved[language]), language)
            self.assertEqual(report["auto_semantic"], len(resolved[language]), language)
            for key in keys:
                if key in resolved[language]:
                    self.assertEqual(output[key], expected[language][key], (language, key))
                    self.assertEqual(report["details"][key], "semantic", (language, key))
                    self.assertEqual(report["provenance"][key]["qids"], qids[key], (language, key))
                    self.assertEqual(printf_directives(output[key]), ["%s", "%s"], (language, key))
                else:
                    self.assertEqual(output[key], "", (language, key))
                    self.assertEqual(report["details"][key], "semantic_unresolved", (language, key))
                    self.assertEqual(report["provenance"][key]["qids"], qids[key], (language, key))
            if language in {"fr", "de", "ja-Hrkt"}:
                rendered = output[keys[0]] % ("PIKACHU", "ATTACK")
                self.assertIn("ATTACK", rendered, language)

    def test_real_corpus_es_it_stat_stage_editorial_overrides_are_language_scoped(self):
        """Editorial stat strings use the real qid corpus only for the anchor set.

        The four ES/IT entries are deliberately not qid-derived: overrides win
        over semantic anchors and report editorial provenance without a qid.
        Other languages must continue through their normal semantic/fallback
        paths, proving that the per-language override files do not leak.
        """
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = (
            "%s's\n%s rose!", "%s's\n%s\ngreatly rose!",
            "%s's\n%s fell!", "%s's\n%s\ngreatly fell!",
        )
        expected = {
            "es": {
                keys[0]: "¡%s\nsu %s subió!",
                keys[1]: "¡%s\nsu %s\nsubió mucho!",
                keys[2]: "¡%s\nsu %s bajó!",
                keys[3]: "¡%s\nsu %s\nbajó mucho!",
            },
            "it": {
                keys[0]: "%s\n%s sale!",
                keys[1]: "%s\n%s\nsale molto!",
                keys[2]: "%s\n%s cala!",
                keys[3]: "%s\n%s\ncala molto!",
            },
        }
        anchors = load_semantic_anchors()
        for language in ("es", "it"):
            items = align(parse_redblue(root, language), target_lang=language)
            override_path = Path("overrides") / language / "rby" / "engine.json"
            overrides = load_engine_overrides(override_path)
            self.assertTrue(set(keys) <= set(overrides), language)
            self.assertTrue(all("provenance" in overrides[key] for key in keys), language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items, overrides,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], 4, language)
            self.assertEqual(report["override"], 4, language)
            for key in keys:
                self.assertEqual(output[key], expected[language][key], (language, key))
                self.assertEqual(report["details"][key], "override", (language, key))
                provenance = report["provenance"][key]
                self.assertEqual(provenance, {"method": "override", "target_lang": language})
                self.assertNotIn("qid", provenance)
                self.assertEqual(printf_directives(output[key]), ["%s", "%s"], (language, key))
                self.assertEqual(output[key] % ("PIKACHU", "ATTACK"),
                                 expected[language][key] % ("PIKACHU", "ATTACK"), (language, key))
            # Ensure the actual language package scaffold contains the exact
            # source keys consumed by generation, including their line breaks.
            scaffold = Path(".cache/build") / language / "mod-worksheet" / "strings.lua"
            if not scaffold.is_file():
                self.skipTest(f"cached {language} engine scaffold is unavailable")
            catalog = read_engine_catalog(scaffold)
            self.assertTrue(set(keys) <= set(catalog), language)

        # FR/DE/JA do not load ES/IT override files. Their proven semantic paths
        # therefore remain untouched and never report editorial overrides.
        for language in ("fr", "de", "ja-Hrkt"):
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {key: "" for key in keys}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["override"], 0, language)
            self.assertTrue(all(report["details"][key] != "override" for key in keys), language)

    def test_stat_stage_composite_anchor_fails_closed_on_duplicate_or_missing_qid(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        key = "%s's\n%s rose!"
        anchors = load_semantic_anchors()
        items = align(parse_redblue(root, "fr"), target_lang="fr")
        first_qid = anchors[key]["parts"][0]["qid"]
        duplicate = next(item for item in items if item.qid == first_qid)
        output, report = match_engine_catalog(
            {key: ""}, items + [duplicate], semantic_anchors=anchors, target_lang="fr",
        )
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)

        missing = json.loads(json.dumps(anchors))
        missing[key]["parts"][0]["qid"] = "rb.text_3.MissingMonsStatsRoseText"
        output, report = match_engine_catalog(
            {key: ""}, items, semantic_anchors=missing, target_lang="fr",
        )
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")
        self.assertEqual(report["fallback_english"], 1)

    def test_battle_charge_anchor_batch_fails_closed_on_duplicate_qids(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        keys = (
            "%s\nmade a whirlwind!", "%s\ntook in sunlight!",
            "%s\nlowered its head!", "%s\nflew up high!",
            "%s\ndug a hole!", "%s\nis storing energy!",
        )
        anchors = load_semantic_anchors()
        items = align(parse_redblue(root, "fr"), target_lang="fr")
        for key in keys:
            qids = anchors[key].get("parts") or [anchors[key]]
            qid = qids[0]["qid"] if isinstance(qids[0], dict) else qids[0]
            row = next(item for item in items if item.qid == qid)
            output, report = match_engine_catalog(
                {key: ""}, items + [row], semantic_anchors=anchors, target_lang="fr",
            )
            self.assertEqual(output[key], "", key)
            self.assertEqual(report["details"][key], "semantic_unresolved", key)
            self.assertEqual(report["fallback_english"], 1, key)

    def test_real_corpus_normal_learned_key_is_a_semantic_anchor(self):
        root = Path(".cache/dependencies/poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        anchors = load_semantic_anchors()
        ordinary = "%s learned\n%s!"
        expected = {
            "fr": "%s\napprend...\x0b%s!",
            "de": "%s lernt\n%s!",
            "es": "¡%s\naprendió\x0b%s!",
            "it": "%s impara\n%s!",
            "ja-Hrkt": "%sは　あたらしく\n%sを　おぼえた！",
        }
        self.assertEqual(
            anchors[ordinary],
            {"qid": "rb.text_4.LearnedMove1Text", "extraction": {"kind": "full", "index": 0}},
        )
        for language, language_expected in expected.items():
            items = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog(
                {ordinary: ""}, items,
                semantic_anchors=anchors, target_lang=language,
            )
            self.assertEqual(report["translated"], 1, language)
            self.assertEqual(report["auto_semantic"], 1, language)
            self.assertFalse(report["ambiguous"], language)
            self.assertEqual(output[ordinary], language_expected, language)
            self.assertEqual(printf_directives(output[ordinary]), ["%s", "%s"], language)
            self.assertEqual(report["details"][ordinary], "semantic", language)
            self.assertEqual(report["provenance"][ordinary]["qid"], "rb.text_4.LearnedMove1Text", language)

        # A repeated qid is not proof of a unique source/translation pair;
        # semantic resolution must fail closed instead of selecting a row.
        items = align(parse_redblue(root, "fr"), target_lang="fr")
        normal_row = next(item for item in items if item.qid == anchors[ordinary]["qid"])
        output, report = match_engine_catalog(
            {ordinary: ""}, items + [normal_row],
            semantic_anchors=anchors, target_lang="fr",
        )
        self.assertEqual(output[ordinary], "")
        self.assertEqual(report["details"][ordinary], "semantic_unresolved")
        self.assertEqual(report["provenance"][ordinary]["qid"], "rb.text_4.LearnedMove1Text")
        self.assertEqual(report["fallback_english"], 1)

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

    def test_semantic_anchor_accepts_explicit_corpus_punctuation_alias(self):
        row = Alignment(
            "rb.text_6.FluteWokeUpText", "both",
            CorpusRecord("rb.text_6.FluteWokeUpText", "en", "{text_start}All sleeping<LINE>#MON woke up.<PROMPT>"),
            CorpusRecord("rb.text_6.FluteWokeUpText", "fr", "{text_start}Tous les #MON<LINE>endormis se<CONT>réveillent.<PROMPT>"),
            "qid", target_lang="fr",
        )
        key = "All sleeping\nPOKéMON woke up!"
        output, report = match_engine_catalog(
            {key: ""}, [row], semantic_anchors={key: {
                "qid": row.qid,
                "source_aliases": ["All sleeping\nPOKéMON woke up."],
                "extraction": {"kind": "full"},
            }}, target_lang="fr",
        )
        self.assertEqual(output[key], "Tous les POKéMON\nendormis se\x0bréveillent.")
        self.assertEqual(report["details"][key], "semantic")

    def test_semantic_anchor_rejects_unlisted_corpus_spelling(self):
        row = Alignment(
            "rb.text_6.FluteWokeUpText", "both",
            CorpusRecord("rb.text_6.FluteWokeUpText", "en", "{text_start}A different message.<PROMPT>"),
            CorpusRecord("rb.text_6.FluteWokeUpText", "fr", "{text_start}Un autre message.<PROMPT>"),
            "qid", target_lang="fr",
        )
        key = "All sleeping\nPOKéMON woke up!"
        output, report = match_engine_catalog(
            {key: ""}, [row], semantic_anchors={key: {
                "qid": row.qid,
                "source_aliases": ["All sleeping\nPOKéMON woke up."],
                "extraction": {"kind": "full"},
            }}, target_lang="fr",
        )
        self.assertEqual(output[key], "")
        self.assertEqual(report["details"][key], "semantic_unresolved")

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
            self.assertEqual(manifest["description"], f"{target_name}, based mostly on PokeCorpus.")
            default_mod = generate_mod([row], Path(tmp) / "default")
            default_manifest = json.loads((default_mod / "manifest.json").read_text(encoding="utf-8"))
            described_mod = generate_mod(
                [row], Path(tmp) / "described", target_description="Use the supplied description."
            )
            described_manifest = json.loads((described_mod / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(default_manifest["name"], "fr translation for Red, Blue and Yellow")
        self.assertEqual(default_manifest["description"], "fr translation for Red, Blue and Yellow, based mostly on PokeCorpus.")
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
            self.assertEqual(manifests[language]["name"], f"{code} translation for Red, Blue and Yellow")
            self.assertEqual(manifests[language]["description"], f"{code} translation for Red, Blue and Yellow, based mostly on PokeCorpus.")
            self.assertEqual(manifests[language]["priority"], 100)


if __name__ == "__main__":
    unittest.main()
