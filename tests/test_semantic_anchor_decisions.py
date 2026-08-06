import json
import shutil
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from pipeline.engine import (
    corpus_to_engine,
    load_semantic_anchor_decisions,
    load_semantic_anchors,
    match_engine_catalog,
    merge_semantic_anchors,
)
from pipeline.align import align
from pipeline.corpus import parse_redblue
from pipeline.model import Alignment, CorpusRecord


ROOT = Path(__file__).resolve().parents[1]


class SemanticAnchorDecisionTests(unittest.TestCase):
    def test_checked_in_assets_load_and_merge_without_overlap(self):
        deterministic = load_semantic_anchors(ROOT / "config/semantic_anchors.json")
        decisions = load_semantic_anchor_decisions(ROOT / "config/semantic_anchor_decisions.json")
        self.assertTrue(set(deterministic).isdisjoint(decisions))
        merged, provenance = merge_semantic_anchors(deterministic, decisions)
        self.assertEqual(set(merged), set(deterministic) | set(decisions))
        self.assertEqual(set(provenance), set(decisions))

    def test_remaining_rby_candidates_resolve_across_target_languages(self):
        qids = {
            "item_use_1": ("rb.text_7.ItemUseText001", "{text_start}<PLAYER> used@@"),
            "pokeball": ("rb.names.ItemNames.4", "POKé BALL@"),
            "item_use_2": ("rb.text_7.ItemUseText002", "{text_ram wStringBuffer}{text_start}!<DONE>"),
            "able": ("rb.party_menu.RedrawPartyMenu_.ableToLearnMoveText", "ABLE@"),
            "box_no": ("rb.bills_pc.BoxNoPCText", "BOX No.@"),
            "not_able": ("rb.party_menu.RedrawPartyMenu_.notAbleToLearnMoveText", "NOT ABLE@"),
            "player_mon": ("rb.text_2.PlayerMon1Text", "{text_ram wBattleMonNick}{text_start}!<DONE>"),
            "cancel": ("rb.list_menu.ListMenuCancelText", "CANCEL@"),
            "mist": ("rb.text_3.ShroudedInMistText", "{text_start}<USER>'s<LINE>shrouded in mist!<PROMPT>"),
            "pc_menu": ("rb.bills_pc.BillsPCMenuText", "WITHDRAW <PKMN><NEXT>DEPOSIT <PKMN><NEXT>RELEASE <PKMN><NEXT>CHANGE BOX<NEXT>SEE YA!@"),
            "carry": ("rb.text_2.CantCarryMoreText", "{text_start}You can't carry<LINE>any more items.<PROMPT>"),
            "box_full": ("rb.text_5.BoxIsFullText", "{text_start}There's no more<LINE>room for #MON!<PARA>The #MON BOX<LINE>is full and can't<CONT>accept any more!<PARA>Change the BOX at<LINE>a #MON CENTER!<DONE>"),
        }
        translations = {
            "fr": ["{text_start}<PLAYER> utilise:@@", "POKé BALL@", "{text_ram wStringBuffer}{text_start}!<DONE>", "APTE@", "BOITE@", "PAS APTE@", "{text_ram wBattleMonNick}{text_start}!<DONE>", "RETOUR@", "{text_start}<USER><LINE>s'estompe dans la<CONT>brume!<PROMPT>", "RETIRER <PKMN><NEXT>STOCKER <PKMN><NEXT>RELACHER <PKMN><NEXT>CHANGER BOITE<NEXT>SALUT!@", "{text_start}Votre inventaire<LINE>est plein.<PROMPT>", "{text_start}Plus de place<LINE>pour un #MON!<PARA>La BOITE #MON<LINE>est pleine!<PARA>Changez de BOITE<LINE>dans un CENTRE<CONT>#MON!<DONE>"],
            "de": ["{text_start}<PLAYER> setzt@@", "POKéBALL@", "{text_ram wStringBuffer}{text_start} ein!<DONE>", "OK@", "BOX Nr.@", "NEIN@", "{text_ram wBattleMonNick}{text_start}!<DONE>", "ZURÜCK@", "{text_start}<USER><LINE>ist eingenebelt!<PROMPT>", "<PKMN> MITNEHMEN<NEXT><PKMN> ABLEGEN<NEXT><PKMN> FREILASSEN<NEXT>BOX WECHSELN<NEXT>TSCHÜSS!@", "{text_start}Du kannst keine<LINE>weiteren Items<CONT>tragen.<PROMPT>", "{text_start}Es ist kein Platz<LINE>für das #MON!<PARA>Die #MON-BOX<LINE>ist voll und kann<CONT>keine weiteren<CONT>#MON<CONT>aufnehmen!<PARA>Wechsle in einem<LINE>#MON-CENTER<CONT>die BOX!<DONE>"],
            "es": ["{text_start}¡<PLAYER> utilizó@@", "POKé BALL@", "{text_ram wStringBuffer}{text_start}!<DONE>", "PUEDE@", "CAJA Nº@", "NO PUEDE@", "{text_ram wBattleMonNick}{text_start}!<DONE>", "SALIR@", "{text_start}¡<USER><LINE>padece neblina!<PROMPT>", "SACAR <PKMN><NEXT>DEJAR <PKMN><NEXT>SOLTAR <PKMN><NEXT>CAMBIA CAJA<NEXT>¡NOS VEMOS!@", "{text_start}No puedes llevar<LINE>más objetos.<PROMPT>", "{text_start}¡No tienes sitio<LINE>para más #MON!<PARA>¡La CAJA #MON<LINE>está llena y no<CONT>caben más!<PARA>¡Cambia la CAJA<LINE>en un CENTRO<CONT>#MON!<DONE>"],
            "it": ["{text_start}<PLAYER> usa@@", "POKé BALL@", "{text_ram wStringBuffer}{text_start}!<DONE>", "CAPACE@", "BOX Nº@", "INCAPACE@", "{text_ram wBattleMonNick}{text_start}!<DONE>", "ESCI@", "{text_start}La nebbia avvolge<LINE><USER><PROMPT>", "RITIRA <PKMN><NEXT>DEPOSITA <PKMN><NEXT>LIBERA <PKMN><NEXT>CAMBIA BOX<NEXT>CIAO!@", "{text_start}Non puoi portare<LINE>altri strumenti.<PROMPT>", "{text_start}Non c'è spazio per<LINE>altri #MON!<PARA>Il #MON BOX è<LINE>pieno e non ne<CONT>accetta più!<PARA>Cambia il BOX al<LINE>CENTRO #MON!<DONE>"],
            "ja-Hrkt": ["{text_start}<PLAYER>は@", "モンスターボール@", "{text_ram wStringBuffer}{text_start}を　つかった！<DONE>", "おぼえられる@", "いまのボックス@", "おぼえられない@", "{text_ram wBattleMonNick}{text_start}！<DONE>", "やめる@", "{text_start}<USER>は<LINE>しろい　きりに　つつまれた！<PROMPT>", "#を　つれていく<NEXT>#を　あずける<NEXT>#を　にがす<NEXT>ボックスを　かえる<NEXT>さようなら@", "{text_start}どうぐが　いっぱいです<LINE>もう　もてません！<PROMPT>", "{text_start}#を　もちきれません！<PARA>ボックスも　いっぱいで<LINE>てんそうできません！<PARA>#センターなどで<LINE>ボックスを　かえてきて　ください<DONE>"],
        }
        anchors, _ = merge_semantic_anchors(
            load_semantic_anchors(ROOT / "config/semantic_anchors.json"),
            load_semantic_anchor_decisions(),
        )
        keys = ("%s used\nPOKé BALL!", "ABLE", "BOX No.%d", "NOT ABLE", "%s!", "CANCEL", "%s's\nprotected against\nstat changes!", "DEPOSIT <PK><MN>", "RELEASE <PK><MN>", "WITHDRAW <PK><MN>", "You can't carry\nany more items!", "But every BOX\nis full!")
        for language, values in translations.items():
            rows = [Alignment(qids[name][0], "both", CorpusRecord(qids[name][0], "en", qids[name][1]), CorpusRecord(qids[name][0], language, value), "qid") for name, value in zip(qids, values)]
            output, report = match_engine_catalog({key: "" for key in keys}, rows, semantic_anchors=anchors, target_lang=language)
            item_use = corpus_to_engine(values[0] + "\n" + values[2]).replace("{PLAYER}", "%s").replace("{RAM:wStringBuffer}", corpus_to_engine(values[1]).strip())
            self.assertEqual(output["%s used\nPOKé BALL!"], item_use, language)
            self.assertEqual(output["BOX No.%d"], corpus_to_engine(values[4]).strip() + "%d", language)
            self.assertEqual(output["%s!"], corpus_to_engine(values[6]).replace("{RAM:wBattleMonNick}", "%s"), language)
            self.assertEqual(output["DEPOSIT <PK><MN>"], corpus_to_engine(values[9]).split("\n")[1], language)
            self.assertEqual(output["RELEASE <PK><MN>"], corpus_to_engine(values[9]).split("\n")[2], language)
            self.assertEqual(output["WITHDRAW <PK><MN>"], corpus_to_engine(values[9]).split("\n")[0], language)
            self.assertEqual(output["%s's\nprotected against\nstat changes!"], corpus_to_engine(values[8]).replace("{USER}", "%s"), language)
            self.assertEqual(output["You can't carry\nany more items!"], corpus_to_engine(values[10]).strip(), language)
            self.assertEqual(output["But every BOX\nis full!"], corpus_to_engine(values[11]).strip(), language)
            self.assertTrue(all(report["provenance"][key]["method"] == "semantic" for key in keys), language)

    def test_printf_parts_reconstruct_label_and_connector_keys_multilingually(self):
        anchors = load_semantic_anchors()
        labels = {
            "fr": ("NOM/", "DO/", "CONTRE"),
            "de": ("NAME/", "OT/", "VS"),
            "es": ("NOM./", "EO/", "VS"),
            "it": ("NOME/", "AO/", "CONTRO"),
            "ja-Hrkt": ("なまえ／", "おや／", "ＶＳ"),
        }
        qids = (
            "rb.start_sub_menus.TrainerInfo_NameMoneyTimeText",
            "rb.trade2.Trade_MonInfoText",
            "rb.link_battle_versus_text.DisplayLinkBattleVersusTextBox",
        )
        english = (
            "NAME/<NEXT>MONEY/<NEXT>TIME/@",
            "──№<DOT><NEXT><NEXT>OT/<NEXT><ID>№<DOT>@",
            "ＶＳ",
        )
        for language, (name, ot, connector) in labels.items():
            rows = [
                Alignment(qid, "both", CorpusRecord(qid, "en", source), CorpusRecord(qid, language, target), "qid")
                for qid, source, target in zip(qids, english, (name.replace("/", "") if language == "ja-Hrkt" else name,
                                                               "──№<DOT><NEXT><NEXT>" + ot + "<NEXT><ID>№<DOT>@",
                                                               connector))
            ]
            output, report = match_engine_catalog(
                {"NAME/%s": "", "OT/%s": "", "%s vs %s!": ""},
                rows,
                semantic_anchors=anchors,
                target_lang=language,
            )
            self.assertEqual(output["NAME/%s"], name + "%s", language)
            self.assertEqual(output["OT/%s"], ot + "%s", language)
            self.assertEqual(output["%s vs %s!"], "%s " + connector + " %s!", language)
            self.assertTrue(all(report["provenance"][key]["decision_type"] == "composition"
                                for key in ("NAME/%s", "OT/%s", "%s vs %s!")), language)

    def test_printf_part_schema_rejects_bad_shape_duplicate_and_index(self):
        base = {
            "X": {
                "parts": [{"printf": 0}],
                "placeholders": {},
            }
        }
        with self.assertRaises(ValueError):
            load_semantic_anchors(base)
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"parts": [{"printf": 0, "qid": "q"}], "placeholders": {}}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"parts": [{"printf": -1}], "placeholders": {}}})
        with self.assertRaises(ValueError):
            load_semantic_anchors({"X": {"parts": [{"printf": 0}, {"printf": 0}], "placeholders": {}}})

    def test_schema_validation_and_conflict_rejection(self):
        decision = {
            "anchor": {"qid": "q", "extraction": {"kind": "full"}},
            "decision_type": "contextual",
            "rationale": "known limitation",
            "languages": [],
            "languages_verified": False,
            "qids": ["q"],
            "trace_status": "known-limitation",
        }
        wrapped = {
            "schema": "gen1recomp-translation-mods/semantic-anchor-decisions",
            "version": 1,
            "decisions": {"X": decision},
        }
        self.assertIn("X", load_semantic_anchor_decisions(wrapped))
        decision["trace_status"] = "unknown"
        with self.assertRaises(ValueError):
            load_semantic_anchor_decisions(wrapped)
        with self.assertRaises(ValueError):
            load_semantic_anchor_decisions({"X": {"anchor": {"qid": "q", "extraction": {"kind": "full"}}, "decision_type": "x", "rationale": "ok", "languages": ["xx"]}})
        with self.assertRaises(ValueError):
            merge_semantic_anchors({"X": {"qid": "q", "extraction": {"kind": "full"}}}, {"X": {"anchor": {"qid": "q2", "extraction": {"kind": "full"}}}})

    def test_missing_corrupt_and_wrong_shape_decision_files_fail_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            with self.assertRaises(FileNotFoundError):
                load_semantic_anchor_decisions(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)
            path.write_text(json.dumps({"schema": "wrong", "version": 1, "decisions": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)
            path.write_text(json.dumps({"schema": "gen1recomp-translation-mods/semantic-anchor-decisions", "version": 1, "decisions": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)

    def test_catalog_provenance_survives_explicit_match_call(self):
        catalog = load_semantic_anchors()
        qid = "rb.pokedex.PokedexMenuItemsText"
        rows = [Alignment(qid, "both", CorpusRecord(qid, "en", "DATA\nCRY\nAREA\nQUIT"), CorpusRecord(qid, "fr", "DON\nCRI\nZONE\nQUITTER"), "qid")]
        output, report = match_engine_catalog({"QUIT": ""}, rows, semantic_anchors=catalog, target_lang="fr")
        self.assertEqual(output["QUIT"], "QUITTER")
        self.assertEqual(report["decision_provenance"]["QUIT"]["trace_status"], "known-limitation")

    def test_diploma_wrapper_anchor_strips_only_verified_escape(self):
        root = Path("../poke-corpus/corpus/RedBlue")
        if not (root / "qid_msg.txt").is_file():
            self.skipTest("canonical local poke-corpus checkout is unavailable")
        deterministic = load_semantic_anchors(ROOT / "config/semantic_anchors.json")
        decisions = load_semantic_anchor_decisions(ROOT / "config/semantic_anchor_decisions.json")
        anchors, _ = merge_semantic_anchors(deterministic, decisions)
        expected = {"fr": "Diplôme", "de": "Diplom", "es": "Diploma", "it": "Diploma", "ja-Hrkt": "しょうじょう"}
        for language, value in expected.items():
            rows = align(parse_redblue(root, language), target_lang=language)
            output, report = match_engine_catalog({"<Diploma>": ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output["<Diploma>"], value, language)
            self.assertEqual(report["details"]["<Diploma>"], "semantic", language)
            self.assertEqual(report["provenance"]["<Diploma>"]["qid"], "rb.diploma.DiplomaText", language)
            self.assertEqual(report["provenance"]["<Diploma>"]["extraction"]["wrapper"], {"prefix": "\\x70", "suffix": "\\x70"}, language)

    def test_diploma_wrapper_anchor_positive_without_external_corpus(self):
        qid = "rb.diploma.DiplomaText"
        rows = [Alignment(qid, "both", CorpusRecord(qid, "en", r"\x70Diploma\x70@"), CorpusRecord(qid, "fr", r"\x70Diplôme\x70@"), "qid")]
        anchor = {
            "<Diploma>": {
                "qid": qid,
                "source_aliases": ["Diploma"],
                "extraction": {"kind": "full", "wrapper": {"prefix": r"\x70", "suffix": r"\x70"}},
            }
        }
        output, report = match_engine_catalog({"<Diploma>": ""}, rows, semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(output["<Diploma>"], "Diplôme")
        self.assertEqual(report["details"]["<Diploma>"], "semantic")

    def test_diploma_wrapper_anchor_rejects_unexpected_escape(self):
        qid = "rb.diploma.DiplomaText"
        rows = [Alignment(qid, "both", CorpusRecord(qid, "en", r"\x71Diploma\x71@"), CorpusRecord(qid, "fr", r"\x71Diplôme\x71@"), "qid")]
        anchor = {
            "<Diploma>": {
                "qid": qid,
                "source_aliases": ["Diploma"],
                "extraction": {"kind": "full", "wrapper": {"prefix": r"\x70", "suffix": r"\x70"}},
            }
        }
        output, report = match_engine_catalog({"<Diploma>": ""}, rows, semantic_anchors=anchor, target_lang="fr")
        self.assertEqual(output["<Diploma>"], "")
        self.assertEqual(report["details"]["<Diploma>"], "semantic_unresolved")

    def test_ghost_appearance_composition_uses_encounter_name(self):
        key = "The GHOST\nappeared!"
        enemy_qid = "rb.text_2.EnemyAppearedText"
        ghost_qid = "rb.core.InitWildBattle.isGhost"
        translations = {
            "fr": ("Un {RAM:wEnemyMonNick}\napparaît!", "SPECTRE", "Un SPECTRE\napparaît!"),
            "de": ("{RAM:wEnemyMonNick}\ntaucht auf!", "GEIST", "GEIST\ntaucht auf!"),
            "es": ("¡{RAM:wEnemyMonNick}\napareció!", "GHOST", "¡GHOST\napareció!"),
            "it": ("Appare\n{RAM:wEnemyMonNick}!", "SPETTRO", "Appare\nSPETTRO!"),
            "ja-Hrkt": ("{RAM:wEnemyMonNick}が　\nあらわれた！", "ゆうれい", "ゆうれいが　\nあらわれた！"),
        }
        anchors, _ = merge_semantic_anchors(
            load_semantic_anchors(ROOT / "config/semantic_anchors.json"),
            load_semantic_anchor_decisions(ROOT / "config/semantic_anchor_decisions.json"),
        )
        for language, (sentence, ghost, expected) in translations.items():
            rows = [
                Alignment(enemy_qid, "both", CorpusRecord(enemy_qid, "en", "{RAM:wEnemyMonNick}\nappeared!"), CorpusRecord(enemy_qid, language, sentence), "qid"),
                Alignment(ghost_qid, "both", CorpusRecord(ghost_qid, "en", "GHOST"), CorpusRecord(ghost_qid, language, ghost), "qid"),
            ]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], expected, language)
            self.assertEqual(report["provenance"][key]["decision_type"], "composition", language)

    def test_move_learning_message_is_split_by_language(self):
        qid = "rb.text_4.TryingToLearnText"
        keys = (
            "%s is\ntrying to learn\v%s!\fBut, %s\ncan't learn more\vthan 4 moves!\f",
            "Delete an older\nmove to make room\vfor %s?",
        )
        english = "{RAM:wLearnMoveMonName} is\ntrying to learn\v{RAM:wStringBuffer}!\fBut, {RAM:wLearnMoveMonName}\ncan't learn more\vthan 4 moves!\fDelete an older\nmove to make room\vfor {RAM:wStringBuffer}?"
        translations = {
            "fr": "{RAM:wLearnMoveMonName} essaie\nd'apprendre\v{RAM:wStringBuffer}!\fMais {RAM:wLearnMoveMonName}\nne peut plus rien\vapprendre!\fOublier une\nattaque pour\vapprendre\v{RAM:wStringBuffer}?",
            "de": "{RAM:wLearnMoveMonName}\nversucht,\v{RAM:wStringBuffer} zu\verlernen!\fAber {RAM:wLearnMoveMonName}\nkann nicht mehr\vals vier Attacken\verlernen!\fSoll eine andere\nAttacke zugunsten\vvon {RAM:wStringBuffer}\vvergessen werden?",
            "es": "¡{RAM:wLearnMoveMonName}\nintenta aprender\v{RAM:wStringBuffer}!\f¡Pero {RAM:wLearnMoveMonName}\nno puede aprender\vmás de 4 ataques!\f¿Borrar un ataque\nanterior para\vhacer sitio a\v{RAM:wStringBuffer}?",
            "it": "{RAM:wLearnMoveMonName}\ncerca di imparare\v{RAM:wStringBuffer}!\fMa {RAM:wLearnMoveMonName} non\npuò imparare più\vdi 4 mosse!\fEliminare una\nvecchia mossa\vper far spazio a\v{RAM:wStringBuffer}?",
            "ja-Hrkt": "{RAM:wLearnMoveMonName}は　あたらしく\n{RAM:wStringBuffer}を　おぼえたい……！\fしかし　{RAM:wLearnMoveMonName}は　わざを　４つ\nおぼえるので　せいいっぱいだ！\f{RAM:wStringBuffer}の　かわりに\nほかの　わざを　わすれさせますか？",
        }
        anchors = load_semantic_anchors()
        for language, translation in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", english), CorpusRecord(qid, language, translation), "qid")]
            output, report = match_engine_catalog({key: "" for key in keys}, rows, semantic_anchors=anchors, target_lang=language)
            expected = corpus_to_engine(translation).replace("{RAM:wLearnMoveMonName}", "%s").replace("{RAM:wStringBuffer}", "%s")
            self.assertTrue(output[keys[0]].endswith("\f"), language)
            self.assertEqual(output[keys[0]] + output[keys[1]], expected, language)
            self.assertTrue(all(report["provenance"][key]["decision_type"] == "target_extraction" for key in keys), language)

    def test_trade_cancellation_restores_complete_rom_message(self):
        key = "The trade was\ncancelled."
        qid = "rb.cable_club.TradeCanceled"
        translations = {
            "fr": "Dommage! L'échange\vest annulé!",
            "de": "Schade! Der tausch\vwurde abgebrochen!",
            "es": "¡Mal! ¡El trato\vestá cancelado!",
            "it": "PECCATO! SCAMBIO\vANNULLATO!",
            "ja-Hrkt": "ざんねんながら\vこうかんは　キャンセルされました",
        }
        anchors = load_semantic_anchors()
        for language, translation in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", "Too bad! The trade\vwas canceled!"), CorpusRecord(qid, language, translation), "qid")]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], translation, language)
            self.assertEqual(report["provenance"][key]["decision_type"], "source_alias", language)

    def test_last_party_member_guard_uses_deposit_message(self):
        key = "You need at least\none POKéMON!"
        qid = "rb.text_2.CantDepositLastMonText"
        translations = {
            "fr": "Vous ne pouvez\nstocker votre\vdernier #MON!",
            "de": "Du kannst Dein\nletztes #MON\vnicht lagern!",
            "es": "¡No puedes\nguardar el\vúltimo #MON!",
            "it": "Non depositare\nl'ultimo #MON!",
            "ja-Hrkt": "それ　あずけたら\nこまるん　ちゃう？",
        }
        anchors = load_semantic_anchors()
        english = "You can't deposit\nthe last #MON!"
        for language, translation in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", english), CorpusRecord(qid, language, translation), "qid")]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], corpus_to_engine(translation), language)
            self.assertEqual(report["provenance"][key]["decision_type"], "contextual", language)

    def test_toss_confirmation_preserves_selected_item(self):
        key = "Toss %s?"
        qid = "rb.text_7.IsItOKToTossItemText"
        translations = {
            "fr": "Jeter:\n{RAM:wStringBuffer}, OK?",
            "de": "Willst Du\n{RAM:wStringBuffer}\vwegwerfen?",
            "es": "¿Puedo tirar\n{RAM:wStringBuffer}?",
            "it": "Buttare via\n{RAM:wStringBuffer}?",
            "ja-Hrkt": "{RAM:wStringBuffer}を　すてます\nほんとに　よろしいですか？",
        }
        anchors = load_semantic_anchors()
        english = "Is it OK to toss\n{RAM:wStringBuffer}?"
        for language, translation in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", english), CorpusRecord(qid, language, translation), "qid")]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], corpus_to_engine(translation).replace("{RAM:wStringBuffer}", "%s"), language)
            self.assertEqual(report["provenance"][key]["decision_type"], "contextual", language)

    def test_bicycle_message_composes_localized_fragments(self):
        key = "%s got on\nthe BICYCLE!"
        qids = ("rb.text_7.GotOnBicycleText1", "rb.text_7.GotOnBicycleText2", "rb.names.ItemNames.6")
        english = ("{PLAYER} got on the", "{RAM:wStringBuffer}!", "BICYCLE")
        translations = {
            "fr": ("{PLAYER} monte sur", "{RAM:wStringBuffer}!", "BICYCLETTE"),
            "de": ("{PLAYER} steigt", "auf das {RAM:wStringBuffer}!", "FAHRRAD"),
            "es": ("¡{PLAYER} subió en", "la {RAM:wStringBuffer}!", "BICICLETA"),
            "it": ("{PLAYER} sale sulla", "{RAM:wStringBuffer}!", "BICICLETTA"),
            "ja-Hrkt": ("{PLAYER}は", "{RAM:wStringBuffer}に　のった", "じてんしゃ"),
        }
        anchors = load_semantic_anchors()
        for language, target in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", source), CorpusRecord(qid, language, value), "qid") for qid, source, value in zip(qids, english, target)]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            expected = corpus_to_engine(f"{target[0]}\n{target[1]}").replace("{PLAYER}", "%s").replace("{RAM:wStringBuffer}", target[2])
            self.assertEqual(output[key], expected, language)
            self.assertEqual(report["provenance"][key]["decision_type"], "composition", language)

    def test_evolution_messages_compose_original_flow(self):
        short_key = "Congratulations!\nYour %s\nevolved into\n%s!"
        full_key = "What?\n%s is\nevolving!\fCongratulations!\nYour %s\nevolved into\n%s!"
        qids = ("rb.text_3.IsEvolvingText", "rb.text_3.EvolvedText", "rb.text_3.IntoText")
        english = ("What? {RAM:wStringBuffer}\nis evolving!", "{RAM:wStringBuffer} evolved", "\ninto {RAM:wNameBuffer}!")
        translations = {
            "fr": ("Hein? {RAM:wStringBuffer}\névolue!", "{RAM:wStringBuffer} évolue", "\nen {RAM:wNameBuffer}!"),
            "de": ("Hey! {RAM:wStringBuffer}\nentwickelt sich!", "{RAM:wStringBuffer} wurde", "\nzu {RAM:wNameBuffer}!"),
            "es": ("¡{RAM:wStringBuffer} está\nevolucionando!", "¡{RAM:wStringBuffer} evol.", "\nen {RAM:wNameBuffer}!"),
            "it": ("Ehi! {RAM:wStringBuffer}\nsi evolve!", "{RAM:wStringBuffer} diventa", "\n{RAM:wNameBuffer}!"),
            "ja-Hrkt": ("<……>　おや！？\n{RAM:wStringBuffer}の　ようすが<……>！", "おめでとう！　{RAM:wStringBuffer}は", "\n{RAM:wNameBuffer}に　しんかした"),
        }
        anchors = load_semantic_anchors()
        for language, target in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", source), CorpusRecord(qid, language, value), "qid") for qid, source, value in zip(qids, english, target)]
            output, report = match_engine_catalog({short_key: "", full_key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            short = corpus_to_engine(target[1] + target[2]).replace("{RAM:wStringBuffer}", "%s").replace("{RAM:wNameBuffer}", "%s")
            intro = corpus_to_engine(target[0]).replace("{RAM:wStringBuffer}", "%s")
            self.assertEqual(output[short_key], short, language)
            self.assertEqual(output[full_key], intro + "\f" + short, language)
            self.assertTrue(all(report["provenance"][key]["decision_type"] == "composition" for key in (short_key, full_key)), language)

    def test_safari_exhaustion_restores_both_rom_messages(self):
        key = "PA: You're out of\nSAFARI BALLs!\nGame over!"
        qids = ("rb.text_2.OutOfSafariBallsText", "rb.text_2.GameOverText")
        english = ("PA: Ding-dong!\fYou are out of\nSAFARI BALLs!", "PA: Your SAFARI\nGAME is over!")
        translations = {
            "fr": ("Haut-parleur: Hé!\fVous n'avez plus\nde SAFARI BALL!", "Haut-parleur:\nLe SAFARI est\vterminé!"),
            "de": ("DURCHSAGE: Gong!\fDu hast keine\nSAFARIBÄLLE mehr!", "DURCHSAGE: Deine\nSAFARI-TOUR ist\vvorüber!"),
            "es": ("Aviso:¡Ding-dong!\f¡No tienes más\nSAFARI BALL!", "AVISO: ¡Tu JUEGO\nde SAFARI se ha\vterminado!"),
            "it": ("ANNUNCIO: Din-don!\fHai finito tutte\nle SAFARI BALL!", "ANNUNCIO: Il tuo\nGIOCO SAFARI è\vfinito!"),
            "ja-Hrkt": ("アナウンス『ピンポーン！\fサファリ　ボールを\nぜんぶ　なげました！", "アナウンス『サファリ　ゲーム\nおわり　でーす！"),
        }
        anchors = load_semantic_anchors()
        for language, target in translations.items():
            rows = [Alignment(qid, "both", CorpusRecord(qid, "en", source), CorpusRecord(qid, language, value), "qid") for qid, source, value in zip(qids, english, target)]
            output, report = match_engine_catalog({key: ""}, rows, semantic_anchors=anchors, target_lang=language)
            self.assertEqual(output[key], corpus_to_engine(target[0] + "\f" + target[1]), language)
            self.assertEqual(report["provenance"][key]["decision_type"], "composition", language)

    def test_naming_screen_compositions_include_name_fragment_per_language(self):
        translations = {
            "fr": ("VOTRE NOM?", "NOM DU RIVAL?", "NOM?"),
            "de": ("DEIN ", "GEGNER-", "NAME?"),
            "es": ("¿TU NOMBRE?", "¿NOMBRE RIVAL?", "¿NOMBRE?"),
            "it": ("NOME TUO?", "NOME RIVALE?", "NOME?"),
            "ja-Hrkt": ("あなた", "ライバル", "のなまえは？"),
        }
        qids = (
            "rb.naming_screen.YourTextString",
            "rb.naming_screen.RivalsTextString",
            "rb.naming_screen.NameTextString",
        )
        english = ("YOUR @", "RIVAL's @", "NAME?@")
        anchors = load_semantic_anchors()
        for language, target in translations.items():
            rows = [
                Alignment(qid, "both", CorpusRecord(qid, "en", source), CorpusRecord(qid, language, value), "qid")
                for qid, source, value in zip(qids, english, target)
            ]
            output, report = match_engine_catalog(
                {"YOUR NAME?": "", "HIS NAME?": ""},
                rows,
                semantic_anchors=anchors,
                target_lang=language,
            )
            self.assertEqual(output["YOUR NAME?"], target[0] + (target[2] if language in {"de", "ja-Hrkt"} else ""), language)
            self.assertEqual(output["HIS NAME?"], target[1] + (target[2] if language in {"de", "ja-Hrkt"} else ""), language)
            self.assertEqual(report["provenance"]["HIS NAME?"]["decision_type"], "composition", language)

    def test_naming_parts_target_languages_are_strict(self):
        base = {
            "X": {
                "parts": [
                    {"qid": "a", "extraction": {"kind": "full"}, "target_languages": ["de", "de"]},
                ],
                "placeholders": {},
            }
        }
        with self.assertRaises(ValueError):
            load_semantic_anchors(base)
        base["X"]["parts"][0]["target_languages"] = ["xx"]
        with self.assertRaises(ValueError):
            load_semantic_anchors(base)

    def test_self_check_requires_and_validates_decision_asset(self):
        import build_translation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            shutil.copy(ROOT / "config" / "pipeline.toml", root / "config" / "pipeline.toml")
            shutil.copy(ROOT / "config" / "semantic_anchors.json", root / "config" / "semantic_anchors.json")
            shutil.copy(ROOT / "pyproject.toml", root / "pyproject.toml")
            with patch("pipeline.builder.resource_root", return_value=root), patch("pipeline.builder.work_root", return_value=root / "work"):
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(build_translation._self_check(), 1)
                self.assertIn("semantic anchor decisions file missing", error.getvalue())
                decisions = root / "config" / "semantic_anchor_decisions.json"
                decisions.write_text("{}", encoding="utf-8")
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(build_translation._self_check(), 1)
                self.assertIn("wrapped schema", error.getvalue())
                shutil.copy(ROOT / "config" / "semantic_anchor_decisions.json", decisions)
                with patch("build_translation.importlib.util.find_spec", return_value=object()), redirect_stdout(StringIO()):
                    self.assertEqual(build_translation._self_check(), 0)


if __name__ == "__main__":
    unittest.main()
