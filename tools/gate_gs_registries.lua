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
-- Usage: luajit gate_gs_registries.lua <gen1recomp_root> <mod_dir>

local engineRoot, modDir = ...
if not engineRoot or engineRoot == "" or not modDir or modDir == "" then
  io.stderr:write("usage: luajit gate_gs_registries.lua <gen1recomp_root> <mod_dir>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path

local ok, T = pcall(require, "tests.modkit")
if not ok then
  io.stderr:write("unable to load tests.modkit from " .. engineRoot .. ": " .. tostring(T) .. "\n")
  os.exit(2)
end

-- Single source of truth for the four Silver/Crystal #DEX expectation keys:
-- both the top-level shape validation (an "optional" member, like
-- species_dex_text2) and the per-edition check below (which must NOT be
-- routed through the generic single-load targets/fields check further down,
-- see editionSpecific's own comment) need the exact same name set.
local EDITION_DEX_TEXT_KEYS = {
  species_dex_text_silver = true, species_dex_text2_silver = true,
  species_dex_text_crystal = true, species_dex_text2_crystal = true,
}

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
  -- species_dex_text2 (the #DEX entry's second page) is present only when
  -- the language's corpus actually preserved one: ja-Hrkt/ko's
  -- dex_entries_gold rows never do (verified against poke-corpus), so the
  -- Python side omits the key entirely for those rather than shipping an
  -- empty expectation. Still verified below like any other expectation
  -- when it IS present.
  --
  -- species_dex_text_{silver,crystal}/species_dex_text2_{silver,crystal}
  -- are Silver's/Crystal's OWN #DEX flavor text (see generate_gs_mod's
  -- docstring): unlike every registry above, these only apply behind a
  -- GameVersion-gated conditional layer, so a bug in that layer never shows
  -- up in the unconditional checks below. Optional the same way
  -- species_dex_text2 is (a missing/empty per-edition catalog -- e.g. no
  -- Crystal corpus supplied -- degrades to omitting the key, not a
  -- BuildError; see pipeline.gs_mod._write_gate_expectations).
  local optional = { species_dex_text2 = true }
  for name, _ in pairs(EDITION_DEX_TEXT_KEYS) do optional[name] = true end
  for _, name in ipairs(required) do
    local value = expectations[name]
    if type(value) ~= "table" or type(value.id) ~= "string" or value.id == ""
        or type(value.value) ~= "string" or value.value == "" then
      io.stderr:write("registry expectation is missing or empty: " .. name .. "\n")
      os.exit(2)
    end
  end
  for name, _ in pairs(expectations) do
    local known = optional[name] or false
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

-- root = modDir's parent, path = its own name (see tools/gate_gen2.lua's
-- loadFixture for why an absolute path can't be handed to root="" -- it
-- breaks on Windows).
local modParent, modName = modDir:match("^(.*)[/\\]([^/\\]*)$")
if not modParent then modParent, modName = ".", modDir end
local result = T.sdk.loadMod(modName, { generation = 2, root = modParent })

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
    species_dex_text2 = function(id) return data.pokemon and data.pokemon[id] and data.pokemon[id].dexEntry end,
    move_names = function(id) return data.moves and data.moves[id] end,
    item_names = function(id) return data.items and data.items[id] end,
    trainer_class_names = function(id) return data.gen2Trainers and data.gen2Trainers.classes and data.gen2Trainers.classes[id] end,
    landmarks = function(id) return data.gen2Landmarks and data.gen2Landmarks.landmarks and data.gen2Landmarks.landmarks[id] end,
  }
  local fields = {
    strings = "value",
    species_names = "name", species_kinds = "kind", species_dex_text = "text",
    species_dex_text2 = "text2",
    move_names = "name", item_names = "name", trainer_class_names = "name", landmarks = "name",
  }
  -- Verified separately below, each under its own edition's GameVersion --
  -- these only ever patch data.pokemon on a Silver/Crystal save, so
  -- checking them against this (default-edition) load's data would either
  -- find the guard never ran (false pass) or nothing at all.
  local editionSpecific = EDITION_DEX_TEXT_KEYS
  for name, expected in pairs(expectations) do
    local target = targets[name]
    local field = fields[name]
    check(type(expected) == "table" and type(expected.id) == "string" and type(expected.value) == "string",
      name .. " gate expectation has a valid shape")
    if editionSpecific[name] then
      -- shape already checked above; value verified under its own edition
      -- load further down.
    elseif name == "oak_speech" and type(expected) == "table" then
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

  -- The `pokemon` registry's own dexEntry (verified above) is a separate
  -- table from data.gen2Pokedex.entries, which is what
  -- src/ui/gen2/PokedexMenu.lua actually reads for the #DEX screen; before
  -- gen1recomp v0.2.33 nothing routed one into the other, so a translated
  -- #DEX entry validated against the registry but stayed invisible in-game
  -- (see src/core/gen2/PokedexText.lua's own docstring). Game2:load() closes
  -- that gap by calling PokedexText.apply(data) once after the mod merge;
  -- this SDK harness never boots a real Game2, so the bridge is invoked here
  -- directly, over a synthetic pre-merge entry (STALE placeholders standing
  -- in for the real ROM-extracted English #DEX text Game2:load() would have
  -- loaded from disk), to prove this translation's dexEntry actually reaches
  -- the same table the #DEX screen reads.
  if expectations.species_dex_text then
    local PokedexText = require("src.core.gen2.PokedexText")
    local dexId = expectations.species_dex_text.id
    data.gen2Pokedex = data.gen2Pokedex or {}
    data.gen2Pokedex.entries = data.gen2Pokedex.entries or {}
    data.gen2Pokedex.entries[dexId] = { kind = "STALE", text = "STALE", text2 = "STALE" }
    PokedexText.apply(data)
    eq(data.gen2Pokedex.entries[dexId].text, expectations.species_dex_text.value,
      "PokedexText.apply projects species_dex_text[" .. dexId .. "] onto the #DEX screen's own read table")
    if expectations.species_dex_text2 then
      eq(data.gen2Pokedex.entries[dexId].text2, expectations.species_dex_text2.value,
        "PokedexText.apply projects species_dex_text2[" .. dexId .. "] onto the #DEX screen's own read table")
    end
  end

  -- Silver's/Crystal's own #DEX flavor text (generate_gs_mod's silver_
  -- registration/crystal_dex_registration) each patch mod.content.pokemon
  -- behind their own "GameVersion.get() == edition" guard, AFTER the
  -- unconditional Gold catalog checked above -- so re-loading the same mod
  -- under that edition's GameVersion is the only way to prove the guard
  -- actually fires and the patch lands, instead of only proving the
  -- unconditional Gold text is correct (the gap a prior review found: this
  -- gate never exercised these guards at all, always loading under the
  -- SDK's default GameVersion, which is neither "silver" nor "crystal").
  -- GameVersion is process-wide (see src/core/GameVersion.lua's own
  -- docstring), so it must be set again before each edition's load, and the
  -- previous result released first -- Sdk.loadMods captures the runtime
  -- bus globals into one shared slot, and capturing over an unreleased
  -- load would leak/corrupt that slot instead of restoring it.
  result.release()
  local editionExpectations = {
    silver = { text = expectations.species_dex_text_silver, text2 = expectations.species_dex_text2_silver },
    crystal = { text = expectations.species_dex_text_crystal, text2 = expectations.species_dex_text2_crystal },
  }
  local anyEditionExpectation = expectations.species_dex_text_silver or expectations.species_dex_text_crystal
  if anyEditionExpectation then
    local okGameVersion, GameVersion = pcall(require, "src.core.GameVersion")
    local gameVersionUsable = okGameVersion and type(GameVersion) == "table" and type(GameVersion.set) == "function"
    check(gameVersionUsable,
      "src.core.GameVersion is loadable and exposes set() for the edition-specific #DEX checks")
    if gameVersionUsable then
      for edition, pages in pairs(editionExpectations) do
        if pages.text or pages.text2 then
          GameVersion.set(edition)
          local editionResult = T.sdk.loadMod(modName, { generation = 2, root = modParent })
          check(#editionResult.errors == 0, "the mod loads with no errors under GameVersion=" .. edition)
          local editionData = editionResult.data
          if pages.text then
            local record = editionData.pokemon and editionData.pokemon[pages.text.id]
            eq(record and record.dexEntry and record.dexEntry.text, pages.text.value,
              "species_dex_text_" .. edition .. "[" .. pages.text.id .. "] replaces Gold's text under GameVersion=" .. edition)
          end
          if pages.text2 then
            local record = editionData.pokemon and editionData.pokemon[pages.text2.id]
            eq(record and record.dexEntry and record.dexEntry.text2, pages.text2.value,
              "species_dex_text2_" .. edition .. "[" .. pages.text2.id .. "] replaces Gold's text under GameVersion=" .. edition)
          end
          editionResult.release()
        end
      end
    end
  end

  if failures > 0 then
    io.stderr:write(failures .. " gs registry gate check(s) failed\n")
    os.exit(1)
  end
  print("all gs registries gate checks passed")
  os.exit(0)
end

local pokemon = data.pokemon and data.pokemon.BULBASAUR
eq(pokemon and pokemon.name, "BULBIZARRE", "pokemon BULBASAUR.name is the real translation")
eq(pokemon and pokemon.dexEntry and pokemon.dexEntry.kind, "GRAINE", "pokemon BULBASAUR.dexEntry.kind is the real translation")
check(pokemon and pokemon.dexEntry and pokemon.dexEntry.text and #pokemon.dexEntry.text > 0,
  "pokemon BULBASAUR.dexEntry.text is a non-empty real translation")
check(pokemon and pokemon.dexEntry and pokemon.dexEntry.text2 and #pokemon.dexEntry.text2 > 0,
  "pokemon BULBASAUR.dexEntry.text2 is a non-empty real translation")

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
  io.stderr:write(failures .. " gs registries gate check(s) failed\n")
  os.exit(1)
end
print("all gs registries gate checks passed")
os.exit(0)
