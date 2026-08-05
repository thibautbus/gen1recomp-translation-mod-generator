"""Pinned, informational Gen1Recomp engine scope classification.

The classifier is deliberately pure: callsites are supplied by the caller and
the result depends only on the versioned scope manifest.  It is shared by the
coverage report and the private engine backlog so the two cannot drift.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "engine_scope.json"

_SCOPE_CATEGORIES = {"rby", "ui", "link", "import", "core", "modern", "unknown", "mixed"}
_SCOPE_ELIGIBILITIES = {"eligible", "review", "ineligible"}
_SCOPE_REASONS = {"modern", "diagnostic", "engine-fallback", "engine-contract-gap", "fallback-only", "covered-by-rom", "defensive", "dead"}


def load_scope(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("engine scope config must be an object")
    required = ("schema", "classifier_version", "gen1recomp_revision", "source_subdir", "rby_paths", "rby_ui_modules", "ui_review_modules", "link_modules", "modern_ui_modules", "rby_ui_keys", "link_ui_keys", "modern_ui_keys", "forced_dynamic_keys", "engine_dynamic_values", "key_scope_overrides")
    if set(data) != set(required):
        raise ValueError("engine scope config has unknown or missing fields")
    if data["schema"] != "gen1recomp-translation-mods/engine-scope" or data["classifier_version"] != 4:
        raise ValueError("unsupported engine scope schema/version")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"engine scope config missing fields: {', '.join(missing)}")
    if not isinstance(data["classifier_version"], int) or isinstance(data["classifier_version"], bool):
        raise ValueError("engine scope classifier_version must be an integer")
    if not isinstance(data["gen1recomp_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["gen1recomp_revision"]):
        raise ValueError("engine scope gen1recomp_revision must be a revision string")
    if data["source_subdir"] != "src":
        raise ValueError("engine scope source_subdir must be src")
    for key in ("rby_paths", "rby_ui_modules", "ui_review_modules", "link_modules", "modern_ui_modules", "rby_ui_keys", "link_ui_keys", "modern_ui_keys"):
        if not isinstance(data[key], list) or not all(isinstance(value, str) and value for value in data[key]):
            raise ValueError(f"engine scope {key} must be a list of strings")
        if len(data[key]) != len(set(data[key])):
            raise ValueError(f"engine scope {key} contains duplicates")
    for key in ("rby_ui_modules", "ui_review_modules", "link_modules", "modern_ui_modules"):
        if any(not value.endswith(".lua") or "/" in value or "\\" in value for value in data[key]):
            raise ValueError(f"engine scope {key} contains an invalid module")
    module_sets = [set(data[key]) for key in ("rby_ui_modules", "ui_review_modules", "link_modules", "modern_ui_modules")]
    if any(module_sets[i] & module_sets[j] for i in range(4) for j in range(i + 1, 4)):
        raise ValueError("engine scope module sets overlap")
    key_sets = [set(data[key]) for key in ("rby_ui_keys", "link_ui_keys", "modern_ui_keys")]
    if any(key_sets[i] & key_sets[j] for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("engine scope UI key sets overlap")
    forced = data["forced_dynamic_keys"]
    if not isinstance(forced, dict):
        raise ValueError("engine scope forced_dynamic_keys must be an object")
    configured_ui_keys = set().union(*(set(data[key]) for key in ("rby_ui_keys", "link_ui_keys", "modern_ui_keys")))
    if set(forced) & configured_ui_keys:
        raise ValueError("engine scope forced_dynamic_keys overlap configured UI keys")
    for key, value in forced.items():
        if not isinstance(key, str) or not key:
            raise ValueError("engine scope forced_dynamic_keys keys must be non-empty strings")
        if not isinstance(value, dict) or set(value) != {"category", "eligibility", "reason", "provenance", "callsite", "qid"}:
            raise ValueError(f"engine scope forced dynamic entry for {key!r} has unknown or missing fields")
        if value["category"] != "rby" or value["eligibility"] != "eligible":
            raise ValueError(f"engine scope forced dynamic entry for {key!r} must be eligible RBY")
        if value["reason"] not in _SCOPE_REASONS or value["provenance"] != "forced_dynamic":
            raise ValueError(f"engine scope forced dynamic entry for {key!r} has invalid provenance/reason")
        if not all(isinstance(value[field], str) and value[field] for field in ("callsite", "qid")):
            raise ValueError(f"engine scope forced dynamic entry for {key!r} requires callsite/qid")
    dynamic = data["engine_dynamic_values"]
    if not isinstance(dynamic, dict):
        raise ValueError("engine scope engine_dynamic_values must be an object")
    if set(dynamic) & configured_ui_keys:
        raise ValueError("engine scope engine_dynamic_values overlap configured UI keys")
    for key, value in dynamic.items():
        if not isinstance(key, str) or not key:
            raise ValueError("engine scope engine_dynamic_values keys must be non-empty strings")
        if not isinstance(value, dict) or set(value) != {"category", "eligibility", "reason", "provenance", "callsite", "qid"}:
            raise ValueError(f"engine scope engine_dynamic_values entry for {key!r} has unknown or missing fields")
        if value["category"] not in _SCOPE_CATEGORIES or value["eligibility"] not in _SCOPE_ELIGIBILITIES:
            raise ValueError(f"engine scope engine_dynamic_values entry for {key!r} has an invalid category/eligibility")
        if value["reason"] not in _SCOPE_REASONS or value["provenance"] != "engine_dynamic":
            raise ValueError(f"engine scope engine_dynamic_values entry for {key!r} has invalid provenance/reason")
        if not all(isinstance(value[field], str) and value[field] for field in ("callsite",)):
            raise ValueError(f"engine scope engine_dynamic_values entry for {key!r} requires callsite")
        if not isinstance(value.get("qid", ""), str):
            raise ValueError(f"engine scope engine_dynamic_values entry for {key!r} qid must be a string")
    overrides = data["key_scope_overrides"]
    if not isinstance(overrides, dict):
        raise ValueError("engine scope key_scope_overrides must be an object")
    if set(overrides) & configured_ui_keys:
        raise ValueError("engine scope key_scope_overrides overlap configured UI keys")
    for key, value in overrides.items():
        if not isinstance(key, str) or not key:
            raise ValueError("engine scope key_scope_overrides keys must be non-empty strings")
        if not isinstance(value, dict) or set(value) not in ({"category", "eligibility", "reason"}, {"category", "eligibility", "reason", "engine_empty"}):
            raise ValueError(f"engine scope override for {key!r} has unknown or missing fields")
        if not isinstance(value["category"], str) or value["category"] not in _SCOPE_CATEGORIES:
            raise ValueError(f"engine scope override for {key!r} has an invalid category")
        if not isinstance(value["eligibility"], str) or value["eligibility"] not in _SCOPE_ELIGIBILITIES:
            raise ValueError(f"engine scope override for {key!r} has an invalid eligibility")
        if not isinstance(value["reason"], str) or value["reason"] not in _SCOPE_REASONS:
            raise ValueError(f"engine scope override for {key!r} has an invalid reason")
        if "engine_empty" in value and (value["reason"] != "covered-by-rom" or value["engine_empty"] is not True):
            raise ValueError(f"engine scope override for {key!r} has an invalid engine_empty marker")
    if set(forced) & set(overrides):
        raise ValueError("engine scope forced_dynamic_keys overlap key_scope_overrides")
    return data


def forced_dynamic_keys(scope: Mapping[str, Any] | None = None) -> set[str]:
    return set((scope or load_scope()).get("forced_dynamic_keys", {}))


def engine_dynamic_values(scope: Mapping[str, Any] | None = None) -> set[str]:
    """Keys the literal callsite scanner cannot see (dynamic ``Strings``
    lookups) that are NOT RBY-eligible — e.g. option values returned by
    label functions (``SPEEDS[...]``, ``Performance.label``).  Unlike
    ``forced_dynamic_keys`` they may carry any category/eligibility.
    """
    return set((scope or load_scope()).get("engine_dynamic_values", {}))


def source_root(checkout: str | Path, scope: Mapping[str, Any] | None = None) -> Path:
    root = Path(checkout)
    scope = scope or load_scope()
    subdir = str(scope.get("source_subdir", "src"))
    # Accept either a checkout root or an already-selected src root.
    candidate = root / subdir
    if candidate.is_dir() and (root / ".git").exists():
        return candidate
    if root.name == subdir and root.is_dir() and ((root.parent / ".git").exists() or (root.parent / ".archive-marker.json").is_file()):
        return root
    raise ValueError(f"engine source must be checkout root containing {subdir}/ or that exact source directory: {root}")


def verified_source(checkout: str | Path, scope: Mapping[str, Any] | None = None) -> tuple[Path, Path, str]:
    scope = scope or load_scope()
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
        if _tree_digest(src) != expected_tree:
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
        dirty = subprocess.check_output(
            ["git", "-C", str(git_root), "status", "--porcelain", "--untracked-files=all", "--", str(scope["source_subdir"])],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"unable to inspect Gen1Recomp source status: {git_root}") from exc
    if dirty.strip():
        raise ValueError(f"Gen1Recomp production source is dirty: {git_root / scope['source_subdir']}")
    return src, git_root, revision


def iter_callsites(checkout: str | Path) -> list[dict[str, Any]]:
    """Collect production ``Strings`` callsites and rendered romText fallbacks."""
    # Imported lazily to keep this module independent of backlog analysis.
    from .engine_backlog import iter_literal_strings_callsites, iter_romtext_fallback_callsites
    src = source_root(checkout)
    return iter_literal_strings_callsites(src) + iter_romtext_fallback_callsites(src)


def _module(path: str) -> str:
    return Path(path).name


def classify_path(path: str, key: str | None = None, scope: Mapping[str, Any] | None = None) -> str:
    scope = scope or load_scope()
    parts = [part.casefold() for part in Path(path).parts]
    if parts and parts[0] == "src":
        parts = parts[1:]
    lowered = path.casefold()
    module = _module(path)
    if "link" in parts or "online" in lowered or "tournament" in lowered:
        return "link"
    if "import" in parts or "romimporter" in lowered:
        return "import"
    if "core" in parts:
        return "core"
    if "mods" in parts or any(token in lowered for token in ("modmanager", "discord", "updater")):
        return "modern"
    if parts and parts[0] == "ui":
        if module in set(scope.get("link_modules", ())):
            return "link"
        if module in set(scope.get("ui_review_modules", ())):
            return "ui"
        if module in set(scope.get("modern_ui_modules", ())):
            return "modern"
        # OptionsMenu/StartMenu rows not explicitly audited are modern.
        if module in {"OptionsMenu.lua", "StartMenu.lua"}:
            if key in set(scope.get("link_ui_keys", ())):
                return "link"
            if key in set(scope.get("rby_ui_keys", ())):
                return "rby"
            if key in set(scope.get("modern_ui_keys", ())):
                return "modern"
            return "modern"
        if module in set(scope.get("rby_ui_modules", ())):
            return "rby"
        return "ui"
    if parts and parts[0] in set(scope.get("rby_paths", ())):
        return "rby"
    return "unknown"


def classify_callsites(callsites: Iterable[Mapping[str, Any]], scope: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Classify each key using the audited any-RBY/no-link inclusion rule."""
    scope = scope or load_scope()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in callsites:
        key = str(item.get("source", ""))
        if not key:
            continue
        row = dict(item)
        row["category"] = classify_path(str(row.get("path", "")), key, scope)
        grouped.setdefault(key, []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(grouped):
        rows = sorted(grouped[key], key=lambda row: (str(row.get("path", "")), int(row.get("line", 0)), str(row.get("kind", ""))))
        categories = {str(row["category"]) for row in rows}
        has_rby, has_link = "rby" in categories, "link" in categories
        if has_rby and not has_link:
            eligibility = "eligible"
        elif has_link and has_rby:
            eligibility = "review"
        elif categories & {"ui", "unknown"}:
            eligibility = "review"
        else:
            eligibility = "ineligible"
        # Eligible keys with additional non-link rows remain in the RBY bucket;
        # mixed denotes an explicit audited cross-surface key (or link review).
        if eligibility == "eligible" and categories == {"rby"}:
            category = "rby"
        elif eligibility == "eligible" and categories - {"rby"}:
            category = "mixed"
        elif len(categories) == 1:
            category = next(iter(categories))
        else:
            category = "mixed"
        result[key] = {
            "category": category,
            "categories": sorted(categories),
            "eligibility": eligibility,
            "callsites": rows,
            "raw_callsites": list(rows),
            "raw_category": category,
            "raw_eligibility": eligibility,
        }
    for key, override in scope.get("key_scope_overrides", {}).items():
        if key not in result:
            continue
        result[key].update({
            "category": override["category"],
            "eligibility": override["eligibility"],
            "reason": override["reason"],
        })
        if "engine_empty" in override:
            result[key]["engine_empty"] = True
    for key, dynamic in scope.get("forced_dynamic_keys", {}).items():
        result.setdefault(key, {
            "category": dynamic["category"],
            "categories": [dynamic["category"]],
            "eligibility": dynamic["eligibility"],
            "callsites": [],
            "raw_callsites": [],
            "raw_category": "unknown",
            "raw_eligibility": "review",
        })
        result[key].update({
            "category": dynamic["category"],
            "eligibility": dynamic["eligibility"],
            "reason": dynamic["reason"],
            "provenance": dynamic["provenance"],
            "callsite": dynamic["callsite"],
            "qid": dynamic["qid"],
        })
    return result


def classify_catalog(keys: Iterable[str], callsites: Iterable[Mapping[str, Any]], scope: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    scope = scope or load_scope()
    result = classify_callsites(callsites, scope)
    for key in keys:
        result.setdefault(str(key), {"category": "unknown", "categories": [], "eligibility": "review", "callsites": [], "raw_callsites": [], "raw_category": "unknown", "raw_eligibility": "review"})
    for key in scope.get("forced_dynamic_keys", {}):
        result.setdefault(key, {"category": "unknown", "categories": [], "eligibility": "review", "callsites": [], "raw_callsites": [], "raw_category": "unknown", "raw_eligibility": "review"})
    for key, dynamic in scope.get("forced_dynamic_keys", {}).items():
        if key in result:
            result[key].update({
                "category": dynamic["category"],
                "categories": [dynamic["category"]],
                "eligibility": dynamic["eligibility"],
                "reason": dynamic["reason"],
                "provenance": dynamic["provenance"],
                "callsite": dynamic["callsite"],
                "qid": dynamic["qid"],
            })
    for key, dynamic in scope.get("engine_dynamic_values", {}).items():
        if key in result:
            result[key].update({
                "category": dynamic["category"],
                "categories": [dynamic["category"]],
                "eligibility": dynamic["eligibility"],
                "reason": dynamic["reason"],
                "provenance": dynamic["provenance"],
                "callsite": dynamic["callsite"],
                "qid": dynamic.get("qid", ""),
            })
    for key, override in scope.get("key_scope_overrides", {}).items():
        if key in result:
            result[key].update({"category": override["category"], "eligibility": override["eligibility"], "reason": override["reason"]})
            if "engine_empty" in override:
                result[key]["engine_empty"] = True
    return {key: result[key] for key in sorted(result)}


def validate_catalog_universe(catalog_keys: Iterable[str], checkout: str | Path, scope: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Assert that a production checkout and catalog describe the same keys.

    A catalog key missing from the source is a stale override (the engine no
    longer looks it up) and fails the check.  Source keys without a catalog
    entry are tolerated: they are either engine strings the mod leaves in
    English (fallback) or fragments of concatenated literals that Modkit's
    scaffold harvester joins into the full form.
    """
    catalog = {str(key) for key in catalog_keys}
    calls = iter_callsites(checkout)
    source = {str(row.get("source", "")) for row in calls if row.get("source")}
    scope = scope or load_scope()
    dynamic = forced_dynamic_keys(scope) | engine_dynamic_values(scope)
    source_with_dynamic = source | dynamic
    missing = sorted(catalog - source_with_dynamic)
    if missing:
        raise ValueError(f"engine catalog/source key universe mismatch (missing={len(missing)})")
    return {"catalog_total": len(catalog), "source_keys": len(source_with_dynamic), "callsites": len(calls), "forced_dynamic": len(forced_dynamic_keys(scope)), "engine_dynamic": len(engine_dynamic_values(scope))}


def coverage_metadata(scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    scope = scope or load_scope()
    return {"classifier_version": scope.get("classifier_version", 1), "source_revision": scope.get("gen1recomp_revision"), "source_subdir": scope.get("source_subdir", "src"), "scope_config": "config/engine_scope.json"}
