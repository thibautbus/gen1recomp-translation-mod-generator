-- Proof the Emerald translation mod reaches what the game3 runtime reads,
-- through gen1recomp's real generation-3 mod loader (tests.modkit's SDK, the
-- seam its own Gen 3 registry tests use), on top of the real game3 data
-- modules loaded from the private Emerald extract -- not a fixture.  It
-- follows tools/frlg/gate.lua; what differs is Emerald's own consumers.
--
-- Checks, for one sample per catalog the build hands over in <expectations>:
--   * the mod loads with no error under GameVersion "emerald", and does not
--     load under "firered" (its manifest names Emerald only);
--   * dialogue: data.gen3Text (the live Space.bundle.text a game3 boot
--     exposes) holds the translated IR, and RomText.box renders it;
--   * the battle string table reaches BattleText.get, the nature names
--     SummaryData.NATURES (both read pointer-table keys through RomText);
--   * species/move/item/trainer names, item descriptions and trainer class
--     names are read back through the module APIs the game3 UI calls;
--   * the strings registry holds the translated engine string, and an Easy
--     Chat word resolves through EasyChatText.word.
--
-- It also measures, without failing, what the translation mod cannot fix
-- itself (docs/upstream-fixes.md, Emerald section):
--   * glyphs: how many characters of the shipped catalogs FrlgFont.glyphId
--     maps to glyph 0 (a blank);
--   * strings: whether Strings() answers from the merged catalog at all;
--   * hooks: whether the Emerald screens that print the cart's English
--     through Strings() (ability names, Pokédex entries, contest texts, map
--     sections) do so at the pinned revision.
--
-- Usage: luajit tools/rse/gate.lua <gen1recomp_root> <extract_cache_dir> <mod_dir>
--                                  <expectations.json> <report.json>

local engineRoot, cacheDir, modDir, expectationPath, reportPath = ...
if not (engineRoot and cacheDir and modDir and expectationPath and reportPath) then
  io.stderr:write("usage: luajit tools/rse/gate.lua <gen1recomp_root> <extract_cache_dir> "
    .. "<mod_dir> <expectations.json> <report.json>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path
love = require("tests.love_stub")

local Json = require("src.link.Json")
local FileIO = require("src.import.gba.file_io")
local GameVersion = require("src.core.GameVersion")
GameVersion.set("emerald")
require("src.import.gba.versions").select("emerald")

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

-- Every check below runs on a sample the build hands over: a sample the build
-- names as required (its catalog is not empty) and does not hand over would
-- skip its check, so it fails here instead.
for _, name in ipairs(expectations.required or {}) do
  local sample = expectations[name]
  check(type(sample) == "table" and next(sample) ~= nil, "the build provides a " .. name .. " sample")
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
local Space = require("src.core.game3.scripting.space")
Space.bundle = bundle

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

local T = require("tests.modkit")
local modParent, modName = modDir:match("^(.*)[/\\]([^/\\]*)$")
if not modParent then modParent, modName = ".", modDir end
local result = T.sdk.loadMod(modName, { generation = 3, root = modParent, data = data })
check(#result.errors == 0, "the mod loads with no errors under GameVersion=emerald")
for _, err in ipairs(result.errors or {}) do
  io.stderr:write("  loader error: " .. tostring(err.message or err) .. "\n")
end
local mod = result.mod
check(mod ~= nil and mod.state == "loaded", "the mod reaches state=loaded")

local RomText = require("src.core.game3.rom_text")
local Strings = require("src.core.Strings")
local game3 = readFile(engineRoot .. "/src/core/Game3.lua") or ""
local loadsCatalog = game3:find('"src%.core%.Strings"%)%.load%(self%.data%)') ~= nil
if loadsCatalog then Strings.load(data) end

local function sameIr(actual, expected)
  if type(actual) ~= "table" or #actual ~= #expected then return false end
  for index, want in ipairs(expected) do
    local got = actual[index]
    if type(got) ~= "table" then return false end
    for _, field in ipairs({ "t", "s", "n", "code", "name", "cmd", "font", "tag" }) do
      if got[field] ~= want[field] then return false end
    end
  end
  return true
end

local sample = expectations.dialogue
if sample then
  check(sameIr(data.gen3Text[sample.key], sample.ir), "dialogue " .. sample.key .. " holds the translated IR")
  local rendered = RomText.box(sample.key, { playerName = "BRENDAN" })
  check(type(rendered) == "string" and rendered:find(sample.probe, 1, true) ~= nil,
    "dialogue " .. sample.key .. " renders through RomText.box")
end

local row = expectations.battle
if row then
  local Versions = require("src.import.gba.versions")
  local id
  for number, key in pairs(Versions.BATTLE_STRING_IDS or {}) do
    if key == row.key then id = number end
  end
  check(id ~= nil, "battle string " .. row.key .. " has a STRINGID")
  if id then
    eq(require("src.core.game3.battle.battle_text").get(id, {}), row.value,
      "BattleText.get(" .. row.key .. ")")
  end
end
local SummaryData = require("src.core.game3.summary_data")
for _, nature in ipairs(expectations.natures or {}) do
  eq(SummaryData.NATURES[nature.id], nature.value, "SummaryData.NATURES[" .. nature.id .. "]")
end

row = expectations.species_names
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
if row then eq(ItemsData.description(row.number), row.value, "item " .. row.id .. " description") end
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

local stringsLive
row = expectations.strings
if row then
  eq(data.strings and data.strings[row.id], row.value, "strings registry " .. row.id)
  stringsLive = { key = row.id, resolves = Strings(row.id) == row.value, game3_loads_catalog = loadsCatalog }
end

row = expectations.easy_chat
if row and loadsCatalog then
  local EasyChatText = require("src.core.game3.easy_chat_text")
  local id = row.id
  for _, group in pairs(row.word and EasyChatText.groups() or {}) do
    if group.name == row.group then
      for _, word in ipairs(group.words or {}) do
        if word.text == row.word then id = word.id end
      end
    end
  end
  check(id ~= nil, "Easy Chat word " .. tostring(row.word or row.id) .. " is in the vocabulary")
  if id then eq(EasyChatText.word(id), row.value, "EasyChatText.word(" .. tostring(row.word or id) .. ")") end
end

-- The Emerald screens that print the cart's English through Strings() once
-- gen1recomp routes them there: measured against the pinned engine.
local hooks = {}
local function measure(name, fn, want)
  local ok, got = pcall(fn)
  hooks[name] = { routed = ok and got == want, got = ok and got or nil }
end
local wanted = expectations.hooks or {}
if wanted.ability_name then
  local id
  for number = 1, 255 do
    local ok, name = pcall(Pokemon.romAbilityName, number)
    if ok and name == wanted.ability_name.source then id = number break end
  end
  if id then measure("ability_name", function() return Pokemon.abilityName(id) end, wanted.ability_name.value) end
end
if wanted.contest then
  measure("contest", function()
    if type(SummaryData.contestEffectDescription) ~= "function" then return nil end
    local text = SummaryData.contestEffectDescription({ description = wanted.contest.source })
    if text == wanted.contest.source and SummaryData.contestCategoryName then
      text = SummaryData.contestCategoryName(wanted.contest.source)
    end
    return text
  end, wanted.contest.value)
end
if wanted.map_section then
  measure("map_section", function()
    local Mapsec = require("src.ui.game3.rse.mapsec")
    for sec = 0, Mapsec.count() - 1 do
      if (Mapsec.entry(sec) or {}).name == wanted.map_section.source then return Mapsec.name(sec) end
    end
  end, wanted.map_section.value)
end
if wanted.pokedex then
  measure("pokedex", function()
    local Pokedex = require("src.ui.game3.rse.pokedex")
    local entries = assert(load(assert(cache:read("data/generated/gba/pokemon/pokedex/entries.lua")),
      "@entries", "t", {}))()
    for species, entry in pairs(entries) do
      if entry.category == wanted.pokedex.source or entry.description == wanted.pokedex.source then
        local national = Pokemon.national and Pokemon.national(species) or species
        for _, line in ipairs(Pokedex.monInfo({}, national, true, true, false) or {}) do
          if line.text and line.text:find(wanted.pokedex.value, 1, true) then return wanted.pokedex.value end
        end
        return nil
      end
    end
  end, wanted.pokedex.value)
end

-- Characters of every shipped catalog that FrlgFont draws as glyph 0.
local FrlgFont = require("src.ui.game3.frlg_font")
local blank, blankByCatalog = {}, {}
local blankTotal = 0
local function brailleCell(char)
  local b1, b2, b3 = char:byte(1, 3)
  return #char == 3 and b1 == 0xE2 and b2 == 0xA0 and b3 and b3 >= 0x80 and b3 <= 0xBF
end
local function countBlanks(catalogName, text)
  text = text:gsub("%%%d*%$?[-+ #0]*%d*%.?%d*[%a%%]", ""):gsub("{[%u%d_]+}", ""):gsub("\\[npl]", ""):gsub("\f", "")
  for char in text:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
    if char ~= " " and char ~= "\n" and not brailleCell(char) and FrlgFont.glyphId(char) == 0 then
      blank[char] = (blank[char] or 0) + 1
      blankByCatalog[catalogName] = (blankByCatalog[catalogName] or 0) + 1
      blankTotal = blankTotal + 1
    end
  end
end
local shippedCatalogs = { "species_names", "move_names", "item_names", "item_descriptions",
  "trainer_names", "trainer_class_names", "strings", "strings_by_english" }
local part = 1
while readFile(modDir .. "/lang/" .. (part == 1 and "dialogue" or ("dialogue_" .. part)) .. ".lua") do
  table.insert(shippedCatalogs, part, part == 1 and "dialogue" or ("dialogue_" .. part))
  part = part + 1
end

-- A label the mod ships as dialogue is read through RomText, which takes the
-- label's strings entry first (RomText.translate): an entry there would show
-- another screen's wording instead of the label's own row.
local shadowed = {}
for index = 1, part - 1 do
  local name = index == 1 and "dialogue" or ("dialogue_" .. index)
  local body = readFile(modDir .. "/lang/" .. name .. ".lua")
  local chunk = body and loadstring(body)
  for key in pairs(chunk and chunk() or {}) do
    if not key:match("^g3:") and data.strings and data.strings[key] ~= nil then
      shadowed[#shadowed + 1] = key
    end
  end
end
table.sort(shadowed)
check(#shadowed == 0, ("no dialogue label is shadowed by a strings entry%s"):format(
  #shadowed > 0 and (" (" .. table.concat(shadowed, ", ", 1, math.min(#shadowed, 10)) .. ")") or ""))

for _, catalogName in ipairs(shippedCatalogs) do
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

if result.release then result.release() end

-- The manifest names Emerald only: under a FireRed session the loader must
-- leave the mod out, so its Emerald text never reaches FireRed's addresses.
GameVersion.set("firered")
local other = T.sdk.loadMod(modName, { generation = 3, root = modParent, data = { gen3Text = {} } })
check(not other.mod or other.mod.state ~= "loaded", "the mod stays out of a FireRed session")
if other.release then other.release() end

local report = {
  failures = failures,
  blank_glyphs = { total = blankTotal, characters = blank, by_catalog = blankByCatalog },
  strings_live = stringsLive,
  hooks = hooks,
}
local out = assert(io.open(reportPath, "wb"))
out:write(Json.encode(report))
out:write("\n")
out:close()

if failures > 0 then os.exit(1) end
