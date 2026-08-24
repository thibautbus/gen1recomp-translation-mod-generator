#!/usr/bin/env python3
"""Measure the corpus <-> Gold-pointer join by normalised English.

Reads gs_text.tsv (produced by gs_extract.lua) and the parallel
GoldSilver corpus files, then reports how many ROM pointers get exactly one
corpus candidate, how many are ambiguous, and how many have none.

Usage: measure_join.py <out_dir> <corpus_dir>
"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.gs_text import normalise, split_lines, unescape  # noqa: E402


def main() -> int:
    out_dir, corpus_dir = Path(sys.argv[1]), Path(sys.argv[2])

    pointers = []
    for line in split_lines((out_dir / "gs_text.tsv").read_text(encoding="utf-8")):
        if not line:
            continue
        key, _, raw = line.partition("\t")
        pointers.append((key, unescape(raw)))

    def lines(name):
        return split_lines((corpus_dir / f"{name}_msg.txt").read_text(
            encoding="utf-8"))

    qids, ens, frs = lines("qid"), lines("en"), lines("fr")
    assert len(qids) == len(ens) == len(frs), "corpus files are not parallel"

    # normalised English -> list of (qid, fr)
    by_english = defaultdict(list)
    for qid, en, fr in zip(qids, ens, frs):
        by_english[normalise(en)].append((qid, fr))

    unique = usable_fr = ambiguous = harmless = markup_only = absent = 0
    for key, value in pointers:
        norm = normalise(value)
        if not norm:
            markup_only += 1
            continue
        candidates = by_english.get(norm)
        if not candidates:
            absent += 1
        elif len(candidates) == 1:
            unique += 1
            if candidates[0][1].strip():
                usable_fr += 1
        else:
            ambiguous += 1
            # Compare the *normalised* French: candidates that differ only in
            # line-break markup carry the same translation, so the ambiguity
            # has no consequence for the mod. Comparing raw strings instead
            # undercounts these (84 rather than 111).
            french = {normalise(fr) for _, fr in candidates}
            if len(french) == 1:
                harmless += 1

    total = len(pointers)
    def pct(n):
        return f"{100.0 * n / total:.1f} %"

    print(f"ROM text pointers                : {total}")
    print(f"GoldSilver corpus entries        : {len(qids)}")
    print(f"Unique match on normalised EN    : {unique}  {pct(unique)}")
    print(f"  of which usable French         : {usable_fr}  {pct(usable_fr)}")
    print(f"Ambiguous (several candidates)   : {ambiguous}  {pct(ambiguous)}")
    print(f"  of which harmless (same FR)    : {harmless}")
    print(f"  needing disambiguation         : {ambiguous - harmless}"
          f"  {pct(ambiguous - harmless)}")
    print(f"Markup-only (empty normalised)   : {markup_only}  {pct(markup_only)}")
    print(f"Pointers with no candidate       : {absent}  {pct(absent)}")
    print(f"Ceiling without disambiguation   : {unique + harmless} / {total}"
          f"  {pct(unique + harmless)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
