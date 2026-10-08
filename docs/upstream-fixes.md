# Translation: upstream engine gaps

This file tracks, game by game, what the translation mods cannot translate, or translate only through a compromise, because of how gen1recomp draws the text. The mods use only the public `gen1recomp` content and hook APIs, so a gap here is closed by an engine change, never by reaching into private UI classes.

The pin is gen1recomp v0.3.63 (`7ab2f865`, `gen1recomp_revision` in `config/shared/engine_manifest.json` and `config/pipeline.toml`). Each game section says which revision its file:line citations were reviewed at; a citation marked "(v0.3.63)" was checked against the pin. Line numbers drift between releases, so a citation from an older revision names the right function, not always the right line.

Each game section follows the same order:

- **Summary at the pinned revision**: what a mod can reach, and through which mechanism.
- **Required upstream capabilities**: text that stays English because no override reaches it. Open entries only.
- **Translated via a compromise (`engine-contract-gap`)**: text that shows translated, but not in the cart's own wording, because the engine's call site does not map onto the cart's row. The pipeline tags each such override `"reason": "engine-contract-gap"` in `overrides/<language>/<family>/*.json` and says why in its `provenance`; those two fields are authoritative, so the sections below describe the kinds of compromise rather than list every row.
- **Verified working, not a gap**: things that looked like gaps and were checked against a real build, kept so they are not investigated again.
- **Fixed upstream**: one line per fixed entry (what changed, the gen1recomp PR and release), linking to [upstream-fixes-history.md](upstream-fixes-history.md), which keeps the investigations, the pin-bump audits and the fix narratives.

Numbered entries (FireRed, Ruby and Sapphire, Emerald) keep their number once fixed, because code, tests and override provenances cite them ("FireRed entry 9", "Emerald entry 2"). The game3 files the hardcoded-text scan flags are listed in [game3-hardcoded-text-inventory.md](game3-hardcoded-text-inventory.md).

**Numbered directives.** Since gen1recomp#2346 (v0.2.67), `Strings()` accepts POSIX numbered directives (`%2$s de %1$s`) in every game, so a translation can put two values in another order than the English. Many former compromises existed only because the engine passes its values in the English order (the stat-change messages above all: the French carts write "DEFENSE de HERICENDRE diminue!", stat first). Those keys now ship the cart's own row, renumbered, tagged `engine-corpus-reordered` (French Gold `"%s's %s fell!"` is `"%2$s de\n%1$s\vdiminue!"`); where the cart already names the values in the engine's order, the row ships as `engine-corpus-cart-order`.

Two reports that are not translation gaps close the file: gen1recomp rendering bugs surfaced by TTF mode (three, all fixed) and `tools/modkit.py` Windows encoding bugs (one fixed, one open with a project-side workaround).

## Contents

- [Open gaps at the pinned revision (v0.3.63)](#open-gaps-at-the-pinned-revision-v0363)
- [RBY](#rby): Red, Blue and Yellow
- [Gold and Silver](#gold-and-silver): Gold, Silver and Crystal
- [FireRed](#firered): FireRed and LeafGreen
- [Ruby and Sapphire](#ruby-and-sapphire)
- [Emerald](#emerald)
- [The cross-game Union Room](#the-cross-game-union-room)
- [Engine bugs surfaced by TTF mode](#engine-bugs-surfaced-by-ttf-mode-not-translation-gaps)
- [Build tooling bugs in `tools/modkit.py` on Windows](#build-tooling-bugs-in-toolsmodkitpy-on-windows-not-translation-gaps)
- [History of the fixed entries](upstream-fixes-history.md) and the [game3 hardcoded-text inventory](game3-hardcoded-text-inventory.md)

## Open gaps at the pinned revision (v0.3.63)

The code path behind every entry below was checked against the pinned tree; figures measured at an older revision are dated in the entry itself. Most entries leave text in English because no override reaches it; FireRed entries 14 and 15 are translated today, through a catalog `modkit pack` would refuse if it were keyed the usual way, and Emerald entry 9 is translated but overflows.

| Game | Entry | What is affected | Upstream change needed |
| --- | --- | --- | --- |
| Red, Blue, Yellow | — | Nothing known | — |
| Gold, Silver, Crystal | [Buena's password words](#buenas-password-words-crystal) | Crystal: the radio password's town, type and station words | Pass a `string` word through `Strings()` (or a registry) where it is drawn |
| Gold, Silver, Crystal | [Item descriptions](#item-and-tm-descriptions-in-the-pack) | The PACK's item and TM descriptions | A `description` field in the Gen 2 `items` registry, or `Strings()` on the description |
| Gold, Silver, Crystal | [Default box names](#default-box-names) | `BOX1` … `BOX14` for a box the player never renamed | `Strings()` in `Boxes.defaultName` |
| FireRed, LeafGreen | [4](#4-pokédex-categories-and-descriptions-have-no-reachable-registry) | Pokédex categories and descriptions | Route the merged `dexEntry` (kind, text) into `PokedexData` |
| FireRed, LeafGreen | [5](#5-help-system-and-quest-log) | The help system, the quest log and the place names it records | A text registry keyed by pret label; places recorded as section ids |
| FireRed, LeafGreen | [8](#8-the-naming-keyboard-is-the-us-layout) | The naming keyboard's letters (no accented letters, no `ß`) | A keyboard layout the language can supply |
| FireRed, LeafGreen | [9](#9-minor-extraction-and-runtime-inconsistencies) | Small extraction inconsistencies; type badges baked into graphics | Per item (see the entry) |
| FireRed, LeafGreen | [10](#10-cart-text-the-extractor-never-reaches) | Fame Checker, Safari Zone, cable club, Pokédude and Pokédex rating lines | Extract those texts and look them up by key |
| FireRed, LeafGreen | [13](#13-the-mod-managers-own-screens-are-in-english) | The mod manager's tab headers, titles and mod states | `Strings()` on those literals |
| FireRed, LeafGreen | [14](#14-the-map-name-popup-spells-its-floor-label-itself) | Translated through a by-English key (entry 15) | Read `gText_%dF`/`gText_B%dF` through `RomText` |
| FireRed, LeafGreen | [15](#15-a-cart-string-keyed-by-its-english-is-refused-even-where-nothing-else-reaches-it) | Translated through `lang/strings_by_english.lua`: section names, region map guide text, ability names | A label path for those lookups, or an MK306 exemption |
| FireRed, LeafGreen | [16](#16-a-cart-fragment-the-engine-concatenates-cannot-be-reordered) | Cart fragments printed between two buffers | The cart's whole row, with numbered directives |
| FireRed, LeafGreen | [17](#17-engine-rows-with-no-firered-cart-row) | 12 engine rows with no FireRed cart row | None on the engine side: rows no FireRed cart has, kept English |
| Ruby, Sapphire | [6](#6-engine-rows-with-no-cart-row) | 6 engine rows with no cart row | As Emerald entry 2 |
| Ruby, Sapphire | [7](#7-official-rows-that-print-a-buffer-the-runtime-does-not-fill) | Rows naming a buffer the runtime leaves empty | Fill the buffers, as pret does |
| Ruby, Sapphire | [9](#9-native-screens-whose-packs-keep-their-strings-under-local-names) | Secret base, roulette, trainer card labels, diploma, berry tag, PokéNav, Battle Tower records | Extractors that record the labels; screens that read the script cache |
| Emerald | [2](#2-engine-rows-with-no-emerald-cart-row) | 7 engine rows with no cart row | None on the engine side: port-added or empty rows |
| Emerald | [5](#5-cart-text-the-emerald-script-cache-does-not-carry) | Berries, decorations, Frontier lounges, Battle Tower partners, Pyramid rest and retire prompts | Put those strings in the script cache's text table |
| Emerald | [7](#7-buffers-the-runtime-does-not-fill) | Rows naming a buffer the runtime leaves empty | Fill the buffers, as pret does |
| Emerald | [8](#8-descriptions-are-keyed-by-their-english) | The second move or ability sharing one English description | Look descriptions up by id |
| Emerald | [9](#9-italian-lines-that-spell-out-a-glyph-run) | Seven Italian lines overflow the message box (translated) | Draw the POKéMELLA glyph run as the cart does |
| Emerald | [10](#10-the-berry-blender-reads-its-own-pack) | The Berry Blender's messages and opponents' names | Thread the script cache lookup through the screen and its logic |
| Union Room | [Validation messages](#required-upstream-capability-the-validation-messages) | Why a Pokémon cannot cross to the other game | Route `Messages.text` through `Strings()` with named slots |
| Tooling | [`modkit pack`](#still-open-modkit-pack-fails-on-a-non-ascii-windows-path) | Not a translation gap: `modkit pack` fails under a non-ASCII Windows path | Hand LuaJIT an ASCII-safe path |

## RBY

Red, Blue and Yellow, one universal `translation-<lang>` mod built from Red or Blue plus Yellow. The citations in this section were checked at v0.3.63 (`7ab2f865`) unless they name another revision.

Summary of what a translation mod can reach at the pinned revision:

| Surface | Reachable | Mechanism |
| --- | --- | --- |
| Cart dialogue, Red/Blue and Yellow | Yes | `mod.content.text:override(label, value)`, by pokered label, with a Yellow layer |
| Engine strings (menus, battle messages, options, launcher) | Yes | `strings` registry, `overrides/<language>/rby/engine.json` |
| Status abbreviations | Yes | `statuses` registry |
| Pokédex kinds | Yes | `species_kinds` catalog |
| Pokédex heights and weights in metres and kilograms | Yes since v0.3.61 | `pokemon` patches (`dexEntry.heightM`/`weightKg`) and the cart's own labels |

### Required upstream capabilities

None at v0.3.63. The last entry this list held, Silph Co. 9F's nurse, reads her three labels from the dialogue catalog (`data/scripts/flavor/silph_co_9f.lua:14-21`, v0.3.63).

### Translated via a compromise (`engine-contract-gap`)

Between 41 and 47 entries per language in `overrides/<language>/rby/engine.json` (43 in French). They fall into these kinds:

- **Engine-authored launcher and options labels** (palette modes such as `OG RED` and `CLASSIC`, video modes, the void fill's `TREES`/`WATER`): no cart has them, so they are composed per language and flagged for in-game validation, or kept as they are where translating would lose the meaning (`SGB`, `GBC`, `SGB INV`).
- **Cart rows re-flowed or cut to the engine's layout**: the diploma's lines, the trainer card's `MONEY/¥%d` and `TIME/  %d:%02d`, the town map's `%s's NEST` and `AREA UNKNOWN`, `%s's PC`, `Choose a\n<PK><MN> BOX.`, and the metric Pokédex labels (`GR. %.1fm`, `GEW. %.1fkg` and their unknown-entry variants), each taken from the cart's own row.
- **One key for several cart lines**: the engine prints different cart messages through one key (`%s\nfainted!` for the player's, the foe's and the field's faint lines, `POKéDEX` for four screens, `It won't have\nany effect.`, `%s found\n%s!`, `%s\nlearned\n%s!`, `WITHDRAW`), so one reviewed row serves them all.
- **Labels kept as the English source**: the imperial branch's `HT`, `WT` and `lb`, which only print for a species without metric values, and `BADGES`.
- **Port-added or reworded lines**: `EXIT GAME` (a cart is switched off, not exited), `Once released,\n%s is\ngone forever. OK?`, `Converted type to\n%s's!`.

Spanish and Italian ship the stat-change messages (`"%s's\n%s fell!"` and its three siblings) in the cart's own order through numbered directives (`engine-corpus-reordered`, Spanish `"¡%2$s de\n%1$sbajó!"`); French, German and Japanese already name the POKéMON first and need none.

### Verified working, not a gap

- **The summary's stat labels** (`NAME`, `ATTACK`, `DEFENSE`, `SPEED`, `SPECIAL`) are drawn through `Strings(s[1])` from a table (`src/ui/SummaryMenu.lua:201`, v0.3.63), which the literal scanner cannot see: `forced_dynamic_keys` in `config/shared/engine_manifest.json` adds the keys and `config/rby/semantic_anchors.json` translates them.
- **Yellow's Melanie's House dialogue** is in the Yellow manifest through `tools/make_yellow_manifest.py`'s `YELLOW_EXTRA_TEXT_LABELS`, and translated.
- **Poison and burn's `Strings.source()` fallbacks** only render when their ROM labels are missing, which no real build has, so they are classified `covered-by-rom` in `config/rby/engine_scope.json`.
- **Scripts that read `game.data.text` directly are translatable**: extracted dialogue has its own override path (`mod.content.text:override(id, value)`), independent of `Strings()`. `config/rby/literal_handlers.json` has no handlers left.

The investigations behind these are in the [history file](upstream-fixes-history.md#rby-verified-working-investigations).

### Fixed upstream

- Pokédex heights and weights in metres and kilograms: gen1recomp#2747, v0.3.61 ([details](upstream-fixes-history.md#rby-fixed-upstream-engine-changes-not-just-this-projects-config)).
- The X-item and vitamin stat-rise messages pass the stat's name through `Strings()`: PR #1439 ([details](upstream-fixes-history.md#rby-fixed-upstream-engine-changes-not-just-this-projects-config)).
- Museum 1F's ticket clerk reads all five of his lines from the cart: PR #1492 ([details](upstream-fixes-history.md#rby-fixed-upstream-engine-changes-not-just-this-projects-config)).
- Status abbreviations on the summary and party screens go through the `statuses` registry: PR #1527 ([details](upstream-fixes-history.md#rby-fixed-upstream-engine-changes-not-just-this-projects-config)).
- 21 battle, overworld and menu messages read their real ROM label instead of an adapted `Strings()` source: PR #1559, v0.2.12 ([details](upstream-fixes-history.md#rby-more-battleoverworldmenu-messages-routed-through-the-real-rom-text)).
- pokered labels without a leading underscore reach `data/generated/text.lua`: PR #1598, v0.2.12 ([details](upstream-fixes-history.md#rby-pokered-dialogue-labels-were-missing-from-datageneratedtextlua)).
- The Surfing Pikachu minigame's HUD and the Hall of Fame's `HALL OF FAME No` were rewritten upstream (PR #1581, v0.2.19); the full investigation of their translations is in the [history file](upstream-fixes-history.md#rby-surfing-pikachuhall-of-fame-hud-text-rewritten-upstream).
- Silph Co. 9F's nurse reads her lines by label: fixed by v0.3.51 ([details](upstream-fixes-history.md#rby-silph-co-9fs-nurse)).
- The keys retired or renamed by the v0.2.19 to v0.2.26 pin bumps, and the semantic-anchor audit of v0.2.19, are in the [history file](upstream-fixes-history.md#rby-keys-retired-or-renamed-by-the-v0219-to-v0226-pin-bumps); the compromise tables of that time are [there too](upstream-fixes-history.md#rby-the-compromise-tables-before-numbered-directives).

## Gold and Silver

Gold, Silver and Crystal, one `translation-<lang>-gen2` mod built from Gold or Silver plus Crystal. The citations in this section were checked at v0.3.63 (`7ab2f865`) unless they name another revision.

Summary of what a translation mod can reach at the pinned revision:

| Surface | Reachable | Mechanism |
| --- | --- | --- |
| Cart dialogue, Gold and Silver | Yes | `mod.content.text:override(key, value)` by `bank:address`, `lang/dialogue.lua` |
| Crystal's dialogue | Yes, except Korean (no Crystal corpus) | `lang/dialogue_crystal.lua`, applied when `GameVersion.get() == "crystal"` |
| Engine strings (menus, battle messages, options, clock, Pokédex and Pokegear labels, naming screen) | Yes | `strings` registry, `overrides/<language>/gsc/engine.json` |
| Menu rows the engine hands to a public list hook (PC, START menu descriptions, party submenu) | Yes | `ui.*` hooks, `config/gsc/literal_handlers.json` |
| Status abbreviations, #DEX kinds and texts | Yes | `statuses` and `pokemon` registries |
| Pokédex heights and weights in metres and kilograms | Yes since v0.3.61 | `pokemon` patches (`dexEntry.heightM`/`weightKg`) |
| Buena's password words, item descriptions, default box names | **No** | see below |

### Required upstream capabilities

Gen 2 keys its cart text by `bank:address` and, outside two Crystal screens (`src/ui/gen2/GenderSelect.lua`, `src/script/gen2/specials/battle_tower.lua`), reads none of it by label, so a screen that prints cart text without `Strings()` or a registry is out of a mod's reach.

#### Buena's password words (Crystal)

Up to v0.3.47 the radio password's town, type and station words (`NEW BARK TOWN`, `BUG`, `#MON Talk`…) went through `Strings()`, and this project translated them from Crystal's rows. Since v0.3.51 the table comes from the cart (`gen2EventTables.buenaPassword`), and `Buena.word` (`src/core/gen2/Buena.lua:30-38`, v0.3.63) returns a `string` word as the cart's English; species, item and move words come from the translated registries. Fix: pass a `string` word through `Strings()` (or a registry) where it is drawn.

#### Item and TM descriptions in the PACK

The PACK prints an item's description, or a TM's move description, straight from the extracted records (`PackMenu:description`, `src/ui/gen2/PackMenu.lua:1047`, v0.3.63), and the Gen 2 `items` registry has no `description` field (`src/mods/Schemas.lua`: only `gen3Fields` has one). Fix: a `description` field for the Gen 2 items (and the TMs' moves), or `Strings()` on the description where it is drawn.

#### Default box names

A box the player never renamed is named `"BOX" .. index` (`Boxes.defaultName`, `src/core/gen2/Boxes.lua:28-30`, v0.3.63, after pokecrystal's `SetDefaultBoxNames`), with no `Strings()` on the way. Fix: `Strings()` on that name (or a `%d` template).

### Translated via a compromise (`engine-contract-gap`)

Between 152 and 182 entries per language in `overrides/<language>/gsc/engine.json` (152 in French). They fall into these kinds:

- **One cart sentence split across engine fragments**: `Registered the` + `that item.`, `You can't register`, `{PLAYER} used the`, the #DEX rating's two `printf` calls, `%s got %s%d for winning!`.
- **Bare-suffix cart rows**: `%s o'clock` and `%d min.` come from rows with no placeholder to align against.
- **Physical button glyphs** kept in a translated label (`A▶PRINT`, `START>CANCEL`) and **compact engine labels** (`OT/`, `HP`, `№.`).
- **Cart rows adapted to the engine's menus**: decoration, pocket, mail box, Pokédex option and display-mode labels, the battle menu header the cart packs into one row, the trainer card header, Crystal's gender menu, each derived from the named cart row.
- **Rows cut to the engine's box**: the CHANGE BOX save prompt keeps the last three lines of the cart's `ChangeBoxSaveText`.
- **Status names on a mod-compatibility path**: `Battle:applyStatus()`/`tickStatus()`'s fallback messages.

The stat-change and item/move status messages ship the cart's order through numbered directives (6 to 16 `engine-corpus-reordered` entries per language); German, Japanese and Korean, whose carts already name the POKéMON first for some of them, ship those as `engine-corpus-cart-order`.

### Verified working, not a gap

- **Japanese `Options_BattleScene.On` is never used.** Its corpus row reads garbled, but BATTLE SCENE and MENU ACCOUNT both draw the shared `Strings.source("ON ")`/`"OFF"` (`src/ui/gen2/OptionsMenu.lua:138`, `:168`, v0.3.63), which ships a generic word in every language.
- **Silver: supported by declaration, then by real dual-ROM extraction.** Silver's dialogue keys match Gold's except 8 field-move prompts shifted by two bytes, which `config/gsc/silver_pointer_aliases.json` aliases, so Gold's corpus join serves Silver too ([history](upstream-fixes-history.md#silver-supported-by-declaration-then-by-real-dual-rom-extraction)).
- **Crystal: mandatory companion ROM, its own corpus join, shared engine strings.** Crystal's dialogue is joined against its own corpus collection (`pipeline/gsc/crystal_mod.py`) with reviewed pointer decisions, and its engine strings are Gold's ([history](upstream-fixes-history.md#crystal-mandatory-companion-rom-its-own-corpus-join-shared-engine-strings)).

### Fixed upstream

- Pokédex heights and weights in metres and kilograms: gen1recomp#2747, v0.3.61 ([details](upstream-fixes-history.md#gold-fixed-upstream-engine-changes-not-just-this-projects-config)).
- The clock's weekdays, `o'clock`, minutes and `MORN`/`DAY`/`NITE`: PR #1450; its `AM`/`PM` marker: PR #1759, v0.2.21 ([details](upstream-fixes-history.md#gold-fixed-upstream-engine-changes-not-just-this-projects-config)).
- PcMenu's CHANGE BOX save flow goes through `Strings()`: PR #1774, v0.2.24 ([details](upstream-fixes-history.md#gold-pcmenus-own-change-box-save-flow)).
- The SAVE screen's overwrite prompt is the cart's whole three-line text since v0.2.55 ([details](upstream-fixes-history.md#gold-the-save-overwrite-prompts-missing-third-page)).
- The Options menu (PR #1735) and the naming screen (PR #1738), both in v0.2.24; the PC and storage dialogue and the battle action menu by v0.2.51; the status abbreviations, the #DEX screen and the Pokegear's exit line by v0.3.51; `src/battle/gen2/Battle.lua`'s messages, which at v0.3.63 all go through `Strings()` ([details](upstream-fixes-history.md#gold-formerly-required-upstream-capabilities)).
- On this project's side: the PC rows, the party submenu and the START menu descriptions reached through public hooks ([details](upstream-fixes-history.md#gold-rows-already-reachable-through-an-existing-public-hook)); received-item names (PR #37, see the [same section](upstream-fixes-history.md#gold-formerly-required-upstream-capabilities)); the word order of the stat-change messages before numbered directives ([details](upstream-fixes-history.md#gold-stat-change-and-itemmove-status-messages)); the compromise table of that time is [kept too](upstream-fixes-history.md#gold-the-compromise-table-before-numbered-directives).

## FireRed

FireRed and LeafGreen (US, v1.0) run on gen1recomp's generation-3 runtime (`src/core/game3`, `src/ui/game3`), with its own mod surface (`src/mods/Gen3Compat.lua`, `Schemas.GEN3` in `src/mods/Schemas.lua`). LeafGreen is the `GameVersion` id `leafgreen`: `Versions.select` moves every FireRed address onto LeafGreen's (`src/import/gba/editions/leafgreen_1_0.lua`), so everything below applies to both. The translation mod (`translation-<lang>-gen3`, fr/de/es/it/ja-Hrkt) uses only the public generation-3 registries and one public hook, and `tools/frlg/gate.lua` loads it through the real generation-3 loader on top of the game3 data modules built from a private extract of the cart, so every "lands"/"does not land" statement here is measured, not inferred.

This section was reviewed at v0.3.14 (`591bf4d6`), and its file:line citations refer to that tree unless an entry names another revision; citations marked "(v0.3.63)" were checked against the pin, v0.3.63 (`7ab2f865`).

Summary of what a translation mod can and cannot reach at the pinned revision:

| Surface | Reachable | Mechanism |
| --- | --- | --- |
| Cart text (script messages, menus, battle messages, lists, the region map, the intro) | Yes, 99.9% of it | `mod.content.text:override(key, ir)`, by ROM address or label |
| Species, move, item names; item descriptions | Yes (since v0.2.70, entry 2) | `pokemon`/`moves`/`items` patches |
| Trainer names and class names | Yes (since v0.3.4, entry 6) | `trainers` patches |
| Start menu labels | Yes | `ui.start_menu.items` hook |
| game3's own text: menus and prompts, ability names, move and ability descriptions, map section names, the region map's guide text, Easy Chat (1,946 `Strings()` keys) | Yes, except 12 rows with no cart row (entry 17) and the mod manager's screens (entry 13); section names, guide text, ability names and floor labels through a by-English catalog (entries 14 and 15) | `strings` registry, keys listed in `config/frlg/engine_scope.json` |
| Accented letters, Japanese, braille | Yes (since v0.2.67 and v0.3.4, entries 1, 3 and 7) | the cart's own fonts |
| The Japanese honorific after the player's name | Yes (since v0.3.52, entry 18) | `text` overrides the placeholder reads |
| Pokédex heights and weights in metres and kilograms | Yes since v0.3.61 | `pokemon` patches (`dexEntry.heightM`/`weightKg`) |
| Pokédex categories and descriptions, help system, quest log, naming keyboard | **No** (entries 4, 5 and 8) | extracted packs or cart layouts with no registry |
| Cart fragments printed between two buffers | **No** (entry 16) | |
| Cart text the extractor never reaches | **No** (entry 10) | |

### Required upstream capabilities

Ordered by entry number.

#### 4. Pokédex categories and descriptions have no reachable registry

The Pokédex screen builds its entries from the `pokedex/entries.lua` pack through `PokedexData.getEntry` (`src/core/game3/pokedex_data.lua:186-230`, v0.3.63) and draws `entry.description` (`src/ui/game3/pokedex.lua:946`, v0.3.63). The `pokemon` registry's `dexEntry` writes the species pack's `_dex` table instead, which only the damage formula reads in game (for weight, `src/core/game3/battle/damage.lua:169`; `Gen3Compat.lua` also hands it to Gen 1-style compat mods), and `gen3Fields.dexEntry` has no text field at all. A `dexEntry = { kind = ... }` patch therefore loads fine and is never displayed; the pipeline stopped shipping it for that reason. (The page's own labels, the unknown-species entry and the "%s POKéMON" category line go through `Strings()` or the cart's rows.)

Fix: route the merged `dexEntry` (kind, and new `text`/`text2` fields) into `PokedexData._entries`, the way Gen 2's `PokedexText.apply` bridges the Gold registry into the #DEX screen. Corpus side everything is ready: `frlg.common.pokedex_entries.gPokedexEntries.N` (387 categories, by National Dex number) and `frlg.common.pokedex_text_fr.*` (FireRed's own descriptions; LeafGreen's are `pokedex_text_lg.*`, so the mod would ship them per edition, the way it already ships its dialogue).

#### 5. Help system and quest log

The help system and the "Previously on your quest…" log are extracted packs installed straight into their UI modules (`Help.install`/`QuestLog.install`, `src/core/Game3.lua:152-153`, v0.3.63), with no registry. Corpus: `frlg.script.help_system.*` (369 rows) and `frlg.common.quest_log.*` (125), both keyed by pret label. Their button bars go through `Strings()`; the help text's `{PC_OWNER}` reads the cart's `gString_Bill`/`gString_Someone` (`src/ui/game3/help_system.lua:220`, v0.3.63), but its `{PLAYER}`/`{RIVAL}` fallbacks (`PLAYER`, `RIVAL`, `:217-218`) stay English with the text they belong to. The quest log also records place names in English (`R.location`, `src/core/game3/quest_log_recorder.lua:4-10`, v0.3.63, reads the map section extract's name), so a translated log would still mix in English places.

Fix: a text registry (or the dialogue `text` registry itself) consulted by both packs, keyed by their pret labels, and place names recorded as section ids and translated when the log is shown.

#### 8. The naming keyboard is the US layout

At v0.3.63 the naming screen draws its pages from the cart's own keyboard tilemaps (`Naming.pagesFromKeyboard`, `src/ui/game3/naming.lua:491`, extracted by `src/import/gba/extract_naming.lua:188-190`), so the letters are the US cart's. The European carts add accented letters (and, in German, `ß`) to their keyboards, so a translated player cannot type the names the translated game uses (`BULBIZARRE` is fine; `ÉCRAS'FACE`-style nicknames are not). Fix: a keyboard layout the language can supply (a registry or a `Strings()`-style table of rows), drawn with the Latin glyphs of entry 1.

#### 9. Minor extraction and runtime inconsistencies

- The `PK`/`MN` ligature pair (`53 54`) decodes to `{PK}`/`{MN}` tags in `TextIR.decode` (`TextIR.LIGATURE`, `src/core/game3/scripting/text_ir.lua:243`, v0.3.63) but to the text `"POKéMON"` in the trainer extractor (`src/import/gba/trainer_extract.lua:153`, v0.3.63); the pipeline follows each one where it applies.
- The items extractor drops the `POKEBLOCK` glyph run (`55`–`59`), so item 273 reads `" CASE"` and its description `"A case for holding S made…"` in English too. The item cannot be obtained in FireRed, so the pipeline leaves it out of the join and of the coverage (`UNOBTAINABLE_ITEMS` in `pipeline/frlg/mod.py`) rather than asking for an extractor fix.
- The summary's met-location line used to print the "PALLET TOWN" fallback for every mon. At v0.3.63 it names the section the mon was met in (`met_location_name`, `src/core/game3/summary_data.lua:240`, after `pokemon_summary_screen.c:2632`), from the section extract's English name; whether that name reaches the screen translated has not been measured at the pin.
- Type badges and some chrome are ROM graphics with English text baked in; they are out of reach of any text mod.

#### 10. Cart text the extractor never reaches

Joining the other way round -- every `frlg.script.*` corpus row whose pret label has an address in `pokefirered.sym` but no key in the extracted text table -- found 1,078 FireRed lines at v0.2.67 that the game3 text registry cannot carry, because the script BFS (`src/import/gba/extract_scripts.lua`) never reads them. Part of it is dead content in FireRed, the rest is shown in game from Lua literals or by a native that bypasses the table:

| Corpus namespace | Rows | In game |
| --- | ---: | --- |
| `help_system` | 369 | Help menu (entry 5) |
| `fame_checker` | 320 | FAME CHECKER key item |
| `new_game_intro` | 65 | Oak's speech: through `Strings()` |
| `safari_zone` | 31 | Safari Zone gate, ball count, time-up |
| `event_scripts`, `cable_club` | 56 | Link-cable and record-mixing counters |
| `pokedude` | 20 | Viridian City Pokédude battle tutorial |
| `pokedex_rating` | 17 | Prof. Oak's rating through a PC |
| `field_moves`, `surf` | 13 | Waterfall/Surf prompts: through `Strings()` where game3 writes them |
| `obtain_item`, `white_out`, `save`, `pc`, `day_care`, `itemfinder`, VS Seeker (`trainers`) | 35 | Coin pickups, blacking out, save prompts, PC access, Day-Care, Itemfinder, VS Seeker |
| rival and trainer lines on individual maps (`SilphCo_7F`, `Route22`, `SSAnne_2F_Corridor`, `Route21_North`, …) | 62 | Rival win lines and a few map scripts the BFS does not reach |
| `berries`, `competitive_brothers`, `eon_ticket`, `mystery_event_msg`, `test`, `*JP*`/`JPText_*` lines | 90 | Ruby/Sapphire leftovers, Japanese-only and debug text: not shown in FireRed |

Where game3 writes the text itself (white-out, REPEL, VS Seeker, field moves, save and PC prompts), it goes through `Strings()` and the pipeline joins the cart's row by its English, so those lines are translated even though the extractor does not emit their keys. The Safari Zone, cable club, Pokédude, Pokédex rating and Fame Checker screens are still missing features or English-only paths. Trainer battle lines are not affected: 913 of the 919 trainer dialog strings carry a text key the table holds, and `trainerbattle` resolves them through it (`src/core/game3/scripting/ops_a.lua`); only the pack's plain-string fallback stays English.

Fix: seed the script BFS from the same labels the cart uses for these features (or extract those text tables alongside the scripts), and have the Lua screens look their messages up in the text table by that key. Because every row above is keyed by a pret label with a known address, the pipeline joins them without any new work: they land in `lang/dialogue.lua` as soon as the extractor emits their keys.

#### 13. The mod manager's own screens are in English

The mod manager (`src/ui/game3/mod_manager.lua`) writes most of its screens from English literals: the three tab headers (`[MODS] PROFILES ERRORS`...), the titles of its six screens (`MOD DETAIL`, `MOD OPTIONS`, `PERMISSIONS`, `PENDING CHANGES`..., `:40-44`, v0.3.63) and the states it prints for a mod (`ENABLED`, `DISABLED`, ` (STAGED)`, `:315-316`, v0.3.63, `FAILED: `, `SKIPPED: `). What does reach `Strings()` is translated by this mod: the list screen's title, the quantity box, the YES/NO of its confirmations and the four hints of its help bar (`Strings(helpText())`, `:76`, v0.3.63), which the scope reads from the returns of `helpText`. The rest leaves a translated game on an English manager.

Fix: hand those literals to `Strings()` too, as the help bar and the option rows already do. They are the engine's own screens, with no cart row to follow, so the catalog would carry them like the other engine strings this mod translates.

#### 14. The map name popup spells its floor label itself

The map name popup prints a section name followed by the floor, and builds that floor label with `string.format("%dF", floor)` / `"B%dF"` before handing it to `Strings()` (`translated_name`, `src/ui/game3/map_name_popup.lua:290-299`, v0.3.63). The cart has a row for each of them (`gText_1F` … `gText_11F`, `gText_B1F` … `gText_B4F`), and the popup already reads one of those rows for the rooftop (`RomText.plain("gText_Rooftop2")`). A European cart counts floors its own way — French writes 3F as `2e` — so the label has to be translated, and the only key that reaches this lookup is the English `"3F"`, which `modkit pack` refuses (entry 15).

Fix: read the floor label from the cart as the rooftop already is, `RomText.plain(("gText_%dF"):format(floor))` and `("gText_B%dF")` below ground, so the label follows the mod's ROM text like every other cart string.

#### 15. A cart string keyed by its English is refused, even where nothing else reaches it

Since v0.3.0 a mod keys the cart's text by its ROM label, and `modkit pack` refuses a `lang/strings.lua` that still keys one by its English (MK306, `tools/modkit.py:1170`, v0.3.63). The pipeline follows Modkit's own migration, so the catalog ships labels. Three lookups read their value by its English text and by nothing else, though, and their entries would simply stop being found:

- the region map's section names and its guide text (`Strings(RegionExtract.SECTION_NAMES[...])` and `Strings(desc)`, `src/ui/game3/region_map.lua:375`, `:395`, v0.3.63). The section name is what the map name popup prints when the player enters a town, so this is `JADIELLE` against `VIRIDIAN CITY` on screen.
- the ability name (`Pokemon.abilityName` returns `Strings(n)`, `src/core/game3/pokemon.lua:335`, and the summary passes a stored name through `Strings(abilityText)`, `src/ui/game3/summary_menu.lua:927`, v0.3.63). 70 of those names are also an Easy Chat word, so their English is cart text and the key is refused.
- the floor label of the map name popup (entry 14).

That is about 150 strings per language. Until the engine reads them by label, the pipeline writes exactly those entries to a catalog of its own (`lang/strings_by_english.lua`, `ENGLISH_LOOKUP_SITES` in `pipeline/frlg/mod.py`) and registers them through the same `mod.content.strings:override`, which MK306 does not read: the runtime resolves an English key exactly as before, as Modkit's own documentation states ("A catalog made before labels were used, keyed by the English, still translates"). It is a shim around a lint, not around the runtime, and it goes away with the fix.

Fix: give those three lookups the label path the rest of the cart's text has — the ability name through a registry or `RomText`, the section name and its description through `sMapsecName_*` and `gText_RegionMap_AreaDesc_*`, the floor label through `gText_%dF` — or let MK306 spare an English key the engine itself passes to `Strings()` from a table the scaffold lists.

#### 16. A cart fragment the engine concatenates cannot be reordered

Five rows are fragments the cart prints between two buffers -- `gText_LevelRoseTo` (`[name]` + `"'s level rose to\n"` + `[level]`), `gText_WasUsedOn`, `gText_PkmnsNickname`, `gText_Var1sTrainerCard` and `sText_BerrySuffix` -- and two more join them per language where that language rewords a second line the same way (the SPIKES message in German and Spanish, the quest log's opening and the item-tossing prompt in Italian). Japanese has 37, since its phrasing names the player or the mon where the English line does not. Each European cart rewords the whole sentence instead, and puts the buffers where its own grammar wants them (`CARTE DE DRESSEUR de [STR_VAR_1]`, `BAYA [B_COPY_VAR_3]`), so the corpus row carries the buffers and the ROM row does not. A mod can only replace the fragment, which the engine still prints between the same two buffers, in the cart's English order: the five rows stay English rather than read wrong. Measured before v0.3.63; at the pin the trainer card's title (`src/ui/game3/trainer_card.lua:941`) and the nickname prompt (`src/ui/game3/naming.lua:76`) read `gText_Var1sTrainerCard` and `gText_PkmnsNickname` whole through `RomText.plain` with the name as a variable, which has not been re-measured.

Fix: give those five lookups the cart's own row instead of the fragment, as `Strings()` already does elsewhere with numbered directives (`%2$s ... %1$s`), so a translation can put the buffers where the language needs them.

#### 17. Engine rows with no FireRed cart row

Since Gen1Recomp v0.3.47, 12 reachable dynamic keys have no reviewed FireRed corpus row (still the case at v0.3.63: 1,934 of the 1,946 keys resolve), so all five FRLG language catalogs keep their natural English fallback for them. The join report keeps these keys in `fallback_english`; they are not represented by identity overrides as translated. `test_reviewed_qids_exist_in_the_pinned_corpus` pins the reviewed fallback set so future engine or corpus changes require an explicit review.

### Translated via a compromise (`engine-contract-gap`)

`overrides/<language>/frlg/engine.json` holds about 155 entries per language (156 in French: 112 `engine-original`, 27 `engine-corpus`, 17 `engine-contract-gap`). The kinds of compromise:

- **TM/HM pickup** (`Text_FoundTMHMContainsMove`): gen1recomp's item-ball script buffers only the TM's name and prints `"[PLAYER] found\n[STR_VAR_2]!"`, while the cart's line also names the move from `STR_VAR_1`. Each language keeps the first clause of its own cart row (`overrides/<lang>/frlg/dialogue.json`); German, whose cart line names only the move, is reworded around the TM name.
- **Corrupted-save warning**: `src/ui/game3/boot.lua` prints the cart's `gText_SaveFileCorrupted` as two `Strings()` pages. The official translation is split at its sentence (Italian: paragraph) boundary.
- **Trainer names**: gen1recomp composes "<class> <name>" (`battle/init.lua`, `switch_seq.lua`, `trainers.lua`) and passes it as one argument to messages such as "%s defeated\n%s!". The Italian cart writes "<name>, <class>"; the Italian lines keep gen1recomp's order.
- **Lines gen1recomp words its own way**: keys with no cart row that reads as them. They are either English the port added (PC item storage, Hall of Fame banner, bicycle, repel reuse, release, OAK's refusal without the player's name…), or cart messages gen1recomp rewords or splits (the Wonder Card bodies split into four lines, the stat-change lines the cart builds from `sText_AttackersStatRose` and a verb row, "gained a boosted", the double send-out, the berry flavour lines). They are worded from the nearest cart row, named in each entry's `provenance` (`reason: "engine-corpus"`, `"engine-contract-gap"` or `"engine-original"`).
- **One English label, several cart wordings**: FireRed words the same English differently from menu to menu (CANCEL is RETOUR in most French menus, ANNUL. in the PC). A `Strings()` key has one value, so the scope keeps the wording most cart rows share for that English (a `REVIEWED` pin where the majority is the wrong sense: FIGHT is the battle menu's ATTAQUE, not the FIGHT type).
- **Shared `OFF`/`NORMAL` keys**: `Strings("OFF")` serves both the volume and music-filter rows, and the project's existing Red/Blue/Gold wording is reused for it.
- **Named glyph runs**: the cart's superscript ordinals (`[SUPER_E]`/`[SUPER_ER]`/`[SUPER_RE]`, French "1er"/"2e", Spanish "1.er") and the `[POKEBLOCK]` glyph run have no single character FrlgFont can map, so translations spell them out as plain letters ("er", "POKéBLOCK").
- **Context-dependent shared keys**: `UPPER` (screen position) and `LIGHT` (vibration strength) reuse the Red/Blue/Gold keys, whose wording there is the naming keyboard's upper case and the light/lamp sense; FireRed overrides give them the Options meaning.
- **Port-added Options rows** (group labels, OVERWORLD/BATTLE/MENU SPEED, RETURN TO MAIN MENU?) have no cart text; they are AI-composed per language following the project's existing wording (`reason: "engine-original"`).

### Verified working, not a gap

- **Dialogue overrides keep placeholders and page breaks.** `Schemas.GEN3` routes `text` to `data.gen3Text`, which `Game3:_exposeModData` (`src/core/Game3.lua:302`, v0.3.63) binds to the live `Space.bundle.text` before mods load; `ExtractScripts.loadBundle` only ever builds that bundle once, so overrides persist. A plain string override would lose the player name, `STR_VAR_n` buffers and scroll/paragraph breaks (`G3.textIr` only splits on `\n`), but the registry also accepts the IR segment list itself (`gen3Value = f.union{ f.str, f.list(f.any) }`), which is what the mod ships. The gate reads the override back from `data.gen3Text` and renders it through `TextIR.toTextBox`, the call `src/ui/game3/message.lua` uses.
- **Dialogue keys can be joined exactly.** The extractor keys messages by ROM pointer (`Opcodes.key`, `g3:%08x`); pret's published symbol table (`pokefirered.sym`, `symbols` branch) names every one of those addresses with the label the PokeCorpus qid ends with. 3,559/3,559 pointer keys resolve to exactly one corpus row, and for every non-braille row the corpus English, encoded through pret's charmap and decoded by a port of `TextIR.decode`, reproduces the extracted IR exactly (same segments, texts and codes). No upstream change is needed for dialogue keys.
- **Trainer patches preserve parties.** A `trainers` patch rewrites the whole row through `G3.trainerRecord`/`G3.trainerWrite` (species, held items and moves converted to ids and back). The gate compares all 743 parties before and after the mod loads: none changes.
- **The start menu is hookable.** `src/ui/game3/start_menu.lua:183-184` (v0.3.63) hands its entry list to `ui.start_menu.items` before printing the labels, so the mod relabels POKéDEX/POKéMON/BAG/SAVE/OPTION/EXIT from the cart's `gText_Menu*` rows. This is the only public `ui.*` hook game3 raises.
- **Standard scripts gen1recomp rewrote itself are still joinable.** Seven label-keyed lines of `src/core/game3/scripting/stdscripts.lua` (nurse greeting, "OK." vs "Okay,", item pickup, PC unavailable, TOWN MAP sign) are the same messages as cart rows with different English wording. They are joined through reviewed decisions (`config/frlg/dialogue_decisions.json`); not a gap.

### Fixed upstream

v0.2.67 (gen1recomp#2346 and #2342) routed game3's own text through `Strings()` and gave the ROM font its European letters; v0.2.70 (gen1recomp#2374 and #2375) kept the species and move names and translated Easy Chat; v0.3.4 read most of game3's text from the cart's own rows through `RomText` and brought the egg, trainer-class, braille and Japanese-font fixes; v0.3.11 and v0.3.14 rewrote the link screens. Details: [v0.2.67](upstream-fixes-history.md#firered-fixed-upstream-in-v0267), [v0.2.70](upstream-fixes-history.md#firered-fixed-upstream-in-v0270).

#### 1. Accented letters are drawn blank (blocks readable fr/de/es/it)

Fixed in v0.2.67 by gen1recomp#2342 (`FrlgFont.LATIN_GLYPHS`); the release gate reports `blank_glyphs: 0` ([details](upstream-fixes-history.md#firered-1-accented-letters-were-drawn-blank)).

#### 2. Name patches are reverted on entering the field

Fixed in v0.2.70 by gen1recomp#2374: `Pokemon.onReload` re-applies the `moves` and `pokemon` registries ([details](upstream-fixes-history.md#firered-2-name-patches-were-reverted-on-entering-the-field)).

#### 3. Japanese cannot be rendered (fixed upstream in v0.3.4)

Fixed in v0.3.4 by gen1recomp#2406: the cart's own Japanese fonts draw Japanese text ([details](upstream-fixes-history.md#firered-3-japanese-could-not-be-rendered)).

#### 6. Trainers are recognised by their English class name

Fixed in v0.3.4 by gen1recomp#2398: the rival, the quest log and the battle transition read the class id ([details](upstream-fixes-history.md#firered-6-trainers-were-recognised-by-their-english-class-name)).

#### 7. Braille and keypad icons in dialogue (fixed upstream in v0.3.4)

Fixed in v0.3.4: braille by gen1recomp#2400 (Unicode braille cells), keypad icons and symbols by the `TextIR.decode` tags ([details](upstream-fixes-history.md#firered-7-braille-and-keypad-icons-in-dialogue)).

#### 11. Easy Chat words are engine data, not a registry

Fixed in v0.2.70 by gen1recomp#2375: words through `Strings(word, "easyChat.<group>")`, species and moves through the name tables ([details](upstream-fixes-history.md#firered-11-easy-chat-words-were-engine-data-not-a-registry)).

#### 12. An EGG is named in English in the party

Fixed in v0.3.4 by gen1recomp#2396: an egg is named by the cart's `gText_EggNickname` row (`src/core/game3/pokemon.lua:1307`, v0.3.63) ([details](upstream-fixes-history.md#firered-12-an-egg-was-named-in-english-in-the-party)).

#### 18. The honorific after the player's name -- fixed upstream in v0.3.52

Fixed in v0.3.52 by gen1recomp#2679: `{KUN}` expands from the cart's `gExpandedPlaceholder_Kun`/`_Chan` rows, which the mod ships ([details](upstream-fixes-history.md#firered-18-the-honorific-after-the-players-name)).

The Pokédex's imperial units, a compromise until then, are replaced by the official metric values since gen1recomp#2747 (v0.3.61) ([details](upstream-fixes-history.md#firered-the-imperial-units-compromise)).

## Ruby and Sapphire

Ruby and Sapphire run on the same game3 runtime as Emerald, as gen1recomp's latest generation-3 games (`GameVersion` ids `ruby` and `sapphire`, added by 67748895 and released in v0.3.52): the `rs` text dialect of `src/core/game3/scripting/text_ir.lua` (named placeholders such as `EVIL_TEAM` and `GOOD_LEADER`, the `FONT_RS_*` fonts, pokeruby's own battle placeholder numbers), Emerald's screens under `src/ui/game3/rse/` with the native Ruby/Sapphire ones under `src/ui/game3/rs/`, and the six English revisions `src/import/gba/rs_builds.lua` lists. The translation mod covers them with Emerald in one `translation-<lang>-gen3-rse` release.

This section was reviewed at the `dev` commit `160895c6`, and its file:line citations refer to it unless marked "(v0.3.63)"; the pin is v0.3.63 (`7ab2f865`), which carries gen1recomp#2724 (entries 1 to 3), #2743 (entry 5), #2745 (entry 4) and #2747 (entry 8).

Summary of what a translation mod can and cannot reach at the pinned revision:

| Surface | Reachable | Mechanism |
| --- | --- | --- |
| Cart text (13,336 rows: script messages, menus, battle messages, lists, the Pokédex entries' text, PokéNav, contests) | Yes, 99.1–99.5% of it in every language | `mod.content.text:override(key, ir)`, by ROM address or label, each edition's and revision's layer |
| Species, move, item names; item descriptions; trainer names and class names | Yes | `pokemon`/`moves`/`items`/`trainers` patches |
| game3's own text (2,552 `Strings()` keys) | Yes, except 6 engine rows (entry 6) | `strings` registry, keys listed in `config/rse/engine_scope.json` |
| The version placeholders: the teams, leaders and legendaries, the rival's name, the version name and the Japanese honorific | Yes since v0.3.58 (entry 1) | `text` overrides, which the placeholders read from the script cache |
| The native Pokédex screen's entry, category, labels and search screen | Yes since v0.3.58 (entry 2) | `text` overrides by the entries' and labels' pret names |
| Cart text the native screens print from their own pack: party menu actions and prompts, shop, decoration, move relearner, contest paintings, Easy Chat editors and words, the summary's contest effect descriptions | Yes since v0.3.58 (entry 3) | `text` overrides by label, `strings` for the contest descriptions |
| The same for the secret base, roulette, trainer card, diploma, berry tag, PokéNav and Battle Tower records screens | **No** (entry 9) | `text` overrides once their extractors record the labels |
| A secret base's name in the European languages | Yes since v0.3.61 (entry 4) | the runtime fills the row's `[PLAYER]` with the owner |
| Heights and weights in metric units, as the European and Japanese carts print them | Yes since v0.3.61 (entry 8) | `pokemon` patches (`dexEntry.heightM`/`weightKg`) |
| Japanese | Yes (the cart's own Japanese fonts) | as for FireRed |

### Required upstream capabilities

#### 6. Engine rows with no cart row

Six `Strings()` keys keep their English in every language, the same as Emerald's (Emerald entry 2): the controls screen's three rows, the bag actions `CHECK_TAG` and `OPEN`, and the Easy Chat word `{POKEBLOCK}`. The keys Ruby and Sapphire's carts never had (the link lobby, the Union Room's words, Mystery Gift) take Emerald's resolved value.

#### 7. Official rows that print a buffer the runtime does not fill

As for Emerald (entry 7), a few official rows print a value the US line does not: four European fragments (`BattleText_Berry`, `PCText_WasReleased`, `gOtherText_Berry`, `gOtherText_PokeBlockMade`) and 29 Japanese lines that name a trainer, the rival or Kiri with a `STR_VAR` buffer the US script leaves empty. Their English stays.

#### 9. Native screens whose packs keep their strings under local names

Split from entry 3, whose change did not reach these screens. The secret base, roulette, trainer card labels, diploma, berry tag, PokéNav and Battle Tower records packs keep their strings under local names, so the screens print the pack's English copy: at v0.3.63, `src/ui/game3/rs/{roulette,berry_tag,diploma,battle_tower_records,trainer_card}.lua` and `src/ui/game3/rs/pokenav/*.lua` make no `RomText` or `Strings()` call. Strings no cache key holds (the PokéNav's help lines, the option menu) need the import plan to add them first, as Emerald entry 5 describes.

Fix: have those extractors record each string's pret label, as gen1recomp#2724 did for the party menu and the shop, and the screens read the script cache by that label with the pack's copy as the fallback; the mod already ships the rows.

### Verified working, not a gap

- **Every revision.** A build reads one Ruby or Sapphire cart and keys the other edition's and revision's script text through pokeruby's symbol tables (`pipeline/rse/join.py`). Built from Ruby 1.2 and from Sapphire 1.0, the other edition's layers equal the ones read from its own cart, except 8 or 9 lines only that edition prints whose label has no corpus row; the mod tells 1.0 from 1.1/1.2 at runtime by a guard address (`lang/rs/layouts.lua`), which the release gate checks on the cart it reads.
- **European name order.** The European Ruby/Sapphire rows have the same shape as Emerald's: the wild and foe words follow the name (" sauvage", " (Wild)", pokeruby's German `HANDLE_NICKNAME_STRING_CASE`, `src/battle_message.c:505`) and the "sharply" rows carry the whole change ("monte beaucoup!", `:851`), so gen1recomp#2680's changes apply to them as they are.
- **European trainer classes.** Ruby and Sapphire's trainer classes come in a male and a female form (TUBER, PLAYERO and PLAYERA), and the corpus's trainer names and classes are the European carts' own: no review is needed, unlike Emerald's.
- **Japanese battle placeholders.** The Japanese rows name five battle escapes after the Emerald table (`[B_COPY_VAR_2]` for the opponent's POKéMON, `[B_26]` for the attacker's prefix); they are the same bytes, which the `rs` dialect encodes under both names.

### Fixed upstream

#### 1. The version placeholders came from the US cart

Fixed in v0.3.58 by gen1recomp#2724: the message box reads the teams, leaders, legendaries, rival, version and honorific from the script cache by Ruby and Sapphire's labels ([details](upstream-fixes-history.md#ruby-and-sapphire-1-the-version-placeholders-came-from-the-us-cart)).

#### 2. The native Pokédex screen printed its pack's copy

Fixed in v0.3.58 by gen1recomp#2724: the entry page, its labels and its search screen read the script cache by pret label ([details](upstream-fixes-history.md#ruby-and-sapphire-2-the-native-pokédex-screen-printed-its-packs-copy)).

#### 3. Native screens printed their own pack's copy of cart text

Fixed in v0.3.58 by gen1recomp#2724 for the party menu, the shop, the decoration menus, the move relearner, the contest paintings, the summary's contest descriptions and the Easy Chat words ([details](upstream-fixes-history.md#ruby-and-sapphire-3-native-screens-printed-their-own-packs-copy-of-cart-text)); the screens it did not reach are entry 9.

#### 4. A secret base's name ended in the English row

Fixed in v0.3.61 by gen1recomp#2745: `SB.nameWith` fills the row's player placeholder with the owner ([details](upstream-fixes-history.md#ruby-and-sapphire-4-a-secret-bases-name-ended-in-the-english-row)).

#### 5. The starter's category was cut by bytes

Fixed in v0.3.61 by gen1recomp#2743: the category is cut by character ([details](upstream-fixes-history.md#ruby-and-sapphire-5-the-starters-category-was-cut-by-bytes)).

#### 8. Heights and weights were printed in US units

Fixed in v0.3.61 by gen1recomp#2747: the native Pokédex prints a species' metric height and weight when a mod gives them ([details](upstream-fixes-history.md#ruby-and-sapphire-8-heights-and-weights-were-printed-in-us-units)).

## Emerald

Emerald (US, v1.0) runs on the same game3 runtime as FireRed, as its own game family: `GameVersion` id `emerald`, the `rse` text dialect of `src/core/game3/scripting/text_ir.lua` (named `ph` placeholders, named `FC 06` fonts, the `{POKEBLOCK}`, `{LV}` and arrow glyph runs), and its own screens under `src/ui/game3/rse/`. Emerald ships with Ruby and Sapphire in one translation mod (`translation-<lang>-gen3-rse`, fr/de/es/it/ja-Hrkt), as the release's companion edition: built from the Emerald ROM, it goes through the same joins as FireRed (`pipeline/gen3/`) with the Emerald family (`pipeline/gen3/family.py`): the Emerald PokeCorpus collection, pret's `pokeemerald.sym` (`symbols` branch) and `charmap.txt`, and its own reviewed configuration (`config/rse/emerald_*.json`). `tools/rse/extract.lua` runs the text steps of the engine's own Emerald import plan (`src/import/gba/plans/rse/`), and `tools/rse/gate.lua` loads the mod through the real generation-3 loader on top of the game3 data modules built from that extract, so the statements below are measured, not inferred.

This section was reviewed at `a729af23` (v0.3.51), and its file:line citations refer to it unless marked "(v0.3.63)"; the pin is v0.3.63 (`7ab2f865`), which carries gen1recomp#2678 (released in v0.3.53), #2679 to #2681 and Ruby and Sapphire (released in v0.3.52).

Summary of what a translation mod can and cannot reach at the pinned revision:

| Surface | Reachable | Mechanism |
| --- | --- | --- |
| Cart text (15,444 rows: script messages, menus, battle messages, lists, the Pokédex screens, PokéNav, contests, the Battle Frontier, the intro) | Yes, 99.9% of it | `mod.content.text:override(key, ir)`, by ROM address or label |
| Species, move, item names; item descriptions; trainer names and class names | Yes | `pokemon`/`moves`/`items`/`trainers` patches |
| game3's own text: its menus and prompts, the options it adds, the move and ability descriptions, Easy Chat (2,685 `Strings()` keys) | Yes, except 7 engine rows (entry 2) | `strings` registry, keys listed in `config/rse/emerald_engine_scope.json` |
| Ability names, Pokédex categories and descriptions, contest categories and effect descriptions, map section names | Yes since v0.3.53 (entry 1) | `strings` registry |
| Cart text some screens print from their own pack: the party menu's actions, the Pokédex search screen, the Battle Frontier's records, Dome, Arena, Apprentice, S.S. Tidal menu and Pyramid bag, the Frontier Pass, the Trainer Hill records, the Battle Pyramid's floor names, Ever Grande City's fly destinations | Yes since v0.3.53 (entries 3 and 4) | `text` overrides, which the screens read from the script cache |
| The rival's name (`{RIVAL}`) and the Japanese honorific (`{KUN}`) | Yes since v0.3.53 (entry 6) | `text` overrides, which the placeholders read from the script cache |
| Heights and weights in metric units | Yes since v0.3.61 | `pokemon` patches (`dexEntry.heightM`/`weightKg`) |
| Official rows that print a buffer the runtime does not fill (party menu prompt in de/ja, Spikes, Shadow Tag in ja) | **No** (entry 7) | the runtime filling the buffer |
| Cart text the script cache does not carry: berry names and descriptions, decorations, the Frontier lounges' messages, the Battle Tower multi battle partners' lines, the Pyramid's rest and retire prompts and hints | **No** (entry 5) | needs extraction, then the same lookup |
| The Berry Blender's messages and opponents' names | **No** (entry 10) | the screen reading the script cache |
| Japanese | Yes (the cart's own Japanese fonts) | as for FireRed |

### Required upstream capabilities

#### 2. Engine rows with no Emerald cart row

Seven `Strings()` keys have neither an Emerald corpus row nor a reviewed override, in every language, and keep their English: the controls screen's `ESC/2ND CANCELS`, `PRESS A BUTTON` and `RELEASE TO SET`, FireRed's bag actions `CHECK_TAG` and `OPEN` (shared code), the `SPECIAL AREA` map section (an empty row in every European cart) and the Easy Chat word `{POKEBLOCK}`, whose European rows are each cart's own glyph run. FireRed leaves the controls screen's three and `CHECK_TAG` in English too (`test_reviewed_qids_exist_in_the_pinned_corpus`); `test_engine_strings_resolve_in_every_language` pins Emerald's set.

The Wonder Cards gen1recomp composes for Emerald's events (`src/core/game3/mystery_gift.lua`, `rseBuiltins`) have no cart row either: FireRed's reviewed overrides cover most of their lines, and `overrides/<language>/rse/emerald_engine.json` covers the rest (`It is for use at LILYCOVE CITY port.`, `We received this OLD SEA MAP`, `addressed to you.`, `on ROUTE 103.`) and the `EVENT TICKETS` option (`src/core/game3/rse/event_islands.lua:101`), worded from the Emerald cart's own names (NENUCRIQUE, VIEILLECARTE, ROUTE 103). In Japanese, the ALTERING CAVE card names the cave as Emerald does (へんげのどうくつ) instead of FireRed's override.

#### 5. Cart text the Emerald script cache does not carry

Some screens print cart strings that only their own pack holds: the script cache has no key for them, so a text override cannot reach them even through entry 4's lookup, and this project's extract has no row to join. Entry 4's change leaves these screens reading their copy (spot-checked at v0.3.63 for the lounges, the Battle Tower partners, the Pyramid's retire prompt and the decorations). The Emerald corpus has the official rows for most of them.

- Berries: the name and both description lines on the berry tag and in the berry tree messages (`src/core/game3/rse/berry_trees.lua:46`, `gBerries`, corpus `e.common.berry.*`).
- Decorations: names and descriptions in the PC and the secret base (`src/ui/game3/rse/decoration.lua:940`, `:959`, `gDecorations`).
- The Battle Pyramid's rest and retire prompts (`src/ui/game3/save_menu.lua:323`, `src/ui/game3/rse/pyramid_retire.lua:23`, `gText_BattlePyramidConfirmRest`/`Retire`) and its post-battle hints.
- The Battle Frontier lounges: the Frontier Maniac's, the nature girl's and the gambler's messages (`src/core/game3/scripting/natives_frontier.lua:387-415`, `BattleFrontier_Lounge2_Text_*`, `Lounge3`, `Lounge5`), and the Battle Tower multi battle partners' lines (`src/core/game3/rse/frontier/tower.lua:490`). The script cache does key the partners' lines by their table's label (`sPartnerTextsHiker[0]`), but the pack keeps only each line's own symbol, which the cache does not hold, so the partners need their table's name in the pack rather than a new extraction.
- Strings gen1recomp writes itself in Lua: the contest results' link save error (`src/ui/game3/rse/contest_results.lua:483`), which has no cart row, and the Pokédex's weight in pounds (`src/ui/game3/rse/pokedex.lua:910`), as for FireRed, which a mod's metric values replace since v0.3.61. The Berry Blender's two link messages are written in Lua too (`src/ui/game3/rse/berry_blender.lua:1005-1007`), although the cache holds the cart's own `sText_HasNoBerriesToPut` and `sText_ApostropheSPokeblockCaseIsFull`.

Fix: have the import plan put these strings in the script cache's text table (pret's `TEXT_TABLES`/`NAMED_TEXTS` in `src/import/gba/versions_text_emerald.lua` already list most Emerald tables) and the screens read them through entry 4's helpers (`RomText.refIr`), so this project's extract emits their keys; the join then picks up their corpus rows with no new work.

#### 7. Buffers the runtime does not fill

Some official rows print a value the US line does not, which the runtime never fills, so their English stays:

- the party menu's prompt "Do what with this PKMN?" in German and Japanese, whose rows name the POKéMON with `STR_VAR_1`, while the menu draws `RomText.plain("gText_DoWhatWithPokemon")` with no variables (`src/ui/game3/party_menu.lua:2973`, v0.3.63);
- 22 Japanese lines (36 keys) that name a trainer, the rival or Kiri with a `STR_VAR` buffer where the US script prints the name in the text or names nobody (`Route104_Text_GinaPostBattle`, `LittlerootTown_Text_YouSavedBirch`, `SootopolisCity_Text_*Kiri*`, `BattleDome_Text_TrainerBecameChamp`...); the rows that only add the player's name or the honorific after it are not among them, since the runtime always fills those (`ALWAYS_FILLED`, `pipeline/gen3/join.py`);
- the Shadow Tag / Arena Trap message in Japanese, whose row also names the POKéMON trying to switch with `B_BUFF2`, which pret buffers (`pokeemerald/src/pokemon.c:6664`) and the runtime does not (`src/core/game3/battle/engine.lua:2131`);
- the Spikes message in German, Spanish and Japanese, whose rows name the target side with `B_DEF_PREFIX1`, while the runtime calls `sayText("STRINGID_SPIKESSCATTERED")` with no target (`src/core/game3/battle/effects/hazards.lua:51`, v0.3.63).

A secret base's name is no longer one of them: its French, German, Spanish and Italian rows put the owner inside ("BASE DE [STR_VAR_1]"), which gen1recomp#2680 (v0.3.52) fills with the owner (`src/core/game3/rse/secret_base.lua:602` at v0.3.54), so `overrides/<language>/rse/emerald_dialogue.json` ships them with `runtime_fills`.

The TM shop's prompt is not one of them: its rows name the move with `STR_VAR_2`, which the runtime does buffer (`src/ui/game3/rse/shop_menu.lua:240`), so `overrides/<language>/rse/emerald_dialogue.json` ships the official rows with `runtime_fills`.

Fix: fill the buffers the official rows use, as pret does.

#### 8. Descriptions are keyed by their English

The move and ability descriptions go through `Strings()` by their English text (`SummaryData.moveDescription`/`abilityDescription`, `src/core/game3/summary_data.lua:406-435`, v0.3.63), and some moves or abilities share one English text the carts word for each of them: Seismic Toss and Night Shade (Japanese: "いんりょくを　りようして　なげる" against "おそろしい　まぼろしを　みせ"), Absorb and Leech Life, Eruption and Water Spout in FireRed's Japanese. One key carries one row, so the other move shows the first one's wording. Most shared texts read the same for every entry; where one row fits all of them and the automatic one does not, `REVIEWED`/`REVIEWED_RSE` in `pipeline/gen3/engine_scope.py` pick it (Cloud Nine's "Keine Wetter-Effekte" over Air Lock's "Kehrt Wetter-Effekte um", Sludge's Spanish row over Sludge Bomb's "Explosión de lodo"). The Pokédex categories several species share read the same in every language. Fix: look the text up by move or ability id, as the cart does.

#### 9. Italian lines that spell out a glyph run

The Italian cart draws `POKéMELLA`/`POKéMELLE` (POKéBLOCK) as a glyph run, which the rows spell out as letters, wider than the cart's glyphs: seven Italian lines (11 keys) exceed the 216-pixel message box by 1 to 11 pixels (`LilycoveCity_ContestLobby_Text_LadyGaveMePokeblockCase`, 227 pixels). Fix: draw the glyph run as the cart does.

#### 10. The Berry Blender reads its own pack

Split from entry 4, whose change did not include it. The script cache holds the Berry Blender's messages (`sText_BerryBlenderStart` and 26 more of the 28) and its opponents' names (`sBlenderOpponentsNames`), but the screen and the blender's logic read them from the `rse/berry_blender` pack under keys of their own (`self.man.texts`/`self.man.irs`, `src/ui/game3/rse/berry_blender.lua:58-59`, v0.3.63, and `src/core/game3/rse/berry_blender.lua`), plain strings included, so the screen prints English. Fix: thread the script cache lookup (`RomText.refIr`, as entry 4 does) through both the screen and the blender's logic.

### Translated via a compromise (`engine-contract-gap`)

- **The move-use line in Japanese.** The cart's row is `[B_ATK_NAME_WITH_PREFIX][B_BUFF1]\n[B_BUFF2]`, whose particle and ending depend on the move (`ChooseMoveUsedParticle`, `ChooseTypeOfMoveUsedString`, `pokeemerald/src/battle_message.c:2882`, `:2922`); the runtime fills no particle and always appends `sText_ExclamationMark` (`src/core/game3/battle/battle_text.lua:249`), whose Japanese row ("を　つかった！") is the ending of a small group of moves. `overrides/ja-Hrkt/rse/emerald_dialogue.json` gives both the values of the moves outside `sGrammarMoveUsedTable`, most of them: the particle `sText_ApostropheS` ("の") and the ending `sText_ExclamationMark5` ("！"), so the line reads "ポチエナの\nたいあたり！" as the cart prints it for those moves; the 114 moves of the table read the same instead of their own ending, and 67 of them take の where the cart has は. The Japanese FireRed mod does the same (`overrides/ja-Hrkt/frlg/dialogue.json`).
- **The continue window in Japanese.** The cart's rows print their value themselves ("しゅじんこう　[STR_VAR_1]"); the runtime draws the value in its own column (`src/ui/game3/rse/main_menu_rse.lua:509`), so the Japanese overrides ship the label part only.
- **Japanese labels with an empty row.** The Easy Chat footer (DEL. ALL, OK, QUIZ, ANSWER), the Pokédex's HT/WT and the Battle Frontier's list joiners (" and ", ", ", `pokeemerald/src/frontier_util.c:1949`) have an empty Japanese row. `overrides/ja-Hrkt/rse/emerald_dialogue.json` takes the cart's own words where it has them (たかさ/おもさ from FireRed's `gText_HT`/`gText_WT`, けってい from `gText_Confirm2`, クイズ and こたえ from `gText_TheQuizColon`/`gText_TheAnswer`), joins a list with と and ・ (the Japanese font has no comma) and words DEL. ALL ぜんけし. The Birch intro's YES/NO takes the cart's other YES/NO row (`sUnusedText_YesNo`).
- **Option values a corpus row says in another sense.** The screen position's UPPER (the naming keyboard's upper case in `gText_Upper`), the SPEED group label (the SPEED stat, abbreviated `INIT.` in German, `VELOCID.` in Spanish and `VELOC.` in Italian) and Japanese ON/OFF (the battle scene's みる/みない) take the FireRed, Gold/Silver or Red/Blue overrides' wording, in both game3 families.
- **Glyph runs.** The cart draws `{POKEBLOCK}` and `{LV}` as glyph runs the `rse` dialect reads as tags. A translation keeps the tag the English row has, so the French, German and Spanish rows' `[POKEBLOCK]` show the US cart's run; the Italian carts' own runs are spelled out (`[POKEMELLA]` as POKéMELLA, like the French Battle Points symbol `[Pco]` as Pco, which the French rows also write in letters).
- **A section name that holds a placeholder.** `MAPSEC_AQUA_HIDEOUT_OLD` holds the team's name as a placeholder, which the map section extractor drops (" HIDEOUT" in English); its translation drops it the same way.

### Verified working, not a gap

- **Dialogue overrides reach Emerald's text.** `mod.content.text` writes into the live `Space.bundle.text` exactly as for FireRed, and `RomText.ir`/`RomText.box`, `BattleText.get` (the `STRINGID_*` keys) and `SummaryData.NATURES` read it back; the gate checks each. Emerald's 15,000 IR lists exceed LuaJIT's 65,536 constants per chunk, so the mod writes them in several files (`lang/dialogue.lua`, `lang/dialogue_2.lua`, ...), and its `main.lua` fails loudly on a file it cannot load instead of skipping it.
- **Pointer tables join exactly.** The extractor keys a ROM pointer table by its name and index (`gNatureNamePointers[3]`, `sMenuTexts[25]`, `gStdStrings[18]`, the `stdstring:<n>` script menu entries) and the battle string table by `STRINGID_*`; the corpus names the strings those pointers reach (`sHardyNatureName`). `tools/rse/extract.lua` writes each slot's pointer (`rse_text_pointers.json`) and the join names it with pret's symbol table, so these rows join on a symbol like any script message.
- **Lines left in English by every European cart are unused.** Outside the credits, the rows that read the same as English in every European corpus (`gText_Birch_Pokemon`, the Battle Tent rules variants, the `*2` Union Room and trade-center lines, `gText_HOFDexRating`...) are almost all lines pokeemerald never references and gen1recomp's Emerald screens never draw; the mod ships no stand-in for them.
- **A label keeps its own row.** The engine catalog's ROM-label migration copies an English entry onto every label with that English, and `RomText.translate` reads a label's catalog entry before the label's text: the French cart's SORTIR menu would have read the RETOUR of another one. A label the dialogue join has its own row for keeps it (`dialogue_label_rows`), for FireRed too, and both release gates fail if a shipped dialogue label also has a catalog entry.
- **One French corpus row is empty.** `Route119_Text_StayAwayFromWeatherInstitute` is empty in the French corpus only; `overrides/fr/rse/emerald_dialogue.json` ships the French cart's own line, read from pret's multi-language decompilation (`data/maps/Route119/text_fr.inc`), which builds the French cart byte for byte.
- **The start menu needs no hook.** Emerald's start menu prints the cart's `gText_Menu*` rows through `RomText` (`src/ui/game3/rse/start_menu_data.lua:8`), which the named text join translates.

### Fixed upstream

#### 1. Emerald screens print the cart's English names and descriptions as they are

Fixed in v0.3.53 by gen1recomp#2678: ability names, Pokédex entries, contest texts and map section names go through `Strings()` ([details](upstream-fixes-history.md#emerald-1-emerald-screens-printed-the-carts-english-names-and-descriptions-as-they-were)).

#### 3. The Battle Pyramid's floor names

Fixed in v0.3.53 by gen1recomp#2678: the map name popup reads them through `RomText.refIr(ref)` (`src/ui/game3/map_name_popup.lua:158`, v0.3.63) ([details](upstream-fixes-history.md#emerald-3-the-battle-pyramids-floor-names)).

#### 4. Emerald screens print their own pack's copy of cart text

Fixed in v0.3.53 by gen1recomp#2678: the party menu, the Pokédex search screen, the Battle Frontier screens, the Frontier Pass, the Trainer Hill records and Ever Grande City's fly destinations read the script cache ([details](upstream-fixes-history.md#emerald-4-emerald-screens-printed-their-own-packs-copy-of-cart-text)); the Berry Blender, which it did not reach, is entry 10.

#### 6. The rival's name and the Japanese honorific come from the US cart

Fixed in v0.3.53 by gen1recomp#2678: `{RIVAL}` and `{KUN}` resolve through the script cache by label ([details](upstream-fixes-history.md#emerald-6-the-rivals-name-and-the-japanese-honorific-came-from-the-us-cart)).

## The cross-game Union Room

gen1recomp v0.3.61 adds a Union Room that trainers from every generation share: Red/Blue/Yellow (`src/ui/union/gen1/Text.lua`), Gold/Silver/Crystal from the Pokémon Center (`src/ui/gen2/union/Text.lua`, `src/world/gen2/UnionCenter2F.lua`) and the generation-3 games through their own Union Room (`src/core/game3/link/union_room.lua`, `src/core/game3/rse/union_rs.lua`). No cart has this text, so the mods translate it as reviewed overrides (`overrides/<lang>/{rby,gsc,frlg}/engine.json`, the generation-3 ones shared by Ruby/Sapphire and Emerald), worded with each cart's own Union Room terms (SALLE UNION, KONNEX-KLUB, SALA UNIÓN, SALA CONTATTO, ユニオンルーム) and the official Emerald phrasing where it says the same thing. Checked at v0.3.63.

### Required upstream capability: the validation messages

The messages that explain why a Pokémon cannot cross to the other game (`src/online/xgen/Messages.lua`: "Import {need} first.", "{species} doesn't exist in the other game.", "Its nickname can't be written in the other game."…) are English literals the module formats itself, never through `Strings()` (the file makes no `Strings()` call at v0.3.63), so no mod can translate them. Routing `Messages.text` through `Strings()` with named slots would let the mods ship them like the rest of the Union Room.

## Engine bugs surfaced by TTF mode (not translation gaps)

Three gen1recomp bugs only visible once a mod activates TTF text mode (`mod.content.font:register("ttf", ...)`), none fixable from a mod. All three are merged upstream; the [history file](upstream-fixes-history.md#engine-bugs-surfaced-by-ttf-mode-not-translation-gaps) keeps their root causes.

- The Mod Manager drew white on white: PR #1426.
- A mod's TTF font came out fragmented on Android: PR #1042 (`dpiscale = 1`).
- The Gold naming screen, Diploma and Pokegear drew TTF text invisible through `Chrome.printThrough`'s shader: PR #1736.

## Build tooling bugs in `tools/modkit.py` on Windows (not translation gaps)

Two Windows-only encoding bugs in gen1recomp's own `tools/modkit.py` (vendored, not this project's code), both hit while running or validating a translation mod. The first, a crash when dumped text is not representable in the system codepage, is fixed upstream (PR #996, [details](upstream-fixes-history.md#fixed-upstream-dumped-text-crash-when-it-isnt-representable-in-the-system-codepage)).

### Still open: `modkit pack` fails on a non-ASCII Windows path

Checked at v0.3.63: `run_loader` still embeds absolute paths (`tools/modkit.py:803-805`).

Two independent Windows users reported the GUI's build failing with only
"Command failed with exit code 1" and a `modkit.py ... pack ...` command
line -- no other detail (the GUI not showing the command's own captured
output was a separate, real gap, since fixed). One report's path was
`D:\Jeux\Fan Made Pokémon\Gen1 Recomp\...`; the other reproduced it
directly by picking `Downloads\ééé` as the output directory. Both point at
the same thing: an accented character anywhere in the working tree
`modkit.py pack` runs from.

**Root cause, traced through `tools/modkit.py` (gen1recomp, vendored --
not this project's own code):** `cmd_pack` calls `run_loader(repo, mod_dir,
...)` to validate the mod by actually booting it under LuaJIT headlessly.
`run_loader` builds a map of every mod file to its absolute filesystem path
(`files[f"{mount}/{rel}"] = os.path.join(mod_dir, rel)`), embeds that map as
Lua string literals into a generated driver script
(`entries = "".join("  [%s] = %s,\n" % (lua_quote(k), lua_quote(v)) ...)`),
writes it as a UTF-8 text file (`tempfile.NamedTemporaryFile("w", ...,
encoding="utf-8")`), and runs it with
`subprocess.run([LUAJIT, driver_path], cwd=repo, ...)`. The driver then
`io.open`s those paths to actually load the mod's files.

LuaJIT (like standard Lua) has no concept of source-file text encoding for
string literals: the bytes between the quotes in the driver script --
UTF-8 bytes, since Python wrote the file as UTF-8 -- become the runtime
string's bytes verbatim. On Windows, `io.open` reaches the C runtime's
narrow `fopen()`, which interprets those bytes against the **active ANSI
codepage**, not UTF-8. UTF-8's two-byte encoding of "é" (`0xC3 0xA9`)
decoded as a Windows-1252-family codepage does not round-trip back to "é"
-- the resulting filename does not exist on disk, `io.open` fails, the Lua
driver errors out, LuaJIT exits non-zero, and `run_loader` reports it as
`Finding("MK100", "error", "loader driver crashed: ...")`, which `cmd_pack`
treats as fatal (exit code 1) -- matching both reports exactly.

Not fixable from this project: the failure is inside gen1recomp's own
`tools/modkit.py` driving LuaJIT, not in anything this mod's pipeline
generates or controls. Two realistic upstream fixes, neither requiring a
LuaJIT rebuild:

- Convert `mod_dir`'s absolute path to its Windows short (8.3) name (always
  pure ASCII) via `ctypes.windll.kernel32.GetShortPathNameW` before
  embedding it in the driver script. Requires 8.3 name generation to be
  enabled for the volume, which is the Windows default but can be turned
  off.
- Copy the mod tree into an ASCII-safe temporary directory before invoking
  LuaJIT for this check, sidestepping the encoding mismatch entirely
  regardless of where the real mod directory lives.

This project's own workaround is narrower and does not depend on an
upstream fix: the GUI no longer roots its working directory (where
gen1recomp is cloned and the mod is actually built and packed) inside the
user's chosen *output* directory, since that is the more commonly
non-ASCII one (a deep, descriptively-named project folder picked via a
file browser, as in both reports) -- it now stays anchored near the
executable, matching what the CLI already did by default. That does not
fully close the gap (the executable's own launch location, or a
non-ASCII Windows username, can still trigger this), which is why it is
recorded here rather than closed.
