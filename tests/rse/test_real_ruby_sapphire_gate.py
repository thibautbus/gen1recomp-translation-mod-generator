"""The Ruby and Sapphire pipeline end to end on the real inputs, when this
machine has them: a Ruby and a Sapphire ROM next to the repository (or in
config/rom_paths.toml), the pinned engine, corpus and pret files a build
leaves under .cache/."""
import tempfile
import unittest
from pathlib import Path

from pipeline.rse.join import join_rs
from pipeline.rse.mod import generate_rse_mod, rs_coverage, run_rse_gate
from pipeline.shared.project import which_luajit
from pipeline.shared.rom_paths import configured_path, load_rom_paths
from pipeline.shared.roms import import_rse_rom, rs_revision_for_sha1, verify_rs_rom

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / ".cache" / "dependencies" / "gen1recomp"
CORPUS = ROOT / ".cache" / "dependencies" / "poke-corpus" / "corpus" / "RubySapphire"
SYMBOLS = ROOT / ".cache" / "dependencies" / "pret" / "ruby_sapphire_symbols"
CHARMAP = ROOT / ".cache" / "dependencies" / "pret" / "ruby_sapphire_charmap" / "charmap.txt"


def rom(edition: str, *names: str) -> Path | None:
    configured = configured_path(load_rom_paths(ROOT / "config" / "rom_paths.toml"), "rom", edition)
    for candidate in (configured, *(ROOT.parent / name for name in names)):
        if candidate and Path(candidate).is_file():
            try:
                if verify_rs_rom(candidate)["version"] == edition:
                    return Path(candidate)
            except ValueError:
                continue
    return None


def inputs_ready() -> bool:
    return bool(which_luajit() and (ENGINE / "src").is_dir() and (CORPUS / "qid_msg.txt").is_file()
                and (SYMBOLS / "pokeruby_rev1.sym").is_file() and CHARMAP.is_file())


class RealRubySapphireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ruby = rom("ruby", "Pokemon - Ruby Version (USA, Europe) (Rev 2).gba")
        cls.sapphire = rom("sapphire", "Pokemon - Sapphire Version (USA).gba")
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def join(self, path: Path):
        info = verify_rs_rom(path)
        extracted = Path(self.tmp.name) / info["section"]
        if not (extracted / "rse_text.json").is_file():
            import_rse_rom(path, ENGINE, extracted, game="rs")
        return extracted, info, join_rs(extracted, CORPUS, "fr", SYMBOLS, CHARMAP,
                                        rs_revision_for_sha1(info["sha1"]))

    def test_extract_join_and_runtime_gate(self):
        path = self.ruby or self.sapphire
        if not (path and inputs_ready()):
            self.skipTest("Ruby or Sapphire ROM, pinned engine, corpus, pret files or LuaJIT unavailable")
        extracted, info, joined = self.join(path)
        coverage = rs_coverage(joined)
        # nearly every extracted line has its official French row
        self.assertGreater(coverage["rom"]["percent"], 99.0)
        self.assertGreater(coverage["engine_gen3"]["percent"], 97.0)
        mod_dir = Path(self.tmp.name) / "mods" / "translation-fr-gen3-rse"
        generate_rse_mod(mod_dir, language="fr", target_name="French", rs=joined)
        gate = run_rse_gate(mod_dir, extracted, joined, ENGINE, which_luajit(), game=info["version"],
                            rom_sha1=info["sha1"])
        self.assertEqual(gate["failures"], 0)
        self.assertEqual(gate["blank_glyphs"]["total"], 0)

    def test_the_other_editions_layers_match_its_own_cart(self):
        if not (self.ruby and self.sapphire and inputs_ready()):
            self.skipTest("a Ruby and a Sapphire ROM, the pinned engine, corpus, pret files or LuaJIT unavailable")
        _ruby_dir, ruby_info, from_ruby = self.join(self.ruby)
        _sapphire_dir, sapphire_info, from_sapphire = self.join(self.sapphire)
        for read, derived, info in ((from_sapphire, from_ruby, sapphire_info), (from_ruby, from_sapphire, ruby_info)):
            edition, layout = info["version"], info["layout"]
            with self.subTest(edition=edition):
                self.assertEqual(derived["named"][edition], read["named"][edition])
                own, keyed = read["script"][f"{edition}_{layout}"], derived["script"][f"{edition}_{layout}"]
                self.assertEqual({key: keyed[key] for key in own if key in keyed},
                                 {key: own[key] for key in own if key in keyed})
                # the few texts only that edition prints, under a label the
                # collection has no row for, stay in English
                self.assertLess(len(set(own) - set(keyed)), 15)

    def test_a_mod_built_from_one_edition_runs_on_the_other(self):
        if not (self.ruby and self.sapphire and inputs_ready()):
            self.skipTest("a Ruby and a Sapphire ROM, the pinned engine, corpus, pret files or LuaJIT unavailable")
        _ruby_dir, _ruby_info, from_ruby = self.join(self.ruby)
        sapphire_dir, sapphire_info, _from_sapphire = self.join(self.sapphire)
        mod_dir = Path(self.tmp.name) / "cross" / "translation-fr-gen3-rse"
        generate_rse_mod(mod_dir, language="fr", target_name="French", rs=from_ruby)
        # the gate samples the Sapphire cart's layers of the mod built from Ruby
        target = rs_revision_for_sha1(sapphire_info["sha1"])
        layout = f"sapphire_{target.layout}"
        view = dict(from_ruby, source=target,
                    dialogue={**from_ruby["named"]["sapphire"], **from_ruby["script"][layout]})
        gate = run_rse_gate(mod_dir, sapphire_dir, view, ENGINE, which_luajit(), game="sapphire",
                            rom_sha1=sapphire_info["sha1"])
        self.assertEqual(gate["failures"], 0)


if __name__ == "__main__":
    unittest.main()
