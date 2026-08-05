# Gen1Recomp translation mods

[![All Contributors](https://img.shields.io/badge/all_contributors-2-orange.svg?style=flat-square)](#contributors-)

This repository reproducibly generates multilingual `Gen1Recomp` translation
mods. It turns the parallel Red/Blue data in `poke-corpus` into ROM catalogs
and engine string overrides without storing a ROM or ROM extract here.

> **AI-assisted development disclosure:** The repository and pipeline were
> developed with AI assistance. Changes are checked through automated tests,
> generated-artifact validation, and code review.

## Quick start

### Recommended: use the graphical application

Download the GUI executable for your platform from the
[latest release](https://github.com/thibautbus/gen1recomp-translation-mod-generator/releases/latest),
then select:

![Gen1Recomp translation mod generator GUI](docs/gui.png)

1. your own canonical US Pokémon Red and Blue ROM dumps;
2. the target language;
3. the output directory.

The GUI runs the same verified matching and packaging pipeline as the CLI and
writes the ready-to-import ZIP into the selected directory. The standalone
application bundles its Python runtime, Pillow (used by Gen1Recomp's US-ROM
asset importer), and LuaJIT, so those
prerequisites do not need to be installed separately. Network access is still
required to download the pinned Gen1Recomp and PokeCorpus inputs.

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

Use `python3 build_translation.py` or `py -3 build_translation.py` when needed.
With a virtual environment, use its interpreter explicitly, for example
`./venv/bin/python build_translation.py` or
`venv\Scripts\python.exe build_translation.py` on Windows.

The assistant asks for full paths to canonical US Pokémon Red and Blue ROM
dumps, then a target language. It verifies US SHA-1 fingerprints and asks
before cloning pinned repositories. After validation, it:

1. clones the pinned Gen1Recomp revision and required
   `poke-corpus/corpus/RedBlue` subtree under `.cache/`;
2. extracts both ROMs into private ignored directories and creates the complete
   Modkit worksheet;
3. matches ROM and engine catalogs against the selected corpus language;
4. enables Gen1Recomp's bundled Plain Pixel TTF (keeping macro and tile
   glyphs on the engine's tile pages) and applies the
   selected language's optional corpus overrides;
5. runs strict validation and ROM-content lint while packing, scans a private
   candidate archive, and atomically publishes it to `dist/`.

The final file is `dist/translation-<lang>-<version>.zip` (for example
`dist/translation-fr-0.5.0.zip`); the command prints its absolute path.

### Optional local path configuration

Copy [`config/rom_paths.example.toml`](config/rom_paths.example.toml) to the
ignored `config/rom_paths.toml` and edit it:

```toml
[rom]
red = "/absolute/path/to/PokemonRed.gb"
blue = "/absolute/path/to/PokemonBlue.gb"

```

Red and Blue entries may be independent. `~` expands and relative paths resolve relative to this file;
absolute paths are recommended. On Windows use forward slashes or TOML
literal single-quoted paths such as `red = 'C:\Games\PokemonRed.gb'` (a
double-quoted string must double each backslash). A configured path is still
validated for existence and SHA-1; choosing No
returns to the normal prompt. The ROM file remains private and Git-ignored.

## Legal inputs and privacy

Use dumps from your own original US Red
(`ea9bcae617fdf159b045185467ae58b2e4a48b9a`) and Blue
(`d7037c83e1ae5b39bde3c30787637ba1d4c48ce2`) cartridges. These canonical
files are local and configurable; the pipeline never downloads, provides, or
redistributes ROMs, patches, or copyrighted text extracts. Import verifies
both SHA-1 values before reading.

`config/pipeline.toml` records
the canonical hashes and pinned Gen1Recomp/`poke-corpus` revisions;
The builder asks permission before cloning revisions into private ignored
`.cache/` paths. Imported data, worksheets, catalogs, reports, and complete
extracted fonts remain there and are never committed or packaged.
`config/rom_paths.toml` is ignored; never commit it or replace the tracked
example with personal paths. A publication may contain the generated mod,
English documentation only after a no-ROM-content inspection. Do not claim ROM redistribution or provide download
instructions.

## Languages and fonts

The RedBlue corpus currently provides `fr`, `de`, `es`, `it`, and `ja-Hrkt`.
English is the source language and runtime fallback: an empty generated value
leaves the original English string visible. The builder always requires an
explicit target-language selection.

All languages use the bundled Plain Pixel TTF. Latin languages register its
default profile (`{}`). Japanese uses `{ size = 10, tiles = "0123456789/:" }`
so numeric and punctuation columns retain vanilla tile widths. Macros and
border/chrome glyphs remain tile-rendered by the engine. ROM-derived worksheets contain six catalogs
(`dialogue`, `species_names`, `move_names`, `item_names`, `trainer_names`,
`status_labels`) plus the empty 604-key `strings.lua` scaffold; none are
committed or packaged. Two engine edges are covered by one dedicated hook:
Yellow's Pallet-intro catch demo and the old-man tutorial both route the
hard-coded English thrower names `PROF.OAK` and `OLD MAN` into
`BattleState.makeOldManDemo` (shown in the translated
`%s used POKé BALL!` template). Both literals are joined qid-driven into a
`demo_names` catalog (`rb.core.DisplayBattleMenu.oldManName` and
`rb.name_pointers.TrainerNamePointers.ProfOakName`) — e.g. `OLD MAN` →
`VIEILLARD`/`GREIS`/`ANCIANO`/`VECCHIETTO`/`おじいさん`, `PROF.OAK` →
`PROF.CHEN`/`PROF.EICH`/`PROF. OAK`/`PROF. OAK`/`オーキド`. The engine's
`BattleState.demoName` stays the canonical English literal — Yellow's
Pallet intro keys its sprite selection off `demoName == "PROF.OAK"` — so
the generated `main.lua` swaps in the localized name only at the render
site, wrapping `BattleState.oldManThrow`'s `%s used POKé BALL!` message
(and reverting right after; the translated trainer record
`data.trainers["OPP_PROF_OAK"].name` remains a last-resort fallback).
The Pallet-intro thrower sprite is deliberately left to the engine: with
`demoName` kept canonical, the engine itself selects Prof. Oak's back pic
for that demo, exactly as in vanilla (a `player.sprite` override would
clobber it with the front trainer pic).

The trainer send-out message (BattleState's TrainerSentOutText) is joined
qid-driven from the single corpus row `rb.text_2.TrainerAboutToUseText`
into the `strings` engine catalog. The engine templates are
English-structured, so the derivation follows each language's ROM
structure: faithful for fr/es/it (`PIERRE\nva appeler...`, `¡%s\nva a
utilizar a`), adapted for de (the nick's verb phrase moves into the
`%s!` message and the player name is injected into the change prompt,
whose ROM form has no placeholder) and ja (`%sは\u3000` / `%sを...` / `%sも
\u3000POKéMONを...`). Engines since commit #565 merged the first two parts into
one template (`%s is\nabout to use\v%s!`, two placeholders); with the engine
pinned to v0.1.72 (commit `a83d18fc`) that merged key is the worksheet's
own entry; the pre-#565 split forms were dropped with the pin.
The Pokédex footer (`SEEN %3d  OWN %3d`) is similarly joined qid-driven from
the two corpus label rows `rb.pokedex.PokedexSeenText` and
`rb.pokedex.PokedexOwnText` (`VUS %3d  PRIS %3d`).
Type display names are engine content — the runtime
`type_chart` registry, not a modkit worksheet — so a seventh `type_names.lua`
catalog is joined qid-driven from `rb.names.TypeNames.*`: exactly the 15
types the engine registers. The registry keeps the English names and the mod
localizes them at draw time instead: every engine site renders a type name as
a standalone `Font.draw` string, which the generated `main.lua` substitutes
(e.g. `FIRE` → `FEU`) before drawing. This keeps `TypeChart.displayName`
returning the English name, so third-party mods that key colors or UI off it
(Kanto Companion's type-color chips) keep working unmodified in every
language. The substitution is exact-string: any drawn text equal to an
English type name is localized (a nickname like `FIRE` renders as `FEU`), a
deliberate side effect of the draw-time approach. The corpus `Bird` row is
recorded as excluded: Gen 1's unused type id 6 is never registered by the
engine. Western ZIPs include only the compact glyph sheet and its Lua
registration files.

The same generated hook translates an explicit allowlist of raw values in the
in-game Options menu (`COLORS`, video mode, void fill, music filter and game
speed). Gen1Recomp's label helpers return these values without calling
`Strings()`, so they use documented manual overrides. Numeric values and
acronyms remain unchanged. The desktop launcher uses a separate Kit renderer
and is intentionally outside this hook. The v0.1.72 Options menu also exposes
`VIBRATION`; its `LIGHT`/`MEDIUM`/`HEAVY` labels are tracked as dynamic engine
values alongside the existing option labels.

## Translation coverage

`ROM aggregate` is the release gate: six ROM-derived catalogs plus the
corpus-derived runtime extras in the next column (type names, literal handlers,
demo names `OLD MAN`/`PROF.OAK`, the two trainer send-out keys, the
Pokédex footer `SEEN %3d  OWN %3d`, the two romText fallback keys
(`%s\nused %s!`, `The enemy's weak!\nGet'm! %s!`) and the enemy qualifier
`Enemy %s`). Engine
columns are informational; English fallback keeps untranslated entries
playable. Reports are generated from cached ROM imports and corpus
snapshots, so revisions can change these values.

> The `Corpus-derived runtime extras` column is the sum `15 + 5 + 2 + 2 + 1 + 2 + 1 = 28`
> (romText fallbacks + the enemy qualifier), so `3130 = 3102 + 28` reads
> directly off the table. Engine-string counts also
> move between revisions because the engine re-channels texts. The v0.1.72
> denominator (638, scope classifier v4) contains the union of the 604-key
> Modkit scaffold and 31 option-value keys
> (`FAST`/`MEDIUM`/`SLOW`, `low`/`balanced`/`high`/`auto`, `AUTO`/
> `PORTRAIT`/`LANDSCAPE`/`REVERSE LANDSCAPE`, plus haptic `LIGHT`/`HEAVY`)
> that the literal callsite
> scanner cannot see (dynamic `Strings` lookups through label functions) —
> declared in `engine_scope.json` `engine_dynamic_values` so their manual
> overrides ship. `OFF` already exists in the scaffold; one additional forced
> dynamic key (`NAME`) and the three
> rendered romText fallbacks omitted by the scaffold. The `All engine strings`
> numerator counts translations
> that reach the screen: the strings catalog PLUS the 13 keys marked
> `engine_empty` in `key_scope_overrides` whose text the dialogue owns
> (`data.text` renders localized — e.g. `Welcome to our POKéMON CENTER!`,
> `Keep it up!`, `No SURFing here!`), reported as `covered_by_rom` in the
> per-key breakdown. `fallback_english` counts unmatched keys; ambiguous keys
> are reported separately, and both keep the runtime's English text. Six
> corpus-qid keys are merged after matching (the two trainer
> send-out, Pokédex footer `SEEN %3d  OWN %3d`, the two romText fallbacks and
> `Enemy %s`). Their shipped values are synced back into the report so its
> count stays accurate; existing semantic or override provenance is retained.

| Target | ROM catalogs | Corpus-derived runtime extras | ROM aggregate | RBY-related engine strings | All engine strings |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 3102/3102 (100%) | 28/28 (100%) | 3130/3130 (100%) | 248/249 (99.60%) | 338/638 (52.98%) |
| `de` | 3102/3102 (100%) | 28/28 (100%) | 3130/3130 (100%) | 248/249 (99.60%) | 338/638 (52.98%) |
| `es` | 3102/3102 (100%) | 28/28 (100%) | 3130/3130 (100%) | 248/249 (99.60%) | 337/638 (52.82%) |
| `it` | 3102/3102 (100%) | 28/28 (100%) | 3130/3130 (100%) | 248/249 (99.60%) | 339/638 (53.13%) |
| `ja-Hrkt` | 3102/3102 (100%) | 28/28 (100%) | 3130/3130 (100%) | 248/249 (99.60%) | 338/638 (52.98%) |

The corpus-derived runtime extras are fully translated (28/28): fifteen type names,
five literal handlers (15/15 unique corpus qids), two demo names, the
three engine templates (send-out + Pokédex footer), the two romText
fallback keys (the battle move-use `X used Y!` and the rival `The enemy's
weak!` messages, rendered via Strings because their pokered labels carry
fewer slots than the calls pass), and the enemy qualifier `Enemy %s` (words
from `rb.text.EnemyText`). Engine coverage separately includes manual
engine-original translations such as `FOE`, the four gameplay templates with
no compatible PokeCorpus source, and the Options/launcher labels.
`RBY-related engine strings` counts 249 keys from Gen1Recomp v0.1.72's
638-key engine universe whose
production callsites reproduce original Red/Blue gameplay or interfaces; `All
engine strings` covers the complete catalog, including modern surfaces. The
versioned [`engine_scope.json`](config/engine_scope.json) classifier (revision
`a83d18fc5139b99305e010ab077028a91a65074a`) scans production `src` callsites:
249 keys form the eligible denominator, seven require review, and 382 modern,
network/link, import, core, diagnostic, defensive, fallback-only, or
ROM/generated-path keys are ineligible. Coverage comes from
isolated clean rebuilds using pinned snapshots; if the engine source is
unavailable, the report omits RBY coverage and warns instead of guessing. Each
`x/y` value is translated entries out of eligible entries, followed by a
percentage; fallback entries are not counted as translated.
Versioned per-key scope overrides preserve the original callsites in private
audits while excluding proven modern, diagnostic, unreachable-vanilla, or
ROM/generated-path fallbacks from the RBY denominator.
An original-RBY callsite qualifies unless the same key also has a link callsite;
modern mod-manager/desktop surfaces, network/tournament flows, imports, and
shared link+RBY keys are therefore not silently counted as RBY coverage.
`_OakSpeechText2A` is intentionally excluded from the engine denominator: its
localized text is supplied by the ROM/Data.text dialogue catalog, while the
engine symbol remains empty to avoid OakSpeech's double lookup. The 249
eligible RBY keys (the `Strings(...)` callsites plus the rendered romText
fallbacks `%s\nused %s!`, `The enemy's weak!\nGet'm! %s!` and the
Yellow-only `%s\nis refusing!`) are translated 248/249 (99.60%) — the sole
gap is the deliberately English Yellow Pikachu-stone line, deferred to
Yellow support.

Fallback-only labels shown exclusively when generated assets or vanilla
reward metadata are missing remain in English and outside RBY coverage. This
includes the text substitutes for the intro sprites, title logo and slot
machine frame, plus the generic gym-reward templates. Translating them would
inflate coverage without changing a normal build.

`forced_dynamic_keys` in `engine_scope.json` records the five SummaryMenu
labels (`NAME`, `ATTACK`, `DEFENSE`, `SPEED`, `SPECIAL`) selected from a runtime
table that the literal scanner cannot see. They are unioned into the engine
catalog as `forced_dynamic` RBY-eligible entries, with callsite, qid, and the
upstream engine-contract limitation retained in coverage provenance. The 13
ROM-owned literals marked `covered-by-rom` stay empty in `strings.lua`; their
localized values come from `Data.text`/the dialogue catalog. Generation also
rejects only the three fragile `Commands.show_text` literals when a translated
value is itself a dialogue/Data.text key, because that upstream API performs a
second lookup before formatting.

## Translation provenance

Every translated engine string remains traceable:

| Origin | Meaning | Recorded in |
| --- | --- | --- |
| Automatic match | Exact, normalized, or structural match proved by the generator. | Generation report |
| Deterministic anchor | Reliable PokeCorpus qid, composition, or extraction rule. | `config/semantic_anchors.json` |
| Human-reviewed anchor | Contextual or language-specific extraction reviewed by a maintainer; text still comes from PokeCorpus. | `config/semantic_anchor_decisions.json` |
| Manual corpus correction | A maintainer corrects one selected-language corpus translation without changing the upstream corpus. Entries are indexed by qid. | `overrides/<language>/corpus_overrides.json` |
| Manual translation — engine contract gap | PokeCorpus has the text, but Gen1Recomp merges contexts or hides required parameters. | `overrides/<language>/engine_overrides.json`, `reason: "engine-contract-gap"` |
| Manual translation — engine original | Engine-specific text with no Red/Blue source. | `overrides/<language>/engine_overrides.json`, `reason: "engine-original"` |
| Editorial correction | Deliberately preferred engine formulation. | `overrides/<language>/engine_overrides.json`, `reason: "editorial-correction"` |
| Known limitation | Active anchor/override knowingly imperfect in a context or language; a status, not an origin. | Anchor metadata or override provenance |
| English fallback | No sufficiently reliable translation; runtime keeps English. | Generation report |

This taxonomy is exhaustive, but the affected strings evolve with engine and
corpus revisions. Generated coverage reports are the authoritative inventory
of unmatched and ambiguous strings for a given build.

Each language currently contains 32 AI-generated `engine-contract-gap`
overrides, including 13 raw in-game option values that Gen1Recomp does not
route through `Strings()`. The larger `engine-original` group includes eleven legacy manual
entries that were normalized during the provenance migration. Their per-entry provenance records the limitation,
the upstream improvement path where applicable, and the need for in-game
visual validation. Technical labels and formats are retained where changing
them would break the engine contract.

Battle stat-stage messages have a known upstream limitation: Gen1Recomp passes
the raw English `stat:upper()` value into otherwise localized templates, so a
message may still contain `ATTACK`, `DEFENSE`, `SPEED`, or `SPECIAL`. Localizing
the Summary menu labels does not change those battle arguments; a fully faithful
fix requires Gen1Recomp to pass localized stat names at those callsites.

Manual overrides include a concrete provenance explanation. Do not add one just
to raise coverage: a shared key or missing runtime argument can make a value
wrong elsewhere. Keep English unless the limitation is explicitly accepted.

## Windows/Linux standalone executables

The GitHub Actions workflow builds CLI and graphical Tkinter executables for
Windows x64 and Linux x86_64:

- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-windows-x64.exe`
- `gen1recomp-translation-mod-generator-<version>-<cli|gui>-linux-x86_64.tar.gz`

Linux builds target Ubuntu 22.04 (glibc) and
newer compatible systems; other architectures and libc implementations are
not supported. No executable is published until a release workflow run
occurs. Both platform builds compile official LuaJIT at a pinned commit, bundle
checked-in configuration, and validate the CLI and GUI before
uploading the four versioned artifacts.

The CLI keeps the terminal prompts documented above. The GUI provides file
pickers for both US ROMs, the target language, and the output directory. It runs the same matching and packaging
pipeline in the background, with a build status and collapsible log. Closing
the GUI is blocked while a build is active to avoid interrupting private
extraction or dependency downloads.

Windows users download the preferred versioned EXE. Linux users extract the
matching CLI or GUI tarball and run its binary, for example:

```sh
tar -xzf gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64.tar.gz
chmod +x gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
./gen1recomp-translation-mod-generator-<version>-gui-linux-x86_64
```

Each standalone executable needs network access for
the pinned Gen1Recomp archive and seven pinned PokeCorpus files; it never
bundles/uploads ROMs. The CLI keeps downloads and intermediate data in
`.cache/` under the current working directory and writes the final ZIP there.
The GUI stores them in `.cache/` under the selected output directory and writes
the ZIP directly into that directory. Archive URLs, revisions, and SHA-256 pins
are checked; traversal, symlink, duplicate, and corrupt entries are rejected,
and a marker permits reuse of a verified cache. Keep `config/rom_paths.toml`
and ROM files out of the bundled application directory.

Maintainers rebuild locally with:

```powershell
./packaging/build_windows_executable.ps1
```

```sh
./packaging/build_linux_executable.sh
```

Tag pushes (`v<version>`) build all four artifacts and publish one release after
validating the tag against `pyproject.toml`; `workflow_dispatch` performs the
builds without publishing a release.

This does not alter the manual flow: unfrozen `python build_translation.py`
still checks Python, Git, LuaJIT, and Pillow, clones pinned repositories into
the repository `.cache/`, and writes under `dist/`. The `.zip` extension is
intentional because Gen1Recomp's importer accepts Modkit's deterministic ZIP.
The builder prints ROM, RBY-engine, and all-engine match percentages immediately
before the final path. `dist/` is ignored, so
publishing is explicit.
Manual engine translations and corpus corrections live in separate versioned
files and are applied automatically. Each language has a checked-in
`overrides/<language>/corpus_overrides.json` skeleton as an extension point;
its `entries` object is keyed by corpus qid and is empty until a real
correction is needed. `engine_overrides.json` remains reserved for engine
contract gaps, engine-original strings, and their explicit provenance.
Corpus sources and private generated catalogs are never rewritten.

## Maintainer reference

### Data flow and matching

```text
parse corpus -> align by qid -> join the ROM worksheet catalogs -> fill engine strings
```

`strict_engine` remains available: it requires engine catalog/scaffold files,
not complete translation. Resolution is deterministic:

```text
explicit override > semantic anchor > exact > normalized
> structural placeholder match > empty entry (runtime English fallback)
```

`config/semantic_anchors.json` contains deterministic qids/rules only;
`config/semantic_anchor_decisions.json` records reviewed choices, rationale,
trace status, and selected qids. Decision rows explicitly mark unavailable
language evidence rather than claiming verification. The engine validates both
files, rejects key overlap, and reports decision provenance separately.
Private manual candidates stay in the ignored review cache and are never
executable configuration. `config/terminology_anchors.json` is corpus-only;
it proves terminology rather than hard-coding a language.
It covers prefixes/digit style for 50 TM and
5 HM displays (FR `CT`/`CS`, DE `TM`/`VM`, ES `MT`/`MO`, IT `MT`/`MN`, and
Japanese full-width examples `わざマシン`/`ひでんマシン`, `３４`). Missing or
ambiguous anchors stay manual. `config/literal_handlers.json` describes qid
extraction gaps; the selected corpus supplies branch text, and incomplete or
ambiguous recipes leave the English handler active.

### Module map

| Module | Responsibility |
| --- | --- |
| `pipeline/cli.py` | Defines `parse`, `align`, `generate`, `validate`, and ROM import commands. |
| `pipeline/corpus.py` | Reads RedBlue files, canonicalizes languages, validates cardinality, and produces records. |
| `pipeline/model.py` | Shared `CorpusRecord` and `Alignment` structures. |
| `pipeline/align.py` | Pairs by qid, applies `corpus_overrides`, and writes aligned data. |
| `pipeline/worksheet.py` | Loads and writes versioned `corpus_overrides` documents. |
| `pipeline/join.py` | Joins aligned records to exact worksheet keys, TM/HM terminology, and qid-driven type names (`type_names` catalog). |
| `pipeline/engine.py` | Matches the versioned engine-string universe using overrides, anchors, text, and placeholders. |
| `pipeline/engine_scope.py` / `config/engine_scope.json` | Versioned RBY classifier over production `src` callsites. |
| `pipeline/literals.py` | Generates qid-driven handlers for Lua literals. |
| `pipeline/tokens.py` | Converts corpus control tokens and validates placeholders. |
| `pipeline/mod.py` | Writes Modkit Lua catalogs, manifest, worksheets, and coverage report. |
| `pipeline/validate.py` | Checks placeholders, glyphs, versions, ROM gate, and engine diagnostics. |
| `pipeline/roms.py` | Verifies hashes and imports private Red/Blue caches. |
| `pipeline/disassembly_audit.py` | Parses private localized disassembly snapshots into audit reports. |
| `pipeline/engine_backlog.py` | Read-only analyzer for unresolved engine keys, scope, placeholders, fallbacks, and qid candidates. |

`pipeline/generate.py` and `pipeline/worksheet.py` provide serialization
helpers. `build_translation.py` is the normal entry point;
`scripts/build-mod.sh` remains for maintainers with a private worksheet. Both
keep intermediates under `.cache/`.

### Audit commands

Disassembly audit:

```sh
python scripts/pipeline.py audit-disassemblies
```

It fetches pinned audit repositories into `.cache/audit/disassemblies/` and
writes private match/divergence/callsite reports under `.cache/audit/reports/`.
Never publish them: they can contain copyrighted text and local paths. The
Italian snapshot is excluded when detected as German. This command is separate
from `build_translation.py` and does not require existing coverage reports.

Engine backlog:

```sh
python scripts/pipeline.py engine-backlog --language fr
```

This records callsites, RBY eligibility, placeholders, fallback reasons, and
qid candidates in `.cache/audit/engine-backlog/<language>.{json,md}`. Fuzzy
suggestions are advisory, and anchors, overrides, and catalogs are untouched.
A matching cached coverage/catalog snapshot is required. Classification reuses
the versioned production scanner, so it cannot invent a second RBY scope.

For all languages (default `fr,de,es,it,ja-Hrkt`):

```sh
python scripts/pipeline.py engine-backlog-matrix
```

It writes `.cache/audit/engine-backlog/matrix.{json,md}` with candidates,
callsites, key commonality, and conservative triage. Use `--coverage-dir`,
`--engine-catalog-dir`, `--coverage LANG=PATH`, or
`--engine-catalog LANG=PATH` to select explicit private snapshots.

### Remaining limitations

UI-width constraints, in-game testing, and incomplete engine coverage remain
relevant; Japanese uses the dedicated Plain Pixel size/tiles profile above.

## Credits

- [Gen1Recomp](https://github.com/bryanthaboi/gen1recomp) by [bryanthaboi](https://github.com/bryanthaboi), the target game recompilation.
- [PokéCorpus](https://github.com/abcboy101/poke-corpus) by [abcboy101](https://github.com/abcboy101), the multilingual translation corpus.

## Contributors ✨

Thanks go to these wonderful people:

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<table>
  <tr>
    <td align="center" valign="top" width="14.28%"><a href="https://github.com/thibautbus"><img src="https://avatars.githubusercontent.com/thibautbus?s=100" width="100px;" alt="thibautbus"/><br /><sub><b>thibautbus</b></sub></a><br /><a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Code">💻</a> <a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Documentation">📖</a> <a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/commits?author=thibautbus" title="Maintenance">🚧</a></td>
    <td align="center" valign="top" width="14.28%"><a href="https://github.com/antoniman31"><img src="https://avatars.githubusercontent.com/u/268696974?s=100" width="100px;" alt="AntoniMan31"/><br /><sub><b>AntoniMan31</b></sub></a><br /><a href="https://github.com/thibautbus/gen1recomp-translation-mod-generator/pull/10" title="Bug fixes">🐛</a></td>
  </tr>
</table>

<!-- ALL-CONTRIBUTORS-LIST:END -->
