import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.crystal_mod import (
    crystal_feature_catalogs, crystal_text_catalog_from_join, join_crystal_dialogue,
    join_crystal_rom_text, load_crystal_rom_text_anchors, parse_rom_text_catalog,
    load_crystal_dialogue_overrides, load_crystal_pointer_decisions,
)
from pipeline.crystal_registries import (
    CRYSTAL_ONLY_ITEMS, CRYSTAL_ONLY_LANDMARKS, CRYSTAL_ONLY_TRAINER_CLASSES,
    crystal_registry_catalogs, load_crystal_registry_overrides,
)
from pipeline.crystal_strings import match_crystal_engine_strings
from pipeline.engine_profile import PINNED_PROFILE
from pipeline.gs_join import REVIEWED_QID, join_gs_pointers
from pipeline.gs_text import GsTextRecord


class JoinCrystalDialogueTests(unittest.TestCase):
    @staticmethod
    def write_corpus(root: Path, language: str | None, rows: list[tuple[str, str, str]]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "qid_msg.txt").write_text("\n".join(qid for qid, _, _ in rows) + "\n", encoding="utf-8")
        (root / "en_msg.txt").write_text("\n".join(f"{en}@" for _, en, _ in rows) + "\n", encoding="utf-8")
        if language is not None:
            (root / f"{language}_msg.txt").write_text(
                "\n".join(f"{target}@" for _, _, target in rows) + "\n", encoding="utf-8",
            )

    @staticmethod
    def write_text_catalog(root: Path, rows: list[tuple[str, str]]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "gs_text.tsv").write_text(
            "\n".join(f"{pointer}\t{text}" for pointer, text in rows) + "\n", encoding="utf-8",
        )

    def test_resolves_a_unique_normalized_english_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_text_catalog(root / "extracted", [("00:0001", "Hello!")])
            self.write_corpus(root / "corpus", "fr", [("c.text.Hello", "Hello!", "Bonjour!")])
            entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["unique"], 1)
            self.assertEqual(stats["total"], 1)
            catalog = crystal_text_catalog_from_join(entries)
            self.assertEqual(catalog, {"00:0001": "Bonjour!"})

    def test_missing_language_corpus_degrades_gracefully_instead_of_raising(self):
        """Crystal has no ko_msg.txt in poke-corpus, unlike GoldSilver -- dialogue
        should simply stay in English for that language, not crash the build."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_text_catalog(root / "extracted", [("00:0001", "Hello!"), ("00:0002", "Bye!")])
            # fr exists, ko doesn't -- mirrors the real Crystal/ collection.
            self.write_corpus(root / "corpus", "fr", [("c.text.Hello", "Hello!", "Bonjour!")])
            entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "ko")
            self.assertEqual([entry.provenance for entry in entries], ["no_match", "no_match"])
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["no_match"], 2)
            self.assertEqual(stats["unique"], 0)
            catalog = crystal_text_catalog_from_join(entries)
            self.assertEqual(catalog, {})

    def test_missing_language_corpus_still_applies_a_hand_composed_dialogue_override(self):
        # Korean has no Crystal corpus row for this pointer, but a
        # translator composing one anyway (overrides/ko/gsc/
        # crystal_dialogue.json, this catalog's own documented escape hatch
        # for prose with no corpus row at all) must still reach the join --
        # the corpus being empty is not a reason to discard overrides.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_text_catalog(root / "extracted", [("00:0001", "Hello!")])
            self.write_corpus(root / "corpus", "fr", [("c.text.Hello", "Hello!", "Bonjour!")])
            with patch(
                "pipeline.crystal_mod.load_crystal_dialogue_overrides",
                return_value={"00:0001": "안녕!"},
            ):
                entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "ko")
            self.assertEqual(stats["override"], 1)
            self.assertEqual(crystal_text_catalog_from_join(entries), {"00:0001": "안녕!"})

    def test_unmatched_english_stays_untranslated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_text_catalog(root / "extracted", [("00:0001", "Nothing matches this.")])
            self.write_corpus(root / "corpus", "fr", [("c.text.Other", "Something else.", "Autre chose.")])
            entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["no_match"], 1)
            self.assertEqual(crystal_text_catalog_from_join(entries), {})

    def test_ambiguous_english_is_resolved_by_a_reviewed_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_text_catalog(root / "extracted", [("00:0001", "Here you go!")])
            self.write_corpus(root / "corpus", "fr", [
                ("c.SiteA.HereYouGo", "Here you go!", "Tenez!"),
                ("c.SiteB.HereYouGo", "Here you go!", "Voila!"),
            ])
            with patch("pipeline.crystal_mod.load_crystal_pointer_decisions", return_value={"00:0001": "c.SiteA.HereYouGo"}):
                entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["reviewed_qid"], 1)
            self.assertEqual(entries[0].provenance, REVIEWED_QID)
            self.assertEqual(crystal_text_catalog_from_join(entries), {"00:0001": "Tenez!"})


class CrystalFeatureCatalogTests(unittest.TestCase):
    @staticmethod
    def write_corpus(root: Path, rows: list[tuple[str, str, str]], language: str = "fr") -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "qid_msg.txt").write_text("\n".join(row[0] for row in rows) + "\n", encoding="utf-8")
        (root / "en_msg.txt").write_text("\n".join(row[1] for row in rows) + "\n", encoding="utf-8")
        (root / f"{language}_msg.txt").write_text("\n".join(row[2] for row in rows) + "\n", encoding="utf-8")

    def test_rom_text_parser_decodes_only_the_extractor_escape_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gs_rom_text.tsv"
            path.write_text("_One\tLine\\nTwo\\tX\\\\Y\n", encoding="utf-8")
            self.assertEqual(parse_rom_text_catalog(path), {"_One": "Line\nTwo\tX\\Y"})
            path.write_text("_One\tbad\\q\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid escape"):
                parse_rom_text_catalog(path)

    def test_joins_all_seven_crystal_rom_text_labels_by_reviewed_qid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labels = load_crystal_rom_text_anchors()
            rows = []
            extracted = []
            for index, (label, qid) in enumerate(labels.items()):
                source = f"Unique Crystal ROM text {index}."
                target = f"Texte Cristal unique {index}."
                extracted.append(f"{label}\t{source}")
                rows.append((qid, source, target))
            (root / "extracted").mkdir()
            (root / "extracted" / "gs_rom_text.tsv").write_text(
                "\n".join(extracted) + "\n", encoding="utf-8",
            )
            self.write_corpus(root / "corpus", rows)
            catalog, stats = join_crystal_rom_text(root / "extracted", root / "corpus", "fr")
            self.assertEqual(set(catalog), set(labels))
            self.assertEqual(stats["translated"], 7)
            self.assertEqual(stats["fallback_english"], 0)

    def test_crystal_registry_catalog_is_exactly_four_items_one_class_one_landmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            extracted.mkdir()
            (extracted / "gs_items.tsv").write_text(
                "BLUE_CARD\t1\tBLUE CARD\nCLEAR_BELL\t2\tCLEAR BELL\n"
                "EGG_TICKET\t3\tEGG TICKET\nGS_BALL\t4\tGS BALL\nPOTION\t5\tPOTION\n",
                encoding="utf-8",
            )
            (extracted / "gs_trainer_classes.tsv").write_text(
                "MYSTICALMAN\t1\tMYSTICALMAN\nBEAUTY\t2\tBEAUTY\n", encoding="utf-8",
            )
            (extracted / "gs_landmarks.tsv").write_text(
                "LANDMARK_BATTLE_TOWER\t1\tBATTLE TOWER\nLANDMARK_AZALEA_TOWN\t2\tAZALEA TOWN\n",
                encoding="utf-8",
            )
            self.write_corpus(root / "corpus", [
                (f"c.names.ItemNames.{index}", english, target)
                for index, english, target in (
                    (1, "BLUE CARD", "CARTE BLEUE"), (2, "CLEAR BELL", "GLAS TRANSPARENT"),
                    (3, "EGG TICKET", "TICKET OEUF"), (4, "GS BALL", "GS BALL"),
                )
            ] + [
                ("c.class_names.TrainerClassNames.1", "MYSTICALMAN", "MYSTIQUE"),
                ("c.landmarks.BattleTowerName", "BATTLE TOWER", "TOUR DE COMBAT"),
            ])
            catalogs, stats = crystal_registry_catalogs(extracted, root / "corpus", "fr")
            self.assertEqual(set(catalogs["item_names"]), {"BLUE_CARD", "CLEAR_BELL", "EGG_TICKET", "GS_BALL"})
            self.assertEqual(catalogs["trainer_class_names"], {"MYSTICALMAN": "MYSTIQUE"})
            self.assertEqual(catalogs["landmarks"], {"LANDMARK_BATTLE_TOWER": "TOUR DE COMBAT"})
            self.assertEqual(sum(section["translated"] for section in stats.values()), 6)

    def test_registry_catalog_absent_crystal_language_with_no_overrides_is_an_explicit_english_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            extracted.mkdir()
            (extracted / "gs_items.tsv").write_text(
                "BLUE_CARD\t1\tBLUE CARD\nCLEAR_BELL\t2\tCLEAR BELL\n"
                "EGG_TICKET\t3\tEGG TICKET\nGS_BALL\t4\tGS BALL\n", encoding="utf-8",
            )
            (extracted / "gs_trainer_classes.tsv").write_text("MYSTICALMAN\t1\tMYSTICALMAN\n", encoding="utf-8")
            (extracted / "gs_landmarks.tsv").write_text(
                "LANDMARK_BATTLE_TOWER\t1\tBATTLE TOWER\n", encoding="utf-8",
            )
            with patch("pipeline.crystal_registries.load_crystal_registry_overrides", return_value={}):
                catalogs, stats = crystal_registry_catalogs(extracted, root / "corpus", "ko")
            self.assertEqual(catalogs, {"item_names": {}, "trainer_class_names": {}, "landmarks": {}})
            self.assertEqual(stats["item_names"]["fallback_english"], 4)
            self.assertEqual(stats["trainer_class_names"]["fallback_english"], 1)
            self.assertEqual(stats["landmarks"]["fallback_english"], 1)

    def test_registry_catalog_absent_crystal_language_still_ships_hand_composed_overrides(self):
        # Korean has no Crystal corpus row for any of these six records, but
        # a translator composing one anyway (overrides/ko/gsc/
        # crystal_registries.json, this catalog's own documented way to
        # cover that gap) must still reach the shipped catalogue.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            extracted.mkdir()
            (extracted / "gs_items.tsv").write_text("BLUE_CARD\t1\tBLUE CARD\n", encoding="utf-8")
            (extracted / "gs_trainer_classes.tsv").write_text("MYSTICALMAN\t1\tMYSTICALMAN\n", encoding="utf-8")
            (extracted / "gs_landmarks.tsv").write_text(
                "LANDMARK_BATTLE_TOWER\t1\tBATTLE TOWER\n", encoding="utf-8",
            )
            overrides = {
                "BLUE_CARD": "블루카드", "MYSTICALMAN": "신비한 남자", "LANDMARK_BATTLE_TOWER": "배틀타워",
            }
            with (
                patch("pipeline.crystal_registries.CRYSTAL_ONLY_ITEMS", frozenset({"BLUE_CARD"})),
                patch("pipeline.crystal_registries.load_crystal_registry_overrides", return_value=overrides),
            ):
                catalogs, stats = crystal_registry_catalogs(extracted, root / "corpus", "ko")
            self.assertEqual(catalogs["item_names"], {"BLUE_CARD": "블루카드"})
            self.assertEqual(catalogs["trainer_class_names"], {"MYSTICALMAN": "신비한 남자"})
            self.assertEqual(catalogs["landmarks"], {"LANDMARK_BATTLE_TOWER": "배틀타워"})
            self.assertEqual(stats["item_names"]["translated"], 1)
            self.assertEqual(stats["item_names"]["fallback_english"], 0)

    def test_absent_crystal_language_with_no_overrides_is_an_explicit_english_fallback(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.crystal_strings.load_gs_engine_scope_exclusions", return_value={"A", "B"}),
            patch("pipeline.crystal_strings.load_engine_overrides", return_value={}),
            patch("pipeline.crystal_strings._load_selectors", return_value={}),
        ):
            catalog, stats = match_crystal_engine_strings(Path(tmp), "ko")
            self.assertEqual(catalog, {})
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["fallback_english"], 2)
            self.assertEqual(stats["policy"], "english-fallback")

    def test_absent_crystal_language_still_ships_a_hand_composed_override(self):
        # Korean has no Crystal corpus row for any of these 48 keys, but a
        # translator composing one anyway (this catalog's own documented way
        # to cover that gap, see match_crystal_engine_strings' docstring)
        # must still reach the shipped catalogue -- the corpus being empty
        # is not a reason to silently discard overrides/ko/gsc/engine.json.
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.crystal_strings.load_gs_engine_scope_exclusions", return_value={"A", "B"}),
            patch("pipeline.crystal_strings.load_engine_overrides", return_value={
                "A": {"override": "Traduit A", "reason": "engine-original", "provenance": "fixture"},
            }),
            patch("pipeline.crystal_strings._load_selectors", return_value={}),
        ):
            catalog, stats = match_crystal_engine_strings(Path(tmp), "ko")
            self.assertEqual(catalog, {"A": "Traduit A"})
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["translated"], 1)
            self.assertEqual(stats["fallback_english"], 1)
            self.assertEqual(stats["unmatched"], ["B"])
            self.assertEqual(stats["policy"], "english-fallback")

    def test_scanner_overrides_complete_the_specialized_crystal_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            self.write_corpus(corpus, [("c.fixture.Row", "A", "Traduit A")])
            base_report = {
                "translated": 1, "total": 2, "override": 0,
                "details": {"A": "unique"}, "provenance": {},
                "ambiguous": {}, "unmatched": ["B"],
            }
            with (
                patch("pipeline.crystal_strings.load_gs_engine_scope_exclusions", return_value={"A", "B"}),
                patch("pipeline.crystal_strings.load_engine_overrides", return_value={
                    "B": {"override": "Traduit B", "reason": "engine-contract-gap", "provenance": "fixture"},
                    "Gold only": {"override": "Hors périmètre", "reason": "fixture", "provenance": "fixture"},
                }),
                patch("pipeline.crystal_strings.match_engine_catalog") as matcher,
                patch("pipeline.crystal_strings._load_selectors", return_value={}),
            ):
                matcher.return_value = ({"A": "Traduit A", "B": "Traduit B"}, base_report)
                catalog, stats = match_crystal_engine_strings(corpus, "fr")
            self.assertEqual(catalog, {"A": "Traduit A", "B": "Traduit B"})
            self.assertEqual(matcher.call_args.kwargs["overrides"], {
                "B": {"override": "Traduit B", "reason": "engine-contract-gap", "provenance": "fixture"},
            })
            self.assertEqual(stats["translated"], 2)
            self.assertEqual(stats["catalog_kind"], "Crystal-exclusive Strings callsites")

    def test_feature_metrics_keep_upstream_rom_text_out_of_the_54_entry_aggregate(self):
        named_stats = {
            "item_names": {"translated": 4, "total": 4},
            "trainer_class_names": {"translated": 1, "total": 1},
            "landmarks": {"translated": 1, "total": 1},
        }
        with (
            patch("pipeline.crystal_mod.crystal_registry_catalogs", return_value=({}, named_stats)),
            patch("pipeline.crystal_mod.join_crystal_rom_text", return_value=({}, {"translated": 7, "total": 7})),
            patch("pipeline.crystal_mod.match_crystal_engine_strings", return_value=({}, {"translated": 48, "total": 48})),
        ):
            _catalogs, stats = crystal_feature_catalogs("unused", "unused", "fr")
        self.assertEqual(stats["aggregate"], {
            "translated": 54, "total": 54, "percent": 100.0,
            "policy": "english-fallback",
        })
        self.assertEqual(stats["named_registries"], named_stats)
        self.assertEqual(stats["engine_crystal"]["translated"], 48)
        self.assertEqual(stats["rom_text"]["runtime_dependency"], "mod.content.rom_text")

    def test_pinned_profile_keeps_the_54_autonomous_crystal_catalogs(self):
        named = {
            "item_names": {"BLUE_CARD": "CARTE"},
            "trainer_class_names": {"MYSTICALMAN": "MYSTIQUE"},
            "landmarks": {"LANDMARK_BATTLE_TOWER": "TOUR"},
        }
        named_stats = {
            "item_names": {"translated": 4, "total": 4},
            "trainer_class_names": {"translated": 1, "total": 1},
            "landmarks": {"translated": 1, "total": 1},
        }
        strings = {f"Crystal {index}": f"Cristal {index}" for index in range(48)}
        string_stats = {"translated": 48, "total": 48}
        with (
            patch("pipeline.crystal_mod.crystal_registry_catalogs", return_value=(named, named_stats)),
            patch("pipeline.crystal_mod.match_crystal_engine_strings", return_value=(strings, string_stats)),
        ):
            catalogs, stats = crystal_feature_catalogs(
                "unused", "unused", "fr", engine_profile=PINNED_PROFILE,
            )
        self.assertEqual(set(catalogs), {"item_names", "trainer_class_names", "landmarks", "strings"})
        self.assertEqual(stats["aggregate"]["total"], 54)
        self.assertNotIn("rom_text", catalogs)
        self.assertEqual(stats["rom_text"]["runtime_dependency"], "not available in v0.2.41")


class LoadCrystalPointerDecisionsTests(unittest.TestCase):
    def test_repository_decisions_are_valid_and_qid_based(self):
        decisions = load_crystal_pointer_decisions()
        self.assertTrue(decisions)
        for pointer, qid in decisions.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(qid.startswith("c."), (pointer, qid))

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_crystal_pointer_decisions(Path(tmp) / "absent.json"), {})

    def test_rejects_a_gold_prefixed_qid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.json"
            path.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/crystal-pointer-decisions",
                "version": 1,
                "entries": {"00:0001": {"qid": "gs.a.One", "symbol": "One"}},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_pointer_decisions(path)

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.json"
            path.write_text(json.dumps({"schema": "wrong", "version": 1, "entries": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_pointer_decisions(path)

    def test_join_gs_pointers_still_uses_qid_decisions_directly(self):
        # Sanity check on the primitive load_crystal_pointer_decisions()
        # feeds into join_gs_pointers() with, independent of the Crystal
        # corpus-file plumbing exercised by JoinCrystalDialogueTests above.
        records = [GsTextRecord("00:0001", "Ambiguous line.")]
        corpus_rows = [
            ("c.SiteA.Line", "Ambiguous line.", "Ligne A."),
            ("c.SiteB.Line", "Ambiguous line.", "Ligne B."),
        ]
        entries, stats = join_gs_pointers(records, corpus_rows, qid_decisions={"00:0001": "c.SiteB.Line"})
        self.assertEqual(stats["reviewed_qid"], 1)
        self.assertEqual(entries[0].translation, "Ligne B.")


class LoadCrystalDialogueOverridesTests(unittest.TestCase):
    def test_repository_french_overrides_are_valid_and_cover_the_mobile_adapter_gap(self):
        overrides = load_crystal_dialogue_overrides("fr")
        self.assertEqual(len(overrides), 17)
        for pointer, text in overrides.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(text.strip())
            self.assertNotIn("%", text)

    def test_repository_german_overrides_are_valid_and_cover_the_mobile_adapter_gap(self):
        overrides = load_crystal_dialogue_overrides("de")
        self.assertEqual(len(overrides), 17)
        for pointer, text in overrides.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(text.strip())
            self.assertNotIn("%", text)

    def test_repository_italian_overrides_are_valid_and_cover_the_mobile_adapter_gap(self):
        overrides = load_crystal_dialogue_overrides("it")
        self.assertEqual(len(overrides), 18)
        for pointer, text in overrides.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(text.strip())
            self.assertNotIn("%", text)

    def test_repository_spanish_overrides_are_valid_and_cover_the_mobile_adapter_gap(self):
        overrides = load_crystal_dialogue_overrides("es")
        self.assertEqual(len(overrides), 17)
        for pointer, text in overrides.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(text.strip())
            self.assertNotIn("%", text)

    def test_repository_japanese_overrides_are_valid_and_cover_the_mobile_adapter_gap(self):
        overrides = load_crystal_dialogue_overrides("ja-Hrkt")
        self.assertEqual(len(overrides), 17)
        for pointer, text in overrides.items():
            self.assertRegex(pointer, r"^[0-7][0-9a-f]:[0-7][0-9a-f]{3}$")
            self.assertTrue(text.strip())
            self.assertNotIn("%", text)

    def test_missing_language_file_is_empty(self):
        self.assertEqual(load_crystal_dialogue_overrides("ko"), {})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                load_crystal_dialogue_overrides("fr", Path(tmp) / "absent.json"), {},
            )

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text(json.dumps({"schema": "wrong", "version": 1, "entries": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_dialogue_overrides("fr", path)

    def test_rejects_a_row_missing_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/crystal-dialogue-overrides",
                "version": 1,
                "entries": {"00:0001": {"override": "Bonjour!", "reason": "engine-original"}},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_dialogue_overrides("fr", path)

    def test_join_crystal_dialogue_applies_the_override_for_a_no_match_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            JoinCrystalDialogueTests.write_text_catalog(
                root / "extracted", [("18:6674", "No corpus row for this at all.")],
            )
            JoinCrystalDialogueTests.write_corpus(
                root / "corpus", "fr", [("c.text.Other", "Something else.", "Autre chose.")],
            )
            with patch(
                "pipeline.crystal_mod.load_crystal_dialogue_overrides",
                return_value={"18:6674": "Texte traduit sans corpus."},
            ):
                entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["override"], 1)
            self.assertEqual(crystal_text_catalog_from_join(entries), {"18:6674": "Texte traduit sans corpus."})


class LoadCrystalRegistryOverridesTests(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_crystal_registry_overrides("fr", root=Path(tmp)), {})

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "overrides" / "ko" / "gsc" / "crystal_registries.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema": "wrong", "version": 1, "entries": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_registry_overrides("ko", root=root)

    def test_rejects_an_empty_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "overrides" / "ko" / "gsc" / "crystal_registries.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/crystal-registry-overrides",
                "version": 1,
                "entries": {"BLUE_CARD": {"override": "  "}},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crystal_registry_overrides("ko", root=root)

    def test_repository_korean_overrides_are_valid_and_cover_all_six_records(self):
        overrides = load_crystal_registry_overrides("ko")
        expected_ids = CRYSTAL_ONLY_ITEMS | CRYSTAL_ONLY_TRAINER_CLASSES | CRYSTAL_ONLY_LANDMARKS
        self.assertEqual(set(overrides), expected_ids)
        for entry_id, text in overrides.items():
            self.assertTrue(text.strip(), entry_id)


if __name__ == "__main__":
    unittest.main()
