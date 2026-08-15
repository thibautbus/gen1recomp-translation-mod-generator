# Gold translation: upstream engine gaps

The Gold mod now uses only the public `gen1recomp` content and hook APIs. The
following strings are tracked here because translating them correctly requires
new upstream APIs; they must not be implemented by reaching into private UI
classes from the translation mod.

## Required upstream capabilities

- **PC and storage menus:** expose the labels and prompts built directly by
  `CenterPcMenu`, `PcMenu`, `ItemPcMenu`, and `BoxMenu` (including the player
  name's PC, `What?`, `Withdraw Pokémon`, `Deposit Pokémon`, `Change box`,
  `Move Pokémon w/o mail`, `See ya!`, `Choose a Pokémon`, `Box 1`, `Cancel`,
  `Party Pokémon`, and `Which box?`). The public item-menu hook only covers
  actions already passed through the hook; it cannot see these internal rows.
- **Battle messages and action menu:** provide a public string/event registry
  for `Wild Pokémon appeared!`, `Go Pokémon!`, `Fight`, `Pack`, `Run`,
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
- **Received-item/system rewards:** expose the item/reward name passed to the
  received-item message. Without that value, messages such as `… received
  Héricendre!` lose the name and render as `… received .`.
- **Item descriptions and summary/stat labels:** expose the bag item
  descriptions and the remaining Pokémon summary labels (`Level up`, `EXP
  Points`, `Type`, `Item`, `Move`, `OT`, `Attack`, `Defense`, and related
  screens) through public data or hooks.

The entries in `config/gold/literal_handlers.json` record known stable corpus
matches for these screens. They can be activated when the corresponding public
upstream hooks exist; they are deliberately not a private-class monkey patch.
This keeps the release manifest permission-free and makes the remaining work
visible to the engine project.
