import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.gold_engine import engine_string_keys, match_gold_engine_strings
from pipeline.engine_scope import is_gen2_path, load_manifest


CALLSITES = [
    {"source": "Hello!", "path": "world/gen2/World.lua", "kind": "call"},
    {"source": "Shared", "path": "battle/BattleState.lua", "kind": "source"},
    {"source": "Fallback only", "path": "battle/BattleState.lua", "kind": "romtext-fallback"},
]


class GoldEngineCatalogTests(unittest.TestCase):
    def test_gen2_scope_excludes_cross_generation_surfaces(self):
        self.assertTrue(is_gen2_path("src/world/gen2/World.lua"))
        for path in (
            "src/link/gen2/Trade.lua", "src/online/gen2/Lobby.lua",
            "src/core/gen2/State.lua", "src/import/gen2/Importer.lua",
            "src/mods/gen2/ModManager.lua", "src/ui/gen2/Tournament.lua",
        ):
            self.assertFalse(is_gen2_path(path), path)

    def test_catalog_and_gen2_scope_come_from_strings_callsites(self):
        manifest = {
            **load_manifest(),
            "forced_dynamic_keys": {"Forced": {}},
            "engine_dynamic_values": {"Dynamic": {}},
        }
        catalog, gen2 = engine_string_keys(CALLSITES, manifest)
        self.assertEqual(
            catalog,
            {"Hello!", "Shared", "Fallback only", "Forced", "Dynamic"},
        )
        self.assertEqual(gen2, {"Hello!"})

    def test_matches_full_and_gen2_metrics_and_omits_empty_values(self):
        rows = [
            ("gs.a.Hello", "Hello!", "Bonjour!"),
            ("gs.a.Shared", "Shared", "{sound_item}"),
        ]
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.gold_engine.load_manifest", return_value=manifest),
            patch(
                "pipeline.gold_engine.verified_source",
                return_value=(Path(tmp) / "src", Path(tmp), "0" * 40),
            ),
            patch("pipeline.gold_engine.iter_callsites", return_value=CALLSITES),
            patch("pipeline.gold_engine.load_engine_overrides", return_value={}),
        ):
            values, coverage = match_gold_engine_strings(rows, tmp, "fr")
        self.assertEqual(values, {"Hello!": "Bonjour!"})
        self.assertEqual(coverage["engine"]["translated"], 1)
        self.assertEqual(coverage["engine"]["total"], 3)
        self.assertEqual(coverage["engine_gen2"]["translated"], 1)
        self.assertEqual(coverage["engine_gen2"]["total"], 1)
        self.assertEqual(coverage["engine"]["source_revision"], "0" * 40)

    def test_rejects_a_gold_override_for_an_unknown_engine_key(self):
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.gold_engine.load_manifest", return_value=manifest),
            patch(
                "pipeline.gold_engine.verified_source",
                return_value=(Path(tmp) / "src", Path(tmp), "0" * 40),
            ),
            patch("pipeline.gold_engine.iter_callsites", return_value=CALLSITES),
            patch("pipeline.gold_engine.load_engine_overrides", return_value={"Stale": {"override": "X"}}),
        ):
            with self.assertRaisesRegex(ValueError, "unknown key"):
                match_gold_engine_strings([], tmp, "fr")


if __name__ == "__main__":
    unittest.main()
