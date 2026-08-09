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

    def test_show_text_before_the_first_trigger_stays_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "show_text", "_ChallengeText" },\n'
                '  { "rival_battle", "OPP_RIVAL1", 1 },\n'
                '  { "show_text", "_VictoryTauntText" },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = battle_adjacent_text_keys(directory)
            # The opening challenge, shown before the fight even starts, is
            # the same kind of plain pre-fight TextBox call as
            # trainer_headers.lua's `battle` field: stays eligible.
            self.assertNotIn("_ChallengeText", keys)
            # Anything from the trigger onward (post-battle reaction) is
            # conservatively excluded, however far from the opcode it sits.
            self.assertIn("_VictoryTauntText", keys)

    def test_far_show_text_after_the_trigger_is_still_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            lines = ['return {', '  { "rival_battle", "OPP_RIVAL1", 1 },']
            for i in range(10):
                lines.append(f'  {{ "face_object", {i}, "up" }},')
            lines.append('  { "show_text", "_FarAfterText" },')
            lines.append('}')
            script.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertIn("_FarAfterText", battle_adjacent_text_keys(directory))

    def test_scene_reset_ends_the_battle_zone(self):
        # Regression: a big script file (data/scripts/story.lua) routinely
        # concatenates several unrelated encounters. Once the NPC leaves
        # (hide_object) or the screen changes (warp/fade), a later
        # show_text is a new, unrelated story beat, not this battle's
        # aftermath -- however close it sits in the file.
        for reset_opcode in ("hide_object", "warp", "fade"):
            with tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / "a.lua"
                script.write_text(
                    'return {\n'
                    '  { "rival_battle", "OPP_RIVAL1", 1 },\n'
                    '  { "show_text", "_DefeatedText" },\n'
                    f'  {{ "{reset_opcode}", "SOME_MAP" }},\n'
                    '  { "show_text", "_UnrelatedLaterText" },\n'
                    '}\n',
                    encoding="utf-8",
                )
                keys = battle_adjacent_text_keys(directory)
                self.assertIn("_DefeatedText", keys, reset_opcode)
                self.assertNotIn("_UnrelatedLaterText", keys, reset_opcode)

    def test_a_second_trigger_reopens_the_zone_after_a_reset(self):
        # Two independent battles in one file (e.g. story5.lua's Route 22
        # then Cerulean City rival fights): each gets its own zone.
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'return {\n'
                '  { "rival_battle", "OPP_RIVAL1", 1 },\n'
                '  { "show_text", "_FirstDefeatedText" },\n'
                '  { "hide_object", "ROUTE_22" },\n'
                '  { "show_text", "_SecondChallengeText" },\n'
                '  { "rival_battle", "OPP_RIVAL1", 1 },\n'
                '  { "show_text", "_SecondDefeatedText" },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = battle_adjacent_text_keys(directory)
            self.assertIn("_FirstDefeatedText", keys)
            self.assertNotIn("_SecondChallengeText", keys)  # pre-fight again, before the 2nd trigger
            self.assertIn("_SecondDefeatedText", keys)

    def test_save_end_battle_text_is_captured_regardless_of_position(self):
        # save_end_battle_text *registers* text for the battle to show once
        # it ends; it's routinely written before start_battle in the script
        # (yellow_jessie_james.lua), so unlike show_text its position
        # relative to the trigger doesn't matter.
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

    def test_battle_opcode_inside_table_insert_is_still_found(self):
        # Regression: data/scripts/oaks_lab.lua builds its rival encounter
        # via table.insert(rows, { "start_battle", ... }), which doesn't
        # start the line with `{` the way a bare list entry does. A scan
        # anchored to line-start silently missed this file's battle opcode
        # entirely, so the post-battle reaction below was never recognized
        # as battle-adjacent either.
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text(
                'local rows = {\n'
                '  { "show_text", "_ChallengeText" },\n'
                '}\n'
                'table.insert(rows, { "start_battle", "trainer", "OPP_RIVAL1", party })\n'
                'table.insert(rows, { "show_text", "_VictoryTauntText" })\n',
                encoding="utf-8",
            )
            keys = battle_adjacent_text_keys(directory)
            self.assertNotIn("_ChallengeText", keys)
            self.assertIn("_VictoryTauntText", keys)

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
        # A taunt shown before the rival battle actually triggers (a
        # separate onStep coordinate check) -- shown via show_text earlier
        # in oaks_lab.lua than the table.insert(rows, {"start_battle",...})
        # line, so it's the same "opening challenge" shape as
        # OaksLabRivalIllTakeYouOnText below: stays eligible.
        self.assertFalse(is_excluded_qid("rb.OaksLab.OaksLabRivalMyPokemonLooksStrongerText", keys))
        # The rival's actual pre-fight challenge line, also before the
        # table.insert-hidden start_battle opcode in the same file --
        # confirms the table.insert fix restores detection of the file's
        # battle opcode without over-excluding what comes before it.
        self.assertFalse(is_excluded_qid("y.OaksLab.OaksLabRivalIllTakeYouOnText", keys))
        # The reported case: Oak's Pikachu-dislikes-Poké-Balls speech,
        # scripted after the same rival battle in oaks_lab_yellow.lua but
        # past the rival's hide_object (he's walked off-screen by then) --
        # unrelated to the fight, now correctly eligible again.
        self.assertFalse(is_excluded_qid("y.OaksLab.OaksLabPikachuDislikesPokeballsText1", keys))
        self.assertFalse(is_excluded_qid("y.OaksLab.OaksLabPikachuDislikesPokeballsText2", keys))
        # The rival's actual parting line right after that same battle,
        # before hide_object fires: still correctly excluded.
        self.assertTrue(is_excluded_qid("rb.OaksLab.OaksLabRivalSmellYouLaterText", keys))


if __name__ == "__main__":
    unittest.main()
