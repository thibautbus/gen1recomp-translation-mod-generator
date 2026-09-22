"""RBY engine scope classification.

The classifier is deliberately pure: callsites are supplied by the caller and
the result depends only on the versioned RBY scope.  It is shared by the
coverage report and the private engine backlog so the two cannot drift.  The
pinned manifest it classifies lives in pipeline/shared/engine_manifest.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..shared.engine_manifest import (
    MANIFEST_PATH,
    ROOT,
    _SCOPE_CATEGORIES,
    _SCOPE_ELIGIBILITIES,
    _SCOPE_REASONS,
    complete_engine_keys,
    engine_dynamic_values,
    forced_dynamic_keys,
    is_gen2_path,
    iter_callsites,
    load_manifest,
)

SCOPE_PATH = ROOT / "config" / "rby" / "engine_scope.json"


def load_scope(
    path: str | Path = SCOPE_PATH,
    manifest_path: str | Path = MANIFEST_PATH,
) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("RBY engine scope config must be an object")
    required = (
        "schema", "classifier_version", "rby_paths", "rby_ui_modules",
        "ui_review_modules", "link_modules", "modern_ui_modules", "rby_ui_keys",
        "link_ui_keys", "modern_ui_keys", "key_scope_overrides",
    )
    if set(data) != set(required):
        raise ValueError("RBY engine scope config has unknown or missing fields")
    if data["schema"] != "gen1recomp-translation-mods/rby-engine-scope" or data["classifier_version"] != 4:
        raise ValueError("unsupported RBY engine scope schema/version")
    if not isinstance(data["classifier_version"], int) or isinstance(data["classifier_version"], bool):
        raise ValueError("RBY engine scope classifier_version must be an integer")
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
    manifest = load_manifest(manifest_path)
    forced = manifest["forced_dynamic_keys"]
    configured_ui_keys = set().union(*(set(data[key]) for key in ("rby_ui_keys", "link_ui_keys", "modern_ui_keys")))
    if set(forced) & configured_ui_keys:
        raise ValueError("engine scope forced_dynamic_keys overlap configured UI keys")
    dynamic = manifest["engine_dynamic_values"]
    if set(dynamic) & configured_ui_keys:
        raise ValueError("engine scope engine_dynamic_values overlap configured UI keys")
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
    return {**manifest, **data}



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
    if is_gen2_path(path):
        # Gen 2 subtrees (src/{battle,world,script,ui}/gen2/...) reuse RBY
        # top-level directory names and, under ui/, RBY module basenames
        # (BoxMenu.lua, PartyMenu.lua...). Both the rby_paths first-segment
        # check and the ui module-name check below are basename/prefix
        # matches that do not see the gen2 segment, so without this early
        # return a Gen 2 callsite is silently folded into "rby"/"eligible".
        return "gen2"
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
    scope = scope or load_scope()
    source_with_dynamic = complete_engine_keys(calls, scope)
    missing = sorted(catalog - source_with_dynamic)
    if missing:
        raise ValueError(f"engine catalog/source key universe mismatch (missing={len(missing)})")
    return {"catalog_total": len(catalog), "source_keys": len(source_with_dynamic), "callsites": len(calls), "forced_dynamic": len(forced_dynamic_keys(scope)), "engine_dynamic": len(engine_dynamic_values(scope))}


def coverage_metadata(scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    scope = scope or load_scope()
    return {
        "classifier_version": scope.get("classifier_version", 1),
        "source_revision": scope.get("gen1recomp_revision"),
        "source_subdir": scope.get("source_subdir", "src"),
        "engine_manifest": "config/shared/engine_manifest.json",
        "scope_config": "config/rby/engine_scope.json",
    }
