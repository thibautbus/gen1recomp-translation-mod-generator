import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pipeline.builder import _which_luajit
from pipeline.gs_join import GsJoinEntry, NO_MATCH, UNIQUE
from pipeline.gs_mod import (
    attach_gs_validation, build_gs_dialogue_mod, gs_archive_name,
    gs_mod_id, gs_oak_speech_catalog_from_join,
    gs_text_catalog_from_join, generate_gs_mod, package_gs_mod,
    run_gs_release_gates,
)
from pipeline.gs_mod import _gs_ui_labels, _write_dialogue_gate_expectation, _write_gate_expectations
from pipeline.project import project_version
from pipeline.engine_profile import UPSTREAM_PROFILE

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / ".cache" / "dependencies" / "gen1recomp"
MODKIT = ENGINE_ROOT / "tools" / "modkit.py"
GATE_SCRIPT = ROOT / "tools" / "gate_gs_package.lua"
DIALOGUE_GATE_SCRIPT = ROOT / "tools" / "gate_gs_dialogue.lua"
REGISTRIES_GATE_SCRIPT = ROOT / "tools" / "gate_gs_registries.lua"
RBY_FIXTURE = ROOT / "tools" / "gen2_gate_fixtures" / "rby_translation"


class IdentifierTests(unittest.TestCase):
    def test_mod_id_is_generation_scoped_not_game_scoped(self):
        # The id encodes generation, never today's game list, so
        # Gold/Silver/Crystal never force a rename.
        self.assertEqual(gs_mod_id("fr"), "translation-fr-gen2")
        self.assertEqual(gs_mod_id("FR"), "translation-fr-gen2")

    def test_archive_name_is_distinct_from_the_rby_one(self):
        name = gs_archive_name("fr", "1.2.3")
        self.assertEqual(name, "translation-fr-gen2-1.2.3.zip")
        self.assertNotEqual(name, "translation-fr-1.2.3.zip")


class GenerateGsModTests(unittest.TestCase):
    def test_manifest_declares_gold_and_silver_and_nothing_else(self):
        # Built and extracted from a Gold ROM only, but declared compatible
        # with Silver too: Gold/Silver share the same dialogue text-table
        # addresses closely enough (see pipeline/gs_mod.py's comment above
        # this manifest body) that the mod's overrides apply cleanly to a
        # Silver save via src/mods/ModTargets.lua's specApplies(), and a
        # miss there just silently falls back to Silver's own English text
        # rather than showing anything wrong.
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "translation-fr-gen2")
            self.assertEqual(manifest["games"], ["gold", "silver"])
            self.assertEqual(manifest["api"], 2)
            self.assertEqual(manifest["entry"], "main.lua")
            self.assertEqual(manifest["permissions"], [])
            self.assertIn("Gold", manifest["description"])

    def test_main_lua_has_no_content_registrations_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn("return function(mod)", main)
            self.assertIn('font:register("ttf"', main)
            for registry in ("text:override", "strings:override", "pokemon:patch"):
                self.assertNotIn(registry, main)

    def test_custom_mod_id_and_description_are_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", mod_id="custom-id", language="fr",
                target_description="Custom description.",
            )
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "custom-id")
            self.assertEqual(manifest["description"], "Custom description.")

    def test_crystal_catalog_declares_crystal_and_writes_a_conditional_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                text_catalog={"55:0001": "Bonjour!"},
                crystal_text_catalog={"00:0001": "Salut!"},
            )
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["games"], ["gold", "silver", "crystal"])
            self.assertIn("Crystal", manifest["description"])
            crystal_lua = (mod_dir / "lang" / "dialogue_crystal.lua").read_text(encoding="utf-8")
            self.assertIn('["00:0001"] = "Salut!"', crystal_lua)
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('GameVersion.get() == "crystal"', main)
            self.assertIn("crystal_game_version", main)
            self.assertIn("dialogue_crystal.lua", main)
            # Gold/Silver's own base dialogue catalog is untouched by the
            # Crystal layer -- both files coexist, only one applies at a time.
            self.assertTrue((mod_dir / "lang" / "dialogue.lua").is_file())

    def test_empty_crystal_catalog_still_declares_crystal_with_no_layer_file(self):
        # Korean: Crystal has no corpus for it, so the catalog resolves empty,
        # but the mod still declares Crystal compatibility (its shared
        # engine-string catalog still applies) -- see build_gs()'s own
        # comment on join_crystal_dialogue()'s graceful degradation.
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="ko", crystal_text_catalog={},
            )
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["games"], ["gold", "silver", "crystal"])
            self.assertFalse((mod_dir / "lang" / "dialogue_crystal.lua").exists())

    def test_crystal_feature_catalogs_are_written_behind_the_crystal_guard(self):
        crystal_catalogs = {
            "strings": {"Crystal only": "Cristal seulement"},
            "rom_text": {"_CrystalOnlyText": "Texte Cristal"},
            "item_names": {"BLUE_CARD": "CARTE BLEUE"},
            "trainer_class_names": {"MYSTICALMAN": "MYSTIQUE"},
            "landmarks": {"LANDMARK_BATTLE_TOWER": "TOUR DE COMBAT"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr", crystal_catalogs=crystal_catalogs,
            )
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["games"], ["gold", "silver", "crystal"])
            for name in crystal_catalogs:
                self.assertTrue((mod_dir / "lang" / f"crystal_{name}.lua").is_file())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('GameVersion.get() == "crystal"', main)
            self.assertIn("mod.content.rom_text:override(id, value)", main)
            self.assertIn("mod.content.items:patch(id, { name = value })", main)
            self.assertIn("mod.content.trainers:patch(id, { name = value })", main)
            self.assertIn("mod.content.landmarks:patch(id, { name = value })", main)

    def test_silver_dex_text_writes_a_conditional_layer_over_golds(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"species_dex_text": {"BULBASAUR": "Texte Or."}},
                silver_dex_text_catalog={"BULBASAUR": "Texte Argent."},
                silver_dex_text2_catalog={"BULBASAUR": "Texte Argent, page 2."},
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Or."',
                (mod_dir / "lang" / "species_dex_text.lua").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Argent."',
                (mod_dir / "lang" / "species_dex_text_silver.lua").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Argent, page 2."',
                (mod_dir / "lang" / "species_dex_text2_silver.lua").read_text(encoding="utf-8"),
            )
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('SilverGameVersion.get() == "silver"', main)
            self.assertIn("silver_game_version", main)
            self.assertIn("species_dex_text_silver", main)
            self.assertIn("species_dex_text2_silver", main)
            self.assertIn("dexEntry = { text = value }", main)
            self.assertIn("dexEntry = { text2 = value }", main)
            # The unconditional Gold catalog registration still runs too --
            # the Silver layer patches over it at runtime, it doesn't replace it.
            self.assertIn("species_dex_text", main)

    def test_no_silver_dex_text_argument_writes_no_conditional_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            self.assertFalse((mod_dir / "lang" / "species_dex_text_silver.lua").exists())
            self.assertFalse((mod_dir / "lang" / "species_dex_text2_silver.lua").exists())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("silver_game_version", main)

    def test_crystal_dex_text_writes_a_conditional_layer_over_golds(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"species_dex_text": {"BULBASAUR": "Texte Or."}},
                crystal_dex_text_catalog={"BULBASAUR": "Texte Cristal."},
                crystal_dex_text2_catalog={"BULBASAUR": "Texte Cristal, page 2."},
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Or."',
                (mod_dir / "lang" / "species_dex_text.lua").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Cristal."',
                (mod_dir / "lang" / "species_dex_text_crystal.lua").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '["BULBASAUR"] = "Texte Cristal, page 2."',
                (mod_dir / "lang" / "species_dex_text2_crystal.lua").read_text(encoding="utf-8"),
            )
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('CrystalDexGameVersion.get() == "crystal"', main)
            self.assertIn("crystal_dex_game_version", main)
            self.assertIn("species_dex_text_crystal", main)
            self.assertIn("species_dex_text2_crystal", main)
            self.assertIn("dexEntry = { text = value }", main)
            self.assertIn("dexEntry = { text2 = value }", main)
            # The unconditional Gold catalog registration still runs too --
            # the Crystal layer patches over it at runtime, it doesn't replace it.
            self.assertIn("species_dex_text", main)

    def test_no_crystal_dex_text_argument_writes_no_conditional_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            self.assertFalse((mod_dir / "lang" / "species_dex_text_crystal.lua").exists())
            self.assertFalse((mod_dir / "lang" / "species_dex_text2_crystal.lua").exists())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("crystal_dex_game_version", main)

    def test_no_crystal_argument_keeps_the_gold_silver_only_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["games"], ["gold", "silver"])
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("crystal_game_version", main)


class GsReleaseGateFlowTests(unittest.TestCase):
    def test_registry_expectations_reject_missing_or_empty_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                _write_gate_expectations(Path(tmp) / "mod", {})
            catalogs = {name: {"ID": "VALUE"} for name in (
                "strings", "species_names", "species_kinds", "species_dex_text", "species_dex_text2", "move_names",
                "item_names", "trainer_class_names", "landmarks", "type_names",
                "status_labels", "oak_speech",
            )}
            catalogs["landmarks"] = {}
            with self.assertRaisesRegex(RuntimeError, "empty"):
                _write_gate_expectations(Path(tmp) / "mod", catalogs)

    def test_all_gates_run_before_a_release_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            (engine / "tools").mkdir(parents=True)
            mod = generate_gs_mod(root / "mod", language="fr", text_catalog={"55:0001": "Bonjour!"})
            entries = [GsJoinEntry("55:0001", None, "Hello", "Bonjour!", UNIQUE, "gs.a.One")]
            log_fn = lambda message: None
            with patch("pipeline.gs_mod._run") as run:
                # _run is mocked, so luajit is never actually invoked; only
                # run_gs_release_gates' own "does this path exist" check
                # needs a real file. sys.executable is guaranteed to exist
                # everywhere the suite runs, unlike a hardcoded system
                # luajit path -- CI's build-then-test job builds LuaJIT from
                # source *after* running the suite, so a hardcoded
                # /usr/bin/luajit is not there yet at test time.
                report = run_gs_release_gates(
                    mod, entries, engine, sys.executable,
                    catalogs={name: {"ID": "X"} for name in (
                        "strings", "species_names", "species_kinds", "species_dex_text", "species_dex_text2", "move_names",
                        "item_names", "trainer_class_names", "landmarks", "type_names",
                        "status_labels", "oak_speech",
                    )},
                    log_fn=log_fn,
                )
            self.assertIn("coverage", report)
            self.assertEqual(report["validation"]["policy"], "english-fallback")
            self.assertEqual(report["validation"]["coverage"]["translated"], 1)
            self.assertEqual(len(report["validation"]["checks"]), 4)
            self.assertTrue(all(check["status"] == "passed" for check in report["validation"]["checks"]))
            commands = [Path(call.args[0][1]).name for call in run.call_args_list]
            self.assertEqual(commands, ["gate_gen2.lua", "gate_gs_dialogue.lua", "gate_gs_registries.lua"])
            # A real Windows GUI report: the three gate scripts' own output
            # never reached the "Build failed" dialog at all (an empty
            # detail, just an exit code) -- run_gs_release_gates never
            # forwarded log_fn to _run(), so a console-less frozen GUI had
            # nowhere for that output to go. Every gate call must thread it
            # through.
            self.assertTrue(all(call.kwargs.get("log_fn") is log_fn for call in run.call_args_list))

    def test_manifest_keeps_only_compact_engine_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            (engine / "tools").mkdir(parents=True)
            mod = generate_gs_mod(root / "mod", language="fr", text_catalog={"55:0001": "Bonjour!"})
            entries = [GsJoinEntry("55:0001", None, "Hello", "Bonjour!", UNIQUE, "gs.a.One")]
            coverage = {
                "rom": {"translated": 8, "total": 9, "percent": 88.89},
                "rom_dialogue": {"translated": 1, "total": 2, "percent": 50.0},
                "rom_catalogs": {"translated": 7, "total": 7, "percent": 100.0},
                "ambiguous": [], "unmatched": [], "ignored_markup_only": 0,
                "engine": {
                    "translated": 2, "total": 4, "percent": 50.0,
                    "source_revision": "abc", "details": {"Private English": "unmatched"},
                },
                "engine_gen2": {
                    "translated": 1, "total": 2, "percent": 50.0,
                    "source_revision": "abc", "scope": "gen2",
                },
                "crystal": {
                    "engine_crystal": {
                        "translated": 48, "total": 48, "percent": 100.0,
                        "policy": "english-fallback",
                    },
                    "aggregate": {"translated": 54, "total": 54, "percent": 100.0},
                },
            }
            catalogs = {name: {"ID": "X"} for name in (
                "strings", "species_names", "species_kinds", "species_dex_text", "species_dex_text2", "move_names",
                "item_names", "trainer_class_names", "landmarks", "type_names",
                "status_labels", "oak_speech",
            )}
            with patch("pipeline.gs_mod._run"):
                # See the same-shaped call above: _run is mocked, so this
                # only needs to be a real file, not a real luajit.
                report = run_gs_release_gates(
                    mod, entries, engine, sys.executable, catalogs=catalogs, coverage=coverage,
                )
        manifest_coverage = report["validation"]["coverage"]
        self.assertEqual(manifest_coverage["translated"], 8)
        self.assertNotIn("rom_dialogue", manifest_coverage)
        self.assertNotIn("rom_catalogs", manifest_coverage)
        self.assertEqual(manifest_coverage["engine"], {
            "translated": 2, "total": 4, "percent": 50.0, "source_revision": "abc",
        })
        self.assertNotIn("details", manifest_coverage["engine"])
        self.assertEqual(manifest_coverage["crystal"]["engine_crystal"]["translated"], 48)
        self.assertEqual(manifest_coverage["crystal"]["aggregate"]["total"], 54)

    def test_validation_provenance_is_attached_deterministically(self):
        validation = {
            "schema": 1,
            "policy": "english-fallback",
            "coverage": {"translated": 1, "total": 2, "percent": 50.0},
            "checks": [{"tool": "fixture", "version": "1", "command": "fixture", "status": "passed"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            mod = generate_gs_mod(Path(tmp) / "mod", language="fr")
            attach_gs_validation(mod, validation)
            first = (mod / "manifest.json").read_bytes()
            attach_gs_validation(mod, validation)
            second = (mod / "manifest.json").read_bytes()
            manifest = json.loads(second)
        self.assertEqual(first, second)
        self.assertEqual(manifest["validation"], validation)

    def test_registry_expectations_gate_runs_against_real_loader(self):
        luajit = _which_luajit()
        if luajit is None or not ENGINE_ROOT.is_dir():
            self.skipTest("cached Gen1Recomp/LuaJIT unavailable")
        catalogs = {
            "strings": {"But nothing happened.": "Mais rien ne se passe."},
            "species_names": {"BULBASAUR": "BULBIZARRE"},
            "species_kinds": {"BULBASAUR": "GRAINE"},
            "species_dex_text": {"BULBASAUR": "Une graine."},
            "species_dex_text2": {"BULBASAUR": "Sur le dos."},
            "move_names": {"ABSORB": "VOL-VIE"},
            "item_names": {"AMULET_COIN": "PIECE RUNE"},
            "trainer_class_names": {"BEAUTY": "CANON"},
            "landmarks": {"LANDMARK_AZALEA_TOWN": "ECORCIA"},
            "type_names": {"FIRE": "FEU"},
            "status_labels": {"sleep": "SOM"},
            "oak_speech": {"_OakText1": "Bienvenue dans le monde des POKéMON !"},
        }
        crystal_catalogs = {
            "strings": {"Crystal only": "Cristal seulement"},
            "item_names": {"BLUE_CARD": "CARTE BLEUE"},
            "trainer_class_names": {"MYSTICALMAN": "MYSTIQUE"},
            "landmarks": {"LANDMARK_BATTLE_TOWER": "TOUR DE COMBAT"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = generate_gs_mod(
                root / "mod", language="fr", extra_catalogs=catalogs,
                crystal_catalogs=crystal_catalogs,
            )
            expectations = _write_gate_expectations(
                mod, catalogs, crystal_catalogs=crystal_catalogs,
            )
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(ENGINE_ROOT), str(mod), str(expectations)],
                capture_output=True, text=True,
            )
            expectations.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("all gs registries gate checks passed", result.stdout)

    def test_crystal_rom_text_gate_uses_data_text_and_the_real_rom_text_consumer(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("LuaJIT unavailable")
        engine = None
        for candidate in (ENGINE_ROOT, ROOT.parent / "gen1recomp"):
            schema = candidate / "src" / "mods" / "Schemas.lua"
            if schema.is_file() and "R.rom_text" in schema.read_text(encoding="utf-8"):
                engine = candidate
                break
        if engine is None:
            self.skipTest("upstream mod.content.rom_text registry is unavailable")
        catalogs = {
            "strings": {"But nothing happened.": "Mais rien ne se passe."},
            "species_names": {"BULBASAUR": "BULBIZARRE"},
            "species_kinds": {"BULBASAUR": "GRAINE"},
            "species_dex_text": {"BULBASAUR": "Une graine."},
            "move_names": {"ABSORB": "VOL-VIE"},
            "item_names": {"AMULET_COIN": "PIECE RUNE"},
            "trainer_class_names": {"BEAUTY": "CANON"},
            "landmarks": {"LANDMARK_AZALEA_TOWN": "ECORCIA"},
            "type_names": {"FIRE": "FEU"},
            "status_labels": {"sleep": "SOM"},
            "oak_speech": {"_OakText1": "Bienvenue dans le monde des POKéMON !"},
        }
        crystal = {
            "rom_text": {"_AreYouABoyOrAreYouAGirlText": "Es-tu un garçon ou une fille ?"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = generate_gs_mod(
                root / "mod", language="fr", extra_catalogs=catalogs,
                crystal_catalogs=crystal,
            )
            expectations = _write_gate_expectations(
                mod, catalogs, crystal_catalogs=crystal,
            )
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(engine), str(mod), str(expectations)],
                capture_output=True, text=True,
            )
            expectations.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RomText reads crystal_rom_text", result.stdout)

    def test_registry_expectations_gate_rejects_empty_file(self):
        luajit = _which_luajit()
        if luajit is None or not ENGINE_ROOT.is_dir():
            self.skipTest("cached Gen1Recomp/LuaJIT unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = generate_gs_mod(root / "mod", language="fr")
            expectations = root / "expectations.json"
            expectations.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(ENGINE_ROOT), str(mod), str(expectations)],
                capture_output=True, text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or empty", result.stderr)


class PackageGsModTests(unittest.TestCase):
    def test_packages_into_a_distinctly_named_archive(self):
        if not MODKIT.is_file():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod_dir = generate_gs_mod(root / "build" / "translation-fr-gen2", language="fr")
            attach_gs_validation(
                mod_dir,
                {
                    "schema": 1, "policy": "english-fallback",
                    "coverage": {"translated": 1, "total": 1, "percent": 100.0},
                    "checks": [],
                },
            )
            output = package_gs_mod(
                mod_dir, gen1recomp=ENGINE_ROOT, modkit=MODKIT,
                build_root=root / "build", destination=root / "dist", language="fr",
            )
            self.assertEqual(output.name, f"translation-fr-gen2-{project_version()}.zip")
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("main.lua", names)


class GsPackageGateTests(unittest.TestCase):
    """The headless loader keeps RBY and Gold scoped to their generations."""

    def test_side_by_side_coexistence(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "translation-fr-gen2", language="fr")
            result = subprocess.run(
                [luajit, str(GATE_SCRIPT), str(ENGINE_ROOT), str(RBY_FIXTURE), str(mod_dir)],
                capture_output=True, text=True,
            )
        self.assertEqual(
            result.returncode, 0,
            f"gate_gs_package.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gs package gate checks passed", result.stdout)


class TextCatalogFromJoinTests(unittest.TestCase):
    def test_only_resolved_entries_are_included(self):
        entries = [
            GsJoinEntry("55:0001", None, "Hi", "Salut", UNIQUE, "gs.a.One"),
            GsJoinEntry("55:0002", None, "Bye", None, NO_MATCH),
        ]
        self.assertEqual(gs_text_catalog_from_join(entries), {"55:0001": "Salut"})

    def test_a_resolved_gold_pointer_is_also_aliased_onto_its_silver_pointer(self):
        # config/gsc/silver_pointer_aliases.json's real "03:4d76" -> "03:4d74"
        # entry (see tools/spike_gold_silver_text_overlap.lua's measurement):
        # whatever the Gold pointer resolves to should also land under the
        # Silver pointer, so a Silver save gets the same translation.
        entries = [GsJoinEntry("03:4d76", None, "hi", "Salut", UNIQUE, "gs.a.One")]
        catalog = gs_text_catalog_from_join(entries)
        self.assertEqual(catalog["03:4d76"], "Salut")
        self.assertEqual(catalog["03:4d74"], "Salut")

    def test_an_unresolved_gold_pointer_is_not_aliased(self):
        entries = [GsJoinEntry("03:4d76", None, "hi", None, NO_MATCH)]
        self.assertEqual(gs_text_catalog_from_join(entries), {})

    def test_oak_speech_catalog_uses_only_runtime_intro_labels(self):
        entries = [
            GsJoinEntry("65:5624", "_OakText1", "Hello", "Bonjour", UNIQUE, "gs.a.One"),
            GsJoinEntry("65:56d1", "_OakText3", "@", None, NO_MATCH),
            GsJoinEntry("55:0001", "Other", "Hi", "Salut", UNIQUE, "gs.a.Other"),
        ]
        self.assertEqual(gs_oak_speech_catalog_from_join(entries), {"_OakText1": "Bonjour"})


class GenerateGsModWithTextTests(unittest.TestCase):
    def test_text_catalog_is_written_and_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr", text_catalog={"55:0001": "Bonjour!"},
            )
            catalog = (mod_dir / "lang" / "dialogue.lua").read_text(encoding="utf-8")
            self.assertIn('["55:0001"] = "Bonjour!"', catalog)
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.text:override(id, value)', main)
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["description"], "fr translation for Gold and Silver, based mostly on PokeCorpus.")

    def test_without_a_catalog_stays_the_step_9_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(Path(tmp) / "mod", language="fr")
            self.assertFalse((mod_dir / "lang").exists())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("text:override", main)

    def test_regeneration_removes_stale_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "mod"
            generate_gs_mod(destination, language="fr", text_catalog={"55:0001": "Bonjour!"})
            self.assertTrue((destination / "lang" / "dialogue.lua").is_file())
            generate_gs_mod(destination, language="fr")
            self.assertFalse((destination / "lang").exists())

    def test_generation_is_deterministic(self):
        catalog = {"55:0002": "B", "55:0001": "A", "55:0003": "C"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = generate_gs_mod(root / "one", language="fr", text_catalog=catalog)
            second = generate_gs_mod(root / "two", language="fr", text_catalog=catalog)
            self.assertEqual(
                (first / "lang" / "dialogue.lua").read_bytes(), (second / "lang" / "dialogue.lua").read_bytes(),
            )
            # manifest.json embeds only the project version, not a
            # timestamp or any other run-to-run varying field.
            self.assertEqual((first / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())
            self.assertEqual((first / "main.lua").read_bytes(), (second / "main.lua").read_bytes())

    def test_extra_catalogs_are_written_and_registered_each_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={
                    "species_names": {"BULBASAUR": "BULBIZARRE"},
                    "move_names": {"ABSORB": "VOL-VIE"},
                },
            )
            self.assertTrue((mod_dir / "lang" / "species_names.lua").is_file())
            self.assertTrue((mod_dir / "lang" / "move_names.lua").is_file())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('each("species_names", function(id, value) mod.content.pokemon:patch(id, { name = value }) end)', main)
            self.assertIn('each("move_names", function(id, value) mod.content.moves:patch(id, { name = value }) end)', main)

    def test_engine_strings_are_written_and_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"strings": {"Hello!": "Bonjour!"}},
            )
            self.assertIn(
                '["Hello!"] = "Bonjour!"',
                (mod_dir / "lang" / "strings.lua").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'mod.content.strings:override(id, value)',
                (mod_dir / "main.lua").read_text(encoding="utf-8"),
            )

    def test_oak_speech_catalog_is_applied_through_the_public_intro_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"oak_speech": {"_OakText1": "Bienvenue !"}},
            )
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.hooks:wrap("intro.oak_speech.build"', main)
            self.assertIn('speech.texts[id] = value', main)

    def test_empty_extra_catalogs_are_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"species_names": {}, "move_names": {"ABSORB": "VOL-VIE"}},
            )
            self.assertFalse((mod_dir / "lang" / "species_names.lua").exists())
            self.assertTrue((mod_dir / "lang" / "move_names.lua").exists())

    def test_unknown_catalog_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no registry hook"):
                generate_gs_mod(
                    Path(tmp) / "mod", language="fr", extra_catalogs={"not_a_registry": {"X": "Y"}},
                )


class BuildGsDialogueModTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> tuple[Path, Path]:
        gold_out = root / "gold-out"
        gold_out.mkdir()
        (gold_out / "gs_text.tsv").write_text(
            "55:0001\tHello there!\n55:0002\tUnmatched pointer.\n", encoding="utf-8",
        )
        (gold_out / "gs_labels.tsv").write_text("Greeting\t55:0001\n", encoding="utf-8")
        (gold_out / "gs_stages.tsv").write_text("text\tok\n", encoding="utf-8")
        (gold_out / "gs_rom_text.tsv").write_text("_FixtureText\tFixture text.\n", encoding="utf-8")
        (gold_out / "gs_species.tsv").write_text("BULBASAUR\t1\tBULBASAUR\n", encoding="utf-8")
        (gold_out / "gs_moves.tsv").write_text("ABSORB\t71\tABSORB\n", encoding="utf-8")
        (gold_out / "gs_items.tsv").write_text("AMULET_COIN\t91\tAMULET COIN\n", encoding="utf-8")
        (gold_out / "gs_types.tsv").write_text("NORMAL\t0\tNORMAL\n", encoding="utf-8")
        (gold_out / "gs_trainer_classes.tsv").write_text("BEAUTY\t29\tBEAUTY\n", encoding="utf-8")
        (gold_out / "gs_landmarks.tsv").write_text("LANDMARK_TEST\t1\tTEST\n", encoding="utf-8")
        corpus = root / "corpus"
        corpus.mkdir()
        (corpus / "qid_msg.txt").write_text("gs.a.Greeting\n", encoding="utf-8")
        (corpus / "en_msg.txt").write_text("Hello there!\n", encoding="utf-8")
        (corpus / "fr_msg.txt").write_text("Bonjour!\n", encoding="utf-8")
        return gold_out, corpus

    def test_builds_a_mod_with_the_joined_text_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            mod_dir, entries, stats = build_gs_dialogue_mod(gold_out, corpus, root / "mod", language="fr")
            self.assertEqual(stats["unique"], 1)
            self.assertEqual(stats["no_match"], 1)
            catalog = (mod_dir / "lang" / "dialogue.lua").read_text(encoding="utf-8")
            self.assertIn('["55:0001"] = "Bonjour!"', catalog)
            self.assertNotIn("55:0002", catalog)
            self.assertEqual({e.pointer for e in entries}, {"55:0001", "55:0002"})

    def test_engine_matches_are_shipped_and_reported_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            engine_coverage = {
                "engine": {"translated": 1, "total": 3, "percent": 33.33},
                "engine_gen2": {"translated": 1, "total": 2, "percent": 50.0},
            }
            with patch(
                "pipeline.gs_mod.match_gs_engine_strings",
                return_value=({"Hello!": "Bonjour!"}, engine_coverage),
            ) as match:
                mod_dir, _entries, stats = build_gs_dialogue_mod(
                    gold_out, corpus, root / "mod", language="fr", engine_source=root / "engine",
                    engine_profile=UPSTREAM_PROFILE,
                )
            match.assert_called_once()
            self.assertEqual(stats["coverage"]["engine_gen2"]["total"], 2)
            self.assertEqual(stats["coverage"]["rom"], {
                "translated": 1, "total": 115, "percent": 0.87,
            })
            self.assertEqual(stats["coverage"]["rom_dialogue"]["total"], 2)
            # 113: the existing named/dex rows plus one extracted type,
            # six status labels, and the phone/decorations/radio registries.
            # species_dex_text_silver/species_dex_text2_silver are each
            # their own index_stats entry alongside species_dex_text now.
            self.assertEqual(stats["coverage"]["rom_catalogs"]["total"], 113)
            self.assertEqual(stats["_gate_catalogs"]["strings"], {"Hello!": "Bonjour!"})
            self.assertIn(
                '["Hello!"] = "Bonjour!"',
                (mod_dir / "lang" / "strings.lua").read_text(encoding="utf-8"),
            )

    def test_release_import_rejects_missing_required_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            (gold_out / "gs_landmarks.tsv").unlink()
            with self.assertRaisesRegex(ValueError, "gs_landmarks.tsv"):
                build_gs_dialogue_mod(gold_out, corpus, root / "mod", language="fr")

    def test_release_import_rejects_empty_required_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            (gold_out / "gs_moves.tsv").write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gs_moves.tsv"):
                build_gs_dialogue_mod(gold_out, corpus, root / "mod", language="fr")

    def _write_index_fixtures(self, gold_out: Path, corpus: Path) -> None:
        (gold_out / "gs_species.tsv").write_text("BULBASAUR\t1\tBULBASAUR\n", encoding="utf-8")
        (gold_out / "gs_moves.tsv").write_text("ABSORB\t71\tABSORB\n", encoding="utf-8")
        (gold_out / "gs_items.tsv").write_text("AMULET_COIN\t91\tAMULET COIN\n", encoding="utf-8")
        (gold_out / "gs_trainer_classes.tsv").write_text("BEAUTY\t29\tBEAUTY\n", encoding="utf-8")
        qid_lines = ["gs.names.PokemonNames.1", "gs.names.MoveNames.71", "gs.names.ItemNames.91",
                     "gs.class_names.TrainerClassNames.29", "gs.dex_entries.BulbasaurPokedexEntry.Species",
                     "gs.dex_entries_gold.BulbasaurPokedexEntry",
                     "gs.dex_entries_silver.BulbasaurPokedexEntry"]
        en_lines = ["BULBASAUR", "ABSORB", "AMULET COIN", "BEAUTY", "SEED", "A seed.@It hides.@",
                    "A different seed.@It really hides.@"]
        fr_lines = ["BULBIZARRE", "VOL-VIE", "PIECE RUNE", "CANON", "GRAINE", "Une graine.@Elle se cache.@",
                    "Une autre graine.@Elle se cache vraiment.@"]
        existing_qid = (corpus / "qid_msg.txt").read_text(encoding="utf-8").splitlines()
        existing_en = (corpus / "en_msg.txt").read_text(encoding="utf-8").splitlines()
        existing_fr = (corpus / "fr_msg.txt").read_text(encoding="utf-8").splitlines()
        (corpus / "qid_msg.txt").write_text("\n".join(existing_qid + qid_lines) + "\n", encoding="utf-8")
        (corpus / "en_msg.txt").write_text("\n".join(existing_en + en_lines) + "\n", encoding="utf-8")
        (corpus / "fr_msg.txt").write_text("\n".join(existing_fr + fr_lines) + "\n", encoding="utf-8")

    def test_builds_a_mod_with_the_index_joined_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            self._write_index_fixtures(gold_out, corpus)
            mod_dir, _entries, stats = build_gs_dialogue_mod(gold_out, corpus, root / "mod", language="fr")
            self.assertEqual(stats["index_catalogs"]["species_names"], {
                "total": 1, "translated": 1, "no_corpus_entry": 0, "same_as_english": 0,
            })
            self.assertEqual((mod_dir / "lang" / "species_names.lua").read_text(encoding="utf-8"),
                              '-- Generated by the Gold pipeline (fr): species_names\nreturn {\n'
                              '  ["BULBASAUR"] = "BULBIZARRE",\n}\n')
            self.assertIn('["ABSORB"] = "VOL-VIE"', (mod_dir / "lang" / "move_names.lua").read_text(encoding="utf-8"))
            self.assertIn('["AMULET_COIN"] = "PIECE RUNE"',
                           (mod_dir / "lang" / "item_names.lua").read_text(encoding="utf-8"))
            self.assertIn('["BEAUTY"] = "CANON"',
                           (mod_dir / "lang" / "trainer_class_names.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "GRAINE"',
                           (mod_dir / "lang" / "species_kinds.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "Une graine."',
                           (mod_dir / "lang" / "species_dex_text.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "Elle se cache."',
                           (mod_dir / "lang" / "species_dex_text2.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "Une autre graine."',
                           (mod_dir / "lang" / "species_dex_text_silver.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "Elle se cache vraiment."',
                           (mod_dir / "lang" / "species_dex_text2_silver.lua").read_text(encoding="utf-8"))
            self.assertEqual(stats["index_catalogs"]["species_dex_text_silver"], {
                "total": 1, "translated": 1, "no_corpus_entry": 0,
            })
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.pokemon:patch(id, { name = value })', main)
            self.assertIn('mod.content.pokemon:patch(id, { dexEntry = { kind = value } })', main)
            self.assertIn('mod.content.pokemon:patch(id, { dexEntry = { text = value } })', main)
            self.assertIn('mod.content.pokemon:patch(id, { dexEntry = { text2 = value } })', main)
            self.assertIn('SilverGameVersion.get() == "silver"', main)
            self.assertIn('mod.content.moves:patch(id, { name = value })', main)
            self.assertIn('mod.content.items:patch(id, { name = value })', main)
            self.assertIn('mod.content.trainers:patch(id, { name = value })', main)

    def test_builds_a_mod_with_crystals_own_dex_text_when_a_crystal_corpus_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            self._write_index_fixtures(gold_out, corpus)
            crystal_corpus = root / "crystal-corpus"
            crystal_corpus.mkdir()
            (crystal_corpus / "qid_msg.txt").write_text(
                "c.dex_entries.BulbasaurPokedexEntry\n", encoding="utf-8",
            )
            (crystal_corpus / "en_msg.txt").write_text(
                "A Crystal-only seed.@It really grows.@\n", encoding="utf-8",
            )
            (crystal_corpus / "fr_msg.txt").write_text(
                "Une graine Cristal.@Elle grandit vraiment.@\n", encoding="utf-8",
            )
            mod_dir, _entries, stats = build_gs_dialogue_mod(
                gold_out, corpus, root / "mod", language="fr", crystal_corpus_dir=crystal_corpus,
            )
            self.assertIn('["BULBASAUR"] = "Une graine Cristal."',
                           (mod_dir / "lang" / "species_dex_text_crystal.lua").read_text(encoding="utf-8"))
            self.assertIn('["BULBASAUR"] = "Elle grandit vraiment."',
                           (mod_dir / "lang" / "species_dex_text2_crystal.lua").read_text(encoding="utf-8"))
            self.assertEqual(stats["index_catalogs"]["species_dex_text_crystal"], {
                "total": 1, "translated": 1, "no_corpus_entry": 0,
            })
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('CrystalDexGameVersion.get() == "crystal"', main)
            self.assertIn("species_dex_text_crystal", main)

    def test_no_crystal_dex_catalog_or_stats_without_a_crystal_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            self._write_index_fixtures(gold_out, corpus)
            mod_dir, _entries, stats = build_gs_dialogue_mod(gold_out, corpus, root / "mod", language="fr")
            self.assertNotIn("species_dex_text_crystal", stats["index_catalogs"])
            self.assertFalse((mod_dir / "lang" / "species_dex_text_crystal.lua").exists())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("crystal_dex_game_version", main)

    def test_engine_source_alone_cannot_select_upstream_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "explicit.*upstream-local"):
                build_gs_dialogue_mod(
                    Path(tmp) / "gold", Path(tmp) / "corpus", Path(tmp) / "mod",
                    engine_source=Path(tmp) / "engine",
                )


class GsDialogueGateTests(unittest.TestCase):
    """tools/gate_gs_dialogue.lua: a resolved pointer's translation is
    actually selected, and an unresolved one stays absent (English
    fallback).
    """

    def test_translation_is_selected_and_unresolved_pointer_falls_back(self):
        # The expected translation ("こんにちは！") is deliberately non-ASCII:
        # a real Windows GUI report showed this exact check failing with
        # mojibake for Japanese/Korean Gold translations (German passed),
        # because it used to be handed to luajit as a plain command-line
        # argument -- Windows narrows a child process's argv to the active
        # ANSI codepage before the C runtime ever sees it. Going through the
        # same JSON expectation file gate_gs_registries.lua already uses
        # sidesteps that: the string only ever travels as UTF-8 file bytes.
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "translation-fr-gen2", language="fr",
                text_catalog={"55:0001": "こんにちは！"},
            )
            expectation_path = _write_dialogue_gate_expectation(mod_dir, "55:0001", "こんにちは！", "55:9999")
            try:
                result = subprocess.run(
                    [luajit, str(DIALOGUE_GATE_SCRIPT), str(ENGINE_ROOT), str(mod_dir), str(expectation_path)],
                    capture_output=True, text=True,
                )
            finally:
                expectation_path.unlink(missing_ok=True)
        self.assertEqual(
            result.returncode, 0,
            f"gate_gs_dialogue.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gs dialogue gate checks passed", result.stdout)


class GsRegistriesGateTests(unittest.TestCase):
    """tools/gate_gs_registries.lua: pokemon/moves/items/trainer-class
    patches land where the routed generation=2 target actually reads them
    -- trainers in particular, which is routed to gen2Trainers.classes
    even though the mod-facing patch call keeps the Gen 1 shape.
    """

    def test_each_registry_lands_at_its_routed_target(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gs_mod(
                Path(tmp) / "translation-fr-gen2", language="fr",
                extra_catalogs={
                    "species_names": {"BULBASAUR": "BULBIZARRE"},
                    "species_kinds": {"BULBASAUR": "GRAINE"},
                    "species_dex_text": {"BULBASAUR": "Une graine sur le dos."},
                    "species_dex_text2": {"BULBASAUR": "Elle grandit avec le POKéMON."},
                    "move_names": {"ABSORB": "VOL-VIE"},
                    "item_names": {"AMULET_COIN": "PIECE RUNE"},
                    "trainer_class_names": {"BEAUTY": "CANON"},
                    "landmarks": {"LANDMARK_AZALEA_TOWN": "ECORCIA"},
                },
            )
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(ENGINE_ROOT), str(mod_dir)],
                capture_output=True, text=True,
            )
        self.assertEqual(
            result.returncode, 0,
            f"gate_gs_registries.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gs registries gate checks passed", result.stdout)


class GsUiLabelsTests(unittest.TestCase):
    """PcMenu/StartMenu/PartyMenu rows reach the player through public
    gen1recomp list hooks (ui.pc.items, ui.start_menu.items,
    ui.party.submenu) this mod already wraps; _gs_ui_labels() is what
    feeds their translations into that catalog.
    """

    def test_pc_storage_menu_rows_use_the_real_five_at_sign_row(self):
        corpus_rows = [
            ("gs.bills_pc_top.BillsPC.strings",
             "WITHDRAW <PK><MN>@DEPOSIT <PK><MN>@CHANGE BOX@MOVE <PK><MN> W/O MAIL@SEE YA!@",
             "RETIRER <PK><MN>@STOCKER <PK><MN>@CHANGER BOITE@DEP.<PK><MN> SNS LETTRE@SALUT!@"),
        ]
        labels = _gs_ui_labels(corpus_rows)
        self.assertEqual(labels["WITHDRAW <PK><MN>"], "RETIRER <PK><MN>")
        self.assertEqual(labels["DEPOSIT <PK><MN>"], "STOCKER <PK><MN>")
        self.assertEqual(labels["CHANGE BOX"], "CHANGER BOITE")
        self.assertEqual(labels["MOVE <PK><MN> W/O MAIL"], "DEP.<PK><MN> SNS LETTRE")
        self.assertEqual(labels["SEE YA!"], "SALUT!")

    def test_single_tag_pkmn_markup_is_normalized_to_the_two_glyph_form(self):
        # Segment mode ships raw corpus markup verbatim (these labels are
        # tile-width-critical), but some corpus rows spell the same glyph
        # pair "<PKMN>" instead of "<PK><MN>" (observed in es/it's MOVE W/O
        # MAIL row); "<PKMN>" has no Font.split() macro and would render as
        # literal garbage if shipped as-is.
        corpus_rows = [
            ("gs.bills_pc_top.BillsPC.strings",
             "WITHDRAW <PK><MN>@DEPOSIT <PK><MN>@CHANGE BOX@MOVE <PK><MN> W/O MAIL@SEE YA!@",
             "SACAR <PK><MN>@DEJAR <PK><MN>@CAMBIA CAJA@MOVER <PKMN> SIN CAR@¡NOS VEMOS!@"),
        ]
        labels = _gs_ui_labels(corpus_rows)
        self.assertEqual(labels["MOVE <PK><MN> W/O MAIL"], "MOVER <PK><MN> SIN CAR")
        self.assertNotIn("<PKMN>", labels["MOVE <PK><MN> W/O MAIL"])

    def test_battle_party_submenu_switch_and_stats_rows(self):
        corpus_rows = [
            ("gs.mon_submenu.BattleMonMenu.MenuData", "SWITCH@STATS@CANCEL@", "CHANGER@STATS@RETOUR@"),
        ]
        labels = _gs_ui_labels(corpus_rows)
        self.assertEqual(labels["SWITCH"], "CHANGER")
        self.assertEqual(labels["STATS"], "STATS")

    def test_start_menu_two_line_descriptions_are_keyed_by_both_lines(self):
        corpus_rows = [
            ("gs.start_menu.StartMenu.PokedexDesc", "#MON<NEXT>database@", "Index<NEXT>#MON@"),
            ("gs.start_menu.StartMenu.PackDesc", "Contains<NEXT>items@", "Contient<NEXT>objets@"),
        ]
        labels = _gs_ui_labels(corpus_rows)
        self.assertEqual(labels["POKéMON\ndatabase"], "Index\nPOKéMON")
        self.assertEqual(labels["Contains\nitems"], "Contient\nobjets")


if __name__ == "__main__":
    unittest.main()
