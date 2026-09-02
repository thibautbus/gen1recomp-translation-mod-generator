"""Exhaustive checks for the engine Strings added by the upstream Gen-2 work."""

import json
import unittest
from pathlib import Path

from pipeline.engine import check_printf_directives, load_engine_overrides
from pipeline.gs_engine import load_gs_engine_fallbacks


LANGUAGES = ("fr", "de", "es", "it", "ja-Hrkt", "ko")
# 550 at the batch's own genesis, +1: a later, unrelated upstream commit
# (v0.2.51) split "1, 2 and… %s forgot %s!" -- one of the original 550 --
# into two separate Strings() calls ("1, 2 and…" plus a new "Poof! %s
# forgot %s!" carrying the two printf args the combined key used to). Both
# now stand in for that one original batch slot in
# config/gsc/new_engine_set_v0241.json, so the frozen count grows by
# exactly the one key this split actually added.
NEW_ENTRY_COUNT = 551
# The corpus confirms AM/PM as the English source in these locales.  They are
# intentionally omitted from those override tables so the runtime does not
# carry a pointless identity override; the complete-set check accounts for
# that explicit no-op policy below.
NO_OP_ENGINE_KEYS = {"AM", "PM"}
# The specific 550-key batch the upstream Gen-2 work added, frozen at the
# time it landed. This has to be an explicit, persisted list rather than
# "every key currently tagged engine-corpus": the engine has kept growing
# since (a later gen1recomp pin bump surfaced dozens more real Strings()
# callsites, corpus-matched the same way once translated), and deriving the
# batch from the tag would silently absorb every later addition into what
# is supposed to be one fixed, already-closed batch.
NEW_ENGINE_SET_PATH = Path("config") / "gsc" / "new_engine_set_v0241.json"


def _load_new_engine_set(path=NEW_ENGINE_SET_PATH):
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
            or data.get("schema") != "gen1recomp-translation-mods/gs-new-engine-set"
            or data.get("version") != 1
            or not isinstance(data.get("keys"), list)):
        raise ValueError("unsupported new-engine-set schema")
    keys = data["keys"]
    if len(keys) != len(set(keys)) or not all(isinstance(k, str) and k for k in keys):
        raise ValueError("new-engine-set keys must be a list of unique non-empty strings")
    return set(keys)


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
        universe = _load_new_engine_set()
        self.assertEqual(len(universe), NEW_ENTRY_COUNT)
        self.assertTrue(NO_OP_ENGINE_KEYS <= universe)
        for language in LANGUAGES:
            entries = self._entries(language)
            fallback = load_gs_engine_fallbacks(language)[language]
            resolved = {
                key for key in universe - NO_OP_ENGINE_KEYS
                if key not in fallback and entries.get(key, {}).get("override") not in (None, key)
            }
            missing = universe - NO_OP_ENGINE_KEYS - resolved - set(fallback)
            self.assertFalse(missing, (language, sorted(missing)))
            self.assertTrue(resolved.isdisjoint(fallback), language)

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
