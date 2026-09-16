import json
import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides, printf_directives


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("fr", "de", "es", "it", "ja-Hrkt")

# RBY catalogue keys introduced by gen1recomp d633cb4d, once kept in the
# upstream-only engine_upstream.json layer because the v0.2.41 pin did not
# have them yet. Every one of them is a real Strings() callsite on the
# current pin, so they live in the pinned engine.json layer now; the upstream
# layer stays available (empty) for keys that are not pinned yet.
PROMOTED_KEYS = {
    "%s's PC", "%s's NEST", "AREA UNKNOWN", "Congrats! This", "FLY TO?", "HEAL",
    "ITEMS", "MONEY/¥%d", "PIKACHU'S BEACH", "POKéDEX.", "POKéMON BLUE",
    "POKéMON YELLOW", "TIME/  %d:%02d", "The party is full!", "To %s",
    "completed your", "diploma certifies", "that you have",
}
# Resolved by the automatic corpus matcher with the cart's own wording, which
# a composed override would only shadow (e.g. fr "PC DE LEO", fr "STOCKER").
CORPUS_OWNED_KEYS = {"BILL'S PC", "DEPOSIT"}
# Gone from the pinned catalogue (TIME/%3d:%02d became TIME/  %d:%02d).
RETIRED_KEYS = {"TIME/%3d:%02d", "Which move?"}
LINK_ONLY_KEYS = {"No.%03d", "IDNo.%05d"}


class RbyEngineOverrideTests(unittest.TestCase):
    def _pinned(self, language):
        return load_engine_overrides(ROOT / "overrides" / language / "rby" / "engine.json")

    def _upstream(self, language):
        return load_engine_overrides(ROOT / "overrides" / language / "rby" / "engine_upstream.json")

    def test_promoted_keys_live_in_the_pinned_layer_for_every_language(self):
        self.assertEqual(len(PROMOTED_KEYS), 18)
        for language in LANGUAGES:
            pinned = self._pinned(language)
            self.assertTrue(PROMOTED_KEYS <= set(pinned), (language, sorted(PROMOTED_KEYS - set(pinned))))
            for key in PROMOTED_KEYS:
                row = pinned[key]
                self.assertIn(row["reason"], {"engine-original", "engine-contract-gap"}, (language, key))
                self.assertTrue(row["override"].strip(), (language, key))
                self.assertIn("Promoted from the upstream-only", row["provenance"], (language, key))

    def test_upstream_layer_no_longer_duplicates_pinned_keys(self):
        for language in LANGUAGES:
            upstream = self._upstream(language)
            self.assertTrue(set(upstream).isdisjoint(self._pinned(language)), language)
            self.assertTrue((PROMOTED_KEYS | CORPUS_OWNED_KEYS | RETIRED_KEYS).isdisjoint(upstream), language)

    def test_corpus_owned_and_retired_keys_are_not_overridden(self):
        for language in LANGUAGES:
            pinned = self._pinned(language)
            self.assertTrue(CORPUS_OWNED_KEYS.isdisjoint(pinned), language)
            self.assertTrue(RETIRED_KEYS.isdisjoint(pinned), language)
            self.assertTrue(LINK_ONLY_KEYS.isdisjoint(pinned), language)

    def test_printf_placeholders_are_preserved(self):
        placeholder_keys = {"%s's PC", "%s's NEST", "MONEY/¥%d", "TIME/  %d:%02d", "To %s"}
        for language in LANGUAGES:
            pinned = self._pinned(language)
            for key in placeholder_keys:
                self.assertEqual(
                    printf_directives(key),
                    printf_directives(pinned[key]["override"]),
                    (language, key),
                )

    def test_area_unknown_does_not_repeat_the_engine_leading_space(self):
        # ui/TownMap.lua draws " " .. Strings("AREA UNKNOWN").
        for language in LANGUAGES:
            value = self._pinned(language)["AREA UNKNOWN"]["override"]
            self.assertEqual(value, value.strip(), language)

    def test_files_keep_engine_override_schema(self):
        for language in LANGUAGES:
            data = json.loads((ROOT / "overrides" / language / "rby" / "engine_upstream.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "gen1recomp-translation-mods/engine-overrides")
            self.assertEqual(data["version"], 1)


if __name__ == "__main__":
    unittest.main()
