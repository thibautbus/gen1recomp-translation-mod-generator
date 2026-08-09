import tempfile
import unittest
from pathlib import Path

from pipeline.battle_scope import (
    battle_adjacent_text_keys, dynamic_text_lookup_keys, gen1recomp_root,
    is_excluded_qid, trainer_won_text_keys,
)

GEN1RECOMP_SRC = Path("/home/thibaut/code/perso/gen1recomp/src")


class BattleScopeTests(unittest.TestCase):
    def test_missing_scripts_dir_returns_empty_set(self):
        self.assertEqual(battle_adjacent_text_keys("/no/such/directory"), set())

    def test_file_without_any_battle_opcode_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "show_text", "_JustTalkingText" },\n'
                '  { "face_object", 1, "down" },\n'
                '}\n',
                encoding="utf-8",
            )
            self.assertEqual(battle_adjacent_text_keys(directory), set())

    def test_direct_opcode_argument_is_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "save_end_battle_text", "_SomeJessieJamesText3" },\n'
                '  { "start_battle", "trainer", "OPP_ROCKET", 1 },\n'
                '}\n',
                encoding="utf-8",
            )
            self.assertIn("_SomeJessieJamesText3", battle_adjacent_text_keys(directory))

    def test_whole_file_is_excluded_once_it_contains_a_battle_opcode(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            lines = ['return {', '  { "show_text", "_FarBeforeText" },']
            for i in range(10):
                lines.append(f'  {{ "face_object", {i}, "down" }},')
            lines.append('  { "rival_battle", "OPP_RIVAL1", 1 },')
            for i in range(10):
                lines.append(f'  {{ "face_object", {i}, "up" }},')
            lines.append('  { "show_text", "_FarAfterText" },')
            lines.append('}')
            script.write_text("\n".join(lines) + "\n", encoding="utf-8")
            keys = battle_adjacent_text_keys(directory)
            # No proximity window anymore: a battle opcode anywhere in the
            # file puts every show_text in that file out of scope, however
            # far away it sits.
            self.assertIn("_FarBeforeText", keys)
            self.assertIn("_FarAfterText", keys)

    def test_literal_inline_text_argument_is_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "start_battle", "trainer", "OPP_PROF_OAK", 1 },\n'
                '  { "show_text", "OAK: Impressive!" },\n'
                '}\n',
                encoding="utf-8",
            )
            self.assertIn("OAK: Impressive!", battle_adjacent_text_keys(directory))

    def test_gen1recomp_root_resolves_sibling_of_src(self):
        self.assertEqual(gen1recomp_root(Path("/x/gen1recomp/src")), Path("/x/gen1recomp"))
        self.assertEqual(gen1recomp_root(Path("/x/gen1recomp")), Path("/x/gen1recomp"))

    def test_dynamic_text_lookup_keys_scans_all_of_src_not_just_battle(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "src"
            (src / "battle").mkdir(parents=True)
            (src / "ui").mkdir(parents=True)
            (src / "battle" / "BattleState.lua").write_text(
                'local raw = (self.data.text and self.data.text._Rival1WinText)\n',
                encoding="utf-8",
            )
            (src / "ui" / "PartyMenu.lua").write_text(
                'return self.game.data.text._PartyMenuBattleText\n',
                encoding="utf-8",
            )
            keys = dynamic_text_lookup_keys(src)
            self.assertEqual(keys, {"_Rival1WinText", "_PartyMenuBattleText"})

    def test_dynamic_text_lookup_keys_missing_dir_returns_empty_set(self):
        self.assertEqual(dynamic_text_lookup_keys("/no/such/src"), set())

    def test_trainer_won_text_keys_reads_only_the_won_field(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "src"
            (src.parent / "data" / "generated").mkdir(parents=True)
            (src.parent / "data" / "generated" / "trainer_headers.lua").write_text(
                'return {\n'
                '  AgathasRoom = {\n'
                '    [1] = {\n'
                '      after = "_AgathaAfterBattleText",\n'
                '      battle = "_AgathaBeforeBattleText",\n'
                '      won = "_AgathaEndBattleText",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = trainer_won_text_keys(src)
            self.assertEqual(keys, {"_AgathaEndBattleText"})

    def test_trainer_won_text_keys_missing_file_returns_empty_set(self):
        self.assertEqual(trainer_won_text_keys("/no/such/src"), set())

    def test_is_excluded_qid_matches_runtime_symbol(self):
        excluded = {"_CeruleanCityRivalDefeatedText"}
        self.assertTrue(is_excluded_qid("rb.CeruleanCity.CeruleanCityRivalDefeatedText", excluded))
        self.assertFalse(is_excluded_qid("rb.PalletTown.PalletTownSomeOtherText", excluded))

    @unittest.skipUnless(GEN1RECOMP_SRC.parent.is_dir(), "gen1recomp checkout not available")
    def test_real_checkout_flags_known_battle_adjacent_qids(self):
        keys = (battle_adjacent_text_keys(GEN1RECOMP_SRC.parent / "data" / "scripts")
                | dynamic_text_lookup_keys(GEN1RECOMP_SRC)
                | trainer_won_text_keys(GEN1RECOMP_SRC))
        self.assertTrue(is_excluded_qid("rb.CeruleanCity.CeruleanCityRivalDefeatedText", keys))
        self.assertTrue(is_excluded_qid("rb.ChampionsRoom.ChampionsRoomRivalAfterBattleText", keys))
        # Read directly off the dialogue table by BattleState.lua at
        # runtime (self.data.text._Rival1WinText), invisible to the
        # scripted-opcode scan: this is what dynamic_text_lookup_keys
        # specifically exists to catch.
        self.assertTrue(is_excluded_qid("y.text_2.Rival1WinText", keys))
        # A generic trainer's defeat line: shown on the battle screen via
        # PrintEndBattleText, the systematic source *_won_text_keys covers.
        self.assertTrue(is_excluded_qid("rb.AgathasRoom.AgathaEndBattleText", keys))
        # That same trainer's pre-fight challenge line renders on the
        # ordinary field TextBox, before the battle screen even opens.
        self.assertFalse(is_excluded_qid("rb.AgathasRoom.AgathaBeforeBattleText", keys))
        # A plain pre-battle field taunt triggered by a separate onStep
        # coordinate check, not an inline battle opcode: stays eligible.
        self.assertFalse(is_excluded_qid("rb.OaksLab.OaksLabRivalMyPokemonLooksStrongerText", keys))


if __name__ == "__main__":
    unittest.main()
