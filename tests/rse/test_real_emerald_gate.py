"""The Emerald pipeline end to end on the real inputs, when this machine has
them: the canonical ROM next to the repository (or in config/rom_paths.toml),
the pinned engine, corpus and pret files a build leaves under .cache/."""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.gen3.mod import gen3_coverage
from pipeline.rse.emerald import join_emerald
from pipeline.rse.mod import generate_rse_mod, run_rse_gate
from pipeline.shared.project import which_luajit
from pipeline.shared.rom_paths import configured_path, load_rom_paths
from pipeline.shared.roms import import_rse_rom

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / ".cache" / "dependencies" / "gen1recomp"
CORPUS = ROOT / ".cache" / "dependencies" / "poke-corpus" / "corpus" / "Emerald"
SYMBOLS = ROOT / ".cache" / "dependencies" / "pret" / "emerald_symbols" / "pokeemerald.sym"
CHARMAP = ROOT / ".cache" / "dependencies" / "pret" / "emerald_charmap" / "charmap.txt"


def emerald_rom() -> Path | None:
    configured = configured_path(load_rom_paths(ROOT / "config" / "rom_paths.toml"), "rom", "emerald")
    for candidate in (configured, ROOT.parent / "Pokemon - Emerald Version (USA, Europe).gba"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class RealEmeraldTests(unittest.TestCase):
    def test_extract_join_and_runtime_gate(self):
        rom, luajit = emerald_rom(), which_luajit()
        if not (rom and luajit and (ENGINE / "src").is_dir() and (CORPUS / "qid_msg.txt").is_file()
                and SYMBOLS.is_file() and CHARMAP.is_file()):
            self.skipTest("Emerald ROM, pinned engine, corpus, pret files or LuaJIT unavailable")
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory) / "extracted"
            import_rse_rom(rom, ENGINE, extracted)
            joined = join_emerald(extracted, CORPUS, "fr", SYMBOLS, CHARMAP)
            coverage = gen3_coverage(joined)
            # nearly every extracted line has its official French row
            self.assertGreater(coverage["rom"]["percent"], 99.0)
            # 98.5, not 99: the 25 cross-game Union Room rows of gen1recomp
            # v0.3.61 keep their English until a reviewed translation lands
            self.assertGreater(coverage["engine_gen3"]["percent"], 98.5)
            pointers = json.loads((extracted / "rse_text_pointers.json").read_text(encoding="utf-8"))
            self.assertIn("STRINGID_ATTACKMISSED", pointers)
            self.assertIn("gNatureNamePointers[24]", pointers)
            mod_dir = Path(directory) / "mods" / "translation-fr-gen3-rse"
            generate_rse_mod(mod_dir, language="fr", target_name="French", emerald=joined)
            gate = run_rse_gate(mod_dir, extracted, joined, ENGINE, luajit)
            self.assertEqual(gate["failures"], 0)
            self.assertEqual(gate["blank_glyphs"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
