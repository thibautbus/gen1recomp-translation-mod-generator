"""Ruby and Sapphire, the rse release's base game (pipeline/rse/join.py)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline.gen3.family import EMERALD, RUBY_SAPPHIRE
from pipeline.gen3.join import (
    CART_EMPTY, TRANSLATED, join_gen3_dialogue, join_gen3_engine_strings, load_dialogue_decisions,
    load_engine_scope, load_gen3_corpus,
)
from pipeline.gen3.text import RS_DIALECT, corpus_ir, encode, load_charmap
from pipeline.rse.join import (
    derive_layout_text, japanese_single_page_entries, keyed_by_layout, layout_guards, other_edition_script_text,
)
from pipeline.rse.mod import generate_rse_mod, write_gate_expectations
from pipeline.shared.project import project_config
from pipeline.shared.roms import CANONICAL, RS_REVISIONS, rs_revision_for_sha1, verify_rs_rom
from pipeline.shared.specs import game_spec, release_profile

ROOT = Path(__file__).resolve().parents[2]

# The pokeruby charmap lines the tests exercise (no B_* names: pokeruby
# writes its battle placeholders as bare FD escapes).
CHARMAP = """\
' '         = 00
'!'         = AB
'.'         = AD
'-'         = AE
""" + "".join(f"'{chr(ord('A') + i)}'         = {0xBB + i:02X}\n" for i in range(26)) \
    + "".join(f"'{chr(ord('a') + i)}'         = {0xD5 + i:02X}\n" for i in range(26)) + """\
'あ' = 01
PLAYER         = FD 01
STR_VAR_1      = FD 02
KUN            = FD 05
RIVAL          = FD 06
EVIL_TEAM      = FD 08 @ "MAGMA"   / "AQUA"
COLOR = FC 01
NAME_END = FC 00
'\\l' = FA
'\\p' = FB
'\\n' = FE
"""


def text(value: str) -> dict:
    return {"t": "text", "s": value}


EOS = {"t": "eos"}


def write_corpus(directory: Path, language: str, rows: list[tuple[str, str, str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, column in (("qid", 0), ("en", 1), (language, 2)):
        (directory / f"{name}_msg.txt").write_text(
            "\n".join(row[column] for row in rows) + "\n", encoding="utf-8")
    return directory


class RubySapphireTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        path = self.tmp / "charmap.txt"
        path.write_text(CHARMAP, encoding="utf-8")
        self.charmap = load_charmap(path, RS_DIALECT)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def corpus(self, rows, language="fr"):
        return load_gen3_corpus(write_corpus(self.tmp / language, language, rows), language, RUBY_SAPPHIRE)

    def test_the_rs_dialect_names_its_version_placeholders(self):
        self.assertEqual(corpus_ir("TEAM [EVIL_TEAM]!", self.charmap),
                         [text("TEAM "), {"t": "ph", "code": 8, "name": "EVIL_TEAM"}, text("!"), EOS])

    def test_battle_placeholders_take_ruby_and_sapphires_codes(self):
        # pokeruby/include/battle_message.h:24, B_TXT_OPPONENT_MON1_NAME 0x3
        self.assertEqual(encode("[B_OPPONENT_MON1_NAME]", self.charmap), b"\xfd\x03")
        self.assertEqual(corpus_ir("Wild [B_OPPONENT_MON1_NAME]!", self.charmap, battle=True),
                         [text("Wild "), {"t": "bph", "code": 3}, text("!"), EOS])

    def test_a_map_names_short_form_mark_is_not_printed(self):
        # PokeCorpus writes NAME_END as [NOP]; the screens print the whole name
        corpus = self.corpus([("rs.common.region_map_entries.sMapName_LITTLEROOTNAMEENDTOWN",
                               "LITTLEROOT[NOP] TOWN", "BOURG[NOP]-EN-VOL")])
        scope = {"LITTLEROOT TOWN": {"callsite": "x",
                                     "qid": "rs.common.region_map_entries.sMapName_LITTLEROOTNAMEENDTOWN"}}
        values, _stats = join_gen3_engine_strings(scope, corpus, self.charmap, root=self.tmp)
        self.assertEqual(values, {"LITTLEROOT TOWN": "BOURG-EN-VOL"})

    def test_each_edition_reads_its_own_row_of_a_shared_label(self):
        corpus = self.corpus([
            ("rs.common.pokedex_entries^R.DexDescription_Paras_2", "Ruby line.", "Ligne rubis."),
            ("rs.common.pokedex_entries^S.DexDescription_Paras_2", "Sapphire line.", "Ligne saphir."),
            ("rs.common.credits.Text_Version^R", "RUBY", "RUBIS"),
            ("rs.common.credits.Text_Version^S", "SAPPHIRE", "SAPHIR"),
        ])
        for edition, dex, version in (("ruby", "Ruby line.", "RUBY"), ("sapphire", "Sapphire line.", "SAPPHIRE")):
            with self.subTest(edition=edition):
                entries, _stats = join_gen3_dialogue(
                    {"DexDescription_Paras_2": [text(dex), EOS], "Text_Version": [text(version), EOS]},
                    corpus, {}, self.charmap, edition=edition)
                self.assertEqual([entry.status for entry in entries], [TRANSLATED, TRANSLATED])
        entries, _stats = join_gen3_dialogue({"Text_Version": [text("SAPPHIRE"), EOS]}, corpus, {},
                                             self.charmap, edition="sapphire")
        self.assertEqual(entries[0].translation, [text("SAPHIR"), EOS])

    def test_japanese_rows_name_some_escapes_after_emeralds_table(self):
        for japanese, english in (("B_COPY_VAR_1", "B_PLAYER_MON1_NAME"), ("B_COPY_VAR_2", "B_OPPONENT_MON1_NAME"),
                                  ("B_COPY_VAR_3", "B_PLAYER_MON2_NAME"), ("B_TRAINER1_WIN_TEXT", "B_DEF_PREFIX1"),
                                  ("B_26", "B_ATK_PREFIX2")):
            self.assertEqual(encode(f"[{japanese}]", self.charmap), encode(f"[{english}]", self.charmap))

    def test_the_sound_effect_wait_is_pokerubys_unknown_a(self):
        self.assertEqual(encode("[WAIT_SE]", self.charmap), bytes((0xFC, 0x0A)))
        self.assertEqual(encode("[B_BUFF1] grew to\\nLV. [B_BUFF2]![WAIT_SE]\\c", self.charmap)[-3:],
                         bytes((0xFC, 0x0A, 0xFB)))

    def test_a_japanese_pokedex_entry_is_one_page(self):
        corpus = japanese_single_page_entries(self.corpus([
            ("rs.common.pokedex_entries^R.DexDescription_Treecko_1", "First half.", "ぜんぶ"),
            ("rs.common.pokedex_entries^R.DexDescription_Treecko_2", "Second half.", "[NULL]"),
        ], "ja-Hrkt"))
        entries, _stats = join_gen3_dialogue(
            {"DexDescription_Treecko_2": [text("Second half."), EOS]}, corpus, {}, self.charmap, edition="ruby")
        self.assertEqual((entries[0].status, entries[0].translation), (CART_EMPTY, [EOS]))

    def test_another_layouts_text_is_keyed_through_its_symbols(self):
        corpus = self.corpus([
            ("rs.script.MtChimney.MtChimney_Text_Sapphire", "Aqua only.", "Aqua seulement."),
            ("rs.script.MtChimney.MtChimney_Text_Unknown", "Nowhere.", "Nulle part."),
            ("rs.common.credits.Text_Version^R", "RUBY", "RUBIS"),
            ("rs.common.credits.Text_Version^S", "SAPPHIRE", "SAPHIR"),
        ])
        ruby = {0x08100000: ["Route_Text_Hello"], 0x08100010: ["MtChimney_Text_Ruby"]}
        sapphire = {0x08200000: ["Route_Text_Hello"], 0x08200020: ["MtChimney_Text_Sapphire"],
                    0x08200040: ["MtChimney_Text_Ruby"]}
        read = {"g3:08100000": [text("Hello"), EOS], "g3:08100010": [text("Magma only."), EOS],
                "Text_Version": [text("RUBY"), EOS]}
        derived = derive_layout_text(read, ruby, sapphire, corpus, self.charmap,
                                     source_edition="ruby", target_edition="sapphire")
        self.assertEqual(derived["g3:08200000"], [text("Hello"), EOS])
        # a label the collection splits by edition reads the other edition's English
        self.assertEqual(derived["Text_Version"], [text("SAPPHIRE"), EOS])
        self.assertNotIn("g3:08200020", derived)
        # the text only Sapphire's scripts print comes from its corpus row
        exclusive = other_edition_script_text(read, ruby, sapphire, corpus, self.charmap)
        self.assertEqual(exclusive, {"g3:08200020": [text("Aqua only."), EOS]})
        # a label Sapphire's symbols do not have is not invented
        self.assertNotIn("MtChimney_Text_Unknown", json.dumps(exclusive))
        same = derive_layout_text(read, ruby, {0x08300000: ["Route_Text_Hello"]}, corpus, self.charmap,
                                  source_edition="ruby", target_edition="ruby")
        self.assertEqual(same, {"g3:08300000": [text("Hello"), EOS], "Text_Version": [text("RUBY"), EOS]})

    def test_reviewed_entries_follow_a_label_to_each_layouts_address(self):
        keyed = keyed_by_layout({"Some_Text": "rs.script.x.Some_Text"}, {0x08000004: ["Some_Text"]},
                                {"STRINGID_SOME": ("Some_Text",), "STRINGID_OTHER": ("Other_Text",)})
        self.assertEqual(keyed, {"Some_Text": "rs.script.x.Some_Text", "g3:08000004": "rs.script.x.Some_Text",
                                 "STRINGID_SOME": "rs.script.x.Some_Text"})

    def test_a_guard_address_tells_the_revisions_apart(self):
        tables = {
            ("ruby", "1_0"): {0x10: ["A_Text"], 0x20: ["B_Text"]},
            ("ruby", "1_1"): {0x10: ["B_Text"], 0x30: ["A_Text"]},
            ("sapphire", "1_0"): {0x50: ["A_Text"], 0x60: ["B_Text"]},
            ("sapphire", "1_1"): {0x50: ["B_Text"], 0x70: ["A_Text"]},
        }
        text_by_key = {"g3:00000010": [text("Bee"), EOS], "g3:00000030": [text("Ay"), EOS]}
        guards = layout_guards(text_by_key, tables[("ruby", "1_1")], tables)
        self.assertEqual(guards["ruby"], [
            {"dir": "ruby_1_1", "key": "g3:00000010", "text": "Bee"},
            {"dir": "ruby_1_0", "key": "g3:00000010", "text": "Ay"},
        ])
        self.assertEqual(guards["sapphire"][0], {"dir": "sapphire_1_1", "key": "g3:00000050", "text": "Bee"})
        # a text a revision rewords never serves as a guard
        with self.assertRaisesRegex(ValueError, "1.0 from 1.1/1.2"):
            layout_guards(text_by_key, tables[("ruby", "1_1")], tables, skip_labels=["B_Text"])

    def test_engine_strings_take_emeralds_for_screens_their_carts_never_had(self):
        corpus = self.corpus([("rs.common.strings.OtherText_Yes", "YES", "OUI")])
        scope = {"YES": {"callsite": "x", "qid": "rs.common.strings.OtherText_Yes"},
                 "JOIN GROUP": {"callsite": "x"}, "VSYNC": {"callsite": "x"}}
        values, stats = join_gen3_engine_strings(scope, corpus, self.charmap, root=self.tmp,
                                                 companion={"JOIN GROUP": "JOINDRE", "YES": "PERDU"})
        self.assertEqual(values, {"YES": "OUI", "JOIN GROUP": "JOINDRE"})
        self.assertEqual(stats["details"], {"YES": "corpus", "JOIN GROUP": "companion", "VSYNC": "fallback_english"})


class RubySapphireModTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def joined(self):
        source = next(revision for revision in RS_REVISIONS if revision.section == "ruby_rev2")
        guards = {edition: [{"dir": f"{edition}_1_1", "key": "g3:08000010", "text": "Bee"},
                            {"dir": f"{edition}_1_0", "key": "g3:08000010", "text": "Ay"}]
                  for edition in ("ruby", "sapphire")}
        script = {f"{edition}_{layout}": {"g3:08000010": [text("Abeille "), {"t": "player"}, EOS]}
                  for edition in ("ruby", "sapphire") for layout in ("1_0", "1_1")}
        named = {edition: {"STRINGID_ATTACKMISSED": [text("Raté !"), EOS]} for edition in ("ruby", "sapphire")}
        return {"source": source, "guards": guards, "script": script, "named": named,
                "dialogue": {**named["ruby"], **script["ruby_1_1"]},
                "catalogs": {"species_names": {"TREECKO": "ARCKO"}, "strings": {"YES": "OUI"}},
                "numbers": {"species": {252: "TREECKO"}, "items": {}, "moves": {}}, "scope": {}}

    def test_one_mod_carries_every_games_layers(self):
        mod = generate_rse_mod(self.tmp / "mod", language="fr", target_name="French", rs=self.joined(),
                               emerald={"dialogue": {"g3:08100000": [text("x"), EOS]},
                                        "catalogs": {"strings": {"YES": "OUI"}}})
        for path in ("rs/layouts.lua", "rs/species_names.lua", "rs/strings.lua", "ruby/dialogue.lua",
                     "sapphire/dialogue.lua", "ruby_1_0/dialogue.lua", "sapphire_1_1/dialogue.lua",
                     "emerald/dialogue.lua", "emerald/strings.lua"):
            self.assertTrue((mod / "lang" / path).is_file(), path)
        layouts = (mod / "lang" / "rs" / "layouts.lua").read_text(encoding="utf-8")
        self.assertIn('dir = "ruby_1_1", key = "g3:08000010", text = "Bee"', layouts)
        main = (mod / "main.lua").read_text(encoding="utf-8")
        self.assertIn('mod.content.text:get(layout.key)', main)
        self.assertIn('each("species_names"', main)

    def test_the_launcher_reads_the_strings_every_game_agrees_on(self):
        joined = self.joined()
        joined["catalogs"] = {"strings": {"YES": "OUI", "WINDOWED": "FENÊTRE", "RS ONLY": "R"}}
        mod = generate_rse_mod(self.tmp / "mod", language="fr", target_name="French", rs=joined,
                               emerald={"dialogue": {}, "catalogs": {"strings": {
                                   "YES": "OUI", "WINDOWED": "FENÊTRE", "RS ONLY": "E"}}})
        launcher = (mod / "lang" / "strings.lua").read_text(encoding="utf-8")
        self.assertIn('["WINDOWED"] = "FENÊTRE"', launcher)
        self.assertNotIn("RS ONLY", launcher)

    def test_main_lua_picks_each_games_layers(self):
        luajit = shutil.which("luajit")
        if not luajit:
            self.skipTest("LuaJIT unavailable")
        joined = self.joined()
        joined["script"] = {f"{edition}_{layout}": {f"g3:0800{layout}": [text("x"), EOS]}
                            for edition in ("ruby", "sapphire") for layout in ("1_0", "1_1")}
        mod = generate_rse_mod(self.tmp / "mod", language="fr", target_name="French", rs=joined,
                               emerald={"dialogue": {"g3:08100000": [text("x"), EOS]}, "catalogs": {}})
        probe = """
local dir, game, guard = ...
package.preload["src.core.GameVersion"] = function() return { get = function() return game end } end
local applied = {}
local mod = {
  read = function(_, path)
    local f = io.open(dir .. "/" .. path, "rb"); if not f then return nil end
    local body = f:read("*a"); f:close(); return body
  end,
  content = setmetatable({ text = {
    get = function(_, key) return guard ~= "" and { { t = "text", s = guard }, { t = "eos" } } or nil end,
    override = function(_, key, ir) applied[#applied + 1] = key end,
  } }, { __index = function() return { override = function() end, patch = function() end } end }),
}
local f = io.open(dir .. "/main.lua", "rb"); local main = assert(loadstring(f:read("*a"))); f:close()
main()(mod)
table.sort(applied)
io.write(table.concat(applied, ","))
"""
        script = self.tmp / "probe.lua"
        script.write_text(probe, encoding="utf-8")

        def applied(game, guard):
            out = subprocess.run([luajit, str(script), str(mod), game, guard], capture_output=True, text=True,
                                 check=True).stdout
            return out.split(",")

        # the guard's text picks the layout; none matching picks 1.1/1.2's
        self.assertEqual(applied("ruby", "Ay"), ["STRINGID_ATTACKMISSED", "g3:08001_0"])
        self.assertEqual(applied("sapphire", "Bee"), ["STRINGID_ATTACKMISSED", "g3:08001_1"])
        self.assertEqual(applied("ruby", "something else"), ["STRINGID_ATTACKMISSED", "g3:08001_1"])
        self.assertEqual(applied("emerald", ""), ["g3:08100000"])
        self.assertEqual(applied("firered", ""), ["g3:08100000"])

    def test_gate_expectations_name_the_layers_of_the_cart_read(self):
        expectations = write_gate_expectations(self.tmp / "gate.json", self.joined(), "ruby")
        self.assertEqual((expectations["dialogue_layers"], expectations["catalog_layer"]),
                         (["ruby", "ruby_1_1"], "rs"))
        self.assertEqual(expectations["layout"]["key"], "g3:08000010")
        self.assertIn("layout", expectations["required"])
        self.assertNotIn("hooks", expectations)


class RubySapphireConfigTests(unittest.TestCase):
    def test_japanese_ships_the_honorifics_the_us_cart_leaves_empty(self):
        entries = json.loads((ROOT / "overrides" / "ja-Hrkt" / "rse" / "dialogue.json").read_text(encoding="utf-8"))["entries"]
        for label, honorific in (("gExpandedPlaceholder_Kun", "くん"), ("gExpandedPlaceholder_Chan", "ちゃん")):
            self.assertEqual(entries[label]["text"], honorific)
            self.assertEqual(entries[label]["qid"], f"rs.common.strings.{label}")

    def test_every_english_revision_is_accepted(self):
        sections = {revision.section: revision for revision in RS_REVISIONS}
        self.assertEqual(sorted(sections), ["ruby", "ruby_rev1", "ruby_rev2", "sapphire", "sapphire_rev1",
                                            "sapphire_rev2"])
        for section, revision in sections.items():
            with self.subTest(section=section):
                self.assertIs(rs_revision_for_sha1(CANONICAL[section]), revision)
                self.assertEqual(revision.layout, "1_0" if revision.revision == "1.0" else "1_1")
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "ruby.gba"
            rom.write_bytes(b"not a rom")
            with self.assertRaisesRegex(ValueError, "not an English Ruby or Sapphire ROM"):
                verify_rs_rom(rom)

    def test_release_profile(self):
        self.assertEqual(release_profile("rse").games, ("rs", "emerald"))
        self.assertEqual(game_spec("rs").corpus_collection, "RubySapphire")

    def test_family_files(self):
        # the base game's files carry no prefix, the companion's are named
        self.assertEqual(RUBY_SAPPHIRE.config_path(ROOT, "engine_scope.json"),
                         ROOT / "config" / "rse" / "engine_scope.json")
        self.assertEqual(EMERALD.config_path(ROOT, "engine_scope.json"),
                         ROOT / "config" / "rse" / "emerald_engine_scope.json")
        pret = project_config()["pret"]
        self.assertEqual(sorted(pret["ruby_sapphire_symbols"]["archive_files"]),
                         ["pokeruby.sym", "pokeruby_rev1.sym", "pokesapphire.sym", "pokesapphire_rev1.sym"])

    def test_revision_decisions_pick_each_editions_row(self):
        ruby = load_dialogue_decisions(RUBY_SAPPHIRE, edition="ruby")
        sapphire = load_dialogue_decisions(RUBY_SAPPHIRE, edition="sapphire")
        self.assertEqual(ruby["DexDescription_Paras_2"], "rs.common.pokedex_entries^R.DexDescription_Paras_2")
        self.assertEqual(sapphire["DexDescription_Paras_2"], "rs.common.pokedex_entries^S.DexDescription_Paras_2")
        self.assertEqual(ruby["DecorDesc_TOGEPI_DOLL"], sapphire["DecorDesc_TOGEPI_DOLL"])

    def test_engine_scope_matches_the_pinned_engine(self):
        engine = ROOT / ".cache" / "dependencies" / "gen1recomp"
        extracted = ROOT / ".cache" / "ruby" / "extracted" / "cache"
        if not (engine / "src").is_dir() or not shutil.which("luajit") or not (extracted / "data").is_dir():
            self.skipTest("pinned gen1recomp checkout, LuaJIT or Ruby extract unavailable")
        from pipeline.gen3.engine_scope import collect_keys
        keys = collect_keys(engine, extracted, RUBY_SAPPHIRE)
        scope = load_engine_scope(RUBY_SAPPHIRE)
        self.assertEqual(sorted(set(keys) - set(scope)), [], "engine keys missing from the scope")
        self.assertEqual(sorted(set(scope) - set(keys)), [], "scope keys the engine no longer has")


if __name__ == "__main__":
    unittest.main()
