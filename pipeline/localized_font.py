"""Extract a Western Gen I font from a user-supplied localized ROM.

Reviewed Red and Blue releases keep the same graphics layout but replace
unused glyph slots with localized letters. This module verifies a SHA-256 of
the relevant font-tile region, not a whole-ROM hash, reads those 1bpp tiles,
and writes a compact one-row Modkit extension page. No source ROM or complete
ROM font is written to the repository.

French/German share 19 copied faces. Italian/Spanish share 30; Spanish adds
locally derived ``¿`` and ``¡`` for a total of 32. Apostrophe ligatures remain
on the vanilla page and are not duplicated in ``lang/charmap.lua``.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from PIL import Image


# Canonical Gen1Recomp Red manifest locations.  Localized European releases
# preserve these graphics locations even though their ROM SHA-1 differs.
FONT_FILE_OFFSET = 0x11A80  # bank 4:$5A80, 128 1bpp 8x8 tiles
FONT_BYTES = 128 * 8
FRENCH_GLYPH_FIRST = 0xBA
FRENCH_GLYPH_LAST = 0xCC
# Fingerprint only the reviewed French glyph-tile range, not the ROM. This
# distinguishes the French code page from an English Red/Blue font while
# allowing either localized game and avoiding a whole-ROM SHA allowlist.
FRENCH_GLYPH_SHA256 = (
    "f64f7f22c97fd2c455c3efa6b4aa950d8f9055ae99f03e1b68daadd602c951a9"
)
GERMAN_GLYPH_FIRST = FRENCH_GLYPH_FIRST
GERMAN_GLYPH_LAST = FRENCH_GLYPH_LAST
GERMAN_GLYPH_SHA256 = FRENCH_GLYPH_SHA256

# French/German code page values from the Generation I Western character map.
# Keeping values as code points makes the generated page independent of
# whichever canonical English ROM is imported.
FRENCH_GLYPHS: Mapping[int, str] = {
    0xBA: "à",
    0xBB: "è",
    0xBC: "é",
    0xBD: "ù",
    0xBE: "ß",
    0xBF: "ç",
    0xC0: "Ä",
    0xC1: "Ö",
    0xC2: "Ü",
    0xC3: "ä",
    0xC4: "ö",
    0xC5: "ü",
    0xC6: "ë",
    0xC7: "ï",
    0xC8: "â",
    0xC9: "ô",
    0xCA: "û",
    0xCB: "ê",
    0xCC: "î",
}
GERMAN_GLYPHS = FRENCH_GLYPHS

# Italian and Spanish use a second reviewed Western code page.  D8-DE are
# apostrophe ligatures (the runtime's vanilla page already supplies those),
# so they are deliberately not copied into the extension page.
SPANISH_GLYPH_FIRST = 0xBA
SPANISH_GLYPH_LAST = 0xD7
SPANISH_GLYPH_SHA256 = (
    "ce52611128e88da45c7a321f9576d83045fe9affd44c9d5e6d5a7bb52275df7d"
)
ITALIAN_GLYPH_FIRST = SPANISH_GLYPH_FIRST
ITALIAN_GLYPH_LAST = SPANISH_GLYPH_LAST
ITALIAN_GLYPH_SHA256 = SPANISH_GLYPH_SHA256
SPANISH_GLYPHS: Mapping[int, str] = {
    0xBA: "à", 0xBB: "è", 0xBC: "é", 0xBD: "ù",
    0xBE: "À", 0xBF: "Á", 0xC0: "Ä", 0xC1: "Ö", 0xC2: "Ü",
    0xC3: "ä", 0xC4: "ö", 0xC5: "ü", 0xC6: "È", 0xC7: "É",
    0xC8: "Ì", 0xC9: "Í", 0xCA: "Ñ", 0xCB: "Ò", 0xCC: "Ó",
    0xCD: "Ù", 0xCE: "Ú", 0xCF: "á", 0xD0: "ì", 0xD1: "í",
    0xD2: "ñ", 0xD3: "ò", 0xD4: "ó", 0xD5: "ú", 0xD6: "º",
    0xD7: "&",
}
ITALIAN_GLYPHS = SPANISH_GLYPHS

_PROFILES: Mapping[str, tuple[Mapping[int, str], int, int, str, str]] = {
    "fr": (FRENCH_GLYPHS, FRENCH_GLYPH_FIRST, FRENCH_GLYPH_LAST, FRENCH_GLYPH_SHA256, "French"),
    "de": (GERMAN_GLYPHS, GERMAN_GLYPH_FIRST, GERMAN_GLYPH_LAST, GERMAN_GLYPH_SHA256, "German"),
    "es": (SPANISH_GLYPHS, SPANISH_GLYPH_FIRST, SPANISH_GLYPH_LAST, SPANISH_GLYPH_SHA256, "Spanish"),
    "it": (ITALIAN_GLYPHS, ITALIAN_GLYPH_FIRST, ITALIAN_GLYPH_LAST, ITALIAN_GLYPH_SHA256, "Italian"),
}

# The Spanish corpus uses inverted punctuation, but the reviewed Spanish ROM
# page has no dedicated faces for it. Derive them locally by rotating the
# vanilla question/exclamation glyphs from the same ROM.
_DERIVED_GLYPHS: Mapping[str, tuple[tuple[str, int], ...]] = {
    "es": (("¿", 0xE6), ("¡", 0xE7)),
}


class LocalizedFontError(ValueError):
    """A localized ROM is not suitable for font extraction."""


def validate_localized_rom(path: str | Path) -> Path:
    """Validate basic cartridge structure without a whole-ROM SHA allowlist.

    ``extract_localized_font`` subsequently checks the reviewed font-region
    SHA-256 for the selected language.
    """

    candidate = Path(path).expanduser()
    if not candidate.is_file():
        raise LocalizedFontError(f"localized ROM not found: {candidate}")
    try:
        size = candidate.stat().st_size
    except OSError as exc:  # pragma: no cover - race/permission path
        raise LocalizedFontError(f"unable to inspect localized ROM: {candidate}") from exc
    if size < 0x4000 or size % 0x4000:
        raise LocalizedFontError(
            f"localized ROM has an invalid size ({size} bytes; expected a whole Game Boy bank)"
        )
    with candidate.open("rb") as stream:
        header = stream.read(0x150)
    title = header[0x134:0x143].rstrip(b"\0")
    if len(header) < 0x150 or title not in {b"POKEMON RED", b"POKEMON BLUE"}:
        raise LocalizedFontError("localized ROM does not have a Pokémon Game Boy header")
    # Gen1Recomp's Red/Blue extractor expects a 1 MiB, MBC5-style cartridge;
    # accepting other capacities would make the fixed symbol locations unsafe.
    if len(header) <= 0x148 or header[0x148] not in {0x04, 0x05}:
        raise LocalizedFontError("localized ROM is not a Pokémon Red/Blue cartridge")
    return candidate.resolve()


def _font_offset(manifest: str | Path | None) -> int:
    if manifest is None:
        return FONT_FILE_OFFSET
    try:
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        bank, address = payload["symbols"]["FontGraphics"]
        bank, address = int(bank), int(address)
        if bank == 0 or not 0x4000 <= address < 0x8000:
            raise ValueError
        return bank * 0x4000 + address - 0x4000
    except (OSError, UnicodeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise LocalizedFontError(f"invalid Gen1Recomp ROM manifest: {manifest}") from exc


def _write_font_png(
    raw: bytes,
    codes: list[int],
    destination: Path,
    derived: tuple[tuple[str, int], ...] = (),
) -> None:
    """Write one compact 8px-high row, never the complete 128-tile ROM font."""

    # One tightly packed row avoids shipping dozens of transparent cells and
    # is intentionally unlike either complete vanilla font page.
    specs = [(code, False) for code in codes]
    specs.extend((code, True) for _glyph, code in derived)
    image = Image.new("RGBA", (len(specs) * 8, 8), (0, 0, 0, 0))
    pixels = image.load()
    for target_tile, (code, rotate) in enumerate(specs):
        source_tile = code - 0x80
        tile_x, tile_y = target_tile * 8, 0
        for y, row in enumerate(raw[source_tile * 8 : source_tile * 8 + 8]):
            for x in range(8):
                if row & (1 << (7 - x)):
                    target_x = 7 - x if rotate else x
                    target_y = 7 - y if rotate else y
                    pixels[tile_x + target_x, tile_y + target_y] = (0, 0, 0, 255)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, optimize=True)


def _lua_string(value: str) -> str:
    # JSON string quoting is valid Lua for the strings emitted here and keeps
    # Unicode glyphs readable in the generated, ignored build directory.
    return json.dumps(value, ensure_ascii=False)


def _write_lua(destination: Path, body: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")


def extract_localized_font(
    rom: str | Path,
    destination: str | Path,
    *,
    language: str = "fr",
    manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Extract a localized font into a Modkit translation directory.

    ``destination`` is the private mod root (it receives ``assets/font`` and
    ``lang``). The compact PNG is intended for the generated ZIP; the source
    ROM and complete extracted font remain private. Returned metadata is safe
    for logging/tests and contains no ROM contents or fingerprint.
    """

    aliases = {
        "fra": "fr", "french": "fr", "français": "fr", "francais": "fr",
        "deu": "de", "german": "de", "deutsch": "de",
        "spa": "es", "spanish": "es", "español": "es", "espanol": "es",
        "ita": "it", "italian": "it", "italiano": "it",
    }
    canonical = aliases.get(language.lower(), language.lower())
    try:
        glyphs, glyph_first, glyph_last, fingerprint, language_name = _PROFILES[canonical]
    except KeyError:
        raise LocalizedFontError(
            f"unsupported localized font language {language!r}; supported languages are French, German, Spanish, and Italian"
        )
    # Keep the public fingerprint constants patchable for fixture tests and
    # downstream callers that review a replacement ROM dump.
    fingerprint = {
        "fr": FRENCH_GLYPH_SHA256,
        "de": GERMAN_GLYPH_SHA256,
        "es": SPANISH_GLYPH_SHA256,
        "it": ITALIAN_GLYPH_SHA256,
    }[canonical]
    path = validate_localized_rom(rom)
    offset = _font_offset(manifest)
    data = path.read_bytes()
    if offset < 0 or offset + FONT_BYTES > len(data):
        raise LocalizedFontError("localized ROM is too small for the Gen1Recomp font location")
    raw = data[offset : offset + FONT_BYTES]
    if not any(raw):
        raise LocalizedFontError("localized ROM font area is empty")
    first = (glyph_first - 0x80) * 8
    last = (glyph_last - 0x80 + 1) * 8
    if sha256(raw[first:last]).hexdigest() != fingerprint:
        raise LocalizedFontError(
            f"localized ROM does not contain the reviewed {language_name} font"
        )

    root = Path(destination)
    image = root / "assets" / "font" / "localized.png"
    glyph_codes = sorted(glyphs)
    derived = _DERIVED_GLYPHS.get(canonical, ())
    _write_font_png(raw, glyph_codes, image, derived)

    # Pack 19 FR/DE, 30 IT, or 32 ES faces densely into one extension row.
    # Shipping the complete 128-tile ROM font would be unnecessary and rejected
    # by Modkit's ROM-content lint as a near-duplicate of the imported US font.
    mappings = {
        glyphs[code]: 0x100 + index
        for index, code in enumerate(glyph_codes)
    }
    mappings.update({
        glyph: 0x100 + len(glyph_codes) + index
        for index, (glyph, _source_code) in enumerate(derived)
    })
    font_lua = (
        f"-- Compact page generated from a user-provided {language_name} Gen I ROM.\n"
        "-- The source ROM and complete extracted font remain private/ignored.\n"
        "return {\n"
        "  localized = {\n"
        "    image = \"assets/font/localized.png\",\n"
        "    base = 0x100,\n"
        f"    glyphsPerRow = {len(glyph_codes) + len(derived)},\n"
        "  },\n"
        "}\n"
    )
    charmap_lines = [
        f"-- {language_name} glyphs extracted from the localized ROM; vanilla ASCII remains unchanged.",
        "return {",
    ]
    charmap_lines.extend(
        f"  [{_lua_string(glyph)}] = 0x{code:03X},"
        for glyph, code in mappings.items()
    )
    charmap_lines.append("}\n")
    _write_lua(root / "lang" / "font.lua", font_lua)
    _write_lua(root / "lang" / "charmap.lua", "\n".join(charmap_lines))
    return {
        "language": canonical,
        "font": str(image),
        "font_lua": str(root / "lang" / "font.lua"),
        "charmap_lua": str(root / "lang" / "charmap.lua"),
        "glyphs": mappings,
        "offset": offset,
    }
