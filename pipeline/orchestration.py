"""Shared dispatch for resolved build requests.

The adapters (interactive CLI and GUI) resolve a ``BuildRequest`` once.  The
release-specific builders remain small implementations of their extraction
contracts, while dependency setup and profile selection stay declarative.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .specs import BuildRequest, ReleaseProfile


@dataclass(frozen=True)
class BuildContext:
    """Common workspace/dependency phase shared by release builders."""
    workspace: Path
    destination: Path
    gen1recomp: Path
    corpus: Path
    font_source: Path
    profile: ReleaseProfile


def prepare_build_context(
    workspace_root: str | Path | None,
    output_dir: str | Path | None,
    *,
    profile: ReleaseProfile,
    language: str,
    font_profile: str,
) -> BuildContext:
    """Resolve paths and prepare exactly the collections in ``profile``."""
    from .builder import prepare_dependencies
    from .project import is_frozen, project_config, work_root

    workspace = Path(workspace_root) if workspace_root is not None else work_root() / ".cache"
    destination = Path(output_dir) if output_dir is not None else (
        work_root() if is_frozen() else work_root() / "dist"
    )
    collections = profile.corpus_collections
    gen1recomp, corpus, font_source = prepare_dependencies(
        workspace.resolve(), project_config(), corpus_collection=collections,
        font_profile=font_profile, language=language,
    )
    return BuildContext(workspace.resolve(), destination.resolve(), gen1recomp, corpus, font_source, profile)


def package_release(
    mod_dir: str | Path,
    gen1recomp: str | Path,
    modkit: str | Path,
    build_root: str | Path,
    destination: str | Path,
    archive_name: str,
    *,
    base: str | None = None,
    env: dict[str, str] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> Path:
    """Pack a generated mod and publish it through one deterministic phase."""
    from .builder import _modkit_command, _run, publish_archive

    mod_dir = Path(mod_dir).resolve()
    gen1recomp = Path(gen1recomp).resolve()
    modkit = Path(modkit).resolve()
    build_root = Path(build_root).resolve()
    destination = Path(destination).resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    candidate = build_root / f"{archive_name}.candidate"
    candidate.unlink(missing_ok=True)
    command = _modkit_command(modkit, "--repo", str(gen1recomp), "pack", str(mod_dir), "-o", str(candidate))
    if base is not None:
        command.extend(("--base", base))
    _run(command, cwd=gen1recomp, env=env, log_fn=log_fn)
    return publish_archive(candidate, destination / archive_name)


def build_request(
    request: BuildRequest,
    *,
    language_name: str,
    luajit: str,
    workspace_root: Path | None = None,
    output_dir: Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> Path:
    """Dispatch a fully resolved request without sentinel ROM arguments."""
    request.validate()
    if output_dir is None:
        output_dir = request.output_dir
    if request.profile.id == "gs":
        from .gs_mod import build_gs

        return build_gs(
            request.source_for("gs"), request.source_for("crystal"), request.language, language_name, luajit,
            workspace_root=workspace_root, output_dir=output_dir,
            log_fn=log_fn, status_fn=status_fn, font_profile=request.font_profile,
        )
    if request.profile.id == "rby":
        from .builder import build

        return build(
            request.source_for("rb"), request.language,
            language_name, luajit, workspace_root=workspace_root, output_dir=output_dir,
            log_fn=log_fn, status_fn=status_fn, font_profile=request.font_profile,
            yellow_rom=request.source_for("yellow"),
        )
    raise ValueError(f"unsupported release profile: {request.profile.id!r}")
