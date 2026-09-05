import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.gs_engine import engine_string_keys, load_gs_engine_scope_exclusions, match_gs_engine_strings
from pipeline.engine import load_engine_overrides
from pipeline.engine_scope import is_gen2_path, load_manifest
from pipeline.engine_profile import UPSTREAM_PROFILE



def load_engine_no_op_entries(language):
    report = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "gsc" / "engine_fallbacks.json").read_text(
            encoding="utf-8",
        ),
    )
    return report["languages"][language].get("no_op_entries", {})


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

    def test_all_languages_translate_new_gen2_options_and_crystal_talk_label(self):
        keys = {
            "AUDIO", "BACK", "BATTLE OPTIONS", "BATTLE SIZE", "EXTRAS",
            "GRAPHICS", "KEY BAR", "UI LETTERBOX", "UNAVAILABLE", "VIDEO",
            "VSYNC", "#MON Talk",
        }
        root = Path(__file__).resolve().parents[1]
        for language in ("fr", "de", "es", "it", "ja-Hrkt", "ko"):
            overrides = load_engine_overrides(root / "overrides" / language / "gsc" / "engine.json")
            no_op = load_engine_no_op_entries(language)
            available = {**no_op, **overrides}
            self.assertEqual(keys <= set(available), True, language)
            for key in keys:
                self.assertTrue(available[key]["override"].strip(), f"{language}: {key}")
                self.assertTrue(available[key]["provenance"].strip(), f"{language}: {key}")

    def test_japanese_crystal_shape_gaps_have_explicit_overrides(self):
        root = Path(__file__).resolve().parents[1]
        overrides = load_engine_overrides(root / "overrides" / "ja-Hrkt" / "gsc" / "engine.json")
        no_op = load_engine_no_op_entries("ja-Hrkt")
        available = {**no_op, **overrides}
        keys = {
            "%s is\nnot compatible\vwith %s.",
            "???",
            "Day",
            "Hm… %s\ncame from %s\vin a trade?\f%s\nwas where %s\vmet %s!",
        }
        self.assertEqual(keys <= set(available), True)
        for key in keys:
            self.assertTrue(available[key]["override"])
            self.assertIn("Crystal", available[key]["provenance"])

    def test_load_gs_engine_scope_exclusions_rejects_malformed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclusions.json"
            path.write_text('{"schema": "wrong", "version": 1, "excluded_keys": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_gs_engine_scope_exclusions(path)
            path.write_text(
                '{"schema": "gen1recomp-translation-mods/gs-engine-scope-exclusions", '
                '"version": 2, "source_revision": "babac97526c4e95445f8710f397da9f0dfd10e16", '
                '"excluded_keys": {"X": {"reason": ""}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_gs_engine_scope_exclusions(path)
            path.write_text(
                '{"schema": "gen1recomp-translation-mods/gs-engine-scope-exclusions", '
                '"version": 2, "source_revision": "0000000000000000000000000000000000000000", '
                '"excluded_keys": {}}', encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "source_revision"):
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

    def test_runtime_catalogue_drops_identity_matches_for_every_language(self):
        rows = [
            ("gs.identity", "SAME", "SAME"),
            ("gs.translated", "Hello!", "Bonjour!"),
        ]
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        callsites = [
            {"source": "SAME", "path": "world/gen2/World.lua", "kind": "call"},
            {"source": "Hello!", "path": "world/gen2/World.lua", "kind": "call"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for language in ("fr", "de", "es", "it", "ja-Hrkt", "ko"):
                with (
                    patch("pipeline.gs_engine.load_manifest", return_value=manifest),
                    patch(
                        "pipeline.gs_engine.verified_source",
                        return_value=(Path(tmp) / "src", Path(tmp), "0" * 40),
                    ),
                    patch("pipeline.gs_engine.iter_callsites", return_value=callsites),
                    patch("pipeline.gs_engine.load_engine_overrides", return_value={}),
                ):
                    values, coverage = match_gs_engine_strings(rows, tmp, language)
                self.assertEqual(values, {"Hello!": "Bonjour!"}, language)
                self.assertFalse(any(key == value for key, value in values.items()), language)
                # The identity remains counted and traceable by the matcher;
                # only the runtime catalogue is filtered.
                self.assertEqual(coverage["engine"]["translated"], 2, language)
                self.assertEqual(coverage["engine"]["total"], 2, language)
                self.assertEqual(coverage["engine"]["percent"], 100.0, language)
                self.assertEqual(coverage["engine_gen2"]["translated"], 2, language)
                self.assertEqual(coverage["engine_gen2"]["total"], 2, language)

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

    def test_rejects_a_fallback_entry_for_an_unknown_engine_key_on_the_upstream_profile(self):
        # engine_fallbacks.json is audited against the upstream engine; a
        # stale entry there should be caught the same way a stale override
        # already is (gen1recomp-translation-mods review, 2026-09-01).
        manifest = {**load_manifest(), "forced_dynamic_keys": {}, "engine_dynamic_values": {}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pipeline.gs_engine.load_manifest", return_value=manifest),
            patch("pipeline.gs_engine.checkout_revision", return_value="local-unversioned"),
            patch("pipeline.gs_engine.iter_callsites", return_value=CALLSITES),
            patch("pipeline.gs_engine.load_engine_overrides", return_value={}),
            patch(
                "pipeline.gs_engine.load_gs_engine_fallbacks",
                return_value={"fr": {"Stale fallback": {"override": "X"}}},
            ),
        ):
            (Path(tmp) / "src").mkdir()
            with self.assertRaisesRegex(ValueError, "unknown key"):
                match_gs_engine_strings([], tmp, "fr", engine_profile=UPSTREAM_PROFILE)

    def test_pinned_profile_does_not_check_fallback_entries_against_the_pin(self):
        # engine_fallbacks.json tracks literals from the upstream engine, so
        # a fallback key absent from the currently pinned revision (not yet
        # released) must not fail a pinned-profile build.
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
            patch(
                "pipeline.gs_engine.load_gs_engine_fallbacks",
                return_value={"fr": {"Not yet pinned": {"override": "X"}}},
            ),
        ):
            values, _report = match_gs_engine_strings([], tmp, "fr")
            self.assertIsInstance(values, dict)


if __name__ == "__main__":
    unittest.main()
