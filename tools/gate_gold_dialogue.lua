-- Proof a Gold translation is actually selected, not just that the mod
-- loads, plus the English-fallback half -- an unresolved pointer must
-- stay absent from data.gen2Text so the ROM's own English renders,
-- rather than showing up as an empty string or some placeholder.
--
-- Usage: luajit gate_gold_dialogue.lua <gen1recomp_root> <mod_dir>
--          <resolved_pointer> <expected_translation> <unresolved_pointer>

local engineRoot, modDir, resolvedPointer, expectedTranslation, unresolvedPointer = ...
if not (engineRoot and modDir and resolvedPointer and expectedTranslation and unresolvedPointer) then
  io.stderr:write("usage: luajit gate_gold_dialogue.lua <gen1recomp_root> <mod_dir> "
    .. "<resolved_pointer> <expected_translation> <unresolved_pointer>\n")
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
  io.stderr:write(failures .. " gold dialogue gate check(s) failed\n")
  os.exit(1)
end
print("all gold dialogue gate checks passed")
os.exit(0)
