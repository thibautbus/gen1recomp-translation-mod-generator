#!/usr/bin/env python3
"""Run the Tkinter Gen1Recomp translation mod builder."""

import runpy
import subprocess
import sys
from pathlib import Path

from pipeline.gui import main


def _force_utf8_subprocess_text_decoding() -> None:
    """The frozen bootstrap re-spawns this executable as the internal
    worker, and PyInstaller's own interpreter startup doesn't reliably
    propagate PYTHONUTF8 to that new process. Vendored tools invoked here
    (gen1recomp's tools/modkit.py dump_dataset()) call
    subprocess.run(text=True) with no explicit encoding, which then falls
    back to the OS locale codepage (e.g. cp1252 on Windows) and crashes on
    dumped text outside it. Patch Popen directly so text-mode subprocess
    calls decode as UTF-8 regardless of locale, matching the encoding the
    Lua sources are written in.
    """
    original_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        if kwargs.get("encoding") is None and (kwargs.get("text") or kwargs.get("universal_newlines")):
            kwargs["encoding"] = "utf-8"
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init


def _internal_worker() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("internal worker requires a script path")
    script, *arguments = sys.argv[2:]
    script_path = Path(script).resolve()
    for entry in (str(script_path.parent), str(Path.cwd())):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    sys.argv[:] = [str(script_path), *arguments]
    _force_utf8_subprocess_text_decoding()
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


def _self_check() -> int:
    """Keep the standalone dispatch contract identical to the CLI entrypoint."""
    from build_translation import _self_check as builder_self_check

    return builder_self_check()


def _gui_self_check() -> int:
    from pipeline.gui import TranslationBuilderApp

    app = TranslationBuilderApp()
    app.root.update_idletasks()
    app.root.destroy()
    print("GUI self-check OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--internal-worker":
        raise SystemExit(_internal_worker())
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        raise SystemExit(_self_check())
    if len(sys.argv) > 1 and sys.argv[1] == "--gui-self-check":
        raise SystemExit(_gui_self_check())
    raise SystemExit(main())
