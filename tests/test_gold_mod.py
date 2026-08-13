import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pipeline.builder import _which_luajit
from pipeline.gold_join import GoldJoinEntry, NO_MATCH, UNIQUE
from pipeline.gold_mod import (
    build_gold_dialogue_mod, gold_archive_name, gold_mod_id, gold_text_catalog_from_join,
    generate_gold_mod, package_gold_mod, run_gold_release_gates,
)
from pipeline.gold_mod import _write_gate_expectations
from pipeline.project import project_version

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / ".cache" / "dependencies" / "gen1recomp"
MODKIT = ENGINE_ROOT / "tools" / "modkit.py"
GATE_SCRIPT = ROOT / "tools" / "gate_gold_package.lua"
DIALOGUE_GATE_SCRIPT = ROOT / "tools" / "gate_gold_dialogue.lua"
REGISTRIES_GATE_SCRIPT = ROOT / "tools" / "gate_gold_registries.lua"
RBY_FIXTURE = ROOT / "tools" / "gen2_gate_fixtures" / "rby_translation"


class IdentifierTests(unittest.TestCase):
    def test_mod_id_is_generation_scoped_not_game_scoped(self):
        # The id encodes generation, never today's game list, so
        # Gold/Silver/Crystal never force a rename.
        self.assertEqual(gold_mod_id("fr"), "translation-fr-gen2")
        self.assertEqual(gold_mod_id("FR"), "translation-fr-gen2")

    def test_archive_name_is_distinct_from_the_rby_one(self):
        name = gold_archive_name("fr", "1.2.3")
        self.assertEqual(name, "translation-fr-gen2-1.2.3.zip")
        self.assertNotEqual(name, "translation-fr-1.2.3.zip")


class GenerateGoldModTests(unittest.TestCase):
    def test_manifest_declares_gold_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(Path(tmp) / "mod", language="fr")
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "translation-fr-gen2")
            self.assertEqual(manifest["games"], ["gold"])
            self.assertEqual(manifest["api"], 2)
            self.assertEqual(manifest["entry"], "main.lua")
            self.assertIn("Gold", manifest["description"])

    def test_main_lua_has_no_content_registrations_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(Path(tmp) / "mod", language="fr")
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn("return function(mod)", main)
            self.assertIn('font:register("ttf"', main)
            for registry in ("text:override", "strings:override", "pokemon:patch"):
                self.assertNotIn(registry, main)

    def test_custom_mod_id_and_description_are_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(
                Path(tmp) / "mod", mod_id="custom-id", language="fr",
                target_description="Custom description.",
            )
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "custom-id")
            self.assertEqual(manifest["description"], "Custom description.")


class GoldReleaseGateFlowTests(unittest.TestCase):
    def test_registry_expectations_reject_missing_or_empty_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                _write_gate_expectations(Path(tmp) / "mod", {})
            catalogs = {name: {"ID": "VALUE"} for name in (
                "species_names", "species_kinds", "species_dex_text", "move_names",
                "item_names", "trainer_class_names", "landmarks",
            )}
            catalogs["landmarks"] = {}
            with self.assertRaisesRegex(RuntimeError, "empty"):
                _write_gate_expectations(Path(tmp) / "mod", catalogs)

    def test_all_gates_run_before_a_release_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            (engine / "tools").mkdir(parents=True)
            mod = generate_gold_mod(root / "mod", language="fr", text_catalog={"55:0001": "Bonjour!"})
            entries = [GoldJoinEntry("55:0001", None, "Hello", "Bonjour!", UNIQUE, "gs.a.One")]
            with patch("pipeline.gold_mod._run") as run:
                report = run_gold_release_gates(
                    mod, entries, engine, "/usr/bin/luajit",
                    catalogs={name: {"ID": "X"} for name in (
                        "species_names", "species_kinds", "species_dex_text", "move_names",
                        "item_names", "trainer_class_names", "landmarks",
                    )},
                )
            self.assertIn("coverage", report)
            commands = [Path(call.args[0][1]).name for call in run.call_args_list]
            self.assertEqual(commands, ["gate_gen2.lua", "gate_gold_dialogue.lua", "gate_gold_registries.lua"])

    def test_registry_expectations_gate_runs_against_real_loader(self):
        luajit = _which_luajit()
        if luajit is None or not ENGINE_ROOT.is_dir():
            self.skipTest("cached Gen1Recomp/LuaJIT unavailable")
        catalogs = {
            "species_names": {"BULBASAUR": "BULBIZARRE"},
            "species_kinds": {"BULBASAUR": "GRAINE"},
            "species_dex_text": {"BULBASAUR": "Une graine."},
            "move_names": {"ABSORB": "VOL-VIE"},
            "item_names": {"AMULET_COIN": "PIECE RUNE"},
            "trainer_class_names": {"BEAUTY": "CANON"},
            "landmarks": {"LANDMARK_AZALEA_TOWN": "ECORCIA"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = generate_gold_mod(root / "mod", language="fr", extra_catalogs=catalogs)
            expectations = _write_gate_expectations(mod, catalogs)
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(ENGINE_ROOT), str(mod), str(expectations)],
                capture_output=True, text=True,
            )
            expectations.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("all gold registries gate checks passed", result.stdout)

    def test_registry_expectations_gate_rejects_empty_file(self):
        luajit = _which_luajit()
        if luajit is None or not ENGINE_ROOT.is_dir():
            self.skipTest("cached Gen1Recomp/LuaJIT unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = generate_gold_mod(root / "mod", language="fr")
            expectations = root / "expectations.json"
            expectations.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [luajit, str(REGISTRIES_GATE_SCRIPT), str(ENGINE_ROOT), str(mod), str(expectations)],
                capture_output=True, text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or empty", result.stderr)


class PackageGoldModTests(unittest.TestCase):
    def test_packages_into_a_distinctly_named_archive(self):
        if not MODKIT.is_file():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod_dir = generate_gold_mod(root / "build" / "translation-fr-gen2", language="fr")
            output = package_gold_mod(
                mod_dir, gen1recomp=ENGINE_ROOT, modkit=MODKIT,
                build_root=root / "build", destination=root / "dist", language="fr",
            )
            self.assertEqual(output.name, f"translation-fr-gen2-{project_version()}.zip")
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("main.lua", names)


class GoldPackageGateTests(unittest.TestCase):
    """tools/gate_gold_package.lua: both archives installed side by side,
    each active on its own generation's boot and gated out of the other's.
    """

    def test_side_by_side_coexistence(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(Path(tmp) / "translation-fr-gen2", language="fr")
            result = subprocess.run(
                [luajit, str(GATE_SCRIPT), str(ENGINE_ROOT), str(RBY_FIXTURE), str(mod_dir)],
                capture_output=True, text=True,
            )
        self.assertEqual(
            result.returncode, 0,
            f"gate_gold_package.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gold package gate checks passed", result.stdout)


class TextCatalogFromJoinTests(unittest.TestCase):
    def test_only_resolved_entries_are_included(self):
        entries = [
            GoldJoinEntry("55:0001", None, "Hi", "Salut", UNIQUE, "gs.a.One"),
            GoldJoinEntry("55:0002", None, "Bye", None, NO_MATCH),
        ]
        self.assertEqual(gold_text_catalog_from_join(entries), {"55:0001": "Salut"})


class GenerateGoldModWithTextTests(unittest.TestCase):
    def test_text_catalog_is_written_and_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(
                Path(tmp) / "mod", language="fr", text_catalog={"55:0001": "Bonjour!"},
            )
            catalog = (mod_dir / "lang" / "dialogue.lua").read_text(encoding="utf-8")
            self.assertIn('["55:0001"] = "Bonjour!"', catalog)
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.text:override(id, value)', main)
            manifest = json.loads((mod_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("Some engine-specific text remains untranslated", manifest["description"])

    def test_without_a_catalog_stays_the_step_9_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(Path(tmp) / "mod", language="fr")
            self.assertFalse((mod_dir / "lang").exists())
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("text:override", main)

    def test_generation_is_deterministic(self):
        catalog = {"55:0002": "B", "55:0001": "A", "55:0003": "C"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = generate_gold_mod(root / "one", language="fr", text_catalog=catalog)
            second = generate_gold_mod(root / "two", language="fr", text_catalog=catalog)
            self.assertEqual(
                (first / "lang" / "dialogue.lua").read_bytes(), (second / "lang" / "dialogue.lua").read_bytes(),
            )
            # manifest.json embeds only the project version, not a
            # timestamp or any other run-to-run varying field.
            self.assertEqual((first / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())
            self.assertEqual((first / "main.lua").read_bytes(), (second / "main.lua").read_bytes())

    def test_extra_catalogs_are_written_and_registered_each_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(
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

    def test_empty_extra_catalogs_are_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(
                Path(tmp) / "mod", language="fr",
                extra_catalogs={"species_names": {}, "move_names": {"ABSORB": "VOL-VIE"}},
            )
            self.assertFalse((mod_dir / "lang" / "species_names.lua").exists())
            self.assertTrue((mod_dir / "lang" / "move_names.lua").exists())

    def test_unknown_catalog_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no registry hook"):
                generate_gold_mod(
                    Path(tmp) / "mod", language="fr", extra_catalogs={"not_a_registry": {"X": "Y"}},
                )


class BuildGoldDialogueModTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> tuple[Path, Path]:
        gold_out = root / "gold-out"
        gold_out.mkdir()
        (gold_out / "gold_text.tsv").write_text(
            "55:0001\tHello there!\n55:0002\tUnmatched pointer.\n", encoding="utf-8",
        )
        (gold_out / "gold_labels.tsv").write_text("Greeting\t55:0001\n", encoding="utf-8")
        (gold_out / "gold_maps.tsv").write_text("TEST_MAP\t55\n", encoding="utf-8")
        (gold_out / "gold_species.tsv").write_text("BULBASAUR\t1\tBULBASAUR\n", encoding="utf-8")
        (gold_out / "gold_moves.tsv").write_text("ABSORB\t71\tABSORB\n", encoding="utf-8")
        (gold_out / "gold_items.tsv").write_text("AMULET_COIN\t91\tAMULET COIN\n", encoding="utf-8")
        (gold_out / "gold_trainer_classes.tsv").write_text("BEAUTY\t29\tBEAUTY\n", encoding="utf-8")
        (gold_out / "gold_landmarks.tsv").write_text("LANDMARK_TEST\t1\tTEST\n", encoding="utf-8")
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
            mod_dir, entries, stats = build_gold_dialogue_mod(gold_out, corpus, root / "mod", language="fr")
            self.assertEqual(stats["unique"], 1)
            self.assertEqual(stats["no_match"], 1)
            catalog = (mod_dir / "lang" / "dialogue.lua").read_text(encoding="utf-8")
            self.assertIn('["55:0001"] = "Bonjour!"', catalog)
            self.assertNotIn("55:0002", catalog)
            self.assertEqual({e.pointer for e in entries}, {"55:0001", "55:0002"})

    def test_release_import_rejects_missing_required_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            (gold_out / "gold_landmarks.tsv").unlink()
            with self.assertRaisesRegex(ValueError, "gold_landmarks.tsv"):
                build_gold_dialogue_mod(gold_out, corpus, root / "mod", language="fr")

    def test_release_import_rejects_empty_required_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_out, corpus = self._write_fixture(root)
            (gold_out / "gold_moves.tsv").write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gold_moves.tsv"):
                build_gold_dialogue_mod(gold_out, corpus, root / "mod", language="fr")

    def _write_index_fixtures(self, gold_out: Path, corpus: Path) -> None:
        (gold_out / "gold_species.tsv").write_text("BULBASAUR\t1\tBULBASAUR\n", encoding="utf-8")
        (gold_out / "gold_moves.tsv").write_text("ABSORB\t71\tABSORB\n", encoding="utf-8")
        (gold_out / "gold_items.tsv").write_text("AMULET_COIN\t91\tAMULET COIN\n", encoding="utf-8")
        (gold_out / "gold_trainer_classes.tsv").write_text("BEAUTY\t29\tBEAUTY\n", encoding="utf-8")
        qid_lines = ["gs.names.PokemonNames.1", "gs.names.MoveNames.71", "gs.names.ItemNames.91",
                     "gs.class_names.TrainerClassNames.29", "gs.dex_entries.BulbasaurPokedexEntry.Species",
                     "gs.dex_entries_gold.BulbasaurPokedexEntry"]
        en_lines = ["BULBASAUR", "ABSORB", "AMULET COIN", "BEAUTY", "SEED", "A seed."]
        fr_lines = ["BULBIZARRE", "VOL-VIE", "PIECE RUNE", "CANON", "GRAINE", "Une graine."]
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
            mod_dir, _entries, stats = build_gold_dialogue_mod(gold_out, corpus, root / "mod", language="fr")
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
            main = (mod_dir / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod.content.pokemon:patch(id, { name = value })', main)
            self.assertIn('mod.content.pokemon:patch(id, { dexEntry = { kind = value } })', main)
            self.assertIn('mod.content.pokemon:patch(id, { dexEntry = { text = value } })', main)
            self.assertIn('mod.content.moves:patch(id, { name = value })', main)
            self.assertIn('mod.content.items:patch(id, { name = value })', main)
            self.assertIn('mod.content.trainers:patch(id, { name = value })', main)


class GoldDialogueGateTests(unittest.TestCase):
    """tools/gate_gold_dialogue.lua: a resolved pointer's translation is
    actually selected, and an unresolved one stays absent (English
    fallback).
    """

    def test_translation_is_selected_and_unresolved_pointer_falls_back(self):
        luajit = _which_luajit()
        if luajit is None:
            self.skipTest("luajit is unavailable")
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = generate_gold_mod(
                Path(tmp) / "translation-fr-gen2", language="fr",
                text_catalog={"55:0001": "Bonjour!"},
            )
            result = subprocess.run(
                [luajit, str(DIALOGUE_GATE_SCRIPT), str(ENGINE_ROOT), str(mod_dir),
                 "55:0001", "Bonjour!", "55:9999"],
                capture_output=True, text=True,
            )
        self.assertEqual(
            result.returncode, 0,
            f"gate_gold_dialogue.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gold dialogue gate checks passed", result.stdout)


class GoldRegistriesGateTests(unittest.TestCase):
    """tools/gate_gold_registries.lua: pokemon/moves/items/trainer-class
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
            mod_dir = generate_gold_mod(
                Path(tmp) / "translation-fr-gen2", language="fr",
                extra_catalogs={
                    "species_names": {"BULBASAUR": "BULBIZARRE"},
                    "species_kinds": {"BULBASAUR": "GRAINE"},
                    "species_dex_text": {"BULBASAUR": "Une graine sur le dos."},
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
            f"gate_gold_registries.lua failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("all gold registries gate checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
