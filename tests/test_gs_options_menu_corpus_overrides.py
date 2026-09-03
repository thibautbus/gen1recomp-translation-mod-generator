import json
import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides


def load_engine_no_op_entries(language):
    report = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "gsc" / "engine_fallbacks.json").read_text(
            encoding="utf-8",
        ),
    )
    return report["languages"][language].get("no_op_entries", {})


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
        ":TYPE": ":TYPE",
    },
    "de": {
        "TEXT SPEED": "TEXT-TEMPO", "BATTLE SCENE": "KAMPFANIMATION",
        "BATTLE STYLE": "KAMPFSTIL", "SOUND": "SOUND", "PRINT": "DRUCKEN",
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

# MONO  /STEREO are identical to the English source in fr/de/it, so they
# carry no override at all -- Strings() already falls through to the source
# with no catalog entry needed. Identical corpus values such as fr's ":TYPE"
# and de's "SOUND" are tracked in engine_fallbacks.json's no-op policy.
NO_OP_KEYS = {
    "fr": {"MONO  ", "STEREO"},
    "de": {"MONO  ", "STEREO"},
    "es": set(),
    "it": {"MONO  ", "STEREO"},
}


class GoldOptionsMenuCorpusOverrideTests(unittest.TestCase):
    def test_all_languages_have_the_expected_corpus_values(self):
        for language in LABELS:
            path = Path("overrides") / language / "gsc" / "engine.json"
            overrides = load_engine_overrides(path)
            no_op = load_engine_no_op_entries(language)
            expected = {**LABELS[language], **VALUES[language]}
            for source, override in expected.items():
                row = overrides.get(source, no_op.get(source))
                self.assertIsNotNone(row, (language, source))
                self.assertEqual(row["override"], override, (language, source))
                if source in overrides:
                    self.assertEqual(row["reason"], "engine-original", (language, source))
                    self.assertIn("Corpus-confirmed", row["provenance"], (language, source))
                    self.assertIn(
                        "gs.options_menu", row["provenance"], (language, source),
                    )
                else:
                    self.assertEqual(row["reason"], "engine-fallback", (language, source))
                    self.assertIn("Corpus-confirmed", row["original_provenance"], (language, source))
                    self.assertIn("gs.options_menu", row["original_provenance"], (language, source))

    def test_identical_to_source_values_carry_no_pointless_override(self):
        for language, keys in NO_OP_KEYS.items():
            path = Path("overrides") / language / "gsc" / "engine.json"
            overrides = load_engine_overrides(path)
            no_op = load_engine_no_op_entries(language)
            for key in keys:
                self.assertNotIn(key, overrides, (language, key))
                if key in no_op:
                    self.assertEqual(no_op[key]["override"], key, (language, key))
                    self.assertTrue(no_op[key]["original_provenance"], (language, key))


if __name__ == "__main__":
    unittest.main()
