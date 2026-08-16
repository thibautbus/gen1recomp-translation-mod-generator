import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides, printf_directives


KEYS = (
    "%s :L%d",
    ":L%d No.%03d",
    "Empty.",
    "No good! It's not\neven near water.",
    "PP",
    "PRNT",
    "BOX %d (WITHDRAW)",
    "BOX %d (RELEASE)",
    "Data unknown.",
    "The boulder fell\nthrough the hole!",
)

EXPECTED = {
    "fr": [
        "%s :N%d", ":N%d No.%03d", "Vide.",
        "Pas bon! Même pas\nprès de l'eau.", "PP", "PRNT",
        "BOITE %d (RETIRER)", "BOITE %d (RELACHER)", "Données inconnues.",
        "Le rocher est tombé\ndans le trou!",
    ],
    "de": [
        "%s :L%d", ":L%d Nr.%03d", "Leer.",
        "Schade! Nicht mal\nin Wassernähe.", "PP", "PRNT",
        "BOX %d (MITNEHMEN)", "BOX %d (FREILASSEN)", "Daten unbekannt.",
        "Der Felsen fiel\ndurch das Loch!",
    ],
    "es": [
        "%s :N%d", ":N%d Nº%03d", "Vacía.",
        "¡Qué mal! No estás\nni cerca del agua.", "PP", "PRNT",
        "CAJA %d (SACAR)", "CAJA %d (SOLTAR)", "Datos desconocidos.",
        "¡La roca cayó\npor el agujero!",
    ],
    "it": [
        "%s :L%d", ":L%d Nº%03d", "Vuoto.",
        "Niente da fare!\nLontano dall'acqua.", "PP", "PRNT",
        "BOX %d (RITIRA)", "BOX %d (LIBERA)", "Dati sconosciuti.",
        "Il masso è caduto\nnel buco!",
    ],
    "ja-Hrkt": [
        "%s :L%d", ":L%d No.%03d", "からっぽ。",
        "だめだ！\nみずの　そばじゃ　ない！", "PP", "PRNT",
        "ボックス%d（つれていく）", "ボックス%d（にがす）", "データ　ふめい。",
        "いわが　あなに\nおちた！",
    ],
}

CONTRACT_GAP_KEYS = (
    "%s lined up!\nScored %d coins!", "%s was\ntransferred to\n%s!",
    "%s's\nhurt by poison!", "%s's\nhurt by the burn!", "%s's %s\nrose!",
    "Once released,\n%s is\ngone forever. OK?",
    "PLAYER %s\nBADGES    %d\nPOKéDEX %3d\nTIME %6d:%02d", "BADGES",
    "HT %d′%02d″", "This POKéMON\ncan't be caught!", "Use on which one?", "WT %.1flb",
    "evolving!", "%sBOX %2d",
    "%s\nfainted!", "%s found\n%s!",
    "%s's HP\nwas restored!", "It won't have\nany effect.", "POKéDEX",
)

COLLISION_KEYS = (
    "%s\nfainted!", "%s found\n%s!",
    "%s's HP\nwas restored!", "It won't have\nany effect.", "POKéDEX",
)

POKEDEX_NUMBER_LABELS = {
    "fr": "№",
    "de": "Nr.",
    "es": "Nº",
    "it": "Nº",
    "ja-Hrkt": "№",
}


class ManualCorpusGapOverrideTests(unittest.TestCase):
    def test_pokedex_number_labels_are_corpus_inspired(self):
        for language, expected in POKEDEX_NUMBER_LABELS.items():
            path = Path("overrides") / language / "rby" / "engine.json"
            row = load_engine_overrides(path)["No."]
            self.assertEqual(row["override"], expected, language)
            self.assertEqual(row["reason"], "engine-original", language)
            self.assertIn("Corpus-inspired", row["provenance"], language)
            self.assertIn("DexEntryMenu.lua:97", row["provenance"], language)
            self.assertIn("PokeCorpus qid", row["provenance"], language)
            self.assertIn("in-game visual validation", row["provenance"], language)

    def test_japanese_quantity_prompt_is_corpus_inspired(self):
        row = load_engine_overrides(
            Path("overrides/ja-Hrkt/rby/engine.json")
        )["How many?"]
        self.assertEqual(row["override"], "いくつ？")
        self.assertEqual(row["reason"], "engine-contract-gap")
        self.assertIn("Corpus-inspired", row["provenance"])
        self.assertIn("PlayerPC.lua:47", row["provenance"])
        self.assertIn("DepositHowManyText", row["provenance"])
        self.assertIn("WithdrawHowManyText", row["provenance"])
        self.assertIn("TossHowManyText", row["provenance"])
        self.assertIn("splitting the key upstream", row["provenance"])
        self.assertIn("in-game validation", row["provenance"])

    def test_all_languages_have_exact_manual_corpus_gap_values(self):
        for language, values in EXPECTED.items():
            path = Path("overrides") / language / "rby" / "engine.json"
            overrides = load_engine_overrides(path)
            self.assertTrue(set(KEYS) <= set(overrides), language)
            for source, expected in zip(KEYS, values):
                row = overrides[source]
                self.assertEqual(row["override"], expected, (language, source))
                self.assertTrue(row["override"].strip(), (language, source))
                self.assertEqual(row["reason"], "engine-original", (language, source))
                self.assertIn("AI-generated engine-original translation", row["provenance"], (language, source))
                self.assertIn("no compatible PokeCorpus qid", row["provenance"], (language, source))
                self.assertIn("requires in-game visual validation", row["provenance"], (language, source))
                self.assertEqual(printf_directives(source), printf_directives(expected), (language, source))

    def test_engine_contract_gap_candidates_are_traceable_and_printf_safe(self):
        required_provenance = (
            "AI-generated", "concrete engine contract limitation",
            "could be improved by an upstream Gen1Recomp change",
            "requires in-game visual validation",
        )
        for language in EXPECTED:
            overrides = load_engine_overrides(Path("overrides") / language / "rby" / "engine.json")
            self.assertTrue(set(CONTRACT_GAP_KEYS) <= set(overrides), language)
            for source in CONTRACT_GAP_KEYS:
                row = overrides[source]
                self.assertEqual(row["reason"], "engine-contract-gap", (language, source))
                if source in COLLISION_KEYS:
                    continue
                self.assertTrue(all(token in row["provenance"] for token in required_provenance), (language, source))
                self.assertEqual(printf_directives(source), printf_directives(row["override"]), (language, source))
                self.assertEqual(source.count("\n"), row["override"].count("\n"), (language, source))

    def test_collision_contract_gap_provenance_names_shared_context_compromise(self):
        required_provenance = (
            "AI-generated neutral translation", "shared incompatible contexts",
            "split/context change upstream in Gen1Recomp", "ROM fidelity",
            "in-game validation of all callsites",
        )
        for language in EXPECTED:
            overrides = load_engine_overrides(Path("overrides") / language / "rby" / "engine.json")
            for source in COLLISION_KEYS:
                row = overrides[source]
                self.assertEqual(row["reason"], "engine-contract-gap", (language, source))
                self.assertTrue(all(token in row["provenance"] for token in required_provenance), (language, source))
                self.assertEqual(printf_directives(source), printf_directives(row["override"]), (language, source))
                self.assertEqual(source.count("\n"), row["override"].count("\n"), (language, source))


if __name__ == "__main__":
    unittest.main()
