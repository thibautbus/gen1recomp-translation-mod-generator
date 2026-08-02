#!/usr/bin/env python3
"""Run the Tkinter Gen1Recomp translation mod builder."""

import runpy
import sys
from pathlib import Path

from pipeline.gui import main


def _internal_worker() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("internal worker requires a script path")
    script, *arguments = sys.argv[2:]
    script_path = Path(script).resolve()
    for entry in (str(script_path.parent), str(Path.cwd())):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    sys.argv[:] = [str(script_path), *arguments]
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
