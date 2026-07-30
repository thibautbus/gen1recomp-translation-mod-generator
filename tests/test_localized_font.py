from __future__ import annotations

from pathlib import Path
from hashlib import sha256
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pipeline.localized_font import (
    FONT_FILE_OFFSET,
    FRENCH_GLYPH_FIRST,
    FRENCH_GLYPH_LAST,
    SPANISH_GLYPH_FIRST,
    SPANISH_GLYPH_LAST,
    LocalizedFontError,
    extract_localized_font,
    validate_localized_rom,
)


def _rom(title: bytes = b"POKEMON RED") -> bytes:
    payload = bytearray(0x100000)
    payload[0x134 : 0x143] = title.ljust(15, b"\0")
    payload[0x148] = 0x05
    glyph_offset = FONT_FILE_OFFSET + (FRENCH_GLYPH_FIRST - 0x80) * 8
    payload[glyph_offset : glyph_offset + 8] = b"\x80\x40\x20\x10\x08\x04\x02\x01"
    return bytes(payload)


def _synthetic_font_signature(payload: bytes) -> str:
    raw = payload[FONT_FILE_OFFSET : FONT_FILE_OFFSET + 128 * 8]
    first = (FRENCH_GLYPH_FIRST - 0x80) * 8
    last = (FRENCH_GLYPH_LAST - 0x80 + 1) * 8
    return sha256(raw[first:last]).hexdigest()


def _spanish_rom(title: bytes = b"POKEMON RED") -> bytes:
    payload = bytearray(_rom(title))
    # Make the Spanish/Italian reviewed range non-empty; tests patch the
    # fingerprint so the synthetic bytes remain independent of real ROM data.
    start = FONT_FILE_OFFSET + (SPANISH_GLYPH_FIRST - 0x80) * 8
    end = FONT_FILE_OFFSET + (SPANISH_GLYPH_LAST - 0x80 + 1) * 8
    payload[start:end] = bytes((index * 17) & 0xFF for index in range(end - start))
    return bytes(payload)


class LocalizedFontTests(unittest.TestCase):
    def test_extract_writes_official_modkit_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom = root / "localized.gb"
            payload = _rom()
            rom.write_bytes(payload)
            with patch(
                "pipeline.localized_font.FRENCH_GLYPH_SHA256",
                _synthetic_font_signature(payload),
            ):
                result = extract_localized_font(rom, root / "mod")

            self.assertEqual(result["offset"], FONT_FILE_OFFSET)
            self.assertEqual(result["glyphs"]["é"], 0x102)
            image_path = root / "mod/assets/font/localized.png"
            self.assertTrue(image_path.is_file())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (19 * 8, 8))
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 255))
                self.assertEqual(image.getpixel((7, 0)), (0, 0, 0, 0))
                self.assertEqual(image.getpixel((7, 7)), (0, 0, 0, 255))
            self.assertIn("base = 0x100", (root / "mod/lang/font.lua").read_text())
            self.assertIn(
                "glyphsPerRow = 19",
                (root / "mod/lang/font.lua").read_text(),
            )
            self.assertEqual(len(result["glyphs"]), 19)
            charmap = (root / "mod/lang/charmap.lua").read_text()
            self.assertIn('["é"] = 0x102', charmap)

    def test_localized_whole_rom_sha_is_not_required(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "translated.gb"
            rom.write_bytes(_rom())
            # A deliberately non-canonical payload is accepted after basic checks.
            self.assertEqual(validate_localized_rom(rom), rom.resolve())

    def test_blue_rom_header_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "blue.gb"
            rom.write_bytes(_rom(b"POKEMON BLUE"))
            self.assertEqual(validate_localized_rom(rom), rom.resolve())

    def test_invalid_paths_and_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(LocalizedFontError, "not found"):
                validate_localized_rom(root / "missing.gb")
            bad = root / "bad.gb"
            bad.write_bytes(b"\0" * 0x100000)
            with self.assertRaisesRegex(LocalizedFontError, "header"):
                validate_localized_rom(bad)

    def test_german_map_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "localized.gb"
            rom.write_bytes(_rom())
            with patch(
                "pipeline.localized_font.GERMAN_GLYPH_SHA256",
                _synthetic_font_signature(rom.read_bytes()),
            ):
                result = extract_localized_font(rom, Path(directory) / "mod", language="de")
            self.assertEqual(result["language"], "de")
            self.assertEqual(len(result["glyphs"]), 19)

    def test_spanish_and_italian_maps_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "localized.gb"
            payload = _spanish_rom()
            rom.write_bytes(payload)
            raw = payload[FONT_FILE_OFFSET : FONT_FILE_OFFSET + 128 * 8]
            first = (SPANISH_GLYPH_FIRST - 0x80) * 8
            last = (SPANISH_GLYPH_LAST - 0x80 + 1) * 8
            digest = sha256(raw[first:last]).hexdigest()
            with (
                patch("pipeline.localized_font.SPANISH_GLYPH_SHA256", digest),
                patch("pipeline.localized_font.ITALIAN_GLYPH_SHA256", digest),
            ):
                for language in ("es", "it"):
                    result = extract_localized_font(rom, Path(directory) / language, language=language)
                    self.assertEqual(result["language"], language)
                    self.assertIn("ñ", result["glyphs"])
                    if language == "es":
                        self.assertEqual(len(result["glyphs"]), 32)
                        self.assertEqual(result["glyphs"]["¿"], 0x11E)
                        self.assertEqual(result["glyphs"]["¡"], 0x11F)
                    else:
                        self.assertEqual(len(result["glyphs"]), 30)

    def test_non_french_font_region_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "english.gb"
            rom.write_bytes(_rom())
            with self.assertRaisesRegex(LocalizedFontError, "reviewed French font"):
                extract_localized_font(rom, Path(directory) / "mod")

    def test_wrong_language_font_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "german.gb"
            payload = _rom()
            rom.write_bytes(payload)
            with patch("pipeline.localized_font.SPANISH_GLYPH_SHA256", _synthetic_font_signature(payload)):
                with self.assertRaisesRegex(LocalizedFontError, "reviewed Spanish font"):
                    extract_localized_font(rom, Path(directory) / "mod", language="es")


if __name__ == "__main__":
    unittest.main()
