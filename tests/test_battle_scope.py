import tempfile
import unittest
from pathlib import Path

from pipeline.battle_scope import (
    _script_entries, _symbol_forms, battle_adjacent_text_keys, dex_entry_text_keys,
    dynamic_text_lookup_keys, field_dialogue_text_keys, gen1recomp_root, is_excluded_qid,
    is_reflow_safe_qid, map_text_pointer_keys, reflow_safe_keys, script_dialogue_text_keys,
    sign_title_text_keys, trainer_challenge_text_keys, trainer_won_text_keys,
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

    def test_script_entries_finds_every_opcode_on_a_shared_line(self):
        # Regression: a search()-first-match-only scan silently dropped a
        # second entry sharing a line with a preceding one (e.g.
        # data/scripts/yellow_jessie_james.lua's
        # '{ "face_player" }, { "show_text", "..." },'), which could hide a
        # real battle trigger or show_text from the zone state machine.
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "a.lua"
            script.write_text('{ "face_player" }, { "show_text", "_SomeText" },\n', encoding="utf-8")
            self.assertEqual(
                _script_entries(script),
                [("face_player", None), ("show_text", "_SomeText")],
            )

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

    def test_trainer_won_text_keys_merges_red_blue_and_yellow_layers(self):
        # Regression: Red, Blue and Yellow each import their own ROM into a
        # separate trainer_headers.lua (pipeline.builder.build's
        # import_rom); a defeat line that only exists in one layer (e.g.
        # four real Yellow-only encounters -- Rocket Hideout B4F, Route 9,
        # two Viridian Forest trainers) must still be excluded, not just
        # the ones Red happens to also have.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "trainer_headers.lua").write_text(
                'return { Room = { [1] = { won = "_RedOnlyEndBattleText" } } }\n',
                encoding="utf-8",
            )
            (root / "blue" / "data" / "generated").mkdir(parents=True)
            (root / "blue" / "data" / "generated" / "trainer_headers.lua").write_text(
                'return { Room = { [1] = { won = "_BlueOnlyEndBattleText" } } }\n',
                encoding="utf-8",
            )
            (root / "yellow" / "data" / "generated").mkdir(parents=True)
            (root / "yellow" / "data" / "generated" / "trainer_headers.lua").write_text(
                'return { Room = { [1] = { won = "_ViridianForestYoungster5EndBattleText" } } }\n',
                encoding="utf-8",
            )
            keys = trainer_won_text_keys(src)
            self.assertEqual(
                keys,
                {"_RedOnlyEndBattleText", "_BlueOnlyEndBattleText", "_ViridianForestYoungster5EndBattleText"},
            )

    def test_trainer_won_text_keys_tolerates_a_missing_yellow_layer(self):
        # A build without a Yellow ROM has no yellow/data/generated at all
        # -- must not raise, just contribute nothing from that layer.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "trainer_headers.lua").write_text(
                'return { Room = { [1] = { won = "_RedOnlyEndBattleText" } } }\n',
                encoding="utf-8",
            )
            keys = trainer_won_text_keys(src)
            self.assertEqual(keys, {"_RedOnlyEndBattleText"})

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

    def test_symbol_forms_normalizes_bare_and_prefixed(self):
        self.assertEqual(_symbol_forms({"RouteSignText"}), {"RouteSignText", "_RouteSignText"})
        self.assertEqual(_symbol_forms({"_RouteSignText"}), {"RouteSignText", "_RouteSignText"})

    def test_map_text_pointer_keys_reads_labels_in_both_forms(self):
        # Regression: data/generated/text_pointers.lua's "label" field is
        # bare (no leading underscore), unlike the raw runtime-symbol keys
        # a Modkit-worksheet-built catalog checks membership against
        # directly -- without normalization this source silently failed to
        # match any real qid.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "text_pointers.lua").write_text(
                'return {\n'
                '  Route17 = {\n'
                '    TEXT_ROUTE17_SIGN = {\n'
                '      label = "Route17SignText",\n'
                '      text = "_Route17SignText",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = map_text_pointer_keys(src)
            self.assertEqual(keys, {"Route17SignText", "_Route17SignText"})

    def test_map_text_pointer_keys_trusts_text_over_a_diverging_label(self):
        # Regression: an "asm = true" pointer not yet migrated off the raw
        # ROM text stream can have a "label" that's just a provenance
        # naming guess, not an actual symbol in text.lua -- e.g. the real
        # TEXT_PALLETTOWN_OAK entry's label is "PalletTownOakText" (which
        # doesn't exist anywhere in text.lua) while its real, displayed
        # symbol is "_PalletTownOakHeyWaitDontGoOutText" (the "text"
        # field). Reading only "label" silently missed every qid like
        # this one.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "text_pointers.lua").write_text(
                'return {\n'
                '  PalletTown = {\n'
                '    TEXT_PALLETTOWN_OAK = {\n'
                '      asm = true,\n'
                '      label = "PalletTownOakText",\n'
                '      text = "_PalletTownOakHeyWaitDontGoOutText",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = map_text_pointer_keys(src)
            self.assertIn("_PalletTownOakHeyWaitDontGoOutText", keys)
            self.assertIn("PalletTownOakHeyWaitDontGoOutText", keys)

    def test_field_dialogue_text_keys_reads_named_fields_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "field.lua").write_text(
                'return {\n'
                '  badgeGates = {\n'
                '    ROUTE_22_GATE = {\n'
                '      failText = "Route22GateGuardNoBoulderbadgeText",\n'
                '      text = "Route22GateGuardText",\n'
                '    },\n'
                '  },\n'
                '  signs = {\n'
                '    afterText = "ViridianCityOldManYouNeedToWeakenTheTargetText",\n'
                '    textFacing = "left",\n'
                '    gamefreakText = {\n'
                '      path = "assets/generated/intro/gamefreak_text.png",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = field_dialogue_text_keys(src)
            self.assertIn("Route22GateGuardText", keys)
            self.assertIn("_Route22GateGuardText", keys)
            self.assertIn("Route22GateGuardNoBoulderbadgeText", keys)
            self.assertIn("ViridianCityOldManYouNeedToWeakenTheTargetText", keys)
            # "textFacing" (a direction word) and "gamefreakText" (an intro
            # image asset, not even a string value) share the "text"
            # substring but aren't text pointers.
            self.assertNotIn("left", keys)
            self.assertNotIn("_left", keys)
            self.assertNotIn("assets/generated/intro/gamefreak_text.png", keys)

    def test_dex_entry_text_keys_reads_pokemon_dex_entries(self):
        # dex_entry_text_keys itself still finds these (a structural
        # inventory, and DexEntryMenu.lua's own qid -> symbol mapping is
        # real) -- it's simply not fed into reflow_safe_keys' positive
        # union (see test_reflow_safe_keys_never_includes_dex_entries).
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "pokemon.lua").write_text(
                'return {\n'
                '  ABRA = {\n'
                '    dex = 63,\n'
                '    dexEntry = {\n'
                '      heightFt = 2,\n'
                '      kind = "PSI",\n'
                '      text = "_AbraDexEntry",\n'
                '      weight = 430,\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            self.assertIn("_AbraDexEntry", dex_entry_text_keys(src))

    def test_reflow_safe_keys_never_includes_dex_entries(self):
        # DexEntryMenu.lua renders through a third display path (no
        # pixel-width wrap of its own, silently truncates past a fixed
        # ~6-line budget) that pipeline.text_pacing doesn't model -- a
        # reflowed entry could come out with a different line count than
        # the original and get cut off. dex_entry_text_keys finding the
        # symbol must not be enough to make it reflow-eligible.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            (src / "battle").mkdir(parents=True)
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "scripts").mkdir(parents=True)
            (root / "data" / "generated" / "pokemon.lua").write_text(
                'return {\n'
                '  ABRA = { dexEntry = { text = "_AbraDexEntry" } },\n'
                '}\n',
                encoding="utf-8",
            )
            safe = reflow_safe_keys(src)
            self.assertNotIn("_AbraDexEntry", safe)
            self.assertNotIn("AbraDexEntry", safe)

    def test_sign_title_text_keys_reads_sign_constants_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "text_pointers.lua").write_text(
                'return {\n'
                '  PalletTown = {\n'
                '    TEXT_PALLETTOWN_SIGN = {\n'
                '      label = "PalletTownSignText",\n'
                '      text = "_PalletTownSignText",\n'
                '    },\n'
                '    TEXT_PALLETTOWN_OAK = {\n'
                '      label = "PalletTownOakText",\n'
                '      text = "_PalletTownOakHeyWaitDontGoOutText",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            keys = sign_title_text_keys(src)
            self.assertIn("_PalletTownSignText", keys)
            self.assertNotIn("_PalletTownOakHeyWaitDontGoOutText", keys)

    def test_reflow_safe_keys_never_includes_signs(self):
        # A sign is still an ordinary field text pointer (map_text_pointer_keys
        # finds it too), but opens with a title line by design -- reflow
        # would run it into the following prose. sign_title_text_keys must
        # carve it back out of the positive union.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            (src / "battle").mkdir(parents=True)
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "scripts").mkdir(parents=True)
            (root / "data" / "generated" / "text_pointers.lua").write_text(
                'return {\n'
                '  PalletTown = {\n'
                '    TEXT_PALLETTOWN_SIGN = {\n'
                '      label = "PalletTownSignText",\n'
                '      text = "_PalletTownSignText",\n'
                '    },\n'
                '  },\n'
                '}\n',
                encoding="utf-8",
            )
            safe = reflow_safe_keys(src)
            self.assertNotIn("_PalletTownSignText", safe)
            self.assertNotIn("PalletTownSignText", safe)

    def test_trainer_challenge_text_keys_reads_battle_and_after_not_won(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "generated" / "trainer_headers.lua").write_text(
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
            keys = trainer_challenge_text_keys(src)
            self.assertEqual(keys, {"_AgathaAfterBattleText", "_AgathaBeforeBattleText"})

    def test_script_dialogue_text_keys_is_the_complement_of_battle_adjacent(self):
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
            safe = script_dialogue_text_keys(directory)
            battle = battle_adjacent_text_keys(directory)
            self.assertIn("_ChallengeText", safe)
            self.assertNotIn("_ChallengeText", battle)
            self.assertIn("_VictoryTauntText", battle)
            self.assertNotIn("_VictoryTauntText", safe)

    def test_reflow_safe_keys_subtracts_negative_signals_from_positive_ones(self):
        # A qid a positive source names but a negative signal also catches
        # (e.g. referenced both generically and from a special-cased
        # engine callsite) must stay excluded: the negative signals are a
        # second, independent check, not superseded by the whitelist.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            (src / "battle").mkdir(parents=True)
            (root / "data" / "generated").mkdir(parents=True)
            (root / "data" / "scripts").mkdir(parents=True)
            (root / "data" / "generated" / "text_pointers.lua").write_text(
                'return { Route1 = { TEXT_X = { label = "DoublyReferencedText" } } }\n',
                encoding="utf-8",
            )
            (src / "battle" / "BattleState.lua").write_text(
                'local raw = self.data.text._DoublyReferencedText\n',
                encoding="utf-8",
            )
            safe = reflow_safe_keys(src)
            self.assertNotIn("_DoublyReferencedText", safe)
            self.assertNotIn("DoublyReferencedText", safe)

    def test_is_reflow_safe_qid_matches_runtime_symbol(self):
        safe = {"Route17SignText", "_Route17SignText"}
        self.assertTrue(is_reflow_safe_qid("rb.Route17.Route17SignText", safe))
        self.assertFalse(is_reflow_safe_qid("rb.PalletTown.PalletTownSomeOtherText", safe))

    @unittest.skipUnless(GEN1RECOMP_SRC.parent.is_dir(), "gen1recomp checkout not available")
    def test_real_checkout_reflow_safe_keys_covers_known_safe_and_excludes_known_risky(self):
        safe = reflow_safe_keys(GEN1RECOMP_SRC)
        # Ordinary NPC dialogue via a map text pointer (a Youngster and an
        # Old Man in Viridian City), unrelated to any battle or sign.
        self.assertTrue(is_reflow_safe_qid("rb.ViridianCity.ViridianCityYoungster1Text", safe))
        self.assertTrue(is_reflow_safe_qid("rb.ViridianCity.ViridianCityOldManSleepyPrivatePropertyText", safe))
        # A generic trainer's pre-fight challenge and post-defeat rematch
        # lines: plain field TextBox calls, unlike `won`.
        self.assertTrue(is_reflow_safe_qid("rb.AgathasRoom.AgathaBeforeBattleText", safe))
        self.assertTrue(is_reflow_safe_qid("rb.AgathasRoom.AgathaAfterBattleText", safe))
        # A battle-screen defeat line and a dynamically-looked-up rival
        # win text must never end up in the whitelist, whatever positive
        # source might otherwise have named them.
        self.assertFalse(is_reflow_safe_qid("rb.AgathasRoom.AgathaEndBattleText", safe))
        self.assertFalse(is_reflow_safe_qid("rb.CeruleanCity.CeruleanCityRivalDefeatedText", safe))
        self.assertFalse(is_reflow_safe_qid("y.text_2.Rival1WinText", safe))
        # A physical sign (an ordinary field text pointer otherwise) opens
        # with a title line by design -- reflow would disrupt that layout,
        # so it's excluded even though it isn't remotely battle-related.
        self.assertFalse(is_reflow_safe_qid("rb.Route17.Route17SignText", safe))
        # Pokédex entries render through DexEntryMenu.lua's own display
        # path, not the ordinary field TextBox this whitelist models --
        # never eligible, whatever pokemon.lua's dexEntry.text says.
        self.assertFalse(is_reflow_safe_qid("rb.PokemonList.AbraDexEntry", safe))


if __name__ == "__main__":
    unittest.main()
