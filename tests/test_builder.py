from __future__ import annotations

import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import zipfile

from pipeline import builder
from pipeline.project import project_version


class BuilderTests(unittest.TestCase):
    def test_project_version_comes_from_pyproject(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "1.2.3"\n', encoding="utf-8"
            )
            self.assertEqual(project_version(root), "1.2.3")

    def test_language_menu(self):
        with patch("builtins.print"):
            self.assertEqual(builder._prompt_language(lambda _: "5"), ("ja-Hrkt", "Japanese"))

    def test_invalid_language_menu(self):
        with patch("builtins.print"), self.assertRaises(builder.BuildError):
            builder._prompt_language(lambda _: "99")

    def test_japanese_language_warning(self):
        output = io.StringIO()
        with redirect_stdout(output):
            builder._print_language_warning("ja")
        self.assertIn(
            "Japanese translation does not currently display correctly in game",
            output.getvalue(),
        )

    def test_other_languages_have_no_display_warning(self):
        output = io.StringIO()
        with redirect_stdout(output):
            builder._print_language_warning("fr")
        self.assertEqual(output.getvalue(), "")

    def test_confirmation_defaults_to_yes(self):
        self.assertTrue(builder._confirm(lambda _: ""))
        self.assertTrue(builder._confirm(lambda _: "YES"))
        self.assertFalse(builder._confirm(lambda _: "no"))

    def test_checkout_uses_pinned_revision(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repo"
            builder.ensure_checkout("https://example.invalid/repo.git", "abc123", destination, runner=run)
        self.assertEqual(calls[0][0][:3], ["git", "clone", "--no-checkout"])
        self.assertEqual(calls[1][0], ["git", "fetch", "--depth", "1", "origin", "abc123"])
        self.assertEqual(calls[2][0], ["git", "checkout", "--detach", "abc123"])

    def test_existing_checkout_is_not_cloned_again(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repo"
            (destination / ".git").mkdir(parents=True)
            builder.ensure_checkout(
                "url", "revision", destination,
                runner=lambda command, **kwargs: calls.append(command),
            )
        self.assertEqual([command[1] for command in calls], ["fetch", "checkout"])

    def test_sparse_checkout_fetches_only_requested_blobs(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "poke-corpus"
            builder.ensure_checkout(
                "https://example.invalid/poke-corpus.git",
                "abc123",
                destination,
                sparse_paths=("corpus/RedBlue",),
                runner=run,
            )

        self.assertEqual(
            calls[0][0][:5],
            ["git", "clone", "--no-checkout", "--filter=blob:none", "--sparse"],
        )
        self.assertEqual(
            calls[1][0],
            [
                "git", "fetch", "--depth", "1", "--filter=blob:none",
                "origin", "abc123",
            ],
        )
        self.assertEqual(
            calls[3][0],
            ["git", "sparse-checkout", "set", "corpus/RedBlue"],
        )

    def test_archive_scan_accepts_modkit_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "translation-fr-0.1.0.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("manifest.json", json.dumps({"id": "translation-fr"}))
                output.writestr("lang/dialogue.lua", "return {}")
            builder.inspect_archive(archive)

    def test_archive_scan_rejects_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("manifest.json", "{}")
                output.writestr("translation-worksheet/dialogue.txt", "private")
            with self.assertRaises(builder.BuildError):
                builder.inspect_archive(archive)

    def test_archive_scan_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("manifest.json", "{}")
                output.writestr("../outside.lua", "unsafe")
            with self.assertRaises(builder.BuildError):
                builder.inspect_archive(archive)

    def test_scaffold_runtime_support_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text("scaffold main", encoding="utf-8")
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text(name, encoding="utf-8")
            (scaffold / "assets" / "font" / "target.png").write_bytes(b"png")
            (scaffold / "assets" / "font" / "README.md").write_text("docs", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            self.assertEqual((mod / "main.lua").read_text(encoding="utf-8"), "scaffold main")
            self.assertTrue((mod / "lang" / "charmap.lua").is_file())
            self.assertTrue((mod / "assets" / "font" / "target.png").is_file())
            self.assertFalse((mod / "assets" / "font" / "README.md").exists())

    def test_rejected_candidate_does_not_replace_final_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "dist" / "translation-fr-0.1.0.zip"
            final.parent.mkdir()
            final.write_bytes(b"known-good")
            candidate = root / "private" / "candidate.zip"
            candidate.parent.mkdir()
            with zipfile.ZipFile(candidate, "w") as output:
                output.writestr("manifest.json", "{}")
                output.writestr("data/generated/pokemon.lua", "private")

            with self.assertRaises(builder.BuildError):
                builder.publish_archive(candidate, final)

            self.assertEqual(final.read_bytes(), b"known-good")
            self.assertTrue(candidate.is_file())

    def test_valid_candidate_atomically_replaces_final_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "dist" / "translation-fr-0.1.0.zip"
            final.parent.mkdir()
            final.write_bytes(b"old")
            candidate = root / "private" / "candidate.zip"
            candidate.parent.mkdir()
            with zipfile.ZipFile(candidate, "w") as output:
                output.writestr("manifest.json", "{}")
                output.writestr("main.lua", "return function() end")

            published = builder.publish_archive(candidate, final)

            self.assertEqual(published, final.resolve())
            self.assertTrue(zipfile.is_zipfile(final))
            self.assertFalse(candidate.exists())

    def test_coverage_prints_rom_and_engine_percentages(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.json"
            report.write_text(
                json.dumps(
                    {
                        "rom": {
                            "translated": 3101,
                            "total": 3101,
                            "percent": 100,
                        },
                        "engine": {
                            "translated": 220,
                            "total": 533,
                            "percent": 41.28,
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                builder.print_coverage(report)
        self.assertIn("ROM catalog: 3101/3101 (100.00%)", output.getvalue())
        self.assertIn("Engine catalog: 220/533 (41.28%)", output.getvalue())

    def test_prerequisite_message_is_actionable(self):
        with (
            patch("pipeline.builder.shutil.which", return_value=None),
            patch("pipeline.builder.importlib.util.find_spec", return_value=None),
            self.assertRaises(builder.BuildError) as raised,
        ):
            builder.check_prerequisites()
        message = str(raised.exception)
        self.assertIn("Git", message)
        self.assertIn("LuaJIT", message)
        self.assertIn("Pillow", message)

    def test_luajit_hint_uses_detected_linux_package_manager(self):
        with (
            patch("pipeline.builder.platform.system", return_value="Linux"),
            patch(
                "pipeline.builder.shutil.which",
                side_effect=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
            ),
        ):
            self.assertEqual(
                builder._luajit_install_hint(),
                "run: sudo apt install luajit",
            )

    def test_pillow_hint_detects_bypassed_virtual_environment(self):
        with (
            patch.dict(
                "pipeline.builder.os.environ",
                {"VIRTUAL_ENV": "/workspace/venv"},
                clear=True,
            ),
            patch("pipeline.builder.sys.executable", "/usr/bin/python3"),
            patch("pipeline.builder.platform.system", return_value="Linux"),
        ):
            hint = builder._pillow_install_hint()
        self.assertIn("active virtual environment is /workspace/venv", hint)
        self.assertIn('"/workspace/venv/bin/python" build_translation.py', hint)


if __name__ == "__main__":
    unittest.main()
