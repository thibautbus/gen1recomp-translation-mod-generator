import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides


# gs.options_menu.StringOptions (the OPTION screen's combined label text) and
# its per-row value ladders (gs.options_menu.Options_*), poke-corpus
# GoldSilver. Verified directly against en/fr/de/es/it_msg.txt line 1093
# onward: the engine's Strings() literals (src/ui/gen2/OptionsMenu.lua,
# fixed on gen1recomp branch fix/translate-gold-options-menu) match the
# ROM's own English source character-for-character, padding included, so
# these are exact corpus values, not adaptations.
LABELS = {
    "fr": {
        "TEXT SPEED": "VIT. TEXTE", "BATTLE SCENE": "ANIMATION COMBAT",
        "BATTLE STYLE": "STYLE COMBAT", "SOUND": "SON", "PRINT": "IMPRIMER",
        "MENU ACCOUNT": "COMPTE MENU", "FRAME": "FENETRE", "CANCEL": "RETOUR",
    },
    "de": {
        "TEXT SPEED": "TEXT-TEMPO", "BATTLE SCENE": "KAMPFANIMATION",
        "BATTLE STYLE": "KAMPFSTIL", "PRINT": "DRUCKEN",
        "MENU ACCOUNT": "MENÜ-STEUERUNG", "FRAME": "RAHMEN",
        "CANCEL": "ZURÜCK", ":TYPE": ":TYP ",
    },
    "es": {
        "TEXT SPEED": "VELOCIDAD TEXTO", "BATTLE SCENE": "ANIMACIÓN BATALLA",
        "BATTLE STYLE": "ESTILO BATALLA", "SOUND": "SONIDO",
        "PRINT": "IMPRIMIR", "MENU ACCOUNT": "DESCRIPCIÓN MENÚ",
        "FRAME": "IMAGEN", "CANCEL": "SALIR", ":TYPE": ":TIPO",
    },
    "it": {
        "TEXT SPEED": "VELOC. TESTO", "BATTLE SCENE": "ANIMAZIONE LOTTA",
        "BATTLE STYLE": "STILE LOTTA", "SOUND": "SUONO", "PRINT": "STAMPA",
        "MENU ACCOUNT": "GUIDA MENU", "FRAME": "CORNICE", "CANCEL": "ESCI",
        ":TYPE": ":TIPO",
    },
}

VALUES = {
    "fr": {
        "FAST": "3", "MID ": "2", "SLOW": "1", "ON ": "OUI", "OFF": "NON",
        "SHIFT": "CHOIX ", "SET  ": "DEFINI",
        "LIGHTEST": "CLAIR+ ", "LIGHTER ": "CLAIR  ", "NORMAL  ": "NORMAL ",
        "DARKER  ": "SOMBRE ", "DARKEST ": "SOMBRE+",
    },
    "de": {
        "FAST": "3", "MID ": "2", "SLOW": "1", "ON ": "AN ", "OFF": "AUS",
        "SHIFT": "WECHSEL", "SET  ": "FOLGEND",
        "LIGHTEST": "SEHR HELL  ", "LIGHTER ": "HELL       ",
        "NORMAL  ": "NORMAL     ", "DARKER  ": "DUNKEL     ",
        "DARKEST ": "SEHR DUNKEL",
    },
    "es": {
        "FAST": "3", "MID ": "2", "SLOW": "1", "ON ": "SÍ", "OFF": "NO",
        "SHIFT": "CAMBIAR ", "SET  ": "MANTENER", "MONO  ": "MONO   ",
        "STEREO": "ESTÉREO",
        "LIGHTEST": "MÁS CLARO ", "LIGHTER ": "CLARO     ",
        "NORMAL  ": "NORMAL    ", "DARKER  ": "OSCURO    ",
        "DARKEST ": "MÁS OSCURO",
    },
    "it": {
        "FAST": "3", "MID ": "2", "SLOW": "1", "ON ": "SÌ", "OFF": "NO",
        "SHIFT": "SCEGLI", "SET  ": "FISSO ",
        "LIGHTEST": "CHIARIS.", "LIGHTER ": "CHIARA  ", "NORMAL  ": "NORMALE ",
        "DARKER  ": "SCURA   ", "DARKEST ": "SCURISS.",
    },
}

# MONO  /STEREO are identical to the English source in fr/de/it (and ":TYPE"
# in fr), so they carry no override at all -- Strings() already falls
# through to the source with no catalog entry needed.
NO_OP_KEYS = {
    "fr": {"MONO  ", "STEREO", ":TYPE"},
    "de": {"MONO  ", "STEREO"},
    "es": set(),
    "it": {"MONO  ", "STEREO"},
}


class GoldOptionsMenuCorpusOverrideTests(unittest.TestCase):
    def test_all_languages_have_the_expected_corpus_values(self):
        for language in LABELS:
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            expected = {**LABELS[language], **VALUES[language]}
            for source, override in expected.items():
                self.assertIn(source, overrides, (language, source))
                row = overrides[source]
                self.assertEqual(row["override"], override, (language, source))
                self.assertEqual(row["reason"], "engine-original", (language, source))
                self.assertIn("Corpus-confirmed", row["provenance"], (language, source))
                self.assertIn(
                    "gs.options_menu", row["provenance"], (language, source),
                )

    def test_identical_to_source_values_carry_no_pointless_override(self):
        for language, keys in NO_OP_KEYS.items():
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            for key in keys:
                self.assertNotIn(key, overrides, (language, key))


if __name__ == "__main__":
    unittest.main()
