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

| Target | ROM catalogs (6) | Literal handlers | ROM aggregate | Engine catalog |
| --- | ---: | ---: | ---: | ---: |
| `fr` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 259/533 (48.59%) |
| `de` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 257/533 (48.22%) |
| `es` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 260/533 (48.78%) |
| `it` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 260/533 (48.78%) |
| `ja-Hrkt` | 3101/3101 (100%) | 5/5 (100%) | 3106/3106 (100%) | 254/533 (47.65%) |

Collectively, the 5/5 literal-handler results are backed by 15/15 unique
corpus qids; those handlers are included in the ROM aggregate above. Engine
fallback entries are not counted as translated. A release still needs both
the ROM aggregate and engine catalog at 100%; these snapshot metrics are not
a promise of complete translation.

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
- Generic found/received/reward/bag-full strings vary by context and are not
  safely overridable.
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
example `dist/translation-fr-0.2.0.zip`. The command prints its absolute path.
The `.zip` extension is intentional: Gen1Recomp's mod importer accepts ZIP
files, while Modkit writes the same deterministic ZIP format.
Immediately before the final path, the builder prints the separate ROM-catalog
and engine-catalog match percentages.

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

## Data flow and matching

The pipeline follows one direction:

```text
parse corpus -> align by qid -> join the ROM worksheet catalogs -> fill engine strings
```

### Low-level module map

The command-line wrapper in `scripts/pipeline.py` delegates to the modules in
`pipeline/`. The main workflow is split as follows:

| Module | Responsibility |
| --- | --- |
| `pipeline/cli.py` | Defines the `parse`, `align`, `generate`, `validate`, and ROM import commands and connects their inputs and outputs. |
| `pipeline/corpus.py` | Reads the parallel Red/Blue corpus files, canonicalizes language codes, validates line cardinality, and produces source/target records. |
| `pipeline/model.py` | Defines the shared `CorpusRecord` and `Alignment` data structures passed between stages. |
| `pipeline/align.py` | Pairs English and target-language records by stable qid, applies qid-based editorial overrides, and writes the aligned intermediate representation. |
| `pipeline/join.py` | Joins aligned records to the exact ROM-derived Modkit worksheet keys, including catalog-specific rules and corpus-backed TM/HM terminology. |
| `pipeline/engine.py` | Matches the 533 engine strings using overrides, semantic anchors, exact/normalized text, and structural placeholders; unmatched entries remain empty. |
| `pipeline/literals.py` | Generates qid-driven Mod API handlers for ROM dialogue that Gen1Recomp carries as Lua literals instead of extracted text keys. |
| `pipeline/tokens.py` | Converts `poke-corpus` control tokens to Gen1Recomp notation and validates dynamic placeholders. |
| `pipeline/mod.py` | Writes the final Modkit-compatible Lua catalogs, manifest, worksheet outputs, and ROM/engine coverage report. |
| `pipeline/validate.py` | Checks placeholders, glyph coverage, version consistency, and the separate ROM/engine release gates. |
| `pipeline/roms.py` | Verifies canonical ROM hashes and orchestrates private Red/Blue imports into ignored local caches. |
| `pipeline/localized_font.py` | Validates reviewed Western font regions, extracts compact language glyph pages, and generates the Modkit font/charmap catalogs. |
| `pipeline/disassembly_audit.py` | Developer-only parser for private localized disassembly snapshots; emits match/divergence/callsite reports without editing anchors or review files. |
| `pipeline/engine_backlog.py` | Read-only developer analyzer for unresolved/ambiguous engine keys, literal Gen1Recomp callsites, conservative RBY eligibility, placeholders, fallback reasons, and PokeCorpus qid suggestions. |

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

`config/semantic_anchors.json` contains only stable qids and extraction rules;
it contains no translations. `config/terminology_anchors.json` is likewise
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

Keep canonical and localized ROM paths, fingerprints, corpus revisions,
import logs, worksheets, catalogs, complete extracted fonts, and coverage
reports private: they can contain source text or local filesystem
information. A publication may contain the generated translation mod,
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
