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
from pipeline import project
from pipeline.project import project_version
from pipeline.rom_paths import load_rom_paths
from pipeline.gui import available_font_profiles, coverage_lines, font_profile_label, language_code, validate_inputs
from pipeline.specs import BuildRequest, ReleaseProfile, release_profile
from pipeline.engine_profile import PINNED_PROFILE, UPSTREAM_PROFILE


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

    def test_run_failure_includes_captured_output_in_the_gui_error(self):
        # A bare "exit code 1" with no other detail is exactly what a real
        # GUI bug report showed: the GUI's error dialog only ever saw this
        # message, never the separate "Show log" panel's transcript.
        class Process:
            stdout = iter(("modkit: validate --strict\n", "MK006 WARN: ...\n"))

            @staticmethod
            def wait():
                return 1

        with patch("pipeline.builder.subprocess.Popen", return_value=Process()):
            with self.assertRaises(builder.BuildError) as caught:
                builder._run(["tool"], log_fn=lambda _: None)
        message = str(caught.exception)
        self.assertIn("Command failed with exit code 1: tool", message)
        self.assertIn("modkit: validate --strict", message)
        self.assertIn("MK006 WARN: ...", message)

    def test_run_failure_truncates_a_long_transcript_to_its_tail(self):
        class Process:
            stdout = iter(f"line {i}\n" for i in range(60))

            @staticmethod
            def wait():
                return 1

        with patch("pipeline.builder.subprocess.Popen", return_value=Process()):
            with self.assertRaises(builder.BuildError) as caught:
                builder._run(["tool"], log_fn=lambda _: None)
        message = str(caught.exception)
        self.assertNotIn("line 0\n", message)
        self.assertIn("line 59", message)
        self.assertIn("20 earlier line(s) omitted", message)

    def test_run_cli_path_omits_captured_output_since_it_inherits_the_console(self):
        with patch("pipeline.builder.subprocess.run", side_effect=builder.subprocess.CalledProcessError(1, ["tool"])):
            with self.assertRaises(builder.BuildError) as caught:
                builder._run(["tool"])
        self.assertEqual(str(caught.exception), "Command failed with exit code 1: tool")

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
        self.assertEqual(builder.languages_for_generation(1)[-1][0], "ja-Hrkt")
        self.assertEqual(builder.languages_for_generation(2)[-1][0], "ko")
        self.assertEqual(language_code("Korean (ko)", 2), "ko")
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.json"
            report.write_text(json.dumps({"rom": {"translated": 2, "total": 4, "percent": 50}, "engine": {"translated": 1, "total": 2, "percent": 50}, "engine_rby": {"translated": 3, "total": 4, "percent": 75}}), encoding="utf-8")
            self.assertEqual(coverage_lines(report), ["Red Blue ROM aggregate: 2/4 (50.00%)", "RBY-related engine strings: 3/4 (75.00%)", "All engine strings: 1/2 (50.00%)"])

            report.write_text(json.dumps({
                "rom": {"translated": 2, "total": 4, "percent": 50},
                "yellow": {"coverage": {"rom": {
                    "translated": 5, "total": 6, "percent": 83.33,
                }}},
            }), encoding="utf-8")
            self.assertIn(
                "Yellow ROM aggregate: 5/6 (83.33%)",
                coverage_lines(report),
            )

            # ROM aggregates first (Red/Blue then Yellow), then engine
            # metrics from most to least specific: RBY-related before the
            # unfiltered "All engine strings" total.
            report.write_text(json.dumps({
                "rom": {"translated": 3278, "total": 3278, "percent": 100},
                "engine": {"translated": 352, "total": 951, "percent": 37.01},
                "engine_rby": {"translated": 256, "total": 256, "percent": 100},
                "yellow": {"coverage": {"rom": {
                    "translated": 3402, "total": 3402, "percent": 100,
                }}},
            }), encoding="utf-8")
            self.assertEqual(coverage_lines(report), [
                "Red Blue ROM aggregate: 3278/3278 (100.00%)",
                "Yellow ROM aggregate: 3402/3402 (100.00%)",
                "RBY-related engine strings: 256/256 (100.00%)",
                "All engine strings: 352/951 (37.01%)",
            ])

            report.write_text(json.dumps({
                "rom": {"translated": 8, "total": 9, "percent": 88.89},
                "rom_dialogue": {"translated": 1, "total": 2, "percent": 50},
                "rom_catalogs": {"translated": 7, "total": 7, "percent": 100},
                "engine": {"translated": 2, "total": 4, "percent": 50},
                "engine_gen2": {"translated": 1, "total": 2, "percent": 50},
            }), encoding="utf-8")
            self.assertEqual(coverage_lines(report, generation=2), [
                "Gold and Silver ROM aggregate: 8/9 (88.89%)",
                "Gold and Silver-related engine strings: 1/2 (50.00%)",
                "All engine strings: 2/4 (50.00%)",
            ])

    def test_gui_validation_requires_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rb, yellow = root / "red.gb", root / "yellow.gb"
            rb.write_bytes(b"red")
            yellow.write_bytes(b"yellow")
            rom_paths = {"rb": rb, "yellow": yellow}
            with patch.object(builder, "verify_rb_rom"), patch.object(builder, "verify_rom"), self.assertRaisesRegex(builder.BuildError, "output directory"):
                validate_inputs(1, rom_paths, "fr", "")
            with patch.object(builder, "verify_rb_rom"), patch.object(builder, "verify_rom"):
                inputs = validate_inputs(1, rom_paths, "fr", root / "out")
            self.assertEqual(inputs.language, "fr")
            self.assertEqual(inputs.font_profile, "fusion")
            self.assertEqual(inputs.rom_paths["yellow"], yellow.resolve())

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
                validate_inputs(1, {"red": red, "blue": blue, "yellow": yellow}, "ja-Hrkt", root / "out", "pokemon")

    def test_cli_and_gui_lock_korean_to_fusion_font(self):
        self.assertEqual(builder._prompt_font_profile("ko", lambda _: self.fail("Pokemon must not be offered")), "fusion")
        self.assertEqual(available_font_profiles("ko"), ("fusion",))
        self.assertEqual(available_font_profiles("ja-Hrkt"), ("fusion",))
        self.assertEqual(available_font_profiles("fr"), ("fusion", "pokemon"))

    def test_release_collections_are_derived_from_game_specs(self):
        self.assertEqual(release_profile("rby").corpus_collections, ("RedBlue", "Yellow"))
        self.assertEqual(release_profile("gsc").corpus_collections, ("GoldSilver", "Crystal"))

    def test_build_request_requires_exact_profile_sources(self):
        rby = release_profile("rby")
        with self.assertRaisesRegex(ValueError, "missing ROM sources: yellow"):
            BuildRequest({"rb": Path("red.gb")}, rby, "fr").validate()
        with self.assertRaisesRegex(ValueError, "unexpected ROM sources: gs"):
            BuildRequest(
                {
                    "rb": Path("red.gb"),
                    "yellow": Path("yellow.gb"), "gs": Path("gold.gbc"),
                },
                rby,
                "fr",
            ).validate()

    def test_build_request_rejects_a_profile_generation_mismatch(self):
        profile = ReleaseProfile("invalid", 2, ("rb",))
        with self.assertRaisesRegex(ValueError, "mixes game generations"):
            BuildRequest({"rb": Path("red.gb")}, profile, "fr").validate()

    def test_korean_uses_fusion_and_rejects_pokemon_font(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold = root / "gold.gbc"
            gold.write_bytes(b"gold")
            crystal = root / "crystal.gbc"
            crystal.write_bytes(b"crystal")
            roms = {"gs": gold, "crystal": crystal}
            with patch.object(builder, "verify_gs_rom"), patch.object(builder, "verify_crystal_rom"):
                inputs = validate_inputs(2, roms, "ko", root / "out", "fusion")
                self.assertEqual(inputs.language, "ko")
                with self.assertRaisesRegex(ValueError, "Pokemon Font"):
                    validate_inputs(2, roms, "ko", root / "out", "pokemon")
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
            rb, yellow = (root / name for name in ("red.gb", "yellow.gb"))
            for rom in (rb, yellow):
                rom.write_bytes(b"rom")
            configured = {"rom": {"red": rb, "yellow": yellow}}
            prompts = []
            answers = iter(("", "", "2", ""))
            events = []

            def input_fn(prompt):
                prompts.append(prompt)
                return next(answers)

            def verify_rb(path):
                events.append(("verify_rb", path))
                return {"version": "red", "path": str(path), "sha1": "x", "size": 3}

            def verify(path, version):
                events.append(("verify", version, path))

            with (
                patch.object(builder, "check_prerequisites", return_value="luajit"),
                patch.object(builder, "load_rom_paths", return_value=configured) as load,
                patch.object(builder, "configured_path", wraps=builder.configured_path) as configured_lookup,
                patch.object(builder, "verify_rb_rom", side_effect=verify_rb),
                patch.object(builder, "verify_rom", side_effect=verify),
                patch.object(builder, "_confirm", return_value=True),
                patch.object(builder, "build", return_value=root / "out.zip") as build,
            ):
                self.assertEqual(builder.main(input_fn, generation=1), 0)
            load.assert_called_once_with(builder.ROOT / "config" / "rom_paths.toml")
            self.assertEqual([call.args for call in configured_lookup.call_args_list],
                             [(configured, "rom", "red"), (configured, "rom", "yellow")])
            self.assertEqual(events, [("verify_rb", rb.resolve()), ("verify", "yellow", yellow.resolve())])
            self.assertFalse(any("localized" in prompt.lower() for prompt in prompts))
            # _prompt_configured_path returns configured.resolve(): on some
            # Windows runners the tempdir is handed out as an 8.3 short name
            # (RUNNER~1) that resolve() canonicalizes to the long form, so
            # compare against the same resolved path rather than the raw one.
            self.assertEqual(build.call_args.kwargs["yellow_rom"], yellow.resolve())

    def test_main_japanese_uses_same_red_blue_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rb, yellow = (root / name for name in ("red.gb", "yellow.gb"))
            for rom in (rb, yellow):
                rom.write_bytes(b"rom")
            configured = {"rom": {"red": rb, "yellow": yellow}}
            answers = iter(("", "", "5"))
            with (
                patch.object(builder, "check_prerequisites", return_value="luajit"),
                patch.object(builder, "load_rom_paths", return_value=configured),
                patch.object(builder, "verify_rb_rom"),
                patch.object(builder, "verify_rom"),
                patch.object(builder, "_confirm", return_value=True),
                patch.object(builder, "build", return_value=root / "out.zip"),
            ):
                self.assertEqual(builder.main(lambda prompt: next(answers), generation=1), 0)
            self.assertEqual(configured["rom"].keys(), {"red", "yellow"})

    def test_main_explicit_pokemon_profile_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rb, yellow = (root / name for name in ("red.gb", "yellow.gb"))
            rb.write_bytes(b"rom")
            yellow.write_bytes(b"rom")
            configured = {"rom": {"red": rb, "yellow": yellow}}
            output = io.StringIO()
            answers = iter(("", "", "1"))
            with redirect_stdout(output):
                with patch.object(builder, "check_prerequisites", return_value="luajit"), \
                    patch.object(builder, "load_rom_paths", return_value=configured), \
                    patch.object(builder, "verify_rb_rom"), \
                    patch.object(builder, "verify_rom"), \
                    patch.object(builder, "_confirm", return_value=True), \
                    patch.object(builder, "build", return_value=root / "out.zip") as build:
                    self.assertEqual(builder.main(lambda _: next(answers), font_profile="pokemon", generation=1), 0)
            self.assertIn("some translated text may overflow", output.getvalue())
            self.assertEqual(build.call_args.kwargs["font_profile"], "pokemon")

    def test_main_prompts_which_games_before_anything_else(self):
        # Gen 1 gate: with no generation injected, the flow leads with the
        # games question, and everything after an answer of "1" (or the
        # default) reproduces the pre-existing Gen 1 prompt sequence
        # unchanged.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rb, yellow = (root / name for name in ("red.gb", "yellow.gb"))
            for rom in (rb, yellow):
                rom.write_bytes(b"rom")
            configured = {"rom": {"red": rb, "yellow": yellow}}
            prompts = []
            answers = iter(("", "", "", "2", ""))

            def input_fn(prompt):
                prompts.append(prompt)
                return next(answers)

            output = io.StringIO()
            with redirect_stdout(output):
                with (
                    patch.object(builder, "check_prerequisites", return_value="luajit"),
                    patch.object(builder, "load_rom_paths", return_value=configured),
                    patch.object(builder, "verify_rb_rom"),
                    patch.object(builder, "verify_rom"),
                    patch.object(builder, "_confirm", return_value=True),
                    patch.object(builder, "build", return_value=root / "out.zip") as build,
                ):
                    self.assertEqual(builder.main(input_fn), 0)
            self.assertIn("Which games do you want to translate?", output.getvalue())
            self.assertEqual(prompts[0], "Games number [1]: ")
            self.assertEqual(build.call_args.kwargs["yellow_rom"], yellow.resolve())

    def test_main_gs_prompts_single_rom_and_builds_gs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold = root / "gold.gbc"
            gold.write_bytes(b"rom")
            crystal = root / "crystal.gbc"
            crystal.write_bytes(b"rom")
            configured = {"rom": {"gold": gold, "crystal": crystal}}
            prompts = []
            # "" accepts the configured Gold path, "" the configured Crystal
            # path; "5" selects Japanese, which skips the font-profile prompt
            # (Fusion-only), so no fourth answer is needed.
            answers = iter(("", "", "5"))

            def input_fn(prompt):
                prompts.append(prompt)
                return next(answers)

            with (
                patch.object(builder, "check_prerequisites", return_value="luajit"),
                patch.object(builder, "load_rom_paths", return_value=configured),
                patch.object(builder, "verify_gs_rom") as verify,
                patch.object(builder, "verify_crystal_rom") as verify_crystal,
                patch.object(builder, "_confirm", return_value=True),
                patch("pipeline.gs_mod.build_gs", return_value=root / "out.zip") as build_gs,
            ):
                self.assertEqual(builder.main(input_fn, generation=2), 0)
            verify.assert_called_once_with(gold.resolve())
            verify_crystal.assert_called_once_with(crystal.resolve())
            self.assertFalse(any("Red" in prompt or "Blue" in prompt or "Yellow" in prompt for prompt in prompts))
            self.assertEqual(build_gs.call_args.args[0], gold.resolve())
            self.assertEqual(build_gs.call_args.args[1], crystal.resolve())
            self.assertEqual(build_gs.call_args.args[2], "ja-Hrkt")
            self.assertEqual(build_gs.call_args.args[3], "Japanese")
            self.assertEqual(build_gs.call_args.kwargs["font_profile"], "fusion")

    def test_invalid_injected_generation_fails_cleanly(self):
        with (
            patch.object(builder, "check_prerequisites", return_value="luajit"),
            patch.object(builder, "load_rom_paths", return_value={"rom": {}}),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(builder.main(lambda _: "", generation=9), 1)
        self.assertIn("Invalid games selection", stderr.getvalue())

    def test_generation_menu(self):
        with patch("builtins.print"):
            self.assertEqual(builder._prompt_generation(lambda _: ""), 1)
            self.assertEqual(builder._prompt_generation(lambda _: "1"), 1)
            self.assertEqual(builder._prompt_generation(lambda _: "2"), 2)

    def test_invalid_generation_menu(self):
        with patch("builtins.print"), self.assertRaises(builder.BuildError):
            builder._prompt_generation(lambda _: "9")

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
            builder.ROOT / "overrides" / "fr" / "rby" / "corpus.json",
        )
        self.assertEqual(
            builder._engine_overrides_path("es"),
            builder.ROOT / "overrides" / "es" / "rby" / "engine.json",
        )
        self.assertTrue(builder._corpus_overrides_path("fr").is_file())
        self.assertTrue(builder._engine_overrides_path("it").is_file())

    def test_rby_override_layers_are_selected_only_by_explicit_profile(self):
        pinned = builder._rby_engine_override_paths("fr", PINNED_PROFILE)
        upstream = builder._rby_engine_override_paths("fr", UPSTREAM_PROFILE)
        self.assertEqual(pinned, (builder._engine_overrides_path("fr"),))
        self.assertEqual(len(upstream), 2)
        self.assertTrue(upstream[0].name == "engine.json")
        self.assertTrue(upstream[1].name == "engine_upstream.json")
        self.assertNotEqual(pinned, upstream)

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

    def test_stale_non_git_destination_is_replaced_not_crashed_on(self):
        # A destination can exist without being a git checkout: a prior run
        # that used the archive path instead (frozen build, or a switch
        # between the two), an interrupted clone, or a corrupted directory.
        # `git clone` refuses a non-empty destination, so this must not
        # blindly attempt one.
        calls = []

        def run(command, **kwargs):
            calls.append(command)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "repo"
            destination.mkdir(parents=True)
            (destination / ".archive-marker.json").write_text("{}", encoding="utf-8")
            (destination / "leftover.txt").write_text("stale archive content", encoding="utf-8")
            builder.ensure_checkout("url", "revision", destination, runner=run)
            self.assertEqual(calls[0][:3], ["git", "clone", "--no-checkout"])
            self.assertFalse((destination / "leftover.txt").exists())

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

    def test_prepare_dependencies_verifies_engine_before_other_dependencies(self):
        calls = []

        def fake_ensure(config, destination, **kwargs):
            calls.append(destination.name)
            return destination

        def fake_verify(*args):
            calls.append("verify-engine")
            return args[0] / "src", args[0], "revision"

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(builder, "_ensure_dependency", side_effect=fake_ensure), \
                patch.object(builder, "_font_source", side_effect=lambda *args, **kwargs: calls.append("font") or Path(directory) / "font"), \
                patch("pipeline.engine_scope.verified_source", side_effect=fake_verify):
            builder.prepare_dependencies(
                Path(directory), builder.project_config(),
                corpus_collection="RedBlue", font_profile="fusion", language="fr",
            )
        self.assertEqual(calls[:3], ["gen1recomp", "verify-engine", "poke-corpus"])
        self.assertEqual(calls[3], "font")

    def test_prepare_dependencies_uses_explicit_upstream_checkout(self):
        calls = []

        def fake_ensure(config, destination, **kwargs):
            calls.append(destination.name)
            return destination

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "upstream"
            (engine / "src").mkdir(parents=True)
            (engine / "tools").mkdir()
            (engine / "tools" / "modkit.py").write_text("", encoding="utf-8")
            with patch.object(builder, "_ensure_dependency", side_effect=fake_ensure), \
                    patch.object(builder, "_font_source", return_value=root / "font"), \
                    patch("pipeline.engine_scope.verified_source") as verify:
                prepared, _, _ = builder.prepare_dependencies(
                    root / "workspace", builder.project_config(),
                    corpus_collection="RedBlue", font_profile="fusion", language="fr",
                    engine_source=engine,
                )
            self.assertEqual(prepared, engine.resolve())
            self.assertEqual(calls, ["poke-corpus"])
            verify.assert_not_called()

    def test_rby_upstream_profile_requires_an_explicit_checkout(self):
        with self.assertRaisesRegex(builder.BuildError, "upstream-local.*engine-source.*checkout"):
            builder.build(
                Path("missing-red.gb"), "fr", "French", "luajit",
                engine_profile=UPSTREAM_PROFILE,
            )

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

    def test_scaffold_species_kinds_injection_applies_generated_catalog(self):
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
            (mod / "lang" / "species_kinds.lua").write_text(
                'return {\n  ["ABRA"] = "SEED",\n}\n', encoding="utf-8"
            )

            builder.preserve_scaffold_support(scaffold, mod)

            main = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('counts.species_kinds = each("species_kinds"', main)
            self.assertIn(
                'mod.content.pokemon:patch(id, { dexEntry = { kind = value } })',
                main,
            )
            self.assertLess(main.index("counts.species_kinds"), main.rfind("\nend"))

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
        self.assertIn("Red Blue ROM aggregate: 3/4 (75.00%)", output.getvalue())
        self.assertIn("All engine strings: 1/2 (50.00%)", output.getvalue())

    def test_coverage_groups_rom_metrics_before_engine_metrics(self):
        # ROM aggregates first (Red/Blue then Yellow), then engine metrics
        # from most to least specific: RBY-related before the unfiltered
        # "All engine strings" total -- matches pipeline.gui.coverage_lines'
        # ordering.
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.json"
            report.write_text(json.dumps({
                "rom": {"translated": 3278, "total": 3278, "percent": 100},
                "engine": {"translated": 352, "total": 951, "percent": 37.01},
                "engine_rby": {"translated": 256, "total": 256, "percent": 100},
                "yellow": {"coverage": {"rom": {
                    "translated": 3402, "total": 3402, "percent": 100,
                }}},
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                builder.print_coverage(report)
        lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
        self.assertEqual(lines, [
            "Translation coverage:",
            "Red Blue ROM aggregate: 3278/3278 (100.00%)",
            "Yellow ROM aggregate: 3402/3402 (100.00%)",
            "RBY-related engine strings: 256/256 (100.00%)",
            "All engine strings: 352/951 (37.01%)",
        ])

    def test_coverage_shows_gold_and_silver_related_engine_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.json"
            report.write_text(json.dumps({
                "rom": {"translated": 8, "total": 9, "percent": 88.89},
                "engine": {"translated": 2, "total": 4, "percent": 50},
                "engine_gen2": {"translated": 1, "total": 2, "percent": 50},
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                builder.print_coverage(report)
        self.assertIn("Gold and Silver-related engine strings: 1/2 (50.00%)", output.getvalue())

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
                patch.object(project, "is_frozen", return_value=True),
                patch.object(project, "resource_root", return_value=root),
                patch.object(builder.platform, "system", return_value="Linux"),
            ):
                self.assertTrue(os.path.samefile(builder._which_luajit(), linux / "luajit"))

            windows = root / "luajit"
            (windows / "luajit.exe").write_bytes(b"MZ")
            (windows / "lua51.dll").write_bytes(b"DLL")
            with (
                patch.object(project, "is_frozen", return_value=True),
                patch.object(project, "resource_root", return_value=root),
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
            self.assertEqual(fetch.call_args.kwargs["immutable_prefixes"], ("src", "tools"))

    def test_merge_engine_overrides_rejects_a_key_present_in_more_than_one_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "engine.json"
            upstream = root / "engine_upstream.json"
            base.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                "entries": {"HELLO": {"override": "Bonjour", "reason": "engine-corpus", "provenance": "x"}},
            }), encoding="utf-8")
            upstream.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                "entries": {"HELLO": {"override": "Salut", "reason": "engine-corpus", "provenance": "y"}},
            }), encoding="utf-8")
            with self.assertRaisesRegex(builder.BuildError, "HELLO"):
                builder._merge_engine_overrides(base, upstream, destination_dir=root, strict=True)
            # Non-strict (the default, used for the shared/Yellow layering)
            # keeps its own deliberate later-wins contract instead.
            destination = builder._merge_engine_overrides(base, upstream, destination_dir=root)
            merged = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(merged["entries"]["HELLO"]["override"], "Salut")

    def test_merge_engine_overrides_combines_disjoint_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "engine.json"
            upstream = root / "engine_upstream.json"
            base.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                "entries": {"HELLO": {"override": "Bonjour", "reason": "engine-corpus", "provenance": "x"}},
            }), encoding="utf-8")
            upstream.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/engine-overrides", "version": 1,
                "entries": {"GOODBYE": {"override": "Au revoir", "reason": "engine-corpus", "provenance": "y"}},
            }), encoding="utf-8")
            destination = builder._merge_engine_overrides(base, upstream, destination_dir=root)
            merged = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(set(merged["entries"]), {"HELLO", "GOODBYE"})


if __name__ == "__main__":
    unittest.main()
