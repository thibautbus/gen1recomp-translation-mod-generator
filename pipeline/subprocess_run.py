"""Shared streamed-subprocess execution for pipeline.builder and pipeline.roms.

A leaf module on purpose: pipeline.builder imports pipeline.roms (ROM
verification/import), so pipeline.roms cannot import pipeline.builder's own
runner back without a cycle -- this used to mean the two kept independent,
drifting copies of the same streaming/error-tail logic instead.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable

_RUN_ERROR_TAIL_LINES = 40


def run_streamed(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log_fn: Callable[[str], None] | None = None,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    """Run `command`, streaming combined stdout/stderr to `log_fn` if given.

    `log_fn is None` means a CLI caller with its own console: the child
    inherits stdout/stderr directly, and there is nothing to capture for the
    exception message below. Any other caller -- notably the frozen GUI,
    which has no console to inherit at all -- streams the child's combined
    output live via a pipe and includes its tail in the raised error; a bare
    exit code with no detail left real GUI bug reports with nothing to
    diagnose from.
    """
    printable = " ".join(command)
    line = f"\n> {printable}"
    print(line)
    if log_fn:
        log_fn(line)
    captured: list[str] = []
    try:
        if log_fn is None:
            subprocess.run(command, cwd=cwd, env=env, check=True)
        else:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, text=True, errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for output_line in process.stdout:
                output_line = output_line.rstrip("\r\n")
                print(output_line)
                log_fn(output_line)
                captured.append(output_line)
            returncode = process.wait()
            if returncode:
                raise subprocess.CalledProcessError(returncode, command)
    except subprocess.CalledProcessError as error:
        detail = ""
        if captured:
            tail = captured[-_RUN_ERROR_TAIL_LINES:]
            if len(captured) > len(tail):
                tail = [f"... ({len(captured) - len(tail)} earlier line(s) omitted)", *tail]
            detail = "\n\n" + "\n".join(tail)
        raise error_cls(
            f"Command failed with exit code {error.returncode}: {printable}{detail}"
        ) from error
