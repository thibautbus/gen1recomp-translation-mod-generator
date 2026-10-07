import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline.frlg.mod import generate_frlg_mod
from pipeline.gsc.mod import generate_gs_mod
from pipeline.rse.mod import generate_rse_mod
from pipeline.shared.builder import BuildError
from pipeline.shared.pokedex_metrics import (
    GEN3_SPECIES_METRICS_HOOK, SPECIES_METRICS_HOOK, lua_catalog, parse_pokedex_metrics, species_metrics,
)


ENTRIES = """const struct PokedexEntry gPokedexEntries[] =
{
    [NATIONAL_DEX_NONE] =
    {
        .categoryName = _("UNKNOWN"),
        .height = 0,
        .weight = 0,
    },

    [NATIONAL_DEX_BULBASAUR] =
    {
        .categoryName = _("SEED"),
        .height = 7,
        .weight = 69,
        .description = gBulbasaurPokedexText,
    },

    [NATIONAL_DEX_NIDORAN_F] =
    {
        .categoryName = _("POISON PIN"),
        .height = 4,
        .weight = 70,
    },

    [NATIONAL_DEX_MR_MIME] =
    {
        .categoryName = _("BARRIER"),
        .height = 13,
        .weight = 545,
    },
};
"""


class PokedexMetricsTest(unittest.TestCase):
    def test_parses_decimetres_and_hectograms_by_national_dex_name(self):
        self.assertEqual(parse_pokedex_metrics(ENTRIES), {"BULBASAUR": (7, 69), "NIDORAN_F": (4, 70), "MR_MIME": (13, 545)})

    def test_species_metrics_converts_to_metres_and_kilograms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pokedex_entries.h"
            path.write_text(ENTRIES, encoding="utf-8")
            self.assertEqual(species_metrics(path, ["BULBASAUR", "NIDORAN_F"]),
                             {"BULBASAUR": (0.7, 6.9), "NIDORAN_F": (0.4, 7.0)})
            with self.assertRaisesRegex(BuildError, "MEW"):
                species_metrics(path, ["BULBASAUR", "MEW"])
            # Gold's ids spell the English name with an underscore per sign
            self.assertEqual(species_metrics(path, ["MR__MIME"]), {"MR__MIME": (1.3, 54.5)})

    def test_lua_catalog_lists_metres_then_kilograms(self):
        body = lua_catalog({"NIDORAN_F": (0.4, 7.0), "BULBASAUR": (0.7, 6.9)}, "species_metrics")
        self.assertIn('  ["BULBASAUR"] = { 0.7, 6.9 },\n  ["NIDORAN_F"] = { 0.4, 7 },\n', body)



METRICS = {"BULBASAUR": (0.7, 6.9)}


class SpeciesMetricsHookTest(unittest.TestCase):
    """Every generation's mod patches its species with the metric values."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def assertShipsMetrics(self, mod, hook=GEN3_SPECIES_METRICS_HOOK):
        self.assertIn('["BULBASAUR"] = { 0.7, 6.9 },',
                      (mod / "lang" / "species_metrics.lua").read_text(encoding="utf-8"))
        self.assertIn(hook, (mod / "main.lua").read_text(encoding="utf-8"))

    def test_gold_silver_and_crystal(self):
        mod = generate_gs_mod(self.tmp / "mod", language="fr", species_metrics=METRICS)
        self.assertShipsMetrics(mod, SPECIES_METRICS_HOOK)
        self.assertNotIn("src.core.game3", (mod / "main.lua").read_text(encoding="utf-8"))

    def test_firered_and_leafgreen(self):
        self.assertShipsMetrics(generate_frlg_mod(
            self.tmp / "mod", language="fr", target_name="French", dialogue={},
            catalogs={"species_names": {"BULBASAUR": "BULBIZARRE"}}, species_metrics=METRICS))

    def test_ruby_sapphire_and_emerald_share_one_file(self):
        mod = generate_rse_mod(self.tmp / "mod", language="fr", target_name="French",
                               emerald={"dialogue": {}, "catalogs": {}}, species_metrics=METRICS)
        self.assertShipsMetrics(mod)

    def probe(self, mod):
        luajit = shutil.which("luajit")
        if not luajit:
            self.skipTest("LuaJIT unavailable")
        probe = """
local dir, units = ...
if units == "yes" then package.preload["src.core.game3.pokedex_units"] = function() return {} end end
local patched = {}
local registry = setmetatable({ patch = function(_, id, value) patched[#patched + 1] = id .. "=" .. value.dexEntry.heightM .. "/" .. value.dexEntry.weightKg end },
                               { __index = function() return function() end end })
local mod = { content = setmetatable({}, { __index = function() return registry end }),
              read = function(_, path) local f = io.open(dir .. "/" .. path) if not f then return nil end
                                       local body = f:read("*a") f:close() return body end }
dofile(dir .. "/main.lua")(mod)
io.write(table.concat(patched, ","))
"""
        script = self.tmp / "probe.lua"
        script.write_text(probe, encoding="utf-8")
        return lambda units: subprocess.run([luajit, str(script), str(mod), units], capture_output=True,
                                            text=True, check=True).stdout

    def test_gen3_patches_only_an_engine_that_prints_them(self):
        run = self.probe(generate_frlg_mod(self.tmp / "mod", language="fr", target_name="French", dialogue={},
                                           catalogs={}, species_metrics=METRICS))
        self.assertEqual(run("yes"), "BULBASAUR=0.7/6.9")
        self.assertEqual(run("no"), "")

    def test_gold_and_silver_patch_without_probing_a_gen3_module(self):
        run = self.probe(generate_gs_mod(self.tmp / "mod", language="fr", species_metrics=METRICS))
        self.assertEqual(run("yes"), "BULBASAUR=0.7/6.9")
        self.assertEqual(run("no"), "BULBASAUR=0.7/6.9")


if __name__ == "__main__":
    unittest.main()
