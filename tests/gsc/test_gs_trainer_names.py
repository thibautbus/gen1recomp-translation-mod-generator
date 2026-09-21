import tempfile
import unittest
from pathlib import Path

from pipeline.gsc.mod import _complete_delegated_registries, generate_gs_mod
from pipeline.gsc.trainer_names import parse_trainer_names, trainer_name_catalog


CORPUS = [
    ("gs.parties.YoungsterGroup._1", "JOEY@", "GASPARD@"),
    ("gs.parties.YoungsterGroup._2", "MIKEY@", "MIKEY@"),
    ("gs.parties.BlackbeltGroup._1", "KENJI@", "KENJI-FR@"),
    ("gs.parties.PKMNTrainerGroup._1", "CAL@", "CAL@"),
    ("gs.parties.LassGroup._1", "CARRIE@", "CARINE@"),
]


class TrainerNameCatalogTests(unittest.TestCase):
    def test_joins_by_class_group_and_member_with_a_matching_english_name(self):
        rows = [
            ("YOUNGSTER", 1, "JOEY"), ("YOUNGSTER", 2, "MIKEY"),
            ("BLACKBELT_T", 1, "KENJI"), ("CAL", 1, "CAL"),
            ("LASS", 1, "BRIDGET"), ("SAGE", 1, "LI"),
            ("POKEMON_PROF", 1, "WILL"),
        ]
        catalog, stats = trainer_name_catalog(rows, CORPUS)
        self.assertEqual(catalog, {"YOUNGSTER#1#JOEY": "GASPARD", "BLACKBELT_T#1#KENJI": "KENJI-FR"})
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["translated"], 4)
        self.assertEqual(stats["same_as_english"], 2)
        self.assertEqual(stats["excluded_unreachable"], 1)
        self.assertEqual(
            {row["id"]: row["reason"] for row in stats["backlog"]},
            {"LASS#1#BRIDGET": "english-mismatch", "SAGE#1#LI": "missing-corpus"},
        )

    def test_crystal_party_rows_join_the_same_way(self):
        catalog, stats = trainer_name_catalog(
            [("YOUNGSTER", 1, "JOEY")], [("c.parties.YoungsterGroup._1", "JOEY@", "JULIAN@")],
        )
        self.assertEqual(catalog, {"YOUNGSTER#1#JOEY": "JULIAN"})
        self.assertEqual(stats["translated"], 1)

    def test_parse_rejects_a_malformed_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gs_trainer_names.tsv"
            path.write_text("YOUNGSTER\t1\tJOEY\nYOUNGSTER\tone\tMIKEY\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                parse_trainer_names(path)
            path.write_text("YOUNGSTER\t1\tJOEY\n", encoding="utf-8")
            self.assertEqual(parse_trainer_names(path), [("YOUNGSTER", 1, "JOEY")])

    def test_generated_mod_writes_both_editions_and_the_roster_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr", font_profile="fusion",
                text_catalog={"55:0001": "Bonjour!"},
                trainer_name_catalog={"YOUNGSTER#1#JOEY": "GASPARD"},
                crystal_trainer_name_catalog={"YOUNGSTER#1#JOEY": "JULIAN"},
            )
            self.assertIn('["YOUNGSTER#1#JOEY"] = "GASPARD"',
                          (mod_dir / "lang" / "trainer_names.lua").read_text(encoding="utf-8"))
            self.assertIn('["YOUNGSTER#1#JOEY"] = "JULIAN"',
                          (mod_dir / "lang" / "trainer_names_crystal.lua").read_text(encoding="utf-8"))
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn("mod.content.trainers:get(class)", main)
            self.assertIn("row.name == entry.english", main)
            self.assertIn('trainerCatalogName = "trainer_names_crystal"', main)

    def test_generated_mod_omits_the_registration_without_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr", font_profile="fusion",
                text_catalog={"55:0001": "Bonjour!"},
            )
            self.assertNotIn("trainer_names", (mod_dir / "main.lua").read_text(encoding="utf-8"))
            self.assertFalse((mod_dir / "lang" / "trainer_names.lua").exists())


class DelegatedRegistryCoverageTests(unittest.TestCase):
    def _stats(self, trainer_translated):
        return {
            "phone_contacts": {"total": 3, "translated": 1, "no_corpus_entry": 2, "fallback_english": 2,
                               "omitted_registry_ids": ["PHONE_00", "PHONE_YOUNGSTER_JOEY"]},
            "trainer_names": {"total": 2, "translated": trainer_translated},
            "decorations": {"total": 3, "translated": 1, "no_corpus_entry": 2, "fallback_english": 2,
                            "fallback_ids": ["deco:18", "deco:19"],
                            "omitted_species_ids": ["deco:18", "deco:19"]},
        }

    def test_species_decorations_get_the_translated_species_name(self):
        # Decorations.name prints the row's own name ("CLEFAIRY POSTER"); no
        # caller translates the species, so the patch must carry it.
        stats = self._stats(2)
        catalogs = {"decorations": {"deco:0": "RETOUR"}, "species_names": {"CLEFAIRY": "MELOFEE"}}
        _complete_delegated_registries(catalogs, stats)
        self.assertEqual(catalogs["decorations"], {"deco:0": "RETOUR", "deco:18": "MELOFEE"})
        self.assertEqual(stats["decorations"]["translated"], 2)
        self.assertEqual(stats["decorations"]["fallback_ids"], ["deco:19"])

    def test_phone_trainer_contacts_count_only_once_every_trainer_name_is_translated(self):
        stats = self._stats(1)
        _complete_delegated_registries({"decorations": {}, "species_names": {}}, stats)
        self.assertEqual(stats["phone_contacts"]["translated"], 1)
        stats = self._stats(2)
        _complete_delegated_registries({"decorations": {}, "species_names": {}}, stats)
        self.assertEqual(stats["phone_contacts"]["translated"], 3)
        self.assertEqual(stats["phone_contacts"]["no_corpus_entry"], 0)


if __name__ == "__main__":
    unittest.main()
