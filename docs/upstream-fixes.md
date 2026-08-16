# Translation: upstream engine gaps

Both mods use only the public `gen1recomp` content and hook APIs. Two kinds
of gap are tracked here, and each game's section separates them:

- **Required upstream capabilities**: strings that render in English with no
  override reaching them at all -- no hook, no catalog entry can fix these
  from the translation mod; they must not be implemented by reaching into
  private UI classes.
- **Translated via a compromise (`engine-contract-gap`)**: strings that
  already render translated today, but through a compromise (an
  AI-generated stand-in, a reordered-argument adaptation, or one override
  serving several incompatible original contexts) instead of the real
  official localized phrasing, because the engine's callsite doesn't map
  cleanly onto it. These are not broken, but the goal is to retire this
  category over time as gen1recomp's contract at each callsite is fixed.

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

### Translated via a compromise, not blocked (`engine-contract-gap`)

Everything in "Required upstream capabilities" below renders in **English**
with no override reaching it at all. This section is different: every entry
here already shows **translated** text in-game today, through
`overrides/<language>/rby/engine.json`. They are tracked here anyway because
the override is a compromise, not the real localized phrasing -- the
pipeline tags each one `"reason": "engine-contract-gap"` and records why in
its `provenance` field. The goal is to retire this whole section over time:
each row names the concrete engine change that would let the corpus's real,
official localized text replace the compromise. Until then the compromise
stays live and correct-enough, not broken.

Two distinct shapes of compromise, both summarized below (fr shown; every
shipped language carries its own compromise the same way, see each
`overrides/<language>/rby/engine.json`):

**Engine-authored settings/launcher labels** (31 entries,
`config/shared/engine_manifest.json`'s `engine_dynamic_values`, each with a
real callsite, `eligibility: "ineligible"`): these are modern
launcher/options UI, not ROM text, so there is no PokeCorpus qid to match
against at all -- the override is either an AI-generated translation
(flagged `requires in-game visual validation` in its provenance) or the
acronym/label kept as-is where translating it would lose meaning (`SGB`,
`GBC`, `SGB INV`).

| Source | fr override | Callsite |
|---|---|---|
| `OG RED` | `OG ROUGE` | `ui/OptionsMenu.lua:276`, `import/LauncherSettings.lua:118` (`PaletteFX.modeLabel`) |
| `OG BLUE` | `OG BLEU` | same as above |
| `OG YELLOW` | `OG JAUNE` | same as above |
| `SGB` | `SGB` (kept) | same as above |
| `ADVANCED` | `AVANCE` | same as above |
| `OG INV` | `ORIG. INV` | same as above |
| `SGB INV` | `SGB INV` (kept) | same as above |
| `CLASSIC` | `CLASSIQUE` | same as above |
| `GBC` | `GBC` (kept) | same as above |
| `WINDOWED` | `FENETRE` | `ui/OptionsMenu.lua:346`, `import/LauncherSettings.lua:169` (`VideoMode.modeLabel`) |
| `BORDERLESS` | `SANS BORD` | same as above |
| `TREES` | `ARBRES` | `ui/OptionsMenu.lua:330`, `import/LauncherSettings.lua:154` (`TileRenderer.voidFillLabel`) |
| `WATER` | `EAU` | same as above |
| `OFF` | (per-context) | `ui/OptionsMenu.lua:251`, `import/LauncherSettings.lua:99` (`FILTERS`/`FaithfulRes`) |
| `1X` / `2X` / `3X` | (per-context) | `ui/OptionsMenu.lua:251`, `import/LauncherSettings.lua:60` (`FILTERS`/`FaithfulRes`) |
| `NORMAL` | (per-context) | `ui/OptionsMenu.lua:398`, `import/LauncherSettings.lua:218` (`GameSpeed.levelLabel`) |
| `AUTO` / `LANDSCAPE` / `PORTRAIT` / `REVERSE LANDSCAPE` | (per-context) | `ui/OptionsMenu.lua:357` (`Orientation.modeLabel`) |
| `FAST` / `MEDIUM` / `SLOW` | (per-context) | `ui/OptionsMenu.lua:126` (`SPEEDS` table) |
| `HEAVY` / `LIGHT` | (per-context) | `ui/OptionsMenu.lua:447`, `import/LauncherSettings.lua:250` (`TouchControls.hapticLabel`) |
| `auto` / `balanced` / `high` / `low` | (per-context) | `ui/OptionsMenu.lua:264` (`Performance.label`) |

**Corpus translations adapted to the engine's argument order or a
fragmentary source** (13 entries): the real localized ROM phrasing exists in
the corpus, but the engine's `Strings()` callsite either reorders the
`printf` arguments differently than the ROM script did, or only passes a
fragment of the original sentence (a technical field like `HT`/`WT`, a
standalone word like `evolving!`), so a literal per-fragment translation
loses grammatical context a single upstream template change could restore.

| Source | fr override | What's missing |
|---|---|---|
| `%s's %s\nrose!` | `%s voit son %s\naugmenter !` | Reordered stat arguments at shared callsites |
| `%s lined up!\nScored %d coins!` | `%s est aligné !\nA gagné %d jetons !` | Slot-machine context around the reordered arguments |
| `%s was\ntransferred to\n%s!` | `%s a été\ntransféré vers\n%s !` | Context-specific transfer arguments, fragmentary source |
| `%s's\nhurt by poison!` | `%s\nest blessé par le poison !` | Fragmentary status-effect source |
| `%s's\nhurt by the burn!` | `%s\nest blessé par la brûlure !` | Fragmentary status-effect source |
| `%sBOX %2d` | `%sBOITE %2d` | Reordered box-name and numeric arguments |
| `BADGES` | `BADGES` (kept) | Fragmentary standalone source |
| `HT %d′%02d″` | `HT %d′%02d″` (kept) | Missing unit context around numeric arguments |
| `WT %.1flb` | `WT %.1flb` (kept) | Missing unit-context argument |
| `Once released,\n%s is\ngone forever. OK?` | `Une fois libéré,\n%s sera\nperdu à jamais. OK ?` | Reordered release-confirmation context around one argument |
| `PLAYER %s\nBADGES %d\nPOKéDEX %3d\nTIME %6d:%02d` | `JOUEUR %s\nBADGES %d\nPOKéDEX %3d\nTEMPS %6d:%02d` | Technical status fragment, reordered display arguments |
| `This POKéMON\ncan't be caught!` | `Ce POKéMON\nne peut pas être attrapé !` | Fragmentary capture-result source |
| `Use on which one?` | `Utiliser sur lequel ?` | Missing target-selection argument |
| `evolving!` | `évolue !` | Fragmentary evolution source |

**Corpus translations shared across multiple, incompatible original
contexts** (5 entries): the engine collapses several different ROM strings
into one `Strings()` source key, so one override has to serve every context
even though the original ROM script used different phrasing for each --
only a callsite split upstream (one key per context) would let each one
carry its real, distinct localized text.

| Source | fr override | Original contexts collapsed together |
|---|---|---|
| `%s\nfainted!` | `%s\nest K.O. !` | `rb.text_2.EnemyMonFaintedText`, `rb.text_2.PlayerMonFaintedText`, `rb.text_4.PokemonFaintedText` |
| `%s found\n%s!` | `%s trouve\n%s !` | `rb.text_1.FoundItemText`, `rb.text_2.FoundHiddenItemText` |
| `%s's HP\nwas restored!` | `Les PV de %s\nont été restaurés !` | `ItemEffects.lua` and `PartyMenu.lua` item-healing callsites, different target presentation |
| `It won't have\nany effect.` | `Cela n'aura\naucun effet.` | `rb.text_6.VitaminNoEffectText`, `rb.text_6.ItemUseNoEffectText`, `ItemEffects.lua`, `PartyMenu.lua` item failures |
| `POKéDEX` | `POKéDEX` (kept) | `StartMenu.lua`, `PokedexMenu.lua`, `TitleState.lua`, `HallOfFame.lua` labels, no single upstream qid |

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
  version of this doc claimed (see the correction note below) -- reading
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

  **Corrected from a prior version of this doc:** this bullet used to claim
  the shared root cause was simply "reads `game.data.text` directly, not
  through `Strings()`," covering 6 handlers and a "no public API to give a
  Pokémon" blocker on Mt. Moon's Magikarp salesman. That does not hold:
  `src/core/Strings.lua`'s own header explains extracted dialogue
  (`Data.text`) has its own override path, `mod.content.text:override(id,
  value)`, independent of `Strings()`/`mod.content.strings` -- so a script
  reading `t[label]` is translatable regardless of whether it goes through
  `Strings()` first. Confirmed directly against a real build's
  `lang/dialogue.lua`: the Bike Shop's three NPCs and Route 24's
  Nugget-Bridge recruiter were already reading `game.data.text` correctly,
  with translations already present, via gen1recomp's own vanilla scripts
  (`data/scripts/story2.lua`, `story4.lua`,
  `data/scripts/flavor/bike_shop.lua`) -- which are also *more* complete
  than this project's former reimplementations (real bag-capacity checks
  via a `require` unrestricted for core engine code). Those 4 redundant
  handlers were removed from `config/rby/literal_handlers.json`; letting
  vanilla run is simpler and more correct. The same real-build check was
  repeated for every other flavor NPC reading `game.data.text` this way --
  Viridian City's `GAMBLER1`/`GIRL`, Pewter City's `SUPER_NERD1`/
  `SUPER_NERD2`, the 8-NPC `gift()` family in `data/scripts/story5.lua`,
  and Mt. Moon's Magikarp salesman (which also already calls
  `give_pokemon` natively, so "no public API to give a Pokémon" was never
  actually a blocker here) -- all confirmed already correctly translated,
  no config needed. The Pokédex "kind" classification
  (`src/ui/DexEntryMenu.lua:93`) looked like the same deep gap as the
  status-ailment abbreviations above, but isn't: `pipeline/mod.py` already
  has a dedicated `species_kinds` catalog for it.

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

### Translated via a compromise, not blocked (`engine-contract-gap`)

Same pattern as RBY above -- these 20 entries (`overrides/<language>/gold/engine.json`,
common across fr/de/es/it/ja-Hrkt/ko) already show translated text in-game,
adapted to the engine's split or reordered contract rather than the exact
official phrasing:

| Source | fr override | What's missing |
|---|---|---|
| `%d #MON seen\n%d #MON owned\n\nPROF.OAK's\nRating:` | `%d POKéMON vus\n%d POKéMON pris\fÉvaluation du\nPROF. CHEN :` | Adapted to the engine's two-`printf` contract |
| `%s got %s%d for winning!` | `%s remporte %s%d !` | Currency/printf order follows the engine contract |
| `%s got %s%d for winning! Sent some to MOM!` | `%s remporte %s%d ! Une partie est\nenvoyée à MAMAN !` | Currency/printf order follows the engine contract |
| `A▶PRINT` / `B▶CANCEL` / `L▶BEFORE` / `R▶NEXT` | `A▶IMPRIMER` / `B▶ANNULER` / `L▶RETOUR` / `R▶SUITE` | Physical button glyph retained |
| `Fly to %s?` | `Voler vers %s ?` | Adapted to the engine's printf contract |
| `LEFT SIDE` / `RIGHT SIDE` | `À GAUCHE` / `À DROITE` | Decoration-menu labels, engine contract |
| `START>CANCEL` | `START>ANNULER` | Physical button retained |
| `Registered the` + `that item.` | `Objet enregistré :` + `d'enregistrer.` | One ROM sentence (`CantRegisterItemText`/`RegisteredItemText`) split across two engine fragments |
| `You can't register` | `Impossible` | Split-contract half of `CantRegisterItemText` |
| `You have no more\nPOKéMON that can\x0bfight!` | `Plus de POKéMON\napte au combat !` | Adapted from `gs.common_2.NoUsableMonText` |
| `{PLAYER} used the` | `{PLAYER} utilise :` | Item-on-next-fragment order |
| `OT/` | `DO/` | Compact engine label, slash retained |

Two more are per-language only, each documented individually in the relevant
`overrides/<language>/gold/engine.json`: German and Korean also override
`"%s\nis about to use...\nWill %s\nchange POKéMON?"` (the German ROM omits
the player-name printf the engine expects), and Japanese and Korean also
override `"Congratulations!"` (the Japanese diploma folds that idea into the
preceding line instead of a standalone sentence).

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

## Engine bug surfaced by TTF mode (not a translation gap)

Reported by a user (fr, Fusion Pixel profile): the in-game **Mod Manager**
screen (`src/mods/ManagerState.lua`, reachable while playing, both RBY and
Gold) shows **no text at all, everything white**. This is not caused by
anything in this mod's content or config -- it is a real gen1recomp bug in
`ManagerState.lua` itself, and it would hit *any* mod that activates TTF text
mode (`mod.content.font:register("ttf", ...)`), translated or not. Not
fixable from a mod: there is no hook into `ManagerState`'s own draw code,
only a candidate upstream bug report.

**Root cause, confirmed by reading the source directly.**
`ManagerState:draw()` (`src/mods/ManagerState.lua:1282-1286`):

```lua
love.graphics.setColor(0, 0, 0, 1)               -- black
love.graphics.rectangle("fill", 0, 0, 160, 144)  -- full-screen background
love.graphics.setColor(1, 1, 1, 1)               -- white
Font.drawBox(0, 0, 20, 18)                       -- restores the CALLER's color: white
Font.draw(self.banner or Strings("MOD MANAGER"), 16, 8)  -- drawn while color is still white
```

Nothing resets the color back to black before this or any subsequent
`Font.draw`/`drawCode` call in `draw()`, `drawList()`, `drawRows()`,
`drawDetail()`, `drawPermissions()`, `drawErrors()`, `drawApply()`, or
`drawOverlay()` (same missing reset after its own `Font.drawBox` at line
1257) -- essentially the whole screen except the unrelated `"options"`
sub-screen, which does reset color correctly (line 1276-1278).

This is invisible in the vanilla, tile-font build: `Font.drawBox`'s own
comment (`src/render/Font.lua`, right above its definition) explains why --
"the tile pages are black glyphs on transparent, so they come out black
whatever the color is," so a leaked white color was harmless as long as
every glyph was a tile. `Font.drawCode`'s TTF branch instead calls
`love.graphics.print(...)`, which *does* draw in the current color, so once
a mod's TTF font is active, any label after a color leak comes out white.
This exact failure mode already happened once and was fixed -- but only
locally, inside `Font.drawBox` itself, which now restores the color the
*caller* had before it filled the interior. That fix cannot help here: the
caller (`ManagerState.lua`) is the one setting white and never restoring it,
so `Font.drawBox` faithfully preserves the caller's own mistake instead of
masking it. Every other screen that calls `Font.drawBox` (checked directly:
`TitleState.lua`, `StartMenu.lua`, `HallOfFame.lua`, `BoxMenu.lua`,
`PartyMenu.lua`) does `love.graphics.setColor(0, 0, 0, 1)` right after
`Font.drawBox` and before printing text -- `ManagerState.lua` is the one
screen missing that reset.
