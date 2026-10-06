import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PokedexUnitOverridesTest(unittest.TestCase):
    """The units the Pokédex prints follow the cart's own labels."""

    def entries(self, language, game, name):
        data = json.loads((ROOT / "overrides" / language / game / name).read_text(encoding="utf-8"))
        return data["entries"]

    def test_rby_prints_metric_formats_in_every_language(self):
        for language in ("fr", "de", "es", "it", "ja-Hrkt"):
            entries = self.entries(language, "rby", "engine.json")
            self.assertIn("%.1f", entries["GR. %.1fm"]["override"], language)
            self.assertIn("%.1f", entries["GEW. %.1fkg"]["override"], language)


if __name__ == "__main__":
    unittest.main()
