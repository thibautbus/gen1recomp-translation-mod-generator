-- THROWAWAY measurement spike, not part of the pipeline: how much of the
-- dialogue text table (`data.text`, bank:address-keyed) actually has the
-- same keys between a Gold and a Silver ROM import.
--
-- This is the real question a Gold-authored dialogue.lua mod needs
-- answered before declaring itself Silver-compatible by manifest alone
-- (see docs/upstream-fixes.md's "Silver: supported by declaration"):
-- gen1recomp's own tools/make_silver_manifest.py only guarantees the
-- *named symbols* mostly match (818/2057 differ, all sprites/Pokedex/
-- graphics, zero Text/Script symbols); the actual per-pointer dialogue
-- addresses are discovered dynamically from map/script data at runtime,
-- not copied from those named anchors, so this measures the real thing
-- directly instead of trusting that proxy.
--
-- Runs the same stages as tools/gs_extract.lua, in the same order, up
-- through "scripts" (the stage that produces the text table) -- copied
-- rather than importing gs_extract.lua, since that script is a
-- standalone CLI tool with no reusable function boundary, and this is a
-- one-off measurement, not something to wire into the pipeline.
--
-- Usage: luajit tools/spike_gold_silver_text_overlap.lua <gen1recomp_root> <gold_rom> <silver_rom>

local root, goldRomPath, silverRomPath = ...
assert(root and goldRomPath and silverRomPath,
  "usage: <gen1recomp_root> <gold_rom> <silver_rom>")

package.path = table.concat({
  root .. "/?.lua",
  root .. "/?/init.lua",
  package.path,
}, ";")

love = require("tests.love_stub")

local Json = require("src.link.Json")
local RomExtractorGen2 = require("src.import.RomExtractorGen2")

local function readFile(path, mode)
  local handle = assert(io.open(path, mode or "r"), "cannot open " .. path)
  local body = handle:read("*a")
  handle:close()
  return body
end

-- Same order as gs_extract.lua/RomExtractorGen2:run(), stopping once the
-- text table is available -- nothing after "scripts" is needed here.
local function extractTextKeys(romPath, manifestPath, label)
  local romData = readFile(romPath, "rb")
  local manifest = Json.decode(readFile(manifestPath))
  local extractor = RomExtractorGen2.new(romData, manifest)
  extractor.write = function() end
  extractor.save = function() end

  extractor:extractConstants()
  extractor:extractFont()
  extractor:extractPalettes()
  extractor:extractTilesets()
  local maps = extractor:extractMaps()
  extractor:extractSprites()
  local stdScripts = extractor:extractStdScripts()
  local scripts = extractor:extractScriptsAndText(maps, stdScripts)
  local text = assert(scripts.text, label .. ": no text table returned")

  local keys, values = {}, {}
  local count = 0
  for key, value in pairs(text) do
    if key ~= "labels" and type(value) == "string" then
      keys[key] = true
      values[key] = value
      count = count + 1
    end
  end
  io.write(("%-8s %d dialogue text pointers\n"):format(label, count))
  return keys, values
end

local goldKeys, goldValues = extractTextKeys(
  goldRomPath, root .. "/tools/rom_manifest_gold.json", "Gold")
local silverKeys, silverValues = extractTextKeys(
  silverRomPath, root .. "/tools/rom_manifest_silver.json", "Silver")

local both, goldOnly, silverOnly = 0, 0, 0
local sameText, differentText = 0, 0
for key in pairs(goldKeys) do
  if silverKeys[key] then
    both = both + 1
  else
    goldOnly = goldOnly + 1
  end
end
for key in pairs(silverKeys) do
  if not goldKeys[key] then
    silverOnly = silverOnly + 1
  end
end

local goldTotal = 0
for _ in pairs(goldKeys) do goldTotal = goldTotal + 1 end

io.write("\n")
io.write(("Gold keys also present in Silver : %d / %d (%.1f%%)\n")
  :format(both, goldTotal, goldTotal > 0 and (100 * both / goldTotal) or 0))
io.write(("Gold-only keys (would silently miss on Silver) : %d\n"):format(goldOnly))
io.write(("Silver-only keys (Gold mod never touches these) : %d\n"):format(silverOnly))
io.write("\nThis is the fraction of a Gold-authored dialogue.lua whose\n"
  .. "overrides will actually find a matching key at runtime against a\n"
  .. "real Silver import -- the number that determines real in-game\n"
  .. "coverage for a Silver player, not the named-symbol proxy.\n")

if goldOnly + silverOnly > 0 then
  io.write("\nGold-only keys and their text:\n")
  for key in pairs(goldKeys) do
    if not silverKeys[key] then
      io.write(("  %s: %q\n"):format(key, goldValues[key]))
    end
  end
  io.write("\nSilver-only keys and their text:\n")
  for key in pairs(silverKeys) do
    if not goldKeys[key] then
      io.write(("  %s: %q\n"):format(key, silverValues[key]))
    end
  end
end
