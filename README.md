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
(`crystal_registries.py`) for the handful of records that are genuinely
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
`es` and `it`, built from a real FireRed ROM. gen1recomp runs it on its own
generation-3 runtime, so the mod uses that runtime's content registries:
dialogue through `mod.content.text`, species, move and item names, item
descriptions, trainer names and class names through their record registries,
the start menu through the public `ui.start_menu.items` hook, and game3's own
text (battle messages, menus, Pokédex labels, Oak's speech, options, place
names) through `Strings()`. That last part needs gen1recomp's
`fix/game3-translatable-strings` branch, not yet merged upstream: the
pipeline is pinned to it on the `thibautbus/gen1recomp` fork until it is.

Dialogue is joined differently from the two older releases: the game3
extractor keys each message by its ROM address, pret's published
`pokefirered.sym` names that address with the same label the PokeCorpus
`FireRedLeafGreen` qid ends with, so every message maps to exactly one corpus
row. Each translation is shipped as the runtime's own text IR (so the player
name, `STR_VAR` buffers and page breaks survive), encoded through pret's
`charmap.txt`, and only after the corpus English has reproduced the ROM's own
text exactly. The pinned symbol table and charmap are downloaded like the
corpus; they carry addresses and an encoding table, no game text.

Before packaging, `tools/gate_frlg.lua` loads the mod through gen1recomp's
real generation-3 loader over the extracted game3 data and checks that each
catalog lands where the FireRed screens read it. It also measures runtime
limits the mod cannot fix itself, and the build prints them. Today the most
visible one is that FireRed's font lookup only knows the US cart's letters, so
accented letters (à, ç, ü, ñ, ¡…) render blank until gen1recomp maps them to
the glyphs the ROM font already has. Species and move names also revert to
English on entering the field, and the Pokédex descriptions, the help system
and the quest log have no way in yet. Every one of these is tracked in the
FireRed section of [docs/upstream-fixes.md](docs/upstream-fixes.md).
Japanese is not offered: the game3 runtime has no way to draw kana yet.

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
| `ja-Hrkt` | RBY, Gold/Silver/Crystal | Fusion Pixel Japanese, 8px | — |
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
| `fr` | 3286/3286 (100%) | 3400/3400 (100%) | 421/421 (100%) |
| `de` | 3286/3286 (100%) | 3400/3400 (100%) | 421/421 (100%) |
| `es` | 3286/3286 (100%) | 3400/3400 (100%) | 421/421 (100%) |
| `it` | 3286/3286 (100%) | 3400/3400 (100%) | 421/421 (100%) |
| `ja-Hrkt` | 3286/3286 (100%) | 3397/3400 (99.91%) | 421/421 (100%) |

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
| `fr` | 6839/6839 (100%) | 943/943 (100%) | 3994/3994 (100%) |
| `de` | 6839/6839 (100%) | 943/943 (100%) | 3994/3994 (100%) |
| `es` | 6839/6839 (100%) | 943/943 (100%) | 3994/3994 (100%) |
| `it` | 6839/6839 (100%) | 943/943 (100%) | 3994/3994 (100%) |
| `ja-Hrkt` | 6839/6839 (100%) | 943/943 (100%) | 3994/3994 (100%) |
| `ko` | 5796/6839 (84.75%) | 943/943 (100%) | 0/3994 (0%) |

### FireRed

- `FireRed ROM aggregate` combines the dialogue messages with the named
  catalogs (species, move and item names, item descriptions, trainer names
  and class names, start menu labels). The 39 braille messages stay in
  English (the runtime cannot draw braille in any language), as do the
  `POKéBLOCK CASE` item whose name the extractor already loses in English,
  and the eight trainer classes whose English name gen1recomp compares
  (RIVAL, LEADER, ELITE FOUR and CHAMPION, two classes each): translating
  them would lose the rival's chosen name and misfile gym, Elite Four and
  champion wins in the quest log.
- `FireRed engine strings` covers the 1,616 `Strings()` keys reachable from
  the game3 runtime: battle messages, menus, Pokédex labels, Oak's speech,
  options, ability names, move and ability descriptions, place names. They
  are listed with their callsites and cart rows in
  [`config/frlg/engine_scope.json`](config/frlg/engine_scope.json), which
  `pipeline/frlg_engine_scope.py` regenerates from the pinned engine; a test
  derives the same set from it, so a new key cannot slip out of the metric.

| Target | FireRed ROM aggregate | FireRed engine strings |
| --- | ---: | ---: |
| `fr` | 5631/5680 (99.14%) | 1616/1616 (100%) |
| `de` | 5631/5680 (99.14%) | 1616/1616 (100%) |
| `es` | 5631/5680 (99.14%) | 1616/1616 (100%) |
| `it` | 5631/5680 (99.14%) | 1616/1616 (100%) |

These measure what the mod ships, not what the current runtime displays; see
"Pokémon FireRed support" above for the runtime limits.

### Other engine strings

The remaining engine keys are reported separately below. They are keys used by
neither RBY nor Gold and Silver, so their denominator is the residual scope:
`2808 - (421 + 943 - 85) = 1529`. The numerator counts keys translated in at
least one of the RBY and Gold/Silver/Crystal artifacts; this is a
project-level metric, not a claim that every key is present in both games.
The FireRed-reachable keys are measured separately above ("FireRed engine
strings"), so this residual scope and its numerators leave the FireRed
artifact out.

| Target | Other engine strings |
| --- | ---: |
| `fr` | 267/1529 (17.46%) |
| `de` | 270/1529 (17.66%) |
| `es` | 267/1529 (17.46%) |
| `it` | 268/1529 (17.53%) |
| `ja-Hrkt` | 267/1529 (17.46%) |
| `ko` | 184/1529 (12.03%) |

The denominator is calculated as follows: `2808` total engine keys, minus the
`421` RBY-related keys and the `943` Gold and Silver-related keys, plus back the `85` keys
shared by both scopes so they are subtracted only once. The resulting residual
scope is `1529` keys, most of them FireRed's: the Gold/Silver corpus
also matches some of them, which is why the numerators grew with the
FireRed branch.

These values use the pinned ROMs, corpus snapshots and Gen1Recomp revision
`b9d2f97f` (v0.2.67); regenerate them whenever one of those inputs changes.

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

## Windows/Linux standalone executables

The GitHub Actions workflow builds CLI and graphical Tkinter executables for
Windows x64 and Linux x86_64:

- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-windows-x64.exe`
- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-linux-x86_64.tar.gz`

Windows users can run the downloaded EXE directly. Linux builds target Ubuntu
22.04 (glibc) and compatible newer systems; extract the selected archive and
run its binary:

```sh
tar -xzf gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64.tar.gz
chmod +x gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
./gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
```

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

| Area | Modules | Responsibility |
| --- | --- | --- |
| Entry points and policy | `cli.py`, `builder.py`, `gui.py`, `orchestration.py`, `specs.py` | Resolve release requests and dispatch the command, interactive and GUI flows. |
| Inputs and workspace | `project.py`, `dependencies.py`, `rom_paths.py`, `roms.py` | Resolve paths, verify private ROMs and prepare pinned dependencies. |
| Corpus model | `corpus.py`, `model.py`, `align.py`, `worksheet.py`, `tokens.py` | Parse parallel corpora, align qids and preserve control-token contracts. |
| RBY generation | `join.py`, `generate.py`, `literals.py`, `yellow.py`, `yellow_audit.py`, `mod.py` | Join Red/Blue catalogs, build the Yellow layer and emit the universal mod. |
| Gold and Silver generation | `gs_text.py`, `gs_join.py`, `gs_index_join.py`, `gs_engine.py`, `gs_mod.py` | Join GoldSilver to pointer/index catalogs, engine strings and the Gen 2 artifact. |
| FireRed generation | `frlg_text.py`, `frlg_join.py`, `frlg_mod.py` | pret charmap/symbols, the game3 text IR, the address-to-label join and the Gen 3 artifact. |
| Engine strings | `engine.py`, `engine_scope.py` | Match the versioned engine catalog and classify production callsites. |
| Validation and audits | `validate.py`, `disassembly_audit.py`, `engine_backlog.py` | Enforce release gates and produce private diagnostic reports. |

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
```

Tag pushes matching `v<version>` validate the version and publish all four
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
- FireRed renders accented letters blank, reverts species and move names on
  entering the field and keeps much of its interface in English until the
  upstream fixes listed in the FireRed section of the document below land.
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
