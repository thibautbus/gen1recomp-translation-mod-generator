from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
import tomllib
from unittest.mock import patch

from pipeline.dependencies import DependencyError, _tree_digest, fetch_archive, fetch_files


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

    def test_windows_release_downloads_artifact_after_checkout(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/windows-executable.yml").read_text()
        release = workflow.split("\n  release:\n", 1)[1]
        checkout = release.index("actions/checkout@")
        download = release.index("actions/download-artifact@")
        validate = release.index("Validate tag and publish release asset")
        self.assertLess(checkout, download)
        self.assertLess(download, validate)
        self.assertIn("path: release", release)
        self.assertIn("release/*.exe", release)

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
            self.assertTrue(os.path.samefile(captured["scripts"][0], script_target))
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
        self.assertEqual(len(corpus["archive_files"]), 7)
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
