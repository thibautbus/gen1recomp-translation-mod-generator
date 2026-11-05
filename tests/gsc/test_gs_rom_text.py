import tempfile
import unittest
from pathlib import Path

from pipeline.gsc.rom_text import (
    RomTextScope, join_rom_text, load_rom_text_scope, parse_rom_text_catalog, rom_text_scope,
)


ENGINE_ROOT = Path(".cache/dependencies/gen1recomp")
SCOPE = RomTextScope(
    readers={"src/battle/gen2": "named", "src/ui/gen2/Quiz.lua": "exact"},
    anchors={"xx": {"SwappedText": "gs.battle.NextText"}},
)


def _rows(*rows):
    return list(rows)


class RomTextParserTests(unittest.TestCase):
    def test_decodes_only_the_extractor_escape_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gs_rom_text.tsv"
            path.write_text("_One\tLine\\nTwo\\tX\\\\Y\n", encoding="utf-8")
            self.assertEqual(parse_rom_text_catalog(path), {"_One": "Line\nTwo\tX\\Y"})
            path.write_text("_One\tbad\\q\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid escape"):
                parse_rom_text_catalog(path)


class RomTextScopeTests(unittest.TestCase):
    def _engine(self, root: Path) -> Path:
        engine = root / "engine"
        (engine / "src/battle/gen2").mkdir(parents=True)
        (engine / "src/ui/gen2").mkdir(parents=True)
        (engine / "src/battle/gen2/Battle.lua").write_text(
            'self:moveText("ZapText", a, b)\n'
            '-- FizzText is still printed through Strings()\n'
            "--[[ BuzzText ]]\n"
            "local LABELS = { quiz = SharedText }\n",
            encoding="utf-8",
        )
        (engine / "src/ui/gen2/Quiz.lua").write_text(
            'local line = RomText(data, "SharedText")\n', encoding="utf-8",
        )
        return engine

    def test_scope_is_the_extracted_labels_the_readers_name_outside_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            labels = ["ZapText", "FizzText", "BuzzText", "SharedText", "UnreadText"]
            self.assertEqual(rom_text_scope(engine, labels, SCOPE),
                             {"SharedText": "exact", "ZapText": "named"})
            # The engine's src directory works as well as its root.
            self.assertEqual(rom_text_scope(engine / "src", labels, SCOPE),
                             {"SharedText": "exact", "ZapText": "named"})

    def test_a_reader_missing_from_the_checkout_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            (engine / "src/ui/gen2/Quiz.lua").unlink()
            with self.assertRaisesRegex(ValueError, "Quiz.lua"):
                rom_text_scope(engine, ["ZapText"], SCOPE)


class JoinRomTextTests(unittest.TestCase):
    def test_joins_the_row_of_the_same_name_and_keeps_the_line_ending(self):
        extracted = {
            "ZapText": "{USER}\nwas zapped!{PROMPT}",
            "_QuizText": "Ready for the\nquiz?\f{DONE}",
        }
        rows = _rows(
            ("gs.battle.ZapText", "{text_start}<USER><LINE>was zapped!<PROMPT>",
             "{text_start}<USER><LINE>est zappé!<LINE><PROMPT>"),
            ("gs.common_1.QuizText", "{text_start}Ready for the<LINE>quiz?<DONE>",
             "{text_start}Prêt pour<LINE>le quiz?<DONE>"),
            ("c.battle.ZapText", "{text_start}<USER><LINE>was zapped!<PROMPT>", "{text_start}Autre<PROMPT>"),
        )
        catalog, report = join_rom_text(
            extracted, {"ZapText": "named", "_QuizText": "exact"}, rows, "fr", "gs.", SCOPE,
        )
        self.assertEqual(catalog, {
            "ZapText": "{USER}\nest zappé!{PROMPT}",
            "_QuizText": "Prêt pour\nle quiz?\f{DONE}",
        })
        self.assertEqual((report["translated"], report["total"], report["shipped"]), (2, 2, 2))
        self.assertEqual(report["details"]["ZapText"], "gs.battle.ZapText")

    def test_a_language_anchor_replaces_the_row_of_the_same_name(self):
        extracted = {"SwappedText": "{USER}\nis swapped!{PROMPT}"}
        rows = _rows(
            ("gs.battle.SwappedText", "{text_start}<USER><LINE>is swapped!<PROMPT>",
             "{text_start}<USER><LINE>wrong line<PROMPT>"),
            ("gs.battle.NextText", "{text_start}<USER><LINE>is next!<PROMPT>",
             "{text_start}<USER><LINE>right line<PROMPT>"),
        )
        catalog, report = join_rom_text(extracted, {"SwappedText": "named"}, rows, "xx", "gs.", SCOPE)
        self.assertEqual(catalog, {"SwappedText": "{USER}\nright line{PROMPT}"})
        self.assertEqual(report["details"]["SwappedText"], "gs.battle.NextText")
        catalog, _report = join_rom_text(extracted, {"SwappedText": "named"}, rows, "yy", "gs.", SCOPE)
        self.assertEqual(catalog, {"SwappedText": "{USER}\nwrong line{PROMPT}"})

    def test_a_label_in_two_files_takes_the_row_whose_english_the_cart_prints(self):
        extracted = {"ThawText": "{USER}\nthawed!{PROMPT}"}
        rows = _rows(
            ("gs.battle.ThawText", "{text_start}<USER><LINE>thawed!<PROMPT>",
             "{text_start}<USER><LINE>dégèle!<PROMPT>"),
            ("gs.field.ThawText", "{text_start}@{text_ram wStringBuffer1}{text_start}<LINE>thawed.<DONE>",
             "{text_start}@{text_ram wStringBuffer1}{text_start}<LINE>dégelé.<DONE>"),
        )
        catalog, _report = join_rom_text(extracted, {"ThawText": "named"}, rows, "fr", "gs.", SCOPE)
        self.assertEqual(catalog, {"ThawText": "{USER}\ndégèle!{PROMPT}"})

    def test_named_slots_may_move_drop_or_name_a_battler_but_not_add_a_value(self):
        extracted = {
            "HitText": "{USER} hit\n{TARGET}!{PROMPT}",
            "UseText": "{USER}\nused it!{PROMPT}",
            "ItemText": "{USER}\nis ready!{PROMPT}",
        }
        rows = _rows(
            ("gs.battle.HitText", "{text_start}<USER> hit<LINE><TARGET>!<PROMPT>",
             "{text_start}Touché!<PROMPT>"),
            ("gs.battle.UseText", "{text_start}<USER><LINE>used it!<PROMPT>",
             "{text_start}<TARGET> subit<LINE><USER>!<PROMPT>"),
            ("gs.battle.ItemText", "{text_start}<USER><LINE>is ready!<PROMPT>",
             "{text_start}<USER><LINE>a @{text_ram wStringBuffer1}{text_start}!<PROMPT>"),
        )
        policies = {"HitText": "named", "UseText": "named", "ItemText": "named"}
        catalog, report = join_rom_text(extracted, policies, rows, "fr", "gs.", SCOPE)
        self.assertEqual(set(catalog), {"HitText", "UseText"})
        self.assertEqual(report["details"]["ItemText"], "slots_mismatch")
        self.assertEqual(report["unmatched"], ["ItemText"])

    def test_exact_slots_keep_the_english_markers_in_order(self):
        extracted = {"RuleText": "The {STRBUF} must\nmatch.\f{DONE}"}
        rows = _rows(
            ("gs.common_3.RuleText", "{text_start}The @{text_ram wStringBuffer2}{text_start} must<LINE>match.<DONE>",
             "{text_start}Il faut<LINE>accorder.<DONE>"),
        )
        catalog, report = join_rom_text(extracted, {"RuleText": "exact"}, rows, "fr", "gs.", SCOPE)
        self.assertEqual(catalog, {})
        self.assertEqual(report["details"]["RuleText"], "slots_mismatch")

    def test_identical_rows_count_as_translated_but_are_not_shipped(self):
        extracted = {"OkText": "OK!{PROMPT}"}
        rows = _rows(("gs.battle.OkText", "{text_start}OK!<PROMPT>", "{text_start}OK!<PROMPT>"))
        catalog, report = join_rom_text(extracted, {"OkText": "named"}, rows, "de", "gs.", SCOPE)
        self.assertEqual(catalog, {})
        self.assertEqual((report["translated"], report["shipped"], report["fallback_english"]), (1, 0, 0))

    def test_a_row_with_no_end_of_message_runs_on_into_the_next_row_of_its_file(self):
        extracted = {
            "MissText": "{USER}'s\nmove missed!{PROMPT}",
            "Miss2Text": "{USER}'s\nmove missed!{PROMPT}",
        }
        rows = _rows(
            ("gs.battle.MissText", "{text_start}<USER>'s<LINE>move missed!<PROMPT>",
             "{text_start}でも\u3000@"),
            ("gs.battle.Miss2Text", "{text_start}<USER>'s<LINE>move missed!<PROMPT>",
             "{text_start}<USER>の<LINE>わざは\u3000それた<PROMPT>"),
            ("gs.field.NextText", "{text_start}Next.<DONE>", "{text_start}つぎ<DONE>"),
        )
        policies = {"MissText": "named", "Miss2Text": "named"}
        catalog, _report = join_rom_text(extracted, policies, rows, "ja-Hrkt", "gs.", SCOPE)
        self.assertEqual(catalog["MissText"], "でも\u3000{USER}の\nわざは\u3000それた{PROMPT}")
        self.assertEqual(catalog["Miss2Text"], "{USER}の\nわざは\u3000それた{PROMPT}")

    def test_without_a_corpus_every_label_keeps_its_english(self):
        catalog, report = join_rom_text({"OkText": "OK!"}, {"OkText": "named"}, None, "ko", "c.", SCOPE)
        self.assertEqual(catalog, {})
        self.assertEqual((report["translated"], report["total"], report["shipped"]), (0, 1, 0))
        self.assertEqual(report["unmatched"], ["OkText"])


class RepositoryScopeTests(unittest.TestCase):
    def test_the_repository_scope_is_valid(self):
        scope = load_rom_text_scope()
        self.assertIn("src/battle/gen2", scope.readers)
        self.assertEqual(scope.readers["src/battle/gen2"], "named")
        for language, anchors in scope.anchors.items():
            for label, qid in anchors.items():
                self.assertTrue(qid.startswith("gs.") or qid.startswith("c."), (language, label))

    def test_every_reader_exists_in_the_pinned_checkout(self):
        if not (ENGINE_ROOT / "src").is_dir():
            self.skipTest("cached Gen1Recomp checkout is unavailable")
        scope = load_rom_text_scope()
        # Every label the battle prints through romText is named in a reader.
        self.assertEqual(
            rom_text_scope(ENGINE_ROOT, ["HurtItselfText", "_MysteryGiftCanceledText"], scope),
            {"HurtItselfText": "named", "_MysteryGiftCanceledText": "exact"},
        )


if __name__ == "__main__":
    unittest.main()
