import tempfile
import unittest
from pathlib import Path

from pipeline.gs_index_join import (
    IndexedEntry, join_by_index, join_dex_entries, join_dex_entries_pages,
    join_landmarks, parse_indexed_catalog,
)


class ParseIndexedCatalogTests(unittest.TestCase):
    def test_parses_id_index_name_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gs_species.tsv"
            path.write_text("BULBASAUR\t1\tBULBASAUR\nABRA\t63\tABRA\n", encoding="utf-8")
            entries = parse_indexed_catalog(path)
            self.assertEqual(entries, [IndexedEntry("BULBASAUR", 1, "BULBASAUR"), IndexedEntry("ABRA", 63, "ABRA")])

    def test_drops_entries_with_no_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gs_moves.tsv"
            path.write_text("REAL\t5\tREAL\nUNUSED\tnil\tUNUSED\n", encoding="utf-8")
            entries = parse_indexed_catalog(path)
            self.assertEqual(entries, [IndexedEntry("REAL", 5, "REAL")])


class JoinByIndexTests(unittest.TestCase):
    def test_joins_by_numeric_qid_suffix_within_the_given_prefix(self):
        entries = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.names.PokemonNames.1", "BULBASAUR", "BULBIZARRE")]
        translations, stats = join_by_index(entries, rows, "gs.names.PokemonNames.")
        self.assertEqual(translations, {"BULBASAUR": "BULBIZARRE"})
        self.assertEqual(stats, {"total": 1, "translated": 1, "no_corpus_entry": 0, "same_as_english": 0})

    def test_does_not_cross_categories_sharing_the_same_number(self):
        # The real bug this join caught: PokemonNames.29 and
        # TrainerClassNames.29 are different, real qids that happen to
        # share the bare suffix "29".
        entries = [IndexedEntry("BEAUTY", 29, "BEAUTY")]
        rows = [
            ("gs.names.DecorationNames.29", "Some Decoration", "Une decoration"),
            ("gs.class_names.TrainerClassNames.29", "BEAUTY", "CANON"),
        ]
        translations, _ = join_by_index(entries, rows, "gs.class_names.TrainerClassNames.")
        self.assertEqual(translations, {"BEAUTY": "CANON"})

    def test_missing_corpus_entry_is_counted_not_guessed(self):
        entries = [IndexedEntry("UNKNOWN", 999, "UNKNOWN")]
        translations, stats = join_by_index(entries, [], "gs.names.PokemonNames.")
        self.assertEqual(translations, {})
        self.assertEqual(stats["no_corpus_entry"], 1)

    def test_same_as_english_is_tracked_but_still_shipped(self):
        entries = [IndexedEntry("PIKACHU", 25, "PIKACHU")]
        rows = [("gs.names.PokemonNames.25", "PIKACHU", "PIKACHU")]
        translations, stats = join_by_index(entries, rows, "gs.names.PokemonNames.")
        self.assertEqual(translations, {"PIKACHU": "PIKACHU"})
        self.assertEqual(stats["same_as_english"], 1)
        self.assertEqual(stats["translated"], 1)

    def test_translation_is_converted_to_engine_form(self):
        entries = [IndexedEntry("X", 1, "X")]
        rows = [("gs.names.MoveNames.1", "X", "Un<LINE>Deux")]
        translations, _ = join_by_index(entries, rows, "gs.names.MoveNames.")
        self.assertEqual(translations["X"], "Un\nDeux")

    def test_control_only_translation_is_not_shipped_or_counted(self):
        entries = [IndexedEntry("X", 1, "X")]
        rows = [("gs.names.MoveNames.1", "X", "{sound_item}")]
        translations, stats = join_by_index(entries, rows, "gs.names.MoveNames.")
        self.assertEqual(translations, {})
        self.assertEqual(stats["translated"], 0)
        self.assertEqual(stats["no_corpus_entry"], 1)


class JoinDexEntriesTests(unittest.TestCase):
    def test_joins_kind_by_normalised_species_name(self):
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries.BulbasaurPokedexEntry.Species", "SEED", "GRAINE")]
        translations, stats = join_dex_entries(species, rows, "dex_entries", "Species")
        self.assertEqual(translations, {"BULBASAUR": "GRAINE"})
        self.assertEqual(stats["translated"], 1)

    def test_prefers_gold_specific_kind_variant_when_the_corpus_splits_versions(self):
        species = [IndexedEntry("EXEGGUTOR", 103, "EXEGGUTOR")]
        rows = [
            ("gs.dex_entries.ExeggutorPokedexEntry.Species^G", "COCONUT", "FRUITPALME"),
            ("gs.dex_entries.ExeggutorPokedexEntry.Species^S", "COCONUT", "FRUIT PALME"),
        ]
        translations, stats = join_dex_entries(species, rows, "dex_entries", "Species")
        self.assertEqual(translations, {"EXEGGUTOR": "FRUITPALME"})
        self.assertEqual(stats["translated"], 1)

    def test_joins_gold_flavor_text_with_no_label_suffix(self):
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une graine.")]
        translations, _ = join_dex_entries(species, rows, "dex_entries_gold")
        self.assertEqual(translations, {"BULBASAUR": "Une graine."})

    def test_handles_species_names_with_underscores_and_punctuation(self):
        # Verified against the real data: FARFETCH_D/HO_OH/MR__MIME/
        # NIDORAN_F all normalise to match their qid-derived names.
        species = [
            IndexedEntry("FARFETCH_D", 83, "FARFETCH'D"),
            IndexedEntry("HO_OH", 250, "HO-OH"),
            IndexedEntry("MR__MIME", 122, "MR.MIME"),
            IndexedEntry("NIDORAN_F", 29, "NIDORAN"),
        ]
        rows = [
            ("gs.dex_entries.FarfetchDPokedexEntry.Species", "WILD DUCK", "CANARD SAUVAGE"),
            ("gs.dex_entries.HoOhPokedexEntry.Species", "RAINBOW", "ARC-EN-CIEL"),
            ("gs.dex_entries.MrMimePokedexEntry.Species", "BARRIER", "BARRIERE"),
            ("gs.dex_entries.NidoranFPokedexEntry.Species", "POISON PIN", "POISON PIQUANT"),
        ]
        translations, stats = join_dex_entries(species, rows, "dex_entries", "Species")
        self.assertEqual(translations, {
            "FARFETCH_D": "CANARD SAUVAGE", "HO_OH": "ARC-EN-CIEL",
            "MR__MIME": "BARRIERE", "NIDORAN_F": "POISON PIQUANT",
        })
        self.assertEqual(stats["translated"], 4)

    def test_missing_dex_entry_is_counted_not_guessed(self):
        species = [IndexedEntry("MEWTWO", 150, "MEWTWO")]
        translations, stats = join_dex_entries(species, [], "dex_entries", "Species")
        self.assertEqual(translations, {})
        self.assertEqual(stats["no_corpus_entry"], 1)

    def test_conflicting_normalised_dex_names_are_rejected(self):
        species = [IndexedEntry("HO_OH", 250, "HO-OH")]
        rows = [
            ("gs.dex_entries.HoOhPokedexEntry.Species", "RAINBOW", "ARC-EN-CIEL"),
            ("gs.dex_entries.Ho_OhPokedexEntry.Species", "RAINBOW", "PRISME"),
        ]
        with self.assertRaisesRegex(ValueError, "conflicting 'dex_entries' translations"):
            join_dex_entries(species, rows, "dex_entries", "Species")

    def test_kind_and_flavor_text_categories_do_not_cross(self):
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [
            ("gs.dex_entries.BulbasaurPokedexEntry.Species", "SEED", "GRAINE"),
            ("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une graine."),
            ("gs.dex_entries_silver.BulbasaurPokedexEntry", "A seed (Silver).", "Une graine (Argent)."),
        ]
        kind, _ = join_dex_entries(species, rows, "dex_entries", "Species")
        gold_text, _ = join_dex_entries(species, rows, "dex_entries_gold")
        self.assertEqual(kind, {"BULBASAUR": "GRAINE"})
        self.assertEqual(gold_text, {"BULBASAUR": "Une graine."})


class JoinDexEntriesPagesTests(unittest.TestCase):
    """gen1recomp's PokedexMenu:drawEntryBody splits entry.text/text2 on the
    literal "<NEXT>" substring, not a real newline (confirmed against
    Rom:readString, the decoder RomExtractorGen2:extractPokedex uses for
    this exact field -- unlike decodeGen2Text, the ordinary dialogue
    pointer catalog's own decoder, which is what corpus_to_engine's
    "<NEXT>" -> "\\n" mapping is written for). A real build showed this:
    the #DEX screen's translated flavor text rendered as one run-on line
    with no wrap at all.
    """

    def test_splits_the_two_pages_and_keeps_next_literal(self):
        species = [IndexedEntry("CYNDAQUIL", 155, "CYNDAQUIL")]
        rows = [(
            "gs.dex_entries_gold.CyndaquilPokedexEntry",
            "It is timid, and<NEXT>always curls itself up in a ball.@"
            "If attacked, it<NEXT>flares up its back for protection.@",
            "Il est timide et<NEXT>se roule en boule.@"
            "S'il est attaque, il<NEXT>enflamme son dos.@",
        )]
        page1, page2, stats1, stats2 = join_dex_entries_pages(species, rows, "dex_entries_gold")
        self.assertEqual(page1, {"CYNDAQUIL": "Il est timide et<NEXT>se roule en boule."})
        self.assertEqual(page2, {"CYNDAQUIL": "S'il est attaque, il<NEXT>enflamme son dos."})
        self.assertNotIn("\n", page1["CYNDAQUIL"])
        self.assertNotIn("\n", page2["CYNDAQUIL"])
        self.assertEqual(stats1, {"total": 1, "translated": 1, "no_corpus_entry": 0})
        self.assertEqual(stats2, {"total": 1, "translated": 1, "no_corpus_entry": 0})

    def test_a_row_with_no_page_marker_at_all_still_ships_page_one(self):
        # ja-Hrkt's real GoldSilver dex_entries_gold rows are shaped exactly
        # like this: no "@" anywhere (they end on "<DEXEND>" instead), so
        # the corpus never preserved a second page for that language.
        # Before this fix the whole species was dropped; page1 is real
        # content and must still ship, just without a page2.
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une graine.<DEXEND>")]
        page1, page2, stats1, stats2 = join_dex_entries_pages(species, rows, "dex_entries_gold")
        # <DEXEND> is a box/timing control corpus_to_engine already strips
        # for every other category (pipeline/tokens.py); dropped here too.
        self.assertEqual(page1, {"BULBASAUR": "Une graine."})
        self.assertEqual(page2, {})
        self.assertEqual(stats1["translated"], 1)
        self.assertEqual(stats2["no_corpus_entry"], 1)

    def test_a_row_with_only_the_terminator_at_still_ships_page_one(self):
        # ko's real GoldSilver dex_entries_gold rows are shaped exactly like
        # this: exactly one "@", the row's own terminator, never a second
        # page's worth of content.
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une graine.@")]
        page1, page2, stats1, stats2 = join_dex_entries_pages(species, rows, "dex_entries_gold")
        self.assertEqual(page1, {"BULBASAUR": "Une graine."})
        self.assertEqual(page2, {})
        self.assertEqual(stats1["translated"], 1)
        self.assertEqual(stats2["no_corpus_entry"], 1)

    def test_more_than_two_pages_raises_instead_of_silently_truncating(self):
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une.@Deux.@Trois.@")]
        with self.assertRaisesRegex(ValueError, "unexpected 'dex_entries_gold' page count"):
            join_dex_entries_pages(species, rows, "dex_entries_gold")

    def test_a_line_token_colliding_with_next_raises_instead_of_mislabeling(self):
        # _CORPUS_EXPANSIONS maps both <LINE> and <NEXT> to "\n"; a blind
        # "\n" -> "<NEXT>" reversal would silently relabel a genuine <LINE>
        # break as <NEXT>. No real dex_entries_gold row does this today
        # (verified against the corpus), so this must fail loudly instead.
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries_gold.BulbasaurPokedexEntry", "A seed.", "Une<LINE>graine.@Suite.@")]
        with self.assertRaisesRegex(ValueError, "line-break token other than <NEXT>"):
            join_dex_entries_pages(species, rows, "dex_entries_gold")

    def test_missing_dex_entry_is_counted_not_guessed(self):
        species = [IndexedEntry("MEWTWO", 150, "MEWTWO")]
        page1, page2, stats1, stats2 = join_dex_entries_pages(species, [], "dex_entries_gold")
        self.assertEqual(page1, {})
        self.assertEqual(page2, {})
        self.assertEqual(stats1["no_corpus_entry"], 1)
        self.assertEqual(stats2["no_corpus_entry"], 1)

    def test_conflicting_normalised_dex_names_are_rejected(self):
        species = [IndexedEntry("HO_OH", 250, "HO-OH")]
        rows = [
            ("gs.dex_entries_gold.HoOhPokedexEntry", "Rainbow.@Bird.@", "Arc-en-ciel.@Oiseau.@"),
            ("gs.dex_entries_gold.Ho_OhPokedexEntry", "Rainbow.@Bird.@", "Prisme.@Volatile.@"),
        ]
        with self.assertRaisesRegex(ValueError, "conflicting 'dex_entries_gold' translations"):
            join_dex_entries_pages(species, rows, "dex_entries_gold")

    def test_kind_category_is_untouched_by_the_page_split(self):
        # dex_entries (the kind label) uses a fourth qid segment
        # (".Species"); join_dex_entries_pages never passes a label_suffix,
        # so this qid shape simply never matches, rather than silently
        # mis-splitting a short label.
        species = [IndexedEntry("BULBASAUR", 1, "BULBASAUR")]
        rows = [("gs.dex_entries.BulbasaurPokedexEntry.Species", "SEED", "GRAINE")]
        page1, page2, stats1, stats2 = join_dex_entries_pages(species, rows, "dex_entries")
        self.assertEqual(page1, {})
        self.assertEqual(page2, {})
        self.assertEqual(stats1["no_corpus_entry"], 1)
        self.assertEqual(stats2["no_corpus_entry"], 1)


class JoinLandmarksTests(unittest.TestCase):
    def test_joins_by_normalised_name_stripping_prefix_and_suffix(self):
        landmarks = [IndexedEntry("LANDMARK_AZALEA_TOWN", 12, "AZALEA TOWN")]
        rows = [("gs.landmarks.AzaleaTownName", "AZALEA TOWN", "ECORCIA")]
        translations, stats = join_landmarks(landmarks, rows)
        self.assertEqual(translations, {"LANDMARK_AZALEA_TOWN": "ECORCIA"})
        self.assertEqual(stats, {"total": 1, "translated": 1, "no_corpus_entry": 0})

    def test_two_line_names_with_bsp_still_match(self):
        # Verified against the real data: "NEW BARK<BSP>TOWN" (corpus) and
        # "NEW BARK\nTOWN" (ROM, real two-line town-map name) both
        # normalise past the markup to the same letters.
        landmarks = [IndexedEntry("LANDMARK_NEW_BARK_TOWN", 1, "NEW BARK\nTOWN")]
        rows = [("gs.landmarks.NewBarkTownName", "NEW BARK TOWN", "BOURG GEO")]
        translations, _ = join_landmarks(landmarks, rows)
        self.assertEqual(translations, {"LANDMARK_NEW_BARK_TOWN": "BOURG GEO"})

    def test_missing_landmark_is_counted_not_guessed(self):
        landmarks = [IndexedEntry("LANDMARK_UNKNOWN", 99, "UNKNOWN")]
        translations, stats = join_landmarks(landmarks, [])
        self.assertEqual(translations, {})
        self.assertEqual(stats["no_corpus_entry"], 1)

    def test_joins_reviewed_rom_identifier_aliases(self):
        landmarks = [
            IndexedEntry("LANDMARK_UNDERGROUND_PATH", 58, "UNDERGROUND"),
            IndexedEntry("LANDMARK_SPECIAL", 0, "SPECIAL"),
        ]
        rows = [
            ("gs.landmarks.UndergroundName", "UNDERGROUND", "SOUTERRAIN"),
            ("gs.landmarks.SpecialMapName", "SPECIAL", "SPECIAL"),
        ]
        translations, stats = join_landmarks(landmarks, rows)
        self.assertEqual(translations, {
            "LANDMARK_UNDERGROUND_PATH": "SOUTERRAIN",
            "LANDMARK_SPECIAL": "SPECIAL",
        })
        self.assertEqual(stats["translated"], 2)

    def test_conflicting_normalised_landmark_names_are_rejected(self):
        landmarks = [IndexedEntry("LANDMARK_NEW_BARK_TOWN", 1, "NEW BARK TOWN")]
        rows = [
            ("gs.landmarks.NewBarkTownName", "NEW BARK TOWN", "BOURG GEO"),
            ("gs.landmarks.New_Bark_TownName", "NEW BARK TOWN", "NOUVEAU BOURG"),
        ]
        with self.assertRaisesRegex(ValueError, "conflicting landmark translations"):
            join_landmarks(landmarks, rows)


if __name__ == "__main__":
    unittest.main()
