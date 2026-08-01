import json
import shutil
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from pipeline.engine import (
    load_semantic_anchor_decisions,
    load_semantic_anchors,
    match_engine_catalog,
    merge_semantic_anchors,
)
from pipeline.model import Alignment, CorpusRecord


ROOT = Path(__file__).resolve().parents[1]


class SemanticAnchorDecisionTests(unittest.TestCase):
    def test_checked_in_assets_load_and_merge_without_overlap(self):
        deterministic = load_semantic_anchors(ROOT / "config/semantic_anchors.json")
        decisions = load_semantic_anchor_decisions(ROOT / "config/semantic_anchor_decisions.json")
        self.assertTrue(set(deterministic).isdisjoint(decisions))
        merged, provenance = merge_semantic_anchors(deterministic, decisions)
        self.assertEqual(set(merged), set(deterministic) | set(decisions))
        self.assertEqual(set(provenance), set(decisions))

    def test_schema_validation_and_conflict_rejection(self):
        decision = {
            "anchor": {"qid": "q", "extraction": {"kind": "full"}},
            "decision_type": "contextual",
            "rationale": "known limitation",
            "languages": [],
            "languages_verified": False,
            "qids": ["q"],
            "trace_status": "known-limitation",
        }
        wrapped = {
            "schema": "gen1recomp-translation-mods/semantic-anchor-decisions",
            "version": 1,
            "decisions": {"X": decision},
        }
        self.assertIn("X", load_semantic_anchor_decisions(wrapped))
        decision["trace_status"] = "unknown"
        with self.assertRaises(ValueError):
            load_semantic_anchor_decisions(wrapped)
        with self.assertRaises(ValueError):
            load_semantic_anchor_decisions({"X": {"anchor": {"qid": "q", "extraction": {"kind": "full"}}, "decision_type": "x", "rationale": "ok", "languages": ["xx"]}})
        with self.assertRaises(ValueError):
            merge_semantic_anchors({"X": {"qid": "q", "extraction": {"kind": "full"}}}, {"X": {"anchor": {"qid": "q2", "extraction": {"kind": "full"}}}})

    def test_missing_corrupt_and_wrong_shape_decision_files_fail_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            with self.assertRaises(FileNotFoundError):
                load_semantic_anchor_decisions(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)
            path.write_text(json.dumps({"schema": "wrong", "version": 1, "decisions": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)
            path.write_text(json.dumps({"schema": "gen1recomp-translation-mods/semantic-anchor-decisions", "version": 1, "decisions": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semantic_anchor_decisions(path)

    def test_catalog_provenance_survives_explicit_match_call(self):
        catalog = load_semantic_anchors()
        qid = "rb.pokedex.PokedexMenuItemsText"
        rows = [Alignment(qid, "both", CorpusRecord(qid, "en", "DATA\nCRY\nAREA\nQUIT"), CorpusRecord(qid, "fr", "DON\nCRI\nZONE\nQUITTER"), "qid")]
        output, report = match_engine_catalog({"QUIT": ""}, rows, semantic_anchors=catalog, target_lang="fr")
        self.assertEqual(output["QUIT"], "QUITTER")
        self.assertEqual(report["decision_provenance"]["QUIT"]["trace_status"], "known-limitation")

    def test_self_check_requires_and_validates_decision_asset(self):
        import build_translation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            shutil.copy(ROOT / "config" / "pipeline.toml", root / "config" / "pipeline.toml")
            shutil.copy(ROOT / "config" / "semantic_anchors.json", root / "config" / "semantic_anchors.json")
            shutil.copy(ROOT / "pyproject.toml", root / "pyproject.toml")
            with patch("pipeline.builder.resource_root", return_value=root), patch("pipeline.builder.work_root", return_value=root / "work"):
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(build_translation._self_check(), 1)
                self.assertIn("semantic anchor decisions file missing", error.getvalue())
                decisions = root / "config" / "semantic_anchor_decisions.json"
                decisions.write_text("{}", encoding="utf-8")
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(build_translation._self_check(), 1)
                self.assertIn("wrapped schema", error.getvalue())
                shutil.copy(ROOT / "config" / "semantic_anchor_decisions.json", decisions)
                with redirect_stdout(StringIO()):
                    self.assertEqual(build_translation._self_check(), 0)


if __name__ == "__main__":
    unittest.main()
