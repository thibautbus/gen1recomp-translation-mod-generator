import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.project import project_config
from pipeline.roms import (
    CANONICAL, GOLD_REQUIRED_TSV, GOLD_SHA1, import_gold_rom, import_rom,
    verify_gold_rom, verify_rom,
)


class RomConfigTests(unittest.TestCase):
    @staticmethod
    def write_config(
        root: Path,
        red: str | None = None,
        blue: str | None = None,
        yellow: str | None = None,
        gold: str | None = "0" * 40,
    ) -> None:
        config = root / "config"
        config.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for section, value in (("red", red), ("blue", blue), ("yellow", yellow), ("gold", gold)):
            lines.append(f"[rom.{section}]")
            if value is not None:
                lines.append(f'sha1 = "{value}"')
            lines.append("")
        (config / "pipeline.toml").write_text("\n".join(lines), encoding="utf-8")

    def test_checked_in_rom_sections_have_no_paths(self):
        config = project_config()
        self.assertEqual(set(config["rom"]), {"red", "blue", "yellow", "gold"})
        for section in config["rom"].values():
            self.assertNotIn("path", section)

    def test_verify_rom_loads_expected_hash_from_toml(self):
        payload = b"test red ROM"
        expected = hashlib.sha1(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            rom.write_bytes(payload)
            self.write_config(root, red=expected, blue="0" * 40, yellow="0" * 40)
            self.assertEqual(verify_rom(rom, "red", config_root=root)["sha1"], expected)

    def test_canonical_mapping_is_read_only_and_loaded_from_checked_in_toml(self):
        config = project_config()
        self.assertEqual(CANONICAL["red"], config["rom"]["red"]["sha1"])
        self.assertEqual(CANONICAL["blue"], config["rom"]["blue"]["sha1"])
        self.assertEqual(CANONICAL["yellow"], config["rom"]["yellow"]["sha1"])
        self.assertEqual(CANONICAL["gold"], config["rom"]["gold"]["sha1"])
        self.assertEqual(GOLD_SHA1, config["rom"]["gold"]["sha1"])
        with self.assertRaises(TypeError):
            CANONICAL["red"] = "0" * 40

    def test_malformed_or_missing_hash_is_rejected(self):
        payload = b"test red ROM"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            rom.write_bytes(payload)
            for malformed in ("", "A" * 40, "0" * 39, "g" * 40):
                with self.subTest(hash=malformed):
                    self.write_config(root, red=malformed, blue="0" * 40, yellow="0" * 40)
                    with self.assertRaisesRegex(ValueError, r"red\]\.sha1"):
                        verify_rom(rom, "red", config_root=root)
            with self.subTest(hash="missing"):
                self.write_config(root, red=None, blue="0" * 40, yellow="0" * 40)
                with self.assertRaisesRegex(ValueError, r"\[rom\.red\]\.sha1"):
                    verify_rom(rom, "red", config_root=root)

    def test_invalid_rom_config_structure_is_rejected_clearly(self):
        payload = b"test red ROM"
        digest = hashlib.sha1(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            rom.write_bytes(payload)
            cases = {
                "unknown field": (
                    f'[rom.red]\nsha1 = "{digest}"\npath = "old.gb"\n'
                    f'[rom.blue]\nsha1 = "{"0" * 40}"\n'
                    f'[rom.yellow]\nsha1 = "{"0" * 40}"\n'
                    f'[rom.gold]\nsha1 = "{"0" * 40}"\n',
                    "unsupported keys: path",
                ),
                "missing rom": ("[output]\nname = \"test\"\n", r"missing \[rom\] section"),
                "extra version": (
                    f'[rom.red]\nsha1 = "{digest}"\n'
                    f'[rom.blue]\nsha1 = "{"0" * 40}"\n'
                    f'[rom.yellow]\nsha1 = "{"0" * 40}"\n'
                    f'[rom.gold]\nsha1 = "{"0" * 40}"\n'
                    f'[rom.green]\nsha1 = "{"1" * 40}"\n',
                    "unsupported versions: green",
                ),
                "non-table": (
                    f'rom = {{ red = "{digest}", blue = {{ sha1 = "{"0" * 40}" }}, yellow = {{ sha1 = "{"0" * 40}" }}, gold = {{ sha1 = "{"0" * 40}" }} }}\n',
                    r"invalid \[rom\.red\] configuration: expected a table",
                ),
                "malformed toml": ("[rom.red\nsha1 = \"bad\"\n", "unable to load ROM configuration"),
            }
            for name, (contents, message) in cases.items():
                with self.subTest(config=name):
                    config = root / "config"
                    config.mkdir(exist_ok=True)
                    (config / "pipeline.toml").write_text(contents, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        verify_rom(rom, "red", config_root=root)

    def test_missing_version_and_mismatch_keep_existing_errors(self):
        payload = b"test red ROM"
        actual = hashlib.sha1(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            rom.write_bytes(payload)
            self.write_config(root, red="0" * 40, blue="1" * 40, yellow="1" * 40)
            with self.assertRaisesRegex(ValueError, "unsupported version 'green'"):
                verify_rom(rom, "green")
            with self.assertRaisesRegex(ValueError, rf"red ROM SHA-1 mismatch: {actual} \(expected {'0' * 40}\)"):
                verify_rom(rom, "red", config_root=root)

    def test_import_rom_uses_internal_worker_when_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            out = root / "out"
            assets = root / "assets"
            (root / "tools").mkdir()
            script = root / "tools" / "build_rom_data.py"
            script.write_text("# test fixture\n", encoding="utf-8")
            with (
                patch("pipeline.roms.verify_rom"),
                patch("pipeline.roms.is_frozen", return_value=True),
                patch("pipeline.roms.sys.executable", r"C:\bundle\builder.exe"),
                patch("pipeline.roms.subprocess.run") as run,
            ):
                import_rom("red", rom, root, out, assets, only=["text"])
            command = run.call_args.args[0]
            self.assertEqual(command[:2], [r"C:\bundle\builder.exe", "--internal-worker"])
            self.assertTrue(os.path.samefile(command[2], script))
            self.assertIn("--only", command)
            self.assertTrue(os.path.samefile(run.call_args.kwargs["cwd"], root / "tools"))

    def test_import_rom_keeps_python_script_dispatch_when_unfrozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            out = root / "out"
            assets = root / "assets"
            (root / "tools").mkdir()
            script = root / "tools" / "build_rom_data.py"
            script.write_text("# test fixture\n", encoding="utf-8")
            with (
                patch("pipeline.roms.verify_rom"),
                patch("pipeline.roms.is_frozen", return_value=False),
                patch("pipeline.roms.sys.executable", "/venv/bin/python"),
                patch("pipeline.roms.subprocess.run") as run,
            ):
                import_rom("red", rom, root, out, assets)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "/venv/bin/python")
            self.assertTrue(os.path.samefile(command[1], script))
            self.assertNotIn("--internal-worker", command)

    def test_import_rom_streams_subprocess_output_to_log_fn(self):
        class Process:
            stdout = iter(("created dataset\n", "  lang/dialogue.lua   2582 entries\n"))

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            out = root / "out"
            assets = root / "assets"
            (root / "tools").mkdir()
            (root / "tools" / "build_rom_data.py").write_text("# test fixture\n", encoding="utf-8")
            messages: list[str] = []
            with (
                patch("pipeline.roms.verify_rom"),
                patch("pipeline.roms.is_frozen", return_value=False),
                patch("pipeline.roms.subprocess.Popen", return_value=Process()) as popen,
            ):
                import_rom("red", rom, root, out, assets, log_fn=messages.append)
            self.assertEqual(messages[1:], ["created dataset", "  lang/dialogue.lua   2582 entries"])
            self.assertTrue(messages[0].startswith("\n> "))
            self.assertTrue(os.path.samefile(popen.call_args.kwargs["cwd"], root / "tools"))

    def test_import_rom_raises_on_nonzero_exit_with_log_fn(self):
        class Process:
            stdout = iter(())

            @staticmethod
            def wait():
                return 120

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "red.gb"
            out = root / "out"
            assets = root / "assets"
            (root / "tools").mkdir()
            (root / "tools" / "build_rom_data.py").write_text("# test fixture\n", encoding="utf-8")
            with (
                patch("pipeline.roms.verify_rom"),
                patch("pipeline.roms.is_frozen", return_value=False),
                patch("pipeline.roms.subprocess.Popen", return_value=Process()),
                self.assertRaises(RuntimeError) as caught,
            ):
                import_rom("red", rom, root, out, assets, log_fn=lambda message: None)
            self.assertIn("exit code 120", str(caught.exception))


class GoldImportTests(unittest.TestCase):
    """Gold has a separate extractor contract despite sharing ROM config."""

    @staticmethod
    def write_extractor_outputs(destination: str | Path) -> None:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for name in GOLD_REQUIRED_TSV:
            (destination / name).write_text("fixture\n", encoding="utf-8")

    def test_verify_gold_rom_accepts_the_canonical_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "gold.gbc"
            rom.write_bytes(b"pretend gold rom bytes")
            with patch("pipeline.roms.sha1", return_value=GOLD_SHA1):
                info = verify_gold_rom(rom)
            self.assertEqual(info["version"], "gold")
            self.assertEqual(info["sha1"], GOLD_SHA1)

    def test_verify_gold_rom_rejects_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "gold.gbc"
            payload = b"not the real gold rom"
            rom.write_bytes(payload)
            actual = hashlib.sha1(payload).hexdigest()
            with self.assertRaisesRegex(ValueError, rf"gold ROM SHA-1 mismatch: {actual} \(expected {GOLD_SHA1}\)"):
                verify_gold_rom(rom)

    def test_import_gold_rom_builds_the_luajit_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = root / "gold.gbc"
            out = root / "out"
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch(
                    "pipeline.roms.subprocess.run",
                    side_effect=lambda command, **_: self.write_extractor_outputs(command[-1]),
                ) as run,
            ):
                import_gold_rom(rom, root / "engine", out)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/luajit")
            self.assertEqual(Path(command[1]), root / "tools" / "gold_extract.lua")
            self.assertEqual(Path(command[2]), (root / "engine").resolve())
            self.assertEqual(Path(command[3]), rom.resolve())
            self.assertTrue(out.is_dir())
            self.assertEqual({path.name for path in out.iterdir()}, set(GOLD_REQUIRED_TSV))

    def test_import_gold_rom_raises_without_luajit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value=None),
                self.assertRaisesRegex(RuntimeError, "LuaJIT"),
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", root / "out")

    def test_import_gold_rom_streams_subprocess_output_to_log_fn(self):
        class Process:
            stdout = iter(("constants      ok\n", "text pointers   : 3044\n"))

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            messages: list[str] = []
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch(
                    "pipeline.roms.subprocess.Popen",
                    side_effect=lambda command, **_: (
                        self.write_extractor_outputs(command[-1]) or Process()
                    ),
                ),
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", root / "out", log_fn=messages.append)
            self.assertEqual(messages[1:], ["constants      ok", "text pointers   : 3044"])
            self.assertTrue(messages[0].startswith("\n> "))

    def test_import_gold_rom_rejects_incomplete_output_without_replacing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            (out / "keep.txt").write_text("old cache", encoding="utf-8")

            def incomplete(command, **_):
                destination = Path(command[-1])
                destination.mkdir(parents=True, exist_ok=True)
                (destination / GOLD_REQUIRED_TSV[0]).write_text("partial\n", encoding="utf-8")

            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch("pipeline.roms.subprocess.run", side_effect=incomplete),
                self.assertRaisesRegex(RuntimeError, "required non-empty outputs"),
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", out)
            self.assertEqual((out / "keep.txt").read_text(encoding="utf-8"), "old cache")

    def test_import_gold_rom_preserves_cache_when_extractor_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            (out / "keep.txt").write_text("old cache", encoding="utf-8")
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch(
                    "pipeline.roms.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, ["luajit"]),
                ),
                # log_fn=None (the CLI path) now goes through the same
                # run_streamed() as the GUI's log_fn path (pipeline.roms and
                # pipeline.builder share it), so this raises the same clean
                # RuntimeError rather than a raw CalledProcessError.
                self.assertRaises(RuntimeError) as caught,
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", out)
            self.assertIn("exit code 1", str(caught.exception))
            self.assertEqual((out / "keep.txt").read_text(encoding="utf-8"), "old cache")

    def test_import_gold_rom_raises_on_nonzero_exit_with_log_fn(self):
        class Process:
            stdout = iter(())

            @staticmethod
            def wait():
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch("pipeline.roms.subprocess.Popen", return_value=Process()),
                self.assertRaises(RuntimeError) as caught,
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", root / "out", log_fn=lambda message: None)
            self.assertIn("exit code 1", str(caught.exception))

    def test_import_gold_rom_error_includes_the_extractor_own_output(self):
        # A real GUI report showed only "Command [...] returned non-zero
        # exit status 1" -- the gold_extract.lua failure itself, streamed to
        # the log panel while the command ran, was lost from the error the
        # user actually saw.
        class Process:
            stdout = iter(("lua: gold_extract.lua:42: bad ROM bank\n",))

            @staticmethod
            def wait():
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("pipeline.roms.verify_gold_rom"),
                patch("pipeline.roms.which_luajit", return_value="/usr/bin/luajit"),
                patch("pipeline.roms.resource_root", return_value=root),
                patch("pipeline.roms.subprocess.Popen", return_value=Process()),
                self.assertRaises(RuntimeError) as caught,
            ):
                import_gold_rom(root / "gold.gbc", root / "engine", root / "out", log_fn=lambda message: None)
            self.assertIn("bad ROM bank", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
