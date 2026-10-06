import tempfile
import unittest
from pathlib import Path

from pipeline.shared.builder import BuildError
from pipeline.shared.pokedex_metrics import lua_catalog, parse_pokedex_metrics, species_metrics


ENTRIES = """const struct PokedexEntry gPokedexEntries[] =
{
    [NATIONAL_DEX_NONE] =
    {
        .categoryName = _("UNKNOWN"),
        .height = 0,
        .weight = 0,
    },

    [NATIONAL_DEX_BULBASAUR] =
    {
        .categoryName = _("SEED"),
        .height = 7,
        .weight = 69,
        .description = gBulbasaurPokedexText,
    },

    [NATIONAL_DEX_NIDORAN_F] =
    {
        .categoryName = _("POISON PIN"),
        .height = 4,
        .weight = 70,
    },
};
"""


class PokedexMetricsTest(unittest.TestCase):
    def test_parses_decimetres_and_hectograms_by_national_dex_name(self):
        self.assertEqual(parse_pokedex_metrics(ENTRIES), {"BULBASAUR": (7, 69), "NIDORAN_F": (4, 70)})

    def test_species_metrics_converts_to_metres_and_kilograms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pokedex_entries.h"
            path.write_text(ENTRIES, encoding="utf-8")
            self.assertEqual(species_metrics(path, ["BULBASAUR", "NIDORAN_F"]),
                             {"BULBASAUR": (0.7, 6.9), "NIDORAN_F": (0.4, 7.0)})
            with self.assertRaisesRegex(BuildError, "MEW"):
                species_metrics(path, ["BULBASAUR", "MEW"])

    def test_lua_catalog_lists_metres_then_kilograms(self):
        body = lua_catalog({"NIDORAN_F": (0.4, 7.0), "BULBASAUR": (0.7, 6.9)}, "species_metrics")
        self.assertIn('  ["BULBASAUR"] = { 0.7, 6.9 },\n  ["NIDORAN_F"] = { 0.4, 7 },\n', body)


if __name__ == "__main__":
    unittest.main()
