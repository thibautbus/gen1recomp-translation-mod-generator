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
# than drawing it separately like the real cart does; the overwrite prompt's
# English source itself is the real cart's full three-line text as of
# v0.2.55 (no longer truncated), so fr/es carry the full quote too -- de/it
# still drop the real quote's opening clause and keep only its last three
# lines, since their own cart text runs one line longer than the engine's
# fixed three-line budget allows).
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
        "There is already a\nsave file. Is it\x0bOK to overwrite?": "Il y a déjà une\nsauvegarde. La\x0bremplacer?",
        "Could not save.": "Sauvegarde impossible.",
    },
    "de": {
        "EXIT GAME": "SPIEL BEENDEN", "PLAYER %s": "SPIELER %s", "NO SAVE FILE": "KEIN SPIELSTAND",
        "There is already a\nsave file. Is it\x0bOK to overwrite?": "einen Spielstand.\nSpielstand\x0büberschreiben?",
        "Could not save.": "Speichern fehlgeschlagen.",
    },
    "es": {
        "EXIT GAME": "SALIR", "PLAYER %s": "JUGADOR %s", "NO SAVE FILE": "SIN GUARDAR",
        "There is already a\nsave file. Is it\x0bOK to overwrite?": "Ya existe un\narchivo guardado.\x0b¿Sobreescribirlo?",
        "Could not save.": "No se pudo guardar.",
    },
    "it": {
        "EXIT GAME": "ESCI", "PLAYER %s": "GIOCA %s", "NO SAVE FILE": "NESSUN SALVATAGGIO",
        "There is already a\nsave file. Is it\x0bOK to overwrite?": "salvato in\nmemoria. Vuoi\x0bsostituirlo?",
        "Could not save.": "Salvataggio fallito.",
    },
}

# POKéDEX (#DEX) and AM/PM are identical to the English source in every
# language poke-corpus covers here, so they carry no override at all.
# Identical runtime values such as fr's BADGES and es/it's NO are tracked in
# engine_fallbacks.json's no-op policy rather than emitted as overrides.
NO_OP_KEYS = {
    "fr": {"POKéDEX", "AM", "PM"},
    "de": {"POKéDEX", "AM", "PM"},
    "es": {"POKéDEX", "AM", "PM"},
    "it": {"POKéDEX", "AM", "PM"},
}


class GoldTitleSaveMenuCorpusOverrideTests(unittest.TestCase):
    def test_engine_original_languages_have_the_expected_corpus_values(self):
        for language, expected in ENGINE_ORIGINAL.items():
            path = Path("overrides") / language / "gsc" / "engine.json"
            overrides = load_engine_overrides(path)
            no_op = load_engine_no_op_entries(language)
            for source, override in expected.items():
                row = overrides.get(source, no_op.get(source))
                self.assertIsNotNone(row, (language, source))
                self.assertEqual(row["override"], override, (language, source))
                if source in overrides:
                    self.assertEqual(row["reason"], "engine-original", (language, source))
                    self.assertIn("Corpus-confirmed", row["provenance"], (language, source))
                else:
                    self.assertEqual(row["reason"], "engine-fallback", (language, source))
                    self.assertIn("Corpus-confirmed", row["original_provenance"], (language, source))

    def test_engine_contract_gap_languages_have_the_expected_values(self):
        for language, expected in ENGINE_CONTRACT_GAP.items():
            path = Path("overrides") / language / "gsc" / "engine.json"
            overrides = load_engine_overrides(path)
            for source, override in expected.items():
                self.assertIn(source, overrides, (language, source))
                row = overrides[source]
                self.assertEqual(row["override"], override, (language, source))
                self.assertEqual(row["reason"], "engine-contract-gap", (language, source))

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
