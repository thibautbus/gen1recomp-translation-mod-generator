import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides


# gs.naming_screen.NamingScreenJumptable (the four name-type prompts and the
# nickname header's two fragments) and gs.name_input_chars.NameInputLower/
# NameInputUpper (the keyboard's own case-switch/delete/end labels), poke-corpus
# GoldSilver. Verified directly against en/fr/de/es/it_msg.txt: the engine's
# Strings()/Strings.source() literals (src/ui/gen2/NamingScreen.lua, fixed on
# gen1recomp branch fix/translate-gold-naming-screen) match the ROM's English
# source, so these are real official localized phrasing, not compromises.
PROMPTS = {
    "fr": {"YOUR NAME?": "VOTRE NOM?", "RIVAL'S NAME?": "NOM DU RIVAL?",
           "MOTHER'S NAME?": "NOM MERE?", "BOX NAME?": "NOM BOITE?",
           "NICKNAME?": "SURNOM?", "%s'S": "%s"},
    "de": {"YOUR NAME?": "DEIN NAME?", "RIVAL'S NAME?": "GEGNER-NAME?",
           "MOTHER'S NAME?": "MAMAs NAME?",
           "NICKNAME?": "ALIAS?", "%s'S": "%s"},
    "es": {"YOUR NAME?": "¿TU NOMBRE?", "RIVAL'S NAME?": "¿NOMBRE RIVAL?",
           "MOTHER'S NAME?": "¿NOMBRE MATERNO?", "BOX NAME?": "¿NOMBRE CAJA?",
           "NICKNAME?": "¿APODO?", "%s'S": "%s"},
    "it": {"YOUR NAME?": "NOME TUO?", "RIVAL'S NAME?": "NOME RIVALE?",
           "MOTHER'S NAME?": "NOME MAMMA?", "BOX NAME?": "NOME BOX?",
           "NICKNAME?": "NOME?", "%s'S": "%s"},
}

BOTTOM = {
    "fr": {"lower": "min", "UPPER": "MAJ", "DEL": "EFF", "END": "FIN"},
    "de": {"lower": "klein", "UPPER": "GROß", "DEL": "LÖSCH", "END": "ENDE"},
    "es": {"lower": "minús", "UPPER": "MAYÚS", "DEL": "BORRA", "END": "FIN"},
    "it": {"lower": "minus", "UPPER": "MAIUS", "DEL": "CANC", "END": "FINE"},
}

# German's "BOX NAME?" is identical to the English source (real German Gold
# shows the same words), so it carries no override at all.
NO_OP_KEYS = {"de": {"BOX NAME?"}}


class GoldNamingScreenCorpusOverrideTests(unittest.TestCase):
    def test_all_languages_have_the_expected_corpus_values(self):
        for language in PROMPTS:
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            expected = {**PROMPTS[language], **BOTTOM[language]}
            for source, override in expected.items():
                self.assertIn(source, overrides, (language, source))
                row = overrides[source]
                self.assertEqual(row["override"], override, (language, source))
                self.assertEqual(row["reason"], "engine-original", (language, source))
                self.assertIn("Corpus-confirmed", row["provenance"], (language, source))

    def test_identical_to_source_values_carry_no_pointless_override(self):
        for language, keys in NO_OP_KEYS.items():
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            for key in keys:
                self.assertNotIn(key, overrides, (language, key))


if __name__ == "__main__":
    unittest.main()
