# Gen1Recomp translation mod generator

[![All Contributors](https://img.shields.io/badge/all_contributors-2-orange.svg?style=flat-square)](#contributors-)

This repository reproducibly generates multilingual `Gen1Recomp` translation
mods without storing a ROM or ROM extract. It currently produces three separate
artifacts per language:

- a universal Pokémon Red, Blue and Yellow mod, with a runtime-selected Yellow
  layer;
- a Pokémon Gold, Silver and Crystal mod for Gen1Recomp's generation-2 runtime;
- a Pokémon FireRed mod for Gen1Recomp's generation-3 (game3) runtime.

The artifacts have distinct mod IDs and filenames, so they can be installed
side by side.

> **AI-assisted development disclosure:** The repository and pipeline were
> developed with AI assistance. Changes are checked through automated tests,
> generated-artifact validation, and code review.

## Quick start

### Recommended: use the graphical application

Download the GUI executable for your platform from the
[latest release](https://github.com/thibautbus/gen1recomp-translation-mod-generator/releases/latest),
then select the target games and the corresponding ROM dumps:

![Gen1Recomp translation mod generator GUI](docs/gui.png)

1. Red, Blue and Yellow, Gold and Silver, or FireRed;
2. your own canonical US ROM dumps for the selected games;
3. the target language and output directory.

The GUI writes a ready-to-import ZIP into the selected directory. It bundles
Python, Pillow and LuaJIT; network access is still required to download the
pinned Gen1Recomp and PokeCorpus inputs.

### Build from source with the CLI

Install Python 3.11+, Git, LuaJIT (`sudo apt install luajit` on
Ubuntu/Debian or `brew install luajit` on macOS), and Pillow
(`python -m pip install Pillow`). The builder checks prerequisites and prints
an installation hint; it never installs software silently. If LuaJIT is not
on `PATH`, set `MODKIT_LUAJIT` to its full executable path (it is a native
executable, not a Python package).

From the repository root:

```sh
python build_translation.py
```

Latin builds default to Fusion Pixel. Use
`python build_translation.py --font-profile pokemon` to select the optional
Pokemon Font profile. Substitute `python3`, `py -3`, or a virtual-environment
interpreter when appropriate.

The builder asks for the target games, canonical US ROM dumps and language. It
verifies the ROM fingerprints, asks before downloading pinned dependencies,
then extracts, translates, validates and packages the selected release in a
private ignored workspace.

The final file is `dist/translation-<lang>-<version>.zip` for RBY,
`dist/translation-<lang>-gen2-<version>.zip` for Gold and Silver, or
`dist/translation-<lang>-gen3-<version>.zip` for FireRed; the command prints its
absolute path.

### Optional local path configuration

Copy [`config/rom_paths.example.toml`](config/rom_paths.example.toml) to the
ignored `config/rom_paths.toml` and edit it:

```toml
[rom]
red = "/absolute/path/to/PokemonRed.gb"
blue = "/absolute/path/to/PokemonBlue.gb"
yellow = "/absolute/path/to/PokemonYellow.gb"
gold = "/absolute/path/to/PokemonGold.gbc"
silver = "/absolute/path/to/PokemonSilver.gbc"
crystal = "/absolute/path/to/PokemonCrystal.gbc"
firered = "/absolute/path/to/PokemonFireRed.gba"
```

The three RBY entries are required for the universal build; `gold`/`silver` are
required only for the Gold and Silver build, and either one alone is enough (the
prompt accepts a Gold or a Silver ROM interchangeably), and `firered` only for the
FireRed build. Relative paths resolve from this file and `~` expands, although
absolute paths are recommended. On Windows, use forward slashes or TOML
single-quoted paths such as `red = 'C:\Games\PokemonRed.gb'`. Configured files
are still checked for existence and SHA-1; declining one returns to the normal
prompt.

## Universal Yellow support

One ZIP per language works on Pokémon Red, Blue and Yellow US. Red/Blue data
lives in the common catalogs; entries whose source or translation differs in
Yellow are emitted into `lang/*_yellow.lua` and applied only when
`GameVersion.isYellow()`. Building it only needs a real Red *or* Blue ROM
(whichever one is supplied) plus a real Yellow ROM: Red and Blue share
byte-identical dialogue text and pointer tables, so either extracts into an
equally correct build.

Shared translations are not duplicated. Missing matches keep the appropriate
ROM English text. The generated coverage report and
`.cache/audit/yellow/<language>.json` retain the full shared/versioned/Yellow-only
breakdown. Yellow-specific manual translations live in
`overrides/<language>/rby/yellow_engine.json`.

## Pokémon Gold, Silver and Crystal support

Gold, Silver and Crystal are published together as `translation-<lang>-gen2`. Gold's
and Silver's own text is built and extracted from either a real Gold or a real
Silver ROM (whichever one is supplied) and covers dialogue, Pokédex entries,
named ROM catalogs and engine strings matched from production Gen 2 callsites.
Crystal is a mandatory companion ROM, the same way Yellow is for the universal
RBY mod: its own dialogue text uses different `bank:address` pointers from
Gold/Silver (95.8% of shared symbol names diverge), so it gets its own corpus
join against poke-corpus's separate `Crystal/` collection and ships as a
`lang/dialogue_crystal.lua` layer, applied only at runtime on an actual Crystal
save. Crystal reuses Gold/Silver's own engine-string catalog and shared named
ROM catalogs (species/moves/items/trainer classes) as-is where the roster is
identical across editions, and ships its own dedicated registries
(`pipeline/gsc/crystal_registries.py`) for the handful of records that are genuinely
Crystal-exclusive (item names, trainer class names and a landmarks subset).
It also carries its own translated engine strings for the 48 keys reachable
only from a Crystal-exclusive feature (Move Tutor, gender selection, the
"PokeSeer"/Buena's Password radio special, Battle Tower); see
[`config/gsc/engine_scope_exclusions.json`](config/gsc/engine_scope_exclusions.json).
Korean has no Crystal corpus in poke-corpus, unlike Gold/Silver -- Crystal's
own dialogue simply stays in English for that language. Missing or ambiguous
matches remain in English. The manifest declares `"gold"`, `"silver"` and
`"crystal"` as supported games, so the same mod loads on any of the three
editions' saves.

Before packaging, headless generation-2 gates verify that the translated
values reach the Gold and Silver registries, and that Crystal's own dialogue
and registries are selected under a Crystal save and never leak onto a Gold
or Silver one. These checks do not replace an in-game smoke test before
release.

## Pokémon FireRed support

FireRed (US, v1.0) is published as `translation-<lang>-gen3` for `fr`, `de`,
`es`, `it` and `ja-Hrkt`, built from a real FireRed ROM. gen1recomp runs it on its own
generation-3 runtime, so the mod uses that runtime's content registries:
dialogue through `mod.content.text`, species, move and item names, item
descriptions, trainer names and class names through their record registries,
the start menu through the public `ui.start_menu.items` hook, and game3's own
text (its own menus, the mod manager, the options it adds) through
`Strings()`. The pipeline is pinned to gen1recomp v0.3.11, whose FireRed
draws most of its text from the cart itself: the option menu,
the summary pages, the intro, the Pokédex, the region map, the battle
messages and the lists all read the cart's rows through `RomText`, so the
mod translates them with the cart's own text instead of a catalog of its
own. That release also names an egg by the language's EGG, reads a
trainer's class by id, takes Unicode braille cells and draws Japanese with
the cart's fonts.

Dialogue is joined differently from the two older releases: the game3
extractor keys each script message by its ROM address, pret's published
`pokefirered.sym` names that address with the same label the PokeCorpus
`FireRedLeafGreen` qid ends with, so every message maps to exactly one corpus
row. The cart text the runtime reads by name joins beside it: a ROM table row
by row (`gTypeNames[1]` against the corpus's `gTypeNames.1`), the battle
string table on its English text (the extractor keys it by pret's
`STRINGID_*`, the corpus by the symbol each row points at), and a row the
cart reaches through a pointer table by the translation every row reading the
same English agrees on. Each translation is shipped as the runtime's own text IR (so the player
name, `STR_VAR` buffers and page breaks survive), encoded through pret's
`charmap.txt`, and only after the corpus English has reproduced the ROM's own
text exactly. The pinned symbol table and charmap are downloaded like the
corpus; they carry addresses and an encoding table, no game text.

Before packaging, `tools/frlg/gate.lua` loads the mod through gen1recomp's
real generation-3 loader over the extracted game3 data and checks that each
catalog lands where the FireRed screens read it. It also measures runtime
limits the mod cannot fix itself, and the build prints them. Accented
letters (fixed in v0.2.67), species and move names reverting to English on
entering the field (v0.2.70), the party naming an egg EGG, the trainer
classes recognised by their English name and the braille lines (all v0.3.4)
no longer apply. What remains is that the help system and the mod manager's
own screens have no way in. Three screens also read their value by its
English text alone — the region map's section names and guide text, the
summary's ability name and the popup's floor suffix — which `modkit pack`
refuses to see in `lang/strings.lua`, so those ~150 entries ship in a
catalog of their own (`lang/strings_by_english.lua`) registered through the
same `strings` registry, until the engine reads them by label. Every one of
these is tracked in the FireRed section of
[docs/upstream-fixes.md](docs/upstream-fixes.md).
Japanese is published too, since the runtime draws kana with the cart's own
Japanese fonts (v0.3.4): its rows keep their characters instead of going
through the cart's byte encoding, whose Japanese block reuses the Latin
block's values.

## Legal inputs and privacy

Use dumps from your own original US cartridges:

| Game | Expected SHA-1 |
| --- | --- |
| Red | `ea9bcae617fdf159b045185467ae58b2e4a48b9a` |
| Blue | `d7037c83e1ae5b39bde3c30787637ba1d4c48ce2` |
| Yellow | `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` |
| Gold | `d8b8a3600a465308c9953dfa04f0081c05bdcb94` |
| Silver | `49b163f7e57702bc939d642a18f591de55d92dae` |
| Crystal | `f4cd194bdee0d04ca4eac29e09b8e4e9d818c133` |
| FireRed | `41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc` |

The pipeline verifies these fingerprints and never downloads, provides or
redistributes ROMs, patches or copyrighted text extracts. Generated data,
worksheets and reports remain under ignored `.cache/` paths and are not
packaged. Keep ROMs and the ignored `config/rom_paths.toml` private.

## Languages and fonts

English is the source language and runtime fallback. Supported targets and
font profiles are:

| Target languages | Releases | Default font | Optional font |
| --- | --- | --- | --- |
| `fr`, `de`, `es`, `it` | RBY, Gold/Silver/Crystal, FireRed | Fusion Pixel Latin, 10px (RBY, GSC); the cart's own font (FireRed) | Pokemon Font, 8px (RBY, GSC) |
| `ja-Hrkt` | RBY, Gold/Silver/Crystal, FireRed | Fusion Pixel Japanese, 8px (RBY, GSC); the cart's own Japanese fonts (FireRed) | — |
| `ko` | Gold/Silver/Crystal only (Crystal's own dialogue stays in English) | Fusion Pixel Hangul, 10px | — |

The optional Pokemon Font is more compact, but translated text can still
overflow fixed-width interfaces.
Macros and interface chrome remain tile-rendered. Each mod packages only the
selected TTF and its applicable license notices.

## Translation coverage

### Red, Blue and Yellow

The ZIP is universal, but ROM coverage is reported separately for Red/Blue
and Yellow:

- `Red Blue ROM aggregate` is the release metric. It combines the six effective ROM
  catalogs (dialogue, species/move/item/trainer names, status labels) with a
  handful of shared runtime entries (types, species kinds, literal handlers,
  demo names and ROM-derived engine templates): `3286` for Red/Blue and
  `3400` for Yellow.
- `RBY-related engine strings` covers engine keys used by original RBY
  gameplay and interfaces.

Engine metrics are informational: unmatched or ambiguous entries keep the
engine's English fallback.

| Target | Red Blue ROM aggregate | Yellow ROM aggregate | RBY-related engine strings |
| --- | ---: | ---: | ---: |
| `fr` | 3286/3286 (100%) | 3400/3400 (100%) | 419/419 (100%) |
| `de` | 3286/3286 (100%) | 3400/3400 (100%) | 419/419 (100%) |
| `es` | 3286/3286 (100%) | 3400/3400 (100%) | 419/419 (100%) |
| `it` | 3286/3286 (100%) | 3400/3400 (100%) | 419/419 (100%) |
| `ja-Hrkt` | 3286/3286 (100%) | 3397/3400 (99.91%) | 419/419 (100%) |

The ROM aggregates exclude extracted labels that do not render visible text.
Reviewed exceptions are recorded in
[`yellow_coverage_exceptions.json`](config/rby/yellow_coverage_exceptions.json).
Full per-key scope, matching strategy and fallback provenance remain available in
the generated coverage report and
[`engine_scope.json`](config/rby/engine_scope.json).

### Gold, Silver and Crystal

Gold and Silver are built as a separate generation-2 artifact, from either ROM. Crystal is
a mandatory companion ROM merged into the same artifact, applied at runtime only on an
actual Crystal save:

- `Gold and Silver ROM aggregate` combines dialogue, Pokédex entries and the named ROM
  catalogs. Its denominator excludes 14 markup-only records with no visible
  prose. The named catalogs include each trainer's own name (JOEY is GASPARD
  in French), joined per class and member number against the corpus: 495
  Gold/Silver trainers and 541 Crystal ones, applied on their own edition's
  save since Crystal's rosters differ. The phone contact registry leaves its
  29 trainer contacts to those trainer names, so they count as covered once
  every trainer name is; the 25 species-backed decorations (CLEFAIRY POSTER)
  are patched with the translated species name. `ja-Hrkt` and `ko` carts
  fit each #DEX description on one page, so their second page is shipped
  blank rather than left to the English ROM's own. `ko` falls short of 100%
  because poke-corpus has no Korean Crystal collection (Crystal's own #DEX
  text and trainer names).
- `Gold and Silver-related engine strings` covers the 940 engine keys used by
  at least one production Gen 2 callsite. 48 keys reachable only from a
  Crystal-exclusive feature (Move Tutor, gender selection, the "PokeSeer"/
  Buena's Password radio special, Battle Tower) are excluded from this
  specific scope, since none of it exists on a real Gold or Silver cart -- but
  they are translated and shipped, tracked separately under Crystal's own
  `engine_crystal` metric below; see
  [`config/gsc/engine_scope_exclusions.json`](config/gsc/engine_scope_exclusions.json).
  A key whose English spelling was reviewed as this language's own (`PP`,
  `♂`, a badge or palette name the cart spells identically) counts as
  translated, like an identical corpus match does; only
  [`config/gsc/engine_fallbacks.json`](config/gsc/engine_fallbacks.json) rows
  still recorded as having no corpus match are gaps (none today; the
  Pokédex entry bar the Japanese and Korean carts draw as tiles is laid out
  to the pixel in each language's bundled font so every word sits between
  the bar's arrows).
- `Crystal dialogue coverage` is Crystal's own dialogue pointers, joined
  separately against poke-corpus's own `Crystal/` collection (different
  `bank:address` values from Gold/Silver almost throughout, so this is not
  the same catalog as the aggregate above). Its denominator excludes 16
  markup-only records, same convention as the ROM aggregate. Crystal's own
  named catalogs (the Crystal-exclusive item names, trainer class names and
  landmarks subset) and its 48 Crystal-exclusive engine strings are also
  translated (fr/de/es/it: 48/48 engine strings and 6/6 named registries;
  ja-Hrkt: 47/48 and 6/6; `ko` has no Crystal corpus, but both its 47/48
  engine strings and all 6/6 named registries -- the Crystal-exclusive
  item/trainer-class/landmark names -- are hand-composed anyway. The
  missing 48th engine string, "???", is a genuine English-identical
  no-op).
  The shared `Gold and Silver-related engine strings` catalog and the shared
  named ROM catalogs (species/moves/items/trainer classes) also apply
  unchanged on a Crystal save, same shared code, no separate work needed
  there. A dedicated release gate verifies all of this is selected only under
  a Crystal save and never leaks onto Gold or Silver. `ko` has no Crystal
  corpus at all in poke-corpus, so its dialogue stays in English.

The generated report retains the dialogue/catalog breakdown and per-key
provenance. Future unresolved entries will keep their original English text.

| Target | Gold and Silver ROM aggregate | Gold and Silver-related engine strings | Crystal dialogue coverage |
| --- | ---: | ---: | ---: |
| `fr` | 6839/6839 (100%) | 951/951 (100%) | 3994/3994 (100%) |
| `de` | 6839/6839 (100%) | 951/951 (100%) | 3994/3994 (100%) |
| `es` | 6839/6839 (100%) | 951/951 (100%) | 3994/3994 (100%) |
| `it` | 6839/6839 (100%) | 951/951 (100%) | 3994/3994 (100%) |
| `ja-Hrkt` | 6839/6839 (100%) | 951/951 (100%) | 3994/3994 (100%) |
| `ko` | 5796/6839 (84.75%) | 951/951 (100%) | 0/3994 (0%) |

### FireRed

- `FireRed ROM aggregate` combines every cart text the runtime reads -- the
  script messages and the 5,475 rows it now reads by name (menus, battle
  messages, lists, the Pokédex, the region map, the intro) -- with the named
  catalogs (species, move and item names, item descriptions, trainer names
  and class names, start menu labels). The 39 braille lines ship as each
  cart's own braille cells, and so do the eight trainer classes the runtime
  used to recognise by name (RIVAL, LEADER, ELITE FOUR, CHAMPION). The
  `POKéBLOCK CASE` item's name and description are left out of the
  aggregate: the item cannot be obtained in FireRed, and the extractor loses
  its name even in English. Three kinds of row are left out the same way:
  157 that carry no text at all (a lone control code, an empty string), 21
  the extractor cannot read (the battle HUD's status strings and the Union
  Room's activity list are drawn from tiles, not from charmap bytes) and 61
  whose only corpus line is Japanese -- the Ruby/Sapphire leftovers the US
  cart still carries, and the Japanese status strings it keeps for a
  comparison. What is left unshipped is five fragments the cart
  concatenates between two buffers (`'s level rose to`, ` was used on`): the
  European carts reword the whole sentence and the engine cannot reorder it.
  German, Spanish and Italian each leave a seventh row for the same reason.
  Japanese leaves 85: 30 whose phrasing names the player where the English
  does not, and 50 lines the collection has no Japanese text for at all.
- `FireRed engine strings` covers the 1,890 `Strings()` keys the game3
  runtime reaches on its own: its menus and prompts, the ability names, the
  move and ability descriptions, the map section names and the region map's
  guide text, and the 1,028 Easy Chat words and group names (the species and
  move groups come from the species and move names). They are listed with
  their callsites and cart rows in
  [`config/frlg/engine_scope.json`](config/frlg/engine_scope.json), which
  `pipeline/frlg/engine_scope.py` regenerates from the pinned engine; a test
  derives the same set from it, so a new key cannot slip out of the metric.
  A key whose text is a cart string is shipped under that string's ROM
  label, which is how the runtime looks it up.

| Target | FireRed ROM aggregate | FireRed engine strings |
| --- | ---: | ---: |
| `fr` | 10921/10929 (99.93%) | 1890/1890 (100%) |
| `de` | 10919/10929 (99.91%) | 1890/1890 (100%) |
| `es` | 10919/10929 (99.91%) | 1890/1890 (100%) |
| `it` | 10919/10929 (99.91%) | 1890/1890 (100%) |
| `ja-Hrkt` | 10844/10929 (99.22%) | 1890/1890 (100%) |

These measure what the mod ships, not what the current runtime displays; see
"Pokémon FireRed support" above for the runtime limits.

### Other engine strings

The remaining engine keys are reported separately below. They are keys used by
neither RBY nor Gold and Silver, so their denominator is the residual scope:
`2467 - (419 + 951 - 86) = 1183`. The numerator counts keys translated in at
least one of the RBY and Gold/Silver/Crystal artifacts, the RBY release's
Yellow layer included; this is a project-level metric, not a claim that
every key is present in both games.
The FireRed-reachable keys are measured separately above ("FireRed engine
strings"), so this residual scope and its numerators leave the FireRed
artifact out.

| Target | Other engine strings |
| --- | ---: |
| `fr` | 118/1183 (9.97%) |
| `de` | 118/1183 (9.97%) |
| `es` | 116/1183 (9.81%) |
| `it` | 117/1183 (9.89%) |
| `ja-Hrkt` | 116/1183 (9.81%) |
| `ko` | 55/1183 (4.65%) |

The denominator is calculated as follows: `2467` total engine keys, minus the
`419` RBY-related keys and the `951` Gold and Silver-related keys, plus back the `86` keys
shared by both scopes so they are subtracted only once. The resulting residual
scope is `1183` keys. Both figures fell when FireRed
stopped passing the cart's own text through `Strings()`: those keys are
measured in the FireRed tables above instead.

These values use the pinned ROMs, corpus snapshots and Gen1Recomp revision
`09a3df2b` (v0.3.11); regenerate them whenever one of those inputs changes.

## Translation provenance

Every translated engine string remains traceable:

| Origin | Meaning | Recorded in |
| --- | --- | --- |
| Automatic match | Exact, normalized, or structural match proved by the generator. | Generation report |
| Deterministic anchor | Reliable PokeCorpus qid, composition, or extraction rule. | `config/rby/semantic_anchors.json`, `config/gsc/semantic_anchors.json`, `config/gsc/crystal_semantic_anchors.json` |
| Human-reviewed RBY anchor | Contextual or language-specific extraction reviewed by a maintainer; text still comes from PokeCorpus. | `config/rby/semantic_anchor_decisions.json` |
| Human-reviewed Gold pointer | Ambiguous ROM pointer resolved to a reviewed PokeCorpus qid. | `config/gsc/pointer_decisions.json` |
| Human-reviewed Crystal pointer | Ambiguous Crystal ROM pointer resolved to a reviewed PokeCorpus qid. | `config/gsc/crystal_pointer_decisions.json` |
| Exact FireRed dialogue join | ROM address -> pret symbol -> PokeCorpus qid label, English verified against the ROM text. | Generation report |
| Reviewed FireRed dialogue decision | A standard-script line gen1recomp reworded itself, joined to the cart's row carrying the same message. | `config/frlg/dialogue_decisions.json` |
| Reviewed FireRed engine anchor | The cart's own row for an original FireRed menu string. | `config/frlg/engine_scope.json` |
| Reviewed Crystal engine selector | Crystal corpus row whose list boundaries or placeholder count don't fit the shared anchor grammar, resolved to a specific qid/segment. | `config/gsc/crystal_string_selectors.json` |
| Reviewed placeholder exception | Official localized wording legitimately adds or omits a runtime value such as the player name or an item quantity. This records no translated text and does not disable the audit; each exception is scoped to a language, ROM pointer, corpus QID, and exact audit message. | `config/gsc/placeholder_decisions.json` |
| Manual corpus correction | A maintainer corrects one selected-language corpus translation without changing the upstream corpus. Entries are indexed by qid. | `overrides/<language>/rby/corpus.json` |
| Manual translation — engine contract gap | PokeCorpus has the text, but Gen1Recomp merges contexts or hides required parameters. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `overrides/<language>/frlg/dialogue.json`, `reason: "engine-contract-gap"` |
| Manual translation — corpus wording restored by reordering | The cart words a message in another order than the engine passes its values, which a numbered directive (`%2$s`) now expresses. The text is the cart's own, apart from an addition the provenance discloses (an adverb a language's cart drops, for instance); only the order is the translation's. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `reason: "engine-corpus-reordered"` |
| Manual translation — corpus wording in the engine's own order | The same restoration for a language whose cart already orders the values the way the engine passes them, so no directive is numbered. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `reason: "engine-corpus-cart-order"` |
| Manual translation — engine original | Engine-specific text with no compatible ROM source. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `reason: "engine-original"` |
| Manual translation — Yellow-only engine text | Engine-authored, Yellow-exclusive text (Surfing Pikachu minigame HUD) with no PokeCorpus source; applied only when `GameVersion.isYellow()`. | `overrides/<language>/rby/yellow_engine.json`, `reason: "yellow-only-engine-text"` |
| Known limitation | Active anchor/override knowingly imperfect in a context or language; a status, not an origin. | Anchor metadata or override provenance |
| English fallback | No sufficiently reliable translation; runtime keeps English. | Generation report |

Generated coverage reports are the authoritative inventory of unmatched and
ambiguous strings. Every manual override must explain its source and accepted
limitations; otherwise the English fallback is preferred.

## Windows, Linux and macOS standalone executables

The GitHub Actions workflow builds CLI and graphical Tkinter executables for
Windows x64, Linux x86_64, and macOS Intel/Apple Silicon:

- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-windows-x64.exe`
- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-linux-x86_64.tar.gz`
- `gen1recomp-translation-mod-generator-<version>-cli-macos-<x86_64|arm64>.tar.gz`
- `gen1recomp-translation-mod-generator-<version>-gui-macos-<x86_64|arm64>.zip`

Windows users can run the downloaded EXE directly. Linux builds target Ubuntu
22.04 (glibc) and compatible newer systems; extract the selected archive and
run its binary:

```sh
tar -xzf gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64.tar.gz
chmod +x gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
./gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
```

On macOS, select `arm64` for Apple Silicon or `x86_64` for Intel. Extract the
GUI ZIP and double-click `gen1recomp-translation-mod-generator-gui.app` in
Finder. The CLI archive still runs from Terminal:

```sh
tar -xzf gen1recomp-translation-mod-generator-<version>-cli-macos-arm64.tar.gz
./gen1recomp-translation-mod-generator-<version>-cli-macos-arm64
```

The macOS builds are not notarized. If macOS blocks the app because its
developer cannot be verified, first confirm that the archive
came from this project's GitHub release. Then try to open the app once, go
to **System Settings → Privacy & Security**, and select **Open Anyway**. See
[Apple's instructions](https://support.apple.com/en-gb/102445). Do not bypass
a warning that the binary is damaged or will harm your computer.

Standalone builds verify their pinned downloads and never bundle or upload
ROMs. The CLI stores its cache in the current directory; the GUI uses the
selected output directory. Keep ROMs and `config/rom_paths.toml` outside the
application bundle.

## Maintainer reference

### Data flow and matching

```text
resolve release -> verify ROMs -> prepare pinned inputs -> extract and match
-> generate -> validate -> inspect and publish archive
```

Text resolution is deterministic:

```text
explicit override > semantic anchor > exact > normalized
> structural placeholder match > empty entry (runtime English fallback)
```

Game-specific configuration lives under `config/rby/`, `config/gsc/` and `config/frlg/`;
language overrides follow the same split under `overrides/<language>/`.

| Configuration | Purpose |
| --- | --- |
| `config/rby/engine_scope.json` | RBY coverage classification for engine strings. |
| `config/rby/terminology_anchors.json` | Evidence for corpus terminology used by RBY. |
| `config/rby/literal_handlers.json` | Documented RBY extraction gaps. |
| `config/rby/semantic_anchor_decisions.json` | Human-reviewed corrections to RBY semantic-anchor picks. |
| `config/rby/yellow_coverage_exceptions.json` | Reviewed exceptions to the Yellow ROM aggregate's markup-only exclusions. |
| `config/shared/engine_manifest.json` | Pinned engine revision and complete string universe shared by the releases. |
| `config/gsc/pointer_decisions.json` | Human-reviewed picks for ambiguous Gold dialogue pointers. |
| `config/gsc/placeholder_decisions.json` | Reviewed placeholder exceptions for Gold dialogue pointers. |
| `config/gsc/silver_pointer_aliases.json` | The 8 Gold-pointer-to-Silver-pointer aliases needed because a handful of field-move prompts shift address between editions. |
| `config/gsc/semantic_anchors.json` | Evidence for Gold/Silver engine-string corpus matches. |
| `config/gsc/engine_fallbacks.json` | Audited ledger of Gold/Silver engine keys deliberately left in English. |
| `config/gsc/engine_scope_exclusions.json` | Crystal-exclusive engine keys excluded from the Gold/Silver engine-string coverage metric (translated separately; see "Gold, Silver and Crystal support" above). |
| `config/gsc/literal_handlers.json` | Reviewed corpus picks for the Gold menu screens exposed through public list hooks (`ui.pc.items` and similar). |
| `config/gsc/engine_launch_batch.json` | The frozen ~551-key batch the original Gold/Silver engine-string work added, kept for exhaustive coverage auditing as the catalog keeps growing. |
| `config/gsc/status_anchors.json` | Evidence for the Gold/Silver status-label registry (`mod.content.statuses`). |
| `config/gsc/type_search_indices.json` | Gen 2 type ids mapped to the Pokédex type-search corpus row. |
| `config/gsc/crystal_pointer_decisions.json` | Human-reviewed picks for ambiguous Crystal dialogue pointers. |
| `config/gsc/crystal_rom_text_anchors.json` | Crystal-only RomText labels mapped to their PokeCorpus rows -- a labeled fallback path alongside Crystal's own pointer-based dialogue join. |
| `config/gsc/crystal_semantic_anchors.json` | Evidence for Crystal engine-string corpus matches. |
| `config/frlg/dialogue_decisions.json` | Reviewed corpus rows for FireRed standard-script lines gen1recomp reworded. |
| `config/frlg/engine_scope.json` | FireRed-reachable `Strings()` keys, their callsites and reviewed cart rows. |
| `config/gsc/crystal_string_selectors.json` | Reviewed qid/segment picks for Crystal corpus rows whose list boundaries or placeholder count don't fit the shared semantic-anchor grammar. |

The semantic anchors and reviewed decisions are described in the
`Translation provenance` section above.
Gold and Silver identify their production strings directly from Gen 2 source subtrees.
Missing or ambiguous evidence always falls back to English. Private review
candidates never become executable configuration automatically.
`strict_engine` requires the engine catalog and scaffold to be present, not
fully translated.

### Module map

The pipeline is split like `config/`: one package per game, and
`pipeline/shared/` for what several games use. `tools/` and `tests/` follow
the same split.

| Package | Modules | Responsibility |
| --- | --- | --- |
| `pipeline/shared/` | `cli.py`, `builder.py`, `gui.py`, `orchestration.py`, `specs.py` | Resolve release requests and dispatch the command, interactive and GUI flows. |
| `pipeline/shared/` | `project.py`, `dependencies.py`, `rom_paths.py`, `roms.py`, `subprocess_run.py`, `engine_profile.py` | Resolve paths, verify private ROMs and prepare pinned dependencies. |
| `pipeline/shared/` | `corpus.py`, `model.py`, `align.py`, `worksheet.py`, `tokens.py`, `generate.py` | Parse parallel corpora, align qids, preserve control-token contracts and write Lua. |
| `pipeline/shared/` | `engine_manifest.py`, `strings_harvest.py`, `engine.py` | The pinned engine string universe, the `Strings()`/RomText callsite harvester, and the engine-string join Red/Blue and Gold share. |
| `pipeline/shared/` | `mod_assets.py`, `validate.py`, `leak_audit.py` | What every mod ships besides its catalogs (fonts, load priority), and the release gates. |
| `pipeline/rby/` | `build.py`, `join.py`, `mod.py`, `yellow.py`, `yellow_audit.py`, `literals.py` | Build the Red/Blue and Yellow mod: join the catalogs, resolve literal handlers, write the mod and its Yellow layer. |
| `pipeline/rby/` | `engine_scope.py`, `engine_backlog.py`, `disassembly_audit.py` | Classify the engine strings for Red/Blue, report the private backlog and audit the localized disassemblies. |
| `pipeline/gsc/` | `text.py`, `join.py`, `index_join.py`, `localized_registries.py`, `trainer_names.py`, `engine.py`, `mod.py` | Join GoldSilver to pointer/index catalogs and engine strings, and emit the Gen 2 artifact. |
| `pipeline/gsc/` | `crystal_mod.py`, `crystal_registries.py`, `crystal_strings.py` | The Crystal layer of the Gen 2 artifact. |
| `pipeline/frlg/` | `text.py`, `join.py`, `engine_scope.py`, `audit.py`, `mod.py` | pret charmap/symbols, the game3 text IR, the address-to-label and engine joins, the hardcoded-text audit and the Gen 3 artifact. |
| `tools/gsc/`, `tools/frlg/` | `extract.lua`, `gate*.lua`, `measure_*.py` | ROM extractors and release gates run under LuaJIT, and the Gold join measurements. |

`build_translation.py` is the normal entry point. Intermediate and audit files
stay under `.cache/`.

### Audit commands

Disassembly audit:

```sh
python scripts/pipeline.py audit-disassemblies
```

This writes private comparison reports under `.cache/audit/`. Never publish
them: they can contain copyrighted text and local paths.

Engine backlog:

```sh
python scripts/pipeline.py engine-backlog --language fr
```

This records unresolved keys, callsites, fallback reasons and qid candidates
without modifying anchors, overrides or catalogs. It requires a matching
cached coverage/catalog snapshot.

For all languages (default `fr,de,es,it,ja-Hrkt`):

```sh
python scripts/pipeline.py engine-backlog-matrix
```

This produces the cross-language backlog matrix under
`.cache/audit/engine-backlog/`.

### Release builds

Build standalone artifacts locally with:

```powershell
./packaging/build_windows_executable.ps1
```

```sh
./packaging/build_linux_executable.sh
./packaging/build_macos_executable.sh
```

Tag pushes matching `v<version>` validate the version and publish all eight
CLI/GUI artifacts. `workflow_dispatch` builds them without publishing. The
workflow compiles pinned LuaJIT, validates both front ends and inspects each
archive before upload.

### Remaining limitations

- Automated gates validate data loading and packaging, not rendering; releases
  still need in-game smoke tests.
- Some engine strings remain in English, as shown by the coverage tables.
- Translated text can exceed fixed UI widths with either font profile.
- The `X ATTACK`/`X DEFENSE`/etc. battle-item and the vitamin (`PROTEIN`,
  `IRON`, `CALCIUM`, `ZINC`, `CARBOS`, `HP UP`) stat-rose messages always
  show the raised stat's name in English: Gen1Recomp substitutes it with a
  raw uppercase value (`stat:upper()`), not through the translated engine
  string catalog. This is different from the SummaryMenu's own stat labels,
  which are fully translated.
- RBY type names are replaced at draw time by exact string match, so a nickname
  identical to an English type name is translated too.
- The desktop launcher uses a separate renderer and is outside the content
  mod's translation hooks.
- FireRed's Pokédex descriptions, help system and quest log stay in English,
  and the party names an egg EGG, until the upstream fixes listed in the
  FireRed section of the document below land.
- RBY-, Gold and Silver- and FireRed-specific upstream engine gaps are tracked in
  [docs/upstream-fixes.md](docs/upstream-fixes.md).

## Credits

- [Gen1Recomp](https://github.com/bryanthaboi/gen1recomp) by [bryanthaboi](https://github.com/bryanthaboi), the native Lua / LÖVE2D recreation.
- [PokéCorpus](https://github.com/abcboy101/poke-corpus) by [abcboy101](https://github.com/abcboy101), the multilingual translation corpus.
- [pokemon-font](https://github.com/cooljeanius/pokemon-font) v1.8.2, the Pokemon Font clone by Superpencil, sourced from the fork maintained by [cooljeanius](https://github.com/cooljeanius), available as the optional Latin profile.
- [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font) by [TakWolf](https://github.com/TakWolf), used by the recommended Latin profile, the Japanese profile, and the Korean profile (Gold and Silver only).

## Contributors ✨

Thanks go to these wonderful people:

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<table>
  <tr>
    <td align="center" valign="top" width="14.28%"><a href="https://github.com/thibautbus"><img src="https://avatars.githubusercontent.com/thibautbus?s=100" width="100px;" alt="thibautbus"/><br /><sub><b>thibautbus</b></sub></a><br /><a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Code">💻</a> <a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Documentation">📖</a> <a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Maintenance">🚧</a></td>
    <td align="center" valign="top" width="14.28%"><a href="https://github.com/antoniman31"><img src="https://avatars.githubusercontent.com/u/268696974?s=100" width="100px;" alt="AntoniMan31"/><br /><sub><b>AntoniMan31</b></sub></a><br /><a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/pull/10" title="Bug fixes">🐛</a> <a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=antoniman31" title="Code">💻</a></td>
  </tr>
</table>

<!-- ALL-CONTRIBUTORS-LIST:END -->
