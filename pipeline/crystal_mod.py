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

Scope: dialogue text only. No named catalogs (species/moves/items/trainer
classes -- likely reusable from Gold/Silver's own translated values, since
the roster is the same generation, but not yet verified or wired up), no
engine-string catalog (the Options/Menu `Strings()` catalog is literally the
same shared Lua code across gold/silver/crystal, so Crystal saves already
get it for free from Gold/Silver's own `overrides/<lang>/gsc/engine.json` --
nothing Crystal-specific needed there), and no release gates.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .gs_join import GsJoinEntry, join_gs_pointers, read_corpus_rows
from .gs_text import parse_gs_text_catalog

CRYSTAL_POINTER_DECISIONS_SCHEMA = "gen1recomp-translation-mods/crystal-pointer-decisions"
_POINTER = re.compile(r"[0-7][0-9a-f]:[0-7][0-9a-f]{3}")


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
    no ko_msg.txt, unlike GoldSilver), returns an empty entry list and
    all-zero stats rather than raising -- Crystal dialogue simply stays in
    English for that language; the caller (build_gs()) still requires and
    extracts the Crystal ROM (for the shared engine-string catalog and
    consistency with the mandatory-Crystal-ROM policy), it just has nothing
    corpus-backed to translate for Crystal specifically.
    """
    crystal_out_dir = Path(crystal_out_dir)
    corpus_dir = Path(corpus_dir)
    records = parse_gs_text_catalog(
        crystal_out_dir / "gs_text.tsv", crystal_out_dir / "gs_labels.tsv",
    )
    if not (corpus_dir / f"{language}_msg.txt").is_file():
        stats = {
            "total": len(records), "unique": 0, "harmless_ambiguous": 0,
            "override": 0, "reviewed_qid": 0, "unresolved": 0, "no_match": len(records),
            "markup_only": 0,
        }
        return [], stats
    corpus_rows = read_corpus_rows(corpus_dir, target_lang=language)
    return join_gs_pointers(
        records, corpus_rows,
        overrides=load_crystal_dialogue_overrides(language),
        qid_decisions=load_crystal_pointer_decisions(),
    )


def crystal_text_catalog_from_join(entries: list[GsJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved."""
    return {entry.pointer: entry.translation for entry in entries if entry.translation}
