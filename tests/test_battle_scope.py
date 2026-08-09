import tempfile
import unittest
from pathlib import Path

from pipeline.battle_scope import battle_adjacent_text_keys, battle_module_dynamic_keys, gen1recomp_root, is_excluded_qid

GEN1RECOMP_SRC = Path("/home/thibaut/code/perso/gen1recomp/src")


class BattleScopeTests(unittest.TestCase):
    def test_missing_scripts_dir_returns_empty_set(self):
        self.assertEqual(battle_adjacent_text_keys("/no/such/directory"), set())

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
            keys = battle_adjacent_text_keys(directory)
            self.assertIn("_SomeJessieJamesText3", keys)

    def test_show_text_within_window_of_trigger_is_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "show_text", "_PreBattleTauntText" },\n'
                '  { "rival_battle", "OPP_RIVAL1", 1 },\n'
                '  { "jump_if_false", "end" },\n'
                '  { "show_text", "_VictoryTauntText" },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = battle_adjacent_text_keys(directory, window=4)
            self.assertIn("_PreBattleTauntText", keys)
            self.assertIn("_VictoryTauntText", keys)

    def test_show_text_far_from_any_trigger_is_not_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            lines = ['return {']
            lines.append('  { "show_text", "_UnrelatedFieldTauntText" },')
            for i in range(10):
                lines.append(f'  {{ "face_object", {i}, "down" }},')
            lines.append('  { "rival_battle", "OPP_RIVAL1", 1 },')
            lines.append('}')
            script.write_text("\n".join(lines) + "\n", encoding="utf-8")
            keys = battle_adjacent_text_keys(directory, window=4)
            self.assertNotIn("_UnrelatedFieldTauntText", keys)

    def test_literal_inline_text_argument_is_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "start_battle", "trainer", "OPP_PROF_OAK", 1 },\n'
                '  { "jump_if_false", "end" },\n'
                '  { "show_text", "OAK: Impressive!" },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = battle_adjacent_text_keys(directory)
            self.assertIn("OAK: Impressive!", keys)

    def test_gen1recomp_root_resolves_sibling_of_src(self):
        self.assertEqual(gen1recomp_root(Path("/x/gen1recomp/src")), Path("/x/gen1recomp"))
        self.assertEqual(gen1recomp_root(Path("/x/gen1recomp")), Path("/x/gen1recomp"))

    def test_battle_module_dynamic_keys_reads_data_text_lookups(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "src"
            (src / "battle").mkdir(parents=True)
            (src / "battle" / "BattleState.lua").write_text(
                'local raw = (self.data.text and self.data.text._Rival1WinText)\n'
                '  or Strings("{RIVAL}: Yeah! Am\\nI great or what?")\n',
                encoding="utf-8",
            )
            keys = battle_module_dynamic_keys(src)
            self.assertEqual(keys, {"_Rival1WinText"})

    def test_battle_module_dynamic_keys_missing_dir_returns_empty_set(self):
        self.assertEqual(battle_module_dynamic_keys("/no/such/src"), set())

    def test_is_excluded_qid_matches_runtime_symbol(self):
        excluded = {"_CeruleanCityRivalDefeatedText"}
        self.assertTrue(is_excluded_qid("rb.CeruleanCity.CeruleanCityRivalDefeatedText", excluded))
        self.assertFalse(is_excluded_qid("rb.PalletTown.PalletTownSomeOtherText", excluded))

    @unittest.skipUnless(GEN1RECOMP_SRC.parent.is_dir(), "gen1recomp checkout not available")
    def test_real_checkout_flags_known_battle_adjacent_qids(self):
        keys = (battle_adjacent_text_keys(GEN1RECOMP_SRC.parent / "data" / "scripts")
                | battle_module_dynamic_keys(GEN1RECOMP_SRC))
        self.assertTrue(is_excluded_qid("rb.CeruleanCity.CeruleanCityRivalDefeatedText", keys))
        self.assertTrue(is_excluded_qid("rb.ChampionsRoom.ChampionsRoomRivalAfterBattleText", keys))
        # Read directly off the dialogue table by BattleState.lua at
        # runtime (self.data.text._Rival1WinText), invisible to the
        # scripted-opcode scan: this is what the *_module_dynamic_keys
        # signal specifically exists to catch.
        self.assertTrue(is_excluded_qid("y.text_2.Rival1WinText", keys))
        # A plain pre-battle field taunt triggered by a separate onStep
        # coordinate check, not an inline battle opcode: stays eligible.
        self.assertFalse(is_excluded_qid("rb.OaksLab.OaksLabRivalMyPokemonLooksStrongerText", keys))


if __name__ == "__main__":
    unittest.main()
