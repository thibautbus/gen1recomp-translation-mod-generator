import json
import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides, printf_directives


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("fr", "de", "es", "it", "ja-Hrkt")

# These are the new RBY catalogue keys introduced by d633cb4d and absent from
# the v0.2.41 scaffold.  The two TradeAnim keys are intentionally excluded:
# they are link-only and their printf arguments are not an RBY translation
# contract.  Review keys are present so the localized text is available while
# their visual fit remains an explicit follow-up concern.
ELIGIBLE_KEYS = {
    "%s's PC", "BILL'S PC", "Congrats! This", "DEPOSIT", "HEAL", "ITEMS",
    "MONEY/¥%d", "POKéDEX.", "POKéMON BLUE", "POKéMON YELLOW",
    "TIME/%3d:%02d", "The party is full!", "Which move?", "completed your",
    "diploma certifies", "that you have",
}
REVIEW_KEYS = {"%s's NEST", "AREA UNKNOWN", "FLY TO?", "PIKACHU'S BEACH", "To %s"}
LINK_ONLY_KEYS = {"No.%03d", "IDNo.%05d"}


class RbyEngineOverrideTests(unittest.TestCase):
    def test_pinned_layer_does_not_contain_upstream_only_keys(self):
        for language in LANGUAGES:
            pinned = load_engine_overrides(ROOT / "overrides" / language / "rby" / "engine.json")
            self.assertTrue(LINK_ONLY_KEYS.isdisjoint(pinned), language)
            self.assertTrue((ELIGIBLE_KEYS | REVIEW_KEYS).isdisjoint(pinned), language)

    def test_all_new_eligible_and_review_keys_have_all_language_overrides(self):
        expected = ELIGIBLE_KEYS | REVIEW_KEYS
        self.assertEqual(len(ELIGIBLE_KEYS), 16)
        self.assertEqual(len(REVIEW_KEYS), 5)
        for language in LANGUAGES:
            overrides = load_engine_overrides(ROOT / "overrides" / language / "rby" / "engine_upstream.json")
            self.assertTrue(expected <= set(overrides), language)
            self.assertTrue(LINK_ONLY_KEYS.isdisjoint(overrides), language)
            for key in expected:
                row = overrides[key]
                self.assertEqual(row["reason"], "engine-original", (language, key))
                self.assertTrue(row["override"].strip(), (language, key))

    def test_new_printf_placeholders_are_preserved(self):
        placeholder_keys = {
            "%s's PC", "%s's NEST", "MONEY/¥%d", "TIME/%3d:%02d", "To %s",
        }
        for language in LANGUAGES:
            overrides = load_engine_overrides(ROOT / "overrides" / language / "rby" / "engine_upstream.json")
            for key in placeholder_keys:
                self.assertEqual(
                    printf_directives(key),
                    printf_directives(overrides[key]["override"]),
                    (language, key),
                )

    def test_files_keep_engine_override_schema(self):
        for language in LANGUAGES:
            data = json.loads((ROOT / "overrides" / language / "rby" / "engine_upstream.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "gen1recomp-translation-mods/engine-overrides")
            self.assertEqual(data["version"], 1)


if __name__ == "__main__":
    unittest.main()
