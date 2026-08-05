from pathlib import Path
import tempfile
import unittest

from pipeline.mod import generate_mod
from pipeline import builder
import zipfile


class PixelFontTests(unittest.TestCase):
    def test_latin_uses_bundled_plain_pixel_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="fr")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.font:register("ttf", {})', main)

    def test_japanese_preserves_vanilla_numeric_tiles(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="ja-Hrkt")
            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(
                'mod.content.font:register("ttf", { size = 10, tiles = "0123456789/:" })',
                main,
            )

    def test_generation_does_not_create_rom_font_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            mod = generate_mod([], Path(directory) / "mod", language="de")
            self.assertFalse((mod / "assets" / "font").exists())
            self.assertFalse((mod / "lang" / "font.lua").exists())
            self.assertFalse((mod / "lang" / "charmap.lua").exists())

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
            self.assertIn('mod.content.font:register("ttf", { size = 10, tiles = "0123456789/:" })', main)
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
