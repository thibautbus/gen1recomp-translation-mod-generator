-- Proof the FireRed translation mod reaches what the game3 runtime reads,
-- through gen1recomp's real generation-3 mod loader (tests.modkit's SDK,
-- the seam its own Gen 3 registry tests use), on top of the real game3 data
-- modules loaded from a private FireRed extract -- not a fixture.
--
-- Checks, for one sample per catalog the build hands over in <expectations>:
--   * the mod loads with no error under GameVersion "firered";
--   * dialogue: data.gen3Text (the live Space.bundle.text a game3 boot
--     exposes) holds the translated IR, and TextIR.toTextBox renders it;
--   * species/move/item/trainer names, item descriptions and trainer class
--     names are read back through the same module
--     APIs the game3 UI calls (Pokemon.name, ItemsData.info, Trainers.get...);
--   * the strings registry holds the translated engine string.
--
-- It also measures, without failing, three runtime limits the translation mod
-- cannot fix itself (docs/upstream-fixes.md, FireRed section):
--   * persistence: whether the name patches survive the second
--     Pokemon.install(cache) that Runtime.start performs on entering the
--     field;
--   * glyphs: how many characters of the shipped catalogs FrlgFont.glyphId
--     maps to glyph 0 (a blank) although the ROM font has them;
--   * strings: whether Strings() answers from the merged catalog at all.
--
-- Usage: luajit tools/frlg/gate.lua <gen1recomp_root> <extract_cache_dir> <mod_dir>
--                             <expectations.json> <report.json>

local engineRoot, cacheDir, modDir, expectationPath, reportPath = ...
if not (engineRoot and cacheDir and modDir and expectationPath and reportPath) then
  io.stderr:write("usage: luajit tools/frlg/gate.lua <gen1recomp_root> <extract_cache_dir> "
    .. "<mod_dir> <expectations.json> <report.json>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path
love = require("tests.love_stub")

local Json = require("src.link.Json")
local FileIO = require("src.import.gba.file_io")
local GameVersion = require("src.core.GameVersion")
GameVersion.set("firered")

local function readFile(path)
  local file = io.open(path, "rb")
  if not file then return nil end
  local body = file:read("*a")
  file:close()
  return body
end

local expectationBody = assert(readFile(expectationPath), "missing expectations")
local expectations = assert(Json.decode(expectationBody), "invalid expectations")

local failures = 0
local function check(condition, message)
  if condition then
    print("ok - " .. message)
  else
    failures = failures + 1
    io.stderr:write("FAIL - " .. message .. "\n")
  end
end

local function eq(actual, expected, message)
  check(actual == expected,
    ("%s (got %q, want %q)"):format(message, tostring(actual), tostring(expected)))
end

-- Every game3 data module reads its pack through Dataset.cache(); point it
-- at the private extract.
local cache = FileIO.makeCache(cacheDir)
package.loaded["src.core.game3.dataset"] = {
  cache = function() return cache end,
  mountExtractRoots = function() end,
}

local Pokemon = require("src.core.game3.pokemon")
Pokemon.install(cache)
local Moves = require("src.core.game3.battle.moves")
if not Moves._romLoaded then pcall(Moves.loadRomPack, cache) end
local ItemsData = require("src.core.game3.items_data")
pcall(ItemsData.ensureLoaded)
local Trainers = require("src.core.game3.scripting.trainers")
local ExtractScripts = require("src.import.gba.extract_scripts")
local bundle = ExtractScripts.loadBundle(cache, "data/generated/gba", { allowIncomplete = true })

-- The same data table Game3:_exposeModData builds before mods load.
local data = {
  maps = {}, tilesets = {},
  gen3Pokemon = Pokemon,
  gen3Moves = Moves,
  gen3Items = ItemsData,
  gen3Encounters = require("src.core.game3.encounters")._tables,
  gen3Trainers = Trainers.pack(),
  gen3Text = bundle and bundle.text,
  gen3Scripts = bundle and bundle.scripts,
}
check(type(data.gen3Text) == "table", "the extracted text bundle loads")

-- A trainers patch rewrites the whole row through G3.trainerRecord /
-- G3.trainerWrite (party species, items and moves go id -> number and back);
-- keep the untouched row to prove the party survives the round trip.
local function partyShape(trainer)
  local out = {}
  for _, mon in ipairs(trainer and trainer.party or {}) do
    local moves = {}
    for i = 1, 4 do moves[i] = tostring((mon.moves or {})[i] or 0) end
    out[#out + 1] = table.concat({ tostring(mon.species), tostring(mon.level or mon.lvl),
      tostring(mon.heldItem or 0), table.concat(moves, "/") }, ":")
  end
  return table.concat(out, ",")
end
local partiesBefore = {}
for id, trainer in pairs(data.gen3Trainers.trainers or {}) do
  partiesBefore[id] = partyShape(trainer)
end

local T = require("tests.modkit")
local modParent, modName = modDir:match("^(.*)[/\\]([^/\\]*)$")
if not modParent then modParent, modName = ".", modDir end
local result = T.sdk.loadMod(modName, { generation = 3, root = modParent, data = data })
check(#result.errors == 0, "the mod loads with no errors under GameVersion=firered")
for _, err in ipairs(result.errors or {}) do
  io.stderr:write("  loader error: " .. tostring(err.message or err) .. "\n")
end
local mod = result.mod
check(mod ~= nil and mod.state == "loaded", "the mod reaches state=loaded")

local TextIR = require("src.core.game3.scripting.text_ir")

local function sameIr(actual, expected)
  if type(actual) ~= "table" or #actual ~= #expected then return false end
  for index, want in ipairs(expected) do
    local got = actual[index]
    if type(got) ~= "table" then return false end
    for _, field in ipairs({ "t", "s", "n", "code", "cmd" }) do
      if got[field] ~= want[field] then return false end
    end
  end
  return true
end

local sample = expectations.dialogue
if sample then
  local live = data.gen3Text[sample.key]
  check(sameIr(live, sample.ir), "dialogue " .. sample.key .. " holds the translated IR")
  local rendered = TextIR.toTextBox(live or {}, { playerName = "RED" })
  check(type(rendered) == "string" and rendered:find(sample.probe, 1, true) ~= nil,
    "dialogue " .. sample.key .. " renders through TextIR.toTextBox")
end

local function names()
  local out = {}
  local row = expectations.species_names
  if row then out.species = Pokemon.name(row.number) end
  row = expectations.move_names
  if row then out.move = Pokemon._moveNames and Pokemon._moveNames[row.number] end
  return out
end

local row = expectations.species_names
if row then eq(Pokemon.name(row.number), row.value, "species " .. row.id .. " name") end
row = expectations.move_names
if row then
  eq(Pokemon._moveNames and Pokemon._moveNames[row.number], row.value, "move " .. row.id .. " name")
end
row = expectations.item_names
if row then
  local info = ItemsData.info(row.number)
  eq(info and info.name, row.value, "item " .. row.id .. " name")
end
row = expectations.item_descriptions
if row then
  eq(ItemsData.description(row.number), row.value, "item " .. row.id .. " description")
end
row = expectations.trainer_names
if row then
  local trainer = Trainers.get(tonumber(row.id))
  eq(trainer and trainer.name, row.value, "trainer " .. row.id .. " name")
end
row = expectations.trainer_class_names
if row then
  local trainer = Trainers.get(tonumber(row.id))
  eq(trainer and trainer.className, row.value, "trainer " .. row.id .. " class name")
end
do
  local changed, checked = {}, 0
  for id, before in pairs(partiesBefore) do
    checked = checked + 1
    if partyShape(data.gen3Trainers.trainers[id]) ~= before then changed[#changed + 1] = id end
  end
  table.sort(changed)
  check(#changed == 0, ("%d trainer parties are unchanged by the renames%s"):format(
    checked, #changed > 0 and (" (changed: " .. table.concat(changed, ", ", 1, math.min(#changed, 10)) .. ")") or ""))
end
-- Trainers.info substitutes the player's rival name only while the class
-- name still reads "RIVAL"; the catalog must leave that class alone.
do
  local rival = Trainers.info(326, { rivalName = "GARY" })
  if rival then eq(rival.name, "GARY", "the rival keeps the player's chosen name") end
end
-- The strings registry merges into data.strings.  Strings() answers from it
-- once Game3:_loadMods hands the merged data to Strings.load, as Game and
-- Game2 do; engines before that fix never did, so it is measured here from
-- the pinned engine's own Game3.lua, not failed on.
local stringsLive
row = expectations.strings
if row then
  eq(data.strings and data.strings[row.id], row.value, "strings registry " .. row.id)
  local Strings = require("src.core.Strings")
  local game3 = readFile(engineRoot .. "/src/core/Game3.lua") or ""
  local loadsCatalog = game3:find('require%("src%.core%.Strings"%)%.load%(self%.data%)') ~= nil
  if loadsCatalog then Strings.load(data) end
  stringsLive = { key = row.id, resolves = Strings(row.id) == row.value, game3_loads_catalog = loadsCatalog }
end

row = expectations.start_menu
if row then
  local ModRuntime = require("src.mods.Runtime")
  local items = {}
  for _, id in ipairs({ "pokedex", "pokemon", "bag", "trainer", "save", "option", "exit" }) do
    items[#items + 1] = { id = id, label = id == "trainer" and "RED" or id:upper() }
  end
  check(ModRuntime.wantsHook("ui.start_menu.items"), "the mod wraps ui.start_menu.items")
  local shown = ModRuntime.call("ui.start_menu.items", function(_, list) return list end, {}, items)
  local byId = {}
  for _, item in ipairs(type(shown) == "table" and shown or {}) do byId[item.id] = item.label end
  for id, label in pairs(row) do eq(byId[id], label, "start menu " .. id .. " label") end
  eq(byId.trainer, "RED", "start menu keeps the player's name")
end

-- Runtime.start (src/core/game3/runtime.lua) installs the species pack again
-- on entering the field; measure whether the patched names survive it.
local before = names()
Pokemon.install(cache)
local after = names()
local persistence = {}
for key, value in pairs(before) do
  persistence[key] = { before_field = value, after_field = after[key],
                       survives = value == after[key] }
end

-- Characters of every shipped catalog that FrlgFont draws as glyph 0: the
-- dialogue IR's text segments and every string value of the other catalogs
-- (names, descriptions, start menu, Strings() values).
local FrlgFont = require("src.ui.game3.frlg_font")
local blank, blankByCatalog = {}, {}
local blankTotal = 0
-- A braille message is drawn cell for cell by src/ui/game3/braille.lua, not
-- by FrlgFont, so a Unicode braille cell (U+2800-U+283F) is never blank.
local function brailleCell(char)
  local b1, b2, b3 = char:byte(1, 3)
  return #char == 3 and b1 == 0xE2 and b2 == 0xA0 and b3 and b3 >= 0x80 and b3 <= 0xBF
end

local function countBlanks(catalogName, text)
  -- Strings() directives, {PLAYER}/{A_BUTTON}-style tokens and page and line
  -- marks are replaced or acted on before anything is drawn, pret's own
  -- escapes included (Teachy TV's lessons write "\\n", "\\l" and "\\p").
  text = text:gsub("%%%d*%$?[-+ #0]*%d*%.?%d*[%a%%]", ""):gsub("{[%u%d_]+}", ""):gsub("\\[npl]", ""):gsub("\f", "")
  for char in text:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
    if char ~= " " and char ~= "\n" and not brailleCell(char) and FrlgFont.glyphId(char) == 0 then
      blank[char] = (blank[char] or 0) + 1
      blankByCatalog[catalogName] = (blankByCatalog[catalogName] or 0) + 1
      blankTotal = blankTotal + 1
    end
  end
end
for _, catalogName in ipairs({ "dialogue", "species_names", "move_names", "item_names",
    "item_descriptions", "trainer_names", "trainer_class_names", "start_menu", "strings",
    "strings_by_english" }) do
  local body = readFile(modDir .. "/lang/" .. catalogName .. ".lua")
  local chunk = body and loadstring(body)
  for _, value in pairs(chunk and chunk() or {}) do
    if type(value) == "string" then
      countBlanks(catalogName, value)
    elseif type(value) == "table" then
      for _, segment in ipairs(value) do
        if segment.t == "text" then countBlanks(catalogName, segment.s) end
      end
    end
  end
end

local report = {
  failures = failures,
  persistence = persistence,
  blank_glyphs = { total = blankTotal, characters = blank, by_catalog = blankByCatalog },
  strings_live = stringsLive,
}
local out = assert(io.open(reportPath, "wb"))
out:write(Json.encode(report))
out:write("\n")
out:close()

if result.release then result.release() end
if failures > 0 then os.exit(1) end
