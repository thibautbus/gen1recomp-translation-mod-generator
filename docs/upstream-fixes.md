# Translation: upstream engine gaps

Both mods use only the public `gen1recomp` content and hook APIs. The
following strings are tracked here because translating them correctly requires
new upstream APIs; they must not be implemented by reaching into private UI
classes from the translation mod.

## RBY

### Verified working, not a gap

The SummaryMenu's own stat labels (`NAME`, `ATTACK`, `DEFENSE`, `SPEED`,
`SPECIAL`) are **not** blocked, despite each needing a `forced_dynamic_keys`
entry in `config/shared/engine_manifest.json` to be reachable at all: the
callsite (`ui/SummaryMenu.lua:150-157`) reads them from a runtime table
(`Strings(s[1])`), which a static literal scanner cannot see, so they must be
force-added to the catalog by key -- but `Strings()` itself is an ordinary
runtime lookup by string value, indifferent to whether the call site used a
literal or a variable. `config/rby/semantic_anchors.json` already carries a
working anchor for all five (`rb.stat_names.VitaminStats.2-5`,
`rb.start_sub_menus.TrainerInfo_NameMoneyTimeText`), confirmed translated
(`fr`: `FOR`/`DEF`/`VIT`/`SPE`/`NOM`) in a real build's generated
`strings.lua`. (A prior version of this README's "Remaining limitations"
listed these as English-only; that was stale.)

If a future gen1recomp version rewrote this callsite to pass a literal
(`Strings("ATTACK")` per case instead of `Strings(s[1])`), the scanner would
discover it on its own and `forced_dynamic_keys` would no longer be needed
to make the key reachable -- and the anchors themselves would likely become
redundant too, not just the manual catalog entry. Two things confirm this
rather than just the scanner gap: `corpus_to_engine("ATTACK@")` already
strips the trailing `@` down to plain `"ATTACK"`, the same normalization
ordinary aligned records get before exact/normalized auto-matching runs;
and although `ATTACK@` appears at two RedBlue qids
(`rb.stat_names.VitaminStats.2` and `rb.stat_mod_names.StatModTextStrings.0`),
both carry the identical translation in every shipped language (`fr`: `FOR`
for both), so the duplicate would not make an automatic match unsafe. The
anchors would stay harmless to keep either way -- explicit anchors still
win over auto-matching in the resolution order -- but the manual upkeep
they represent could be dropped.

### Required upstream capabilities

- **Stat-rise messages:** the `X ATTACK`/`X DEFENSE`/etc. battle items
  (`inventory/ItemEffects.lua:291`, `Strings("%s's\n%s rose!", b.name,
  stat:upper())`) and the vitamins -- `PROTEIN`, `IRON`, `CALCIUM`, `ZINC`,
  `CARBOS`, `HP UP` (`inventory/ItemEffects.lua:489`, `Strings("%s's %s\nrose!",
  monName(data, target), vitaminStat:upper())`) -- both substitute the raised
  stat's name as a raw uppercase Lua string (`stat:upper()`), never through
  `Strings()`, so no override can reach it. It always renders in English
  (`ATTACK`, `DEFENSE`, `SPECIAL`, `SPEED`, `HP`) regardless of language. The
  surrounding sentence template itself *is* translated
  (`overrides/<language>/rby/engine.json`'s `"%s's %s\nrose!"` entry), just
  not this one substituted word. A source comment at the second callsite
  notes the officially localized ROM text (`_VitaminStatRoseText`) puts the
  stat name in a different grammatical position per language (Spanish leads
  with the stat), which is why the override uses neutral wording rather than
  the exact localized phrasing.
- **Status condition abbreviations:** `PSN`/`PAR`/`BRN`/`FRZ`/`SLP` always
  render in English on the summary and party screens
  (`ui/SummaryMenu.lua:146`, `Font.draw(mon.status or "OK", 128, 48)`;
  `ui/PartyMenu.lua:821`, `Font.draw(mon.status, 136, y)`). This is a
  different, deeper gap than the stat labels above: `mon.status` is never
  passed through `Strings()` at all here, not even indirectly through a
  runtime table -- it is the raw internal state code, drawn directly. No
  override, anchor, or catalog entry can reach it; the callsite itself would
  need to route through `Strings(mon.status)` (or an equivalent translated
  lookup) first. The corpus already has the real, correct abbreviation for
  every language (`rb.status_ailments.PrintStatusAilment.{slp,psn,brn,frz,par}`,
  e.g. `fr`: `SOM`/`PSN`/`BRU`/`GEL`/`PAR`), confirmed by direct lookup, so
  only the callsite needs to change, not anything on this project's side.
- **Branching/stateful flavor-NPC dialogue** (`config/rby/literal_handlers.json`,
  now 2 handlers: Viridian City's second Youngster and the Museum 1F ticket
  clerk): each is blocked for a distinct, narrower reason than a prior
  version of this doc claimed (see the "narrowed" note below) -- reading
  `game.data.text` directly instead of through `Strings()` is, by itself,
  *not* a gap (confirmed in the section above), so only NPCs with a second,
  independent problem still need `map_scripts:register` reimplementation:
  - **Viridian City's second Youngster**: two of its three lines
    (`ViridianCityYoungster2OkThenText`,
    `ViridianCityYoungster2CaterpieAndWeedleDescriptionText`) aren't in
    `data/generated/text.lua` at all -- a comment at the callsite
    (`data/scripts/flavor/viridian_city.lua`) says they're "defined without
    a leading underscore in pokered/text/ViridianCity.asm and aren't
    present in data/generated/text.lua, so we fall back to the literal
    strings from pokered" -- gen1recomp's own ROM-text extractor appears to
    skip labels that don't start with `_`. Only the prompt line has a real,
    translatable qid; the other two have no override target to reach at
    all.
  - **Museum 1F's ticket clerk**: worse than a `Strings()` bypass -- its
    vanilla implementation (`data/scripts/story2.lua`, the `museumClerk`
    local function `M.MUSEUM_1F` shares between `talk` and the rope
    `onStep` trigger) never reads `game.data.text` either; every line
    (`"It's ¥50 for a\nchild's ticket."`, `"Right, ¥50!\nThank you!"`,
    `"You don't have\nenough money."`, `"Come again!"`,
    `"Take your time,\nand enjoy it all!"`) is a bare English string
    literal baked into the Lua source. No catalog, override, or anchor can
    reach a literal that was never looked up anywhere -- the callsite
    itself would need to change.

  **Narrowed from a prior version of this doc:** this section previously
  claimed the shared root cause was simply "reads `game.data.text`
  directly, not through `Strings()`", covering 6 handlers (adding the Bike
  Shop's clerk/middle-aged-woman/youngster and Route 24's Nugget-Bridge
  recruiter) plus a "no public API to give a Pokémon" blocker on Mt. Moon's
  Magikarp salesman. That was wrong: `src/core/Strings.lua`'s own header
  says extracted dialogue (`Data.text`) "already had a home... which a mod
  reaches through `mod.content.text`" -- a *second*, separate override path
  from `Strings()`/`mod.content.strings`, and `mod.content.text:override(id,
  value)` patches `game.data.text[id]` directly. So a script reading
  `t[label]` is translatable regardless of whether it goes through
  `Strings()` first. Confirmed directly in a real build's `lang/dialogue.lua`:
  `_BikeShopClerkHowDoYouLikeYourBicycleText`, `_BikeShopClerkOhThatsAVoucherText`,
  `_BikeShopExchangedVoucherText`, `_BikeShopMiddleAgedWomanText`,
  `_BikeShopYoungsterTheseBikesAreExpensiveText`/`CoolBikeText`,
  `_Route24CooltrainerM1*` (all 7 lines), and
  `_MtMoonPokecenterMagikarpSalesman*`/`_GotMonText` are all present with
  correct French text. Their vanilla scripts
  (`data/scripts/story2.lua`'s `TEXT_BIKESHOP_CLERK`,
  `data/scripts/flavor/bike_shop.lua`'s `show_text`-command entries --
  `Commands.show_text` also reads `ctx.game.data.text[textId]`,
  `src/script/Commands.lua:81` --, `data/scripts/story4.lua`'s
  `TEXT_ROUTE24_COOLTRAINER_M1` and `M.MT_MOON_POKECENTER`) already
  implement the same branching, and are *more* complete than this
  project's former reimplementations: they call
  `require("src.inventory.Bag").add(...)` for a real bag-capacity check
  (unrestricted for core engine code -- `SUPPORTED_REQUIRES` only
  constrains mod code) where the removed Route 24/Bike Shop handlers here
  could not, and `M.MT_MOON_POKECENTER` already calls
  `require("src.script.Commands").give_pokemon(...)` directly, so giving a
  Pokémon was never actually blocked for any NPC gen1recomp itself already
  implements. The 4 now-redundant handlers (and the "no public API to give
  a Pokémon" bullet) were removed from `config/rby/literal_handlers.json`
  and this doc; letting vanilla's talk scripts run is both simpler and more
  correct than the reimplementations were. The same check was run against
  every other flavor NPC found reading `game.data.text` directly this way
  -- Viridian City's `GAMBLER1`/`GIRL`, Pewter City's `SUPER_NERD1`/
  `SUPER_NERD2`, and the `gift()` helper family in `data/scripts/story5.lua`
  (8 NPCs: Celadon Diner's Coin Case, Celadon Mart 3F's TM18, Route 12 Gate
  2F's TM39, Celadon City's TM41, Cinnabar Lab's TM35, Viridian City's
  TM42, Silph Co 2F's TM36, Route 1's Potion sample) -- all confirmed
  already correctly translated in a real build's `dialogue.lua`, no config
  needed. Likewise the Pokédex "kind" classification
  (`src/ui/DexEntryMenu.lua:93`, `Font.draw(e.kind or "?", ...)`, no
  `Strings()` call) looked like the same deep gap as the status-ailment
  abbreviations above, but isn't: `pipeline/mod.py` already has a dedicated
  `species_kinds` catalog (`mod.content.pokemon:patch(id, {dexEntry =
  {kind = value}})`) built for exactly this field.

## Gold

### Fixed: rows already reachable through an existing public hook

`PcMenu`'s five storage-menu rows (`Withdraw Pokémon`, `Deposit Pokémon`,
`Change box`, `Move Pokémon w/o mail`, `See ya!`) and the battle party
submenu's `Switch`/`Stats` rows are *not* private-class reads: both already
run through public list hooks (`ui.pc.items`, `ui.party.submenu`) that this
mod already wraps for other rows (`Cancel`, item-PC actions, decoration,
mail box). They were simply missing from `config/gold/literal_handlers.json`.
Likewise the START menu's two-line highlighted-entry description
(`Pokémon database`, `Party Pokémon status`, `Contains items`, and five
more) runs through `ui.start_menu.items`, whose `item.label` field this mod
already localized -- `item.desc` just wasn't wired up yet. All now fixed;
`Party <PK><MN>\nstatus`'s French translation is slightly longer than the
original two-glyph line and should get an in-game width check.

### Required upstream capabilities

Still genuinely out of reach: these have no public hook at all, only a
hardcoded local table or a `self:say(...)`/`:drawBottomLines(...)` call, so
they must not be implemented by reaching into private UI classes.

- **PC and storage dialogue:** `CenterPcMenu:buildEntries()` -- the
  "which PC" list (`BILL's PC`, `PROF.OAK's PC`, the player name's own
  `<name>'s PC`, `HALL OF FAME`, and this menu's own `TURN OFF` row) -- is
  built and stored to `self.entries` directly with no `Runtime.call` at all,
  unlike `PcMenu`/`ItemPcMenu`'s row lists (so `ItemPcMenu`'s own
  `TURN OFF`/`LOG OFF` rows, reached through `ui.pc.items`, *are* already
  translated; only `CenterPcMenu`'s copy of `TURN OFF` is not). The same
  file's free-form prompts (`What?`, `Access whose PC?`,
  `<name>'s PC accessed.`, `Want to get your Pokédex rated?`, the
  link-closed message, and its `YES`/`NO` confirmation box) are drawn with
  direct `self:say(...)`/`Chrome.print(...)` calls, also with no hook.
  `BoxMenu`'s `Choose a Pokémon`/`Cancel`/`Party Pokémon`/`Which box?` rows
  are the same: drawn directly, no hook. Box names (`BOX1`, `BOX2`, …) are a
  different case again -- not a menu string at all, but save data written
  once by `SetDefaultBoxNames` when a new save is created
  (`core/gen2/Boxes.lua`'s `save.boxNames`), so they would need a save-init
  hook, not a menu-list one.
- **Battle messages and action menu:** provide a public string/event registry
  for `Wild Pokémon appeared!`, `Go Pokémon!`, the `Fight`/`Pack`/`Run`
  action menu (a hardcoded local table in `BattleState.lua`, no hook),
  `Pokémon's defense rose`, `Pokémon learned …`, `Got away safely`, `Pokémon's
  attack missed`, `… wants to battle`, `… sent out …`, `A critical hit`, and
  `You have no more Pokémon`.
- **Gen2 Pokédex screen:** expose the Gen2 Pokédex text and its `START` /
  `SELECT` / `OPTION` / `SEARCH` labels through a public registry. The mod can
  generate species and Pokédex catalogs, but the current screen reads a
  separate internal `data.gen2Pokedex` table, which is why the in-game entry
  can be blank.
- **Pokegear and clock UI:** expose `Press any button to exit`, weekdays, and
  `O'clock` through the normal text catalog.
- **Received-item/system rewards:** confirmed with a real in-game boot (fr):
  both cases still show the empty name, and both are genuine engine-side
  name-resolution gaps, not a missing translation -- the surrounding "reçoit"
  text is correctly translated, only the substituted name is empty.
  - The Pokégear ("reçoit .", `gs.std_text.ReceivedItemText`, period
    terminator): this message is built by the `verbosegiveitem` opcode
    (`script/gen2/Vm.lua`), which sources the name from `self.getItemNameFn`.
    POKéGEAR does not appear anywhere in the corpus's 256-entry item table
    (`gs.names.ItemNames.*`) -- it isn't a normal bag item -- and
    `getItemName()` (`world/gen2/World.lua`) is written to fall back to an
    `"ITEM<n>"` placeholder for an unknown item, not an empty string, so
    either a second `getItemName` registration exists elsewhere with a
    different (buggier) fallback, or this specific grant resolves to a
    genuinely empty name upstream.
  - Cyndaquil/Héricendre ("reçoit !", `gs.ElmsLab.ReceivedStarterText`, a
    different row using `wStringBuffer3`, not `wStringBuffer4`): the
    `givepoke` opcode (`script/gen2/Vm.lua`) adds the Pokémon to the party
    but does not itself write any name buffer; whatever separate ROM script
    instruction is supposed to populate `wStringBuffer3` beforehand
    (elsewhere in the engine this is a `nameMon`-style special, see
    `script/gen2/Specials.lua`) is either missing or broken for this
    specific script.
  Neither is fixable from this translation mod without reaching into private
  engine internals; both are candidates for an upstream gen1recomp bug
  report.
- **Item descriptions and summary/stat labels:** expose the bag item
  descriptions and the remaining Pokémon summary labels (`Level up`, `EXP
  Points`, `Type`, `Item`, `Move`, `OT`, `Attack`, `Defense`, and related
  screens) through public data or hooks.

The entries in `config/gold/literal_handlers.json` record known stable corpus
matches for these screens. They can be activated when the corresponding public
upstream hooks exist; they are deliberately not a private-class monkey patch.
This keeps the release manifest permission-free and makes the remaining work
visible to the engine project.
