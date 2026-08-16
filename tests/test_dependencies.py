from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
import runpy
import ssl
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
import tomllib
from unittest.mock import patch

from pipeline.dependencies import DependencyError, _default_ssl_context, _tree_digest, fetch_archive, fetch_files


class _Response:
    def __init__(self, data: bytes, url: str = ""): self.data, self.url = data, url
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, size: int = -1):
        if not self.data: return b""
        value, self.data = self.data[:size], self.data[size:]
        return value
    def geturl(self): return self.url


class DependencyTests(unittest.TestCase):
    @staticmethod
    def _paths_equivalent(left, right):
        """Compare paths across Windows short names and POSIX spellings."""
        try:
            return os.path.samefile(left, right)
        except (AttributeError, OSError):
            canonical = lambda value: os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))
            return canonical(left) == canonical(right)

    def test_tree_digest_order_is_portable_across_path_flavours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "src"
            root.mkdir()
            files = {
                "a.lua": b"lower",
                "B.lua": b"upper",
                "a/b.lua": b"nested",
                "a.b.lua": b"dotted sibling",
            }
            for name, payload in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            expected = hashlib.sha256()
            # Explicitly model POSIX Path's component-wise, case-sensitive
            # ordering; this also puts a/b.lua before a.b.lua.
            for name in sorted(files, key=lambda value: tuple(value.split("/"))):
                expected.update(name.encode())
                expected.update(hashlib.sha256(files[name]).digest())

            def windows_sorted(values, *args, **kwargs):
                values = list(values)
                if "key" in kwargs:
                    return builtins.sorted(values, *args, **kwargs)
                return builtins.sorted(values, key=lambda value: tuple(part.casefold() for part in value.parts))

            with patch("pipeline.dependencies.sorted", windows_sorted, create=True):
                self.assertEqual(_tree_digest(root), expected.hexdigest())

    def test_default_ssl_context_prefers_a_working_os_store(self):
        # certifi's bundle is pinned at packaging time with no update
        # automation (packaging/requirements-windows.txt), so it *will* go
        # stale; a working OS store (update-ca-certificates/update-ca-trust)
        # stays current on its own. Only fall back to certifi when the OS
        # lookup finds nothing, not on every call.
        working = SimpleNamespace(cert_store_stats=lambda: {"x509": 5})
        with patch("pipeline.dependencies.ssl.create_default_context", return_value=working) as create_context:
            context = _default_ssl_context()
        create_context.assert_called_once_with()
        self.assertIs(context, working)

    def test_default_ssl_context_falls_back_to_certifi_when_os_store_is_empty(self):
        # A PyInstaller-frozen Linux build bundles the build machine's
        # OpenSSL, whose compiled-in default CA path does not necessarily
        # exist on the user's distro (CERTIFICATE_VERIFY_FAILED reported on
        # CachyOS from the released binary, not from a source checkout) --
        # the default context then loads zero trust anchors.
        try:
            import certifi
        except ImportError:
            self.skipTest("certifi is not installed in this environment")

        empty = SimpleNamespace(cert_store_stats=lambda: {"x509": 0})
        real_create_context = ssl.create_default_context

        def fake_create_context(*args, **kwargs):
            return empty if not kwargs else real_create_context(*args, **kwargs)

        with patch("pipeline.dependencies.ssl.create_default_context", side_effect=fake_create_context) as create_context:
            context = _default_ssl_context()
        create_context.assert_called_with(cafile=certifi.where())
        self.assertIsNot(context, empty)

    def test_default_ssl_context_falls_back_to_empty_os_context_without_certifi(self):
        empty = SimpleNamespace(cert_store_stats=lambda: {"x509": 0})
        real_import = builtins.__import__

        def no_certifi(name, *args, **kwargs):
            if name == "certifi":
                raise ImportError("no module named certifi")
            return real_import(name, *args, **kwargs)

        with patch("pipeline.dependencies.ssl.create_default_context", return_value=empty), \
             patch("builtins.__import__", side_effect=no_certifi):
            context = _default_ssl_context()
        self.assertIs(context, empty)

    def test_windows_packaging_pins_full_luajit_clone(self):
        script = (Path(__file__).parents[1] / "packaging/build_windows_executable.ps1").read_text()
        self.assertNotIn("$Output = & $Command", script)
        self.assertIn("git clone --no-checkout $LuaRepo $LuaSource", script)
        self.assertNotIn("--depth", script)
        self.assertIn("Invoke-Native { git -C $LuaSource checkout --detach $LuaCommit }", script)
        self.assertRegex(
            script,
            r"\$LuaHead\s*=\s*\(Invoke-Native \{ git -C \$LuaSource rev-parse HEAD \} \"git rev-parse\" \| Out-String\)\.Trim\(\)",
        )
        self.assertIn("if ($LuaHead -ne $LuaCommit)", script)
        self.assertIn('cd /d `"$LuaSourceSrc`" && call msvcbuild.bat', script)
        self.assertNotIn("&& call src\\msvcbuild.bat", script)
        self.assertIn('$Spec = Join-Path $Root "packaging/translation_builder.spec"', script)
        self.assertIn("PyInstaller --clean --noconfirm $Spec", script)
        self.assertNotIn("tomllib", script)
        self.assertIn('foreach ($Variant in @("cli", "gui"))', script)
        self.assertIn('$Version-$Variant-windows-x64.exe', script)

    def test_windows_release_downloads_artifact_after_checkout(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/standalone-executables.yml").read_text()
        release = workflow.split("\n  release:\n", 1)[1]
        checkout = release.index("actions/checkout@")
        download = release.index("actions/download-artifact@")
        validate = release.index("Validate tag and publish release asset")
        self.assertLess(checkout, download)
        self.assertLess(download, validate)
        self.assertIn("path: release", release)
        self.assertIn("release/*.exe", release)

    def test_linux_packaging_script_is_pinned_and_cleans_runtime(self):
        script = (Path(__file__).parents[1] / "packaging/build_linux_executable.sh").read_text()
        self.assertIn("faaf663340347a78b22ed94c63c24fe090bd9784", script)
        self.assertIn("file \"$RUNTIME/luajit\"", script)
        self.assertIn("ldd \"$RUNTIME/luajit\"", script)
        self.assertIn('! grep -q \'not found\'', script)
        self.assertIn('for variant in cli gui; do', script)
        self.assertIn('GEN1RECOMP_VARIANT="$variant"', script)
        self.assertIn('"$binary" --self-check', script)
        self.assertIn('"$binary" --gui-self-check', script)
        self.assertIn('xvfb-run -a "$binary" --gui-self-check', script)
        self.assertIn("linux-x86_64", script)
        self.assertIn('versioned="$DIST/gen1recomp-translation-mod-generator-${VERSION}-$variant-linux-x86_64"', script)
        self.assertIn('tar -czf "$versioned.tar.gz"', script)
        self.assertNotIn("tomllib", script)
        self.assertIn('if [[ -e "$RUNTIME" ]]', script)
        self.assertIn('refusing to overwrite existing runtime', script)
        self.assertIn('rm -rf -- "$TMP_ROOT"', script)
        self.assertIn('rm -rf -- "$RUNTIME"', script)

    def test_standalone_workflow_builds_both_platforms_and_releases_both_assets(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/standalone-executables.yml").read_text()
        self.assertIn("build-windows:", workflow)
        self.assertIn("runs-on: windows-2022", workflow)
        self.assertIn("build-linux:", workflow)
        self.assertIn("runs-on: ubuntu-22.04", workflow)
        self.assertIn("apt-get install --no-install-recommends -y build-essential file python3-tk xvfb xauth", workflow)
        self.assertIn("needs: [build-windows, build-linux]", workflow)
        self.assertIn("actions/download-artifact@", workflow)
        self.assertIn("release/*.exe release/*.tar.gz", workflow)
        self.assertEqual(workflow.count("actions/upload-artifact@"), 4)
        self.assertIn("-eq 2", workflow)
        self.assertIn("dist/gen1recomp-translation-mod-generator-*-cli-windows-x64.exe", workflow)
        self.assertIn("dist/gen1recomp-translation-mod-generator-*-gui-windows-x64.exe", workflow)
        self.assertIn("dist/gen1recomp-translation-mod-generator-*-cli-linux-x86_64.tar.gz", workflow)
        self.assertIn("dist/gen1recomp-translation-mod-generator-*-gui-linux-x86_64.tar.gz", workflow)

    def test_spec_luajit_layout_is_not_double_nested(self):
        spec = Path(__file__).parents[1] / "packaging/translation_builder.spec"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_target = root / "build_translation.py"
            script_target.write_text("# test fixture\n")
            runtime = root / "packaging" / "runtime" / "luajit"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin" / "lua.exe").write_bytes(b"lua")
            (runtime / "luajit.exe").write_bytes(b"luajit")
            (runtime / "lua51.dll").write_bytes(b"dll")
            (runtime / "jit").mkdir()
            (runtime / "jit" / "vm.lua").write_bytes(b"jit")
            (runtime / "lib" / "nested").mkdir(parents=True)
            (runtime / "lib" / "nested" / "helper.dll").write_bytes(b"helper")
            captured = {}

            def fake_analysis(*args, **kwargs):
                captured["scripts"] = args[0]
                captured["datas"] = kwargs["datas"]
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

            runpy.run_path(
                str(spec),
                init_globals={
                    # PyInstaller supplies the directory containing the spec.
                    "SPECPATH": str(root / "packaging"),
                    "Analysis": fake_analysis,
                    "PYZ": lambda pure: SimpleNamespace(pure=pure),
                    "EXE": lambda *args, **kwargs: SimpleNamespace(),
                },
            )

            self.assertEqual(len(captured["scripts"]), 1)
            # Windows may spell the same temp directory with an 8.3 short
            # name in `root`; compare the actual files rather than strings.
            self.assertTrue(self._paths_equivalent(captured["scripts"][0], script_target))
            runtime = os.path.normcase(os.path.realpath(runtime))
            destinations = {}
            for source, destination in captured["datas"]:
                source = os.path.normcase(os.path.realpath(source))
                relative = os.path.relpath(source, runtime)
                if relative == os.pardir or relative.startswith(os.pardir + os.sep):
                    continue
                destinations[Path(relative).as_posix()] = Path(destination).as_posix()
            self.assertEqual(destinations["bin/lua.exe"], Path("luajit").joinpath("bin").as_posix())
            self.assertEqual(destinations["luajit.exe"], Path("luajit").as_posix())
            self.assertEqual(destinations["lua51.dll"], Path("luajit").as_posix())
            self.assertEqual(destinations["jit/vm.lua"], Path("luajit").joinpath("jit").as_posix())
            self.assertEqual(destinations["lib/nested/helper.dll"], Path("luajit").joinpath("lib", "nested").as_posix())
            self.assertNotIn("luajit/luajit", " ".join(destinations.values()).replace("\\", "/"))

    def test_spec_places_linux_luajit_in_binaries(self):
        spec = Path(__file__).parents[1] / "packaging/translation_builder.spec"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packaging").mkdir()
            script_target = root / "build_translation.py"
            script_target.write_text("# test fixture\n")
            runtime = root / "packaging" / "runtime" / "luajit"
            (runtime / "jit").mkdir(parents=True)
            (runtime / "luajit").write_bytes(b"ELF")
            (runtime / "jit" / "vm.lua").write_bytes(b"jit")
            captured = {}

            def fake_analysis(*args, **kwargs):
                captured["binaries"] = kwargs["binaries"]
                captured["datas"] = kwargs["datas"]
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

            runpy.run_path(
                str(spec),
                init_globals={
                    "SPECPATH": str(root / "packaging"),
                    "Analysis": fake_analysis,
                    "PYZ": lambda pure: SimpleNamespace(pure=pure),
                    "EXE": lambda *args, **kwargs: SimpleNamespace(),
                },
            )
            self.assertTrue(
                any(
                    destination == "luajit" and self._paths_equivalent(source, runtime / "luajit")
                    for source, destination in captured["binaries"]
                )
            )
            self.assertTrue(
                any(
                    Path(destination).parts == ("luajit", "jit")
                    and self._paths_equivalent(source, runtime / "jit" / "vm.lua")
                    for source, destination in captured["datas"]
                )
            )

    def test_spec_bundles_the_tools_lua_scripts_and_gate_fixtures(self):
        # A real Windows GUI report: a frozen Gold build failed with
        # "cannot open ... tools\gold_extract.lua: No such file or
        # directory" -- resource_root()/"tools"/... (pipeline/roms.py,
        # pipeline/gold_mod.py) resolves to PyInstaller's extraction dir at
        # runtime, but nothing in the spec's datas ever bundled tools/ at
        # all.
        spec = Path(__file__).parents[1] / "packaging/translation_builder.spec"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packaging").mkdir()
            (root / "build_translation.py").write_text("# test fixture\n")
            tools = root / "tools"
            (tools / "gen2_gate_fixtures" / "broken_gold").mkdir(parents=True)
            (tools / "gold_extract.lua").write_text("-- fixture\n")
            (tools / "gate_gen2.lua").write_text("-- fixture\n")
            (tools / "gen2_gate_fixtures" / "broken_gold" / "main.lua").write_text("-- fixture\n")
            (tools / "__pycache__").mkdir()
            (tools / "__pycache__" / "stale.pyc").write_bytes(b"stale bytecode")
            captured = {}

            def fake_analysis(*args, **kwargs):
                captured["datas"] = kwargs["datas"]
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

            runpy.run_path(
                str(spec),
                init_globals={
                    "SPECPATH": str(root / "packaging"),
                    "Analysis": fake_analysis,
                    "PYZ": lambda pure: SimpleNamespace(pure=pure),
                    "EXE": lambda *args, **kwargs: SimpleNamespace(),
                },
            )
            found = {
                (tools / "gold_extract.lua"): "tools",
                (tools / "gate_gen2.lua"): "tools",
                (tools / "gen2_gate_fixtures" / "broken_gold" / "main.lua"): str(Path("tools") / "gen2_gate_fixtures" / "broken_gold"),
            }
            for expected_source, expected_destination in found.items():
                self.assertTrue(
                    any(
                        Path(destination).as_posix() == Path(expected_destination).as_posix()
                        and self._paths_equivalent(source, expected_source)
                        for source, destination in captured["datas"]
                    ),
                    f"{expected_source} missing from datas",
                )
            self.assertFalse(
                any("__pycache__" in Path(source).parts for source, _ in captured["datas"]),
                "stale __pycache__ bytecode should not be bundled",
            )

    def test_spec_selects_gui_entrypoint_without_console(self):
        spec = Path(__file__).parents[1] / "packaging/translation_builder.spec"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packaging").mkdir()
            (root / "build_translation_gui.py").write_text("# test fixture\n")
            captured = {}

            def fake_analysis(*args, **kwargs):
                captured["scripts"] = args[0]
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

            with patch.dict(os.environ, {"GEN1RECOMP_VARIANT": "gui"}):
                runpy.run_path(
                    str(spec),
                    init_globals={
                        "SPECPATH": str(root / "packaging"),
                        "Analysis": fake_analysis,
                        "PYZ": lambda pure: SimpleNamespace(pure=pure),
                        "EXE": lambda *args, **kwargs: captured.setdefault("exe", kwargs) or SimpleNamespace(),
                    },
                )
            self.assertTrue(self._paths_equivalent(captured["scripts"][0], root / "build_translation_gui.py"))
            self.assertFalse(captured["exe"]["console"])

    def test_archive_failure_closes_temp_download_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("repo/../escape", b"bad")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = root / "out"
            destination.mkdir()
            (destination / "keep.txt").write_bytes(b"existing")
            with self.assertRaises(DependencyError):
                fetch_archive("u", digest, destination, opener=lambda _: _Response(archive.read_bytes()))
            self.assertEqual((destination / "keep.txt").read_bytes(), b"existing")
            self.assertEqual(list(root.glob("dependency-*.zip")), [])

    def test_archive_mkdtemp_failure_cleans_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "out"
            with patch("pipeline.dependencies.tempfile.mkdtemp", side_effect=OSError("cannot create temp directory")):
                with self.assertRaises(OSError):
                    fetch_archive(
                        "u",
                        "0" * 64,
                        destination,
                        opener=lambda _: _Response(b"unused"),
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob("dependency-*.zip")), [])

    def test_checked_in_corpus_manifest_has_all_pins(self):
        config = tomllib.loads((Path(__file__).parents[1] / "config/pipeline.toml").read_text())
        corpus = config["corpus"]
        self.assertTrue(all(len(value) == 64 for value in corpus["archive_files"].values()))

    def test_archive_verifies_and_reuses_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as z: z.writestr("repo/src/main.lua", b"ok")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            calls = []
            opener = lambda url: (calls.append(url) or _Response(archive.read_bytes()))
            destination = fetch_archive("https://example/repo.zip", digest, root / "repo", revision="abc", opener=opener)
            self.assertEqual((destination / "src/main.lua").read_bytes(), b"ok")
            (destination / "src/main.lua").write_bytes(b"tampered")
            marker = destination / ".archive-marker.json"
            marker_data = json.loads(marker.read_text()); marker_data["tree_sha256"] = "0" * 64
            marker.write_text(json.dumps(marker_data))
            fetch_archive("https://example/repo.zip", digest, destination, revision="abc", opener=opener)
            self.assertEqual(len(calls), 2)
            self.assertEqual((destination / "src/main.lua").read_bytes(), b"ok")
            self.assertEqual(list(root.glob("dependency-*.zip")), [])

    def test_flat_archive_extracts_font_files_at_root_and_reuses_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / "fonts.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("font.ttf", b"ttf")
                z.writestr("OFL.txt", b"license")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            calls = []
            opener = lambda url: (calls.append(url) or _Response(archive.read_bytes()))
            destination = fetch_archive("https://example/fonts.zip", digest, root / "fonts", opener=opener)
            self.assertEqual((destination / "font.ttf").read_bytes(), b"ttf")
            self.assertEqual((destination / "OFL.txt").read_bytes(), b"license")
            fetch_archive("https://example/fonts.zip", digest, destination, opener=opener)
            self.assertEqual(calls, ["https://example/fonts.zip"])

    def test_archive_rejects_traversal_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z: z.writestr("repo/../escape", b"bad")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(DependencyError):
                fetch_archive("u", digest, root / "out", opener=lambda _: _Response(archive.read_bytes()))
            with self.assertRaises(DependencyError):
                fetch_archive("u", "0" * 64, root / "out", opener=lambda _: _Response(b"bad"))

    def test_security_paths_redirect_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "redirect.zip"
            with zipfile.ZipFile(archive, "w") as z: z.writestr("repo/a", b"x")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(DependencyError):
                fetch_archive("https://x", digest, root / "x", opener=lambda _: _Response(archive.read_bytes(), "http://downgrade"))
            self.assertEqual(list(root.glob("dependency-*.zip")), [])
            for name in ("safe/C:colon.txt", "safe/CON", "safe/NUL", "safe/file.", "safe/file "):
                archive = root / "x.zip"
                with zipfile.ZipFile(archive, "w") as z: z.writestr("repo/" + name, b"x")
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                with self.assertRaises(DependencyError):
                    fetch_archive("u", digest, root / "out", opener=lambda _: _Response(archive.read_bytes()))
            archive = root / "dup.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("repo/F.txt", b"a"); z.writestr("repo/f.txt", b"b")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(DependencyError):
                fetch_archive("u", digest, root / "dup", opener=lambda _: _Response(archive.read_bytes()))

    def test_manifest_streams_files_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b"qid\n"
            manifest = {"corpus/RedBlue/qid_msg.txt": hashlib.sha256(payload).hexdigest()}
            out = fetch_files("https://raw.example/commit", manifest, root / "corpus", revision="rev", opener=lambda _: _Response(payload))
            self.assertEqual((out / "corpus/RedBlue/qid_msg.txt").read_bytes(), payload)
            with self.assertRaises(DependencyError):
                fetch_files("u", {"../bad": "0" * 64}, root / "x", opener=lambda _: _Response(payload))

    def test_manifest_mutation_and_extra_file_are_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b"x"; manifest = {"corpus/RedBlue/qid_msg.txt": hashlib.sha256(payload).hexdigest()}
            calls = []
            opener = lambda url: (calls.append(url) or _Response(payload))
            out = fetch_files("u", manifest, root / "c", revision="r", opener=opener)
            (out / "corpus/RedBlue/qid_msg.txt").write_bytes(b"bad"); (out / "extra").write_bytes(b"bad")
            fetch_files("u", manifest, out, revision="r", opener=opener)
            self.assertEqual(len(calls), 2); self.assertFalse((out / "extra").exists())
