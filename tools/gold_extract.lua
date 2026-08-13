-- Headless Gold ROM extraction under plain LuaJIT (no LÖVE, no ROM in git).
--
-- Drives RomExtractorGen2:run()'s 26 stages in order under tests/love_stub,
-- and reports which ones complete under the stub and which fail: LÖVE is
-- no longer a dependency for the stages that do not encode an image (the
-- stub's ImageData:encode() returns an empty string and setPixel/mapPixel
-- are no-ops -- tests/love_stub.lua -- so any stage whose result depends
-- on pixel data will fail here even though it "completes").
--
-- The two write sinks are neutralised: RomExtractorGen2:write goes through
-- LuaWriter to data/generated/, and :save goes through ImageWriter (the
-- only real LÖVE-coupled module). A translation mod needs neither, so
-- nothing touches the filesystem outside <out_dir>.
--
-- Usage: luajit gold_extract.lua <gen1recomp_root> <rom_path> <out_dir>
--
-- Writes, in <out_dir>:
--   gold_text.tsv     pointer -> text, from the "scripts" stage (required)
--   gold_labels.tsv    resolved NAMED_TEXT label -> pointer (required)
--   gold_stages.tsv    stage name -> ok|FAIL and, on failure, the error
--   gold_maps.tsv      map constant name -> scripts bank (hex), if the
--                      "maps" stage succeeded

local root, romPath, outDir = ...
assert(root and romPath and outDir, "usage: <gen1recomp_root> <rom> <out_dir>")

package.path = table.concat({
  root .. "/?.lua",
  root .. "/?/init.lua",
  package.path,
}, ";")

-- modkit uses the same substitute for its own headless loader driver.
love = require("tests.love_stub")

local Json = require("src.link.Json")
local RomExtractorGen2 = require("src.import.RomExtractorGen2")

local function readFile(path, mode)
  local handle = assert(io.open(path, mode or "r"), "cannot open " .. path)
  local body = handle:read("*a")
  handle:close()
  return body
end

local romData = readFile(romPath, "rb")
local manifest = Json.decode(readFile(root .. "/tools/rom_manifest_gold.json"))

local extractor = RomExtractorGen2.new(romData, manifest)
extractor.write = function() end
extractor.save = function() end

-- name -> whether this project's translation pipeline needs the stage's
-- result at all. Stages outside this set still run (for coverage
-- measurement) but a failure there does not fail the gate: nothing in the
-- pipeline reads their result yet.
local REQUIRED = {
  constants = true, maps = true, stdScripts = true, scripts = true,
}

local results = {}
local report = {}
local requiredFailed = false

local function stage(name, fn)
  local ok, value = pcall(fn)
  report[#report + 1] = { name = name, ok = ok, err = not ok and tostring(value) or nil }
  io.write(("%-14s %s\n"):format(name, ok and "ok" or ("FAILED: " .. tostring(value))))
  io.flush()
  if ok then
    results[name] = value
  elseif REQUIRED[name] then
    requiredFailed = true
  end
  return ok and value or nil
end

-- Same order as RomExtractorGen2:run(), so a future upstream reordering is
-- visible as a diff here rather than silently changing what this measures.
stage("constants", function() return extractor:extractConstants() end)
stage("font", function() return extractor:extractFont() end)
stage("palettes", function() return extractor:extractPalettes() end)
stage("tilesets", function() return extractor:extractTilesets() end)
stage("maps", function() return extractor:extractMaps() end)
stage("sprites", function() return extractor:extractSprites() end)
stage("stdScripts", function() return extractor:extractStdScripts() end)
stage("scripts", function()
  return extractor:extractScriptsAndText(results.maps, results.stdScripts)
end)
stage("pokemon", function() return extractor:extractPokemon() end)
stage("moves", function() return extractor:extractMoves() end)
stage("items", function() return extractor:extractItems() end)
stage("marts", function() return extractor:extractMarts() end)
stage("encounters", function() return extractor:extractEncounters() end)
stage("trainers", function() return extractor:extractTrainers() end)
stage("pokedex", function() return extractor:extractPokedex() end)
stage("landmarks", function() return extractor:extractLandmarks() end)
stage("icons", function() return extractor:extractIcons() end)
stage("intro", function() return extractor:extractIntro() end)
stage("menuGfx", function() return extractor:extractMenuGfx() end)
stage("oakSpeech", function() return extractor:extractOakSpeech(results.pokemon) end)
stage("title", function() return extractor:extractTitle() end)
stage("credits", function() return extractor:extractCredits() end)
stage("diploma", function() return extractor:extractDiploma() end)
stage("trade", function() return extractor:extractTrade() end)
stage("audio", function() return extractor:extractAudio(results.maps) end)
stage("battleAnims", function() return extractor:extractBattleAnims() end)
stage("stubs", function() return extractor:extractStubs() end)

if requiredFailed then
  io.stderr:write("\na required stage failed; no gold_text.tsv/gold_labels.tsv written\n")
  os.exit(1)
end

local text = assert(results.scripts.text, "no text table returned")

-- TSV needs the value on one physical line.
local function escape(value)
  return (tostring(value)
    :gsub("\\", "\\\\")
    :gsub("\n", "\\n")
    :gsub("\r", "\\r")
    :gsub("\t", "\\t"))
end

local pointers, meta = {}, {}
local seenPointer = {}
for key, value in pairs(text) do
  if key == "labels" then
    -- handled separately
  elseif type(value) ~= "string" then
    meta[#meta + 1] = key
  else
    pointers[#pointers + 1] = { key = key, value = value }
    seenPointer[key] = true
  end
end

-- Elm's intro speech (_OakText1-7) is decoded by its own extraction stage
-- (extractOakSpeech), not reached by extractScriptsAndText's map/script
-- walk above -- verified against a real boot: with only the walk's
-- pointers, Elm's speech rendered in English while ordinary map dialogue
-- (93.2% of pointers, per the step-1 join) rendered correctly in French.
-- Opcodes.key's own format (src/script/gen2/Opcodes.lua) is replicated
-- here since extractOakSpeech returns decoded text keyed by symbol LABEL
-- ("_OakText1"), not by the bank:address every other pointer in this file
-- uses -- symbol() is a public method, so no fork of RomExtractorGen2.lua
-- is needed to recover the bank/address it already resolved internally.
if results.oakSpeech and results.oakSpeech.text then
  for label, value in pairs(results.oakSpeech.text) do
    local ok, sym = pcall(function() return extractor:symbol(label) end)
    if ok and type(value) == "string" then
      local key = ("%02x:%04x"):format(sym.bank, sym.address)
      if not seenPointer[key] then
        pointers[#pointers + 1] = { key = key, value = value }
        seenPointer[key] = true
      end
    end
  end
end

table.sort(pointers, function(a, b) return a.key < b.key end)
table.sort(meta)

local textOut = assert(io.open(outDir .. "/gold_text.tsv", "w"))
for _, row in ipairs(pointers) do
  textOut:write(row.key, "\t", escape(row.value), "\n")
end
textOut:close()

local labels = {}
for label, key in pairs(text.labels or {}) do
  labels[#labels + 1] = { label = label, key = key }
end
table.sort(labels, function(a, b) return a.label < b.label end)

local labelOut = assert(io.open(outDir .. "/gold_labels.tsv", "w"))
for _, row in ipairs(labels) do
  labelOut:write(row.label, "\t", tostring(row.key), "\n")
end
labelOut:close()

local stagesOut = assert(io.open(outDir .. "/gold_stages.tsv", "w"))
for _, row in ipairs(report) do
  stagesOut:write(row.name, "\t", row.ok and "ok" or "FAIL", "\t", row.err or "", "\n")
end
stagesOut:close()

-- Map name -> scripts bank, a weak but free proxy for "which map does this
-- pointer's text most likely belong to": scene scripts and callbacks
-- Opcodes.key() into this same bank (RomExtractorGen2.lua's extractMaps,
-- `scripts = { bank = eventsBank, ... }`). Used by pipeline/gold_join.py
-- to test the "disambiguate by map context" hypothesis against the qids'
-- own map-name prefixes (gs.Route29.*, gs.VioletGym.*...).
if results.maps then
  local mapNames = {}
  for name in pairs(results.maps) do mapNames[#mapNames + 1] = name end
  table.sort(mapNames)
  local mapsOut = assert(io.open(outDir .. "/gold_maps.tsv", "w"))
  for _, name in ipairs(mapNames) do
    local scripts = results.maps[name].scripts or {}
    mapsOut:write(name, "\t", ("%02X"):format(scripts.bank or 0), "\n")
  end
  mapsOut:close()
end

-- Index-keyed catalogs: the corpus joins these by index (dex number,
-- move/item index, class index), not by normalised English -- a
-- different, simpler mechanic than the pointer join above. One TSV per
-- registry: id (the mod-facing patch key) \t index (the corpus join
-- key) \t name (audit only, not consumed).
local function dumpIndexed(stageName, fileName, indexField, table_)
  if not table_ then return end
  local rows = {}
  for id, entry in pairs(table_) do
    -- Skip top-level metadata keys (generation, source...) that sit beside
    -- the per-id entries in these tables, same shape as the text table's
    -- "generation" key handled above.
    if type(entry) == "table" then
      rows[#rows + 1] = { id = id, index = entry[indexField], name = entry.name }
    end
  end
  table.sort(rows, function(a, b) return a.id < b.id end)
  local out = assert(io.open(outDir .. "/" .. fileName, "w"))
  for _, row in ipairs(rows) do
    -- name is audit-only (not read back by the join), but some (e.g.
    -- landmarks: "the two-line name the town map prints, \n and all")
    -- embed control characters that would otherwise split the TSV row.
    out:write(row.id, "\t", tostring(row.index), "\t", escape(row.name), "\n")
  end
  out:close()
end

dumpIndexed("pokemon", "gold_species.tsv", "dex", results.pokemon)
dumpIndexed("moves", "gold_moves.tsv", "index", results.moves)
dumpIndexed("items", "gold_items.tsv", "index", results.items)
dumpIndexed("trainers", "gold_trainer_classes.tsv", "index", results.trainers and results.trainers.classes)
-- landmarks (data.gen2Landmarks.landmarks -- Schemas.GEN2 routes the
-- `landmarks` registry there): the per-id records sit one level under
-- the stage's own return value, unlike pokemon/moves/items/trainers.
dumpIndexed("landmarks", "gold_landmarks.tsv", "index", results.landmarks and results.landmarks.landmarks)

io.write("\n")
io.write(("text pointers   : %d\n"):format(#pointers))
io.write(("metadata keys   : %d (%s)\n"):format(#meta, table.concat(meta, ",")))
io.write(("resolved labels : %d\n"):format(#labels))
local okCount = 0
for _, row in ipairs(report) do if row.ok then okCount = okCount + 1 end end
io.write(("stages ok       : %d/%d\n"):format(okCount, #report))
io.write("wrote gold_text.tsv, gold_labels.tsv, gold_stages.tsv, gold_maps.tsv,\n"
  .. "      gold_species.tsv, gold_moves.tsv, gold_items.tsv, gold_trainer_classes.tsv,\n"
  .. "      gold_landmarks.tsv\n")
