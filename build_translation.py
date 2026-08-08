#!/usr/bin/env python3
"""Run the interactive Gen1Recomp translation mod builder."""

import runpy
import subprocess
import sys
import importlib.util
from pathlib import Path

from pipeline.builder import main


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
    from pipeline.builder import BuildError, _modkit_command, _which_luajit, is_frozen, project_config, project_version, resource_root, work_root
    from pipeline.engine import load_semantic_anchors, load_semantic_anchor_decisions, merge_semantic_anchors
    try:
        config = project_config(resource_root())
        version = project_version(resource_root())
        if not version or "gen1recomp" not in config:
            raise BuildError("bundled project metadata is incomplete")
        anchors_path = resource_root() / "config" / "semantic_anchors.json"
        decisions_path = resource_root() / "config" / "semantic_anchor_decisions.json"
        deterministic = load_semantic_anchors(anchors_path)
        decisions = load_semantic_anchor_decisions(decisions_path)
        merge_semantic_anchors(deterministic, decisions)
        if importlib.util.find_spec("PIL") is None:
            raise BuildError("Pillow is required for private ROM asset extraction")
        corpus = config["corpus"]
        if is_frozen() and not corpus.get("archive_files"):
            raise BuildError("corpus archive manifest is missing")
        if is_frozen() and not _which_luajit():
            raise BuildError("bundled LuaJIT runtime is missing")
        if is_frozen() and _modkit_command(resource_root() / "tools" / "modkit.py")[1] != "--internal-worker":
            raise BuildError("internal worker dispatch is not configured")
        print(f"self-check OK (resource_root={resource_root()}, work_root={work_root()})")
        return 0
    except (BuildError, OSError, KeyError, ValueError) as error:
        print(f"self-check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--internal-worker":
        raise SystemExit(_internal_worker())
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        raise SystemExit(_self_check())
    import argparse
    parser = argparse.ArgumentParser(description="Build a Gen1Recomp translation mod")
    parser.add_argument("--font-profile", choices=("fusion", "pokemon"), default=None)
    args = parser.parse_args()
    raise SystemExit(main(font_profile=args.font_profile))
