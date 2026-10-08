# Generation-3 pipeline

This document explains how the two generation-3 mods are built: the
FireRed and LeafGreen mod (`translation-<lang>-gen3`) and the Ruby,
Sapphire and Emerald mod (`translation-<lang>-gen3-rse`). It covers the
runtime hooks each mod uses, how cart text is joined to its PokeCorpus row,
how one cart keys the other editions' text, and what the release gates
check. The [README](../README.md) says which ROMs each mod needs and what
it translates; [docs/coverage.md](coverage.md) defines the coverage
figures; [docs/upstream-fixes.md](upstream-fixes.md) tracks what the
runtime still keeps out of reach.

## FireRed and LeafGreen

### Runtime hooks

gen1recomp runs FireRed and LeafGreen (US, v1.0) on its own generation-3
(game3) runtime, so the mod uses that runtime's content registries:
dialogue through `mod.content.text`, species, move and item names, item
descriptions, trainer names and class names through their record
registries, the start menu through the public `ui.start_menu.items` hook,
and game3's own text (its own menus, the mod manager, the options it adds)
through `Strings()`. At the pinned revision (gen1recomp v0.3.63), most
screens draw their text from the cart itself: the option menu, the summary
pages, the intro, the region map, the battle messages and the lists read
the cart's rows through `RomText`, so the mod translates them with the
cart's own text instead of a catalog of its own. The Pokédex's descriptions
are the exception: the screen draws them from its own pack, which no
registry reaches (FireRed entry 4 of
[docs/upstream-fixes.md](upstream-fixes.md)). Since v0.3.4 the runtime also
names an egg by the language's EGG, reads a trainer's class by id, takes
Unicode braille cells and draws Japanese with the cart's fonts.

### Dialogue and cart-text join

The game3 extractor keys each script message by its ROM address. pret's
published `pokefirered.sym` names that address with the same label the
PokeCorpus `FireRedLeafGreen` qid ends with, so every message maps to
exactly one corpus row. The cart text the runtime reads by name joins
beside it: a ROM table row by row (`gTypeNames[1]` against the corpus's
`gTypeNames.1`), the battle string table on its English text (the extractor
keys it by pret's `STRINGID_*`, the corpus by the symbol each row points
at), and a row the cart reaches through a pointer table by the translation
every row reading the same English agrees on. Each translation is shipped
as the runtime's own text IR (so the player name, `STR_VAR` buffers and
page breaks survive), encoded through pret's `charmap.txt`, and only after
the corpus English has reproduced the ROM's own text exactly. The pinned
symbol table and charmap are downloaded like the corpus; they carry
addresses and an encoding table, no game text.

Japanese is published too, since the runtime draws kana with the cart's own
Japanese fonts: its rows keep their characters instead of going through the
cart's byte encoding, whose Japanese block reuses the Latin block's values.

### Either cart

Either cart is enough, as for Ruby and Sapphire. The two share their named
text, their catalogs and the engine's own strings, but lay their script
text out at different addresses: the dialogue keys of one cart mean nothing
on the other, and the few addresses both use hold different lines. Every
address the extractor keys sits on a pret label both editions share, so the
build reads the cart it is given and keys the other edition's text through
pret's symbols (`pokefirered.sym`, `pokeleafgreen.sym`;
`pipeline/frlg/editions.py`). The naming screen's default names, which each
edition's tables list differently (FireRed's RED and FIRE, LeafGreen's
GREEN and LEAF), follow `config/frlg/edition_name_choices.json`
(pokefirered `src/oak_speech.c`). Derived from either US cart, the other
edition's text equals that cart's own extract, so a mod built from FireRed
or from LeafGreen is the same.

The mod ships the dialogue in three layers: the named text both carts share
(`lang/dialogue.lua`), then each edition's own (`lang/dialogue_firered.lua`,
`lang/dialogue_leafgreen.lua`), picked at runtime from `GameVersion`. Where
a ROM table points at another string in LeafGreen (the naming screen's
GREEN and LEAF), the reviewed decision names LeafGreen's own row.

### Release gate and runtime limits

Before packaging, `tools/frlg/gate.lua` loads the mod through gen1recomp's
real generation-3 loader over the extracted game3 data and checks that each
catalog lands where the FireRed screens read it, and that an address both
carts use keeps its own edition's line. It also measures runtime limits the
mod cannot fix itself, and the build prints them.

Three screens read their value by its English text alone: the region map's
section names and guide text, the summary's ability name and the map name
popup's floor suffix. `modkit pack` refuses such keys in
`lang/strings.lua`, so those ~150 entries ship in a catalog of their own
(`lang/strings_by_english.lua`) registered through the same `strings`
registry, until the engine reads them by label.

Runtime limits fixed upstream since the first release: accented letters
(v0.2.67), species and move names reverting to English on entering the
field (v0.2.70), the party naming an egg EGG, trainer classes recognised by
their English name, braille lines and Japanese fonts (all v0.3.4), and the
honorific after the player's name (v0.3.52). What remains open is listed in
the FireRed section of [docs/upstream-fixes.md](upstream-fixes.md).

## Ruby, Sapphire and Emerald

### Layers

gen1recomp runs Ruby, Sapphire and Emerald on the same generation-3 runtime
as FireRed, so the mod uses the same content registries and is built the
same way (`pipeline/gen3/`). Ruby and Sapphire are the release's base game,
Emerald its companion edition: each reads its own PokeCorpus collection
(`RubySapphire`, `Emerald`), pret symbol tables and charmap (pokeruby's,
pokeemerald's) and the runtime's text dialect for the game (their named
placeholders, fonts and glyph runs). The mod carries one layer per game,
which `main.lua` picks from `GameVersion`: Emerald's text and catalogs
(`lang/emerald/`), the catalogs and engine strings Ruby and Sapphire share
(`lang/rs/`), each edition's named text (`lang/ruby/`, `lang/sapphire/`)
and the script text of each edition's text layout (`lang/ruby_1_0/`,
`lang/ruby_1_1/`, `lang/sapphire_1_0/`, `lang/sapphire_1_1/`).

### Ruby and Sapphire layouts and revisions

The script text is keyed by ROM address, and Ruby and Sapphire lay it out
in four ways: the two editions differ, and revision 1.0 differs from 1.1
and 1.2 (pret's rev1 and rev2 symbol tables are identical). A build reads
one cart and keys the others through pokeruby's symbol tables: every text
the extract keys by address sits on a pokeruby label, and the same label in
another cart's table is the same text there. The few texts only the other
edition prints (its own Team Magma or Team Aqua scenes) come from their
corpus rows, and the Pokédex entries and version names the two editions
word differently from the corpus rows PokeCorpus marks with the edition
(`pokedex_entries^S`, `Text_Version^S`). Built from Ruby 1.2 and from
Sapphire 1.0, the other edition's layers match the ones read from its own
cart, except 8 or 9 exclusive lines the collection has no row for, which
stay in English. At runtime, the mod tells revision 1.0 from 1.1/1.2 by an
address that starts a different text in each (`lang/rs/layouts.lua`).

The texts pokeruby rewords between revisions (`#if REVISION >= 1`: the
TOGEPI DOLL's description, the Record Corner's welcome, two Pokédex
entries...) are reviewed in
[`config/rse/dialogue_decisions.json`](../config/rse/dialogue_decisions.json):
the 1.0 carts print an earlier wording of the same message. The engine
strings are joined to Ruby and Sapphire's own rows
([`config/rse/engine_scope.json`](../config/rse/engine_scope.json)); the
screens their carts never had (the link lobby, the Union Room's words,
Mystery Gift) take Emerald's resolved value. In Japanese, the second page
of every Pokédex entry is blank, since the Japanese carts print an entry on
one page, and the battle menus and move-use line are reviewed as Emerald's
are (`overrides/ja-Hrkt/rse/dialogue.json`). Ruby and Sapphire's trainer
classes already come in a male and a female form, and their European
carts' trainer names and classes are the corpus rows, so they need no
European trainer review.

### Emerald join

Emerald (US, v1.0) needs its own ROM, like Crystal: its text, its catalogs
and its cart rows differ from Ruby and Sapphire's. `tools/rse/extract.lua`
runs the text steps of the engine's own import plan for each game. Each
script message is joined through the pret symbol its ROM address carries,
the cart's tables by label, and every slot of a ROM pointer table (the
battle string table, the nature names, the script menus' standard
strings...) through the pret symbol its pointer reaches. Where the corpus
lists a label twice, the row whose English reproduces the ROM wins. Every
translation is the official row, shipped as the runtime's text IR once its
English has reproduced the ROM's own text exactly.

The game3 interface strings (`Strings()`) are listed in
[`config/rse/emerald_engine_scope.json`](../config/rse/emerald_engine_scope.json)
and joined to Emerald's own cart rows. A cart label the dialogue already
translates keeps its own row: the engine catalog never lends it another
screen's wording. What has no Emerald row is reviewed and carries its
provenance: the interface rows gen1recomp added to both games reuse
FireRed's reviewed overrides (and the Red/Blue and Gold ones before
English), the Wonder Card lines gen1recomp composes for Emerald's events
are worded from the cart's own vocabulary
(`overrides/<lang>/rse/emerald_engine.json`), and the one French line the
corpus lacks is the French cart's own, read from pret's multi-language
decompilation (`overrides/fr/rse/emerald_dialogue.json`). The Japanese rows
the corpus leaves empty are reviewed one by one, each with its source in
its provenance (`overrides/ja-Hrkt/rse/emerald_dialogue.json`). Both
carry the reason `corpus-gap`.

### European trainer names

The trainers of the cart's trainer table are named as the European carts
name them, which their own code does differently from the US one (pret's
multi-language decompilation, `#if EUROPE`, reviewed in
[`config/rse/emerald_european_trainer_text.json`](../config/rse/emerald_european_trainer_text.json)):
the French and Spanish carts put a Team Aqua or Team Magma grunt's name
before its class (SBIRE TEAM AQUA, SOLDADO EQUIPO AQUA), and the French,
Italian and Spanish carts have their own words for a girl's School Kid
class, the female rival's class and Tate and Liza's plural Leader class.
The runtime reads each trainer's name and class from the mod, so the mod
ships them in the cart's order and words; German, which that decompilation
does not cover, keeps its corpus rows. The Battle Frontier's, Trainer
Hill's and secret bases' trainers do not come from that table, and keep the
US words.

### Release gate

Before packaging, `tools/rse/gate.lua` loads the mod through gen1recomp's
real generation-3 loader, once over the extracted Emerald data and once
over the Ruby or Sapphire cart read, and checks that the dialogue, the
battle string table, the nature names, the name catalogs, the engine
strings and an Easy Chat word land where each game's screens read them,
that a Ruby or Sapphire cart gets its own revision's text layout, and that
the mod stays out of a FireRed session.

Since gen1recomp#2724 (v0.3.58), Ruby and Sapphire's version placeholders,
Pokédex, shop, menus and Easy Chat words read the mod's text too; the
screens that still print gen1recomp's own copies (the secret base,
roulette, trainer card labels, diploma, berry tag, PokéNav and Battle Tower
records screens) are listed in the Ruby and Sapphire section of
[docs/upstream-fixes.md](upstream-fixes.md). The ability names, the Pokédex
entries, the contest texts and the map section names of Emerald's screens
go through `Strings()` since gen1recomp#2678 (v0.3.53), and the gate fails
if one of them prints the cart's English (`hooks` in the build report); the
Emerald section of [docs/upstream-fixes.md](upstream-fixes.md) lists what
Emerald still keeps out of reach, with the seven engine rows that stay in
English.

## Regenerating the engine scopes

Each family's `Strings()` scope is generated from the pinned engine by
`pipeline/gen3/engine_scope.py`; a test derives the same set from the
engine, so a new key cannot slip out of the metric. `--revision` is the
gen1recomp commit the engine checkout is at, recorded as the file's
`source_revision`:

```sh
rev="$(git -C .cache/dependencies/gen1recomp rev-parse HEAD)"
python -m pipeline.gen3.engine_scope --family frlg --revision "$rev"
python -m pipeline.gen3.engine_scope --family rs --revision "$rev" \
  --extracted .cache/ruby/extracted/cache
python -m pipeline.gen3.engine_scope --family emerald --revision "$rev"
```

The outputs are
[`config/frlg/engine_scope.json`](../config/frlg/engine_scope.json),
[`config/rse/engine_scope.json`](../config/rse/engine_scope.json) and
[`config/rse/emerald_engine_scope.json`](../config/rse/emerald_engine_scope.json).
A key whose text is a cart string is shipped under that string's ROM label,
which is how the runtime looks it up, except on a label the dialogue
already ships with its own row: the runtime reads a label's catalog entry
first, which would give it another screen's wording.
