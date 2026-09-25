-- Headless FireRed or LeafGreen ROM extraction under plain LuaJIT (no LÖVE,
-- no ROM in git).  The edition follows from <rom_sha1>: Rom.open hands it to
-- Versions.select, which moves every FireRed address onto LeafGreen's
-- (src/import/gba/editions/leafgreen_1_0.lua).
--
-- Runs the subset of gen1recomp's own FireRed extractor
-- (src/import/gba/extract_island1.lua's Extract.run and
-- src/import/RomExtractorGen3.lua) whose output a translation mod keys
-- its overrides by: the full gMapGroups census, then the script/text BFS
-- every game3 boot reads its dialogue from, the object-interaction pack
-- that src/import/gba/extract_scripts.lua's loadBundle merges into that
-- same text table, and the species/move/item/trainer packs the Gen 3
-- content registries patch. Graphics, tilesets, audio and chrome are
-- skipped: a translation mod needs none of them.
--
-- Nothing is written outside <out_dir>: the extractor's own cache writes
-- go to <out_dir>/cache (the same relative layout as a game3 boot's
-- CacheFS), and the tables this project joins against are re-emitted as
-- JSON next to it.
--
-- Usage: luajit tools/frlg/extract.lua <gen1recomp_root> <rom_path> <out_dir> <rom_sha1>
--
-- <rom_sha1> is computed and verified by pipeline/shared/roms.py before this
-- script runs (love.data.hash, what gen1recomp's own RomExtractorGen3 uses,
-- is not available under tests/love_stub.lua).
--
-- Writes, in <out_dir>:
--   frlg_text.json      runtime text key -> IR segment list (dialogue)
--   frlg_species.json   species number -> name
--   frlg_moves.json     move number -> name
--   frlg_items.json     item number -> { name, description }
--   frlg_trainers.json  trainer number -> { name, className, class }
--   frlg_trainer_classes.json  trainer class number -> name
--   frlg_stages.json    stage -> "ok" or the error message

local root, romPath, outDir, sha1 = ...
assert(root and romPath and outDir and sha1,
  "usage: <gen1recomp_root> <rom> <out_dir> <rom_sha1>")

package.path = table.concat({
  root .. "/?.lua",
  root .. "/?/init.lua",
  package.path,
}, ";")

love = require("tests.love_stub")

-- FileIO.makeCache creates its directories with lfs when it can, and with
-- `mkdir -p '<dir>'` through the shell otherwise, which only a POSIX shell
-- understands: cmd.exe refuses the command, no directory is made and every
-- stage's first cache write fails.  Without lfs, hand it one backed by the
-- mkdir syscall through the FFI, as src/import/CacheFs.lua does for the game
-- itself; it also keeps a path's `&` or quote away from any shell.
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
      -- FileIO splits a path on "/" only, so a Windows path reaches this
      -- with its backslash components joined: make each of them in turn.
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

local Json = require("src.link.Json")
local FileIO = require("src.import.gba.file_io")
local Rom = require("src.import.gba.rom")
local Versions = require("src.import.gba.versions")

local CACHE_ROOT = "data/generated/gba"
local imports = FileIO.makeImports(romPath, sha1, "firered")
local cache = FileIO.makeCache(outDir .. "/cache")

local stages = {}
local function stage(name, fn)
  local ok, err = pcall(fn)
  stages[name] = ok and "ok" or tostring(err)
  if not ok then
    io.stderr:write(("[frlg_extract] %s failed: %s\n"):format(name, tostring(err)))
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
  local chunk = assert(load(body, "@" .. rel, "t", {}))
  return chunk()
end

local version = assert(Versions.lookup(sha1), "unsupported FireRed/LeafGreen ROM " .. sha1)

-- Census first: it registers every map header the script BFS seeds from
-- (Extract.run does the same before writeBundleFromRom).
local scriptBundle
local rom = assert(Rom.open(imports, "firered"))
local censusOk = stage("census", function()
  local MapTree = require("src.import.gba.map_tree")
  local MapCatalog = require("src.import.gba.map_catalog")
  MapCatalog.rebuildIndex()
  local census = assert(MapTree.walk(rom, version))
  local mapOrder, byEngine = MapCatalog.allOrder(census, {})
  MapCatalog.registerOrder(rom, version, mapOrder, byEngine)
  rom:clearCache()
end)

if censusOk then
  stage("scripts", function()
    local ExtractScripts = require("src.import.gba.extract_scripts")
    scriptBundle = ExtractScripts.writeBundleFromRom(rom, cache, CACHE_ROOT, version)
  end)
  stage("object_interactions", function()
    require("src.import.gba.object_interactions_extract")
      .writeExtract(rom, cache, CACHE_ROOT, version)
  end)
  stage("trainers", function()
    require("src.import.gba.trainer_extract").run(rom, cache, {
      cacheRoot = CACHE_ROOT,
      scripts = scriptBundle and scriptBundle.scripts,
      text = scriptBundle and scriptBundle.text,
    })
  end)
end
stage("pokemon", function()
  require("src.import.gba.pokemon_extract").run(rom, cache, { cacheRoot = CACHE_ROOT })
end)
stage("items", function()
  require("src.import.gba.items_extract").run(rom, cache, { cacheRoot = CACHE_ROOT })
end)
-- region_map/names.lua: the cart's own map section names, which the map name
-- popup, the region map and the save menu print through Strings()
-- (MapSectionsExtract.ensureGenerated reads them from this file).
stage("map_sections", function()
  require("src.import.gba.map_preview_extract").run(rom, cache, { cacheRoot = CACHE_ROOT })
end)
-- trades/ingame_trades.lua: the nine in-game trades, whose nickname and OT
-- name the scripts print through Strings() (natives_trade.lua:244, :260).
stage("ingame_trades", function()
  require("src.import.gba.ingame_trades_extract").run(rom, cache, { cacheRoot = CACHE_ROOT })
end)
rom:clearCache()
imports:_close()

-- The text table exactly as a game3 boot builds it
-- (ExtractScripts.loadBundle): the BFS text, which already carries the
-- label-keyed standard-script text (merge_std_text in extract_scripts.lua),
-- then the object-interaction pack's own text on top.
stage("export_text", function()
  local text = assert(loadCached("scripts/text.lua"), "scripts/text.lua missing")
  local objects = loadCached("objects/pack.lua")
  for key, value in pairs(objects and objects.text or {}) do text[key] = value end
  writeJson("frlg_text.json", text)
end)

local function numbered(list)
  local out = {}
  for num, value in pairs(list or {}) do
    if type(num) == "number" then out[tostring(num)] = value end
  end
  return out
end

stage("export_species", function()
  writeJson("frlg_species.json", numbered(assert(loadCached("pokemon/names.lua"))))
end)
stage("export_moves", function()
  writeJson("frlg_moves.json", numbered(assert(loadCached("pokemon/move_names.lua"))))
end)
stage("export_items", function()
  local pack = assert(loadCached("items/pack.lua"))
  local out = {}
  for num, row in pairs(pack.items or {}) do
    if type(num) == "number" and type(row) == "table" then
      out[tostring(num)] = { name = row.name, description = row.description }
    end
  end
  writeJson("frlg_items.json", out)
end)
stage("export_trainers", function()
  local pack = assert(loadCached("trainers.lua"))
  local out = {}
  for num, row in pairs(pack.trainers or {}) do
    if type(num) == "number" and type(row) == "table" then
      out[tostring(num)] = { name = row.name, class = row.class,
                             className = row.className }
    end
  end
  writeJson("frlg_trainers.json", out)
  writeJson("frlg_trainer_classes.json", numbered(pack.classNames))
end)

writeJson("frlg_stages.json", stages)
print(Json.encode(stages))
