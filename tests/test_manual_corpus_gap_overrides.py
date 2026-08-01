import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides, printf_directives


KEYS = (
    "%s :L%d",
    ":L%d No.%03d",
    "Empty.",
    "No good! It's not\neven near water.",
    "PP",
    "PRINT BOX",
    "PRNT",
    "BOX %d (WITHDRAW)",
    "BOX %d (RELEASE)",
    "Data unknown.",
    "The boulder fell\nthrough the hole!",
    "%s's\nstatus returned\nto normal!",
    "Coin count:\n%d",
)

EXPECTED = {
    "fr": [
        "%s :N%d", ":N%d No.%03d", "Vide.",
        "Pas bon! Même pas\nprès de l'eau.", "PP", "IMPRIMER BOITE", "PRNT",
        "BOITE %d (RETIRER)", "BOITE %d (RELACHER)", "Données inconnues.",
        "Le rocher est tombé\ndans le trou!", "L'état de %s\nest redevenu\nnormal!",
        "Jetons :\n%d",
    ],
    "de": [
        "%s :L%d", ":L%d Nr.%03d", "Leer.",
        "Schade! Nicht mal\nin Wassernähe.", "PP", "BOX DRUCKEN", "PRNT",
        "BOX %d (MITNEHMEN)", "BOX %d (FREILASSEN)", "Daten unbekannt.",
        "Der Felsen fiel\ndurch das Loch!", "Der Status von %s\nist wieder\nnormal!",
        "Münzen:\n%d",
    ],
    "es": [
        "%s :N%d", ":N%d Nº%03d", "Vacía.",
        "¡Qué mal! No estás\nni cerca del agua.", "PP", "IMPRIMIR CAJA", "PRNT",
        "CAJA %d (SACAR)", "CAJA %d (SOLTAR)", "Datos desconocidos.",
        "¡La roca cayó\npor el agujero!", "El estado de %s\nvolvió a la\nnormalidad!",
        "Fichas:\n%d",
    ],
    "it": [
        "%s :L%d", ":L%d Nº%03d", "Vuoto.",
        "Niente da fare!\nLontano dall'acqua.", "PP", "STAMPA BOX", "PRNT",
        "BOX %d (RITIRA)", "BOX %d (LIBERA)", "Dati sconosciuti.",
        "Il masso è caduto\nnel buco!", "Lo stato di %s\nè tornato\nnormale!",
        "Gettoni:\n%d",
    ],
    "ja-Hrkt": [
        "%s :L%d", ":L%d No.%03d", "からっぽ。",
        "だめだ！\nみずの　そばじゃ　ない！", "PP", "ボックスを　プリント", "PRNT",
        "ボックス%d（つれていく）", "ボックス%d（にがす）", "データ　ふめい。",
        "いわが　あなに\nおちた！", "%sの\nじょうたいが\nもとに　もどった！",
        "コイン\n%dまい",
    ],
}

REASON_AI = "AI-generated manual corpus-gap translation."
REASON_VISUAL = "AI-generated manual corpus-gap translation; requires in-game visual validation."
VISUAL_KEYS = {
    language: {"No good! It's not\neven near water."}
    for language in EXPECTED
}
VISUAL_KEYS["de"].update(("BOX %d (WITHDRAW)", "BOX %d (RELEASE)"))


class ManualCorpusGapOverrideTests(unittest.TestCase):
    def test_all_languages_have_exact_manual_corpus_gap_values(self):
        for language, values in EXPECTED.items():
            path = Path("overrides") / language / "engine_overrides.json"
            overrides = load_engine_overrides(path)
            self.assertTrue(set(KEYS) <= set(overrides), language)
            for source, expected in zip(KEYS, values):
                row = overrides[source]
                self.assertEqual(row["override"], expected, (language, source))
                self.assertTrue(row["override"].strip(), (language, source))
                reason = REASON_VISUAL if source in VISUAL_KEYS[language] else REASON_AI
                self.assertEqual(row["reason"], reason, (language, source))
                self.assertEqual(printf_directives(source), printf_directives(expected), (language, source))


if __name__ == "__main__":
    unittest.main()
