"""The engine's pinned string universe, shared by every release.

config/shared/engine_manifest.json pins a gen1recomp revision and the
complete set of Strings() keys it reaches: every literal callsite under src/
plus the keys the runtime builds (forced dynamic keys and engine dynamic
values).  Red/Blue classifies that universe (pipeline/rby/engine_scope.py),
Gold/Silver takes its Gen 2 subset (pipeline/gsc/engine.py).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "config" / "shared" / "engine_manifest.json"

_SCOPE_CATEGORIES = {"rby", "ui", "link", "import", "core", "modern", "gen2", "unknown", "mixed"}
_SCOPE_ELIGIBILITIES = {"eligible", "review", "ineligible"}
_SCOPE_REASONS = {"modern", "diagnostic", "engine-fallback", "engine-contract-gap", "fallback-only", "covered-by-rom", "defensive", "dead"}


def load_manifest(path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("engine manifest config must be an object")
    required = (
        "schema", "version", "gen1recomp_revision", "source_subdir",
        "forced_dynamic_keys", "engine_dynamic_values",
    )
    if set(data) != set(required):
        raise ValueError("engine manifest config has unknown or missing fields")
    if data["schema"] != "gen1recomp-translation-mods/engine-manifest" or data["version"] != 1:
        raise ValueError("unsupported engine manifest schema/version")
    if not isinstance(data["gen1recomp_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["gen1recomp_revision"]):
        raise ValueError("engine manifest gen1recomp_revision must be a revision string")
    if data["source_subdir"] != "src":
        raise ValueError("engine manifest source_subdir must be src")
    forced = data["forced_dynamic_keys"]
    if not isinstance(forced, dict):
        raise ValueError("engine manifest forced_dynamic_keys must be an object")
    for key, value in forced.items():
        if not isinstance(key, str) or not key:
            raise ValueError("engine manifest forced_dynamic_keys keys must be non-empty strings")
        if not isinstance(value, dict) or set(value) != {"category", "eligibility", "reason", "provenance", "callsite", "qid"}:
            raise ValueError(f"engine manifest forced dynamic entry for {key!r} has unknown or missing fields")
        if value["category"] != "rby" or value["eligibility"] != "eligible":
            raise ValueError(f"engine manifest forced dynamic entry for {key!r} must be eligible RBY")
        if value["reason"] not in _SCOPE_REASONS or value["provenance"] != "forced_dynamic":
            raise ValueError(f"engine manifest forced dynamic entry for {key!r} has invalid provenance/reason")
        if not all(isinstance(value[field], str) and value[field] for field in ("callsite", "qid")):
            raise ValueError(f"engine manifest forced dynamic entry for {key!r} requires callsite/qid")
    dynamic = data["engine_dynamic_values"]
    if not isinstance(dynamic, dict):
        raise ValueError("engine manifest engine_dynamic_values must be an object")
    for key, value in dynamic.items():
        if not isinstance(key, str) or not key:
            raise ValueError("engine manifest engine_dynamic_values keys must be non-empty strings")
        if not isinstance(value, dict) or set(value) != {"category", "eligibility", "reason", "provenance", "callsite", "qid"}:
            raise ValueError(f"engine manifest engine_dynamic_values entry for {key!r} has unknown or missing fields")
        if value["category"] not in _SCOPE_CATEGORIES or value["eligibility"] not in _SCOPE_ELIGIBILITIES:
            raise ValueError(f"engine manifest engine_dynamic_values entry for {key!r} has an invalid category/eligibility")
        if value["reason"] not in _SCOPE_REASONS or value["provenance"] != "engine_dynamic":
            raise ValueError(f"engine manifest engine_dynamic_values entry for {key!r} has invalid provenance/reason")
        if not isinstance(value["callsite"], str) or not value["callsite"]:
            raise ValueError(f"engine manifest engine_dynamic_values entry for {key!r} requires callsite")
        if not isinstance(value["qid"], str):
            raise ValueError(f"engine manifest engine_dynamic_values entry for {key!r} qid must be a string")
    if set(forced) & set(dynamic):
        raise ValueError("engine manifest dynamic key sets overlap")
    return data


def forced_dynamic_keys(scope: Mapping[str, Any] | None = None) -> set[str]:
    return set((scope or load_manifest()).get("forced_dynamic_keys", {}))


def engine_dynamic_values(scope: Mapping[str, Any] | None = None) -> set[str]:
    """Keys the literal callsite scanner cannot see (dynamic ``Strings``
    lookups) that are NOT RBY-eligible — e.g. option values returned by
    label functions (``SPEEDS[...]``, ``Performance.label``).  Unlike
    ``forced_dynamic_keys`` they may carry any category/eligibility.
    """
    return set((scope or load_manifest()).get("engine_dynamic_values", {}))


def complete_engine_keys(
    callsites: Iterable[Mapping[str, Any]], scope: Mapping[str, Any] | None = None,
) -> set[str]:
    """Return the revision-pinned engine-wide translation universe."""
    scope = scope or load_manifest()
    source_keys = {
        str(row["source"]) for row in callsites
        if isinstance(row.get("source"), str) and row["source"]
    }
    return source_keys | forced_dynamic_keys(scope) | engine_dynamic_values(scope)


def source_root(checkout: str | Path, scope: Mapping[str, Any] | None = None) -> Path:
    root = Path(checkout)
    scope = scope or load_manifest()
    subdir = str(scope.get("source_subdir", "src"))
    # Accept either a checkout root or an already-selected src root.
    candidate = root / subdir
    if candidate.is_dir() and (
        (root / ".git").exists() or (root / ".archive-marker.json").is_file()
    ):
        return candidate
    if root.name == subdir and root.is_dir() and ((root.parent / ".git").exists() or (root.parent / ".archive-marker.json").is_file()):
        return root
    raise ValueError(f"engine source must be checkout root containing {subdir}/ or that exact source directory: {root}")


def verified_source(checkout: str | Path, scope: Mapping[str, Any] | None = None) -> tuple[Path, Path, str]:
    scope = scope or load_manifest()
    root = Path(checkout)
    archive_root = root.parent if root.name == str(scope.get("source_subdir", "src")) and (root.parent / ".archive-marker.json").is_file() else root
    marker = archive_root / ".archive-marker.json"
    if marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"unable to read archive marker: {marker}") from exc
        revision = metadata.get("revision")
        if revision != scope["gen1recomp_revision"]:
            raise ValueError(f"Gen1Recomp revision mismatch: expected {scope['gen1recomp_revision']}, got {revision}")
        archive_hash = str(metadata.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", archive_hash):
            raise ValueError("Gen1Recomp archive marker has an invalid SHA-256 pin")
        if not str(metadata.get("url", "")).startswith("https://"):
            raise ValueError("Gen1Recomp archive marker URL is not HTTPS")
        try:
            from .project import project_config
            engine_cfg = project_config()["gen1recomp"]
            expected_tree = str(engine_cfg["archive_tree_sha256"])
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError("missing trusted Gen1Recomp archive tree pin") from exc
        if metadata.get("tree_sha256") != expected_tree:
            raise ValueError("Gen1Recomp archive source tree digest mismatch")
        if metadata.get("url") != engine_cfg.get("archive_url") or metadata.get("sha256") != engine_cfg.get("archive_sha256"):
            raise ValueError("Gen1Recomp archive marker does not match configured archive pin")
        src = archive_root / str(scope.get("source_subdir", "src"))
        if not src.is_dir():
            raise ValueError(f"engine archive has no {scope.get('source_subdir', 'src')}/ directory: {root}")
        from .dependencies import _tree_digest
        configured_prefixes = metadata.get("immutable_prefixes")
        if configured_prefixes is None:
            # Markers written before immutable-prefix metadata was added used
            # the selected source subtree as their trust boundary.
            immutable_prefixes = (str(scope.get("source_subdir", "src")),)
        elif (not isinstance(configured_prefixes, list) or
              not all(isinstance(prefix, str) and prefix for prefix in configured_prefixes)):
            raise ValueError("Gen1Recomp archive marker has invalid immutable prefixes")
        else:
            immutable_prefixes = tuple(configured_prefixes)
        if _tree_digest(archive_root, immutable_prefixes) != expected_tree:
            raise ValueError("Gen1Recomp archive source tree was modified")
        return src, archive_root, revision
    src = source_root(checkout, scope)
    git_root = src.parent
    try:
        revision = subprocess.check_output(["git", "-C", str(git_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"unable to verify Gen1Recomp git checkout: {git_root}") from exc
    if revision != scope["gen1recomp_revision"]:
        raise ValueError(f"Gen1Recomp revision mismatch: expected {scope['gen1recomp_revision']}, got {revision}")
    try:
        integrity_paths = [str(scope["source_subdir"]), "tools"]
        dirty = subprocess.check_output(
            ["git", "-C", str(git_root), "status", "--porcelain", "--untracked-files=all", "--", *integrity_paths],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"unable to inspect Gen1Recomp source status: {git_root}") from exc
    if dirty.strip():
        raise ValueError(f"Gen1Recomp production source is dirty: {git_root / scope['source_subdir']}")
    return src, git_root, revision


def iter_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Collect production literal ``Strings`` and all literal RomText fallbacks."""
    # Imported lazily to keep this module independent of backlog analysis.
    from .strings_harvest import iter_literal_strings_callsites, iter_romtext_fallback_callsites
    src = source_root(checkout)
    return iter_literal_strings_callsites(src) + iter_romtext_fallback_callsites(src)


def is_gen2_path(path: str) -> bool:
    """Return whether a production callsite belongs to the Gold/Gen 2 scope.

    Link, import, core and modern surfaces keep their engine-wide category even
    when their implementation lives below a ``gen2`` directory.  These checks
    mirror the category precedence in :func:`classify_path` without loading the
    RBY classification policy.
    """
    parts = [part.casefold() for part in Path(path).parts]
    if parts and parts[0] == "src":
        parts = parts[1:]
    lowered = path.casefold()
    if "link" in parts or "online" in lowered or "tournament" in lowered:
        return False
    if "import" in parts or "romimporter" in lowered or "core" in parts:
        return False
    if "mods" in parts or any(token in lowered for token in ("modmanager", "discord", "updater")):
        return False
    return "gen2" in parts
