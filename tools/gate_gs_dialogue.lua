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

if failures > 0 then
  io.stderr:write(failures .. " gs dialogue gate check(s) failed\n")
  os.exit(1)
end
print("all gs dialogue gate checks passed")
os.exit(0)
