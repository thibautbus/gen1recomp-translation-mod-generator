-- Headless Gold/Silver ROM extraction under plain LuaJIT (no LÖVE, no ROM
-- in git).
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
-- Usage: luajit gs_extract.lua <gen1recomp_root> <rom_path> <out_dir> <edition> [engine_profile]
--
-- <edition> is "gold", "silver", or "crystal" -- gen1recomp-translation-mods'
-- own pipeline/roms.py:verify_gs_rom()/verify_crystal_rom() already computed
-- the ROM's SHA-1 and knows which one it is by the time this script runs, so
-- this script doesn't recompute it: love.data.hash (what gen1recomp's own
-- GameVersion.forSha1 callers use) isn't available under the headless
-- tests/love_stub.lua this script runs under. Picks rom_manifest_gold.json,
-- rom_manifest_silver.json, or rom_manifest_crystal.json accordingly, the
-- same manifest gen1recomp's own RomExtractorGen2 would read this ROM
-- through at runtime (RomExtractorGen2.lua's own edition branches already
-- cover "crystal" alongside "gold"/"silver").
-- The optional engine_profile is "pinned" (default, currently backed by
-- gen1recomp v0.2.41) or "upstream-local".
--
-- Writes, in <out_dir>:
--   gs_text.tsv     pointer -> text, from the "scripts" stage (required)
--   gs_labels.tsv    resolved NAMED_TEXT label -> pointer (required)
--   gs_stages.tsv    stage name -> ok|FAIL and, on failure, the error
--   gs_rom_text.tsv  label -> decoded engine text (required)
--   gs_species.tsv, gs_moves.tsv, gs_items.tsv, gs_types.tsv,
--   gs_trainer_classes.tsv, gs_landmarks.tsv

local root, romPath, outDir, edition, engineProfile = ...
assert(root and romPath and outDir, "usage: <gen1recomp_root> <rom> <out_dir> <edition>")
edition = edition or "gold"
assert(edition == "gold" or edition == "silver" or edition == "crystal",
  "edition must be \"gold\", \"silver\", or \"crystal\", got " .. tostring(edition))
engineProfile = engineProfile or "pinned"
assert(engineProfile == "pinned" or engineProfile == "upstream-local",
  "engine profile must be \"pinned\" or \"upstream-local\", got " .. tostring(engineProfile))
local upstreamProfile = engineProfile == "upstream-local"

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
local manifestNames = {
  gold = "rom_manifest_gold.json",
  silver = "rom_manifest_silver.json",
  crystal = "rom_manifest_crystal.json",
}
local manifestName = manifestNames[edition]
local manifest = Json.decode(readFile(root .. "/tools/" .. manifestName))

local extractor = RomExtractorGen2.new(romData, manifest)
local written = {}
extractor.write = function(_, name, value) written[name] = value end
extractor.save = function() end

-- name -> whether this project's translation pipeline needs the stage's
-- result at all. Stages outside this set still run (for coverage
-- measurement) but a failure there does not fail the gate: nothing in the
-- pipeline reads their result yet.
local REQUIRED = {
  constants = true, maps = true, stdScripts = true, scripts = true, text = true,
  pokemon = true, moves = true, items = true, trainers = true,
  landmarks = true, oakSpeech = true,
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
if upstreamProfile then
  stage("text", function()
    assert(type(extractor.extractText) == "function", "upstream profile requires RomExtractorGen2:extractText")
    return extractor:extractText()
  end)
else
  -- RomExtractorGen2:extractText() and its public data.text output were
  -- introduced after the v0.2.41 API.  Script text remains available through
  -- extractScriptsAndText and is sufficient for the pinned release.
  report[#report + 1] = { name = "text", ok = true, skipped = true,
    err = "not supported by pinned v0.2.41 API" }
end
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
  io.stderr:write("\na required stage failed; no gs_text.tsv/gs_labels.tsv written\n")
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

local pointers, meta, oakLabels = {}, {}, {}
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

-- Oak's intro speech (_OakText1-7) is decoded by extractOakSpeech rather
-- than by the map/script walk above.
-- Opcodes.key's own format (src/script/gen2/Opcodes.lua) is replicated
-- here since extractOakSpeech returns decoded text keyed by symbol LABEL
-- ("_OakText1"), not by the bank:address every other pointer in this file
-- uses. symbol() is a public method, so no fork of RomExtractorGen2.lua
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
      oakLabels[label] = key
    end
  end
end

table.sort(pointers, function(a, b) return a.key < b.key end)
table.sort(meta)

local textOut = assert(io.open(outDir .. "/gs_text.tsv", "w"))
for _, row in ipairs(pointers) do
  textOut:write(row.key, "\t", escape(row.value), "\n")
end
textOut:close()

local labels, labelsByName = {}, {}
for label, key in pairs(text.labels or {}) do
  labelsByName[label] = key
end
for label, key in pairs(oakLabels) do
  if labelsByName[label] and labelsByName[label] ~= key then
    error(("label %s resolves to both %s and %s"):format(
      label, tostring(labelsByName[label]), tostring(key)))
  end
  labelsByName[label] = key
end
for label, key in pairs(labelsByName) do
  labels[#labels + 1] = { label = label, key = key }
end
table.sort(labels, function(a, b) return a.label < b.label end)

local labelOut = assert(io.open(outDir .. "/gs_labels.tsv", "w"))
for _, row in ipairs(labels) do
  labelOut:write(row.label, "\t", tostring(row.key), "\n")
end
labelOut:close()

local stagesOut = assert(io.open(outDir .. "/gs_stages.tsv", "w"))
for _, row in ipairs(report) do
  stagesOut:write(row.name, "\t", row.ok and "ok" or "FAIL", "\t", row.err or "", "\n")
end
stagesOut:close()

if upstreamProfile then
  local romTextOut = assert(io.open(outDir .. "/gs_rom_text.tsv", "w"))
  local romTextLabels = {}
  for label, _ in pairs(results.text or {}) do romTextLabels[#romTextLabels + 1] = label end
  table.sort(romTextLabels)
  for _, label in ipairs(romTextLabels) do
    romTextOut:write(label, "\t", escape(results.text[label]), "\n")
  end
  romTextOut:close()
end

-- Index-keyed catalogs: the corpus joins these by index (dex number,
-- move/item index, class index), not by normalised English -- a
-- different, simpler mechanic than the pointer join above. One TSV per
-- registry: id (the mod-facing patch key) \t index (the corpus join
-- key) \t name (audit only, not consumed).
local function dumpIndexed(fileName, indexField, table_)
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

dumpIndexed("gs_species.tsv", "dex", results.pokemon)
dumpIndexed("gs_moves.tsv", "index", results.moves)
dumpIndexed("gs_items.tsv", "index", results.items)
dumpIndexed("gs_types.tsv", "index", written.type_chart and written.type_chart.types)
dumpIndexed("gs_trainer_classes.tsv", "index", results.trainers and results.trainers.classes)
-- landmarks (data.gen2Landmarks.landmarks -- Schemas.GEN2 routes the
-- `landmarks` registry there): the per-id records sit one level under
-- the stage's own return value, unlike pokemon/moves/items/trainers.
dumpIndexed("gs_landmarks.tsv", "index", results.landmarks and results.landmarks.landmarks)

io.write("\n")
io.write(("text pointers   : %d\n"):format(#pointers))
io.write(("metadata keys   : %d (%s)\n"):format(#meta, table.concat(meta, ",")))
io.write(("resolved labels : %d\n"):format(#labels))
local okCount = 0
for _, row in ipairs(report) do if row.ok then okCount = okCount + 1 end end
io.write(("stages ok       : %d/%d\n"):format(okCount, #report))
io.write("wrote gs_text.tsv, gs_labels.tsv, gs_stages.tsv, gs_rom_text.tsv,\n"
  .. "      gs_species.tsv, gs_moves.tsv, gs_items.tsv, gs_types.tsv,\n"
  .. "      gs_trainer_classes.tsv,\n"
  .. "      gs_landmarks.tsv\n")
