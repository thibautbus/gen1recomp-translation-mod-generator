"""Exhaustive checks for the engine Strings added by the upstream Gen-2 work."""

import json
import unittest
from pathlib import Path

from pipeline.engine import check_printf_directives, load_engine_overrides
from pipeline.gs_engine import load_gs_engine_fallbacks


LANGUAGES = ("fr", "de", "es", "it", "ja-Hrkt", "ko")
NEW_ENTRY_REASONS = {"engine-corpus"}
NEW_ENTRY_COUNT = 550
# The corpus confirms AM/PM as the English source in these locales.  They are
# intentionally omitted from those override tables so the runtime does not
# carry a pointless identity override; the complete-set check accounts for
# that explicit no-op policy below.
NO_OP_ENGINE_KEYS = {"AM", "PM"}


class GoldGen2EngineOverrideTests(unittest.TestCase):
    def _entries(self, language):
        return load_engine_overrides(
            Path("overrides") / language / "gsc" / "engine.json",
        )

    def _no_op_entries(self, language):
        report = json.loads(
            (Path("config") / "gsc" / "engine_fallbacks.json").read_text(
                encoding="utf-8",
            ),
        )
        return report["languages"][language].get("no_op_entries", {})

    def test_every_language_carries_the_complete_new_engine_set(self):
        sets = []
        for language in LANGUAGES:
            entries = self._entries(language)
            selected = {
                key: row for key, row in entries.items()
                if row.get("reason") in NEW_ENTRY_REASONS
            }
            fallback = load_gs_engine_fallbacks(language)[language]
            self.assertEqual(len(set(selected) | set(fallback) | NO_OP_ENGINE_KEYS), NEW_ENTRY_COUNT, language)
            self.assertTrue(set(selected).isdisjoint(fallback), language)
            self.assertTrue(all(row.get("provenance") for row in selected.values()))
            sets.append(set(selected) | set(fallback) | NO_OP_ENGINE_KEYS)
        for selected in sets[1:]:
            self.assertEqual(selected, sets[0])

    def test_runtime_overrides_never_emit_english_fallbacks(self):
        for language in LANGUAGES:
            entries = self._entries(language)
            fallback = load_gs_engine_fallbacks(language)[language]
            for source, row in entries.items():
                self.assertNotEqual(row["override"], source, (language, source))
                if row.get("reason") == "engine-corpus":
                    override = row["override"]
                    self.assertFalse(check_printf_directives(source, override), (language, source))
                    self.assertIn("Corpus automatic unique match", row["provenance"])
            self.assertTrue(set(entries).isdisjoint(fallback), language)
            for source, row in fallback.items():
                self.assertEqual(row["override"], source, (language, source))
                self.assertFalse(check_printf_directives(source, row["override"]), (language, source))
                if row.get("original_reason"):
                    self.assertTrue(row.get("original_provenance"), (language, source))

            # Existing invariant symbols and other non-corpus identities are
            # retained for auditability, but never shipped as runtime rows.
            no_op = self._no_op_entries(language)
            self.assertTrue(set(entries).isdisjoint(no_op), language)
            for source, row in no_op.items():
                self.assertEqual(row["override"], source, (language, source))
                self.assertTrue(row.get("original_reason"), (language, source))
                self.assertTrue(row.get("original_provenance"), (language, source))


if __name__ == "__main__":
    unittest.main()
