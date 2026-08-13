-- Deliberately broken: `overrde` is a typo, not a Registry method. Used by
-- tools/gate_gen2.lua to prove the difference between a Gold mod that is
-- merely discovered (generation 1, where games=["gold"] leaves it gated out
-- before this chunk ever runs) and one that actually executes and fails
-- (generation 2).
local mod = ...
mod.content.text:overrde("55:4067", "BONJOUR")
