"""Match GoldSilver corpus rows to versioned Gen1Recomp engine strings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .engine import load_engine_overrides, match_engine_catalog
from .engine_scope import (
    complete_engine_keys, is_gen2_path, iter_callsites, load_manifest,
    verified_source,
)
from .model import CorpusRecord
from .engine_profile import PINNED_PROFILE, UPSTREAM_PROFILE, checkout_revision, normalize_engine_profile

GS_ENGINE_SCOPE_EXCLUSIONS_SCHEMA = "gen1recomp-translation-mods/gs-engine-scope-exclusions"
GS_ENGINE_FALLBACK_REPORT_SCHEMA = "gen1recomp-translation-mods/engine-fallback-report"


def load_gs_engine_fallbacks(
    language: str | None = None, path: str | Path | None = None,
) -> dict[str, dict]:
    """Load the audited English fallbacks kept outside runtime overrides."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gsc" / "engine_fallbacks.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
            or data.get("schema") != GS_ENGINE_FALLBACK_REPORT_SCHEMA
            or data.get("version") != 1
            or not isinstance(data.get("languages"), dict)):
        raise ValueError("unsupported Gen 2 engine fallback report")
    selected = data["languages"] if language is None else {language: data["languages"].get(language)}
    if language is not None and selected[language] is None:
        raise ValueError(f"engine fallback report has no language {language!r}")
    result: dict[str, dict] = {}
    for lang, section in selected.items():
        if not isinstance(section, dict) or not isinstance(section.get("entries"), dict):
            raise ValueError(f"invalid engine fallback report section for {lang!r}")
        entries = section["entries"]
        if section.get("total") != len(entries):
            raise ValueError(f"engine fallback report count mismatch for {lang!r}")
        for source, row in entries.items():
            if (not isinstance(source, str) or not isinstance(row, dict)
                    or row.get("reason") != "engine-fallback"
                    or row.get("override") != source
                    or not isinstance(row.get("provenance"), str)
                    or "Explicit English fallback" not in row["provenance"]):
                raise ValueError(f"invalid English fallback entry {lang!r}/{source!r}")
        result[lang] = entries
    return result


def load_gs_engine_scope_exclusions(path: str | Path | None = None) -> set[str]:
    """Load the Strings() keys reachable only from a Crystal-only feature.

    gen1recomp's Gen 2 UI code bundles some pokecrystal-only content
    (MoveTutor, GenderSelect, the "PokeSeer"/Buena radio special, Battle
    Tower -- see config/gsc/engine_scope_exclusions.json's own entries for
    the exact source .asm each key traces to) under ``ui/gen2``/
    ``script/gen2`` alongside real Gold/Silver code. None of it exists on a
    real Gold or Silver cart, so it has no PokeCorpus row and would need
    invented text to "translate" -- these keys are excluded from the
    Gold/Silver-related engine-string scope instead of counted against it.
    """
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gsc" / "engine_scope_exclusions.json"
    path = Path(path)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Gold/Silver engine scope exclusions JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != GS_ENGINE_SCOPE_EXCLUSIONS_SCHEMA:
        raise ValueError("unsupported Gold/Silver engine scope exclusions schema")
    if data.get("version") != 2 or not isinstance(data.get("excluded_keys"), dict):
        raise ValueError("Gold/Silver engine scope exclusions require version 2 excluded_keys")
    expected_revision = load_manifest()["gen1recomp_revision"]
    if data.get("source_revision") != expected_revision:
        raise ValueError(
            "Gold/Silver engine scope exclusions source_revision does not match "
            "the pinned Gen1Recomp manifest"
        )
    result: set[str] = set()
    for key, row in data["excluded_keys"].items():
        if (not isinstance(row, dict) or not isinstance(row.get("reason"), str) or
                not row["reason"].strip() or not isinstance(row.get("source"), str) or
                not row["source"].strip()):
            raise ValueError(f"invalid Gold/Silver engine scope exclusion for {key!r}")
        result.add(key)
    return result


def engine_string_keys(
    callsites: Iterable[Mapping[str, Any]], manifest: Mapping[str, Any] | None = None,
    exclusions: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Return the full Strings catalog and its Gen-2 callsite subset.

    ``exclusions`` (default: load_gs_engine_scope_exclusions()) drops keys
    reachable only from a Crystal-only feature out of the Gen-2 subset --
    they stay in the full catalog, since they are real Strings() callsites
    a Gold/Silver-mod override could still target, just not counted toward
    the Gold/Silver-related coverage metric.
    """
    manifest = manifest or load_manifest()
    exclusions = load_gs_engine_scope_exclusions() if exclusions is None else exclusions
    gen2_keys: set[str] = set()
    rows = list(callsites)
    for callsite in rows:
        if callsite.get("kind") not in {"call", "source"}:
            continue
        source = callsite.get("source")
        path = callsite.get("path")
        if not isinstance(source, str) or not source or not isinstance(path, str):
            continue
        if is_gen2_path(path):
            gen2_keys.add(source)
    gen2_keys -= exclusions
    return complete_engine_keys(rows, manifest), gen2_keys


def _corpus_records(
    corpus_rows: Iterable[tuple[str, str, str]], target_lang: str,
) -> list[CorpusRecord]:
    records: list[CorpusRecord] = []
    for qid, english, target in corpus_rows:
        records.append(CorpusRecord(qid, "en", english, "gold"))
        records.append(CorpusRecord(qid, target_lang, target, "gold", english=english))
    return records


def match_gs_engine_strings(
    corpus_rows: Iterable[tuple[str, str, str]],
    gen1recomp: str | Path,
    target_lang: str,
    engine_profile: str = PINNED_PROFILE,
) -> tuple[dict[str, str], dict[str, dict]]:
    """Match the selected engine catalog without mixing compatibility layers."""
    profile = normalize_engine_profile(engine_profile)
    manifest = load_manifest()
    if profile == UPSTREAM_PROFILE:
        source = Path(gen1recomp).resolve() / "src"
        if not source.is_dir():
            raise ValueError(f"upstream engine source has no src directory: {gen1recomp}")
        revision = checkout_revision(gen1recomp)
    else:
        source, _root, revision = verified_source(gen1recomp, manifest)
    callsites = iter_callsites(source)
    all_keys, gen2_keys = engine_string_keys(callsites, manifest)
    root = Path(__file__).resolve().parents[1]
    overrides = load_engine_overrides(
        root / "overrides" / target_lang / "gsc" / "engine.json",
    )
    fallback_entries = load_gs_engine_fallbacks(target_lang)[target_lang]
    overlap = sorted(set(overrides) & set(fallback_entries))
    if overlap:
        raise ValueError(
            "Gold engine runtime overrides re-emit fallback entries: "
            f"{overlap!r}"
        )
    stale_overrides = sorted(set(overrides) - all_keys)
    if stale_overrides:
        raise ValueError(
            f"Gold engine overrides contain {len(stale_overrides)} unknown key(s): "
            f"{stale_overrides!r}"
        )
    # engine_fallbacks.json is audited against the upstream-local engine (the
    # branch that will eventually become the next pin), so a literal it
    # tracks may not exist in the CURRENTLY published pin yet -- checking it
    # against the pinned profile's all_keys would flag every not-yet-released
    # literal as stale. Only the upstream profile's all_keys is the right
    # superset to verify the ledger against.
    if profile == UPSTREAM_PROFILE:
        stale_fallbacks = sorted(set(fallback_entries) - all_keys)
        if stale_fallbacks:
            raise ValueError(
                f"Gold engine fallback report contains {len(stale_fallbacks)} unknown key(s): "
                f"{stale_fallbacks!r}"
            )
    values, report = match_engine_catalog(
        sorted(all_keys),
        _corpus_records(corpus_rows, target_lang),
        overrides=overrides,
        semantic_anchors=root / "config" / "gsc" / "semantic_anchors.json",
        target_lang=target_lang,
    )
    # ``match_engine_catalog`` deliberately reports an exact corpus match even
    # when the localized value is byte-for-byte identical to the English
    # source.  That is useful for coverage accounting and audit provenance,
    # but an identity row is a runtime no-op and must not be regenerated in
    # the shipped catalogue after it has been removed from the overrides.
    # Keep the matcher output (and therefore its coverage counters/details)
    # intact, filtering only at this final runtime-catalogue boundary.
    translated = {key for key, value in values.items() if value}
    shipped = {
        key: value for key, value in values.items()
        if value and value != key
    }
    report.update({
        "source_revision": revision,
        "engine_profile": profile,
        "fallback_english": len(fallback_entries),
        "catalog_kind": "Strings and Strings.source callsites",
    })
    gen2_translated = len(translated & gen2_keys)
    gen2_report = {
        "source_revision": revision,
        "engine_profile": profile,
        "scope": "at least one callsite in a gen2 source subtree",
        "translated": gen2_translated,
        "total": len(gen2_keys),
        "percent": round(100.0 * gen2_translated / len(gen2_keys), 2) if gen2_keys else 100.0,
    }
    return shipped, {"engine": report, "engine_gen2": gen2_report}
