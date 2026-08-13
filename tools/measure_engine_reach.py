#!/usr/bin/env python3
"""Attach every GoldSilver corpus category to a registry, or flag it as
out of scope, so the announced Gold scope stops being an estimate.

Two things can make a corpus entry reachable today:

  pointer   its normalised English matches a ROM text pointer gold_extract.lua
            found (tools/measure_join.py's join) -> ships through the `text`
            registry, converted by pipeline/tokens.py:corpus_to_engine.
  strings   its normalised English matches a literal Strings(...) callsite
            found anywhere in the engine source (pipeline/engine_scope.py)
            -> ships through the `strings` registry the same way RBY's
            engine backlog does, via a language's shared_engine_overrides
            (a future gold_engine_overrides.json, per the existing
            yellow_engine_overrides.json precedent).

Neither does not mean untranslatable: it means nothing in this Gen1Recomp
checkout reads that text yet (Phase 1 -- README.md:62), so shipping it
would be a silent no-op. This script only reports; step 8/11 decide what
to wire up.

Usage: measure_engine_reach.py <gold_text.tsv> <corpus_dir> <gen1recomp_root>
"""
import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("measure_join", Path(__file__).resolve().parent / "measure_join.py")
measure_join = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure_join)

sys.path.insert(0, str(ROOT))
from pipeline.engine_scope import iter_callsites  # noqa: E402


def _engine_normalised_index(checkout: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for call in iter_callsites(checkout):
        norm = measure_join.normalise(call["source"])
        if norm:
            index.setdefault(norm, []).append(call["path"])
    return index


def _pointer_normalised_set(gold_text_tsv: Path) -> set[str]:
    pointers = set()
    for line in measure_join.split_lines(gold_text_tsv.read_text(encoding="utf-8")):
        if not line:
            continue
        _, _, raw = line.partition("\t")
        norm = measure_join.normalise(measure_join.unescape(raw))
        if norm:
            pointers.add(norm)
    return pointers


def main() -> int:
    gold_text_tsv, corpus_dir, gen1recomp_root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])

    pointer_norms = _pointer_normalised_set(gold_text_tsv)
    engine_index = _engine_normalised_index(gen1recomp_root)

    def lines(name):
        return measure_join.split_lines((corpus_dir / f"{name}_msg.txt").read_text(encoding="utf-8"))
    qids, ens = lines("qid"), lines("en")

    total, via_pointer, via_strings, via_either = Counter(), Counter(), Counter(), Counter()
    for qid, en in zip(qids, ens):
        category = qid.split(".", 2)[1] if qid.count(".") >= 1 else qid
        total[category] += 1
        norm = measure_join.normalise(en)
        if not norm:
            continue
        hit_pointer = norm in pointer_norms
        hit_strings = norm in engine_index
        if hit_pointer:
            via_pointer[category] += 1
        if hit_strings:
            via_strings[category] += 1
        if hit_pointer or hit_strings:
            via_either[category] += 1

    print(f"{'category':32s} {'total':>6s} {'pointer':>8s} {'strings':>8s} {'either':>8s} {'neither':>8s}")
    for category in sorted(total, key=lambda c: -total[c]):
        n = total[category]
        neither = n - via_either[category]
        print(f"{category:32s} {n:6d} {via_pointer[category]:8d} {via_strings[category]:8d} "
              f"{via_either[category]:8d} {neither:8d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
