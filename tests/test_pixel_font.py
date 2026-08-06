from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.mod import generate_mod
from pipeline import builder
import zipfile


class PixelFontTests(unittest.TestCase):
    @staticmethod
    def _font_source(root: Path) -> Path:
        source = root / "font-source"
        for relative in (
            "fusion-pixel-8px-proportional-latin.ttf",
            "fonts/pokemon-font.ttf",
            "LICENSE.md",
            "fusion-pixel-8px-proportional-ja.ttf",
            "OFL.txt",
            "LICENSES/boutique-bitmap-7x7/OFL.txt",
            "LICENSES/galmuri/LICENSE.txt",
            "LICENSES/misaki/misaki.txt",
            "LICENSES/miseki-bitmap/LICENSE.txt",
        ):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Fusion Pixel Font" if path.name == "OFL.txt" else b"font")
        return source

    def test_latin_uses_bundled_fusion_font_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="fr", font_source=self._font_source(Path(directory)))
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(
                'mod.content.font:register("ttf", '
                '{ file = mod.assets:path("fonts/fusion-pixel-8px-proportional-latin.ttf"), size = 8 })',
                main,
            )
            self.assertTrue((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").is_file())
            self.assertFalse((mod / "fonts/pokemon-font.ttf").exists())
            self.assertFalse((mod / "fonts/fusion-pixel-8px-proportional-ja.ttf").exists())
            self.assertFalse((mod / "fonts/LICENSES/pokemon-font").exists())
            self.assertTrue((mod / "fonts/OFL.txt").is_file())
            self.assertTrue((mod / "fonts/LICENSES/galmuri/LICENSE.txt").is_file())
            self.assertFalse((mod / "assets/fonts").exists())

    def test_latin_pokemon_profile_uses_10px_font(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="fr", font_source=self._font_source(Path(directory)), font_profile="pokemon")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('fonts/pokemon-font.ttf"), size = 10', main)
            self.assertTrue((mod / "fonts/LICENSES/pokemon-font/LICENSE.md").is_file())
            self.assertFalse((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").exists())

    def test_japanese_uses_bundled_fusion_pixel_font(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="ja-Hrkt", font_source=self._font_source(Path(directory)))
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(
                'mod.content.font:register("ttf", '
                '{ file = mod.assets:path("fonts/fusion-pixel-8px-proportional-ja.ttf"), size = 8 })',
                main,
            )
            self.assertTrue((mod / "fonts/fusion-pixel-8px-proportional-ja.ttf").is_file())
            self.assertFalse((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").exists())
            self.assertFalse((mod / "fonts/pokemon-font.ttf").exists())
            self.assertFalse((mod / "fonts/LICENSES/pokemon-font").exists())
            self.assertTrue((mod / "fonts/OFL.txt").is_file())
            self.assertFalse((mod / "assets/fonts").exists())

    def test_generation_does_not_create_rom_font_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="de", font_source=self._font_source(Path(directory)))
            self.assertFalse((mod / "assets" / "font").exists())
            self.assertFalse((mod / "lang" / "font.lua").exists())
            self.assertFalse((mod / "lang" / "charmap.lua").exists())

    def test_incremental_build_removes_stale_font_variant_and_licenses(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = Path(directory) / "mod"
            source = self._font_source(Path(directory))
            generate_mod([], mod, language="fr", font_source=source)
            self.assertTrue((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").exists())
            generate_mod([], mod, language="ja-Hrkt", font_source=source)
            self.assertFalse((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").exists())
            self.assertFalse((mod / "fonts/LICENSES/pokemon-font").exists())
            self.assertTrue((mod / "fonts/fusion-pixel-8px-proportional-ja.ttf").exists())
            self.assertFalse((mod / "assets/fonts").exists())

    def test_font_registration_matches_mod_and_archive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="fr", font_source=self._font_source(Path(directory)))
            archive = Path(directory) / "mod.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in mod.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(mod).as_posix())
            builder.inspect_archive(archive)
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.assets:path("fonts/fusion-pixel-8px-proportional-latin.ttf")', main)
            with zipfile.ZipFile(archive) as source:
                names = set(source.namelist())
            self.assertIn("fonts/fusion-pixel-8px-proportional-latin.ttf", names)
            self.assertNotIn("assets/fonts/fusion-pixel-8px-proportional-latin.ttf", names)

    def test_generation_without_source_uses_plain_pixel_and_refresh_preserves_font(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh = generate_mod([], root / "fresh", language="fr")
            self.assertIn('mod.content.font:register("ttf", {})', (fresh / "main.lua").read_text(encoding="utf-8"))
            self.assertFalse((fresh / "fonts").exists())
            source = self._font_source(root)
            mod = generate_mod([], root / "mod", language="fr", font_source=source)
            font = mod / "fonts/fusion-pixel-8px-proportional-latin.ttf"
            original = font.read_bytes()
            generate_mod([], mod, language="fr")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.font:register("ttf", { file = mod.assets:path("fonts/fusion-pixel-8px-proportional-latin.ttf"), size = 8 })', main)
            self.assertEqual(font.read_bytes(), original)

    def test_invalid_font_source_keeps_existing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._font_source(root)
            mod = generate_mod([], root / "mod", language="fr", font_source=source)
            font = mod / "fonts/fusion-pixel-8px-proportional-latin.ttf"
            original = font.read_bytes()
            (source / "fusion-pixel-8px-proportional-latin.ttf").unlink()
            with self.assertRaises(FileNotFoundError):
                generate_mod([], mod, language="fr", font_source=source)
            self.assertEqual(font.read_bytes(), original)

    def test_failed_validation_keeps_previous_font_and_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._font_source(root)
            mod = generate_mod([], root / "mod", language="fr", font_source=source)
            old_main = (mod / "main.lua").read_bytes()
            old_font = (mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").read_bytes()
            with patch("pipeline.mod.load_recipes", side_effect=ValueError("invalid override")), self.assertRaisesRegex(ValueError, "invalid override"):
                generate_mod([], mod, language="fr", font_source=source, font_profile="pokemon")
            self.assertEqual((mod / "main.lua").read_bytes(), old_main)
            self.assertEqual((mod / "fonts/fusion-pixel-8px-proportional-latin.ttf").read_bytes(), old_font)
            self.assertFalse((mod / "fonts/pokemon-font.ttf").exists())

    def test_scaffold_support_selects_japanese_profile_without_copying_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                'return function(mod)\n  -- mod.content.font:register("ttf", {})\nend\n',
                encoding="utf-8",
            )
            (scaffold / "lang" / "naming.lua").write_text("return {}\n", encoding="utf-8")
            builder.preserve_scaffold_support(scaffold, mod, "ja-Hrkt")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(
                'mod.content.font:register("ttf", {})',
                main,
            )
            self.assertFalse((mod / "lang" / "font.lua").exists())

    def test_incremental_build_removes_stale_legacy_font_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = Path(directory) / "mod"
            for relative in (
                "lang/font.lua",
                "lang/charmap.lua",
                "assets/font/localized.png",
            ):
                path = mod / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stale")
            (mod / "lang" / "strings.lua").write_text("return {}\n", encoding="utf-8")
            builder.remove_legacy_font_artifacts(mod)
            self.assertTrue((mod / "lang" / "strings.lua").exists())
            self.assertFalse((mod / "lang" / "font.lua").exists())
            self.assertFalse((mod / "lang" / "charmap.lua").exists())
            self.assertFalse((mod / "assets" / "font" / "localized.png").exists())
            archive = Path(directory) / "mod.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in mod.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(mod).as_posix())
            with zipfile.ZipFile(archive) as source:
                names = source.namelist()
            self.assertNotIn("lang/font.lua", names)
            self.assertNotIn("lang/charmap.lua", names)
            self.assertNotIn("assets/font/localized.png", names)


if __name__ == "__main__":
    unittest.main()
