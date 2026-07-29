import unittest
import tempfile
from pathlib import Path

from pipeline.align import align
from pipeline.join import WorksheetEntry, join_catalogs
from pipeline.model import CorpusRecord


CATALOGS = ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")


def worksheets(*entries):
    result = {name: [] for name in CATALOGS}
    result["item_names"] = list(entries)
    return result


def rows(language, technical, hidden, quantity="×01@"):
    return align([
        CorpusRecord("rb.names.TechnicalPrefix", "en", "TM"),
        CorpusRecord("rb.names.TechnicalPrefix", language, technical),
        CorpusRecord("rb.names.HiddenPrefix", "en", "HM"),
        CorpusRecord("rb.names.HiddenPrefix", language, hidden),
        CorpusRecord("rb.list_menu.InitialQuantityText", "en", "×01@"),
        CorpusRecord("rb.list_menu.InitialQuantityText", language, quantity),
    ], target_lang=language)


class MachineTerminologyTests(unittest.TestCase):
    def test_fr_prefixes_and_ascii_numbers_are_corpus_derived(self):
        output, report = join_catalogs(rows("fr", "CT", "CS"), worksheets(
            WorksheetEntry("TM_BIDE", "TM34", "item_names"), WorksheetEntry("HM_CUT", "HM01", "item_names")
        ), "fr")
        self.assertEqual(output["item_names"], {"TM_BIDE": "CT34", "HM_CUT": "CS01"})
        self.assertEqual(report["machine_display"]["number_style"], "ascii")
        self.assertEqual(report["machine_display"]["anchors"]["technical_prefix"]["status"], "ok")

    def test_de_and_ja_number_style(self):
        de, _ = join_catalogs(rows("de", "TM", "HM"), worksheets(WorksheetEntry("TM_BIDE", "TM34", "item_names")), "de")
        ja, report = join_catalogs(rows("ja-Hrkt", "わざマシン", "ひでんマシン", "×０１@"), worksheets(WorksheetEntry("TM_BIDE", "TM34", "item_names")), "ja-Hrkt")
        self.assertEqual(de["item_names"]["TM_BIDE"], "TM34")
        self.assertEqual(ja["item_names"]["TM_BIDE"], "わざマシン３４")
        self.assertEqual(report["machine_display"]["number_style_status"], "style_proven_fullwidth")

    def test_es_and_it_prefixes_are_not_language_hardcoded(self):
        for language, technical, hidden in (("es", "MT", "MO"), ("it", "MT", "MN")):
            output, report = join_catalogs(rows(language, technical, hidden), worksheets(
                WorksheetEntry("TM_BIDE", "TM34", "item_names"), WorksheetEntry("HM_CUT", "HM01", "item_names")
            ), language)
            self.assertEqual(output["item_names"], {"TM_BIDE": f"{technical}34", "HM_CUT": f"{hidden}01"})
            self.assertEqual(report["machine_display"]["status"], "ready")

    def test_missing_or_ambiguous_prefix_does_not_create_coverage(self):
        missing, missing_report = join_catalogs(rows("es", "", "HM"), worksheets(WorksheetEntry("TM_BIDE", "TM34", "item_names")), "es")
        self.assertEqual(missing["item_names"]["TM_BIDE"], "")
        self.assertEqual(missing_report["unmatched"]["item_names"], ["TM_BIDE"])
        ambiguous_rows = rows("it", "TM", "HM") + [
            # A duplicate anchor is intentionally rejected as ambiguous.
            align([CorpusRecord("rb.names.TechnicalPrefix", "en", "TM"), CorpusRecord("rb.names.TechnicalPrefix", "it", "TM2")], target_lang="it")[0]
        ]
        ambiguous, report = join_catalogs(ambiguous_rows, worksheets(WorksheetEntry("TM_BIDE", "TM34", "item_names")), "it")
        self.assertEqual(ambiguous["item_names"]["TM_BIDE"], "")
        self.assertEqual(report["machine_display"]["anchors"]["technical_prefix"]["status"], "qid_ambiguous")

    def test_unproven_quantity_style_does_not_create_coverage(self):
        output, report = join_catalogs(rows("it", "MT", "MS", quantity="quantità"), worksheets(
            WorksheetEntry("TM_BIDE", "TM34", "item_names")
        ), "it")
        self.assertEqual(output["item_names"]["TM_BIDE"], "")
        self.assertEqual(report["unmatched"]["item_names"], ["TM_BIDE"])
        self.assertEqual(report["machine_display"]["status"], "fallback")
        self.assertEqual(report["machine_display"]["last_fallback"], "style_unproven")

    def test_invalid_terminology_config_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anchors.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                join_catalogs([], worksheets(), terminology_anchors=path)

    def test_role_mismatch_falls_back_per_family(self):
        bad_technical = align([
            CorpusRecord("rb.names.TechnicalPrefix", "en", "WRONG"), CorpusRecord("rb.names.TechnicalPrefix", "de", "TM"),
            CorpusRecord("rb.names.HiddenPrefix", "en", "HM"), CorpusRecord("rb.names.HiddenPrefix", "de", "VM"),
            CorpusRecord("rb.list_menu.InitialQuantityText", "en", "×01@"), CorpusRecord("rb.list_menu.InitialQuantityText", "de", "×01@"),
        ], target_lang="de")
        output, report = join_catalogs(bad_technical, worksheets(
            WorksheetEntry("TM_BIDE", "TM34", "item_names"), WorksheetEntry("HM_CUT", "HM01", "item_names")
        ), "de")
        self.assertEqual(output["item_names"]["TM_BIDE"], "")
        self.assertEqual(output["item_names"]["HM_CUT"], "VM01")
        self.assertEqual(report["machine_display"]["technical_status"], "role_mismatch")
        self.assertTrue(report["machine_display"]["hidden_ready"])

    def test_terminology_config_contract_rejects_duplicate_or_swapped_anchors(self):
        base = {
            "schema": "gen1recomp-translation-mods/terminology-anchors", "version": 1,
            "anchors": {
                "technical_prefix": {"qid": "a", "extraction": {"kind": "text"}},
                "hidden_prefix": {"qid": "b", "extraction": {"kind": "text"}},
                "quantity_style": {"qid": "c", "extraction": {"kind": "quantity_digits", "sample": "01"}},
            },
        }
        for mutate in (
            lambda x: x["anchors"].update(hidden_prefix=x["anchors"]["technical_prefix"]),
            lambda x: x["anchors"].update(quantity_style={"qid": "c", "extraction": {"kind": "text", "sample": "01"}}),
            lambda x: x.update(schema="wrong"),
            lambda x: x.update(version=2),
        ):
            import copy
            config = copy.deepcopy(base); mutate(config)
            with self.assertRaises(ValueError):
                join_catalogs([], worksheets(), terminology_anchors=config)


if __name__ == "__main__":
    unittest.main()
