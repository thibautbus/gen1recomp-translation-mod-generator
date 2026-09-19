import json
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from pipeline import frlg_join, frlg_text
from pipeline.frlg_join import (
    ENGLISH_MISMATCH, MARKUP_ONLY, NO_MATCH, OVERRIDE, PLACEHOLDER_MISMATCH, REVIEWED,
    SAME_AS_ENGLISH, TRANSLATED, UNENCODABLE, UNRESOLVED,
    join_frlg_dialogue, join_frlg_engine_strings, join_indexed_catalog,
    join_item_descriptions, join_start_menu, load_frlg_corpus, load_frlg_dialogue_decisions,
    load_frlg_dialogue_overrides, load_frlg_engine_scope, placeholders_supported,
    registry_id, registry_ids,
)
from pipeline.frlg_mod import (
    FRLG_CATALOG_HOOKS, _trainer_class_names, frlg_archive_name, frlg_mod_id, generate_frlg_mod, lua_ir,
)
from pipeline.frlg_text import (
    EncodeError, RUNTIME_CHARMAP, corpus_ir, decode, encode, ir_plain, load_charmap, load_symbols,
    text_key_address,
)
from pipeline.specs import game_spec, languages_for_collection, release_profile

ROOT = Path(__file__).resolve().parents[1]

# The pret charmap lines the tests exercise, in pret's own layout: the Latin
# block first, then a Japanese entry reusing a Latin byte.
CHARMAP = """\
' '         = 00
'À'         = 01
'É'         = 06
'Ü'         = F3
'é'         = 1B
'à'         = 16
'ç'         = 19
'¡'         = 52
'¿'         = 51
'0'         = A1
'1'         = A2
'!'         = AB
'?'         = AC
'.'         = AD
'…'         = B0
'“'         = B1
'”'         = B2
'‘'         = B3
'’'         = B4
'\\''        = B4
'¥'         = B7
','         = B8
""" + "".join(f"'{chr(ord('A') + i)}'         = {0xBB + i:02X}\n" for i in range(26)) \
    + "".join(f"'{chr(ord('a') + i)}'         = {0xD5 + i:02X}\n" for i in range(26)) + """\
'$'         = FF
'あ' = 01
PLAYER         = FD 01
STR_VAR_1      = FD 02
STR_VAR_2      = FD 03
RIVAL          = FD 06
PKMN = 53 54
COLOR = FC 01 @ use a color listed below right after
PAUSE = FC 08
PLAY_BGM = FC 0B
BLUE = 08
MUS_TEST = 56 01
DPAD_LEFTRIGHT = F8 04
'\\l' = FA @ scroll up window text
'\\p' = FB @ new paragraph
'\\n' = FE @ new line
"""


def write_charmap(directory: Path) -> frlg_text.PretCharmap:
    path = directory / "charmap.txt"
    path.write_text(CHARMAP, encoding="utf-8")
    return load_charmap(path)


def write_corpus(directory: Path, language: str, rows: list[tuple[str, str, str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, column in (("qid", 0), ("en", 1), (language, 2)):
        (directory / f"{name}_msg.txt").write_text(
            "\n".join(row[column] for row in rows) + "\n", encoding="utf-8")
    return directory


def text(value: str) -> dict:
    return {"t": "text", "s": value}


EOS = {"t": "eos"}


class FrlgTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_charmap_keeps_the_latin_glyph_for_shared_bytes(self):
        self.assertEqual(self.charmap.glyphs[0x01], "À")
        self.assertEqual(self.charmap.chars["あ"], b"\x01")
        self.assertEqual(self.charmap.names["PLAYER"], b"\xfd\x01")

    def test_corpus_escapes_and_placeholders_decode_like_the_runtime(self):
        ir = corpus_ir("Hi [PLAYER]!\\nGot [STR_VAR_2].\\cBye\\rnow", self.charmap)
        self.assertEqual(ir, [
            text("Hi "), {"t": "player"}, text("!"), {"t": "nl"}, text("Got "),
            {"t": "strvar", "n": 2}, text("."), {"t": "para"}, text("Bye"), {"t": "scroll"},
            text("now"), EOS,
        ])

    def test_control_escapes_keep_only_the_command_byte(self):
        ir = corpus_ir("[COLOR BLUE]A[PAUSE 15]B[PLAY_BGM MUS_TEST]C[PLAY_BGM MUS_UNKNOWN]", self.charmap)
        self.assertEqual(ir, [
            {"t": "ext", "cmd": 1}, text("A"), {"t": "ext", "cmd": 8}, text("B"),
            {"t": "ext", "cmd": 11}, text("C"), {"t": "ext", "cmd": 11}, EOS,
        ])

    def test_english_uses_the_runtime_glyphs_and_translations_the_rom_font(self):
        self.assertEqual(corpus_ir("à", self.charmap), [text("?"), EOS])
        self.assertEqual(corpus_ir("à ç", self.charmap, language="fr"), [text("à ç"), EOS])

    def test_keypad_escapes_mirror_the_runtime_in_every_language(self):
        english = corpus_ir("[DPAD_LEFTRIGHT]", self.charmap)
        self.assertEqual(english, [text("??"), EOS])
        self.assertEqual(corpus_ir("[DPAD_LEFTRIGHT]", self.charmap, language="fr"), english)

    def test_pkmn_ligature_reads_poke_like_text_ir(self):
        self.assertEqual(corpus_ir("[PKMN]", self.charmap), [text("POKé"), EOS])

    def test_language_folds_map_missing_quotes_onto_cart_bytes(self):
        self.assertEqual(corpus_ir("«Oui»", self.charmap, language="fr"), [text("“Oui”"), EOS])
        self.assertEqual(corpus_ir("„Ja“", self.charmap, language="de"), [text("“Ja”"), EOS])
        self.assertEqual(corpus_ir("1$", self.charmap), [text("1¥"), EOS])

    def test_unencodable_characters_and_tokens_raise(self):
        for value in ("⠁", "[NOT_A_TOKEN]", "[COLOR PURPLE]", "\\x"):
            with self.subTest(value=value), self.assertRaises(EncodeError):
                encode(value, self.charmap, language="fr")

    def test_decode_handles_names_and_unknown_bytes(self):
        self.assertEqual(decode(b"\xfd\x05\x99\xff"), [{"t": "ph", "code": 5}, text("?"), EOS])
        self.assertEqual(RUNTIME_CHARMAP[0xF0], ":")

    def test_named_glyph_runs_are_spelled_out_or_refused_in_translations(self):
        self.charmap.names["SUPER_ER"] = b"\x2c"
        self.charmap.names["LV"] = b"\x34"
        self.assertEqual(corpus_ir("1[SUPER_ER]", self.charmap, language="es"), [text("1er"), EOS])
        self.assertEqual(corpus_ir("1[SUPER_ER]", self.charmap), [text("1?"), EOS])
        with self.assertRaises(EncodeError):
            encode("[LV]5", self.charmap, language="fr")

    def test_decode_matches_the_runtime_on_random_bytes(self):
        engine = ROOT / ".cache" / "dependencies" / "gen1recomp"
        luajit = shutil.which("luajit")
        if not (engine / "src").is_dir() or not luajit:
            self.skipTest("pinned gen1recomp checkout or LuaJIT unavailable")
        import random
        rng = random.Random(3)
        pool = [0x00, 0x1B, 0x16, 0x53, 0x54, 0xBB, 0xD5, 0xF0, 0xF7, 0xF8, 0xF9, 0xFA, 0xFB,
                0xFC, 0xFD, 0xFE, 0xFF, 0x01, 0x04, 0x0B, 0x10]
        samples = [bytes(rng.choice(pool) if rng.random() < 0.7 else rng.randrange(256)
                         for _ in range(rng.randrange(1, 12))) for _ in range(3000)]
        probe = r"""
package.path = "./?.lua;./?/init.lua;" .. package.path
local TextIR = require("src.core.game3.scripting.text_ir")
for line in io.lines() do
  local bytes = {}
  for hex in line:gmatch("%x%x") do bytes[#bytes + 1] = tonumber(hex, 16) end
  local parts = {}
  for _, seg in ipairs(TextIR.decode(bytes)) do
    parts[#parts + 1] = table.concat({ seg.t, seg.s or "", tostring(seg.n or ""),
      tostring(seg.code or ""), tostring(seg.cmd or "") }, "\1")
  end
  io.write(table.concat(parts, "\2"), "\n")
end
"""
        result = subprocess.run([luajit, "-e", probe], cwd=engine, input="\n".join(s.hex() for s in samples) + "\n",
                                capture_output=True, text=True, check=True, encoding="utf-8")
        runtime = result.stdout.split("\n")
        for sample, expected in zip(samples, runtime):
            mine = "\2".join("\1".join((seg["t"], seg.get("s", ""), str(seg.get("n", "")),
                                          str(seg.get("code", "")), str(seg.get("cmd", ""))))
                             for seg in decode(sample))
            self.assertEqual(mine, expected, sample.hex())

    def test_symbols_and_text_keys(self):
        path = self.tmp / "firered.sym"
        path.write_text("081722c7 g 00000000 ViridianForest_Text_RickIntro\n"
                        "081722c7 l 00000000 alias\nnot a symbol line\n", encoding="utf-8")
        self.assertEqual(load_symbols(path)[0x081722C7], ["ViridianForest_Text_RickIntro", "alias"])
        self.assertEqual(text_key_address("g3:081722c7"), 0x081722C7)
        self.assertIsNone(text_key_address("Text_BootedUpPC"))

    def test_ir_plain(self):
        self.assertEqual(ir_plain([{"t": "player"}, text(" hi"), {"t": "para"}, {"t": "strvar", "n": 1}]),
                         "{PLAYER} hi\\p{STR_VAR_1}")


class FrlgDialogueJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)
        self.symbols = {0x08000010: ["Map_Text_Hello"], 0x08000020: ["Map_Text_Buffer"],
                        0x08000030: ["Map_Text_Twice"], 0x08000040: ["Map_Text_Stale"],
                        0x08000050: ["Map_Text_Ambiguous"], 0x08000060: ["Map_Text_Braille"]}
        self.corpus = load_frlg_corpus(write_corpus(self.tmp / "corpus", "fr", [
            ("frlg.script.Map.Map_Text_Hello", "Hello [PLAYER]!", "Salut [PLAYER]!"),
            ("frlg.script.Map.Map_Text_Buffer", "Got [STR_VAR_1].", "Obtenu [STR_VAR_2]."),
            ("frlg.script.Map.Map_Text_Twice", "[PLAYER]! [PLAYER]!", "Hé!"),
            ("frlg.script.Map.Map_Text_Stale", "Old text.", "Vieux texte."),
            ("frlg.script.A.Map_Text_Ambiguous", "Same.", "Un."),
            ("frlg.script.B.Map_Text_Ambiguous", "Same.", "Deux."),
            ("frlg.script.Map.Map_Text_Braille", "⠁", "⠁"),
            ("frlg.script.std.Text_Std", "Obtained!", "Obtenu!"),
            ("frlg.script.Map.Map_Text_Same", "OK!", "OK!"),
        ]), "fr")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rom(self, value: str) -> list[dict]:
        return corpus_ir(value, self.charmap)

    def join(self, text_table, **kwargs):
        entries, stats = join_frlg_dialogue(text_table, self.corpus, self.symbols, self.charmap, **kwargs)
        return {entry.key: entry for entry in entries}, stats

    def test_pointer_keys_join_through_the_symbol_table(self):
        entries, stats = self.join({"g3:08000010": self.rom("Hello [PLAYER]!")})
        entry = entries["g3:08000010"]
        self.assertEqual(entry.status, TRANSLATED)
        self.assertEqual(entry.qid, "frlg.script.Map.Map_Text_Hello")
        self.assertEqual(entry.translation, [text("Salut "), {"t": "player"}, text("!"), EOS])
        self.assertEqual(stats["covered"], 1)

    def test_statuses(self):
        entries, stats = self.join({
            "g3:08000020": self.rom("Got [STR_VAR_1]."),
            "g3:08000030": self.rom("[PLAYER]! [PLAYER]!"),
            "g3:08000040": self.rom("New text."),
            "g3:08000050": self.rom("Same."),
            "g3:08000060": [text("?é?"), EOS],
            "g3:08000070": self.rom("Nobody."),
            "g3:08000080": [text("  "), EOS],
            "Text_Unknown": self.rom("Engine only."),
        })
        self.assertEqual(entries["g3:08000020"].status, PLACEHOLDER_MISMATCH)
        # Dropping a repeated player name is an official localisation choice.
        self.assertEqual(entries["g3:08000030"].status, TRANSLATED)
        self.assertEqual(entries["g3:08000040"].status, ENGLISH_MISMATCH)
        self.assertEqual(entries["g3:08000050"].status, UNRESOLVED)
        self.assertEqual(entries["g3:08000060"].status, UNENCODABLE)
        self.assertEqual(entries["g3:08000070"].status, frlg_join.NO_SYMBOL)
        self.assertEqual(entries["g3:08000080"].status, MARKUP_ONLY)
        self.assertEqual(entries["Text_Unknown"].status, NO_MATCH)
        self.assertEqual(stats["total"], 7)
        self.assertEqual(stats["ignored_markup_only"], 1)

    def test_same_as_english_is_covered_but_not_shipped(self):
        self.symbols[0x08000090] = ["Map_Text_Same"]
        entries, stats = self.join({"g3:08000090": self.rom("OK!")})
        self.assertEqual(entries["g3:08000090"].status, SAME_AS_ENGLISH)
        self.assertEqual(frlg_join.dialogue_catalog(entries.values()), {})
        self.assertEqual((stats["covered"], stats["shipped"]), (1, 0))

    def test_reviewed_decision_accepts_engine_rewording(self):
        key = "Text_Rewritten"
        entries, _ = self.join({key: self.rom("[PLAYER] obtained it!")},
                               decisions={key: "frlg.script.std.Text_Std"})
        self.assertEqual(entries[key].status, REVIEWED)
        self.assertEqual(entries[key].translation, [text("Obtenu!"), EOS])

    def test_override_bypasses_the_english_check_but_not_placeholders(self):
        key = "g3:08000040"
        entries, _ = self.join({key: self.rom("New text.")},
                               overrides={key: {"qid": "x", "text": "Nouveau.", "reason": "r"}})
        self.assertEqual(entries[key].status, OVERRIDE)
        entries, _ = self.join({key: self.rom("New text.")},
                               overrides={key: {"qid": "x", "text": "[STR_VAR_1]", "reason": "r"}})
        self.assertEqual(entries[key].status, PLACEHOLDER_MISMATCH)

    def test_placeholders_supported(self):
        english = self.rom("[PLAYER] [STR_VAR_2]")
        self.assertTrue(placeholders_supported(self.rom("[STR_VAR_2]"), english))
        self.assertTrue(placeholders_supported(self.rom("[PLAYER][PLAYER]"), english))
        self.assertFalse(placeholders_supported(self.rom("[STR_VAR_1]"), english))
        self.assertFalse(placeholders_supported(self.rom("[RIVAL]"), english))


class FrlgCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)
        self.corpus = load_frlg_corpus(write_corpus(self.tmp / "corpus", "fr", [
            ("frlg.common.species_names.gSpeciesNames.1", "BULBASAUR", "BULBIZARRE"),
            ("frlg.common.species_names.gSpeciesNames.2", "IVYSAUR", "IVYSAUR"),
            ("frlg.common.species_names.gSpeciesNames.3", "VENUSAUR", ""),
            ("frlg.common.trainer_class_names.gTrainerClassNames.1", "[PKMN] TRAINER", "DRESSEUR"),
            ("frlg.common.trainer_class_names.gTrainerClassNames.81", "RIVAL", "RIVALE"),
            ("frlg.common.strings.gText_MenuBag", "BAG", "SAC"),
            ("frlg.common.strings.gText_MenuPokedex", "POKéDEX", "POKéDEX"),
            ("frlg.common.items.gItemDescription_ITEM_BEAD_MAIL", "Mail.", "Lettre perle."),
            ("frlg.common.items.gItemDescription_ITEM_DREAM_MAIL", "Mail.", "Lettre rêve."),
            ("frlg.common.move_descriptions.sRoarDescription", "Roar.", "Hurle."),
            ("frlg.common.move_descriptions.sWhirlwindDescription", "Roar.", "Souffle."),
        ]), "fr")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_registry_id_matches_g3_id_of(self):
        self.assertEqual(registry_id("NIDORAN♀"), "NIDORAN_F")
        self.assertEqual(registry_id("MR. MIME"), "MR_MIME")
        self.assertEqual(registry_id("FARFETCH'D"), "FARFETCHD")
        self.assertEqual(registry_id("POKé BALL"), "POKE_BALL")
        self.assertIsNone(registry_id("??????????"))
        self.assertEqual(registry_ids({1: "TM01", 2: "TM01", 3: "?"}), {1: "TM01"})

    def test_indexed_catalog(self):
        result = join_indexed_catalog({1: "BULBASAUR", 2: "IVYSAUR", 3: "VENUSAUR"},
                                      {1: "BULBASAUR", 2: "IVYSAUR", 3: "VENUSAUR"},
                                      self.corpus, "frlg.common.species_names.gSpeciesNames.", self.charmap)
        self.assertEqual(result.values, {"BULBASAUR": "BULBIZARRE"})
        self.assertEqual(result.summary()["same_as_english"], 1)
        self.assertEqual(result.summary()["fallback_english"], 1)

    def test_pkmn_ligature_matches_the_trainer_extractor(self):
        result = join_indexed_catalog({1: "POKéMON TRAINER"}, {1: "1"}, self.corpus,
                                      "frlg.common.trainer_class_names.gTrainerClassNames.", self.charmap)
        self.assertEqual(result.values, {"1": "DRESSEUR"})

    def test_english_mismatch_is_reported_not_translated(self):
        result = join_indexed_catalog({1: "BULBASAUR!"}, {1: "BULBASAUR"}, self.corpus,
                                      "frlg.common.species_names.gSpeciesNames.", self.charmap)
        self.assertEqual(result.values, {})
        self.assertEqual(result.stats["english_mismatch"], 1)

    def test_class_names_the_engine_compares_stay_english(self):
        trainers = {1: {"class": 1, "className": "POKéMON TRAINER", "name": "A"},
                    2: {"class": 81, "className": "RIVAL", "name": "TERRY"}}
        result = _trainer_class_names(trainers, {1: "1", 2: "2"}, self.corpus, self.charmap)
        self.assertEqual(result.values, {"1": "DRESSEUR"})
        self.assertEqual(result.summary()["fallback_english"], 1)

    def test_every_class_name_the_engine_compares_stays_english(self):
        source = ROOT / ".cache" / "dependencies" / "gen1recomp" / "src"
        if not source.is_dir():
            self.skipTest("pinned gen1recomp checkout unavailable")
        from pipeline.frlg_mod import ENGINE_KEYED_CLASS_NAMES
        pattern = re.compile(r"\b(?:className|trainerClassName|class)\s*[=~]=\s*['\"]([^'\"]+)['\"]")
        compared = set()
        for root in ("core/game3", "ui/game3", "battle/game3", "world/game3"):
            for path in (source / root).rglob("*.lua"):
                compared.update(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
        self.assertTrue(compared)
        self.assertLessEqual(compared, set(ENGINE_KEYED_CLASS_NAMES))

    def test_start_menu_labels(self):
        result = join_start_menu(self.corpus, self.charmap)
        self.assertEqual(result.values, {"bag": "SAC"})
        self.assertEqual(result.stats["same_as_english"], 1)
        self.assertEqual(result.stats["no_corpus_row"], 4)

    def test_item_descriptions_prefer_the_items_own_row(self):
        items = {1: {"name": "BEAD MAIL", "description": "Mail."},
                 2: {"name": "TM05", "description": "Roar."}}
        result = join_item_descriptions(items, {1: "BEAD_MAIL", 2: "TM05"}, self.corpus, self.charmap)
        self.assertEqual(result.values, {"BEAD_MAIL": "Lettre perle."})
        self.assertEqual(result.stats["unresolved"], 1)


class FrlgEngineStringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)
        self.corpus = load_frlg_corpus(write_corpus(self.tmp / "corpus", "fr", [
            ("frlg.common.strings.gText_Yes", "YES", "OUI"),
            ("frlg.common.strings.gText_TextSpeed", "TEXT SPEED", "VIT. TEXTE"),
        ]), "fr")
        base = self.tmp / "repo"
        for game, entries in (("frlg", {"ZOOM": "ZOOM", "YES": "SI"}), ("gsc", {"MUSIC VOL": "VOL. MUSIQUE"})):
            path = base / "overrides" / "fr" / game / "engine.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"entries": {k: {"override": v} for k, v in entries.items()}}),
                            encoding="utf-8")
        self.base = base

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_resolution_order(self):
        scope = {
            "YES": {"callsite": "x", "qid": "frlg.common.strings.gText_Yes"},
            "TEXT SPEED": {"callsite": "x", "qid": "frlg.common.strings.gText_TextSpeed"},
            "MUSIC VOL": {"callsite": "x"},
            "ZOOM": {"callsite": "x"},
            "VSYNC": {"callsite": "x"},
        }
        values, stats = join_frlg_engine_strings(scope, self.corpus, self.charmap, root=self.base)
        self.assertEqual(values, {"YES": "SI", "TEXT SPEED": "VIT. TEXTE", "MUSIC VOL": "VOL. MUSIQUE"})
        self.assertEqual(stats["details"]["ZOOM"], "same_as_english")
        self.assertEqual(stats["fallback_english"], ["VSYNC"])
        self.assertEqual(stats["translated"], 4)

    def test_scope_qid_must_read_the_key(self):
        scope = {"NO": {"callsite": "x", "qid": "frlg.common.strings.gText_Yes"}}
        with self.assertRaises(ValueError):
            join_frlg_engine_strings(scope, self.corpus, self.charmap, root=self.base)

    def test_values_need_rom_glyphs(self):
        path = self.base / "overrides" / "fr" / "frlg" / "engine.json"
        path.write_text(json.dumps({"entries": {"VSYNC": {"override": "⠁"}}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            join_frlg_engine_strings({"VSYNC": {"callsite": "x"}}, self.corpus, self.charmap, root=self.base)


class FrlgEngineTemplateTests(unittest.TestCase):
    """engine_template against pret's charmap (the placeholders need it)."""

    def setUp(self):
        path = ROOT / ".cache" / "dependencies" / "pret" / "charmap" / "charmap.txt"
        if not path.is_file():
            self.skipTest("pinned pret charmap unavailable")
        self.charmap = load_charmap(path)

    def template(self, key, english, target, **kw):
        return frlg_join.engine_template(key, english, target, self.charmap, "fr", **kw)

    def test_same_order_keeps_plain_directives(self):
        self.assertEqual(self.template("%s\nfell asleep!", "[B_EFF_NAME_WITH_PREFIX]\\nfell asleep!",
                                       "[B_EFF_NAME_WITH_PREFIX]\\ns'endort!", battle=True),
                         ("%s\ns'endort!", "exact"))

    def test_reordered_values_are_numbered(self):
        value, _ = self.template(
            "%s knocked off\n%s's %s!",
            "[B_ATK_NAME_WITH_PREFIX] knocked off\\n[B_DEF_NAME_WITH_PREFIX]'s [B_LAST_ITEM]!",
            "[B_ATK_NAME_WITH_PREFIX] fait tomber\\n[B_LAST_ITEM]\\ndu [B_DEF_NAME_WITH_PREFIX]!", battle=True)
        self.assertEqual(value, "%1$s fait tomber\n%3$s\ndu %2$s!")

    def test_directive_flags_follow_their_value(self):
        value, _ = self.template("%s gave %03d", "[STR_VAR_1] gave [STR_VAR_2]", "[STR_VAR_2] de [STR_VAR_1]")
        self.assertEqual(value, "%2$03d de %1$s")

    def test_line_breaks_may_differ(self):
        value, how = self.template("%s fainted!", "[B_EFF_NAME_WITH_PREFIX]\\nfainted!\\p",
                                   "[B_EFF_NAME_WITH_PREFIX]\\nest KO!", battle=True)
        self.assertEqual((value, how), ("%s\nest KO!", "loose"))

    def test_a_trailing_page_mark_of_the_key_is_kept(self):
        value, _ = self.template("This world…\f", "This world…", "Ce monde…")
        self.assertEqual(value, "Ce monde…\f")

    def test_spelled_out_buffers_and_keypad_icons_stay_tokens(self):
        self.assertEqual(self.template("{STR_VAR_1} used SURF!", "[STR_VAR_1] used SURF!", "[STR_VAR_1] utilise SURF!")[0],
                         "{STR_VAR_1} utilise SURF!")
        self.assertEqual(self.template("{A_BUTTON}OK", "[A_BUTTON]OK", "[A_BUTTON]VALE")[0], "{A_BUTTON}VALE")

    def test_rows_that_cannot_stand_for_the_key_are_refused(self):
        with self.assertRaises(ValueError):  # the English says something else
            self.template("%s fled!", "[B_ATK_NAME_WITH_PREFIX] fainted!", "x", battle=True)
        with self.assertRaises(ValueError):  # the translation drops a value
            self.template("%s used %s!", "[STR_VAR_1] used [STR_VAR_2]!", "[STR_VAR_1] attaque!")
        with self.assertRaises(ValueError):  # a repeated value cannot be renumbered
            self.template("%s, %s and %s", "[STR_VAR_1], [STR_VAR_2] and [STR_VAR_1]",
                          "[STR_VAR_2], [STR_VAR_1] et [STR_VAR_1]")

    def test_cart_padding_is_trimmed(self):
        self.assertEqual(self.template("NAME:", "NAME: ", "NOM / ")[0], "NOM /")

    def test_values_strings_would_reject_are_refused(self):
        check = frlg_join.check_engine_directives
        check("%s's %s\nrose!", "%2$s de\n%1$s monte!")
        check("%s is\nabout to use %s.", "%2$s arrive.")  # an argument may be left out
        for bad in ("%s de\n%s %s", "%2$s de %s", "%3$s x", "%1$d x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check("%s's %s\nrose!", bad)

    def test_join_applies_fills_and_contexts(self):
        from pipeline.frlg_join import FrlgCorpus
        rows = [("q.rain", "[B_SCR_ACTIVE_NAME_WITH_PREFIX]'s [B_SCR_ACTIVE_ABILITY]\\nmade it rain!",
                 "[B_SCR_ACTIVE_ABILITY] du\\n[B_SCR_ACTIVE_NAME_WITH_PREFIX] fait pleuvoir!"),
                ("q.drizzle", "DRIZZLE", "CRACHIN"),
                ("q.shift", "SHIFT", "CHOIX")]
        corpus = FrlgCorpus("fr", tuple(r[0] for r in rows), tuple(r[1] for r in rows),
                            tuple(r[2] for r in rows), {r[0]: i for i, r in enumerate(rows)}, {})
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        scope = {
            "%s's DRIZZLE\nmade it rain!": {"callsite": "x", "qid": "q.rain", "fill": {"[B_SCR_ACTIVE_ABILITY]": "q.drizzle"}},
            "option.battleStyle|SHIFT": {"callsite": "x", "qid": "q.shift"},
        }
        with unittest.mock.patch.object(frlg_join, "is_battle_qid", lambda qid: qid == "q.rain"):
            values, stats = join_frlg_engine_strings(scope, corpus, self.charmap, root=tmp)
        self.assertEqual(values, {"%s's DRIZZLE\nmade it rain!": "CRACHIN du\n%s fait pleuvoir!",
                                  "option.battleStyle|SHIFT": "CHOIX"})
        self.assertEqual(stats["fallback_english"], [])


class FrlgEngineScopeTests(unittest.TestCase):
    def test_source_readers(self):
        from pipeline.frlg_engine_scope import _identifier, _line_values, _table_values, context_source
        text = 'local T = {\n  { id = "quit", label = "SEE YA!", icon = "x" },\n  "A\\nB", -- "no"\n}\n'
        self.assertEqual(_table_values(text, "T"), ["SEE YA!", "A\nB"])
        self.assertEqual(_line_values('x = { "USE", "CANCEL" }\ny = "no"', r"x = \{"), ["USE", "CANCEL"])
        self.assertTrue(_identifier("light_screen") and _identifier("battleStyle"))
        self.assertFalse(_identifier("CANCEL") or _identifier("Beach"))
        self.assertEqual(context_source("option.battleStyle|SHIFT"), "SHIFT")
        self.assertEqual(context_source("A|B"), "A|B")


class FrlgModTests(unittest.TestCase):
    def test_generated_mod_targets_firered_with_registry_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_frlg_mod(
                Path(directory) / "mod", language="fr", target_name="French translation for FireRed",
                dialogue={"g3:08000010": [text('Dit "oui"\\'), {"t": "player"}, {"t": "strvar", "n": 2}, EOS]},
                catalogs={"species_names": {"BULBASAUR": "BULBIZARRE"}, "strings": {"YES": "OUI"},
                          "start_menu": {"bag": "SAC"}},
            )
            manifest = json.loads((mod / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["games"], ["firered"])
            self.assertEqual(manifest["id"], "translation-fr-gen3")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(FRLG_CATALOG_HOOKS["species_names"], main)
            self.assertIn(FRLG_CATALOG_HOOKS["strings"], main)
            self.assertNotIn("font", main)
            self.assertIn('mod.hooks:wrap("ui.start_menu.items"', main)
            self.assertTrue((mod / "lang" / "start_menu.lua").is_file())
            self.assertFalse((mod / "lang" / "move_names.lua").exists())
            luajit = shutil.which("luajit")
            if luajit:
                probe = ("local t = dofile(arg[1]); local ir = t['g3:08000010'];"
                         "io.write(ir[1].s, '|', ir[2].t, '|', ir[3].n, '|', ir[4].t)")
                out = subprocess.run([luajit, "-e", probe.replace("arg[1]", repr(str(mod / "lang" / "dialogue.lua")))],
                                     capture_output=True, text=True, check=True).stdout
                self.assertEqual(out, 'Dit "oui"\\|player|2|eos')

    def test_gate_counts_glyphs_in_every_generated_catalog(self):
        gate = (ROOT / "tools" / "gate_frlg.lua").read_text(encoding="utf-8")
        listed = re.search(r'for _, catalogName in ipairs\(\{(.*?)\}\)', gate, re.S).group(1)
        names = set(re.findall(r'"([a-z_]+)"', listed))
        self.assertEqual(names, set(FRLG_CATALOG_HOOKS) | {"dialogue", "start_menu"})

    def test_lua_ir_rejects_unknown_values(self):
        with self.assertRaises(TypeError):
            lua_ir([{"t": "text", "s": 1.5}])

    def test_names(self):
        self.assertEqual(frlg_mod_id("ja-Hrkt"), "translation-ja-hrkt-gen3")
        self.assertEqual(frlg_archive_name("fr", "1.0"), "translation-fr-gen3-1.0.zip")


class FrlgConfigTests(unittest.TestCase):
    def test_release_profile(self):
        profile = release_profile("frlg")
        self.assertEqual((profile.generation, profile.games), (3, ("firered",)))
        self.assertEqual(game_spec("firered").corpus_collection, "FireRedLeafGreen")
        codes = [code for code, _ in languages_for_collection("FireRedLeafGreen")]
        self.assertEqual(codes, ["fr", "de", "es", "it"])

    def test_checked_in_config_loads(self):
        scope = load_frlg_engine_scope()
        self.assertIn("TEXT SPEED", scope)
        self.assertNotIn("Go back to the\nprevious menu.", scope)
        decisions = load_frlg_dialogue_decisions()
        self.assertIn("Text_TownMap", decisions)
        for language in ("fr", "de", "es", "it"):
            with self.subTest(language=language):
                overrides = load_frlg_dialogue_overrides(language)
                self.assertIn("Text_FoundTMHMContainsMove", overrides)
                engine = json.loads((ROOT / "overrides" / language / "frlg" / "engine.json").read_text(encoding="utf-8"))
                for key, row in engine["entries"].items():
                    self.assertIn(key, scope)
                    self.assertTrue(row.get("reason") and row.get("provenance"), key)

    def test_engine_scope_covers_every_game3_strings_key(self):
        engine = ROOT / ".cache" / "dependencies" / "gen1recomp"
        luajit = shutil.which("luajit")
        if not (engine / "src").is_dir() or not luajit:
            self.skipTest("pinned gen1recomp checkout or LuaJIT unavailable")
        from pipeline.engine_backlog import iter_literal_strings_callsites
        scope = load_frlg_engine_scope()
        # Strings.source() only marks a key; it never translates (pc_menu.lua).
        literal = {row["source"] for row in iter_literal_strings_callsites(engine / "src")
                   if "game3" in row.get("path", "") and row.get("kind") != "source"}
        probe = r"""
package.path = "./?.lua;./?/init.lua;" .. package.path
love = require("tests.love_stub")
local out = {}
local function add(v) if type(v) == "string" then out[#out + 1] = v end end
local Rows = require("src.ui.game3.option_rows")
for _, g in ipairs(Rows.GROUPS) do add(g.label) end
for _, row in ipairs(Rows.build({ options = {} })) do add(row.label) end
local L = require("src.render.Letterbox"); for _, m in ipairs(L.MODES) do add(L.label(m)) end
local V = require("src.core.VideoMode"); for _, m in ipairs({ "windowed", "borderless" }) do add(V.modeLabel(m)) end
local O = require("src.core.Orientation"); for _, m in ipairs(O.MODES) do add(O.modeLabel(m)) end
local F = require("src.core.FaithfulRes"); for v = 0, 6 do add(F.label(v)) end
local S = require("src.core.ScreenPosition"); for _, m in ipairs(S.MODES) do add(S.label(m)) end; add("SKIN")
local C = require("src.core.FrameCap"); add(C.label(C.DISPLAY))
local Y = require("src.core.VSync"); for _, m in ipairs(Y.MODES) do add(Y.label(m)) end; add(Y.label("adaptive"))
local P = require("src.core.Performance"); for _, v in pairs(P.LABELS) do add(v) end
local Z = require("src.render.Zoom"); for o = -4, 4 do add(Z.offsetLabel(o)) end
local VF = require("src.core.game3.void_fill"); for _, v in pairs(VF.LABELS) do add(v) end
local TC = require("src.core.TouchControls"); for _, m in ipairs(TC.HAPTICS) do add(TC.hapticLabel(m)) end
io.write(table.concat(out, "\0"))
"""
        values = subprocess.run([luajit, "-e", probe], cwd=engine, capture_output=True,
                                text=True, check=True).stdout.split("\0")
        dynamic = {value for value in values if value and not re.fullmatch(r"[0-9.]+(X|HZ)?", value)}
        self.assertIn("WINDOWED", dynamic)
        self.assertEqual(sorted((literal | dynamic) - set(scope)), [])

    def test_upstream_doc_inventories_every_hardcoded_text_file(self):
        engine = ROOT / ".cache" / "dependencies" / "gen1recomp"
        if not (engine / "src").is_dir():
            self.skipTest("pinned gen1recomp checkout unavailable")
        from pipeline.frlg_audit import NON_DISPLAY_FILES, player_visible_files, scan_hardcoded_literals
        doc = (ROOT / "docs" / "upstream-fixes.md").read_text(encoding="utf-8")
        inventory = doc.split("#### Inventory: every game3 file with hardcoded player-visible text", 1)[1]
        inventory = inventory.split("\n### ", 1)[0]
        listed = set(re.findall(r"^\| `(src/[^`]+)` \|", inventory, re.M))
        visible = set(player_visible_files(engine))
        self.assertEqual(sorted(visible - listed), [], "player-visible files missing from the inventory")
        self.assertEqual(sorted(listed - visible), [], "inventory rows the scan no longer finds")
        self.assertEqual(sorted(set(NON_DISPLAY_FILES) - set(scan_hardcoded_literals(engine))), [],
                         "stale NON_DISPLAY_FILES entries")

    def test_reviewed_qids_exist_in_the_pinned_corpus(self):
        corpus = ROOT / ".cache" / "dependencies" / "poke-corpus" / "corpus" / "FireRedLeafGreen"
        charmap_path = ROOT / ".cache" / "dependencies" / "pret" / "charmap" / "charmap.txt"
        if not (corpus / "qid_msg.txt").is_file() or not charmap_path.is_file():
            self.skipTest("pinned FireRedLeafGreen corpus or pret charmap unavailable")
        charmap = load_charmap(charmap_path)
        qids = set((corpus / "qid_msg.txt").read_text(encoding="utf-8").splitlines())
        for key, qid in load_frlg_dialogue_decisions().items():
            self.assertIn(qid, qids, key)
        for language in ("fr", "de", "es", "it"):
            with self.subTest(language=language):
                loaded = load_frlg_corpus(corpus, language)
                values, stats = join_frlg_engine_strings(load_frlg_engine_scope(), loaded, charmap)
                self.assertEqual(stats["fallback_english"], [])
                for row in load_frlg_dialogue_overrides(language).values():
                    self.assertIn(row["qid"], qids)
                    corpus_ir(row["text"], charmap, language=language)


if __name__ == "__main__":
    unittest.main()
