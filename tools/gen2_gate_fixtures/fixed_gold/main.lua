-- The correct call broken_gold's typo stood in for. Used by
-- tools/gate_gen2.lua to prove a Gold translation mod's text:override
-- actually lands in data.gen2Text, the Data path Schemas.GEN2 routes the
-- `text` registry to. "55:4067" is a bank:address ROM pointer id, Gold's
-- text id shape (docs/mod-api-gen2-compat.md); the id itself does not need
-- to exist in a real ROM for this ROM-free check.
local mod = ...
mod.content.text:override("55:4067", "BONJOUR")
