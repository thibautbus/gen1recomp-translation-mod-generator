-- Headless Emerald, Ruby or Sapphire text extraction under LuaJIT (no LÖVE,
-- no ROM in git).
--
-- Usage: luajit tools/rse/extract.lua <gen1recomp_root> <rom_path> <out_dir> <rom_sha1> [game]
--
-- <game> is emerald (the default), ruby or sapphire.  Runs the text-bearing
-- steps of the engine's own import plan for that game
-- (src/import/gba/plans/rse/*.lua, src/import/gba/plans/rs.lua) into
-- <out_dir>/cache -- the same packs a game3 boot reads, which the release
-- gate and the engine scope generator load back -- and writes the join's
-- inputs next to it:
--   rse_text.json           every text key as the runtime holds it
--                           (ExtractScripts.loadBundle: the script BFS text,
--                           with the label-keyed tables, plus the object
--                           interactions' text);
--   rse_text_pointers.json  for each key of a ROM pointer table
--                           (gNatureNamePointers[3], STRINGID_*...), the
--                           address it points at, which pret's symbol table
--                           names;
--   rse_species.json, rse_moves.json, rse_items.json, rse_trainers.json,
--   rse_trainer_classes.json  the named catalogs, by number;
--   rse_stages.json         each stage's status.
-- The graphics stages are never run.

local root, romPath, outDir, sha1, game = ...
assert(root and romPath and outDir and sha1,
  "usage: luajit tools/rse/extract.lua <gen1recomp_root> <rom> <out_dir> <rom_sha1> [game]")
game = game or "emerald"
assert(game == "emerald" or game == "ruby" or game == "sapphire", "unsupported game " .. game)

package.path = table.concat({
  root .. "/?.lua",
  root .. "/?/init.lua",
  package.path,
}, ";")

love = require("tests.love_stub")

-- FileIO.makeCache creates its directories with lfs when it can, and with a
-- POSIX `mkdir -p` otherwise; tools/frlg/extract.lua explains the FFI
-- fallback used without lfs (Windows).
if not pcall(require, "lfs") then
  local ok, ffi = pcall(require, "ffi")
  if ok then
    local mkdir
    if ffi.os == "Windows" then
      ffi.cdef("int CreateDirectoryA(const char *lpPathName, void *lpSecurityAttributes);")
      mkdir = function(path) return ffi.C.CreateDirectoryA(path, nil) ~= 0 end
    else
      ffi.cdef("int mkdir(const char *pathname, unsigned int mode);")
      mkdir = function(path) return ffi.C.mkdir(path, 493) == 0 end -- 0755
    end
    package.preload.lfs = function()
      return {
        mkdir = function(path)
          local sep = path:find("[\\/]", 2)
          while sep do
            mkdir(path:sub(1, sep - 1))
            sep = path:find("[\\/]", sep + 1)
          end
          return mkdir(path)
        end,
      }
    end
  end
end

-- Each game has its own map, script and text contract: select the game and
-- the GBA version before any of those modules is loaded.  Ruby and Sapphire
-- are selected by the ROM's SHA-1, which names the revision (1.0, 1.1 or
-- 1.2, src/import/gba/rs_builds.lua) whose addresses the engine reads.
local GameVersion = require("src.core.GameVersion")
GameVersion.set(game)
local Versions = require("src.import.gba.versions")
Versions.select(game == "emerald" and "emerald" or sha1)

local Json = require("src.link.Json")
local FileIO = require("src.import.gba.file_io")
local Rom = require("src.import.gba.rom")
local Plans = require("src.import.gba.plans.registry")

local CACHE_ROOT = "data/generated/gba"
local imports = FileIO.makeImports(romPath, sha1, game)
local cache = FileIO.makeCache(outDir .. "/cache")

local stages = {}
local function stage(name, fn)
  local ok, err = pcall(fn)
  stages[name] = ok and "ok" or tostring(err)
  if not ok then
    io.stderr:write(("[rse_extract] %s failed: %s\n"):format(name, tostring(err)))
  end
  return ok
end

local function writeJson(name, value)
  local f = assert(io.open(outDir .. "/" .. name, "wb"))
  f:write(Json.encode(value))
  f:write("\n")
  f:close()
end

local function loadCached(rel)
  local body = cache:read(CACHE_ROOT .. "/" .. rel)
  if not body then return nil end
  return assert(load(body, "@" .. rel, "t", {}))()
end

assert(GameVersion.get() == game, "the " .. game .. " game version was not selected")
assert(Versions.active() == game, "the " .. game .. " ROM family was not selected")
local version = assert(Versions.lookup(sha1), "unsupported " .. game .. " ROM " .. sha1)
local rom = assert(Rom.open(imports, game))

-- One step of the engine's import plan for the game, run the way
-- RomExtractorGen3:runStepsTask runs it.
local function planStep(name, opts)
  local step = { cacheRoot = CACHE_ROOT }
  for key, value in pairs(opts or {}) do step[key] = value end
  return require(Plans.moduleFor(name)).run(rom, cache, step)
end

-- Census first: it registers every map header the script BFS seeds from.
local censusOk = stage("census", function()
  local MapTree = require("src.import.gba.map_tree")
  local MapCatalog = require("src.import.gba.map_catalog")
  MapCatalog.rebuildIndex()
  local census = assert(MapTree.walk(rom, version))
  local mapOrder, byEngine = MapCatalog.allOrder(census, {})
  MapCatalog.registerOrder(rom, version, mapOrder, byEngine)
  assert(census.map_count and census.map_count > 0, "the " .. game .. " map census is empty")
  rom:clearCache()
end)

if censusOk then
  -- plans/rse/scripts.lua
  stage("scripts", function()
    planStep("extract_scripts", { version = version, strict = true })
  end)
  stage("object_interactions", function()
    require("src.import.gba.object_interactions_extract").writeExtract(rom, cache, CACHE_ROOT, version)
  end)
end

-- plans/rse/pokemon_gfx.lua (its data part only: names, move names, ability
-- names, move and ability descriptions), plans/rse/data.lua, text.lua,
-- ui.lua and uc.lua: every other pack whose text a translation reaches.
-- Ruby and Sapphire read their map sections and Easy Chat words with their
-- own native steps, and lay messages out with their native fonts
-- (plans/rs.lua).
local regionSteps = game == "emerald" and {
  { "map_sections", "src.import.gba.rse.map_sections_extract" },
  { "easy_chat", "easy_chat_extract" },
} or {
  { "map_sections", "src.import.gba.rs.extract_region_map" },
  { "easy_chat", "src.import.gba.rs.extract_easy_chat" },
  -- the native fonts' manifest, which lays a message box out (RomText.box)
  { "text_chrome", "src.import.gba.rs.text_chrome_extract" },
}
for _, step in ipairs({
  { "pokemon", "pokemon_extract", { part = "data" } },
  { "battle_moves", "battle_moves_extract" },
  { "items", "items_extract", { force = true } },
  { "contest_moves", "contest_moves_extract" },
  { "ingame_trades", "ingame_trades_extract" },
  { "pokedex_entries", "pokedex_entries_extract" },
  { "trainers", "trainer_extract" },
  { "text_placeholders", "text_placeholders_extract" },
  regionSteps[1],
  regionSteps[2],
  regionSteps[3],
}) do
  stage(step[1], function() planStep(step[2], step[3]) end)
end

-- The pointer tables the script extractor keys by name and index
-- (extract_scripts.lua extract_text_tables): the address each slot points
-- at, so the join can name its string with pret's symbol table.
stage("text_pointers", function()
  local out = {}
  for _, t in ipairs(Versions.TEXT_TABLES) do
    if not t.inline then
      for i = 0, t.count * (t.inner or 1) - 1 do
        local key
        if t.ids then
          key = Versions.BATTLE_STRING_IDS[i + t.ids]
        elseif t.inner then
          key = ("%s[%d][%d]"):format(t.name, math.floor(i / t.inner), i % t.inner)
        else
          key = ("%s[%d]"):format(t.name, i)
        end
        local pointer = rom:u32(t.addr + i * t.stride)
        if key and pointer ~= 0 and rom:ptrOffset(pointer) then out[key] = pointer end
      end
    end
  end
  -- The script menus' standard strings (extract_scripts.lua, gStdStringPtrs).
  for i = 0, Versions.STD_STRING_COUNT - 1 do
    local pointer = rom:u32(Versions.STD_STRING_PTRS + i * 4)
    if pointer ~= 0 and rom:ptrOffset(pointer) then out["stdstring:" .. i] = pointer end
  end
  assert(next(out), "no " .. game .. " text table pointer was read")
  writeJson("rse_text_pointers.json", out)
end)

local itemDescriptionPointers = {}
stage("item_description_pointers", function()
  -- pokeemerald/include/item.h (pokeruby/include/item.h is the same):
  -- struct Item's description pointer, as items_extract.lua reads it.
  for id = 0, Versions.ITEMS_COUNT - 1 do
    local pointer = rom:u32(Versions.ITEMS + id * Versions.ITEM_STRIDE + 20)
    if pointer ~= 0 and rom:ptrOffset(pointer) then itemDescriptionPointers[id] = pointer end
  end
end)

rom:clearCache()
imports:_close()

stage("export_text", function()
  local text = assert(loadCached("scripts/text.lua"), "scripts/text.lua missing")
  local objects = loadCached("objects/pack.lua")
  for key, value in pairs(objects and objects.text or {}) do text[key] = value end
  assert(next(text), "the " .. game .. " text extraction is empty")
  writeJson("rse_text.json", text)
end)

local function numbered(list)
  local out = {}
  for num, value in pairs(list or {}) do
    if type(num) == "number" then out[tostring(num)] = value end
  end
  return out
end

stage("export_species", function()
  writeJson("rse_species.json", numbered(assert(loadCached("pokemon/names.lua"))))
end)
stage("export_moves", function()
  writeJson("rse_moves.json", numbered(assert(loadCached("pokemon/move_names.lua"))))
end)
stage("export_items", function()
  local pack = assert(loadCached("items/pack.lua"))
  local out = {}
  for num, row in pairs(pack.items or {}) do
    if type(num) == "number" and type(row) == "table" then
      out[tostring(num)] = { name = row.name, description = row.description,
                             description_pointer = itemDescriptionPointers[num] }
    end
  end
  writeJson("rse_items.json", out)
end)
stage("export_trainers", function()
  local pack = assert(loadCached("trainers.lua"))
  local out = {}
  for num, row in pairs(pack.trainers or {}) do
    if type(num) == "number" and type(row) == "table" then
      out[tostring(num)] = { name = row.name, class = row.class, className = row.className,
                             encounterMusic = row.encounterMusic }
    end
  end
  writeJson("rse_trainers.json", out)
  writeJson("rse_trainer_classes.json", numbered(pack.classNames))
end)

writeJson("rse_stages.json", stages)
print(Json.encode(stages))
