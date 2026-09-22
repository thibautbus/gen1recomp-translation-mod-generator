import subprocess
import unittest

import build_translation
import build_translation_gui


class ForceUtf8SubprocessTextDecodingTests(unittest.TestCase):
    """gen1recomp's own tools/modkit.py calls subprocess.run(text=True) with
    no explicit encoding, which falls back to the OS locale codepage and
    crashes on non-Latin-1 dumped text on Windows. Both entry points patch
    Popen before running vendored tools through --internal-worker; lock in
    that patch here since neither script is otherwise covered by tests.
    """

    def _run_patched(self, force_utf8, **kwargs):
        original_init = subprocess.Popen.__init__
        try:
            force_utf8()
            return subprocess.Popen(
                ["python3", "-c", "import sys; sys.stdout.buffer.write(bytes.fromhex('e697a5e69cace8aa9e'))"],
                stdout=subprocess.PIPE, **kwargs,
            )
        finally:
            subprocess.Popen.__init__ = original_init

    def test_cli_worker_forces_utf8_when_text_mode_and_no_encoding(self):
        process = self._run_patched(build_translation._force_utf8_subprocess_text_decoding, text=True)
        try:
            self.assertEqual(process.communicate()[0], "日本語")
        finally:
            process.wait()

    def test_gui_worker_forces_utf8_when_universal_newlines_and_no_encoding(self):
        process = self._run_patched(build_translation_gui._force_utf8_subprocess_text_decoding, universal_newlines=True)
        try:
            self.assertEqual(process.communicate()[0], "日本語")
        finally:
            process.wait()

    def test_explicit_encoding_is_not_overridden(self):
        process = self._run_patched(
            build_translation_gui._force_utf8_subprocess_text_decoding, text=True, encoding="latin-1",
        )
        try:
            decoded = process.communicate()[0]
        finally:
            process.wait()
        self.assertNotEqual(decoded, "日本語")

    def test_binary_mode_is_left_untouched(self):
        process = self._run_patched(build_translation_gui._force_utf8_subprocess_text_decoding)
        try:
            raw = process.communicate()[0]
        finally:
            process.wait()
        self.assertEqual(raw, bytes.fromhex("e697a5e69cace8aa9e"))


if __name__ == "__main__":
    unittest.main()
