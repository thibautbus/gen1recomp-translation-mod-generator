from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import zipfile

from pipeline import builder
from pipeline.project import project_version
from pipeline.rom_paths import load_rom_paths
from pipeline.gui import coverage_lines, font_profile_label, language_code, validate_inputs


class BuilderTests(unittest.TestCase):
    def test_run_streams_subprocess_output_to_log(self):
        class Process:
            stdout = iter(("first\n", "second\n"))

            @staticmethod
            def wait():
                return 0

        messages = []
        with patch("pipeline.builder.subprocess.Popen", return_value=Process()):
            builder._run(["tool"], log_fn=messages.append)
        self.assertEqual(messages, ["\n> tool", "first", "second"])

    def test_font_dependencies_use_private_cache_and_checked_in_pins(self):
        config = builder.project_config()
        with tempfile.TemporaryDirectory() as directory, patch(
            "pipeline.builder.fetch_files", side_effect=lambda *args, **kwargs: args[2]
        ) as fetch_files, patch(
            "pipeline.builder.fetch_archive", side_effect=lambda *args, **kwargs: args[2]
        ) as fetch_archive:
            source = builder._font_source(Path(directory), config)
            self.assertEqual(source, Path(directory) / "dependencies" / "fusion-pixel-font")
            fetch_files.assert_not_called()
            fetch_archive.assert_called_once()
            self.assertEqual(
                fetch_archive.call_args.args[1],
                "9dba2b4bef9db81c1a1262eed6fa0aca2f6768cad9c6bf54b55d332f86e02f1e",
            )

    def test_pokemon_font_profile_fetches_only_font_files(self):
        config = builder.project_config()
        with tempfile.TemporaryDirectory() as directory, patch(
            "pipeline.builder.fetch_files", side_effect=lambda *args, **kwargs: args[2]
        ) as fetch_files, patch("pipeline.builder.fetch_archive") as fetch_archive:
            source = builder._font_source(Path(directory), config, "pokemon")
            self.assertEqual(source, Path(directory) / "dependencies" / "pokemon-font")
            fetch_files.assert_called_once()
            fetch_archive.assert_not_called()
            self.assertIn("fonts/pokemon-font.ttf", fetch_files.call_args.args[1])

    def test_japanese_fusion_profile_fetches_the_8px_japanese_font(self):
        config = builder.project_config()
        def fake_fetch(*args, **kwargs):
            destination = args[2]
            destination.mkdir(parents=True, exist_ok=True)
            if destination.name.endswith("japanese"):
                (destination / "fusion-pixel-8px-proportional-ja.ttf").write_bytes(b"font")
            return destination
        with tempfile.TemporaryDirectory() as directory, patch(
            "pipeline.builder.fetch_archive", side_effect=fake_fetch
        ) as fetch_archive:
            source = builder._font_source(Path(directory), config, "fusion", "ja-Hrkt")
            self.assertEqual(source, Path(directory) / "dependencies" / "fusion-pixel-font")
            self.assertEqual(fetch_archive.call_count, 2)

    def test_gui_language_and_coverage_helpers(self):
        self.assertEqual(language_code("French (fr)"), "fr")
        self.assertEqual(language_code("ja-Hrkt"), "ja-Hrkt")
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.json"
            report.write_text(json.dumps({"rom": {"translated": 2, "total": 4, "percent": 50}, "engine": {"translated": 1, "total": 2, "percent": 50}, "engine_rby": {"translated": 3, "total": 4, "percent": 75}}), encoding="utf-8")
            self.assertEqual(coverage_lines(report), ["ROM catalog: 2/4 (50.00%)", "All engine strings: 1/2 (50.00%)", "RBY-related engine strings: 3/4 (75.00%)"])

    def test_gui_validation_requires_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red, blue, yellow = root / "red.gb", root / "blue.gb", root / "yellow.gb"
            red.write_bytes(b"red")
            blue.write_bytes(b"blue")
            yellow.write_bytes(b"yellow")
            with patch.object(builder, "verify_rom"), self.assertRaisesRegex(builder.BuildError, "output directory"):
                validate_inputs(red, blue, yellow, "fr", "")
            with patch.object(builder, "verify_rom"):
                inputs = validate_inputs(red, blue, yellow, "fr", root / "out")
            self.assertEqual(inputs.language, "fr")
            self.assertEqual(inputs.font_profile, "fusion")
            self.assertEqual(inputs.yellow_rom, yellow.resolve())

    def test_gui_font_profile_is_fixed_for_japanese(self):
        self.assertIn("recommended", font_profile_label("fusion"))
        self.assertIn("8px", font_profile_label("fusion", "ja-Hrkt"))
        self.assertIn("10px", font_profile_label("fusion", "fr"))
        self.assertIn("some text may overflow", font_profile_label("pokemon"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red, blue, yellow = root / "red.gb", root / "blue.gb", root / "yellow.gb"
            red.write_bytes(b"red")
            blue.write_bytes(b"blue")
            yellow.write_bytes(b"yellow")
            with patch.object(builder, "verify_rom"), self.assertRaisesRegex(ValueError, "only available"):
                validate_inputs(red, blue, yellow, "ja-Hrkt", root / "out", "pokemon")
    def test_absent_rom_path_config_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = load_rom_paths(Path(directory) / "rom_paths.toml")
        self.assertEqual(paths, {"rom": {}})

    def test_partial_config_resolves_relative_quoted_and_tilde_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "rom_paths.toml"
            config.parent.mkdir()
            config.write_text(
                "[rom]\nred = '../roms/Red.gb'\n",
                encoding="utf-8",
            )
            paths = load_rom_paths(config)
        self.assertEqual(paths["rom"]["red"], (root / "roms" / "Red.gb").resolve())
        self.assertNotIn("blue", paths["rom"])

    def test_full_config_accepts_supported_localized_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "rom_paths.toml"
            config.write_text(
                "[rom]\nred = \"red.gb\"\nblue = \"blue.gb\"\n",
                encoding="utf-8",
            )
            paths = load_rom_paths(config)
        self.assertEqual(set(paths["rom"]), {"red", "blue"})

    def test_windows_literal_path_is_parsed_without_posix_absolute_assumption(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "rom_paths.toml"
            config.write_text("[rom]\nred = 'C:\\Games\\PokemonRed.gb'\n", encoding="utf-8")
            paths = load_rom_paths(config)
        if os.name == "nt":
            self.assertEqual(str(paths["rom"]["red"]), r"C:\Games\PokemonRed.gb")
        else:
            self.assertEqual(
                paths["rom"]["red"],
                (config.parent / r"C:\Games\PokemonRed.gb").resolve(),
            )

    def test_config_validation_errors_are_concise(self):
        cases = {
            "malformed": ("[rom\nred = 'x'", "Unable to load ROM path configuration"),
            "unknown key": ("[rom]\ngreen = 'x'", "Unsupported keys in [rom]: green"),
            "unknown section": ("[localized]\nfr = 'x'", "Unsupported ROM path configuration keys"),
            "wrong type": ("[rom]\nred = 1", "[rom].red must be a string path."),
            "wrong table": ("rom = 'red.gb'", "[rom] must be a TOML table."),
        }
        for name, (contents, message) in cases.items():
            with self.subTest(config=name), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "rom_paths.toml"
                config.write_text(contents, encoding="utf-8")
                with self.assertRaises(ValueError) as raised:
                    load_rom_paths(config)
                self.assertIn(message, str(raised.exception))

    def test_configured_path_accept_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "configured.gb"
            replacement = root / "replacement.gb"
            configured.write_bytes(b"rom")
            replacement.write_bytes(b"rom")
            prompts = iter(["", str(replacement)])
            accepted = builder._prompt_configured_path(
                "replacement: ", configured, lambda _: next(prompts)
            )
            self.assertEqual(accepted, configured.resolve())
            prompts = iter(["n", str(replacement)])
            replaced = builder._prompt_configured_path(
                "replacement: ", configured, lambda _: next(prompts)
            )
            self.assertEqual(replaced, replacement.resolve())

    def test_configured_prompt_accepts_yes_and_rejects_no_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "configured.gb"
            replacement = Path(directory) / "replacement.gb"
            configured.write_bytes(b"rom")
            replacement.write_bytes(b"rom")
            for answer in ("y", "yes", "n", "no"):
                with self.subTest(answer=answer):
                    prompts = iter([answer] if answer in {"y", "yes"} else [answer, str(replacement)])
                    result = builder._prompt_configured_path(
                        "replacement: ", configured, lambda _: next(prompts)
                    )
                    expected = configured if answer in {"y", "yes"} else replacement
                    self.assertEqual(result, expected.resolve())

    def test_configured_prompt_retries_invalid_response(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "configured.gb"
            configured.write_bytes(b"rom")
            prompts = iter(["maybe", "YES"])
            with patch("builtins.print") as printed:
                result = builder._prompt_configured_path(
                    "replacement: ", configured, lambda _: next(prompts)
                )
            self.assertEqual(result, configured.resolve())
            printed.assert_called_once_with("Please answer y/yes or n/no.")

    def test_missing_configured_path_allows_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replacement = root / "replacement.gb"
            replacement.write_bytes(b"rom")
            prompts = iter(["", str(replacement)])
            with patch("builtins.print"):
                result = builder._prompt_configured_path(
                    "replacement: ", root / "missing.gb", lambda _: next(prompts)
                )
            self.assertEqual(result, replacement.resolve())

    def test_main_autoloads_red_blue_without_localized_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red, blue, yellow = (root / name for name in ("red.gb", "blue.gb", "yellow.gb"))
            for rom in (red, blue, yellow):
                rom.write_bytes(b"rom")
            configured = {"rom": {"red": red, "blue": blue, "yellow": yellow}}
            prompts = []
            answers = iter(("", "", "", "2", ""))
            events = []

            def input_fn(prompt):
                prompts.append(prompt)
                return next(answers)

            def verify(path, version):
                events.append(("verify", version, path))

            with (
                patch.object(builder, "check_prerequisites", return_value="luajit"),
                patch.object(builder, "load_rom_paths", return_value=configured) as load,
                patch.object(builder, "configured_path", wraps=builder.configured_path) as configured_lookup,
                patch.object(builder, "verify_rom", side_effect=verify),
                patch.object(builder, "_confirm", return_value=True),
                patch.object(builder, "build", return_value=root / "out.zip") as build,
            ):
                self.assertEqual(builder.main(input_fn), 0)
            load.assert_called_once_with(builder.ROOT / "config" / "rom_paths.toml")
            self.assertEqual([call.args for call in configured_lookup.call_args_list],
                             [(configured, "rom", "red"), (configured, "rom", "blue"), (configured, "rom", "yellow")])
            self.assertEqual([event[0] for event in events], ["verify"] * 3)
            self.assertEqual([event[1] for event in events[:3]], ["red", "blue", "yellow"])
            self.assertFalse(any("localized" in prompt.lower() for prompt in prompts))
            # _prompt_configured_path returns configured.resolve(): on some
            # Windows runners the tempdir is handed out as an 8.3 short name
            # (RUNNER~1) that resolve() canonicalizes to the long form, so
            # compare against the same resolved path rather than the raw one.
            self.assertEqual(build.call_args.kwargs["yellow_rom"], yellow.resolve())

    def test_main_japanese_uses_same_red_blue_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red, blue, yellow = (root / name for name in ("red.gb", "blue.gb", "yellow.gb"))
            for rom in (red, blue, yellow):
                rom.write_bytes(b"rom")
            configured = {"rom": {"red": red, "blue": blue, "yellow": yellow}}
            answers = iter(("", "", "", "5"))
            with (
                patch.object(builder, "check_prerequisites", return_value="luajit"),
                patch.object(builder, "load_rom_paths", return_value=configured),
                patch.object(builder, "verify_rom"),
                patch.object(builder, "_confirm", return_value=True),
                patch.object(builder, "build", return_value=root / "out.zip"),
            ):
                self.assertEqual(builder.main(lambda prompt: next(answers)), 0)
            self.assertEqual(configured["rom"].keys(), {"red", "blue", "yellow"})

    def test_main_explicit_pokemon_profile_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red, blue, yellow = (root / name for name in ("red.gb", "blue.gb", "yellow.gb"))
            red.write_bytes(b"rom")
            blue.write_bytes(b"rom")
            yellow.write_bytes(b"rom")
            configured = {"rom": {"red": red, "blue": blue, "yellow": yellow}}
            output = io.StringIO()
            answers = iter(("", "", "", "1"))
            with redirect_stdout(output):
                with patch.object(builder, "check_prerequisites", return_value="luajit"), \
                    patch.object(builder, "load_rom_paths", return_value=configured), \
                    patch.object(builder, "verify_rom"), \
                    patch.object(builder, "_confirm", return_value=True), \
                    patch.object(builder, "build", return_value=root / "out.zip") as build:
                    self.assertEqual(builder.main(lambda _: next(answers), font_profile="pokemon"), 0)
            self.assertIn("some translated text may overflow", output.getvalue())
            self.assertEqual(build.call_args.kwargs["font_profile"], "pokemon")

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

    def test_build_no_longer_accepts_localized_rom(self):
        import inspect
        self.assertNotIn("localized_rom", inspect.signature(builder.build).parameters)

    def test_corpus_and_engine_override_defaults_use_language_subdirectories(self):
        self.assertEqual(
            builder._corpus_overrides_path("fr"),
            builder.ROOT / "overrides" / "fr" / "corpus_overrides.json",
        )
        self.assertEqual(
            builder._engine_overrides_path("es"),
            builder.ROOT / "overrides" / "es" / "shared_engine_overrides.json",
        )
        self.assertTrue(builder._corpus_overrides_path("fr").is_file())
        self.assertTrue(builder._engine_overrides_path("it").is_file())

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
                output.writestr("fonts/pokemon-font.ttf", b"ttf")
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
            (scaffold / "main.lua").write_text(
                'return function(mod)\n  -- mod.content.font:register("ttf", {})\nend\n', encoding="utf-8"
            )
            (scaffold / "lang" / "naming.lua").write_text("return {}", encoding="utf-8")
            (scaffold / "assets" / "font" / "target.png").write_bytes(b"png")
            (scaffold / "assets" / "font" / "README.md").write_text("docs", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            self.assertIn(
                'mod.content.font:register("ttf", {})',
                (mod / "main.lua").read_text(encoding="utf-8"),
            )
            self.assertFalse((mod / "lang" / "charmap.lua").exists())
            self.assertFalse((mod / "assets" / "font").exists())

    def test_scaffold_font_pages_are_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                "  for id, page in pairs({}) do\n"
                "    mod.content.font:register(id, page)\n"
                "  end\n"
                "end\n",
                encoding="utf-8",
            )
            (scaffold / "lang" / "naming.lua").write_text("return {}", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn(
                'mod.content.font:register("ttf", {})',
                main,
            )
            self.assertFalse((mod / "lang" / "font.lua").exists())

    def test_scaffold_raw_option_hook_is_allowlisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                '  each("strings", function(id, value) end)\n'
                "end\n",
                encoding="utf-8",
            )
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text("return {}", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('local raw_option_keys = {', main)
            self.assertIn('["WINDOWED"] = true', main)
            self.assertIn('["1X"] = true', main)
            self.assertIn('by_raw_option[id] = localized', main)
            self.assertIn('localizeRawOption(text)', main)
            self.assertIn('OptionsMenu.draw = function(self, ...)', main)
            self.assertIn('pcall(original_options_draw, self, ...)', main)
            self.assertIn('Font.split, Font.draw = original_split, original_draw', main)

    def test_scaffold_type_names_injection_applies_generated_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                '  counts.statuses = each("status_labels", function(id, value)\n'
                "    mod.content.statuses:patch(id, { label = value })\n"
                "  end)\n"
                '  mod.events:on("game.ready", function() end)\n'
                "end\n",
                encoding="utf-8",
            )
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text("return {}", encoding="utf-8")
            (mod / "lang" / "type_names.lua").write_text(
                'return {\n  ["FIRE"] = "FEU",\n  ["WATER"] = "EAU",\n}\n',
                encoding="utf-8",
            )

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('counts.type_names = each("type_names"', main)
            self.assertIn('by_english[canonical] = localized', main)
            self.assertIn('Font.draw = function(text, x, y, ...)', main)
            self.assertIn('local demo_names = catalog("demo_names")', main)
            self.assertIn('BS.oldManThrow = function(self, ...)', main)
            self.assertIn('localizedDemoName(self, canonical)', main)
            self.assertNotIn('Runtime.hooks:wrap("player.sprite"', main)
            self.assertNotIn('BS.makeOldManDemo = function', main)
            self.assertNotIn('mod.content.type_chart:patch', main)

    def test_scaffold_type_names_injection_falls_back_when_block_drifts(self):
        # The exact statuses block is scaffold-owned; if its spacing drifts
        # upstream, the injection must still land before the closing function
        # boundary instead of failing the build.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                '  counts.statuses = each("status_labels", function(id, value)\n'
                "    mod.content.statuses:patch(id, { label = value })\n"
                "  end)\n"
                '  mod.events:on("game.ready", function() end)\n'
                "end\n".replace("status_labels\", function(", "status_labels\", function  ("),
                encoding="utf-8",
            )
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text("return {}", encoding="utf-8")
            (mod / "lang" / "type_names.lua").write_text(
                'return {\n  ["FIRE"] = "FEU",\n}\n', encoding="utf-8"
            )

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('counts.type_names = each("type_names"', main)
            self.assertIn('by_english[canonical] = localized', main)
            self.assertIn('Font.draw = function(text, x, y, ...)', main)
            self.assertIn('local demo_names = catalog("demo_names")', main)
            self.assertIn('BS.oldManThrow = function(self, ...)', main)
            self.assertIn('localizedDemoName(self, canonical)', main)
            self.assertNotIn('Runtime.hooks:wrap("player.sprite"', main)
            self.assertNotIn('BS.makeOldManDemo = function', main)
            self.assertNotIn('mod.content.type_chart:patch', main)
            self.assertLess(main.index("counts.type_names"), main.rfind("\nend"))

    def test_scaffold_demo_name_hook_is_injected_independently(self):
        # The PROF.OAK demo-name hook must land even when there is no
        # type_names catalog: it depends on trainer_names only.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                "  local thing = true\n"
                "end\n",
                encoding="utf-8",
            )
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text("return {}", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('local demo_names = catalog("demo_names")', main)
            self.assertIn('BS.oldManThrow = function(self, ...)', main)
            self.assertIn('localizedDemoName(self, canonical)', main)
            self.assertIn('name == "PROF.OAK"', main)
            self.assertNotIn('Runtime.hooks:wrap("player.sprite"', main)
            self.assertNotIn('BS.makeOldManDemo = function', main)

    def test_scaffold_type_names_missing_catalog_keeps_main_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scaffold = root / "scaffold"
            mod = root / "mod"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(parents=True)
            (scaffold / "main.lua").write_text(
                "return function(mod)\n"
                '  counts.statuses = each("status_labels", function(id, value)\n'
                "    mod.content.statuses:patch(id, { label = value })\n"
                "  end)\n"
                "end\n",
                encoding="utf-8",
            )
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text("return {}", encoding="utf-8")

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertNotIn("type_names", main)

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
                            "translated": 3,
                            "total": 4,
                            "percent": 75,
                        },
                        "engine": {
                            "translated": 1,
                            "total": 2,
                            "percent": 50,
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                builder.print_coverage(report)
        self.assertIn("ROM catalog: 3/4 (75.00%)", output.getvalue())
        self.assertIn("All engine strings: 1/2 (50.00%)", output.getvalue())

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

    def test_frozen_luajit_resolver_uses_platform_runtime_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linux = root / "luajit"
            (linux / "jit").mkdir(parents=True)
            (linux / "luajit").write_bytes(b"ELF")
            with (
                patch.object(builder, "is_frozen", return_value=True),
                patch.object(builder, "resource_root", return_value=root),
                patch.object(builder.platform, "system", return_value="Linux"),
            ):
                self.assertTrue(os.path.samefile(builder._which_luajit(), linux / "luajit"))

            windows = root / "luajit"
            (windows / "luajit.exe").write_bytes(b"MZ")
            (windows / "lua51.dll").write_bytes(b"DLL")
            with (
                patch.object(builder, "is_frozen", return_value=True),
                patch.object(builder, "resource_root", return_value=root),
                patch.object(builder.platform, "system", return_value="Windows"),
            ):
                self.assertTrue(os.path.samefile(builder._which_luajit(), windows / "luajit.exe"))

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
        environment = Path("/workspace/venv").absolute()
        expected = environment / "bin" / "python"
        self.assertIn(f"active virtual environment is {environment}", hint)
        self.assertIn(f'"{expected}" build_translation.py', hint)

    def test_ensure_dependency_rejects_multi_prefix_in_frozen_archive_mode(self):
        # Regression: a frozen build with more than one selective_prefix and
        # no archive_files table used to hit a bare `assert`, which silently
        # no-ops under `python -O` (extracting only the first prefix with no
        # error) instead of failing loudly. It must now raise a real
        # BuildError regardless of the -O flag.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(builder, "is_frozen", return_value=True):
                with self.assertRaises(builder.BuildError):
                    builder._ensure_dependency(
                        {"archive_url": "https://example.invalid/archive.zip", "archive_sha256": "0" * 64},
                        Path(directory),
                        selective_prefix=["corpus/RedBlue", "corpus/Yellow"],
                    )

    def test_ensure_dependency_allows_single_prefix_in_frozen_archive_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(builder, "is_frozen", return_value=True),
                patch.object(builder, "fetch_archive", return_value=Path(directory)) as fetch,
            ):
                builder._ensure_dependency(
                    {"archive_url": "https://example.invalid/archive.zip", "archive_sha256": "0" * 64},
                    Path(directory),
                    selective_prefix=["corpus/RedBlue"],
                )
            self.assertEqual(fetch.call_args.kwargs["selective_prefix"], "corpus/RedBlue")


if __name__ == "__main__":
    unittest.main()
