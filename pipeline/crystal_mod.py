"""Join Pokémon Crystal's extracted dialogue against poke-corpus.

Crystal shares Gold/Silver's `tools/gs_extract.lua` extractor contract (the
extractor's own edition branches, and gen1recomp's `RomExtractorGen2.lua`
underneath it, already cover "crystal") and its normalized-English-text join
strategy (`pipeline.gs_join.join_gs_pointers` matches by text, not by
pointer, so it is edition-agnostic despite its "gs_" name) -- but Crystal's
own `bank:address` pointers diverge substantially from Gold/Silver's (95.8%
of shared symbol names have a different address, measured directly against
real ROMs), so it needs its own corpus join against poke-corpus's separate
`Crystal/` collection rather than reusing Gold/Silver's own resolved
catalog. Crystal's qid namespace (`c.*`) mirrors GoldSilver's own rich
per-context taxonomy (e.g. `c.common_2.BouldersMayMoveText`,
`c.RadioTower3F.GruntM7BeatenText`) -- same per-map/per-context structure,
same trailing symbol names, just a `c.` prefix instead of `gs.`, not a flat
namespace and no overlap with `gs.*` itself.

Crystal ships as a mandatory companion catalog merged into the same mod as
Gold/Silver (see pipeline/gs_mod.py's build_gs()/generate_gs_mod()), applied
at runtime only when GameVersion.get() == "crystal" -- the same pattern
pipeline/mod.py's RBY build uses for Yellow's own dialogue_yellow.lua layer,
just without a large pointer-identical "free" majority to lean on (unlike
Yellow vs Red/Blue).

Besides dialogue pointers, the module builds the Crystal-only named records,
RomText labels and Strings keys that do not exist in Gold/Silver.  Every
value remains corpus-backed and is emitted through a Crystal-edition guard;
missing localized corpus data deliberately keeps the engine's English value.
"""
from __future__ import annotations

import json
from pathlib import Path

from .crystal_registries import crystal_registry_catalogs
from .crystal_strings import match_crystal_engine_strings
from .engine import match_engine_catalog
from .gs_engine import _corpus_records
from .gs_join import GsJoinEntry, join_gs_pointers, read_corpus_rows
from .gs_text import GS_POINTER_RE, parse_gs_text_catalog, split_lines
from .engine_profile import UPSTREAM_PROFILE, normalize_engine_profile, profile_for

CRYSTAL_POINTER_DECISIONS_SCHEMA = "gen1recomp-translation-mods/crystal-pointer-decisions"
_POINTER = GS_POINTER_RE


def load_crystal_pointer_decisions(path: str | Path | None = None) -> dict[str, str]:
    """Load reviewed Crystal pointer-to-corpus-QID decisions.

    Mirrors pipeline.gs_join.load_gs_pointer_decisions() exactly, except the
    qid prefix it validates is "c." (Crystal's own corpus namespace) instead
    of "gs.". Kept as a separate function, not a shared one with a
    configurable prefix, so a stray Gold-side edit can't accidentally start
    accepting Crystal qids or vice versa.

    Decisions select corpus rows rather than carrying translated prose, so
    one review applies consistently to every target language. Every entry
    here was mechanically derived (not hand-picked one by one), from two
    sources:

    - A first pass reused Gold's own already-reviewed
      config/gsc/pointer_decisions.json: when a Crystal pointer's ambiguous
      candidates included exactly one qid whose "gs."-prefixed counterpart
      Gold's reviewers already picked as the real, reachable text over its
      sibling candidates (typically a std_text.Unused* duplicate), the same
      suffix was picked here too -- Crystal reuses the same per-location qid
      taxonomy, so a candidate Gold's own review never needed for a real
      pointer is reliably dead/unreachable text in Crystal as well.
    - A second pass resolved the rest against ground truth: the
      pokecrystal disassembly (https://github.com/pret/pokecrystal) builds
      byte-for-byte identical to the real retail ROM (verified: both hash to
      f4cd194bdee0d04ca4eac29e09b8e4e9d818c133), so its own linker-generated
      .sym file is an authoritative bank:address -> real ASM symbol name
      map. For each still-ambiguous pointer, the candidate whose qid
      suffix (stripped of a leading "_", pokecrystal's own local-label
      convention) matches that pointer's one real symbol is correct by
      construction, not inferred from corpus content -- this is not
      building or shipping pokecrystal, just consulting its own linker
      output once to generate this static, checked-in decision list.

    Together these resolved every one of the 113 pointers that were
    genuinely ambiguous (two-plus candidate qids, non-identical
    translations) as of the corpus/engine revisions this file was last
    regenerated against; a future corpus update could introduce new
    ambiguous pointers this file doesn't cover yet, which join_gs_pointers()
    reports as UNRESOLVED same as before this file existed.
    """
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gsc" / "crystal_pointer_decisions.json"
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Crystal pointer decisions JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != CRYSTAL_POINTER_DECISIONS_SCHEMA:
        raise ValueError("unsupported Crystal pointer decisions schema")
    if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
        raise ValueError("Crystal pointer decisions require version 1 entries")
    result: dict[str, str] = {}
    for pointer, row in data["entries"].items():
        if not _POINTER.fullmatch(pointer):
            raise ValueError(f"invalid Crystal pointer decision key: {pointer!r}")
        if (not isinstance(row, dict) or set(row) != {"qid", "symbol"} or
                not isinstance(row.get("qid"), str) or not row["qid"].startswith("c.") or
                not isinstance(row.get("symbol"), str) or not row["symbol"]):
            raise ValueError(f"invalid Crystal pointer decision for {pointer!r}")
        result[pointer] = row["qid"]
    return result


CRYSTAL_DIALOGUE_OVERRIDES_SCHEMA = "gen1recomp-translation-mods/crystal-dialogue-overrides"


def load_crystal_dialogue_overrides(language: str, path: str | Path | None = None) -> dict[str, str]:
    """Load per-language, per-pointer Crystal dialogue overrides.

    Unlike load_crystal_pointer_decisions() (language-independent qid
    picks), this carries actual translated prose for pointers with no
    PokeCorpus row at all -- e.g. the Mobile Adapter GB / PokeCom Center
    content, a peripheral never sold outside Japan and (confirmed against a
    real pokecrystal build) never localized, so no corpus match could ever
    exist for it. Each entry's ``reason``/``provenance`` fields document why
    (this function only returns the flat {pointer: override} shape
    join_gs_pointers()'s own ``overrides`` parameter expects; the richer
    metadata is for human readers of the checked-in JSON, not consumed
    here). Missing file (a language with no such overrides yet) returns {}.
    """
    if path is None:
        path = (
            Path(__file__).resolve().parents[1] / "overrides" / language / "gsc" / "crystal_dialogue.json"
        )
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Crystal dialogue overrides JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != CRYSTAL_DIALOGUE_OVERRIDES_SCHEMA:
        raise ValueError("unsupported Crystal dialogue overrides schema")
    if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
        raise ValueError("Crystal dialogue overrides require version 1 entries")
    result: dict[str, str] = {}
    for pointer, row in data["entries"].items():
        if not _POINTER.fullmatch(pointer):
            raise ValueError(f"invalid Crystal dialogue override key: {pointer!r}")
        if (not isinstance(row, dict) or not isinstance(row.get("override"), str) or
                not row["override"].strip() or not isinstance(row.get("reason"), str) or
                not row["reason"].strip() or not isinstance(row.get("provenance"), str) or
                not row["provenance"].strip()):
            raise ValueError(f"invalid Crystal dialogue override for {pointer!r}")
        result[pointer] = row["override"]
    return result


def join_crystal_dialogue(
    crystal_out_dir: str | Path, corpus_dir: str | Path, language: str,
) -> tuple[list[GsJoinEntry], dict]:
    """Join Crystal's extracted dialogue TSVs against poke-corpus's Crystal/ collection.

    Returns entries and their raw join stats (see pipeline.gs_join.join_gs_pointers).
    If poke-corpus has no Crystal corpus for ``language`` (Korean: Crystal has
    no ko_msg.txt, unlike GoldSilver), there are no corpus rows to join
    against -- Crystal dialogue simply stays in English for that language,
    same as before -- but a hand-composed overrides/<language>/gsc/
    crystal_dialogue.json entry (this catalog's own escape hatch for prose
    with no corpus row at all, see load_crystal_dialogue_overrides()) is
    still applied per pointer; join_gs_pointers() already treats overrides
    as the higher-priority path regardless of what corpus rows are given,
    so an empty corpus row list does not need its own early return here.
    ``qid_decisions`` is skipped in that case: it only disambiguates among
    a language's own corpus candidates, so it would just fail every lookup
    against an empty corpus_by_qid instead of silently doing nothing.
    The caller (build_gs()) still requires and extracts the Crystal ROM
    (for the shared engine-string catalog and consistency with the
    mandatory-Crystal-ROM policy) regardless.
    """
    crystal_out_dir = Path(crystal_out_dir)
    corpus_dir = Path(corpus_dir)
    records = parse_gs_text_catalog(
        crystal_out_dir / "gs_text.tsv", crystal_out_dir / "gs_labels.tsv",
    )
    has_corpus = (corpus_dir / f"{language}_msg.txt").is_file()
    corpus_rows = read_corpus_rows(corpus_dir, target_lang=language) if has_corpus else []
    return join_gs_pointers(
        records, corpus_rows,
        overrides=load_crystal_dialogue_overrides(language),
        qid_decisions=load_crystal_pointer_decisions() if has_corpus else None,
    )


def crystal_text_catalog_from_join(entries: list[GsJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved."""
    return {entry.pointer: entry.translation for entry in entries if entry.translation}


CRYSTAL_ROM_TEXT_ANCHORS_SCHEMA = "gen1recomp-translation-mods/crystal-rom-text-anchors"


def parse_rom_text_catalog(path: str | Path) -> dict[str, str]:
    """Parse ``label<TAB>escaped text`` emitted by ``tools/gs_extract.lua``."""
    result: dict[str, str] = {}
    for line in split_lines(Path(path).read_text(encoding="utf-8")):
        if not line:
            continue
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[0] or fields[0] in result:
            raise ValueError(f"malformed Crystal ROM-text catalog row: {line!r}")
        escaped = fields[1]
        out: list[str] = []
        index = 0
        while index < len(escaped):
            if escaped[index] != "\\":
                out.append(escaped[index])
                index += 1
                continue
            if index + 1 >= len(escaped) or escaped[index + 1] not in {"\\", "n", "r", "t"}:
                raise ValueError(f"invalid escape in Crystal ROM-text row for {fields[0]!r}")
            out.append({"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}[escaped[index + 1]])
            index += 2
        result[fields[0]] = "".join(out)
    return result


def load_crystal_rom_text_anchors(path: str | Path | None = None) -> dict[str, str]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "gsc" / "crystal_rom_text_anchors.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != CRYSTAL_ROM_TEXT_ANCHORS_SCHEMA or data.get("version") != 1:
        raise ValueError("unsupported Crystal ROM-text anchors schema")
    labels = data.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise ValueError("Crystal ROM-text anchors require a non-empty labels object")
    for label, qid in labels.items():
        if (not isinstance(label, str) or not label.startswith("_")
                or not isinstance(qid, str) or not qid.startswith("c.")):
            raise ValueError(f"invalid Crystal ROM-text anchor for {label!r}")
    return labels


def join_crystal_rom_text(
    crystal_out_dir: str | Path, corpus_dir: str | Path, language: str,
    corpus_rows: list[tuple[str, str, str]] | None = None,
) -> tuple[dict[str, str], dict]:
    """Translate the seven Crystal ``RomText`` labels missed by script walk.

    ``corpus_rows``, when given, is read_corpus_rows()'s own output for this
    corpus_dir/language, same as crystal_registry_catalogs() above.
    """
    labels = load_crystal_rom_text_anchors()
    extracted = parse_rom_text_catalog(Path(crystal_out_dir) / "gs_rom_text.tsv")
    missing_labels = sorted(set(labels) - set(extracted))
    if missing_labels:
        raise ValueError("Crystal ROM-text extraction is incomplete: " + ", ".join(missing_labels))
    total = len(labels)
    corpus_dir = Path(corpus_dir)
    if corpus_rows is not None:
        rows = corpus_rows
    elif not (corpus_dir / f"{language}_msg.txt").is_file():
        return {}, {
            "translated": 0, "total": total, "percent": 0.0,
            "fallback_english": total, "unmatched": sorted(labels),
            "policy": "english-fallback",
        }
    else:
        rows = read_corpus_rows(corpus_dir, target_lang=language)
    source_to_label = {extracted[label]: label for label in labels}
    if len(source_to_label) != total:
        raise ValueError("Crystal ROM-text labels do not have unique extracted source strings")
    anchors = {
        extracted[label]: {"qid": qid, "extraction": {"kind": "full"}}
        for label, qid in labels.items()
    }
    values, report = match_engine_catalog(
        sorted(source_to_label), _corpus_records(rows, language),
        semantic_anchors=anchors, target_lang=language,
    )
    catalog = {
        source_to_label[source]: value for source, value in values.items() if value
    }
    report["translated"] = len(catalog)
    report["fallback_english"] = total - len(catalog)
    report["percent"] = round(100.0 * len(catalog) / total, 2) if total else 100.0
    report["policy"] = "english-fallback"
    return catalog, report


def crystal_feature_catalogs(
    crystal_out_dir: str | Path, corpus_dir: str | Path, language: str,
    engine_profile: str = UPSTREAM_PROFILE,
) -> tuple[dict[str, dict[str, str]], dict]:
    """Build every Crystal-only non-dialogue catalog and structured metrics."""
    profile = normalize_engine_profile(engine_profile)
    corpus_dir = Path(corpus_dir)
    # Read once and hand the same rows to every joiner below instead of each
    # of the three re-opening and re-parsing qid/en/<lang>_msg.txt on its own
    # (a language absent from the Crystal collection, currently Korean,
    # keeps degrading to English -- an absent corpus_rows still lets each
    # joiner take its own missing-file fallback path).
    corpus_rows = (
        read_corpus_rows(corpus_dir, target_lang=language)
        if (corpus_dir / f"{language}_msg.txt").is_file() else None
    )
    # The v0.2.41 runtime can consume the ordinary named-record and Strings
    # overlays on both profiles; only RomText needs the upstream `rom_text`
    # registry (the public `text` registry is routed to gen2Text and cannot
    # reach data.text on v0.2.41), so it alone is reported beside the
    # aggregate rather than folded into it, and stays a disabled placeholder
    # under the pinned profile.
    named, named_stats = crystal_registry_catalogs(crystal_out_dir, corpus_dir, language, corpus_rows)
    strings, engine_crystal_stats = match_crystal_engine_strings(corpus_dir, language, corpus_rows)
    translated = sum(
        section["translated"] for section in (*named_stats.values(), engine_crystal_stats)
    )
    total = sum(
        section["total"] for section in (*named_stats.values(), engine_crystal_stats)
    )
    aggregate = {
        "translated": translated,
        "total": total,
        "percent": round(100.0 * translated / total, 2) if total else 100.0,
        "policy": "english-fallback",
    }
    if profile_for(profile).supports_rom_text:
        rom_text, rom_text_stats = join_crystal_rom_text(crystal_out_dir, corpus_dir, language, corpus_rows)
        catalogs = {**named, "rom_text": rom_text, "strings": strings}
        rom_text_report = {
            **rom_text_stats,
            "runtime_dependency": "mod.content.rom_text",
            "scope": "upstream-dependent; excluded from aggregate",
        }
    else:
        catalogs = {**named, "strings": strings}
        rom_text_report = {
            "translated": 0, "total": 0, "fallback_english": 0,
            "runtime_dependency": "not available in v0.2.41",
            "scope": "upstream-dependent; disabled by pinned profile",
        }
    return catalogs, {
        "named_registries": named_stats,
        "rom_text": rom_text_report,
        "engine_crystal": engine_crystal_stats,
        "aggregate": aggregate,
    }
