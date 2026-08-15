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

local function loadBoth(generation)
  -- root = "" so the absolute rbyDir/goldDir paths aliasFs hands to FsIo
  -- resolve as-is (see tools/gate_gen2.lua's loadFixture for why).
  return T.sdk.loadMods({ rbyDir, goldDir }, { generation = generation, root = "" })
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
