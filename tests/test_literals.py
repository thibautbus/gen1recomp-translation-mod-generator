import tempfile
import unittest
import json
from pathlib import Path

from pipeline.literals import extract_handlers, generate_handlers, load_recipes
from pipeline.model import Alignment, CorpusRecord
from pipeline.mod import generate_mod
from pipeline import builder


QYES = "rb.ViridianCity.ViridianCityYoungster2CaterpieAndWeedleDescriptionText"
QNO = "rb.ViridianCity.ViridianCityYoungster2OkThenText"
QPROMPT = "rb.ViridianCity.ViridianCityYoungster2YouWantToKnowAboutText"
QMUSEUM_PROMPT = "rb.Museum1F.Museum1FScientist1WouldYouLikeToComeInText"
QMUSEUM_YES = "rb.Museum1F.Museum1FScientist1ThankYouText"
QMUSEUM_NO_MONEY = "rb.Museum1F.Museum1FScientist1DontHaveEnoughMoneyText"
QMUSEUM_NO = "rb.Museum1F.Museum1FScientist1ComeAgainText"
QMUSEUM_ALREADY = "rb.Museum1F.Museum1FScientist1TakePlentyOfTimeText"


def row(qid, value):
    source = CorpusRecord(qid, "en", "source")
    target = CorpusRecord(qid, "fr", value)
    return Alignment(qid, "red", source, target, "qid")


# Legacy prompt/yes/no recipe shape, used to be the real
# viridian-city-youngster2 entry in config/rby/literal_handlers.json until it
# was removed (vanilla now handles that NPC on its own -- see
# docs/upstream-fixes.md). Kept here as a fixture so these tests exercise the
# legacy shape independent of whatever the production config currently
# contains.
LEGACY_RECIPE = [{
    "id": "viridian-city-youngster2",
    "map": "VIRIDIAN_CITY",
    "text_constant": "TEXT_VIRIDIANCITY_YOUNGSTER2",
    "prompt": {"qid": QPROMPT},
    "yes": {"qid": QYES},
    "no": {"qid": QNO},
}]


class LiteralHandlerTests(unittest.TestCase):
    def test_qid_provenance_and_marker_conversion(self):
        handlers = extract_handlers(
            [
                row(QPROMPT, "Want to know?"),
                row(QYES, "CATERPIE <LINE> poison, but <CONT>WEEDLE does.<PAGE>Watch"),
                row(QNO, "Oh, OK then!"),
            ],
            LEGACY_RECIPE,
        )
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].prompt_qid, QPROMPT)
        self.assertEqual(handlers[0].yes_qid, QYES)
        self.assertIn("\n", handlers[0].yes)
        self.assertIn("\v", handlers[0].yes)
        self.assertIn("\f", handlers[0].yes)

    def test_missing_branch_does_not_generate_false_handler(self):
        handlers = extract_handlers(
            [row(QPROMPT, "Prompt"), row(QYES, "YES")],
            LEGACY_RECIPE,
        )
        self.assertEqual(handlers, [])

    def test_ambiguous_qid_does_not_generate_handler(self):
        handlers = extract_handlers(
            [
                row(QYES, "first"),
                row(QYES, "different"),
                row(QNO, "no"),
                row(QPROMPT, "prompt"),
            ],
            LEGACY_RECIPE,
        )
        self.assertEqual(handlers, [])

    def test_recipe_schema_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                json.dumps({"schema": "wrong", "version": 1, "handlers": []}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_recipes(path)

    def test_generated_runtime_registers_supported_ui_and_map_script(self):
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(
                [row(QPROMPT, "Question?"), row(QYES, "Oui"), row(QNO, "Non")],
                LEGACY_RECIPE,
                Path(directory) / "lang" / "literal_handlers.lua",
            )
            body = path.read_text(encoding="utf-8")
            self.assertEqual(len(handlers), 1)
            self.assertIn("mod.content.map_scripts:register(\"VIRIDIAN_CITY\"", body)
            self.assertIn("mod.ui.TextBox", body)
            self.assertIn("mod.ui.ChoiceBox", body)
            self.assertNotIn('require("src.', body)
            self.assertIn("Question?", body)
            self.assertIn("Oui", body)

    def test_flow_recipe_preserves_state_and_choice_semantics(self):
        recipes = [{
            "map": "MUSEUM_1F", "text_constant": "TEXT_MUSEUM1F_SCIENTIST1",
            "flow": [
                {"if": {"condition": {"money_gte": 50}, "then": [
                    {"say": {"qid": QMUSEUM_PROMPT}},
                    {"choice": {"yes": [
                        {"money": {"amount": -50}},
                        {"set_flag": {"flag": "EVENT_BOUGHT_MUSEUM_TICKET"}},
                        {"say": {"qid": QMUSEUM_YES}},
                    ], "no": [{"say": {"qid": QMUSEUM_NO}}]}},
                ], "else": [{"say": {"qid": QMUSEUM_NO_MONEY}}]}},
            ],
        }]
        rows = [
            row(QMUSEUM_PROMPT, "Entrer?"), row(QMUSEUM_YES, "Merci!"),
            row(QMUSEUM_NO_MONEY, "Pas assez."), row(QMUSEUM_NO, "A bientôt!"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            self.assertEqual(len(handlers), 1)
            body = path.read_text(encoding="utf-8")
            self.assertIn("ChoiceBox.new", body)
            self.assertIn("game.save.money = (game.save.money or 0) + (-50)", body)
            self.assertIn('game.save.flags["EVENT_BOUGHT_MUSEUM_TICKET"] = true', body)

    def test_flow_missing_or_ambiguous_qid_fails_closed(self):
        recipes = [{
            "map": "MUSEUM_1F", "text_constant": "TEXT_MUSEUM1F_SCIENTIST1",
            "flow": [{"say": {"qid": QMUSEUM_ALREADY}}],
        }]
        self.assertEqual(extract_handlers([], recipes), [])
        rows = [row(QMUSEUM_ALREADY, "one"), row(QMUSEUM_ALREADY, "two")]
        self.assertEqual(extract_handlers(rows, recipes), [])

    def test_flow_if_and_choice_continue_with_following_nodes(self):
        recipes = [{
            "map": "X", "text_constant": "T", "flow": [
                {"if": {"condition": {"flag": "F"}, "then": [{"say": {"qid": QMUSEUM_YES}}], "else": [{"say": {"qid": QMUSEUM_NO}}]}},
                {"say": {"qid": QMUSEUM_ALREADY}},
            ],
        }]
        rows = [row(QMUSEUM_YES, "yes"), row(QMUSEUM_NO, "no"), row(QMUSEUM_ALREADY, "tail")]
        with tempfile.TemporaryDirectory() as directory:
            path, _ = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            body = path.read_text(encoding="utf-8")
            self.assertGreater(body.count('TextBox.new(game, "tail"'), 1)

    def test_say_name_qid_splices_the_translated_item_name_into_the_ram_marker(self):
        q_received, q_name = "rb.X.ReceivedText", "rb.names.ItemNames.49"
        recipes = [{
            "map": "X", "text_constant": "T", "flow": [
                {"say": {"qid": q_received, "name_qid": q_name}},
            ],
        }]
        rows = [row(q_received, "Obtenu: {RAM:wStringBuffer}!"), row(q_name, "PEPITE")]
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            self.assertEqual(len(handlers), 1)
            body = path.read_text(encoding="utf-8")
            self.assertIn('TextBox.new(game, "Obtenu: PEPITE!"', body)
            self.assertNotIn("{RAM:", body)

    def test_say_name_qid_fails_closed_when_either_qid_is_unmatched(self):
        recipes = [{
            "map": "X", "text_constant": "T", "flow": [
                {"say": {"qid": "rb.X.ReceivedText", "name_qid": "rb.names.ItemNames.49"}},
            ],
        }]
        rows = [row("rb.X.ReceivedText", "Obtenu: {RAM:wStringBuffer}!")]
        self.assertEqual(extract_handlers(rows, recipes), [])

    def test_trainer_defeated_condition_and_engage_trainer_mirror_the_vanilla_calls(self):
        q_rematch = "rb.X.RematchText"
        recipes = [{
            "map": "X", "text_constant": "T", "flow": [
                {"if": {"condition": {"trainer_defeated": True},
                        "then": [{"say": {"qid": q_rematch}}],
                        "else": [{"engage_trainer": None}]}},
            ],
        }]
        rows = [row(q_rematch, "Bien joué!")]
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            self.assertEqual(len(handlers), 1)
            body = path.read_text(encoding="utf-8")
            self.assertIn("if ow:trainerDefeated(npc) then", body)
            self.assertIn('TextBox.new(game, "Bien joué!"', body)
            self.assertIn("ow:engageTrainer(npc, done)", body)

    def test_inventory_op_and_any_combinator_track_item_state(self):
        # DSL coverage for `inventory` conditions/mutations and the `any`
        # combinator, independent of any specific production recipe: as of
        # gen1recomp's current pinned version the Bike Shop's own flavor
        # scripts (data/scripts/story2.lua, data/scripts/flavor/bike_shop.lua)
        # already read game.data.text directly and are correctly translated
        # by the ordinary dialogue override, so this project no longer
        # reimplements them -- see docs/upstream-fixes.md.
        q_young, q_cool = "rb.X.YoungsterText", "rb.X.CoolBikeText"
        recipes = [{
            "map": "X", "text_constant": "T", "flow": [
                {"if": {
                    "condition": {"any": [
                        {"flag": "EVENT_GOT_BICYCLE"},
                        {"inventory": {"item": "BICYCLE", "op": "gt", "amount": 0}},
                    ]},
                    "then": [{"say": {"qid": q_cool}}],
                    "else": [
                        {"say": {"qid": q_young}},
                        {"inventory": {"item": "BICYCLE", "op": "set", "amount": 1}},
                        {"set_flag": {"flag": "EVENT_GOT_BICYCLE"}},
                    ],
                }},
            ],
        }]
        rows = [row(q_young, "Ces vélos sont chers."), row(q_cool, "Ton vélo est super!")]
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            self.assertEqual(len(handlers), 1)
            body = path.read_text(encoding="utf-8")
            self.assertIn('game.save.flags["EVENT_GOT_BICYCLE"]', body)
            self.assertIn('(game.save.inventory["BICYCLE"] or 0) > 0', body)

    def test_done_operation_rejects_payload_during_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({
                "schema": "gen1recomp-translation-mods/literal-handlers",
                "version": 2,
                "handlers": [{"map": "X", "text_constant": "T", "flow": [{"done": "unsafe"}]}],
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_recipes(path)

    def test_legacy_generation_and_scaffold_preservation_load_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mod = generate_mod(
                [row(QPROMPT, "Question?"), row(QYES, "Oui"), row(QNO, "Non")],
                root / "mod",
            )
            self.assertTrue((mod / "lang" / "literal_handlers.lua").is_file())
            self.assertIn(
                'mod:read("lang/literal_handlers.lua")',
                (mod / "main.lua").read_text(encoding="utf-8"),
            )

            scaffold = root / "scaffold"
            (scaffold / "lang").mkdir(parents=True)
            (scaffold / "assets" / "font").mkdir(parents=True)
            (mod / "lang").mkdir(exist_ok=True)
            (scaffold / "main.lua").write_text("return function(mod)\nend\n", encoding="utf-8")
            for name in ("font.lua", "charmap.lua", "naming.lua"):
                (scaffold / "lang" / name).write_text(name, encoding="utf-8")
            builder.preserve_scaffold_support(scaffold, mod)
            preserved = (mod / "main.lua").read_text(encoding="utf-8")
            self.assertIn('mod:read("lang/literal_handlers.lua")', preserved)
            self.assertIn("setup(mod)", preserved)


if __name__ == "__main__":
    unittest.main()
