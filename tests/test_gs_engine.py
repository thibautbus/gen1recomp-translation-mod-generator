import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.gs_engine import engine_string_keys, load_gs_engine_scope_exclusions, match_gs_engine_strings
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
        catalog, gen2 = engine_string_keys(CALLSITES, manifest, exclusions=set())
        self.assertEqual(
            catalog,
            {"Hello!", "Shared", "Fallback only", "Forced", "Dynamic"},
        )
        self.assertEqual(gen2, {"Hello!"})

    def test_gen2_scope_drops_crystal_only_exclusions_but_keeps_them_in_catalog(self):
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        catalog, gen2 = engine_string_keys(CALLSITES, manifest, exclusions={"Hello!"})
        self.assertIn("Hello!", catalog)
        self.assertNotIn("Hello!", gen2)

    def test_engine_string_keys_defaults_to_the_real_exclusions_file(self):
        exclusions = load_gs_engine_scope_exclusions()
        self.assertIn("#MON Talk", exclusions)
        self.assertGreaterEqual(len(exclusions), 40)

    def test_load_gs_engine_scope_exclusions_rejects_malformed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclusions.json"
            path.write_text('{"schema": "wrong", "version": 1, "excluded_keys": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_gs_engine_scope_exclusions(path)
            path.write_text(
                '{"schema": "gen1recomp-translation-mods/gs-engine-scope-exclusions", '
                '"version": 1, "excluded_keys": {"X": {"reason": ""}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_gs_engine_scope_exclusions(path)

    def test_load_gs_engine_scope_exclusions_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_gs_engine_scope_exclusions(Path(directory) / "absent.json"), set())

    def test_matches_full_and_gen2_metrics_and_omits_empty_values(self):
        rows = [
            ("gs.a.Hello", "Hello!", "Bonjour!"),
            ("gs.a.Shared", "Shared", "{sound_item}"),
        ]
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.gs_engine.load_manifest", return_value=manifest),
            patch(
                "pipeline.gs_engine.verified_source",
                return_value=(Path(tmp) / "src", Path(tmp), "0" * 40),
            ),
            patch("pipeline.gs_engine.iter_callsites", return_value=CALLSITES),
            patch("pipeline.gs_engine.load_engine_overrides", return_value={}),
        ):
            values, coverage = match_gs_engine_strings(rows, tmp, "fr")
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
            patch("pipeline.gs_engine.load_manifest", return_value=manifest),
            patch(
                "pipeline.gs_engine.verified_source",
                return_value=(Path(tmp) / "src", Path(tmp), "0" * 40),
            ),
            patch("pipeline.gs_engine.iter_callsites", return_value=CALLSITES),
            patch("pipeline.gs_engine.load_engine_overrides", return_value={"Stale": {"override": "X"}}),
        ):
            with self.assertRaisesRegex(ValueError, "unknown key"):
                match_gs_engine_strings([], tmp, "fr")


if __name__ == "__main__":
    unittest.main()
