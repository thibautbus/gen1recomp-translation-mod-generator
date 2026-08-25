import json
import tempfile
import unittest
from pathlib import Path

from pipeline.crystal_mod import (
    crystal_text_catalog_from_join, join_crystal_dialogue,
    load_crystal_dialogue_overrides, load_crystal_pointer_decisions,
)
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
            self.assertEqual(entries, [])
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["no_match"], 2)
            self.assertEqual(stats["unique"], 0)
            catalog = crystal_text_catalog_from_join(entries)
            self.assertEqual(catalog, {})

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
            from unittest.mock import patch
            with patch("pipeline.crystal_mod.load_crystal_pointer_decisions", return_value={"00:0001": "c.SiteA.HereYouGo"}):
                entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["reviewed_qid"], 1)
            self.assertEqual(entries[0].provenance, REVIEWED_QID)
            self.assertEqual(crystal_text_catalog_from_join(entries), {"00:0001": "Tenez!"})


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

    def test_missing_language_file_is_empty(self):
        self.assertEqual(load_crystal_dialogue_overrides("de"), {})
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
            from unittest.mock import patch
            with patch(
                "pipeline.crystal_mod.load_crystal_dialogue_overrides",
                return_value={"18:6674": "Texte traduit sans corpus."},
            ):
                entries, stats = join_crystal_dialogue(root / "extracted", root / "corpus", "fr")
            self.assertEqual(stats["override"], 1)
            self.assertEqual(crystal_text_catalog_from_join(entries), {"18:6674": "Texte traduit sans corpus."})


if __name__ == "__main__":
    unittest.main()
