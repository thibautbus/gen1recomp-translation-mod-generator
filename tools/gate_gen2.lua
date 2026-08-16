-- ROM-free, LOVE-free proof that a Gold mod produced by this project's
-- pipeline actually loads under generation = 2.
--
-- `modkit validate` cannot make this check: its driver never injects
-- `generation` into Loader.new (tools/modkit.py DRIVER_TEMPLATE, upstream),
-- so a manifest naming only games=["gold"] is gated out before its entry
-- chunk ever runs and still reports "ok".
--
-- This mirrors the recipe the engine's own
-- tests/engine/gate_gen2_mod_api.lua uses against itself: tests.modkit.sdk
-- (Loader.new({ generation = ... }) under tests.love_stub, no ROM, no LÖVE).
--
-- Usage: luajit tools/gate_gen2.lua <gen1recomp_checkout_root> <fixtures_root>
-- Both paths are taken as given (absolute paths recommended: this script
-- does not depend on being invoked from any particular working directory).

local engineRoot = arg[1]
local fixturesRoot = arg[2]
if not engineRoot or engineRoot == "" or not fixturesRoot or fixturesRoot == "" then
  io.stderr:write("usage: luajit gate_gen2.lua <gen1recomp_checkout_root> <fixtures_root>\n")
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

local function loadFixture(name, generation)
  -- root = fixturesRoot, path = name (relative): FsIo.abs joins as
  -- base .. "/" .. path, so an absolute root plus a bare relative name
  -- resolves correctly on both POSIX and Windows. The previous approach
  -- (root = "", path = the absolute fixturesRoot .. "/" .. name) built
  -- base .. "/" .. absolutePath, which on Windows prepends a stray "/"
  -- before the drive letter (e.g. "/C:\Users\...\gen2_gate_fixtures\..."),
  -- an invalid path io.open silently fails to open -- a real frozen
  -- Windows GUI build reported every fixture check failing with
  -- "broken_gold is discovered" itself already false.
  return T.sdk.loadMod(name, { generation = generation, root = fixturesRoot })
end

-- 1. Under generation 1, games=["gold"] does not cover this generation:
--    the mod is gated out before its entry chunk ever runs, so a broken
--    chunk stays invisible -- the exact blind spot `modkit validate` has
--    today because its driver never sets `generation`.
do
  local result = loadFixture("broken_gold", 1)
  local mod = result.mods.broken_gold
  check(mod ~= nil, "gen1: broken_gold is discovered")
  check(mod ~= nil and mod.state == "wrong_generation",
    "gen1: broken_gold is gated out (state=wrong_generation), never run")
  check(#result.errors == 0, "gen1: a mod that never ran raises no error")
  result.release()
end

-- 2. Under generation 2 (our own gate, not modkit validate), the SAME
--    broken mod actually executes and fails -- proof this harness really
--    loads Gold mods rather than only discovering them.
do
  local result = loadFixture("broken_gold", 2)
  local mod = result.mods.broken_gold
  check(mod ~= nil and mod.state == "failed",
    "gen2: broken_gold actually runs and fails (state=failed)")
  check(#result.errors == 1, "gen2: the failure is reported to loader.errors")
  result.release()
end

-- 3. The fixed mod loads clean under generation 2, and its text:override
--    lands in data.gen2Text -- the Data path Schemas.GEN2 routes the
--    `text` registry to for Gold -- proving a translation mod's registry
--    write reaches the table Gold's TextBox actually reads.
do
  local result = loadFixture("fixed_gold", 2)
  local mod = result.mods.fixed_gold
  check(#result.errors == 0, "gen2: fixed_gold loads with no errors")
  check(mod ~= nil and mod.state == "loaded", "gen2: fixed_gold reaches state=loaded")
  check(result.data.gen2Text ~= nil and result.data.gen2Text["55:4067"] == "BONJOUR",
    "gen2: text:override(\"55:4067\", ...) lands in data.gen2Text")
  result.release()
end

if failures > 0 then
  io.stderr:write(failures .. " gen2 gate check(s) failed\n")
  os.exit(1)
end
print("all gen2 gate checks passed")
os.exit(0)
