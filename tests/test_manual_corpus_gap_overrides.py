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

CONTRACT_GAP_KEYS = (
    "%s can't\nlearn that move!", "%s defeated\n%s!", "%s is\nabout to use",
    "%s lined up!\nScored %d coins!", "%s was\ntransferred to\n%s!",
    "%s's\nSUBSTITUTE broke!", "%s's\nhurt by poison!", "%s's\nhurt by the burn!",
    "%s's %s\nrose!", "%s's PP\nwas restored!", "Converted type to\n%s's!",
    "It didn't affect\n%s!", "It knows that\nmove already!",
    "Once released,\n%s is\ngone forever. OK?",
    "PLAYER %s\nBADGES    %d\nPOKéDEX %3d\nTIME %6d:%02d", "BADGES",
    "HT %d′%02d″", "The wild POKéMON\nran away!",
    "This POKéMON\ncan't be caught!", "Use on which one?", "WT %.1flb",
    "Will %s\nchange POKéMON?", "evolving!", "%sBOX %2d",
    "%s\nfainted!", "%s\nused %s!", "%s found\n%s!",
    "%s's HP\nwas restored!", "It won't have\nany effect.", "POKéDEX",
)

COLLISION_KEYS = (
    "%s\nfainted!", "%s\nused %s!", "%s found\n%s!",
    "%s's HP\nwas restored!", "It won't have\nany effect.", "POKéDEX",
)


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

    def test_engine_contract_gap_candidates_are_traceable_and_printf_safe(self):
        required_provenance = (
            "AI-generated", "concrete engine contract limitation",
            "could be improved by an upstream Gen1Recomp change",
            "requires in-game visual validation",
        )
        for language in EXPECTED:
            overrides = load_engine_overrides(Path("overrides") / language / "engine_overrides.json")
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
            overrides = load_engine_overrides(Path("overrides") / language / "engine_overrides.json")
            for source in COLLISION_KEYS:
                row = overrides[source]
                self.assertEqual(row["reason"], "engine-contract-gap", (language, source))
                self.assertTrue(all(token in row["provenance"] for token in required_provenance), (language, source))
                self.assertEqual(printf_directives(source), printf_directives(row["override"]), (language, source))
                self.assertEqual(source.count("\n"), row["override"].count("\n"), (language, source))


if __name__ == "__main__":
    unittest.main()
