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
QBIKE_WOMAN = "rb.BikeShop.BikeShopMiddleAgedWomanText"
QBIKE_YOUNG = "rb.BikeShop.BikeShopYoungsterTheseBikesAreExpensiveText"
QBIKE_COOL = "rb.BikeShop.BikeShopYoungsterCoolBikeText"


def row(qid, value):
    source = CorpusRecord(qid, "en", "source")
    target = CorpusRecord(qid, "fr", value)
    return Alignment(qid, "red", source, target, "qid")


class LiteralHandlerTests(unittest.TestCase):
    def test_qid_provenance_and_marker_conversion(self):
        handlers = extract_handlers(
            [
                row(QPROMPT, "Want to know?"),
                row(QYES, "CATERPIE <LINE> poison, but <CONT>WEEDLE does.<PAGE>Watch"),
                row(QNO, "Oh, OK then!"),
            ],
            load_recipes(Path(__file__).parents[1] / "config" / "literal_handlers.json"),
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
            load_recipes(
                Path(__file__).parents[1] / "config" / "literal_handlers.json"
            ),
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
            load_recipes(
                Path(__file__).parents[1] / "config" / "literal_handlers.json"
            ),
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
                load_recipes(Path(__file__).parents[1] / "config" / "literal_handlers.json"),
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

    def test_configured_museum_handler_emits_rope_on_step(self):
        rows = [
            row(QMUSEUM_PROMPT, "Entrer?"), row(QMUSEUM_YES, "Merci!"),
            row(QMUSEUM_NO_MONEY, "Pas assez."), row(QMUSEUM_NO, "A bientôt!"),
            row(QMUSEUM_ALREADY, "Profitez-en."),
        ]
        recipes = load_recipes(Path(__file__).parents[1] / "config" / "literal_handlers.json")
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            museum = [h for h in handlers if h.text_constant == "TEXT_MUSEUM1F_SCIENTIST1"]
            self.assertEqual(len(museum), 1)
            body = path.read_text(encoding="utf-8")
            self.assertIn("onStep = function(game, ow, x, y)", body)
            self.assertIn("x == 9 and y == 4", body)
            self.assertIn('game.save.flags["EVENT_BOUGHT_MUSEUM_TICKET"]', body)
            self.assertIn('ow:scriptMove(ow.player, "down", 1', body)

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

    def test_bike_flavor_handlers_use_corpus_and_track_bicycle_state(self):
        rows = [row(QBIKE_WOMAN, "Un vélo de ville."), row(QBIKE_YOUNG, "Ces vélos sont chers."), row(QBIKE_COOL, "Ton vélo est super!")]
        recipes = load_recipes(Path(__file__).parents[1] / "config" / "literal_handlers.json")
        with tempfile.TemporaryDirectory() as directory:
            path, handlers = generate_handlers(rows, recipes, Path(directory) / "handlers.lua")
            constants = {h.text_constant for h in handlers}
            self.assertIn("TEXT_BIKESHOP_MIDDLE_AGED_WOMAN", constants)
            self.assertIn("TEXT_BIKESHOP_YOUNGSTER", constants)
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
