"""Unit-level check of tools/measure_engine_reach.py's categorisation logic
(attach every corpus category to a registry, or flag it out of scope).
Uses synthetic fixtures rather than the real Gold ROM/engine checkout,
which are not committed.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_engine_reach", ROOT / "tools" / "measure_engine_reach.py")
measure_engine_reach = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measure_engine_reach
spec.loader.exec_module(measure_engine_reach)


class MeasureEngineReachTests(unittest.TestCase):
    def test_pointer_normalised_set_reads_the_tsv_second_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "gold_text.tsv"
            tsv.write_text("55:4067\t{text_start}Hello there!<DONE>\n", encoding="utf-8")
            self.assertEqual(measure_engine_reach._pointer_normalised_set(tsv), {"hellothere"})

    def test_engine_normalised_index_maps_normalised_source_to_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "src" / "battle" / "gen2").mkdir(parents=True)
            (root / "src" / "battle" / "gen2" / "Prize.lua").write_text(
                'local GOT = Strings.source("Sent all to MOM!")\n'
                'return Strings(GOT)\n',
                encoding="utf-8",
            )
            index = measure_engine_reach._engine_normalised_index(root)
            self.assertIn("sentalltomom", index)
            self.assertEqual(index["sentalltomom"], ["battle/gen2/Prize.lua"])

    def test_category_attaches_to_pointer_strings_either_or_neither(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            (engine / ".git").mkdir(parents=True)
            (engine / "src" / "battle" / "gen2").mkdir(parents=True)
            (engine / "src" / "battle" / "gen2" / "Prize.lua").write_text(
                'return Strings("Sent all to MOM!")\n', encoding="utf-8",
            )
            gold_text = root / "gold_text.tsv"
            gold_text.write_text("55:0001\t{text_start}Reached by pointer.<DONE>\n", encoding="utf-8")
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "qid_msg.txt").write_text(
                "gs.battle.SentAllToMomText\ngs.text.Reached\ngs.text.Neither\n", encoding="utf-8",
            )
            (corpus / "en_msg.txt").write_text(
                "{text_start}Sent all to MOM!<DONE>\n"
                "{text_start}Reached by pointer.<DONE>\n"
                "{text_start}Nothing reads this yet.<DONE>\n",
                encoding="utf-8",
            )

            pointer_norms = measure_engine_reach._pointer_normalised_set(gold_text)
            engine_index = measure_engine_reach._engine_normalised_index(engine)

            def lines(name):
                return measure_engine_reach.measure_join.split_lines((corpus / f"{name}_msg.txt").read_text(encoding="utf-8"))
            qids, ens = lines("qid"), lines("en")

            hits = {}
            for qid, en in zip(qids, ens):
                norm = measure_engine_reach.measure_join.normalise(en)
                hits[qid] = (norm in pointer_norms, norm in engine_index)

            self.assertEqual(hits["gs.battle.SentAllToMomText"], (False, True))
            self.assertEqual(hits["gs.text.Reached"], (True, False))
            self.assertEqual(hits["gs.text.Neither"], (False, False))


if __name__ == "__main__":
    unittest.main()
