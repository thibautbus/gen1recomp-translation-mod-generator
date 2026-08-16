-- Side-by-side headless loader gate: the real RBY archive's shape (no
-- `games` field) and a Gold mod (games=["gold"]) loaded from the SAME
-- virtual mods directory, proving they never collide and each is selected
-- for its own generation. This is not a LÖVE boot or rendering test.
--
-- Usage: luajit gate_gold_package.lua <gen1recomp_root> <rby_mod_dir> <gold_mod_dir>

local engineRoot, rbyDir, goldDir = ...
if not engineRoot or engineRoot == "" or not rbyDir or rbyDir == "" or not goldDir or goldDir == "" then
  io.stderr:write("usage: luajit gate_gold_package.lua <gen1recomp_root> <rby_mod_dir> <gold_mod_dir>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path

local ok, T = pcall(require, "tests.modkit")
if not ok then
  io.stderr:write("unable to load tests.modkit from " .. engineRoot .. ": " .. tostring(T) .. "\n")
  os.exit(2)
end

local failures = 0

local function check(condition, message)
  if condition then
    print("ok - " .. message)
  else
    failures = failures + 1
    io.stderr:write("FAIL - " .. message .. "\n")
  end
end

-- rbyDir and goldDir are two unrelated absolute directories (no shared
-- parent in general), so the root=parent/path=name split that
-- tools/gate_gen2.lua and tools/gate_gold_dialogue.lua use for a single
-- absolute directory does not apply here: no single root can relativize
-- both at once. Build a tiny two-alias filesystem instead, one FsIo
-- instance per directory rooted at that directory itself, so every path
-- handed to io.open is a normal root .. "/" .. relative-fragment join and
-- never runs into the Windows drive-letter breakage root="" has (see
-- gate_gen2.lua's loadFixture).
local FsIo = require("tests.fs_io")

local function dualFs(rby, gold)
  local roots = { rby = FsIo.new(rby), gold = FsIo.new(gold) }
  local function split(path)
    local name, rest = path:match("^mods/([^/]+)(.*)$")
    return roots[name], rest
  end
  local fs = { root = "." }
  function fs.read(path) local r, rest = split(path) return r and r.read(rest) end
  function fs.write(path, body) local r, rest = split(path) return r and r.write(rest, body) end
  function fs.load(path) local r, rest = split(path) return r and r.load(rest) end
  function fs.getInfo(path)
    if path == "mods" then return { type = "directory" } end
    local r, rest = split(path)
    return r and r.getInfo(rest) or nil
  end
  function fs.getDirectoryItems(path)
    if path == "mods" then return { "gold", "rby" } end
    local r, rest = split(path)
    return r and r.getDirectoryItems(rest) or {}
  end
  return fs
end

local function loadBoth(generation)
  return T.sdk.loadMods({ "mods/rby", "mods/gold" }, {
    generation = generation, fs = dualFs(rbyDir, goldDir),
  })
end

-- Generation 1: the RBY mod (no `games` field, covering all of Gen 1 by
-- default) loads; the Gold mod (games=["gold"]) is gated out before its
-- main.lua ever runs.
do
  local result = loadBoth(1)
  local rby, gold = result.mods["translation-fr"], result.mods["translation-fr-gen2"]
  check(rby ~= nil and rby.state == "loaded", "gen1: translation-fr loads")
  check(gold ~= nil and gold.state == "wrong_generation", "gen1: translation-fr-gen2 is gated out")
  check(#result.errors == 0, "gen1: no error is raised for a mod never run")
  result.release()
end

-- Generation 2: the Gold mod loads; the RBY mod is gated out for the
-- mirror-image reason -- a manifest with no `games` field covers Gen 1
-- only, never Gen 2.
do
  local result = loadBoth(2)
  local rby, gold = result.mods["translation-fr"], result.mods["translation-fr-gen2"]
  check(gold ~= nil and gold.state == "loaded", "gen2: translation-fr-gen2 loads")
  check(rby ~= nil and rby.state == "wrong_generation",
    "gen2: translation-fr is gated out (no games field means Gen 1 only)")
  check(#result.errors == 0, "gen2: no error is raised for a mod never run")
  result.release()
end

if failures > 0 then
  io.stderr:write(failures .. " gold package gate check(s) failed\n")
  os.exit(1)
end
print("all gold package gate checks passed")
os.exit(0)
