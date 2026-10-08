# Translation coverage metrics

This document defines the coverage figures the [README](../README.md)
publishes for each mod, and breaks down what each one leaves in English.
The figures measure what a mod ships, not what the current runtime
displays: text gen1recomp keeps out of a mod's reach is tracked in
[docs/upstream-fixes.md](upstream-fixes.md). Every build writes a coverage
report with the full per-key scope, matching strategy and fallback
provenance; it stays under the ignored `.cache/` tree. All figures use
Gen1Recomp revision `7ab2f865` (v0.3.63) and the pinned ROMs and corpus
snapshots; regenerate them whenever one of those inputs changes. Engine
metrics are informational: unmatched or ambiguous entries keep the
engine's English fallback.

## Red, Blue and Yellow

The ZIP is universal, but ROM coverage is reported separately for Red/Blue
and Yellow. Red/Blue data lives in the common catalogs; entries whose
source or translation differs in Yellow are emitted into
`lang/*_yellow.lua` and applied only when `GameVersion.isYellow()`. Shared
translations are not duplicated, and missing matches keep the appropriate
ROM English text. The coverage report and
`.cache/audit/yellow/<language>.json` retain the full
shared/versioned/Yellow-only breakdown. Yellow-specific manual translations
live in `overrides/<language>/rby/yellow_engine.json`.

- `Red Blue ROM aggregate` is the release metric. It combines the six
  effective ROM catalogs (dialogue, species/move/item/trainer names, status
  labels) with a handful of shared runtime entries (types, species kinds,
  literal handlers, demo names and ROM-derived engine templates): `3286`
  for Red/Blue and `3400` for Yellow.
- `RBY-related engine strings` covers engine keys used by original RBY
  gameplay and interfaces.

The ROM aggregates exclude extracted labels that do not render visible
text. Reviewed exceptions are recorded in
[`yellow_coverage_exceptions.json`](../config/rby/yellow_coverage_exceptions.json);
the engine scope is [`engine_scope.json`](../config/rby/engine_scope.json).

## Gold, Silver and Crystal

Crystal's own dialogue text uses different `bank:address` pointers from
Gold and Silver (95.8% of shared symbol names diverge), so it gets its own
corpus join against PokeCorpus's separate `Crystal/` collection and ships
as a `lang/dialogue_crystal.lua` layer, applied only at runtime on an
actual Crystal save. Crystal reuses Gold and Silver's engine-string catalog
and shared named ROM catalogs (species, moves, items, trainer classes)
as-is where the roster is identical across editions, and ships its own
registries (`pipeline/gsc/crystal_registries.py`) for the records that are
Crystal-exclusive (item names, trainer class names and a landmarks
subset). Before packaging, headless generation-2 gates verify that the
translated values reach the Gold and Silver registries, and that Crystal's
own dialogue and registries are selected under a Crystal save and never
leak onto a Gold or Silver one.

- `Gold and Silver ROM aggregate` combines dialogue, Pokédex entries and
  the named ROM catalogs. Its denominator excludes 14 markup-only records
  with no visible prose. The named catalogs include each trainer's own name
  (JOEY is GASPARD in French), joined per class and member number against
  the corpus: 495 Gold/Silver trainers and 541 Crystal ones, applied on
  their own edition's save since Crystal's rosters differ. The phone
  contact registry leaves its 29 trainer contacts to those trainer names,
  so they count as covered once every trainer name is; the 25
  species-backed decorations (CLEFAIRY POSTER) are patched with the
  translated species name. `ja-Hrkt` and `ko` carts fit each #DEX
  description on one page, so their second page is shipped blank rather
  than left to the English ROM's own. `ko` falls short of 100% because
  PokeCorpus has no Korean Crystal collection (Crystal's own #DEX text and
  trainer names).
- `Gold and Silver-related engine strings` covers the 993 engine keys used
  by at least one production Gen 2 callsite. 39 keys reachable only from a
  Crystal-exclusive feature (Move Tutor, gender selection, the PokeSeer,
  Buena's prize exchange, Battle Tower) are excluded from this scope, since
  none of it exists on a real Gold or Silver cart; they are translated and
  shipped all the same, and reported as `engine_crystal` in the Crystal
  section of the generated coverage report; see
  [`config/gsc/engine_scope_exclusions.json`](../config/gsc/engine_scope_exclusions.json).
  A key whose English spelling was reviewed as this language's own (`PP`,
  `♂`, a badge or palette name the cart spells identically) counts as
  translated, like an identical corpus match does; only
  [`config/gsc/engine_fallbacks.json`](../config/gsc/engine_fallbacks.json)
  rows still recorded as having no corpus match are gaps (none today; the
  Pokédex entry bar the Japanese and Korean carts draw as tiles is laid out
  to the pixel in each language's bundled font so every word sits between
  the bar's arrows).
- `Crystal dialogue coverage` is Crystal's own dialogue pointers, joined
  against the `Crystal/` collection, so this is not the same catalog as the
  aggregate above. Its denominator excludes 16 markup-only records, same
  convention as the ROM aggregate. Crystal's own named catalogs and its 39
  Crystal-exclusive engine strings are also translated (fr/de/es/it/ja-Hrkt:
  39/39 engine strings and 6/6 named registries). Korean has no Crystal
  corpus, so its Crystal dialogue stays in English, but its 6/6 named
  registries and 38/39 engine strings are hand-composed; the 39th, "???",
  is an English-identical no-op.

## FireRed and LeafGreen

- `FireRed ROM aggregate` combines every cart text the runtime reads (the
  script messages and the 5,475 rows it reads by name: menus, battle
  messages, lists, the Pokédex, the region map, the intro) with the named
  catalogs (species, move and item names, item descriptions, trainer names
  and class names, start menu labels). The 39 braille lines ship as each
  cart's own braille cells, and so do the eight trainer classes the runtime
  used to recognise by name (RIVAL, LEADER, ELITE FOUR, CHAMPION). The
  `POKéBLOCK CASE` item's name and description are left out of the
  aggregate: the item cannot be obtained in FireRed, and the extractor
  loses its name even in English. Three kinds of row are left out the same
  way: 157 that carry no text at all (a lone control code, an empty string;
  155 in Japanese, which ships the honorifics the US cart leaves empty), 21
  the extractor cannot read (the battle HUD's status strings and the Union
  Room's activity list are drawn from tiles, not from charmap bytes) and 61
  whose only corpus line is Japanese (the Ruby/Sapphire leftovers the US
  cart still carries, and the Japanese status strings it keeps for a
  comparison).
- What is left unshipped, in every European language, is five fragments
  the cart concatenates around a buffer (`'s level rose to`, ` was used
  on`, ` BERRY`...), whose European rows reword the whole sentence, which
  the engine cannot reorder, and three plural suffixes (`S`, `IES`) the
  collection has no row for. German and Spanish also leave the Spikes
  message (two rows), whose rows name the target side with a value the
  runtime does not pass, and Italian one item prompt whose row prints a
  buffer the English line does not. Japanese leaves 63: 8 whose phrasing
  prints a buffer the English line does not, 50 lines the collection has
  no Japanese text for at all, and 5 written with a token the join does not
  encode. Rows that only add the player's name or the honorific after it
  ship: the runtime always fills the name, and has printed the honorific in
  FireRed since v0.3.52 (FireRed entry 18 of
  [docs/upstream-fixes.md](upstream-fixes.md)).
- `LeafGreen ROM aggregate` measures the same way over LeafGreen's text,
  keyed through `pokeleafgreen.sym`. It lands on the same figures in every
  language: the two carts differ in where their script text sits and in
  the naming screen's choices, not in what can be translated. The README
  shows the two as one column.
- `FireRed engine strings` covers the 1,946 `Strings()` keys the game3
  runtime reaches on its own: its menus and prompts, the ability names, the
  move and ability descriptions, the map section names and the region
  map's guide text, and the 1,028 Easy Chat words and group names (the
  species and move groups come from the species and move names). They are
  listed with their callsites and cart rows in
  [`config/frlg/engine_scope.json`](../config/frlg/engine_scope.json) (see
  [docs/gen3-pipeline.md](gen3-pipeline.md) to regenerate it). The twelve
  left in English, in every language, are rows gen1recomp v0.3.47 added
  without a cart row (Emerald's event tickets among them), listed in the
  FireRed section of [docs/upstream-fixes.md](upstream-fixes.md).

## Ruby and Sapphire

- `Ruby/Sapphire ROM aggregate` measures the cart the build reads the same
  way as FireRed's: its script messages and the 9,158 rows the runtime
  reads by label or through a pointer table, with the named catalogs
  (species, move and item names, item descriptions, trainer names and class
  names). It leaves out 88 rows that carry no text (86 in Japanese, which
  ships the honorifics くん and ちゃん the US cart leaves empty), 22 the
  extractor cannot read (the Sealed Chamber's and the ancient tombs'
  braille) and 10 whose only corpus line is Japanese. Built from Ruby 1.2
  or from Sapphire 1.0, it lands on the same figures, and so does each of
  the four script layouts the mod carries (`ruby_1_0`, `ruby_1_1`,
  `sapphire_1_0`, `sapphire_1_1`), measured on the lines keyed from the
  cart that was read; the other edition's own scenes are placed on top from
  their corpus rows.
- What is left unshipped, in every European language, is the lines the
  collection has no row for under their label or with their English (82 in
  French, 98 in German, 50 in Spanish, 85 in Italian), the 13 credits names
  it leaves blank, the 4 fragments whose European rows print a value the
  runtime does not fill, the rows written with an escape or a character the
  cart cannot encode (1, 3 in German) and one trainer name (GWEN, whose
  corpus English ends in a space). In Japanese, it is 48 lines with no
  Japanese text, 47 with no row, 29 whose placeholders differ from the
  English, 7 written with a token the join does not encode, and the same
  trainer name.
- `Ruby/Sapphire engine strings` covers the 2,552 `Strings()` keys the
  game3 runtime reaches for Ruby and Sapphire, joined to their own cart
  rows, with Emerald's value for the screens their carts never had. Six
  stay in English: the same rows Emerald leaves, minus `SPECIAL AREA` (the
  controls screen's three rows, the bag's `CHECK_TAG` and `OPEN`, and the
  Easy Chat word `{POKEBLOCK}`).

## Emerald

- `Emerald ROM aggregate` measures the same way as FireRed's: the script
  messages and the 10,010 rows the runtime reads by label or through a
  pointer table, with the named catalogs (species, move and item names,
  item descriptions, trainer names and class names). It leaves out 162
  rows that carry no text (160 in Japanese, which ships the honorifics
  くん/ちゃん the US cart leaves empty) and 49 whose only corpus line is
  Japanese. What is left unshipped is the fragments the cart concatenates
  around a buffer, whose European rows reword the whole sentence or print a
  value the runtime does not fill (5 in French and Italian, 7 in Spanish,
  11 in German), and, in Japanese, 104 lines the collection has no Japanese
  text for, 36 whose placeholders differ from the English, three written
  with a token or escape the join does not encode (`[DAKUTEN]`, `\e`) and
  one with no row.
- `Emerald engine strings` covers the 2,685 `Strings()` keys the game3
  runtime reaches for Emerald: its menus and options, the move and ability
  descriptions, the 1,030 Easy Chat words and group names, and the ability
  names, Pokédex entries, contest texts and map section names the Emerald
  screens look up through `Strings()`. Seven engine rows have neither an
  Emerald cart row nor a reviewed override and stay in English (Emerald
  section of [docs/upstream-fixes.md](upstream-fixes.md)).

## Other engine strings

The remaining engine keys are keys used by neither RBY nor Gold and Silver,
so their denominator is the residual scope: `2635` total engine keys, minus
the `425` RBY-related keys and the `993` Gold and Silver-related keys, plus
back the `90` keys shared by both scopes so they are subtracted only once:
`2635 - (425 + 993 - 90) = 1307`. The numerator counts keys translated in
at least one of the RBY and Gold/Silver/Crystal artifacts, the RBY
release's Yellow layer included; this is a project-level metric, not a
claim that every key is present in both games. The FireRed-, Ruby/Sapphire-
and Emerald-reachable keys are measured separately with their own
releases, so this residual scope and its numerators leave the
generation-3 artifacts out.
