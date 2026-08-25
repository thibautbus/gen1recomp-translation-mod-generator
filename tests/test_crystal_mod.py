import tempfile
import unittest
from pathlib import Path

from pipeline.crystal_mod import crystal_text_catalog_from_join, join_crystal_dialogue


class JoinCrystalDialogueTests(unittest.TestCase):
    @staticmethod
    def write_corpus(root: Path, language: str | None, rows: list[tuple[str, str, str]]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "qid_msg.txt").write_text("\n".join(f"{qid}@" for qid, _, _ in rows) + "\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
