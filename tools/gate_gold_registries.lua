-- Proof each index-joined registry (pokemon, moves, items, trainers)
-- actually lands where the routed generation=2 target reads it, not
-- just that the patch calls run without error. pokemon/moves/items
-- keep the shared Gen 1 target
-- (data.pokemon/moves/items); trainers is routed to data.gen2Trainers
-- .classes -- verified against src/mods/Schemas.lua's R.trainers comment
-- and the real merged data below, since a mod author-facing call shape
-- staying the same (mod.content.trainers:patch("BEAUTY", ...)) does not
-- guarantee the READ side does too.  The same gate also invokes the public
-- Oak-speech hook, since those ROM-derived strings are consumed by the
-- speech object rather than by a content registry.
--
-- Usage: luajit gate_gold_registries.lua <gen1recomp_root> <mod_dir>

local engineRoot, modDir = ...
if not engineRoot or engineRoot == "" or not modDir or modDir == "" then
  io.stderr:write("usage: luajit gate_gold_registries.lua <gen1recomp_root> <mod_dir>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path

local ok, T = pcall(require, "tests.modkit")
if not ok then
  io.stderr:write("unable to load tests.modkit from " .. engineRoot .. ": " .. tostring(T) .. "\n")
  os.exit(2)
end

-- The build flow supplies a small JSON file containing one id/value from each
-- generated catalog.  Keeping the old no-argument fixture below preserves a
-- useful focused regression test while making the release gate check the
-- actual language/output instead of hard-coded French sample strings.
local expectationPath = arg[3]
local expectations = nil
if expectationPath and expectationPath ~= "" then
  local jsonOk, Json = pcall(require, "src.link.Json")
  if not jsonOk then
    io.stderr:write("unable to load JSON decoder for registry expectations\n")
    os.exit(2)
  end
  local file = io.open(expectationPath, "rb")
  if not file then
    io.stderr:write("registry expectation file is missing: " .. expectationPath .. "\n")
    os.exit(2)
  end
  local body = file:read("*a")
  file:close()
  expectations = Json.decode(body)
  if type(expectations) ~= "table" then
    io.stderr:write("registry expectation file is not a JSON object\n")
    os.exit(2)
  end
  local required = {
    "strings", "species_names", "species_kinds", "species_dex_text", "move_names",
    "item_names", "trainer_class_names", "landmarks", "oak_speech",
  }
  for _, name in ipairs(required) do
    local value = expectations[name]
    if type(value) ~= "table" or type(value.id) ~= "string" or value.id == ""
        or type(value.value) ~= "string" or value.value == "" then
      io.stderr:write("registry expectation is missing or empty: " .. name .. "\n")
      os.exit(2)
    end
  end
  for name, _ in pairs(expectations) do
    local known = false
    for _, requiredName in ipairs(required) do
      if name == requiredName then known = true break end
    end
    if not known then
      io.stderr:write("registry expectation is unexpected: " .. tostring(name) .. "\n")
      os.exit(2)
    end
  end
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

local function eq(actual, expected, message)
  check(actual == expected, ("%s (got %q, want %q)"):format(message, tostring(actual), tostring(expected)))
end

-- root = "" so the absolute modDir aliasFs hands to FsIo resolves as-is
-- (see tools/gate_gen2.lua's loadFixture for why).
local result = T.sdk.loadMod(modDir, { generation = 2, root = "" })

check(#result.errors == 0, "the mod loads with no errors")
local mod = next(result.mods) and select(2, next(result.mods))
check(mod ~= nil and mod.state == "loaded", "the mod reaches state=loaded")

local data = result.data
if expectations then
  local targets = {
    strings = function(id) return data.strings and { value = data.strings[id] } end,
    species_names = function(id) return data.pokemon and data.pokemon[id] end,
    species_kinds = function(id) return data.pokemon and data.pokemon[id] and data.pokemon[id].dexEntry end,
    species_dex_text = function(id) return data.pokemon and data.pokemon[id] and data.pokemon[id].dexEntry end,
    move_names = function(id) return data.moves and data.moves[id] end,
    item_names = function(id) return data.items and data.items[id] end,
    trainer_class_names = function(id) return data.gen2Trainers and data.gen2Trainers.classes and data.gen2Trainers.classes[id] end,
    landmarks = function(id) return data.gen2Landmarks and data.gen2Landmarks.landmarks and data.gen2Landmarks.landmarks[id] end,
  }
  local fields = {
    strings = "value",
    species_names = "name", species_kinds = "kind", species_dex_text = "text",
    move_names = "name", item_names = "name", trainer_class_names = "name", landmarks = "name",
  }
  for name, expected in pairs(expectations) do
    local target = targets[name]
    local field = fields[name]
    check(type(expected) == "table" and type(expected.id) == "string" and type(expected.value) == "string",
      name .. " gate expectation has a valid shape")
    if name == "oak_speech" and type(expected) == "table" then
      local Runtime = require("src.mods.Runtime")
      local speech = { texts = {} }
      local steps = {}
      local returned = Runtime.call("intro.oak_speech.build",
        function(value) return value end, steps, speech)
      eq(returned, steps, "intro.oak_speech.build preserves the step list")
      eq(speech.texts[expected.id], expected.value,
        "oak_speech[" .. expected.id .. "] is selected by the Gold consumer")
    elseif target and field and type(expected) == "table" then
      local record = target(expected.id)
      eq(record and record[field], expected.value, name .. "[" .. expected.id .. "]." .. field .. " is selected")
    else
      check(false, "unsupported registry expectation " .. tostring(name))
    end
  end
  result.release()
  if failures > 0 then
    io.stderr:write(failures .. " gold registry gate check(s) failed\n")
    os.exit(1)
  end
  print("all gold registries gate checks passed")
  os.exit(0)
end

local pokemon = data.pokemon and data.pokemon.BULBASAUR
eq(pokemon and pokemon.name, "BULBIZARRE", "pokemon BULBASAUR.name is the real translation")
eq(pokemon and pokemon.dexEntry and pokemon.dexEntry.kind, "GRAINE", "pokemon BULBASAUR.dexEntry.kind is the real translation")
check(pokemon and pokemon.dexEntry and pokemon.dexEntry.text and #pokemon.dexEntry.text > 0,
  "pokemon BULBASAUR.dexEntry.text is a non-empty real translation")

local move = data.moves and data.moves.ABSORB
eq(move and move.name, "VOL-VIE", "moves ABSORB.name is the real translation")

local item = data.items and data.items.AMULET_COIN
eq(item and item.name, "PIECE RUNE", "items AMULET_COIN.name is the real translation")

-- Trainers is Gen-2-routed to gen2Trainers.classes (Schemas.lua's R.trainers
-- comment); data.trainers.BEAUTY, the Gen 1 path, must stay untouched.
local trainerClass = data.gen2Trainers and data.gen2Trainers.classes and data.gen2Trainers.classes.BEAUTY
eq(trainerClass and trainerClass.name, "CANON", "gen2Trainers.classes.BEAUTY.name is the real translation")

-- landmarks is routed to data.gen2Landmarks.landmarks (Schemas.lua's
-- Schemas.GEN2.landmarks entry), one level under the registry name
-- itself, unlike pokemon/moves/items.
local landmark = data.gen2Landmarks and data.gen2Landmarks.landmarks and data.gen2Landmarks.landmarks.LANDMARK_AZALEA_TOWN
eq(landmark and landmark.name, "ECORCIA", "gen2Landmarks.landmarks.LANDMARK_AZALEA_TOWN.name is the real translation")
check(not (data.trainers and data.trainers.BEAUTY and data.trainers.BEAUTY.name == "CANON"),
  "the Gen 1 data.trainers path is not where the Gen 2 patch landed")

result.release()

if failures > 0 then
  io.stderr:write(failures .. " gold registries gate check(s) failed\n")
  os.exit(1)
end
print("all gold registries gate checks passed")
os.exit(0)
