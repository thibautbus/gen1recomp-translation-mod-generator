-- A representative RBY-shaped mod: no `games` field, matching
-- pipeline/mod.py's real manifest_body for translation-<lang> (verified:
-- it does not emit the key), which covers every Gen 1 game and none of
-- Gen 2. Used by tools/gate_gs_package.lua to prove the Gold mod
-- coexists with, rather than collides with, the real RBY archive.
local mod = ...
mod.content.font:register("ttf", {})
