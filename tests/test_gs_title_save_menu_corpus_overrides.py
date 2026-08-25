import unittest
from pathlib import Path

from pipeline.engine import load_engine_overrides


# gs.main_menu.MainMenu.Strings, gs.intro_menu.Continue_LoadMenuHeader.
# MenuData_Dex, gs.menu.YesNoMenuHeader.MenuData, gs.common_2.
# WouldYouLikeToSaveTheGameText/SavingDontTurnOffThePowerText/
# SavedTheGameText/AlreadyASaveFileText, poke-corpus GoldSilver. Verified
# directly against en/fr/de/es/it_msg.txt: the engine's Strings()/
# Strings.source() literals (src/ui/gen2/MainMenu.lua, SaveMenu.lua, fixed
# on gen1recomp branch fix/translate-gold-title-and-save-menus) match the
# ROM's English source, so the "engine-original" entries are real official
# localized phrasing, not compromises. "engine-contract-gap" entries are
# either a port-added row with no cart equivalent at all (EXIT GAME, NO
# SAVE FILE, Could not save.) or an adaptation this port's own call-site
# shape forces (PLAYER %s folds the name into one Chrome.print call rather
# than drawing it separately like the real cart does; the overwrite prompt
# is truncated to the two lines this port's fixed layout has room for,
# matching its own English source's identical truncation).
ENGINE_ORIGINAL = {
    "fr": {
        "CONTINUE": "CONTINUER", "NEW GAME": "NOUVEAU JEU", "OPTION": "OPTIONS",
        "TIME": "DUREE JEU", "YES": "OUI", "NO": "NON", "BADGES": "BADGES",
        "Would you like to\nsave the game?": "Voulez-vous sauve-\ngarder la partie?",
        "SAVING… DON'T TURN\nOFF THE POWER.": "SAUVEGARDE...\nNE PAS ETEINDRE.",
        "%s saved\nthe game.": "%s sauve\nla partie.",
    },
    "de": {
        "CONTINUE": "WEITER", "NEW GAME": "NEUES SPIEL", "OPTION": "OPTIONEN",
        "TIME": "SPIELZEIT", "YES": "JA", "NO": "NEIN", "BADGES": "ORDEN",
        "Would you like to\nsave the game?": "Möchtest du das\nSpiel SICHERN?",
        "SAVING… DON'T TURN\nOFF THE POWER.": "Speichern…",
        "%s saved\nthe game.": "%s hat das\nSpiel gesichert.",
    },
    "es": {
        "CONTINUE": "CONTINUAR", "NEW GAME": "JUEGO NUEVO", "OPTION": "OPCIÓN",
        "TIME": "TIEMPO J.", "YES": "SÍ", "NO": "NO", "BADGES": "MEDALLAS",
        "Would you like to\nsave the game?": "¿Quieres guardar\nel juego?",
        "SAVING… DON'T TURN\nOFF THE POWER.": "GUARDANDO… NO\nAPAGAR LA CONSOLA.",
        "%s saved\nthe game.": "%s guardó\nel juego.",
    },
    "it": {
        "CONTINUE": "CONTINUA", "NEW GAME": "NUOVO GIOCO", "OPTION": "OPZIONI",
        "TIME": "DURATA", "YES": "SÌ", "NO": "NO", "BADGES": "MEDAGLIE",
        "Would you like to\nsave the game?": "Vuoi salvare il\ngioco?",
        "SAVING… DON'T TURN\nOFF THE POWER.": "SALVATAGGIO…\nNON SPEGNERE.",
        "%s saved\nthe game.": "%s ha\nsalvato il gioco.",
    },
}

ENGINE_CONTRACT_GAP = {
    "fr": {
        "EXIT GAME": "QUITTER", "PLAYER %s": "JOUEUR %s", "NO SAVE FILE": "PAS DE SAUVEGARDE",
        "There is already a\nsave file. Is it": "Il y a déjà une\nsauvegarde. La",
        "Could not save.": "Sauvegarde impossible.",
    },
    "de": {
        "EXIT GAME": "SPIEL BEENDEN", "PLAYER %s": "SPIELER %s", "NO SAVE FILE": "KEIN SPIELSTAND",
        "There is already a\nsave file. Is it": "Es gibt bereits\neinen Spielstand.",
        "Could not save.": "Speichern fehlgeschlagen.",
    },
    "es": {
        "EXIT GAME": "SALIR", "PLAYER %s": "JUGADOR %s", "NO SAVE FILE": "SIN GUARDAR",
        "There is already a\nsave file. Is it": "Ya existe un\narchivo guardado.",
        "Could not save.": "No se pudo guardar.",
    },
    "it": {
        "EXIT GAME": "ESCI", "PLAYER %s": "GIOCA %s", "NO SAVE FILE": "NESSUN SALVATAGGIO",
        "There is already a\nsave file. Is it": "C'è già un gioco\nsalvato in",
        "Could not save.": "Salvataggio fallito.",
    },
}

# POKéDEX (#DEX) and AM/PM are identical to the English source in every
# language poke-corpus covers here, so they carry no override at all.
# fr's BADGES and es/it's NO are also identical to their English source
# (unlike German/Spanish/Italian's own real BADGES text -- ORDEN/MEDALLAS/
# MEDAGLIE -- or Spanish/Italian's own accented "SÍ"/"SÌ"), but carry an
# explicit "identical to source" override instead -- see ENGINE_ORIGINAL
# above -- so the coverage report doesn't misreport them as untranslated.
NO_OP_KEYS = {
    "fr": {"POKéDEX", "AM", "PM"},
    "de": {"POKéDEX", "AM", "PM"},
    "es": {"POKéDEX", "AM", "PM"},
    "it": {"POKéDEX", "AM", "PM"},
}


class GoldTitleSaveMenuCorpusOverrideTests(unittest.TestCase):
    def test_engine_original_languages_have_the_expected_corpus_values(self):
        for language, expected in ENGINE_ORIGINAL.items():
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            for source, override in expected.items():
                self.assertIn(source, overrides, (language, source))
                row = overrides[source]
                self.assertEqual(row["override"], override, (language, source))
                self.assertEqual(row["reason"], "engine-original", (language, source))
                self.assertIn("Corpus-confirmed", row["provenance"], (language, source))

    def test_engine_contract_gap_languages_have_the_expected_values(self):
        for language, expected in ENGINE_CONTRACT_GAP.items():
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            for source, override in expected.items():
                self.assertIn(source, overrides, (language, source))
                row = overrides[source]
                self.assertEqual(row["override"], override, (language, source))
                self.assertEqual(row["reason"], "engine-contract-gap", (language, source))

    def test_identical_to_source_values_carry_no_pointless_override(self):
        for language, keys in NO_OP_KEYS.items():
            path = Path("overrides") / language / "gs" / "engine.json"
            overrides = load_engine_overrides(path)
            for key in keys:
                self.assertNotIn(key, overrides, (language, key))


if __name__ == "__main__":
    unittest.main()
