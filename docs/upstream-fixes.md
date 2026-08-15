# Gold translation: upstream engine gaps

The Gold mod now uses only the public `gen1recomp` content and hook APIs. The
following strings are tracked here because translating them correctly requires
new upstream APIs; they must not be implemented by reaching into private UI
classes from the translation mod.

## Fixed: rows already reachable through an existing public hook

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

## Required upstream capabilities

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
