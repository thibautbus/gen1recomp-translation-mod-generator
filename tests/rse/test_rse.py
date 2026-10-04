import json
import re
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from pipeline.gen3.family import EMERALD, FRLG
from pipeline.gen3.join import (
    CONTENT_MATCH, SAME_AS_ENGLISH, TRANSLATED, UNRESOLVED,
    join_gen3_dialogue, join_gen3_engine_strings, join_indexed_catalog, join_item_descriptions,
    load_dialogue_decisions, load_dialogue_overrides, load_engine_scope, load_gen3_corpus,
)
from pipeline.gen3.join import Gen3DialogueEntry
from pipeline.gen3.mod import dialogue_label_rows
from pipeline.gen3.text import RSE_DIALECT, corpus_ir, decode, encode, load_charmap
from pipeline.rse.mod import (
    DIALOGUE_FILE_ENTRIES, dialogue_files, generate_rse_mod, rse_archive_name, rse_mod_id,
    text_aliases, write_gate_expectations,
)
from pipeline.rse.european import apply_european_trainer_text, load_european_trainer_text
from pipeline.shared.gui import generation_code, generation_label
from pipeline.shared.roms import verify_emerald_rom
from pipeline.shared.specs import (
    game_spec, languages_for_collection, release_profile, release_profile_for_selection,
)

ROOT = Path(__file__).resolve().parents[2]

# The pokeemerald charmap lines the tests exercise.
CHARMAP = """\
' '         = 00
'é'         = 1B
'Í'         = 5A
'!'         = AB
'.'         = AD
'“'         = B1
'”'         = B2
'\\''        = B4
'¥'         = B7
""" + "".join(f"'{chr(ord('A') + i)}'         = {0xBB + i:02X}\n" for i in range(26)) \
    + "".join(f"'{chr(ord('a') + i)}'         = {0xD5 + i:02X}\n" for i in range(26)) + """\
'$'         = FF
'あ' = 01
LV          = 34
POKEBLOCK   = 55 56 57 58 59
PLAYER         = FD 01
STR_VAR_1      = FD 02
KUN            = FD 05
RIVAL          = FD 06
AQUA           = FD 08
B_ATK_NAME_WITH_PREFIX = FD 0F
PKMN = 53 54
COLOR = FC 01
FONT_NORMAL = FC 06 01
FONT_NARROW = FC 06 07
'\\l' = FA
'\\p' = FB
'\\n' = FE
"""


def write_charmap(directory: Path, dialect="rse"):
    path = directory / "charmap.txt"
    path.write_text(CHARMAP, encoding="utf-8")
    return load_charmap(path, dialect)


def write_corpus(directory: Path, language: str, rows: list[tuple[str, str, str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, column in (("qid", 0), ("en", 1), (language, 2)):
        (directory / f"{name}_msg.txt").write_text(
            "\n".join(row[column] for row in rows) + "\n", encoding="utf-8")
    return directory


def text(value: str) -> dict:
    return {"t": "text", "s": value}


EOS = {"t": "eos"}


class EmeraldTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_the_rse_dialect_names_placeholders_and_fonts_like_the_runtime(self):
        self.assertEqual(self.charmap.dialect, RSE_DIALECT)
        self.assertEqual(corpus_ir("[AQUA] and [KUN][FONT_NARROW]x", self.charmap), [
            {"t": "ph", "code": 8, "name": "AQUA"}, text(" and "),
            {"t": "ph", "code": 5, "name": "KUN"},
            {"t": "ext", "cmd": 6, "args": [7], "font": "FONT_NARROW"}, text("x"), EOS,
        ])

    def test_the_frlg_dialect_keeps_unnamed_placeholders(self):
        frlg = write_charmap(self.tmp, "frlg")
        self.assertEqual(decode(b"\xfd\x08\xff", dialect=frlg.dialect), [{"t": "ph", "code": 8}, EOS])

    def test_glyph_runs_decode_to_the_tags_the_runtime_draws(self):
        self.assertEqual(corpus_ir("A [POKEBLOCK] [LV]", self.charmap), [
            text("A "), {"t": "tag", "tag": "{POKEBLOCK}"}, text(" "), {"t": "tag", "tag": "{LV}"}, EOS,
        ])
        # a translation keeps the tag, which the runtime draws as in English
        self.assertEqual(corpus_ir("[POKEBLOCK]", self.charmap, language="fr"),
                         [{"t": "tag", "tag": "{POKEBLOCK}"}, EOS])
        # FireRed has no such tag: its translations spell the word out
        frlg = write_charmap(self.tmp, "frlg")
        self.assertEqual(corpus_ir("[POKEBLOCK]", frlg, language="fr"), [text("POKéBLOCK"), EOS])

    def test_ruby_sapphire_names_reach_emeralds_bytes(self):
        self.assertEqual(encode("[EVIL_TEAM]", self.charmap), encode("[AQUA]", self.charmap))

    def test_european_glyph_runs_are_spelled_out(self):
        self.assertEqual(corpus_ir("[Pco] [POKEMELLA]", self.charmap, language="it"),
                         [text("Pco POKéMELLA"), EOS])


class EmeraldJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.charmap = write_charmap(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def corpus(self, rows, language="fr"):
        return load_gen3_corpus(write_corpus(self.tmp / language, language, rows), language, EMERALD)

    # pret pokeemerald multi-language: src/battle_message.c:3937 and
    # src/international_string_util.c:280
    EUROPEAN_TRAINERS = {
        10: {"name": "GRUNT", "class": 3, "className": "TEAM AQUA", "encounterMusic": 6},
        273: {"name": "JERRY", "class": 33, "className": "SCHOOL KID", "encounterMusic": 0},
        280: {"name": "KAREN", "class": 33, "className": "SCHOOL KID", "encounterMusic": 2},
        520: {"name": "BRENDAN", "class": 50, "className": "TRAINER", "encounterMusic": 0},
        529: {"name": "MAY", "class": 50, "className": "TRAINER", "encounterMusic": 1},
        271: {"name": "TATE&LIZA", "class": 32, "className": "LEADER", "encounterMusic": 1},
    }

    def european(self, language, rows, names=None, classes=None):
        corpus = self.corpus(rows, language)
        names, classes = dict(names or {}), dict(classes or {})
        ids = {number: str(number) for number in self.EUROPEAN_TRAINERS}
        stats = apply_european_trainer_text(names, classes, self.EUROPEAN_TRAINERS, ids, corpus, self.charmap)
        return names, classes, stats

    def test_french_and_spanish_grunts_take_their_name_before_their_class(self):
        # French: the class row is the English one, so the catalog has no
        # entry for it and the swap reads it from the corpus
        names, classes, _ = self.european("fr", [
            ("e.common.trainer_class_names.gTrainerClassNames.3", "TEAM AQUA", "TEAM AQUA"),
            ("e.common.trainers.gTrainers.10", "GRUNT", "SBIRE"),
        ], names={"10": "SBIRE"})
        self.assertEqual((classes["10"], names["10"]), ("SBIRE", "TEAM AQUA"))
        names, classes, _ = self.european("es", [], names={"10": "SOLDADO"}, classes={"10": "EQUIPO AQUA"})
        self.assertEqual((classes["10"], names["10"]), ("SOLDADO", "EQUIPO AQUA"))
        # Italian and German keep the US order
        names, classes, _ = self.european("it", [], names={"10": "RECLUTA"}, classes={"10": "TEAM IDRO"})
        self.assertEqual((classes["10"], names["10"]), ("TEAM IDRO", "RECLUTA"))
        # a grunt whose rows cannot be read is left as joined
        names, classes, _ = self.european("fr", [], names={"10": "SBIRE"})
        self.assertEqual((classes.get("10"), names["10"]), (None, "SBIRE"))

    def test_european_carts_name_some_trainers_with_their_own_class_words(self):
        names, classes, stats = self.european("it", [], classes={
            "273": "SCOLARO", "280": "SCOLARO", "520": "ALLENATORE", "529": "ALLENATORE", "271": "CAPOPALESTRA"})
        # the cart reads the encounter music, not the trainer's gender
        self.assertEqual((classes["273"], classes["280"]), ("SCOLARO", "SCOLARA"))
        self.assertEqual(classes["271"], "CAPIPALESTRA")
        self.assertEqual((classes["520"], classes["529"]), ("ALLENATORE", "ALLENATORE"))
        self.assertEqual(stats, {"gText_SchoolKidFemale": 1, "gText_LeaderPlural": 1})
        _, classes, _ = self.european("es", [], classes={"520": "ENTRENADOR", "529": "ENTRENADOR"})
        self.assertEqual((classes["520"], classes["529"]), ("ENTRENADOR", "ENTRENADORA"))
        _, classes, _ = self.european("es", [], classes={"271": "LÍDER"})
        self.assertEqual(classes["271"], "LÍDERES")
        # German has no European word to take: nothing changes
        _, classes, stats = self.european("de", [], classes={"280": "SCHULKIND"})
        self.assertEqual((classes, stats), ({"280": "SCHULKIND"}, {}))
        # an extract from before tools/rse/extract.lua exported the music
        # would silently skip the gendered words: it stops the build instead
        stale = {280: {"name": "KAREN", "class": 33, "className": "SCHOOL KID"}}
        with self.assertRaisesRegex(ValueError, "encounterMusic"):
            apply_european_trainer_text({}, {}, stale, {280: "280"}, self.corpus([], "it"), self.charmap)

    def test_the_european_trainer_words_are_reviewed_with_their_source(self):
        config = load_european_trainer_text()
        for variant in config["class_variants"]:
            self.assertEqual(set(variant["values"]), {"fr", "it", "es"}, variant["label"])
            self.assertIn("src/strings.c", variant["provenance"])
        self.assertEqual(config["class_name_swap"]["languages"], ["fr", "es"])

    def test_a_pointer_table_slot_joins_through_the_symbol_it_points_at(self):
        corpus = self.corpus([("e.common.pokemon_summary_screen.sHardyNatureName", "HARDY", "HARDI")])
        entries, _ = join_gen3_dialogue({"gNatureNamePointers[0]": [text("HARDY"), EOS]}, corpus, {},
                                        self.charmap, aliases={"gNatureNamePointers[0]": ("sHardyNatureName",)})
        self.assertEqual((entries[0].status, entries[0].translation), (TRANSLATED, [text("HARDI"), EOS]))

    def test_a_label_the_corpus_lists_twice_takes_the_row_the_rom_reproduces(self):
        corpus = self.corpus([
            ("e.common.union_room.sText_PleaseWait", "Please wait.", "Patientez."),
            ("e.common.berry_blender.sText_PleaseWait", "Please wait a while.", "Veuillez patienter."),
        ])
        entries, _ = join_gen3_dialogue({"sText_PleaseWait": [text("Please wait a while."), EOS]}, corpus, {},
                                        self.charmap)
        self.assertEqual(entries[0].qid, "e.common.berry_blender.sText_PleaseWait")
        self.assertEqual(entries[0].translation, [text("Veuillez patienter."), EOS])
        # rows that both reproduce it and disagree stay unresolved
        corpus = self.corpus([
            ("e.common.a.sText_Same", "Same.", "Un."),
            ("e.common.b.sText_Same", "Same.", "Deux."),
        ], "de")
        entries, _ = join_gen3_dialogue({"sText_Same": [text("Same."), EOS]}, corpus, {}, self.charmap)
        self.assertEqual(entries[0].status, UNRESOLVED)

    def test_an_address_joins_through_pret_symbols_and_keeps_named_placeholders(self):
        corpus = self.corpus([("e.script.Route101.Route101_Text_Help", "Hi [PLAYER][KUN]!", "Salut [PLAYER]!")])
        rom = [text("Hi "), {"t": "player"}, {"t": "ph", "code": 5, "name": "KUN"}, text("!"), EOS]
        entries, _ = join_gen3_dialogue({"g3:08000100": rom}, corpus,
                                        {0x08000100: ["Route101_Text_Help"]}, self.charmap)
        self.assertEqual(entries[0].translation, [text("Salut "), {"t": "player"}, text("!"), EOS])

    def test_identical_and_content_matched_rows(self):
        corpus = self.corpus([
            ("e.common.strings.gText_Ok", "OK", "OK"),
            ("e.common.strings.gText_Bye", "BYE", "SALUT"),
        ])
        entries, _ = join_gen3_dialogue({"gText_Ok": [text("OK"), EOS], "sTable[0]": [text("BYE"), EOS]},
                                        corpus, {}, self.charmap)
        statuses = {entry.key: entry.status for entry in entries}
        self.assertEqual(statuses, {"gText_Ok": SAME_AS_ENGLISH, "sTable[0]": CONTENT_MATCH})

    def test_catalogs_keep_glyph_run_tags_and_spell_the_pkmn_ligature(self):
        corpus = self.corpus([
            ("e.common.items.gItems.1", "[POKEBLOCK] CASE", "BOITE [POKEBLOCK]"),
            ("e.common.item_descriptions.sCaseDesc", "Holds [PKMN].", "Pour [PKMN]."),
            ("e.common.item_descriptions.sOtherDesc", "Holds [PKMN].", "Autre."),
        ])
        names = join_indexed_catalog({1: "{POKEBLOCK} CASE"}, {1: "POKEBLOCK_CASE"}, corpus,
                                     "e.common.items.gItems.", self.charmap)
        self.assertEqual(names.values, {"POKEBLOCK_CASE": "BOITE {POKEBLOCK}"})
        # two rows share the English: the item's own description pointer decides
        descriptions = join_item_descriptions(
            {1: {"name": "{POKEBLOCK} CASE", "description": "Holds POKéMON."}}, {1: "POKEBLOCK_CASE"},
            corpus, self.charmap, own_qids={1: ["e.common.item_descriptions.sCaseDesc"]})
        self.assertEqual(descriptions.values, {"POKEBLOCK_CASE": "Pour POKéMON."})

    def test_engine_strings_fall_back_on_firereds_reviewed_overrides(self):
        corpus = self.corpus([("e.common.strings.gText_Yes", "YES", "OUI")])
        base = self.tmp / "repo"
        for family, entries in (("rse", {"EMERALD ONLY": "EMERAUDE"}),
                                ("frlg", {"MUSIC VOL": "VOL. MUSIQUE", "EMERALD ONLY": "PERDU"})):
            path = base / "overrides" / "fr" / family / "engine.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"entries": {key: {"override": value} for key, value in entries.items()}}),
                            encoding="utf-8")
        scope = {"YES": {"callsite": "x", "qid": "e.common.strings.gText_Yes"},
                 "MUSIC VOL": {"callsite": "x"}, "EMERALD ONLY": {"callsite": "x"}, "VSYNC": {"callsite": "x"}}
        values, stats = join_gen3_engine_strings(scope, corpus, self.charmap, root=base)
        self.assertEqual(values, {"YES": "OUI", "MUSIC VOL": "VOL. MUSIQUE", "EMERALD ONLY": "EMERAUDE"})
        self.assertEqual(stats["details"], {"YES": "corpus", "MUSIC VOL": "game3_override",
                                            "EMERALD ONLY": "rse_override", "VSYNC": "fallback_english"})

    def test_family_configuration_is_read_from_its_own_directories(self):
        base = self.tmp / "repo"
        path = base / "overrides" / "fr" / "rse" / "dialogue.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema": EMERALD.schema("dialogue-overrides"), "version": 1,
                                    "entries": {"k": {"qid": "e.script.x", "text": "t", "reason": "r"}}}),
                        encoding="utf-8")
        self.assertEqual(load_dialogue_overrides("fr", EMERALD, root=base), {
            "k": {"qid": "e.script.x", "text": "t", "reason": "r"}})
        self.assertEqual(load_dialogue_overrides("fr", FRLG, root=base), {})
        path.write_text(json.dumps({"schema": EMERALD.schema("dialogue-overrides"), "version": 1,
                                    "entries": {"k": {"qid": "frlg.script.x", "text": "t", "reason": "r"}}}),
                        encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid Emerald dialogue override"):
            load_dialogue_overrides("fr", EMERALD, root=base)
        with self.assertRaisesRegex(ValueError, "unsupported Emerald edition"):
            load_dialogue_decisions(EMERALD, edition="firered")

    def test_the_player_and_honorific_are_always_filled(self):
        from pipeline.gen3.join import placeholders_supported
        english = [{"t": "text", "s": "Hello!"}, {"t": "eos"}]
        named = [{"t": "player"}, {"t": "ph", "code": 5, "name": "KUN"}, {"t": "text", "s": "!"}, {"t": "eos"}]
        self.assertTrue(placeholders_supported(named, english))
        self.assertFalse(placeholders_supported([{"t": "strvar", "n": 1}, {"t": "eos"}], english))
        self.assertFalse(placeholders_supported([{"t": "ph", "code": 6, "name": "RIVAL"}, {"t": "eos"}], english))

    def test_a_dialogue_override_can_name_a_buffer_the_runtime_fills(self):
        from pipeline.gen3.join import placeholders_supported
        english = [{"t": "strvar", "n": 1}, {"t": "text", "s": "? Certainly."}, {"t": "eos"}]
        target = [{"t": "strvar", "n": 2}, {"t": "text", "s": " de "}, {"t": "strvar", "n": 1}, {"t": "eos"}]
        self.assertFalse(placeholders_supported(target, english))
        self.assertTrue(placeholders_supported(target, english, ["STR_VAR_2"]))
        base = self.tmp / "repo"
        path = base / "overrides" / "fr" / "rse" / "dialogue.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema": EMERALD.schema("dialogue-overrides"), "version": 1, "entries": {
            "k": {"qid": "e.script.x", "text": "t", "reason": "r", "runtime_fills": ["PLAYER"]}}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid Emerald dialogue override"):
            load_dialogue_overrides("fr", EMERALD, root=base)
        path.write_text(json.dumps({"schema": EMERALD.schema("dialogue-overrides"), "version": 1, "entries": {
            "k": {"qid": "e.script.x", "text": "t", "reason": "r", "runtime_fills": "STR_VAR_2"}}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid Emerald dialogue override"):
            load_dialogue_overrides("fr", EMERALD, root=base)


class EmeraldModTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_dialogue_is_split_under_luajits_constant_limit(self):
        dialogue = {f"k{index:05d}": [text("x"), EOS] for index in range(DIALOGUE_FILE_ENTRIES * 2 + 1)}
        files = dialogue_files(dialogue)
        self.assertEqual(list(files), ["dialogue", "dialogue_2", "dialogue_3"])
        self.assertEqual(sum(len(rows) for rows in files.values()), len(dialogue))

    def test_generated_mod_targets_emerald_and_loads_every_dialogue_file(self):
        dialogue = {f"k{index:05d}": [text("x"), EOS] for index in range(DIALOGUE_FILE_ENTRIES + 1)}
        mod = generate_rse_mod(self.tmp / "mod", language="fr", target_name="French",
                               dialogue=dialogue, catalogs={"strings": {"YES": "OUI"}})
        manifest = json.loads((mod / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["id"], manifest["games"]), ("translation-fr-gen3-emerald", ["emerald"]))
        self.assertTrue((mod / "lang" / "dialogue_2.lua").is_file())
        main = (mod / "main.lua").read_text(encoding="utf-8")
        self.assertIn('dialogueFile(part)', main)
        self.assertIn("mod.content.strings:override(id, value)", main)
        with self.assertRaises(ValueError):
            generate_rse_mod(self.tmp / "bad", language="fr", target_name="French", dialogue={},
                             catalogs={"start_menu": {"bag": "SAC"}})

    def test_names(self):
        self.assertEqual(rse_mod_id("ja-Hrkt"), "translation-ja-hrkt-gen3-emerald")
        self.assertEqual(rse_archive_name("fr", "1.0"), "translation-fr-gen3-emerald-1.0.zip")

    def test_text_aliases_name_each_pointer_with_pret_symbols(self):
        (self.tmp / "rse_text_pointers.json").write_text(
            json.dumps({"gNatureNamePointers[0]": 0x08000010, "sUnknown[0]": 0x08000020}), encoding="utf-8")
        self.assertEqual(text_aliases(self.tmp, {0x08000010: ["sHardyNatureName"]}),
                         {"gNatureNamePointers[0]": ("sHardyNatureName",)})

    def test_gate_expectations_sample_every_emerald_consumer(self):
        joined = {
            "dialogue": {
                "g3:08000010": [text("Salut "), {"t": "player"}, text(" !"), EOS],
                "STRINGID_ATTACKMISSED": [text("Raté !"), EOS],
                "gNatureNamePointers[0]": [text("HARDI"), EOS],
            },
            "catalogs": {"species_names": {"TREECKO": "ARCKO"}, "strings": {
                "YES": "OUI", "easyChat.word[2560]": "BONJOUR",
                "A highly appealing move.": "Une démonstration qui\nplaît énormément."}},
            "numbers": {"species": {252: "TREECKO"}, "items": {}, "moves": {}},
            "scope": {"A highly appealing move.": {
                "callsite": "src/core/game3/summary_data.lua (SummaryData.contestEffectDescription)"}},
        }
        expectations = write_gate_expectations(self.tmp / "gate.json", joined)
        self.assertEqual(expectations["dialogue"]["key"], "g3:08000010")
        self.assertEqual(expectations["battle"], {"key": "STRINGID_ATTACKMISSED", "value": "Raté !"})
        self.assertEqual(expectations["natures"], [{"id": 0, "value": "HARDI"}])
        self.assertEqual(expectations["species_names"]["number"], 252)
        self.assertEqual(expectations["easy_chat"], {"id": 2560, "value": "BONJOUR"})
        self.assertEqual(expectations["hooks"]["contest"]["value"], "Une démonstration qui\nplaît énormément.")


class LabelRowTests(unittest.TestCase):
    def test_labels_with_their_own_row_keep_it_after_the_migration(self):
        entries = [
            Gen3DialogueEntry("gText_Cancel", TRANSLATED, [text("CANCEL"), EOS], translation=[text("SORTIR"), EOS]),
            Gen3DialogueEntry("gText_Cancel7", SAME_AS_ENGLISH, [text("CANCEL"), EOS]),
            Gen3DialogueEntry("gText_Hi", SAME_AS_ENGLISH, [text("Hi "), {"t": "player"}, EOS]),
            Gen3DialogueEntry("gText_Lost", UNRESOLVED, [text("CANCEL"), EOS]),
            Gen3DialogueEntry("g3:08000010", TRANSLATED, [text("CANCEL"), EOS], translation=[text("X"), EOS]),
        ]
        rows = dialogue_label_rows(entries)
        self.assertEqual(rows, {"gText_Cancel": None, "gText_Cancel7": "CANCEL", "gText_Hi": None})

        from pipeline.gen3 import mod as gen3_mod

        class Modkit:
            @staticmethod
            def rom_text_caches(repo):
                return [(str(Path(repo) / "emerald" / "text.lua"), "")]

            @staticmethod
            def harvest_rom_text(repo, caches):
                return []

            @staticmethod
            def harvest_engine_strings(repo):
                return [('"CANCEL"', "x")]

            @staticmethod
            def migrate_rom_text(done, rom_text, engine_keys):
                for label in ('"gText_Cancel"', '"gText_Cancel7"', '"gText_Lost"'):
                    done[label] = done['"CANCEL"']

            @staticmethod
            def rom_english_keys(rom_text):
                return set()

        with tempfile.TemporaryDirectory() as directory, \
                unittest.mock.patch.object(gen3_mod, "_modkit", return_value=Modkit):
            catalog, _ = gen3_mod.rom_label_strings({"CANCEL": "RETOUR"}, Path(directory), luajit="luajit",
                                                    label_rows=rows)
        # the menu's own row wins, the cart's English stays English, a label
        # without a row of its own keeps the migrated value
        self.assertEqual(catalog, {"CANCEL": "RETOUR", "gText_Cancel7": "CANCEL", "gText_Lost": "RETOUR"})

    def test_a_context_or_callsite_family_overrides_a_kept_row(self):
        from pipeline.gen3.engine_scope import required_family
        self.assertEqual(required_family("DARK", "src/ui/game3/rse/pokedex.lua (Pokédex entry category)"),
                         ".gPokedexEntries.")
        self.assertEqual(required_family("easyChat.GREETINGS|HELLO", "x"), ".easy_chat_group_greetings.")
        self.assertIsNone(required_family("DARK", "src/core/game3/battle/ui.lua (Types.get)"))

    def test_a_kept_row_keeps_its_reviewed_alternatives(self):
        from pipeline.gen3 import engine_scope
        keys = {"TEXT SPEED": {"callsite": "src/ui/game3/rse/options.lua (option label)"}}
        previous = {"TEXT SPEED": {"qid": "e.common.strings.gText_TextSpeed",
                                   "alternatives": ["e.common.strings.gText_TextSpeed2"]}}
        with unittest.mock.patch.object(engine_scope, "collect_keys", return_value=keys), \
                unittest.mock.patch.object(engine_scope, "load_gen3_corpus", return_value=None), \
                unittest.mock.patch.object(engine_scope, "corpus_index", return_value={}), \
                unittest.mock.patch.object(engine_scope, "derive_fills", return_value={}):
            scope = engine_scope.build_scope(Path("engine"), Path("corpus"), None, extracted=None,
                                             previous=previous, family=EMERALD)
        row = scope["keys"]["TEXT SPEED"]
        self.assertEqual(row["qid"], "e.common.strings.gText_TextSpeed")
        self.assertEqual(row["alternatives"], ["e.common.strings.gText_TextSpeed2"])


class EmeraldConfigTests(unittest.TestCase):
    def test_release_profile_and_selection(self):
        profile = release_profile("rse")
        self.assertEqual((profile.generation, profile.games), (3, ("emerald",)))
        self.assertEqual(game_spec("emerald").corpus_collection, "Emerald")
        self.assertEqual([code for code, _ in languages_for_collection("Emerald")],
                         ["fr", "de", "es", "it", "ja-Hrkt"])
        self.assertEqual(release_profile_for_selection(3).id, "frlg")
        self.assertEqual(release_profile_for_selection(4).id, "rse")
        self.assertEqual(generation_label(4), "Emerald (generation 3)")
        self.assertEqual(generation_code("Emerald (generation 3)"), 4)

    def test_only_the_canonical_rom_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "emerald.gba"
            rom.write_bytes(b"not a rom")
            with self.assertRaisesRegex(ValueError, "Emerald ROM SHA-1 mismatch"):
                verify_emerald_rom(rom)

    def test_checked_in_config_loads(self):
        scope = load_engine_scope(EMERALD)
        self.assertIn("TEXT SPEED", scope)
        self.assertIn("easyChat.GREETINGS|HELLO", scope)
        # every Pokédex category is its own entry's row, never a menu label's,
        # a type's or a move's with the same English
        categories = {key: row.get("qid", "") for key, row in scope.items()
                      if row["callsite"].endswith("(Pokédex entry category)")}
        self.assertGreater(len(categories), 200)
        self.assertEqual({key: qid for key, qid in categories.items() if ".gPokedexEntries." not in qid}, {})
        self.assertTrue(all(row.get("qid", "e.").startswith("e.") for row in scope.values()))
        # shared descriptions take the row whose wording fits every entry
        self.assertEqual(scope["Negates weather effects."]["qid"], "e.common.abilities.sCloudNineDescription")
        self.assertEqual(scope["Sludge is hurled to inflict\ndamage. May also poison."]["qid"],
                         "e.common.move_descriptions.sSludgeDescription")
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            with self.subTest(language=language):
                engine = json.loads((ROOT / "overrides" / language / "rse" / "engine.json").read_text(encoding="utf-8"))
                for key, row in engine["entries"].items():
                    self.assertIn(key, scope)
                    self.assertTrue(row.get("reason") and row.get("provenance"), key)
        self.assertIn("g3:081f50eb", load_dialogue_overrides("fr", EMERALD))
        # the TM shop prompt ships the official rows in every language, the
        # move named by the buffer the runtime fills
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            row = load_dialogue_overrides(language, EMERALD)["gText_Var1CertainlyHowMany2"]
            self.assertEqual(row["runtime_fills"], ["STR_VAR_2"], language)
        japanese = load_dialogue_overrides("ja-Hrkt", EMERALD)
        # the particle and ending of the moves outside sGrammarMoveUsedTable
        self.assertEqual(japanese["sText_AttackerUsedX"]["text"], "[B_ATK_NAME_WITH_PREFIX]の\\n[B_BUFF2]")
        self.assertEqual(japanese["sText_ExclamationMark"]["text"], "！")
        self.assertEqual((japanese["gText_ExpandedPlaceholder_Kun"]["text"],
                          japanese["gText_ExpandedPlaceholder_Chan"]["text"]), ("くん", "ちゃん"))

    def test_japanese_battle_menus_split_into_the_four_options_the_engine_draws(self):
        # gen1recomp splits a battle menu row into its options at each line
        # break and CLEAR_TO (src/core/game3/battle/ui.lua:2097); the
        # Japanese cart aligns them with ideographic spaces instead
        japanese = load_dialogue_overrides("ja-Hrkt", EMERALD)
        menus = {"gText_BattleMenu": ["たたかう", "バッグ", "ポケモン", "にげる"],
                 "gText_SafariZoneMenu": ["ボール", "ポロック", "ちかづく", "にげる"]}
        for label, options in menus.items():
            text = re.sub(r"^(\[[A-Z_]+[^\]]*\])+", "", japanese[label]["text"])
            self.assertEqual(re.split(r"\[CLEAR_TO 56\]|\\n", text), options, label)
        # the action prompt breaks after は, as every other cart's row does
        for label in ("gText_WhatWillPkmnDo", "gText_WhatWillPkmnDo2", "gText_WhatWillWallyDo"):
            self.assertRegex(japanese[label]["text"], r"は\\nどうする？$", label)

    def test_engine_scope_matches_the_pinned_engine(self):
        engine = ROOT / ".cache" / "dependencies" / "gen1recomp"
        extracted = ROOT / ".cache" / "emerald" / "extracted" / "cache"
        if not (engine / "src").is_dir() or not shutil.which("luajit") or not (extracted / "data").is_dir():
            self.skipTest("pinned gen1recomp checkout, LuaJIT or Emerald extract unavailable")
        from pipeline.gen3.engine_scope import collect_keys
        keys = collect_keys(engine, extracted, EMERALD)
        scope = load_engine_scope(EMERALD)
        self.assertEqual(sorted(set(keys) - set(scope)), [], "engine keys missing from the scope")
        self.assertEqual(sorted(set(scope) - set(keys)), [], "scope keys the engine no longer has")

    def test_engine_strings_resolve_in_every_language(self):
        corpus = ROOT / ".cache" / "dependencies" / "poke-corpus" / "corpus" / "Emerald"
        charmap_path = ROOT / ".cache" / "dependencies" / "pret" / "emerald_charmap" / "charmap.txt"
        if not (corpus / "qid_msg.txt").is_file() or not charmap_path.is_file():
            self.skipTest("pinned Emerald corpus or pret charmap unavailable")
        charmap = load_charmap(charmap_path, EMERALD.dialect)
        # engine rows with neither an Emerald corpus row nor a reviewed
        # override (docs/upstream-fixes.md, Emerald entry 2)
        expected = {
            "CHECK_TAG", "ESC/2ND CANCELS", "OPEN", "PRESS A BUTTON", "RELEASE TO SET", "SPECIAL AREA",
            "easyChat.EVENTS|{POKEBLOCK}",
        }
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            with self.subTest(language=language):
                loaded = load_gen3_corpus(corpus, language, EMERALD)
                _values, stats = join_gen3_engine_strings(load_engine_scope(EMERALD), loaded, charmap)
                self.assertEqual(stats["fallback_english"], sorted(expected))


if __name__ == "__main__":
    unittest.main()
