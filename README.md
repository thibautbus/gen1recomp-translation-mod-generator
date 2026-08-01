# Gen1Recomp translation mods

This repository is a reproducible generator for multilingual Gen1Recomp
translation mods. It turns the parallel Red/Blue data in `poke-corpus` into
ROM catalogs and engine string overrides without storing a ROM or a ROM
extract in the repository.

> **AI-assisted development disclosure:** This repository and pipeline were
> developed with AI assistance. Changes are checked through automated tests,
> generated-artifact validation, and code review.

## Scope, legal inputs, and privacy

Use dumps from your own original US versions of Red
(SHA-1: `ea9bcae617fdf159b045185467ae58b2e4a48b9a`) and Blue
(SHA-1: `d7037c83e1ae5b39bde3c30787637ba1d4c48ce2`) cartridges. The pipeline expects
these canonical US files (the paths are local and configurable); it does not
download, provide, or redistribute ROMs, patches, or copyrighted text
extracts. Import verifies the canonical SHA-1 values before reading a ROM.
Imported data, catalogs, worksheets, reports, and other rebuildable material
stay in the private, git-ignored `.cache/` tree.

Western builds also require exactly one user-owned localized Red **or** Blue
ROM as a font source. A localized whole-ROM SHA-1 is intentionally not pinned:
the extractor verifies only the SHA-256 fingerprint of the reviewed font-tile
region. The source ROM remains private and is never copied into the archive.

`config/pipeline.toml` records the canonical US Red/Blue SHA-1 values and the
pinned Gen1Recomp and `poke-corpus` revisions. Reviewed localized font-region
fingerprints live in `pipeline/localized_font.py`. The interactive builder
clones the pinned revisions into the ignored, private `.cache/` directory
after asking for permission.

## Languages and source data

The RedBlue corpus currently provides parallel `fr`, `de`, `es`, `it`, and
`ja-Hrkt` files. English is the source language and the runtime fallback: an
empty generated value leaves the original English string visible. The
interactive builder always requires an explicit target-language selection;
there is no default language.

> **Warning:** Japanese localized-font extraction is not supported yet, and
> the Japanese translation does not currently display correctly in game. The
> builder does not request a Japanese ROM and shows this warning when Japanese
> is selected.

> **Font warning:** Some special or language-specific characters may not
> display correctly in game when they are missing from the generated font or
> charmap. Always verify accented characters, punctuation, and non-Latin
> scripts in game before publishing a translation.

### Localized font support

The Western extractor uses the same 1bpp extraction and compact Modkit page
for every language. The table summarizes the generated pages:

| Target | Packaged glyphs | Font family |
| --- | ---: | --- |
| French (`fr`) | 19 | French/German |
| German (`de`) | 19 | French/German |
| Spanish (`es`) | 32 | Spanish/Italian + `¿`/`¡` |
| Italian (`it`) | 30 | Spanish/Italian |
| Japanese (`ja-Hrkt`) | — | Unsupported |

French and German share one reviewed 19-glyph ROM region. Spanish and Italian
share a reviewed 30-glyph region; Spanish `¿` and `¡` are generated locally by
rotating the same ROM's vanilla `?` and `!` faces. The resulting one-row
`assets/font/localized.png` contains only the required glyphs, never the full
ROM font.

Gen1Recomp/modkit worksheets are private, ROM-derived references generated
from the imported dataset. They contain the six Red/Blue catalogs
(`dialogue`, `species_names`, `move_names`, `item_names`, `trainer_names`, and
`status_labels`) plus the empty 533-key engine `strings.lua` catalog. The
canonical and localized source ROMs, worksheets, and complete extracted fonts
are never committed or packaged. Only the compact generated glyph sheet and
its Lua registration files are included in a Western translation ZIP.

## Translation coverage

The report separates the six ROM-derived text/name catalogs from the
corpus-backed literal handlers and the 533-key engine catalog. The `ROM
aggregate` column is the release-gate value: it includes the six catalogs and
the literal handlers. These are current reports generated from the cached ROM
imports and corpus (corpus and pipeline revisions affect the numbers):

| Target | ROM catalogs | Literal handlers | ROM aggregate | RBY-related engine strings | All engine strings |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 291/383 (75.98%) | 296/533 (55.53%) |
| `de` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 289/383 (75.46%) | 294/533 (55.16%) |
| `es` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 291/383 (75.98%) | 296/533 (55.53%) |
| `it` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 291/383 (75.98%) | 296/533 (55.53%) |
| `ja-Hrkt` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 287/383 (74.93%) | 292/533 (54.78%) |

Column definitions: **ROM catalogs** covers the six extracted Red/Blue
catalogs—`dialogue`, `species_names`, `move_names`, `item_names`,
`trainer_names`, and `status_labels`. **Literal handlers** covers the five
corpus-backed handlers (five reachable handlers backed by 15 unique corpus
qids). **ROM aggregate** is the sum of those six catalogs and handlers; it is
the sole 100% completeness gate for a release. **RBY-related engine strings**
counts the 383 keys from Gen1Recomp's 533-key engine string catalog whose
production callsites reproduce original Red/Blue gameplay or interfaces. It
is a classified set of engine strings, not a separate RBY engine. **All engine
strings** covers the complete 533-key catalog, including modern Gen1Recomp
surfaces. Each `x/y` value means
translated entries out of the total eligible entries, followed by the
percentage. Untranslated engine entries remain playable because Gen1Recomp
falls back to the original English string.

Collectively, the 5/5 literal-handler results are backed by 15/15 unique
corpus qids; those handlers are included in the ROM aggregate above. Engine
fallback entries are not counted as translated, and the two engine columns are
informational only.

The RBY-related engine-string scope is computed by the versioned [`engine_scope.json`](config/engine_scope.json)
classifier (revision `5a48a61f2ed20aee80951aeb1e41b7ec084b350f`) over production
Gen1Recomp `src` callsites only. Its denominator is 383 eligible keys (379
RBY-category plus four eligible mixed keys); four shared/link or UI-only keys
require review and 146 modern, network/link-only, import, core, or other
ineligible keys are excluded. Any original-RBY callsite qualifies unless the
same key also has a link callsite. Modern mod-manager/desktop surfaces,
network/tournament flows, imports, and shared link+RBY keys are therefore not
silently counted as RBY coverage. The table comes from isolated clean rebuilds
using the pinned corpus/engine snapshots; ROM aggregate is 3106/3106 for every
language.

## Strings requiring upstream Gen1Recomp/source changes

Some strings cannot be translated safely from the current engine callsites and
corpus interface. Resolving these gaps requires a change in the pinned
Gen1Recomp source (and then a regenerated engine catalogue), rather than a new
anchor or override in this repository:

- `How many?` is assigned directly to the quantity-selector footer in
  `PlayerPC.lua` (`list.footer = "How many?"`), not routed through
  `Strings(...)`. The Red/Blue corpus has operation-specific qids
  `DepositHowManyText`, `WithdrawHowManyText`, and `TossHowManyText`; Japanese
  supplies distinct variants (`いくつ　あずけますか？`, `いくつ　ひきだしますか？`,
  and `いくつ　すてますか？`). An upstream change must route each operation
  through a context-aware `Strings(...)` key (or another explicit
  localization hook) before those variants can be selected safely.
- `QUIT` is a shared, context-free key in three different menus. The French
  Red/Blue localization uses `RET` in the narrow Pokédex menu, `SALUT!` in the
  shop, and `RETOUR` for the start-menu `EXIT` entry. Gen1Recomp currently
  calls `Strings("QUIT")` for the Pokédex, shop, and start menu, so a translation
  mod can provide only one global value. The current anchor keeps the compact
  Pokédex form (`RET` in French) because `RETOUR` does not fit safely in that
  menu. Correct per-menu translations require distinct upstream contexts such
  as `Strings("QUIT", "pokedex")`, `Strings("QUIT", "shop")`, and
  `Strings("QUIT", "start_menu")`, followed by context-aware catalogue
  generation.
- The hidden-item inventory-full message uses the exclamation key
  `You can't carry\nany more items!`, which is intentionally not aliased to the
  period form because it has different gameplay context. Translating it safely
  requires a reviewed hidden-item qid/context mapping in Gen1Recomp or the
  source corpus.

## Currently untranslated content

The `ROM aggregate` can be 100% without providing 100% engine/UI coverage;
the current split is shown in the table above. The following content therefore
deliberately remains English:

- Gen1Recomp-specific surfaces—the mod manager, link/online/tournament flows,
  and modern desktop UI—have no PokeCorpus (`poke-corpus`) source.
- Original RBY contextual lines may remain English when one generic engine key
  maps to multiple qids or events and no safe context-specific mapping exists.
- A Route 3 trainer override is avoided because it risks disabling the
  sight-trigger battle; gym statues use internal pre-/post-badge logic that is
  not safely exposed.
- Other generic found/received/reward/bag-full strings vary by context and are not
  safely overridable.
- The item-menu `USE` label is anchored to the proven
  `rb.text_boxes.UseTossText` segment and is translated in all five releases.
  The plain-key period form of `You can't carry\nany more items.` is anchored
  to the generic inventory-full qid `rb.text_2.CantCarryMoreText` and translated
  in all five releases. That generic wording intentionally covers both PC
  withdrawal and shop purchase; the shop-specific qid
  `rb.text_4.PokemartItemBagFullText` is not selected. The exclamation/hidden-
  item variant remains outside the safe scope (see the upstream-change section
  above).
- The bicycle mount corpus is split in a way that does not align safely,
  although the dismount message is translated.
- Engine-authored paraphrases with no corpus source include “the boulder fell
  through the hole,” “not near water,” and “Town Map unreadable.”
- Four ES and four IT stat-stage strings are editorial overrides, not
  PokeCorpus/qid-derived translations. They retain the raw English stat-label
  argument (`ATTACK`, `DEFENSE`, `SPEED`, `SPECIAL`, `ACCURACY`, or `EVADE`),
  preserve the runtime Pokémon-first/stat-label-second argument order, and are
  counted in the engine coverage above. The reviewed entries are versioned in
  [`overrides/es/engine_overrides.json`](overrides/es/engine_overrides.json) and
  [`overrides/it/engine_overrides.json`](overrides/it/engine_overrides.json). Other
  stat-message variants (including the alternate line-break form) remain
  English and fall back at runtime. Full stat-label localization still
  requires an upstream localization hook; no `engine_internals` monkeypatch is
  used.
- Ambiguous, missing, or placeholder-incompatible mappings deliberately fall
  back to English.

## Quick start: build a ready-to-import ZIP

Install these prerequisites first:

- Python 3.11 or newer;
- Git;
- LuaJIT (for example `sudo apt install luajit` on Ubuntu/Debian or
  `brew install luajit` on macOS);
- Pillow (`python -m pip install Pillow`).

The builder checks every prerequisite at launch and prints a precise
installation hint when something is missing. It never installs software
silently. Python itself must be installed before the command can start.
If LuaJIT is not on `PATH`, set `MODKIT_LUAJIT` to its full executable path.
LuaJIT is a native executable, not a Python package, so it cannot be installed
reliably with `pip`.

From this repository root, run:

```sh
python build_translation.py
```

On systems where Python 3 uses a different launcher, use `python3
build_translation.py` or `py -3 build_translation.py`.
When using a virtual environment, make sure the builder uses that
environment's interpreter. If a shell alias bypasses it, run
`./venv/bin/python build_translation.py` on Unix-like systems or
`venv\Scripts\python.exe build_translation.py` on Windows.

The assistant asks for the full paths to the canonical US Pokémon Red and
Blue ROM dumps and offers the supported language menu. For French, German,
Spanish, or Italian builds, it also asks for one localized Red **or** Blue ROM
as a font source. It verifies the
US SHA-1 fingerprints and asks before cloning anything. It then:

### Optional local path configuration

To avoid re-entering paths, copy
[`config/rom_paths.example.toml`](config/rom_paths.example.toml) to the ignored
`config/rom_paths.toml` and edit it. The supported schema is:

```toml
[rom]
red = "/absolute/path/to/PokemonRed.gb"
blue = "/absolute/path/to/PokemonBlue.gb"

[localized]
fr = "/absolute/path/to/PokemonFrench.gb"
de = "/absolute/path/to/PokemonGerman.gb"
es = "/absolute/path/to/PokemonSpanish.gb"
it = "/absolute/path/to/PokemonItalian.gb"
```

The Red and Blue entries may be supplied independently. Localized entries are
optional and may be partial; Japanese has no localized-ROM entry because its
font extraction is unsupported. `~` is expanded, and relative paths are
resolved relative to `config/rom_paths.toml`; absolute paths are recommended.
On Windows, either use forward slashes (`C:/Games/PokemonRed.gb`) or TOML
literal single-quoted paths such as `red = 'C:\Games\PokemonRed.gb'` (a
double-quoted TOML string must double each backslash).
When a configured path applies, the builder asks whether to use it and still
performs its normal file, SHA-1, and localized-font validation. Choosing No,
or correcting a missing configured file, returns to the usual path prompt.
The actual file is ignored by Git and must remain private.

1. clones the pinned Gen1Recomp revision and only the required
   `poke-corpus/corpus/RedBlue` subtree under `.cache/`;
2. extracts both ROMs into private, ignored directories;
3. creates the complete Modkit worksheet;
4. matches the ROM and engine catalogs against the selected corpus language;
5. for Western builds, extracts a compact one-row 1bpp glyph sheet and emits
   the official `lang/font.lua`/`lang/charmap.lua` extension-page files;
6. preserves Modkit's font, charmap, and naming integration;
7. applies optional editorial overrides;
8. runs Modkit's strict validation and ROM-content lint while packing;
9. scans a private candidate archive before atomically publishing it to `dist/`.

The final file is written to `dist/translation-<lang>-<version>.zip`, for
example `dist/translation-fr-0.3.0.zip`. The command prints its absolute path.

## Windows standalone executable

The repository includes a pinned/repeatable GitHub Actions workflow and local
`packaging/build_windows_executable.ps1` script for producing a Windows x64
one-file executable. The workflow is available, but no EXE is published until
a release workflow run is performed. The build compiles official LuaJIT at
the pinned commit, bundles Pillow and checked-in configuration, and runs tests
plus `--self-check` before uploading the versioned artifact.

For users, download the versioned EXE and double-click it from the directory
where the output should be written. The prompts are the same as the manual
CLI: provide paths to your own Red and Blue ROMs, select a language, and (for
Western languages) provide one localized font ROM. The EXE requires network
access to download the pinned Gen1Recomp archive and seven pinned PokeCorpus
files. It never bundles or uploads ROMs. Downloads and all intermediate data
are kept privately in `.cache/` under the current working directory; the final
ZIP is written directly there as `translation-<lang>-<version>.zip`.

Archive URLs, commit revisions, and SHA-256 pins are checked before
extraction. Traversal, symlink, duplicate, and corrupt entries are rejected,
and a marker permits safe reuse of a verified cache. Do not place
`config/rom_paths.toml` or ROM files in the bundled application directory.

Maintainers can rebuild the EXE on Windows with:

```powershell
./packaging/build_windows_executable.ps1
```

This dedicated packaging command does not alter the manual developer flow:
running `python build_translation.py` unfrozen still checks installed Python,
Git, LuaJIT, and Pillow, clones pinned repositories into the repository's
`.cache/`, and writes output under `dist/`.
The `.zip` extension is intentional: Gen1Recomp's mod importer accepts ZIP
files, while Modkit writes the same deterministic ZIP format.
Immediately before the final path, the builder prints ROM, RBY-related engine
string, and all-engine-string match percentages. If the pinned engine
source is unavailable, the report omits RBY coverage and records a warning
instead of guessing a denominator.

All cloned repositories, extracted data, worksheets, and reports remain in
ignored directories. Only the final mod archive is intended for use, and
`dist/` is also ignored so publishing it is always an explicit action.
Inside that archive, `assets/font/localized.png` is relative to the mod root;
the generated runtime resolves it with `mod.assets:path(...)` before
registering the page. Japanese builds skip this localized-font stage.

Editorial corrections live in the versioned files under `overrides/` and are
applied automatically by the builder. Corpus sources and generated private
catalogs are never rewritten.

### Developer-only disassembly audit

Maintainers can compare the localized `einstein95` disassembly snapshots with
PokeCorpus using:

```sh
python scripts/pipeline.py audit-disassemblies
```

This command is not part of `build_translation.py`. It fetches only the pinned
audit repositories, checks them out under `.cache/audit/disassemblies/`, and
writes private JSON and Markdown reports under `.cache/audit/reports/`. Reports
include label/qid matches, divergences, missing entries, script callsites, and
candidate context. They may contain copyrighted source text and local paths;
never commit or publish them. The Italian snapshot is intentionally marked
untrusted because it is detected as German, and its candidates are excluded
from recommendations. Existing per-language interactive coverage reports are
used only to enrich audit context; the audit still succeeds when they are
absent.

### Developer-only engine backlog

Maintainers can inventory unresolved and ambiguous engine keys from cached
build artifacts with:

```sh
python scripts/pipeline.py engine-backlog --language fr
```

The command scans the private cached Gen1Recomp checkout for every literal
`Strings(...)` callsite, records relative path/line/context, classifies modern,
link/import/core, UI, and credible original-game surfaces, and conservatively
marks RBY eligibility for review. It also records placeholder signatures,
matcher fallback reasons, and deterministic PokeCorpus qid candidates. Fuzzy
suggestions are advisory only and never count as translated. JSON and Markdown
reports are written to the ignored
`.cache/audit/engine-backlog/<language>.{json,md}` paths; anchors, overrides,
and catalogs are never modified. A cached coverage report whose engine key
universe and total match the selected catalog is required; stale or missing
snapshots fail with an English error.

Backlog classification imports the same versioned `pipeline/engine_scope.py`
rules and production-`src` scanner used by coverage, including exact `.lua`
module handling; it cannot silently invent a second RBY definition.

For a reproducible cross-language view, use the private matrix command (the
default language set is `fr,de,es,it,ja-Hrkt`):

```sh
python scripts/pipeline.py engine-backlog-matrix
```

It validates each cached coverage/catalog snapshot through the same analyzer
and writes `.cache/audit/engine-backlog/matrix.{json,md}` with source paths,
classifier/snapshot metadata, per-language candidates and callsites, key
commonality, and conservative triage labels. Use `--coverage-dir` and
`--engine-catalog-dir` (or repeat `--coverage LANG=PATH` and
`--engine-catalog LANG=PATH`) to select explicit private snapshots; no config,
catalog, or review file is modified.

## Data flow and matching

The pipeline follows one direction:

```text
parse corpus -> align by qid -> join the ROM worksheet catalogs -> fill engine strings
```

### Low-level module map

The command-line wrapper in `scripts/pipeline.py` delegates to the modules in
`pipeline/`. The main workflow is split as follows:

The existing `strict_engine` generation option is intentionally retained: it
requires the engine catalog/scaffold files to be present, but does not require
all engine entries to be translated. Unmatched entries use Gen1Recomp's
English fallback.

| Module | Responsibility |
| --- | --- |
| `pipeline/cli.py` | Defines the `parse`, `align`, `generate`, `validate`, and ROM import commands and connects their inputs and outputs. |
| `pipeline/corpus.py` | Reads the parallel Red/Blue corpus files, canonicalizes language codes, validates line cardinality, and produces source/target records. |
| `pipeline/model.py` | Defines the shared `CorpusRecord` and `Alignment` data structures passed between stages. |
| `pipeline/align.py` | Pairs English and target-language records by stable qid, applies qid-based editorial overrides, and writes the aligned intermediate representation. |
| `pipeline/join.py` | Joins aligned records to the exact ROM-derived Modkit worksheet keys, including catalog-specific rules and corpus-backed TM/HM terminology. |
| `pipeline/engine.py` | Matches the 533 engine strings using overrides, semantic anchors, exact/normalized text, and structural placeholders; unmatched entries remain empty. |
| `pipeline/engine_scope.py` / `config/engine_scope.json` | Versioned informational RBY classifier and pinned Gen1Recomp revision; scans only production `src` callsites and records eligibility/category counts. |
| `pipeline/literals.py` | Generates qid-driven Mod API handlers for ROM dialogue that Gen1Recomp carries as Lua literals instead of extracted text keys. |
| `pipeline/tokens.py` | Converts `poke-corpus` control tokens to Gen1Recomp notation and validates dynamic placeholders. |
| `pipeline/mod.py` | Writes the final Modkit-compatible Lua catalogs, manifest, worksheet outputs, and ROM/engine coverage report. |
| `pipeline/validate.py` | Checks placeholders, glyph coverage, version consistency, the ROM aggregate release gate, and informational engine diagnostics. |
| `pipeline/roms.py` | Verifies canonical ROM hashes and orchestrates private Red/Blue imports into ignored local caches. |
| `pipeline/localized_font.py` | Validates reviewed Western font regions, extracts compact language glyph pages, and generates the Modkit font/charmap catalogs. |
| `pipeline/disassembly_audit.py` | Developer-only parser for private localized disassembly snapshots; emits match/divergence/callsite reports without editing anchors or review files. |
| `pipeline/engine_backlog.py` | Read-only developer analyzer for unresolved/ambiguous engine keys, shared engine-scope categories, placeholders, fallback reasons, and PokeCorpus qid suggestions. |

Supporting compatibility helpers live in `pipeline/generate.py` and
`pipeline/worksheet.py`. `build_translation.py` is the normal interactive
entry point; `scripts/build-mod.sh` remains available to maintainers with an
existing private worksheet. Both keep rebuildable intermediates under
`.cache/`.

For engine keys, resolution is deterministic:

```text
explicit override > semantic anchor > exact > normalized
> structural placeholder match > empty entry (runtime English fallback)
```

`config/semantic_anchors.json` contains only deterministic stable qids and
extraction rules; it contains no translations. Reviewed executable anchors
that depend on contextual, localized, or editorial decisions live in
`config/semantic_anchor_decisions.json`. Each decision records an allowed
decision category, non-empty rationale, trace status, and the qids selected by
the executable anchor. Known-limitation rows explicitly mark language evidence
as unavailable rather than claiming verification. The engine validates both
files, rejects key overlap, and reports decision provenance separately from the
existing matcher details.
Private manual candidates remain in the ignored review cache and are not
executable configuration. `config/terminology_anchors.json` is likewise
corpus-only. It proves the prefixes and digit style used for the 50 TM and
5 HM displays instead of hard-coding a language. Examples are FR `CT`/`CS`,
DE `TM`/`VM`, ES `MT`/`MO`, IT `MT`/`MN`, and Japanese prefixes with Japanese
full-width digits (for example `わざマシン`/`ひでんマシン` and `３４`). Missing,
ambiguous, or unproven anchors leave that family for manual review and do not
create coverage.

`config/literal_handlers.json` describes known extraction gaps by stable qid.
The selected corpus language supplies the actual branch text; incomplete or
ambiguous recipes are skipped so the original English handler remains active.

The contextual-RBY pass adds one proven multi-qid semantic anchor for the
`%s got off\\nthe BICYCLE.` engine message. It composes the split bicycle text
and the localized item-name qid while preserving the single `%s` formatter.
The matching `got on` message is intentionally left as English because its
ROM split places the line break inside a qid part and cannot be composed
without language-specific assumptions. Route 3 Youngster 2 trainer dialogue,
gym-statue pre/post-badge text, and generic found/received/reward messages are
also skipped: overriding them would bypass central trainer or hidden-event
state machines, or would require unsafe runtime placeholder assumptions.

## Provenance, publication, and limitations

Every translated engine string should remain traceable to how it was obtained:

| Origin | Meaning | Recorded in |
| --- | --- | --- |
| Automatic match | An exact, normalized, or structural match proved by the generator. | Generation report |
| Deterministic anchor | A reliable PokeCorpus qid, composition, or extraction rule. | `config/semantic_anchors.json` |
| Human-reviewed anchor | A contextual choice or language-specific extraction reviewed by a maintainer; the translation still comes from PokeCorpus. | `config/semantic_anchor_decisions.json` |
| Manual translation — engine contract gap | PokeCorpus has the relevant text, but Gen1Recomp merges contexts or does not expose the parameters needed to use it faithfully. | `overrides/<language>/engine_overrides.json` with `reason: "engine-contract-gap"` |
| Manual translation — engine original | The text is specific to the engine and has no Red/Blue PokeCorpus source. | `overrides/<language>/engine_overrides.json` with `reason: "engine-original"` |
| Editorial correction | A manual formulation is deliberately preferred to the available corpus result. | `overrides/<language>/engine_overrides.json` with `reason: "editorial-correction"` |
| Known limitation | An active anchor or override is knowingly imperfect in at least one context or language. This is a status, not a translation origin. | Anchor decision metadata or override provenance |
| English fallback | No sufficiently reliable translation is available; the runtime keeps the original English string. | Generation report |

Manual engine overrides also carry a `reason` category and a concrete
`provenance` explanation. Do not add a manual override merely to increase coverage: a shared
key or missing runtime argument may make one translation wrong in another
context. In that case, keep the English fallback unless the limitation is
explicitly accepted and documented.

Keep canonical and localized ROM paths, fingerprints, corpus revisions,
import logs, worksheets, catalogs, complete extracted fonts, and coverage
reports private: they can contain source text or local filesystem
information. `config/rom_paths.toml` is ignored for this reason; never commit
it or replace the tracked example with your personal paths. A publication may contain the generated translation mod,
including its compact localized glyph page, and English documentation after
a no-ROM-content inspection. Do not claim ROM redistribution or provide
download instructions.

Known limitations are incomplete localized glyph/charmap coverage: the
reviewed Western pages contain 19 FR/DE faces, 30 IT faces, and 32 ES faces,
while apostrophe ligatures remain on the vanilla page. Japanese font
extraction remains unsupported. Other special characters may still render
incorrectly, and UI-width constraints, in-game testing, and incomplete engine
coverage remain relevant. The pipeline's fallback keeps an unfinished mod
playable; it does not make those strings translated.
