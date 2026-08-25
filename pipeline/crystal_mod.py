"""Join Pokémon Crystal's extracted dialogue against poke-corpus.

Crystal shares Gold/Silver's `tools/gs_extract.lua` extractor contract (the
extractor's own edition branches, and gen1recomp's `RomExtractorGen2.lua`
underneath it, already cover "crystal") and its normalized-English-text join
strategy (`pipeline.gs_join.join_gs_pointers` matches by text, not by
pointer, so it is edition-agnostic despite its "gs_" name) -- but Crystal's
own `bank:address` pointers diverge substantially from Gold/Silver's (95.8%
of shared symbol names have a different address, measured directly against
real ROMs), so it needs its own corpus join against poke-corpus's separate
`Crystal/` collection (`c.text.*` qids, no overlap with `GoldSilver/`'s
`gs.*`) rather than reusing Gold/Silver's own resolved catalog.

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
get it for free from Gold/Silver's own `overrides/<lang>/gs/engine.json` --
nothing Crystal-specific needed there), and no release gates.
"""
from __future__ import annotations

from pathlib import Path

from .gs_join import GsJoinEntry, join_gs_pointers, read_corpus_rows
from .gs_text import parse_gs_text_catalog


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
    return join_gs_pointers(records, corpus_rows)


def crystal_text_catalog_from_join(entries: list[GsJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved."""
    return {entry.pointer: entry.translation for entry in entries if entry.translation}
