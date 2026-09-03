-- Proof a Gold translation is actually selected, not just that the mod
-- loads, plus the English-fallback half -- an unresolved pointer must
-- stay absent from data.gen2Text so the ROM's own English renders,
-- rather than showing up as an empty string or some placeholder.
--
-- Usage: luajit gate_gs_dialogue.lua <gen1recomp_root> <mod_dir>
--          <expectation_json_path>
--
-- The expected translation is read from a JSON file rather than a plain
-- argument: Windows narrows a child process's argv to the console's active
-- ANSI codepage before the C runtime's main() ever sees it, mangling any
-- translated text outside that codepage (confirmed: German round-tripped,
-- Japanese and Korean did not). A file read as raw UTF-8 bytes sidesteps
-- that narrowing entirely -- see tools/gate_gs_registries.lua's own
-- expectation-file argument for the same reason.

local engineRoot, modDir, expectationPath = ...
if not (engineRoot and modDir and expectationPath) then
  io.stderr:write("usage: luajit gate_gs_dialogue.lua <gen1recomp_root> <mod_dir> "
    .. "<expectation_json_path>\n")
  os.exit(2)
end

package.path = engineRoot .. "/?.lua;" .. engineRoot .. "/?/init.lua;" .. package.path

local ok, T = pcall(require, "tests.modkit")
if not ok then
  io.stderr:write("unable to load tests.modkit from " .. engineRoot .. ": " .. tostring(T) .. "\n")
  os.exit(2)
end

local jsonOk, Json = pcall(require, "src.link.Json")
if not jsonOk then
  io.stderr:write("unable to load JSON decoder for dialogue expectations\n")
  os.exit(2)
end
local file = io.open(expectationPath, "rb")
if not file then
  io.stderr:write("dialogue expectation file is missing: " .. expectationPath .. "\n")
  os.exit(2)
end
local body = file:read("*a")
file:close()
local expectation = Json.decode(body)
if type(expectation) ~= "table"
    or type(expectation.resolved_pointer) ~= "string" or expectation.resolved_pointer == ""
    or type(expectation.expected_translation) ~= "string" or expectation.expected_translation == ""
    or type(expectation.unresolved_pointer) ~= "string" or expectation.unresolved_pointer == "" then
  io.stderr:write("dialogue expectation file is missing required fields\n")
  os.exit(2)
end
local resolvedPointer = expectation.resolved_pointer
local expectedTranslation = expectation.expected_translation
local unresolvedPointer = expectation.unresolved_pointer

-- Crystal's own dialogue layer is optional the same way its registries
-- already are in tools/gate_gs_registries.lua (a language with no Crystal
-- corpus, or nothing resolved, ships no crystal_* fields at all).
local crystalResolvedPointer = expectation.crystal_resolved_pointer
local crystalExpectedTranslation = expectation.crystal_expected_translation
local crystalUnresolvedPointer = expectation.crystal_unresolved_pointer
local hasCrystal = type(crystalResolvedPointer) == "string" and crystalResolvedPointer ~= ""
  and type(crystalExpectedTranslation) == "string" and crystalExpectedTranslation ~= ""
  and type(crystalUnresolvedPointer) == "string" and crystalUnresolvedPointer ~= ""
if (crystalResolvedPointer or crystalExpectedTranslation or crystalUnresolvedPointer) and not hasCrystal then
  io.stderr:write("dialogue expectation file has partial crystal_* fields\n")
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

-- root = modDir's parent, path = its own name (see tools/gate_gen2.lua's
-- loadFixture for why an absolute path can't be handed to root="" -- it
-- breaks on Windows).
local modParent, modName = modDir:match("^(.*)[/\\]([^/\\]*)$")
if not modParent then modParent, modName = ".", modDir end
local result = T.sdk.loadMod(modName, { generation = 2, root = modParent })

check(#result.errors == 0, "the dialogue mod loads with no errors")
local mod = next(result.mods) and select(2, next(result.mods))
check(mod ~= nil and mod.state == "loaded", "the dialogue mod reaches state=loaded")

local text = result.data.gen2Text or {}
check(text[resolvedPointer] == expectedTranslation,
  ("gen2Text[%s] is the expected translation (got %q)"):format(resolvedPointer, tostring(text[resolvedPointer])))
check(text[unresolvedPointer] == nil,
  ("gen2Text[%s] stays absent (English fallback), not overridden with a guess"):format(unresolvedPointer))

result.release()

-- Crystal's own dialogue layer: its own pointer space (mostly disjoint from
-- Gold/Silver's own, see pipeline.crystal_mod's own docstring), merged into
-- the SAME data.gen2Text as the Gold/Silver check above -- src/mods/
-- Schemas.lua's GEN2 table routes the "text" registry to gen2Text, not
-- data.text (that is "rom_text"'s target instead) -- but only registered
-- under GameVersion=="crystal" (pipeline.gs_mod._CRYSTAL_GUARD/
-- dialogue_crystal.lua). Proven the same way tools/gate_gs_registries.lua
-- already proves its Crystal registries: selected under Crystal, absent
-- (English fallback) for an unresolved pointer, and never leaking onto a
-- Gold or Silver save.
if hasCrystal then
  local GameVersion = require("src.core.GameVersion")
  for _, edition in ipairs({ "gold", "silver", "crystal" }) do
    GameVersion.set(edition)
    local editionResult = T.sdk.loadMod(modName, { generation = 2, root = modParent })
    check(#editionResult.errors == 0,
      "the dialogue mod loads under GameVersion=" .. edition .. " for Crystal checks")
    local editionText = editionResult.data.gen2Text or {}
    if edition == "crystal" then
      check(editionText[crystalResolvedPointer] == crystalExpectedTranslation,
        ("text[%s] is the expected Crystal translation (got %q)"):format(
          crystalResolvedPointer, tostring(editionText[crystalResolvedPointer])))
      check(editionText[crystalUnresolvedPointer] == nil,
        ("text[%s] stays absent (English fallback), not overridden with a guess"):format(crystalUnresolvedPointer))
    else
      check(editionText[crystalResolvedPointer] ~= crystalExpectedTranslation,
        ("text[%s] does not leak into %s"):format(crystalResolvedPointer, edition))
    end
    editionResult.release()
  end
end

if failures > 0 then
  io.stderr:write(failures .. " gs dialogue gate check(s) failed\n")
  os.exit(1)
end
print("all gs dialogue gate checks passed")
os.exit(0)
